"""HTTP integration checks; isolated databases, in-memory keys, fake NextEmby."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import APIError, COOKIE, create_app
from core import load_products
from nextemby import NextEmbyError
from secrets_store import FileSecrets, MemorySecrets


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.vault.set('nextemby', 'ne_agent_TEST_ONLY')
        self.environment = patch.dict(os.environ, {
            'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example:888',
            'MEDIADOCK_REQUIRE_HTTPS': '1',
            'MEDIADOCK_PUBLIC_BASE_URL': 'https://cards.example',
            'MEDIADOCK_XIANYU_ADMIN_URL': 'http://127.0.0.1:59188',
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_user_center_frame_is_limited_to_discovery_document(self):
        app = create_app(self.temp.name, self.vault)
        with TestClient(app, base_url='https://cards.example') as client:
            self.assertIn("frame-src 'self';", client.get('/discover').headers['content-security-policy'])
            self.assertEqual(client.post('/api/auth/setup', json={'password': 'isolated-test-password'}).status_code, 200)
            self.assertEqual(client.post('/api/admin/settings', json={'redemption_url': 'https://center.example:8443/account'}).status_code, 200)
            for path in ('/', '/admin', '/library', '/discover'):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                policy = response.headers['content-security-policy']
                self.assertEqual("frame-src 'self' https://center.example:8443;" in policy, path == '/discover')
                self.assertNotIn('user.example.com', policy)
                self.assertIn("frame-ancestors 'none'", policy)
                self.assertEqual(response.headers['x-frame-options'], 'DENY')
            for center_url, expected in (('https://other.example/login', ' https://other.example'),
                                         ('http://center.example', ''), ('', '')):
                self.assertEqual(client.post('/api/admin/settings', json={'redemption_url': center_url}).status_code, 200)
                policy = client.get('/discover').headers['content-security-policy']
                self.assertIn("frame-src 'self'" + expected + ";", policy)

    def test_deployment_endpoint_is_used_and_https_only_blocks_plaintext(self):
        endpoints = []
        remote = FakeNextEmby()
        def factory(url, _token):
            endpoints.append(url)
            return remote
        app = create_app(self.temp.name, self.vault, factory)
        with TestClient(app, base_url='https://cards.example') as client:
            self.assertEqual(client.post('/api/auth/setup', json={'password': 'isolated-test-password'}).status_code, 200)
            self.assertEqual(client.post('/api/admin/nextemby/check', json={}).status_code, 200)
        self.assertEqual(endpoints, ['https://nextemby.example:888'])
        self.assertEqual(remote.generated, [])
        with patch.dict(os.environ, {'MEDIADOCK_NEXTEMBY_URL': 'http://nextemby.example'}):
            with self.assertRaises(NextEmbyError):
                create_app(self.temp.name, self.vault)

    def test_secure_cookie_and_origin_use_asgi_scheme_not_untrusted_header(self):
        app = create_app(self.temp.name, self.vault)
        with TestClient(app, base_url='https://cards.example') as client:
            response = client.post('/api/auth/setup', json={'password': 'isolated-test-password'})
            self.assertIn('; Secure', response.headers['set-cookie'])
            self.assertEqual(client.get('/api/admin/overview').status_code, 200)
            self.assertEqual(client.post('/api/auth/logout', json={}, headers={'Origin': 'http://cards.example'}).status_code, 403)
            response = client.post('/api/auth/logout', json={}, headers={'Origin': 'https://cards.example'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('; Secure', response.headers['set-cookie'])
        with TestClient(app, base_url='http://127.0.0.1:59189') as tunnel:
            response = tunnel.post('/api/auth/login', json={'username': 'admin', 'password': 'isolated-test-password'},
                                   headers={'X-Forwarded-Proto': 'https'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('; Secure', response.headers['set-cookie'])
            self.assertEqual(tunnel.get('/api/admin/overview').status_code, 200)

    def test_public_url_overrides_old_settings_and_rejects_unsafe_updates(self):
        app = create_app(self.temp.name, self.vault)
        app.state.control.set('settings', {'public_base_url': 'http://old-host.example'})
        with TestClient(app, base_url='http://127.0.0.1:59189') as tunnel:
            self.assertEqual(tunnel.post('/api/auth/setup', json={'password': 'isolated-test-password'}).status_code, 200)
            settings = tunnel.get('/api/admin/overview').json()['settings']
            self.assertEqual(settings['public_base_url'], 'https://cards.example')
            self.assertTrue(settings['public_base_url_managed'])
            for value in ('https://changed.example', 'http://cards.example', 'https://cards.example/path',
                          'https://user:password@cards.example', 'https://cards.example?token=hidden'):
                response = tunnel.post('/api/admin/settings', json={'public_base_url': value})
                self.assertEqual(response.status_code, 400)
            self.assertEqual(tunnel.post('/api/admin/settings', json={'public_base_url': 'https://cards.example/'}).status_code, 200)
        with patch.dict(os.environ, {'MEDIADOCK_PUBLIC_BASE_URL': 'http://public.example'}):
            with self.assertRaises(APIError):
                create_app(self.temp.name, self.vault)

    def test_strict_deployment_keeps_unconfigured_loopback_site_usable(self):
        with patch.dict(os.environ, {'MEDIADOCK_PUBLIC_BASE_URL': ''}):
            app = create_app(self.temp.name, self.vault)
            with TestClient(app, base_url='http://127.0.0.1:59189') as tunnel:
                self.assertEqual(tunnel.get('/health').status_code, 200)

    def test_file_backend_reopens_existing_encrypted_card_without_generation(self):
        remote = FakeNextEmby()
        app = create_app(self.temp.name, self.vault, lambda _url, _token: remote)
        products = load_products(Path(__file__).resolve().parent.parent / 'product-mapping.json')
        app.state.store.create_order('MIGRATION-TEST', 'free_monthly', 'direct', 'manual')
        app.state.store.approve_order('MIGRATION-TEST', 'isolated test evidence')
        app.state.store.issue_order('MIGRATION-TEST', remote, products)
        directory = Path(self.temp.name).resolve() / 'secrets'
        migrated = FileSecrets(directory)
        for name in ('card-encryption', 'nextemby', 'bridge'):
            migrated.set(name, self.vault.ensure(name))
        with patch.dict(os.environ, {'MEDIADOCK_SECRETS_DIR': str(directory)}):
            reopened = create_app(self.temp.name, nextemby_factory=lambda _url, _token: remote)
        self.assertIsInstance(reopened.state.vault, FileSecrets)
        self.assertEqual(reopened.state.store.get_order('MIGRATION-TEST', reveal=True)['card'], 'TEST-SECRET-CARD-1')
        self.assertEqual(len(remote.generated), 1)


class FakeNextEmby:
    def __init__(self):
        self.generated = []
        self.reads = 0
        self.fail = False

    def generate(self, product):
        self.generated.append(dict(product))
        if self.fail:
            raise TimeoutError("DO-NOT-EXPOSE-REMOTE-TOKEN")
        return {"batch_id": "TEST-BATCH-%d" % len(self.generated), "card": "TEST-SECRET-CARD-%d" % len(self.generated)}

    def read_batches(self):
        self.reads += 1
        return [{"batch_id": "TEST-METADATA", "remaining": 1}]


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.vault.set("nextemby", "ne_agent_TEST_ONLY_NOT_A_REAL_TOKEN")
        self.remote = FakeNextEmby()
        self.app = create_app(Path(self.temp.name), self.vault, lambda _url, _token: self.remote)
        self.admin = TestClient(self.app, base_url="http://127.0.0.1:59189")
        self.guest = TestClient(self.app, base_url="http://127.0.0.1:59189")
        response = self.admin.post("/api/auth/setup", json={"password": "local-test-password"})
        self.assertEqual(response.status_code, 200, response.text)

    def tearDown(self):
        self.admin.close()
        self.guest.close()
        self.temp.cleanup()

    def configure_web_payment(self):
        response = self.admin.post('/api/admin/settings', json={'payment_wechat_url': '/static/test-wechat.png'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_unconfigured_web_payment_rejects_orders_without_writes(self):
        response = self.guest.post('/api/public/orders', json={'product_id': 'free_monthly', 'payment_configured': True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()['error']['code'], 'payment_not_configured')
        self.assertEqual(self.app.state.store.list_orders(), [])
        with self.app.state.control.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM public_orders').fetchone()[0], 0)
        self.assertEqual(self.remote.generated, [])

    def test_either_payment_method_enables_web_orders_and_removal_disables(self):
        for method in ('payment_wechat_url', 'payment_alipay_url'):
            settings = {'payment_wechat_url': '', 'payment_alipay_url': '', method: '/static/test-pay.png'}
            self.assertEqual(self.admin.post('/api/admin/settings', json=settings).status_code, 200)
            response = self.guest.post('/api/public/orders', json={'product_id': 'free_monthly'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()['payment_configured'])
        self.assertEqual(self.admin.post('/api/admin/settings', json={'payment_wechat_url': '', 'payment_alipay_url': ''}).status_code, 200)
        self.assertEqual(self.guest.post('/api/public/orders', json={'product_id': 'free_monthly'}).status_code, 409)
        self.assertEqual(len(self.app.state.store.list_orders()), 2)

    def create_web_order(self):
        self.configure_web_payment()
        response = self.guest.post("/api/public/orders", json={"product_id": "free_monthly", "contact_note": "测试买家"})
        self.assertEqual(response.status_code, 200, response.text)
        order = response.json()
        return order, {"X-Order-Token": order["access_token"]}

    def issue_admin_order(self, order_id):
        response = self.admin.post(f"/api/admin/orders/{order_id}/approve", json={"evidence_note": "已核对真实到账40元、订单与买家一致"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.admin.post(f"/api/admin/orders/{order_id}/issue", json={})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def configure_bridge(self, sku_id="SKU-FREE-MONTH", item_ids="1000000000001"):
        response = self.admin.post("/api/admin/settings", json={"xianyu_account_id": "SELLER-TEST"})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.admin.post("/api/admin/products/free_monthly", json={
            "xianyu_item_id": item_ids, "xianyu_sku_id": sku_id, "enabled": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        response = self.admin.get("/api/admin/bridge-config")
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    @staticmethod
    def bridge_payload(**changes):
        product = load_products(Path(__file__).resolve().parent.parent / 'product-mapping.json')['free_monthly']
        cents = product['listed_price_cents']
        body = {
            "account_id": "SELLER-TEST", "product_id": "free_monthly", "item_id": "1000000000001",
            "sku_id": "SKU-FREE-MONTH", "spec_name": product["xianyu_spec_name"], "spec_value": product["xianyu_spec_value"], "quantity": 1,
            "amount": f"{cents // 100}.{cents % 100:02d}", "order_id": "TRADE-TEST", "buyer_id": "BUYER-TEST", "delivery_mode": "direct",
        }
        return {**body, **changes}

    def test_admin_routes_require_session_and_session_logout_revokes(self):
        for route in ("/api/admin/overview", "/api/admin/bridge-config", "/api/admin/orders/missing/delivery"):
            self.assertEqual(self.guest.get(route).status_code, 401)
        for route in ("/api/admin/settings", "/api/admin/orders", "/api/admin/nextemby/check"):
            self.assertEqual(self.guest.post(route, json={}).status_code, 401)
        saved_token = self.admin.cookies.get(COOKIE)
        self.assertEqual(self.admin.post("/api/auth/logout", json={}).status_code, 200)
        self.admin.cookies.set(COOKIE, saved_token)
        self.assertEqual(self.admin.get("/api/admin/overview").status_code, 401)
        self.assertEqual(self.remote.generated, [])

    def test_login_password_setup_and_cookie_controls(self):
        self.assertEqual(self.guest.post("/api/auth/login", json={"username": "admin", "password": "incorrect-password"}).status_code, 401)
        self.assertEqual(self.guest.post("/api/auth/setup", json={"password": "replacement-pass"}).status_code, 409)
        response = self.guest.post("/api/auth/login", json={"username": "admin", "password": "local-test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)
        self.assertNotIn("local-test-password", response.text)
        self.assertEqual(self.guest.get("/api/admin/overview").status_code, 200)

    def test_origin_host_and_non_json_requests_blocked(self):
        route = "/api/admin/orders"
        payload = {"order_id": "MANUAL-TEST", "product_id": "free_monthly"}
        self.assertEqual(self.admin.post(route, json=payload, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.admin.post(route, json=payload, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.admin.post(route, data=payload).status_code, 415)
        self.assertEqual(self.admin.get("/api/admin/overview", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(self.app.state.store.list_orders(), [])
        self.assertFalse(self.remote.generated)

    def test_oversized_declared_request_rejected(self):
        response = self.admin.post("/api/admin/orders", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "999999"})
        self.assertEqual(response.status_code, 413)

    def test_chunked_body_cannot_bypass_actual_size_limit(self):
        import json
        payload = json.dumps({"product_id": "free_monthly", "padding": "X" * 40000}).encode()
        chunks = (payload[start:start + 1000] for start in range(0, len(payload), 1000))
        response = self.guest.post("/api/public/orders", content=chunks, headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.app.state.store.list_orders(), [])

    def test_web_self_reported_payment_cannot_issue_or_leak_card(self):
        self.configure_web_payment()
        response = self.guest.post("/api/public/orders", json={
            "product_id": "free_monthly", "status": "paid", "paid": True, "amount": "40.00", "payment_verified": True,
        })
        self.assertEqual(response.status_code, 200, response.text)
        order = response.json()
        order_id = order["order_id"]
        headers = {"X-Order-Token": order["access_token"]}
        self.assertEqual(order["status"], "pending_payment")
        response = self.guest.post(f"/api/public/orders/{order_id}/paid-notice", json={"paid": True}, headers=headers)
        self.assertEqual(response.status_code, 200)
        status = self.guest.get(f"/api/public/orders/{order_id}", headers=headers)
        self.assertEqual(status.json()["status"], "pending_review")
        self.assertNotIn("claim_url", status.json())
        self.assertNotIn("card", status.json())
        self.assertEqual(self.guest.post(f"/api/admin/orders/{order_id}/approve", json={"evidence_note": "已付款"}).status_code, 401)
        self.assertEqual(self.guest.post(f"/api/admin/orders/{order_id}/issue", json={}).status_code, 401)
        self.assertEqual(self.admin.post(f"/api/admin/orders/{order_id}/issue", json={}).status_code, 409)
        self.assertEqual(self.app.state.store.get_order(order_id)["status"], "pending_review")
        self.assertEqual(self.remote.generated, [])

    def test_public_order_requires_its_own_access_token(self):
        order, headers = self.create_web_order()
        other, wrong_headers = self.create_web_order()
        route = f"/api/public/orders/{order['order_id']}"
        self.assertEqual(self.guest.get(route).status_code, 404)
        self.assertEqual(self.guest.get(route, headers=wrong_headers).status_code, 404)
        self.assertEqual(self.guest.post(route + "/paid-notice", json={}, headers=wrong_headers).status_code, 404)
        self.assertEqual(self.guest.get(route, headers=headers).status_code, 200)
        self.assertNotEqual(order["access_token"], other["access_token"])

    def test_complete_web_order_approval_issue_claim_and_replay(self):
        order, headers = self.create_web_order()
        order_id = order["order_id"]
        self.guest.post(f"/api/public/orders/{order_id}/paid-notice", json={}, headers=headers)
        issued = self.issue_admin_order(order_id)
        self.assertEqual(issued["status"], "issued")
        self.assertNotIn("card", issued)
        status = self.guest.get(f"/api/public/orders/{order_id}", headers=headers).json()
        claim_url = status["claim_url"]
        self.assertIn("/claim#", claim_url)
        self.assertNotIn("?", claim_url)
        token = claim_url.split("#", 1)[1]
        first = self.guest.post("/api/claim", json={"token": token})
        second = self.guest.post("/api/claim", json={"token": token})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["card"], "TEST-SECRET-CARD-1")
        self.assertEqual(first.json()["product"]["template_name"], "free")
        self.assertEqual(first.json()["product"]["duration_days"], 30)
        self.assertNotIn("order_id", first.json())
        replay = self.admin.post(f"/api/admin/orders/{order_id}/issue", json={})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(self.guest.get(f"/api/public/orders/{order_id}", headers=headers).json()["claim_url"], claim_url)
        overview = self.admin.get("/api/admin/overview")
        self.assertNotIn("TEST-SECRET-CARD-1", overview.text)
        self.assertNotIn(token, overview.text)
        for header in ("cache-control", "referrer-policy", "content-security-policy", "x-frame-options"):
            self.assertIn(header, first.headers)
        self.assertEqual(first.headers["cache-control"], "no-store")

    def test_manual_direct_delivery_only_visible_to_admin_and_duplicate_safe(self):
        payload = {"order_id": "MANUAL-TEST", "product_id": "vip_trial", "delivery_mode": "direct"}
        response = self.admin.post("/api/admin/orders", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "pending_review")
        self.issue_admin_order("MANUAL-TEST")
        delivery = self.admin.get("/api/admin/orders/MANUAL-TEST/delivery")
        self.assertEqual(delivery.status_code, 200, delivery.text)
        self.assertIn("TEST-SECRET-CARD-1", delivery.json()["message"])
        self.assertNotIn("claim_url", delivery.json())
        self.assertEqual(self.guest.get("/api/admin/orders/MANUAL-TEST/delivery").status_code, 401)
        self.assertEqual(self.admin.post("/api/admin/orders", json=payload).json()["status"], "issued")
        self.assertEqual(self.admin.post("/api/admin/orders", json={**payload, "product_id": "free_monthly"}).status_code, 409)
        self.assertEqual(self.admin.post("/api/admin/orders/MANUAL-TEST/claim", json={}).status_code, 409)
        self.assertEqual(len(self.remote.generated), 1)

    def test_order_channels_preserve_approval_and_disallow_source_reassignment(self):
        self.configure_bridge()
        for source in ('web', 'xianyu', 'xiaohongshu', 'manual'):
            payload = {'order_id': 'CHANNEL-' + source, 'product_id': 'free_monthly', 'source': source}
            response = self.admin.post('/api/admin/orders', json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['source'], source)
            self.assertEqual(response.json()['status'], 'pending_review')
            order_id = response.json()['order_id']
            self.assertEqual(self.admin.post('/api/admin/orders/' + order_id + '/issue', json={}).status_code, 409)
            self.assertEqual(self.admin.post('/api/admin/orders', json=payload).status_code, 200)
            changed = 'web' if source != 'web' else 'xiaohongshu'
            self.assertEqual(self.admin.post('/api/admin/orders', json={**payload, 'order_id': order_id, 'source': changed}).status_code, 409)
        self.assertEqual(len(self.app.state.store.list_orders()), 4)
        for invalid in ('unverified-platform', ['web'], None):
            self.assertEqual(self.admin.post('/api/admin/orders', json={'order_id': 'INVALID', 'product_id': 'free_monthly', 'source': invalid}).status_code, 400)
        self.assertEqual(self.remote.generated, [])

    def test_manual_xianyu_raw_and_canonical_replay_share_the_bridge_card(self):
        headers = self.configure_bridge()
        payload = {'order_id': 'TRADE-TEST', 'product_id': 'free_monthly', 'source': 'xianyu',
                   'delivery_mode': 'direct', 'buyer_note': 'BUYER-TEST'}
        first = self.admin.post('/api/admin/orders', json=payload)
        self.assertEqual(first.status_code, 200, first.text)
        order_id = 'XY-SELLER-TEST-TRADE-TEST'
        self.assertEqual(first.json()['order_id'], order_id)
        self.assertEqual(first.json()['status'], 'pending_review')
        self.issue_admin_order(order_id)
        for supplied_id in ('TRADE-TEST', order_id):
            replay = self.admin.post('/api/admin/orders', json={**payload, 'order_id': supplied_id})
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(replay.json()['order_id'], order_id)
            self.assertEqual(replay.json()['status'], 'issued')
            self.assertNotIn('card', replay.json())
        bridge = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(), headers=headers)
        self.assertEqual(bridge.status_code, 200, bridge.text)
        self.assertIn('TEST-SECRET-CARD-1', bridge.json()['content'])
        self.assertEqual(len(self.app.state.store.list_orders()), 1)
        self.assertEqual(len(self.remote.generated), 1)

    def test_manual_intake_reuses_bridge_order_and_rejects_changed_product_or_mode(self):
        headers = self.configure_bridge()
        bridge = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(), headers=headers)
        self.assertEqual(bridge.status_code, 200, bridge.text)
        payload = {'order_id': 'TRADE-TEST', 'product_id': 'free_monthly', 'source': 'xianyu', 'delivery_mode': 'direct'}
        response = self.admin.post('/api/admin/orders', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['buyer_note'], 'BUYER-TEST')
        for changes in ({'product_id': 'vip_trial'}, {'delivery_mode': 'claim'}):
            response = self.admin.post('/api/admin/orders', json={**payload, **changes})
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.app.state.store.list_orders()), 1)
        self.assertEqual(len(self.remote.generated), 1)

    def test_manual_xianyu_requires_binding_and_rejects_foreign_or_nested_identity(self):
        payload = {'order_id': 'TRADE-TEST', 'product_id': 'free_monthly', 'source': 'xianyu'}
        self.assertEqual(self.admin.post('/api/admin/orders', json=payload).status_code, 409)
        self.configure_bridge()
        for changes in ({'order_id': 'XY-OTHER-SELLER-TRADE-TEST'}, {'account_id': 'OTHER-SELLER'},
                        {'order_id': 'XY-SELLER-TEST-XY-SELLER-TEST-TRADE-TEST'},
                        {'order_id': 'XY-SELLER-TEST-'}, {'order_id': 'X' * 81}):
            response = self.admin.post('/api/admin/orders', json={**payload, **changes})
            self.assertIn(response.status_code, (400, 409), response.text)
        self.assertEqual(self.app.state.store.list_orders(), [])
        self.assertEqual(self.remote.generated, [])

    def test_manual_pending_xianyu_does_not_gain_payment_authority_from_replay(self):
        headers = self.configure_bridge()
        for index, note in enumerate(('BUYER-TEST', '人工备注，不是买家标识')):
            raw_id = f'PENDING-{index}'
            response = self.admin.post('/api/admin/orders', json={
                'order_id': raw_id, 'product_id': 'free_monthly', 'source': 'xianyu', 'buyer_note': note,
                'paid': True, 'status': 'issued',
            })
            self.assertEqual(response.status_code, 200, response.text)
            bridge = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(order_id=raw_id), headers=headers)
            self.assertEqual(bridge.status_code, 409, bridge.text)
            saved = self.app.state.store.get_order(response.json()['order_id'])
            self.assertEqual(saved['status'], 'pending_review')
            self.assertEqual(saved['buyer_note'], note)
        self.assertEqual(self.remote.generated, [])

    def test_legacy_raw_orders_block_both_intake_paths_without_migration(self):
        headers = self.configure_bridge()
        for index, source in enumerate(('xianyu', 'manual', 'web', 'xiaohongshu')):
            raw_id = f'LEGACY-{index}'
            self.app.state.store.create_order(raw_id, 'free_monthly', 'direct', source)
            before = self.app.state.store.get_order(raw_id)
            for supplied_id in (raw_id, 'XY-SELLER-TEST-' + raw_id):
                response = self.admin.post('/api/admin/orders', json={
                    'order_id': supplied_id, 'product_id': 'free_monthly', 'source': 'xianyu',
                })
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()['error']['code'], 'order_conflict')
            bridge = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(order_id=raw_id), headers=headers)
            self.assertEqual(bridge.status_code, 409, bridge.text)
            self.assertEqual(self.app.state.store.get_order(raw_id), before)
        self.assertEqual(len(self.app.state.store.list_orders()), 4)
        self.assertEqual(self.remote.generated, [])

    def test_non_xianyu_intake_does_not_apply_seller_prefix(self):
        for source in ('web', 'xiaohongshu', 'manual'):
            raw_id = 'REFERENCE-' + source
            response = self.admin.post('/api/admin/orders', json={
                'order_id': raw_id, 'product_id': 'free_monthly', 'source': source,
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['order_id'], raw_id)
            self.assertEqual(response.json()['source'], source)
            self.assertEqual(response.json()['status'], 'pending_review')
        self.assertEqual(self.remote.generated, [])

    def test_bridge_cannot_reuse_a_canonical_looking_order_of_another_source(self):
        headers = self.configure_bridge()
        order_id = 'XY-SELLER-TEST-TRADE-TEST'
        self.app.state.store.create_order(order_id, 'free_monthly', 'direct', 'manual', 'BUYER-TEST')
        self.app.state.store.approve_order(order_id, 'isolated evidence')
        response = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(), headers=headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.app.state.store.get_order(order_id)['status'], 'eligible')
        self.assertEqual(self.remote.generated, [])

    def test_delivery_and_bridge_replay_use_the_issued_product_snapshot(self):
        headers = self.configure_bridge()
        first = self.guest.post('/api/bridge/fulfill', json=self.bridge_payload(), headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        order_id = 'XY-SELLER-TEST-TRADE-TEST'
        snapshot = self.app.state.store.get_order(order_id)['product']
        changed = load_products(Path(__file__).resolve().parent.parent / 'product-mapping.json')
        changed['free_monthly'].update(name='新名称，不应用于历史单', requires_own_115=False)
        with patch('app.load_products', return_value=changed):
            reopened = create_app(Path(self.temp.name), self.vault, lambda _url, _token: self.remote)
        with TestClient(reopened, base_url='http://127.0.0.1:59189') as client:
            client.cookies.set(COOKIE, self.admin.cookies.get(COOKIE))
            delivery = client.get(f'/api/admin/orders/{order_id}/delivery')
            self.assertEqual(delivery.status_code, 200, delivery.text)
            self.assertIn(snapshot['name'], delivery.json()['message'])
            self.assertIn('此套餐需要自备 115 网盘', delivery.json()['message'])
            self.assertNotIn('新名称', delivery.json()['message'])
            replay = client.post('/api/bridge/fulfill', json=self.bridge_payload(), headers=headers)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(first.json(), replay.json())
        self.assertEqual(self.app.state.store.get_order(order_id)['product'], snapshot)
        self.assertEqual(len(self.remote.generated), 1)

    def test_public_checkout_and_legacy_website_orders_group_as_web(self):
        order, _ = self.create_web_order()
        self.assertEqual(self.app.state.store.get_order(order['order_id'])['source'], 'web')
        with self.app.state.store._connect() as db:
            db.execute("UPDATE orders SET channel='' WHERE order_id=?", (order['order_id'],))
        overview = self.admin.get('/api/admin/overview').json()
        self.assertEqual(overview['orders'][0]['source'], 'web')
        self.assertEqual(self.remote.generated, [])

    def test_uncertain_generation_is_redacted_and_not_retried(self):
        order, _ = self.create_web_order()
        order_id = order["order_id"]
        self.admin.post(f"/api/admin/orders/{order_id}/approve", json={"evidence_note": "核实已到账"})
        self.remote.fail = True
        first = self.admin.post(f"/api/admin/orders/{order_id}/issue", json={})
        second = self.admin.post(f"/api/admin/orders/{order_id}/issue", json={})
        self.assertEqual((first.status_code, second.status_code), (409, 409))
        self.assertNotIn("DO-NOT-EXPOSE", first.text + second.text)
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(self.app.state.store.get_order(order_id)["status"], "needs_review")

    def test_readonly_connection_check_does_not_generate(self):
        response = self.admin.post("/api/admin/nextemby/check", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["batch_count"], 1)
        self.assertEqual(self.remote.reads, 1)
        self.assertEqual(self.remote.generated, [])

    def test_bridge_requires_secret_and_rejects_bad_identity_sku_amount_quantity(self):
        headers = self.configure_bridge()
        self.assertEqual(self.guest.post("/api/bridge/fulfill", json=self.bridge_payload()).status_code, 401)
        self.assertEqual(self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(), headers={"Authorization": "Bearer WRONG"}).status_code, 401)
        for changes in (
            {"account_id": "OTHER-SELLER"}, {"product_id": "free_trial"}, {"item_id": "1000000000003"},
            {"sku_id": "OTHER-SKU"}, {"spec_name": "错误规格名"}, {"spec_value": "UNMATCHED-ANNUAL-TEST"}, {"quantity": 2}, {"amount": "0.01"},
            {"amount": "NaN"}, {"amount": "Infinity"}, {"quantity": True},
        ):
            with self.subTest(changes=changes):
                response = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(**changes), headers=headers)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.remote.generated, [])
        self.assertEqual(self.app.state.store.list_orders(), [])

    def test_bridge_optional_sku_still_requires_exact_spec_name_and_value(self):
        headers = self.configure_bridge(sku_id="")
        for changes in ({"spec_name": ""}, {"spec_value": ""}, {"spec_name": "套餐"}, {"spec_value": "UNMATCHED-TRIAL-TEST"}):
            response = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(sku_id="", **changes), headers=headers)
            self.assertEqual(response.status_code, 409, response.text)
        self.assertFalse(self.remote.generated)
        body = self.bridge_payload(sku_id="")
        response = self.guest.post("/api/bridge/fulfill", json=body, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(self.remote.generated[0]["id"], "free_monthly")

    def test_both_bound_items_fulfill_and_unbound_third_item_is_rejected(self):
        headers = self.configure_bridge(sku_id="", item_ids="1000000000001,1000000000002")
        configured = self.admin.get("/api/admin/bridge-config").json()["products"]
        monthly = next(product for product in configured if product["id"] == "free_monthly")
        self.assertEqual(monthly["xianyu_item_id"], "1000000000001,1000000000002")
        self.assertEqual(monthly["xianyu_item_ids"], ["1000000000001", "1000000000002"])
        delivered = []
        for index, item_id in enumerate(monthly["xianyu_item_ids"], 1):
            body = self.bridge_payload(item_id=item_id, sku_id="", order_id=f"TRADE-TWO-ITEMS-{index}")
            response = self.guest.post("/api/bridge/fulfill", json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            replay = self.guest.post("/api/bridge/fulfill", json=body, headers=headers)
            self.assertEqual(replay.status_code, 200, replay.text)
            self.assertEqual(response.json(), replay.json())
            delivered.append(response.json()["content"])
        self.assertEqual(len(self.remote.generated), 2)
        self.assertNotEqual(delivered[0], delivered[1])
        rejected = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(
            item_id="1000000000003", sku_id="", order_id="TRADE-UNBOUND-ITEM"
        ), headers=headers)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["error"]["code"], "binding_mismatch")
        self.assertEqual(len(self.remote.generated), 2)
        self.assertEqual(len(self.app.state.store.list_orders()), 2)

    def test_item_bindings_reject_nonnumeric_or_more_than_ten_items(self):
        for item_ids in ("1000000000001,not-an-item", "１２３４５", ",".join(str(1000000000000 + i) for i in range(11))):
            with self.subTest(item_ids=item_ids):
                response = self.admin.post("/api/admin/products/free_monthly", json={
                    "xianyu_item_id": item_ids, "xianyu_sku_id": "", "enabled": True,
                })
                self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(self.app.state.control.get("bindings", {}))
        self.assertEqual(self.remote.generated, [])

    def test_bridge_replay_returns_same_card_and_wrong_buyer_cannot_reuse_order(self):
        headers = self.configure_bridge()
        first = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(), headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(), headers=headers)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()["order_id"], "TRADE-TEST")
        self.assertIn("TEST-SECRET-CARD-1", first.json()["content"])
        for changes in ({"buyer_id": "OTHER-BUYER"}, {"delivery_mode": "claim"}):
            response = self.guest.post("/api/bridge/fulfill", json=self.bridge_payload(**changes), headers=headers)
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.remote.generated), 1)
        self.assertEqual(self.remote.generated[0]["template_name"], "free")
        self.assertEqual(self.remote.generated[0]["duration_days"], 30)

    def test_bridge_claim_mode_reuses_claim_url(self):
        headers = self.configure_bridge()
        body = self.bridge_payload(delivery_mode="claim")
        first = self.guest.post("/api/bridge/fulfill", json=body, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.guest.post("/api/bridge/fulfill", json=body, headers=headers)
        self.assertEqual(first.json(), second.json())
        self.assertNotIn("TEST-SECRET-CARD-1", first.json()["content"])
        token = first.json()["content"].split("#", 1)[1]
        claim = self.guest.post("/api/claim", json={"token": token})
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["card"], "TEST-SECRET-CARD-1")
        self.assertEqual(len(self.remote.generated), 1)


if __name__ == "__main__":
    unittest.main()
