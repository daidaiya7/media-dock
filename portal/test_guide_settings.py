"""Editable tutorial: bounded public text, authenticated writes and persistence."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import create_app
from secrets_store import MemorySecrets


class GuideSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.app = create_app(Path(self.temp.name), self.vault)
        self.admin = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.guest = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.assertEqual(self.admin.post('/api/auth/setup', json={'password': 'isolated-guide-password'}).status_code, 200)

    def tearDown(self):
        self.admin.close()
        self.guest.close()
        self.temp.cleanup()

    def public(self):
        return self.guest.get('/api/public/catalog').json()['guide']

    def test_default_and_unauthenticated_write(self):
        self.assertEqual(self.public(), {'texts': {}})
        self.assertEqual(self.guest.post('/api/admin/settings', json={'guide': {'texts': {'heading-t0': 'changed'}}}).status_code, 401)
        self.assertEqual(self.public(), {'texts': {}})

    def test_save_recreate_and_reset(self):
        guide = {'texts': {'heading-t0': '我的使用说明', 'library-t0': '<script>alert(1)</script>' + chr(10) + '普通文字', 'plans-t0': ''}}
        response = self.admin.post('/api/admin/settings', json={'guide': guide})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.public(), guide)
        self.assertEqual(self.admin.get('/api/admin/overview').json()['settings']['guide'], guide)
        with TestClient(create_app(Path(self.temp.name), self.vault), base_url='http://127.0.0.1:59189') as client:
            self.assertEqual(client.get('/api/public/catalog').json()['guide'], guide)
        self.assertEqual(self.admin.post('/api/admin/settings', json={'guide': {'texts': {}}}).status_code, 200)
        self.assertEqual(self.public(), {'texts': {}})

    def test_invalid_input_rejected_before_settings_or_token_write(self):
        bad = [None, [], '', {}, {'texts': []}, {'texts': None}, {'texts': {}, 'token': 'PRIVATE'},
               {'texts': {'unknown-t0': 'bad'}}, {'texts': {'client-t1000': 'bad'}},
               {'texts': {'client-t0': 1}}, {'texts': {'client-t0': 'bad' + chr(0) + 'text'}},
               {'texts': {'client-t0': 'x' * 5001}},
               {'texts': {f'client-t{i}': 'x' * 5000 for i in range(5)}},
               {'texts': {f'client-t{i}': 'x' for i in range(401)}}]
        for guide in bad:
            with self.subTest(guide=str(guide)[:50]):
                response = self.admin.post('/api/admin/settings', json={'guide': guide, 'redemption_url': 'https://changed.example', 'nextemby_token': 'ne_agent_SHOULD_NOT_SAVE'})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.public(), {'texts': {}})
                self.assertEqual(self.guest.get('/api/public/catalog').json()['redemption_url'], '')
                self.assertFalse(self.vault.get('nextemby'))

    def test_unicode_size_and_line_endings(self):
        guide = {'texts': {f'client-t{i}': '影' * 4800 for i in range(5)}}
        response = self.admin.post('/api/admin/settings', json={'guide': guide})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.public(), guide)
        self.assertEqual(self.admin.post('/api/admin/settings', json={'guide': {'texts': {'heading-t0': '一' + chr(13) + chr(10) + '二' + chr(13) + '三'}}}).status_code, 200)
        self.assertEqual(self.public(), {'texts': {'heading-t0': chr(10).join(['一', '二', '三'])}})

    def test_public_projection_excludes_extra_fields(self):
        self.vault.set('nextemby', 'ne_agent_PRIVATE')
        self.app.state.control.set('settings', {'private_field': 'PRIVATE-SETTINGS', 'guide': {'internal_url': 'https://private.example', 'texts': {'heading-t0': '公开文字', 'token': 'PRIVATE-TOKEN'}}})
        response = self.guest.get('/api/public/catalog')
        self.assertEqual(response.json()['guide'], {'texts': {'heading-t0': '公开文字'}})
        for secret in ['PRIVATE-SETTINGS', 'ne_agent_PRIVATE', 'private.example', 'PRIVATE-TOKEN']:
            self.assertNotIn(secret, response.text)

    def test_cross_origin_save_forbidden(self):
        response = self.admin.post('/api/admin/settings', json={'guide': {'texts': {}}}, headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)


if __name__ == '__main__':
    unittest.main()
