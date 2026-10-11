"""Purchase review security and fulfillment interoperation, with isolated fake data."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import create_app
from secrets_store import MemorySecrets
from test_app import FakeNextEmby
from core import load_products

PRODUCT = load_products(Path(__file__).parent.parent / 'product-mapping.json')['free_monthly']


class PurchaseReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.vault.set('nextemby', 'ne_agent_TEST_ONLY')
        self.remote = FakeNextEmby()
        self.app = create_app(Path(self.temp.name), self.vault, lambda _url, _token: self.remote)
        self.admin = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.guest = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.assertEqual(self.admin.post('/api/auth/setup', json={'password': 'isolated-review-password'}).status_code, 200)
        self.assertEqual(self.admin.post('/api/admin/settings', json={'xianyu_account_id': 'SELLER-TEST'}).status_code, 200)
        self.store = self.app.state.store
        self.queue = self.app.state.purchase_reviews
        self.payload = {'product_id': 'free_monthly', 'xianyu_order_id': '1234567890123456789', 'buyer_note': '昵称：买家甲'}
        self.evidence = {'buyer_id': 'BUYER-TEST', 'evidence_note': '已在闲鱼核对到账40元，卖家、买家与套餐一致'}
        self.canonical = 'XY-SELLER-TEST-' + self.payload['xianyu_order_id']

    def tearDown(self):
        self.admin.close()
        self.guest.close()
        self.temp.cleanup()

    def submit(self, **changes):
        response = self.guest.post('/api/public/purchase-reviews', json={**self.payload, **changes})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def status(self, review, **headers):
        return self.guest.get('/api/public/purchase-reviews/' + review['request_id'],
                              headers={'X-Order-Token': review['access_token'], **headers})

    def approve(self, review, **changes):
        return self.admin.post('/api/admin/purchase-reviews/' + review['request_id'] + '/approve',
                               json={**self.evidence, **changes})

    def bridge(self, **changes):
        binding = self.admin.post('/api/admin/products/free_monthly', json={
            'xianyu_item_id': '1089607470736', 'xianyu_sku_id': 'SKU-MONTH', 'enabled': True})
        self.assertEqual(binding.status_code, 200, binding.text)
        config = self.admin.get('/api/admin/bridge-config').json()
        body = {'account_id': 'SELLER-TEST', 'product_id': 'free_monthly', 'item_id': '1089607470736',
                'sku_id': 'SKU-MONTH', 'spec_name': PRODUCT['xianyu_spec_name'], 'spec_value': PRODUCT['xianyu_spec_value'],
                'quantity': 1, 'amount': f"{PRODUCT['listed_price_cents'] / 100:.2f}", 'order_id': self.payload['xianyu_order_id'],
                'buyer_id': 'BUYER-TEST', 'delivery_mode': 'direct', **changes}
        return self.admin.post('/api/bridge/fulfill', json=body,
                               headers={'Authorization': 'Bearer ' + config['token']})

    def assert_no_public_delivery(self, result):
        self.assertEqual(set(result), {'request_id', 'product_id', 'source', 'status', 'message'})
        self.assertNotIn('BUYER-TEST', str(result))
        self.assertNotIn(self.canonical, str(result))
        self.assertNotIn('TEST-SECRET', str(result))

    def test_public_submission_is_only_a_notice_even_with_spoofed_payment_fields(self):
        review = self.submit(status='issued', paid=True, buyer_id='FORGED', source='web', delivery_mode='claim')
        self.assertEqual(review['status'], 'pending_review')
        self.assertEqual(review['source'], 'xianyu')
        self.assertTrue(review['request_id'].startswith('XR-'))
        self.assertNotEqual(review['request_id'], self.payload['xianyu_order_id'])
        self.assertEqual(self.store.list_orders(), [])
        self.assertEqual(self.remote.generated, [])
        result = self.status(review).json()
        self.assert_no_public_delivery(result)
        with self.app.state.control.db() as db:
            row = db.execute('SELECT * FROM purchase_reviews').fetchone()
        self.assertNotEqual(row['token_hash'], review['access_token'])
        self.assertNotIn(review['access_token'], str(dict(row)))

    def test_per_request_tokens_and_admin_session_are_required(self):
        first, second = self.submit(), self.submit()
        for token in ('', 'wrong', second['access_token']):
            response = self.status(first, **{'X-Order-Token': token})
            self.assertEqual(response.status_code, 404)
        for action in ('approve', 'reject'):
            response = self.guest.post('/api/admin/purchase-reviews/' + first['request_id'] + '/' + action,
                                       json={**self.evidence, 'reason': 'not found'})
            self.assertEqual(response.status_code, 401)
        self.assertEqual(self.guest.get('/api/admin/overview').status_code, 401)
        # A notice token cannot authenticate another endpoint, even if the raw
        # marketplace order happens to exist in the fulfillment ledger.
        response = self.guest.get('/api/public/orders/' + self.canonical,
                                  headers={'X-Order-Token': first['access_token']})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.store.list_orders(), [])

    def test_invalid_public_identifiers_and_notes_are_rejected_without_orders(self):
        for value in ('XY-SELLER-TEST-123', 'abc', '１２３', '123/456', '9' * 81, '', 123):
            # Call validation directly so invalid-input checks do not exhaust
            # the intentionally small public rate limit.
            with self.assertRaises(ValueError):
                self.queue.submit({**self.payload, 'xianyu_order_id': value})
        for changes in ({'product_id': 'absent'}, {'buyer_note': 'x' * 121}, {'buyer_note': 'a\nb'}):
            response = self.guest.post('/api/public/purchase-reviews', json={**self.payload, **changes})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.queue.list_admin(), [])
        self.assertEqual(self.store.list_orders(), [])

    def test_public_queue_has_a_separate_creation_limit_and_keeps_web_flow(self):
        for _ in range(5):
            self.submit()
        blocked = self.guest.post('/api/public/purchase-reviews', json=self.payload)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(self.admin.post('/api/admin/settings', json={'payment_alipay_url': '/static/test-pay.png'}).status_code, 200)
        web = self.guest.post('/api/public/orders', json={'product_id': 'free_monthly', 'contact_note': '网页客户'})
        self.assertEqual(web.status_code, 200, web.text)
        self.assertEqual(web.json()['status'], 'pending_payment')
        self.assertEqual(len(self.store.list_orders()), 1)
        self.assertEqual(self.remote.generated, [])

    def test_admin_overview_separates_notices_from_orders_and_contains_no_token(self):
        review = self.submit()
        overview = self.admin.get('/api/admin/overview').json()
        self.assertEqual(overview['orders'], [])
        self.assertEqual(overview['stats']['total'], 0)
        rows = overview['purchase_reviews']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['request_id'], review['request_id'])
        self.assertEqual(rows[0]['xianyu_order_id'], self.payload['xianyu_order_id'])
        self.assertEqual(rows[0]['product']['id'], 'free_monthly')
        self.assertIsNone(rows[0]['order_id'])
        self.assertNotIn('token', str(rows))

    def test_approval_requires_buyer_and_evidence_then_only_marks_ledger_eligible(self):
        review = self.submit()
        for changes in ({'buyer_id': ''}, {'evidence_note': ''}, {'evidence_note': '已核对'}):
            self.assertEqual(self.approve(review, **changes).status_code, 400)
        self.assertEqual(self.store.list_orders(), [])
        approved = self.approve(review)
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json(), {'order_id': self.canonical, 'status': 'approved'})
        order = self.store.get_order(self.canonical)
        self.assertEqual(order['status'], 'eligible')
        self.assertEqual(order['source'], 'xianyu')
        self.assertEqual(order['buyer_note'], 'BUYER-TEST')
        self.assertEqual(order['delivery_mode'], 'direct')
        self.assertFalse(order['has_card'])
        self.assertFalse(order['has_claim'])
        self.assertEqual(self.remote.generated, [])
        self.assert_no_public_delivery(self.status(review).json())

    def test_duplicate_approvals_and_notices_reuse_single_canonical_order(self):
        first, second = self.submit(), self.submit()
        for review in (first, first, second):
            response = self.approve(review)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['order_id'], self.canonical)
        self.assertEqual(len(self.store.list_orders()), 1)
        self.assertEqual(self.remote.generated, [])
        changed = self.approve(first, buyer_id='OTHER-BUYER')
        self.assertEqual(changed.status_code, 409)

    def test_approved_notice_and_bridge_replay_generate_only_once(self):
        review = self.submit()
        self.assertEqual(self.approve(review).status_code, 200)
        for _ in range(2):
            response = self.bridge()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('TEST-SECRET-CARD-1', response.json()['content'])
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(len(self.store.list_orders()), 1)
        self.assert_no_public_delivery(self.status(review).json())

    def test_bridge_can_issue_while_untrusted_notice_waits_and_approval_reuses_card(self):
        review = self.submit()
        response = self.bridge(delivery_mode='claim')
        self.assertEqual(response.status_code, 200, response.text)
        before = self.store.get_order(self.canonical, reveal=True)
        self.assertEqual(self.approve(review).status_code, 200)
        after = self.store.get_order(self.canonical, reveal=True)
        self.assertEqual(before, after)
        self.assertEqual(after['delivery_mode'], 'claim')
        self.assertTrue(after['has_claim'])
        self.assertEqual(len(self.remote.generated), 1)
        self.assert_no_public_delivery(self.status(review).json())

    def test_forged_submission_does_not_poison_bridge_and_cannot_approve_wrong_identity(self):
        review = self.submit(product_id='vvip_annual', buyer_note='forged')
        self.assertEqual(self.bridge().status_code, 200)
        self.assertEqual(self.approve(review).status_code, 409)
        actual = self.submit()
        self.assertEqual(self.approve(actual, buyer_id='WRONG-BUYER').status_code, 409)
        self.assertEqual(self.status(actual).json()['status'], 'pending_review')
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(len(self.store.list_orders()), 1)

    def test_processing_needs_review_or_legacy_orders_require_manual_attention(self):
        review = self.submit()
        self.store.create_order(self.canonical, 'free_monthly', 'direct', 'xianyu', buyer_note='BUYER-TEST')
        for status in ('processing', 'needs_review'):
            with self.store._connect() as db:
                db.execute('UPDATE orders SET status=? WHERE order_id=?', (status, self.canonical))
            response = self.approve(review)
            self.assertEqual(response.status_code, 409, response.text)
        with self.store._connect() as db:
            db.execute('DELETE FROM orders WHERE order_id=?', (self.canonical,))
        self.store.create_order(self.payload['xianyu_order_id'], 'free_monthly', 'direct', 'manual')
        self.assertEqual(self.approve(review).status_code, 409)
        self.assertEqual(len(self.store.list_orders()), 1)
        self.assertEqual(self.remote.generated, [])

    def test_binding_changes_cannot_rebind_pending_requests(self):
        review = self.submit()
        self.admin.post('/api/admin/settings', json={'xianyu_account_id': 'SECOND-SELLER'})
        response = self.approve(review)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'binding_mismatch')
        self.assertEqual(self.store.list_orders(), [])
        self.admin.post('/api/admin/settings', json={'xianyu_account_id': ''})
        response = self.guest.post('/api/public/purchase-reviews', json=self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.queue.list_admin()), 1)

    def test_rejection_is_terminal_and_does_not_expose_admin_notes(self):
        review = self.submit()
        path = '/api/admin/purchase-reviews/' + review['request_id'] + '/reject'
        for _ in range(2):
            response = self.admin.post(path, json={'reason': 'PRIVATE-REVIEW-NOTE belongs to OTHER-BUYER'})
            self.assertEqual(response.status_code, 200)
        status = self.status(review).json()
        self.assertEqual(status['status'], 'rejected')
        self.assert_no_public_delivery(status)
        self.assertNotIn('PRIVATE-REVIEW-NOTE', str(status))
        self.assertNotIn('OTHER-BUYER', str(status))
        self.assertEqual(self.approve(review).status_code, 409)
        self.assertEqual(self.store.list_orders(), [])
        second = self.submit(xianyu_order_id='987654321')
        self.assertEqual(self.approve(second).status_code, 200)
        response = self.admin.post('/api/admin/purchase-reviews/' + second['request_id'] + '/reject', json={'reason': 'review again'})
        self.assertEqual(response.status_code, 409)

    def test_interruption_after_ledger_approval_is_recoverable_without_duplicate_order(self):
        review = self.submit()
        original = self.store.approve_order
        def crash_after_approval(*args):
            original(*args)
            raise RuntimeError('simulated interruption before request commit')
        with patch.object(self.store, 'approve_order', side_effect=crash_after_approval):
            with self.assertRaises(RuntimeError):
                self.queue.approve(review['request_id'], self.evidence)
        self.assertEqual(self.store.get_order(self.canonical)['status'], 'eligible')
        self.assertEqual(self.status(review).json()['status'], 'pending_review')
        self.assertEqual(self.approve(review).status_code, 200)
        self.assertEqual(len(self.store.list_orders()), 1)
        self.assertEqual(self.remote.generated, [])

    def test_configuration_snapshot_mismatch_does_not_approve(self):
        review = self.submit()
        with patch.dict(self.queue.products['free_monthly'], {'listed_price_cents': 9900}):
            self.assertEqual(self.approve(review).status_code, 409)
        self.assertEqual(self.store.list_orders(), [])


if __name__ == '__main__':
    unittest.main()
