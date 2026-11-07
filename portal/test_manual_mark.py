"""Manual "already generated / already delivered" marks never touch NextEmby."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cryptography.fernet import Fernet

from core import ConflictError, Store, ValidationError, load_products
import test_app as _app_tests

PRODUCTS = load_products(Path(__file__).resolve().parent.parent / "product-mapping.json")


class FakeClient:
    def __init__(self):
        self.calls = 0
    def generate(self, product):
        self.calls += 1
        return {"batch_id": "BATCH-TEST", "card": "TEST-CARD"}


class FailingClient:
    def generate(self, product):
        raise RuntimeError("network")


class ManualMarkStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "orders.sqlite3"
        self.key = Fernet.generate_key()
        self.store = Store(self.path, self.key, PRODUCTS)
        self.client = FakeClient()
        self.store.create_order("ORDER-1", "free_trial", "direct", "xianyu", buyer_note="buyer")
        self.store.approve_order("ORDER-1", "已在闲鱼核对付款")

    def tearDown(self):
        self.directory.cleanup()

    def test_generated_then_delivered_then_undo(self):
        order = self.store.mark_manual("ORDER-1", "generated", "闲鱼手动发卡")
        self.assertEqual(order["status"], "manual_issued")
        self.assertEqual(order["manual_note"], "闲鱼手动发卡")
        self.assertFalse(order["has_card"])
        order = self.store.mark_manual("ORDER-1", "delivered")
        self.assertEqual(order["status"], "manual_delivered")
        self.assertEqual(order["manual_note"], "闲鱼手动发卡")  # empty note keeps the earlier note
        self.assertEqual(self.store.mark_manual("ORDER-1", "delivered")["status"], "manual_delivered")
        order = self.store.unmark_manual("ORDER-1")
        self.assertEqual(order["status"], "eligible")
        self.assertIsNone(order["manual_note"])

    def test_marked_order_never_issues(self):
        self.store.mark_manual("ORDER-1", "delivered")
        with self.assertRaises(ConflictError):
            self.store.issue_order("ORDER-1", self.client, PRODUCTS)
        self.assertEqual(self.client.calls, 0)
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "manual_delivered")

    def test_delivered_cannot_go_back_to_generated(self):
        self.store.mark_manual("ORDER-1", "delivered")
        with self.assertRaises(ConflictError):
            self.store.mark_manual("ORDER-1", "generated")

    def test_pending_and_issued_orders_cannot_be_marked(self):
        self.store.create_order("ORDER-2", "free_trial", "direct", "manual")
        with self.assertRaises(ConflictError):
            self.store.mark_manual("ORDER-2", "delivered")
        self.store.issue_order("ORDER-1", self.client, PRODUCTS)
        with self.assertRaises(ConflictError):
            self.store.mark_manual("ORDER-1", "delivered")
        with self.assertRaises(ValidationError):
            self.store.mark_manual("ORDER-1", "bogus")

    def test_needs_review_can_be_marked_and_restored(self):
        with self.assertRaises(Exception):
            self.store.issue_order("ORDER-1", FailingClient(), PRODUCTS)
        self.assertEqual(self.store.get_order("ORDER-1")["status"], "needs_review")
        self.store.mark_manual("ORDER-1", "generated")
        self.store.mark_manual("ORDER-1", "delivered")
        self.assertEqual(self.store.unmark_manual("ORDER-1")["status"], "needs_review")

    def test_existing_ledger_is_migrated(self):
        # A ledger created before this feature gains the columns without losing rows.
        db = sqlite3.connect(self.path)
        columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        db.close()
        self.assertTrue({"manual_prev_status", "manual_note", "manual_marked_at"} <= columns)
        reopened = Store(self.path, self.key, PRODUCTS)
        self.assertEqual(reopened.get_order("ORDER-1")["status"], "eligible")


class ManualMarkHTTPTests(unittest.TestCase):
    setUp = _app_tests.HTTPTests.setUp
    tearDown = _app_tests.HTTPTests.tearDown
    configure_web_payment = _app_tests.HTTPTests.configure_web_payment
    create_web_order = _app_tests.HTTPTests.create_web_order
    configure_bridge = _app_tests.HTTPTests.configure_bridge
    bridge_payload = staticmethod(_app_tests.HTTPTests.bridge_payload)

    def test_manual_mark_routes(self):
        response = self.admin.post("/api/admin/orders", json={"source": "manual", "order_id": "HAND-1", "product_id": "free_monthly"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.guest.post("/api/admin/orders/HAND-1/manual-mark", json={"state": "delivered"}).status_code, 401)
        response = self.admin.post("/api/admin/orders/HAND-1/manual-mark", json={"state": "delivered"})
        self.assertEqual(response.status_code, 409, response.text)
        self.admin.post("/api/admin/orders/HAND-1/approve", json={"evidence_note": "已核对真实到账40元"})
        response = self.admin.post("/api/admin/orders/HAND-1/manual-mark", json={"state": "delivered", "note": "闲鱼已手动发货"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "manual_delivered")
        response = self.admin.post("/api/admin/orders/HAND-1/issue", json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.remote.generated), 0)
        response = self.admin.post("/api/admin/orders/HAND-1/manual-unmark", json={})
        self.assertEqual(response.json()["status"], "eligible")

    def test_manual_web_order_remains_private_and_has_no_claim(self):
        order, headers = self.create_web_order()
        order_id = order["order_id"]
        self.admin.post(f"/api/admin/orders/{order_id}/approve", json={"evidence_note": "已核对真实到账40元"})
        self.admin.post(f"/api/admin/orders/{order_id}/manual-mark", json={"state": "delivered", "note": "private admin note"})
        self.assertEqual(self.guest.post(f"/api/admin/orders/{order_id}/manual-unmark", json={}).status_code, 401)
        response = self.guest.get(f"/api/public/orders/{order_id}", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "manual_delivered")
        for key in ("claim_url", "claim_token", "card", "manual_note", "manual_marked_at"):
            self.assertNotIn(key, response.json())
        self.assertEqual(self.admin.get("/api/admin/overview").json()["stats"]["issued"], 1)
        self.assertEqual(len(self.remote.generated), 0)

    def test_bridge_does_not_issue_marked_order(self):
        headers = self.configure_bridge()
        self.admin.post("/api/admin/orders", json={"source": "xianyu", "order_id": "TRADE-TEST", "product_id": "free_monthly", "buyer_note": "BUYER-TEST"})
        orders = self.admin.get("/api/admin/overview").json()["orders"]
        order_id = orders[0]["order_id"]
        self.admin.post(f"/api/admin/orders/{order_id}/approve", json={"evidence_note": "已核对真实到账40元"})
        self.assertEqual(self.admin.post(f"/api/admin/orders/{order_id}/manual-mark", json={"state": "delivered"}).status_code, 200)
        response = self.admin.post("/api/bridge/fulfill", headers=headers, json=self.bridge_payload())
        self.assertNotEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
