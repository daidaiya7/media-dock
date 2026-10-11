"""Editable public introduction: authentication, persistence and projection."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from app import INTRODUCTION_DEFAULT, create_app
from secrets_store import MemorySecrets


class IntroductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.app = create_app(Path(self.temp.name), self.vault)
        self.admin = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.guest = TestClient(self.app, base_url='http://127.0.0.1:59189')
        self.assertEqual(self.admin.post('/api/auth/setup', json={'password': 'isolated-intro-password'}).status_code, 200)

    def tearDown(self):
        self.admin.close()
        self.guest.close()
        self.temp.cleanup()

    def public(self):
        return self.guest.get('/api/public/catalog').json()['introduction']

    def test_default_content_and_unauthenticated_save(self):
        self.assertEqual(self.public(), INTRODUCTION_DEFAULT)
        response = self.guest.post('/api/admin/settings', json={'introduction': {'enabled': False}})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.public(), INTRODUCTION_DEFAULT)

    def test_admin_can_save_plain_text_and_it_survives_app_recreation(self):
        intro = {'entry_label': '购买必看', 'enabled': False, 'title': '新的公告', 'subtitle': '',
                 'sections': [{'title': '第一节', 'body': '<script>alert(1)</script>\n普通正文'}]}
        response = self.admin.post('/api/admin/settings', json={'introduction': intro})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.public(), intro)
        recreated = create_app(Path(self.temp.name), self.vault)
        with TestClient(recreated, base_url='http://127.0.0.1:59189') as client:
            self.assertEqual(client.get('/api/public/catalog').json()['introduction'], intro)
        overview = self.admin.get('/api/admin/overview').json()
        self.assertEqual(overview['settings']['introduction'], intro)
        response = self.admin.post('/api/admin/settings', json={'introduction': {'enabled': True}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.public(), {**intro, 'enabled': True})

    def test_invalid_values_are_rejected_without_partial_settings_write(self):
        for field, value in [('entry_label', ''), ('entry_label', '字'*25), ('entry_label', []), ('enabled', 'false'), ('enabled', 1), ('title', ''),
                             ('title', 'x' * 121), ('subtitle', 'x' * 241),
                             ('sections', []), ('sections', [{'title':'x','body':'x'*4001}]), ('sections', [{'title':'x','body':'bad\x00text'}]), ('token', 'not-public')]:
            with self.subTest(field=field, value=str(value)[:30]):
                response = self.admin.post('/api/admin/settings', json={
                    'introduction': {**INTRODUCTION_DEFAULT, field: value},
                    'redemption_url': 'https://changed.example'})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(self.public(), INTRODUCTION_DEFAULT)
                self.assertEqual(self.guest.get('/api/public/catalog').json()['redemption_url'], '')
        for value in [None, 'text', []]:
            self.assertEqual(self.admin.post('/api/admin/settings', json={'introduction': value}).status_code, 400)

    def test_long_chinese_body_within_declared_limits(self):
        intro = {**INTRODUCTION_DEFAULT, 'sections': [{'title': '正文', 'body': '影'*4000} for _ in range(4)]}
        response = self.admin.post('/api/admin/settings', json={'introduction': intro})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.public(), intro)

    def test_public_projection_never_includes_private_settings(self):
        self.vault.set('nextemby', 'ne_agent_PRIVATE')
        self.app.state.control.set('settings', {
            'xianyu_account_id': 'PRIVATE-SELLER', 'private_field': 'PRIVATE-FIELD',
            'introduction': {**INTRODUCTION_DEFAULT, 'internal_url': 'http://private.example', 'token': 'PRIVATE-TOKEN'}})
        response = self.guest.get('/api/public/catalog')
        self.assertEqual(set(response.json()['introduction']), {'entry_label', 'enabled', 'title', 'subtitle', 'sections'})
        for secret in ['ne_agent_PRIVATE', 'PRIVATE-SELLER', 'PRIVATE-FIELD', 'private.example', 'PRIVATE-TOKEN']:
            self.assertNotIn(secret, response.text)


if __name__ == '__main__':
    unittest.main()
