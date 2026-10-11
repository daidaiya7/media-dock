"""Proxy configuration isolation, secret redaction and activation tests."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from app import create_app
from secrets_store import MemorySecrets
from xianyu_proxy import XianyuProxyConfig, ProxyConfigError


class ProxyConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.status = Path(self.temp.name) / 'active.json'
        self.config = XianyuProxyConfig(self.vault, self.status)
        self.body = dict(enabled=True, scheme='https', host='proxy.example', port=8443,
                         username='fixture-user', password='fixture-password')

    def tearDown(self):
        self.temp.cleanup()

    def test_redacted_roundtrip_and_activation(self):
        public = self.config.save(self.body)
        self.assertTrue(public['auth_configured'])
        self.assertNotIn('fixture', json.dumps(public))
        self.assertFalse(public['applied'])
        self.status.write_text(json.dumps(dict(revision=public['revision'], enabled=True)))
        self.assertTrue(self.config.public()['applied'])
        saved = self.config.save({**self.body, 'username': '', 'password': ''})
        private = json.loads(self.vault.get('xianyu-proxy'))
        self.assertEqual(private['password'], self.body['password'])
        self.assertNotEqual(saved['revision'], public['revision'])
        self.assertFalse(saved['applied'])

    def test_changed_endpoint_and_explicit_clear_remove_credentials(self):
        self.config.save(self.body)
        self.config.save({**self.body, 'host': 'new.example', 'username': '', 'password': ''})
        self.assertFalse(self.config.public()['auth_configured'])
        self.config.save(self.body)
        self.config.save({**self.body, 'clear_auth': True})
        self.assertFalse(self.config.public()['auth_configured'])

    def test_invalid_config_does_not_replace_previous_and_error_is_safe(self):
        self.config.save(self.body)
        previous = self.vault.get('xianyu-proxy')
        for change in ({'enabled':'true'}, {'scheme':'file'}, {'host':'http://user:pass@proxy.example'},
                       {'host':'a'*64+'.example'}, {'host':'bad.-label'}, {'host':'fe80::1%eth0'},
                       {'port':0}, {'port':True}, {'port':65536}, {'host':'bad\nhost'},
                       {'password':'invalid\npassword'}, {'scheme':'socks5'}, {'clear_auth':'false'}):
            with self.subTest(change=list(change)):
                with self.assertRaises(ProxyConfigError) as error:
                    self.config.save({**self.body, **change})
                self.assertNotIn('fixture', str(error.exception))
                self.assertEqual(self.vault.get('xianyu-proxy'), previous)

    def test_direct_and_socks5_ipv6(self):
        direct = self.config.save(dict(enabled=False,scheme='http',host='',port=0))
        self.assertFalse(direct['enabled'])
        socks = self.config.save(dict(enabled=True,scheme='socks5',host='[::1]',port=1080))
        self.assertEqual(socks['host'], '::1')
        self.assertFalse(socks['auth_configured'])
        self.status.write_text('broken json')
        self.assertFalse(self.config.public()['applied'])

    def test_admin_auth_origin_and_no_external_side_effects(self):
        self.vault.set('admin-integration', 'isolated-fixture-key')
        with patch.dict(os.environ, {'MEDIADOCK_UNIFIED_ADMIN':'1','MEDIADOCK_PUBLIC_BASE_URL':'https://cards.example',
                                    'MEDIADOCK_XIANYU_PROXY_STATUS_FILE': str(self.status)}):
            app = create_app(self.temp.name, self.vault)
            with TestClient(app, base_url='https://cards.example') as client:
                self.assertEqual(client.get('/api/admin/xianyu-proxy').status_code, 401)
                self.assertEqual(client.post('/api/admin/xianyu-proxy',json=self.body).status_code, 401)
                client.post('/api/auth/setup',json={'password':'fixture-admin-password'})
                self.assertEqual(client.post('/api/admin/xianyu-proxy',json=self.body,
                                 headers={'Origin':'https://evil.example'}).status_code, 403)
                response = client.post('/api/admin/xianyu-proxy',json=self.body)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('fixture', response.text)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(client.get('/api/admin/overview').json()['orders'], [])


if __name__ == '__main__':
    unittest.main()
