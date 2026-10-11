"""Untrusted marketplace purchase notices, separate from the card ledger.

An order number is not proof of ownership. A public request token therefore only
reads that request's review status; it never unlocks a card or a claim link.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse


class PurchaseReviewError(ValueError):
    def __init__(self, message, code='invalid_purchase_review', status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def _text(value, field, limit, required=True):
    if (not isinstance(value, str) or len(value) > limit
            or (required and not value.strip()) or any(ord(c) < 32 for c in value)):
        raise PurchaseReviewError(f'{field}格式不正确')
    return value.strip()


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class PurchaseReviews:
    def __init__(self, control, store, products, settings, identity, guard_legacy):
        self.control, self.store, self.products = control, store, products
        self.settings, self.identity, self.guard_legacy = settings, identity, guard_legacy
        with control.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS purchase_reviews (
                    request_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    seller_account_id TEXT NOT NULL,
                    xianyu_order_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    product_json TEXT NOT NULL,
                    buyer_note TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('pending_review','approved','rejected')),
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    linked_order_id TEXT,
                    verified_buyer_id TEXT,
                    review_note TEXT
                );
                CREATE INDEX IF NOT EXISTS purchase_reviews_status_created
                    ON purchase_reviews(status, created_at);
            ''')

    @staticmethod
    def _public(row):
        messages = {
            'pending_review': '已提交闲鱼订单核对，等待管理员审核。卡密通过原闲鱼会话发放。',
            'approved': '订单已核对通过，请在原闲鱼会话查看发货进度或领取卡密。',
            'rejected': '本次申请未通过核对，请通过原闲鱼会话联系卖家。',
        }
        return {'request_id': row['request_id'], 'source': 'xianyu',
                'product_id': row['product_id'], 'status': row['status'],
                'message': messages[row['status']]}

    @staticmethod
    def _admin(row):
        result = {key: row[key] for key in (
            'request_id', 'xianyu_order_id', 'product_id', 'buyer_note', 'status',
            'created_at', 'reviewed_at', 'review_note')}
        result.update(source='xianyu', product=json.loads(row['product_json']),
                      order_id=row['linked_order_id'])
        return result

    @staticmethod
    def _row(db, request_id):
        row = db.execute('SELECT * FROM purchase_reviews WHERE request_id=?', (request_id,)).fetchone()
        if not row:
            raise PurchaseReviewError('申请不存在或查询凭证不正确', 'not_found', 404)
        return row

    def submit(self, body):
        product_id = _text(body.get('product_id'), '套餐', 160)
        if product_id not in self.products:
            raise PurchaseReviewError('套餐不存在')
        raw_id = _text(body.get('xianyu_order_id'), '闲鱼订单号', 80)
        if not raw_id.isascii() or not raw_id.isdigit():
            raise PurchaseReviewError('请填写闲鱼订单详情中的数字订单号')
        note = _text(body.get('buyer_note', ''), '闲鱼昵称或备注', 120, False)
        # Do not look up the fulfillment ledger here: even a forged or duplicate
        # purchase notice must neither reserve an order ID nor reveal its state.
        with self.control.lock, self.control.db() as db:
            account = self.settings()['xianyu_account_id']
            self.identity(raw_id, account)
            if db.execute('SELECT COUNT(*) FROM purchase_reviews').fetchone()[0] >= 10000:
                raise PurchaseReviewError('暂时无法接收申请，请通过闲鱼联系卖家', 'queue_full', 503)
            request_id, token = 'XR-' + secrets.token_hex(12).upper(), secrets.token_urlsafe(32)
            db.execute('''INSERT INTO purchase_reviews
                (request_id,token_hash,seller_account_id,xianyu_order_id,product_id,product_json,
                 buyer_note,status,created_at) VALUES(?,?,?,?,?,?,?,'pending_review',?)''',
                (request_id, _digest(token), account, raw_id, product_id,
                 json.dumps(self.products[product_id], ensure_ascii=False), note, _now()))
            result = self._public(self._row(db, request_id))
        return {**result, 'access_token': token}

    def public_status(self, request_id, token):
        with self.control.db() as db:
            row = self._row(db, request_id)
            if not hmac.compare_digest(row['token_hash'], _digest(token)):
                raise PurchaseReviewError('申请不存在或查询凭证不正确', 'not_found', 404)
            return self._public(row)

    def list_admin(self):
        with self.control.db() as db:
            return [self._admin(row) for row in db.execute(
                'SELECT * FROM purchase_reviews ORDER BY created_at DESC,rowid DESC')]

    def approve(self, request_id, body):
        buyer = _text(body.get('buyer_id'), '已核验的闲鱼买家 ID', 100)
        evidence = _text(body.get('evidence_note'), '到账核对说明', 240)
        if len(evidence) < 6:
            raise PurchaseReviewError('请填写至少 6 字的到账核对说明')
        # This is the same lock used by the trusted Xianyu bridge and manual
        # ledger intake. Settings changes also take it so seller binding cannot
        # change between canonical identity validation and order creation.
        with self.control.lock, self.control.db() as db:
            row = self._row(db, request_id)
            if row['status'] == 'rejected':
                raise PurchaseReviewError('已拒绝的申请不能通过，请重新核对', 'review_conflict', 409)
            if row['status'] == 'approved':
                if row['verified_buyer_id'] != buyer:
                    raise PurchaseReviewError('已审核申请的买家身份不能修改', 'order_conflict', 409)
                return {'order_id': row['linked_order_id'], 'status': 'approved'}
            account = self.settings()['xianyu_account_id']
            if not account or row['seller_account_id'] != account:
                raise PurchaseReviewError('闲鱼卖家账号已变更，请核查原账号订单；本申请不会创建发卡订单',
                                          'binding_mismatch', 409)
            raw_id, order_id = self.identity(row['xianyu_order_id'], account)
            self.guard_legacy(raw_id)
            snapshot = json.loads(row['product_json'])
            fields = ('id', 'template_name', 'duration_days', 'listed_price_cents', 'requires_own_115')
            current = self.products.get(row['product_id'], {})
            if any(snapshot.get(field) != current.get(field) for field in fields):
                raise PurchaseReviewError('套餐配置已变更，请核对申请时的套餐信息后重新处理', 'order_conflict', 409)
            existing = next((order for order in self.store.list_orders() if order['order_id'] == order_id), None)
            if existing:
                if (existing['source'] != 'xianyu' or existing['product_id'] != row['product_id']
                        or existing['buyer_note'] != buyer):
                    raise PurchaseReviewError('闲鱼订单的套餐或买家与历史记录不一致，请打开原订单核对',
                                              'order_conflict', 409)
                if any(snapshot.get(field) != existing['product'].get(field) for field in fields):
                    raise PurchaseReviewError('原订单的套餐快照与本次申请不一致，请打开原订单核对',
                                              'order_conflict', 409)
                if existing['status'] not in {'pending_review', 'eligible', 'issued'}:
                    raise PurchaseReviewError('原订单正在处理或结果待核实，请在订单管理中核查',
                                              'order_conflict', 409)
            else:
                existing = self.store.create_order(order_id, row['product_id'], 'direct', 'xianyu', buyer_note=buyer)
            if existing['status'] == 'pending_review':
                self.store.approve_order(order_id, evidence)
            # No card generation, claim-token creation, delivery-mode changes or
            # external messaging occurs as part of approving a public notice.
            db.execute('''UPDATE purchase_reviews SET status='approved',reviewed_at=?,linked_order_id=?,
                verified_buyer_id=?,review_note=? WHERE request_id=?''',
                (_now(), order_id, buyer, evidence, request_id))
            return {'order_id': order_id, 'status': 'approved'}

    def reject(self, request_id, body):
        reason = _text(body.get('reason'), '审核备注', 240)
        with self.control.lock, self.control.db() as db:
            row = self._row(db, request_id)
            if row['status'] == 'approved':
                raise PurchaseReviewError('已通过的申请不能改为拒绝，请核查关联订单', 'review_conflict', 409)
            if row['status'] == 'pending_review':
                db.execute("UPDATE purchase_reviews SET status='rejected',reviewed_at=?,review_note=? WHERE request_id=?",
                           (_now(), reason, request_id))
            return {'request_id': request_id, 'status': 'rejected'}


def install_routes(app, admin, rate, control, store, products, settings, identity, guard_legacy):
    reviews = PurchaseReviews(control, store, products, settings, identity, guard_legacy)
    app.state.purchase_reviews = reviews

    @app.exception_handler(PurchaseReviewError)
    async def review_error(request, exc):
        return JSONResponse({'error': {'message': exc.message, 'code': exc.code}}, status_code=exc.status)

    @app.post('/api/public/purchase-reviews')
    def submit(request: Request, body: dict):
        rate(request, 'purchase_review_create', 5, 3600)
        return reviews.submit(body)

    @app.get('/api/public/purchase-reviews/{request_id}')
    def status(request_id: str, request: Request):
        rate(request, 'purchase_review_status', 60, 60)
        return reviews.public_status(request_id, request.headers.get('X-Order-Token', ''))

    @app.post('/api/admin/purchase-reviews/{request_id}/approve')
    def approve(request_id: str, request: Request, body: dict):
        admin(request)
        rate(request, 'purchase_review_admin', 30, 60)
        return reviews.approve(request_id, body)

    @app.post('/api/admin/purchase-reviews/{request_id}/reject')
    def reject(request_id: str, request: Request, body: dict):
        admin(request)
        rate(request, 'purchase_review_admin', 30, 60)
        return reviews.reject(request_id, body)

    return reviews
