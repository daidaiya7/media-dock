"""小红书代理隔离、凭据脱敏、保存与应用结果分离的回归测试。"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import create_app
from secrets_store import MemorySecrets
from xianyu_proxy import ProxyConfigError
from xiaohongshu import XiaohongshuClient, XiaohongshuProxyConfig


class XiaohongshuProxyTests(unittest.TestCase):
    def setUp(self):
        self.vault = MemorySecrets()
        self.vault.set('xiaohongshu-bridge', 'isolated-bridge-key')
        self.vault.set('xianyu-proxy', '{"preserved":"xianyu-private-config"}')
        self.old_xianyu = self.vault.get('xianyu-proxy')
        self.body = dict(enabled=True, scheme='https', host='proxy.example', port=8443,
                         username='proxy-user-secret', password='proxy-password-secret')
        self.mode = 'running'
        self.requests = []

        def handler(request):
            self.requests.append(request)
            config = json.loads(self.vault.get('xiaohongshu-proxy') or '{}')
            if request.url.path == '/proxy/apply' and self.mode == 'failed':
                return httpx.Response(503, json={'error': {'code': 'blocked_network', 'message': self.body['password']}})
            if request.url.path == '/proxy/apply' and self.mode == 'timeout':
                raise httpx.ReadTimeout(self.body['password'])
            running = self.mode in {'running', 'restricted'}
            return httpx.Response(200, json={
                'state': 'needs_attention' if self.mode == 'restricted' else 'ready' if running else 'stopped',
                'account_id': '', 'account_name': '', 'conversation_id': '', 'browser_running': running,
                'proxy_revision': config.get('revision', '') if running else '',
                'proxy_enabled': bool(config.get('enabled')) if running else False,
                'private_config': config,
            })

        self.client = XiaohongshuClient(self.vault, transport=httpx.MockTransport(handler))
        self.config = XiaohongshuProxyConfig(self.vault, self.client)

    def assert_private(self, result):
        serialized = json.dumps(result)
        self.assertNotIn(self.body['username'], serialized)
        self.assertNotIn(self.body['password'], serialized)
        self.assertNotIn('isolated-bridge-key', serialized)
        self.assertEqual(self.vault.get('xianyu-proxy'), self.old_xianyu)

    def test_save_apply_is_private_local_and_isolated_from_xianyu(self):
        result = self.config.save(self.body)
        self.assertTrue(result['saved'])
        self.assertTrue(result['applied'])
        self.assertTrue(result['active_enabled'])
        self.assertFalse(result['reconnect_required'])
        self.assertEqual(result['application_status'], 'applied')
        self.assert_private(result)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(str(self.requests[0].url), 'http://127.0.0.1:59190/proxy/apply')
        self.assertEqual(json.loads(self.requests[0].content), {})
        self.assertNotIn(self.body['password'], self.requests[0].content.decode())
        self.assert_private(self.config.read())

    def test_credentials_preserved_only_for_same_server_and_explicit_clear(self):
        self.config.save(self.body)
        result = self.config.save({**self.body, 'username': '', 'password': ''})
        self.assertTrue(result['auth_configured'])
        self.assertEqual(json.loads(self.vault.get('xiaohongshu-proxy'))['password'], self.body['password'])
        result = self.config.save({**self.body, 'host': 'other.example', 'username': '', 'password': ''})
        self.assertFalse(result['auth_configured'])
        self.config.save(self.body)
        result = self.config.save({**self.body, 'clear_auth': True})
        self.assertFalse(result['auth_configured'])
        self.assert_private(result)

    def test_stopped_pending_and_running_does_not_claim_platform_reachability(self):
        self.mode = 'stopped'
        result = self.config.save(self.body)
        self.assertTrue(result['saved'])
        self.assertFalse(result['applied'])
        self.assertEqual(result['application_status'], 'pending_start')
        self.assertTrue(result['reconnect_required'])
        self.assertFalse(result['active_enabled'])
        self.mode = 'restricted'
        result = self.config.read()
        self.assertTrue(result['applied'])
        self.assertEqual(result['worker_state'], 'needs_attention')
        self.assertNotIn('connected', result)

    def test_application_failure_never_discards_saved_config_or_retries(self):
        for mode in ('failed', 'timeout'):
            self.mode = mode
            self.requests.clear()
            result = self.config.save(self.body)
            self.assertTrue(result['saved'])
            self.assertEqual(result['application_status'], 'failed')
            self.assertFalse(result['applied'])
            self.assertIn('application_error', result)
            self.assertEqual(len(self.requests), 1)
            self.assertEqual(json.loads(self.vault.get('xiaohongshu-proxy'))['host'], self.body['host'])
            self.assert_private(result)

    def test_invalid_config_never_saves_or_applies(self):
        self.config.save(self.body)
        previous = self.vault.get('xiaohongshu-proxy')
        self.requests.clear()
        for changes in ({'scheme': 'socks5'}, {'scheme': 'socks5', 'enabled': False},
                        {'host': 'http://user:password@host'}, {'port': 0},
                        {'enabled': 'true'}, {'password': 'bad\npassword'}, {'url': 'http://external.example'}):
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ProxyConfigError):
                    self.config.save({**self.body, **changes})
                self.assertEqual(self.vault.get('xiaohongshu-proxy'), previous)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.vault.get('xianyu-proxy'), self.old_xianyu)

    def test_direct_and_worker_revision_validation(self):
        result = self.config.save({'enabled': False, 'scheme': 'http', 'host': '', 'port': 0})
        self.assertTrue(result['applied'])
        self.assertFalse(result['active_enabled'])
        self.assertEqual(self.client.request('status')['proxy_revision'], result['revision'])
        self.client.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
            'state': 'ready', 'browser_running': True, 'proxy_revision': self.body['password'], 'proxy_enabled': True}))
        result = self.config.read()
        self.assertFalse(result['applied'])
        self.assertFalse(result['worker_available'])
        self.assert_private(result)


class XiaohongshuProxyHTTPTests(unittest.TestCase):
    def test_auth_same_origin_no_cache_and_save_apply_failure_are_separate(self):
        vault = MemorySecrets()
        vault.set('xiaohongshu-bridge', 'isolated-bridge-key')
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(503, json={'error': {'code': 'proxy_config_invalid', 'message': 'proxy-password-secret'}})

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'MEDIADOCK_UNIFIED_ADMIN': '0', 'MEDIADOCK_REQUIRE_HTTPS': '1',
                'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example', 'MEDIADOCK_PUBLIC_BASE_URL': 'https://cards.example'}):
            app = create_app(directory, vault)
            app.state.xiaohongshu.transport = httpx.MockTransport(handler)
            body = {'enabled': True, 'scheme': 'http', 'host': 'proxy.example', 'port': 8080,
                    'username': 'proxy-user-secret', 'password': 'proxy-password-secret'}
            with TestClient(app, base_url='https://cards.example') as browser:
                self.assertEqual(browser.get('/api/admin/xiaohongshu/proxy').status_code, 401)
                self.assertEqual(browser.post('/api/admin/xiaohongshu/proxy', json=body).status_code, 401)
                self.assertIsNone(vault.get('xiaohongshu-proxy'))
                browser.post('/api/auth/setup', json={'password': 'isolated-admin-password'})
                self.assertEqual(browser.post('/api/admin/xiaohongshu/proxy', json=body,
                                             headers={'Origin': 'https://other.example'}).status_code, 403)
                self.assertEqual(requests, [])
                response = browser.post('/api/admin/xiaohongshu/proxy', json=body)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()['saved'])
                self.assertEqual(response.json()['application_status'], 'failed')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertNotIn(body['password'], response.text)
                self.assertNotIn(body['username'], response.text)
                self.assertEqual(len(requests), 1)
                self.assertEqual(browser.post('/api/admin/xiaohongshu/proxy/apply', json={}).status_code, 404)
                self.assertEqual(browser.get('/api/admin/overview').json()['orders'], [])


if __name__ == '__main__':
    unittest.main()
