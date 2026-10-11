from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest

from cryptography.fernet import Fernet

from core import (
    ConflictError, NotFoundError, PaymentRequiredError, ReviewRequiredError,
    StorageError, Store, ValidationError, load_products,
)


PRODUCTS = load_products(Path(__file__).resolve().parent.parent / "product-mapping.json")


class FakeClient:
    def __init__(self):
        self.calls = []
    def generate(self, product):
        self.calls.append(product)
        return {"batch_id": "BATCH-TEST", "card": "TEST-CARD-KEEP-SECRET"}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "orders.sqlite3"
        self.key = Fernet.generate_key()
        self.store = Store(self.path, self.key, PRODUCTS)
        self.client = FakeClient()
    def tearDown(self):
        self.directory.cleanup()
    def ready(self, order_id="ORDER-1", mode="direct"):
        self.store.create_order(order_id, "free_monthly", mode, "manual")
        self.store.approve_order(order_id, "人工在闲鱼订单页核实已付款，数量1、规格有网盘用户月卡")

    def test_create_is_not_payment_approval(self):
        order = self.store.create_order("ORDER-1", "free_monthly", "direct", "manual")
        self.assertEqual(order["status"], "pending_review")
        with self.assertRaises(PaymentRequiredError):
            self.store.issue_order("ORDER-1", self.client, PRODUCTS)
        self.assertEqual(self.client.calls, [])

    def test_source_xianyu_alone_does_not_trust_payment(self):
        order = self.store.create_order("ORDER-1", "free_monthly", "direct", "xianyu")
        self.assertEqual(order["status"], "pending_review")
        with self.assertRaises(PaymentRequiredError):
            self.store.issue_order("ORDER-1", self.client)

    def test_duplicate_order_does_not_change_product_or_mode(self):
        self.ready()
        existing = self.store.create_order("ORDER-1", "free_monthly", "direct", "manual")
        self.assertEqual(existing["status"], "eligible")
        for product, mode, source in [
            ("svip_annual", "direct", "manual"),
            ("free_monthly", "claim", "manual"),
            ("free_monthly", "direct", "xianyu"),
        ]:
            with self.assertRaises(ConflictError):
                self.store.create_order("ORDER-1", product, mode, source)
        self.assertEqual(len(self.store.list_orders()), 1)

    def test_issue_once_replay_returns_same_card_without_http(self):
        self.ready()
        first = self.store.issue_order("ORDER-1", self.client, PRODUCTS)
        second = self.store.issue_order("ORDER-1", self.client, PRODUCTS)
        self.assertEqual(first["card"], second["card"])
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.client.calls[0]["template_name"], "free")
        self.assertEqual(self.client.calls[0]["duration_days"], 30)

    def test_legacy_channel_migration_preserves_issued_card_and_claim(self):
        self.ready(mode='claim')
        first = self.store.issue_order('ORDER-1', self.client, PRODUCTS)
        token = self.store.create_claim('ORDER-1')
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE orders DROP COLUMN channel')
        reopened = Store(self.path, self.key, PRODUCTS)
        self.assertEqual(reopened.get_order('ORDER-1')['source'], 'manual')
        self.assertEqual(reopened.get_claim(token)['card'], first['card'])
        self.assertEqual(reopened.issue_order('ORDER-1', self.client, PRODUCTS)['card'], first['card'])
        self.assertEqual(len(self.client.calls), 1)

    def test_concurrent_store_instances_never_generate_twice(self):
        self.ready()
        second_store = Store(self.path, self.key, PRODUCTS)
        started, release = threading.Event(), threading.Event()
        client = self.client
        original = client.generate
        def slow_generate(product):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test gate timed out")
            return original(product)
        client.generate = slow_generate
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.store.issue_order, "ORDER-1", client, PRODUCTS)
            self.assertTrue(started.wait(5))
            try:
                # Constructing another Store while a worker is alive is safe.
                third_store = Store(self.path, self.key, PRODUCTS)
                self.assertEqual(third_store.get_order("ORDER-1")["status"], "processing")
                with self.assertRaises(ReviewRequiredError):
                    second_store.issue_order("ORDER-1", client, PRODUCTS)
            finally:
                release.set()
            self.assertEqual(first.result(5)["status"], "issued")
        self.assertEqual(len(client.calls), 1)

    def test_unknown_generation_is_never_retried_and_error_is_redacted(self):
        self.ready()
        class Interrupted:
            calls = 0
            def generate(self, product):
                self.calls += 1
                raise TimeoutError("SECRET-TOKEN-IN-REMOTE-ERROR")
        client = Interrupted()
        for _ in range(2):
            with self.assertRaises(ReviewRequiredError) as caught:
                self.store.issue_order("ORDER-1", client)
            self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "needs_review")
        self.assertNotIn("SECRET", json.dumps(self.store.list_orders()))

    def test_restart_recovers_abandoned_processing_without_generating(self):
        self.ready()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE orders SET status='processing',worker_pid=NULL,worker_token='old-process'")
        restarted = Store(self.path, self.key, PRODUCTS)
        self.assertEqual(restarted.get_order("ORDER-1")["status"], "needs_review")
        with self.assertRaises(ReviewRequiredError):
            restarted.issue_order("ORDER-1", self.client)
        self.assertFalse(self.client.calls)

    def test_claim_stable_secret_and_same_card_across_restarts(self):
        self.ready(mode="claim")
        with self.assertRaises(ConflictError):
            self.store.create_claim("ORDER-1")
        issued = self.store.issue_order("ORDER-1", self.client)
        token = self.store.create_claim("ORDER-1")
        self.assertEqual(len(token), 43)
        restarted = Store(self.path, self.key, PRODUCTS)
        self.assertEqual(restarted.create_claim("ORDER-1"), token)
        first, second = restarted.get_claim(token), restarted.get_claim(token)
        self.assertEqual(first["card"], issued["card"])
        self.assertEqual(first, second)
        self.assertEqual(restarted.get_order("ORDER-1")["claim_views"], 2)
        self.assertNotIn("order_id", first)
        with self.assertRaises(NotFoundError):
            restarted.get_claim("A" * 43)
        with self.assertRaises(NotFoundError):
            restarted.get_claim("ORDER-1")
        public = json.dumps(self.store.list_orders()) + json.dumps(self.store.get_order("ORDER-1"))
        self.assertNotIn(token, public)
        self.assertNotIn(issued["card"], public)
        self.assertNotIn("ciphertext", public)
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT card_ciphertext,claim_ciphertext,claim_hash FROM orders").fetchone()
        self.assertNotIn(issued["card"].encode(), row[0])
        self.assertNotIn(token.encode(), row[1])
        self.assertNotEqual(token, row[2])

    def test_wrong_key_fails_before_any_write(self):
        self.ready()
        with self.assertRaises(StorageError):
            Store(self.path, Fernet.generate_key(), PRODUCTS)
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "eligible")

    def test_snapshot_or_template_mutation_blocks_generation(self):
        self.ready()
        changed = {key: dict(value) for key, value in PRODUCTS.items()}
        changed["free_monthly"]["listed_price_cents"] += 1
        with self.assertRaises(ConflictError):
            self.store.issue_order("ORDER-1", self.client, changed)
        changed["free_monthly"]["template_name"] = "SVIP"
        with self.assertRaises(ValidationError):
            self.store.issue_order("ORDER-1", self.client, changed)
        self.assertFalse(self.client.calls)
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "eligible")

    def test_blank_payment_evidence_rejected(self):
        self.store.create_order("ORDER-1", "free_monthly", "direct", "manual")
        with self.assertRaises(ValidationError):
            self.store.approve_order("ORDER-1", "  ")

    def test_invalid_response_locks_order_for_review(self):
        self.ready()
        class BadClient:
            def generate(self, product):
                return {"cards": ["CARD-1", "CARD-2"]}
        with self.assertRaises(ReviewRequiredError):
            self.store.issue_order("ORDER-1", BadClient())
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
