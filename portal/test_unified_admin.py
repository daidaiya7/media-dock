"""Isolated checks of the shared admin gateway; no production connections."""
import os
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import COOKIE, create_app
from secrets_store import MemorySecrets


class UnifiedAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.vault.set('admin-integration', 'gateway-test-key-with-no-production-access')
        self.env = patch.dict(os.environ, {
            'MEDIADOCK_UNIFIED_ADMIN': '1', 'MEDIADOCK_REQUIRE_HTTPS': '1',
            'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example',
            'MEDIADOCK_PUBLIC_BASE_URL': 'https://cards.example',
        })
        self.env.start()
        self.app = create_app(self.temp.name, self.vault)
        self.client = TestClient(self.app, base_url='https://cards.example', client=('127.0.0.1', 1234))
        self.client.post('/api/auth/setup', json={'password': 'test-only-admin-password'})
        self.headers = {'X-Original-Method': 'GET'}
        self.route = '/internal/xianyu-auth'

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.temp.cleanup()

    def test_custom_entry_and_username_preserve_password_and_protect_login(self):
        saved = self.app.state.control.get('password')
        with TestClient(self.app, base_url='https://cards.example') as guest:
            self.assertEqual(guest.get('/admin').status_code, 200)
            self.assertEqual(guest.get('/admin/', follow_redirects=False).headers['location'], '/admin')
            for path in ('/admin/test', '/admin/unknown'):
                response = guest.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('location', response.headers)
            self.assertEqual(guest.get('/api/admin/overview').status_code, 401)
            for username in (None, 'IncorrectAdmin', 123, '管理员'):
                response = guest.post('/api/auth/login', json={'username': username, 'password': 'test-only-admin-password'})
                self.assertEqual(response.status_code, 401)
            self.assertEqual(guest.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong-password'}).status_code, 401)
            self.assertEqual(guest.post('/api/auth/login', json={'username': 'admin', 'password': 'test-only-admin-password'}).status_code, 200)
            self.assertEqual(guest.get('/api/admin/overview').status_code, 200)
        self.assertEqual(self.app.state.control.get('password'), saved)

    def test_gateway_requires_existing_valid_session_and_private_peer(self):
        with TestClient(self.app, base_url='https://cards.example', client=('127.0.0.1', 1)) as guest:
            self.assertEqual(guest.get(self.route, headers=self.headers).status_code, 401)
        with TestClient(self.app, base_url='https://cards.example', client=('203.0.113.4', 1)) as external:
            external.cookies.set(COOKIE, self.client.cookies.get(COOKIE))
            self.assertEqual(external.get(self.route, headers=self.headers).status_code, 404)
        response = self.client.get(self.route, headers=self.headers)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers['X-MediaDock-Admin-Key'], self.vault.get('admin-integration'))
        self.assertEqual(response.headers['X-MediaDock-Session'], self.client.cookies.get(COOKIE))
        self.assertEqual(response.content, b'')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_session_revocation_and_expiry_stop_gateway(self):
        cookie = self.client.cookies.get(COOKIE)
        with self.app.state.control.db() as c:
            c.execute('UPDATE sessions SET expires=?', (time.time() - 1,))
        self.assertEqual(self.client.get(self.route, headers=self.headers).status_code, 401)
        self.client.post('/api/auth/login', json={'username': 'admin', 'password': 'test-only-admin-password'})
        active = self.client.cookies.get(COOKIE)
        self.client.post('/api/auth/logout', json={})
        self.client.cookies.set(COOKIE, active)
        self.assertEqual(self.client.get(self.route, headers=self.headers).status_code, 401)
        self.assertNotEqual(active, cookie)

    def test_origin_and_internal_headers_fail_closed(self):
        for headers in ({}, {'X-Original-Method': 'TRACE'},
                        {**self.headers, 'Origin': 'https://evil.example'},
                        {**self.headers, 'Origin': 'http://cards.example'},
                        {**self.headers, 'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.client.get(self.route, headers=headers).status_code, 403)
        response = self.client.get(self.route, headers={**self.headers, 'X-MediaDock-Admin-Key': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('X-MediaDock-Admin-Key', response.headers)
        valid = {**self.headers, 'X-MediaDock-Admin-Key': self.vault.get('admin-integration')}
        self.assertEqual(self.client.get(self.route, headers=valid).status_code, 204)
        for method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            self.assertEqual(self.client.get(self.route, headers={'X-Original-Method': method, 'Origin': 'https://cards.example'}).status_code, 204)

    def test_public_routes_do_not_disclose_gateway_keys(self):
        for route in ('/api/public/catalog', '/api/auth/status', '/api/admin/overview', '/admin'):
            response = self.client.get(route)
            self.assertNotIn('X-MediaDock-Admin-Key', response.headers)
            self.assertNotIn('X-MediaDock-Session', response.headers)
            self.assertNotIn(self.vault.get('admin-integration'), response.text)
        conn = self.client.get('/api/admin/overview').json()['connections']['xianyu']
        self.assertEqual(conn['url'], '/xianyu/')
        self.assertTrue(conn['unified_admin'])
        policy = self.client.get('/admin').headers['Content-Security-Policy']
        self.assertIn("frame-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_gateway_must_be_opted_in_and_preconfigured(self):
        with patch.dict(os.environ, {'MEDIADOCK_UNIFIED_ADMIN': '0'}):
            app = create_app(self.temp.name, self.vault)
            with TestClient(app, base_url='https://cards.example', client=('127.0.0.1', 1)) as off:
                off.cookies.set(COOKIE, self.client.cookies.get(COOKIE))
                self.assertEqual(off.get(self.route, headers=self.headers).status_code, 404)
                self.assertFalse(off.get('/api/admin/overview').json()['connections']['xianyu']['unified_admin'])
        with self.assertRaises(RuntimeError):
            create_app(self.temp.name, MemorySecrets())


if __name__ == '__main__':
    unittest.main()
