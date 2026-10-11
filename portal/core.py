"""Encrypted, fail-closed order ledger for the local MediaDock portal.

No card generation happens merely by creating or approving an order. The caller
must explicitly invoke issue_order after payment has been independently checked.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class CoreError(Exception):
    """A deliberately sanitized error suitable for an administrator."""


class ValidationError(CoreError):
    pass


class NotFoundError(CoreError):
    pass


class ConflictError(CoreError):
    pass


class PaymentRequiredError(CoreError):
    pass


class ReviewRequiredError(CoreError):
    pass


class StorageError(CoreError):
    pass


EXPECTED_PRODUCTS = {
    "svip_annual": ("SVIP", 365),
    "vvip_annual": ("VVIP", 365),
    "vip_trial": ("VIP", 1),
    "free_trial": ("free", 1),
    "free_monthly": ("free", 30),
}
_PROCESS_TOKEN = secrets.token_hex(16)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_product(product: dict) -> dict:
    if not isinstance(product, dict):
        raise ValidationError("套餐配置格式无效")
    expected = EXPECTED_PRODUCTS.get(product.get("id"))
    if expected is None or (product.get("template_name"), product.get("duration_days")) != expected:
        raise ValidationError("套餐模板或天数与已确认配置不一致")
    if type(product.get("duration_days")) is not int:
        raise ValidationError("套餐天数必须为整数")
    if type(product.get("listed_price_cents")) is not int or product["listed_price_cents"] < 0:
        raise ValidationError("套餐价格必须为非负整数分")
    if not isinstance(product.get("name"), str) or not product["name"].strip():
        raise ValidationError("套餐名称不能为空")
    if product.get("count", 1) != 1 or type(product.get("count", 1)) is not int:
        raise ValidationError("每单仅支持生成一张卡")
    return dict(product)


def load_products(path: str | Path) -> dict[str, dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValidationError("无法读取套餐配置") from None
    if not isinstance(data, dict) or data.get("cards_per_order") != 1 or data.get("expected_order_quantity") != 1:
        raise ValidationError("套餐配置必须限制每单一件、一张卡")
    products: dict[str, dict] = {}
    for product in data.get("products", []):
        product = validate_product(product)
        if product["id"] in products:
            raise ValidationError("套餐标识重复")
        products[product["id"]] = product
    if set(products) != set(EXPECTED_PRODUCTS):
        raise ValidationError("套餐配置必须包含已确认的五种套餐")
    return products


def _text(value: Any, name: str, limit: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name}必须为文字")
    value = value.strip()
    if (required and not value) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValidationError(f"{name}为空、过长或包含控制字符")
    return value


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Store:
    def __init__(self, db_path: str | Path, encryption_key: bytes, products: dict[str, dict] | None = None):
        self.db_path = str(db_path)
        self.products = products or load_products(Path(__file__).resolve().parent.parent / "product-mapping.json")
        if len(encryption_key) == 32:
            encryption_key = base64.urlsafe_b64encode(encryption_key)
        try:
            self.cipher = Fernet(encryption_key)
        except (ValueError, TypeError):
            raise StorageError("本地加密密钥格式无效") from None
        self._lock = threading.RLock()
        self._memory_connection = None
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.db_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.db_path, 0o600)
        else:
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self._memory_connection.row_factory = sqlite3.Row
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    product_id TEXT NOT NULL,
                    product_json TEXT NOT NULL,
                    delivery_mode TEXT NOT NULL CHECK(delivery_mode IN ('direct','claim')),
                    source TEXT NOT NULL CHECK(source IN ('manual','xianyu')),
                    buyer_note TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    evidence_note TEXT,
                    issue_started_at TEXT,
                    issued_at TEXT,
                    worker_pid INTEGER,
                    worker_token TEXT,
                    card_ciphertext BLOB,
                    batch_ciphertext BLOB,
                    claim_hash TEXT UNIQUE,
                    claim_ciphertext BLOB,
                    claimed_at TEXT,
                    claim_views INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value BLOB NOT NULL);
            """)
            existing = db.execute("SELECT value FROM metadata WHERE key='encryption_check'").fetchone()
            if existing:
                if self._decrypt(existing[0]) != "mediadock-card-ledger-v1":
                    raise StorageError("本地密钥与发卡数据库不匹配")
            else:
                db.execute("INSERT INTO metadata VALUES ('encryption_check', ?)", (self._encrypt("mediadock-card-ledger-v1"),))
            # A separate display channel preserves the legacy source constraint
            # and existing ledger rows, including encrypted cards and claims.
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
            if "channel" not in columns:
                db.execute("ALTER TABLE orders ADD COLUMN channel TEXT NOT NULL DEFAULT ''")
            db.commit()
        self.recover_unfinished()

    class _Connection:
        def __init__(self, store):
            self.store = store
        def __enter__(self):
            self.store._lock.acquire()
            try:
                self.db = self.store._memory_connection or sqlite3.connect(self.store.db_path, timeout=15, isolation_level=None)
                self.db.row_factory = sqlite3.Row
                self.db.execute("PRAGMA busy_timeout=15000")
                return self.db
            except Exception:
                self.store._lock.release()
                raise
        def __exit__(self, kind, value, tb):
            try:
                if self.db.in_transaction:
                    self.db.rollback()
                if self.store._memory_connection is None:
                    self.db.close()
            finally:
                self.store._lock.release()

    def _connect(self):
        return self._Connection(self)

    def _encrypt(self, value: str) -> bytes:
        return self.cipher.encrypt(value.encode("utf-8"))

    def _decrypt(self, value: bytes) -> str:
        try:
            return self.cipher.decrypt(value).decode("utf-8")
        except (InvalidToken, UnicodeError, TypeError):
            raise StorageError("加密数据无法读取，请检查凭据存储中的密钥和数据库是否匹配") from None

    @staticmethod
    def _row(db, order_id):
        row = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise NotFoundError("未找到订单")
        return row

    def _public(self, row, reveal=False) -> dict:
        result = {key: row[key] for key in (
            "order_id", "product_id", "delivery_mode", "source", "buyer_note", "status", "created_at",
            "approved_at", "evidence_note", "issue_started_at", "issued_at", "claimed_at", "claim_views", "last_error"
        )}
        result["product"] = json.loads(row["product_json"])
        result["source"] = row["channel"] or row["source"]
        result["has_card"] = row["card_ciphertext"] is not None
        result["has_claim"] = row["claim_hash"] is not None
        if reveal and row["card_ciphertext"]:
            result["card"] = self._decrypt(row["card_ciphertext"])
            result["batch_id"] = self._decrypt(row["batch_ciphertext"])
        return result

    def list_orders(self) -> list[dict]:
        with self._connect() as db:
            return [self._public(row) for row in db.execute("SELECT * FROM orders ORDER BY created_at DESC, rowid DESC")]

    def get_order(self, order_id: str, reveal: bool = False) -> dict:
        with self._connect() as db:
            return self._public(self._row(db, order_id), reveal)

    def create_order(self, order_id: str, product_id: str, delivery_mode: str, source: str, buyer_note: str = "") -> dict:
        order_id = _text(order_id, "订单号", 128)
        buyer_note = _text(buyer_note, "买家备注", 500, required=False)
        if delivery_mode not in {"direct", "claim"} or not isinstance(source, str) or source not in {"manual", "web", "xianyu", "xiaohongshu"}:
            raise ValidationError("发货方式或订单来源无效")
        if product_id not in self.products:
            raise ValidationError("未知套餐")
        product = validate_product(self.products[product_id])
        snapshot = json.dumps(product, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if row:
                if (row["product_id"], row["delivery_mode"], row["channel"] or row["source"]) != (product_id, delivery_mode, source):
                    raise ConflictError("订单号已存在，套餐、来源或发货方式不一致")
                return self._public(row)
            db.execute("""INSERT INTO orders(order_id,product_id,product_json,delivery_mode,source,channel,buyer_note,status,created_at)
                          VALUES (?,?,?,?,?,?,?,'pending_review',?)""",
                       (order_id, product_id, snapshot, delivery_mode, 'xianyu' if source == 'xianyu' else 'manual', source, buyer_note, _now()))
            db.commit()
            return self._public(self._row(db, order_id))

    def approve_order(self, order_id: str, evidence_note: str) -> dict:
        evidence_note = _text(evidence_note, "付款核验说明", 1000)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, order_id)
            if row["status"] == "eligible":
                return self._public(row)
            if row["status"] != "pending_review":
                raise ConflictError("该订单当前状态不能再次确认付款")
            db.execute("UPDATE orders SET status='eligible',approved_at=?,evidence_note=? WHERE order_id=?",
                       (_now(), evidence_note, order_id))
            db.commit()
            return self._public(self._row(db, order_id))

    def issue_order(self, order_id: str, client, products: dict[str, dict] | None = None) -> dict:
        products = products or self.products
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, order_id)
            if row["status"] == "issued":
                return self._public(row, reveal=True)
            if row["status"] == "pending_review":
                raise PaymentRequiredError("请先核验真实付款并确认订单")
            if row["status"] in {"needs_review", "processing"}:
                raise ReviewRequiredError("该订单正在处理或结果待核实，禁止再次生成卡密")
            if row["status"] != "eligible":
                raise ConflictError("订单当前状态不能生成卡密")
            snapshot = validate_product(json.loads(row["product_json"]))
            current = validate_product(products.get(row["product_id"]))
            if any(snapshot.get(k) != current.get(k) for k in ("id", "template_name", "duration_days", "listed_price_cents")):
                raise ConflictError("套餐配置已变化，请核对订单快照后处理")
            db.execute("UPDATE orders SET status='processing',issue_started_at=?,worker_pid=?,worker_token=? WHERE order_id=?",
                       (_now(), os.getpid(), _PROCESS_TOKEN, order_id))
            db.commit()

        # The SQL lock is deliberately released before the remote operation.
        # Once processing is durable, every uncertain outcome requires review.
        try:
            result = client.generate(snapshot)
            if not isinstance(result, dict) or not isinstance(result.get("card"), str) or not result["card"].strip():
                raise ValueError("invalid result")
            if not isinstance(result.get("batch_id"), str) or not result["batch_id"].strip():
                raise ValueError("invalid batch")
            encrypted_card, encrypted_batch = self._encrypt(result["card"]), self._encrypt(result["batch_id"])
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._row(db, order_id)
                if current["status"] != "processing" or current["worker_token"] != _PROCESS_TOKEN:
                    raise ConflictError("订单生成状态发生变化，需要人工核实")
                db.execute("""UPDATE orders SET status='issued',card_ciphertext=?,batch_ciphertext=?,issued_at=?,
                              worker_pid=NULL,worker_token=NULL,last_error=NULL WHERE order_id=?""",
                           (encrypted_card, encrypted_batch, _now(), order_id))
                db.commit()
                return self._public(self._row(db, order_id), reveal=True)
        except Exception:
            with self._connect() as db:
                db.execute("""UPDATE orders SET status='needs_review',worker_pid=NULL,worker_token=NULL,last_error=?
                              WHERE order_id=? AND status='processing'""",
                           ("生成结果不确定：请到 NextEmby 核对批次，禁止自动重试。", order_id))
            raise ReviewRequiredError("生成结果待核实；为避免重复发卡，已暂停该订单") from None

    def recover_unfinished(self) -> int:
        recovered = 0
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT order_id,worker_pid,worker_token FROM orders WHERE status='processing'").fetchall():
                own_worker = row["worker_pid"] == os.getpid() and row["worker_token"] == _PROCESS_TOKEN
                if not own_worker and (row["worker_pid"] == os.getpid() or not _pid_alive(row["worker_pid"])):
                    db.execute("""UPDATE orders SET status='needs_review',worker_pid=NULL,worker_token=NULL,last_error=?
                                  WHERE order_id=?""",
                               ("服务中断时生成结果未确认，请到 NextEmby 核实，禁止自动重试。", row["order_id"]))
                    recovered += 1
            db.commit()
        return recovered

    def create_claim(self, order_id: str) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._row(db, order_id)
            if row["status"] != "issued" or not row["card_ciphertext"]:
                raise ConflictError("必须成功生成卡密后才能创建领取链接")
            if row["delivery_mode"] != "claim":
                raise ConflictError("该订单的发货方式不是网页领取")
            if row["claim_ciphertext"]:
                return self._decrypt(row["claim_ciphertext"])
            token = secrets.token_urlsafe(32)
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            db.execute("UPDATE orders SET claim_hash=?,claim_ciphertext=? WHERE order_id=?",
                       (digest, self._encrypt(token), order_id))
            db.commit()
            return token

    def get_claim(self, token: str) -> dict:
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            raise NotFoundError("领取链接无效")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM orders WHERE claim_hash=? AND status='issued'", (digest,)).fetchone()
            if not row or not row["card_ciphertext"]:
                raise NotFoundError("领取链接无效")
            card = self._decrypt(row["card_ciphertext"])
            claimed_at = row["claimed_at"] or _now()
            db.execute("UPDATE orders SET claimed_at=?,claim_views=claim_views+1 WHERE order_id=?", (claimed_at, row["order_id"]))
            db.commit()
            product = json.loads(row["product_json"])
            return {"product": {key: product.get(key) for key in (
                "id", "name", "duration_days", "template_name", "requires_own_115", "delivery_mode"
            )}, "card": card, "claimed_at": claimed_at}
