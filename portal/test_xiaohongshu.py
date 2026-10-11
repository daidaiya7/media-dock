"""小红书本机桥接的身份边界、发送不确定性和凭据隔离测试。"""
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import create_app
from secrets_store import MemorySecrets
from xiaohongshu import XiaohongshuClient, XiaohongshuError, worker_url


KEY = 'isolated-xhs-bridge-secret'
REQUEST_ID = '77777777-7777-4777-8777-777777777777'
FRAME_ID = '88888888-8888-4888-8888-888888888888'
SEND = {'request_id': REQUEST_ID, 'account_id': 'fixture-account',
        'conversation_id': 'fixture-conversation', 'text': '测试消息\n第二行'}
STATUS = {'state': 'ready', 'account_id': SEND['account_id'], 'account_name': '隔离账号',
          'conversation_id': SEND['conversation_id'], 'browser_running': True}
MESSAGES = {'account_id': SEND['account_id'], 'conversation_id': SEND['conversation_id'],
            'header': '隔离会话', 'composer_ready': True,
            'messages': [{'id': 'fixture-message', 'text': '收到', 'direction': 'incoming',
                          'time': '10:00', 'status': 'observed'}]}


class XiaohongshuAdapterTests(unittest.TestCase):
    def setUp(self):
        self.vault = MemorySecrets()
        self.vault.set('xiaohongshu-bridge', KEY)
        self.requests = []
        self.response = STATUS

        def handler(request):
            self.requests.append(request)
            if isinstance(self.response, Exception):
                raise self.response
            if isinstance(self.response, httpx.Response):
                return self.response
            return httpx.Response(200, json=self.response)

        self.client = XiaohongshuClient(self.vault, transport=httpx.MockTransport(handler))

    def test_configuration_cannot_resolve_or_forward_to_remote_hosts(self):
        for value in ('https://remote.example:59190', 'http://localhost:59190',
                      'http://127.0.0.1.evil.example:59190', 'http://127.0.0.1:59190/path',
                      'http://u:p@127.0.0.1:59190', 'http://127.0.0.1:59190?url=secret',
                      'http://127.0.0.1:59190#secret', 'http://127.0.0.1:0',
                      'http://[::ffff:127.0.0.1]:59190', 'http://127.0.0.1', 'file:///tmp/socket'):
            with self.subTest(value=value):
                with self.assertRaises(RuntimeError) as caught:
                    worker_url(value)
                self.assertNotIn(value, str(caught.exception))
        self.assertEqual(worker_url('http://[::1]:59190/'), 'http://[::1]:59190')

    def test_private_key_only_in_worker_authorization_and_outputs_whitelisted(self):
        self.response = {**STATUS, 'cookie': 'worker-cookie-secret', 'key': KEY, 'detail_code': KEY}
        with patch('xiaohongshu.httpx.Client', wraps=httpx.Client) as factory:
            result = self.client.request('status')
        self.assertTrue(result['available'])
        self.assertEqual(result['state'], 'ready')
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn('worker-cookie', json.dumps(result))
        self.assertEqual(self.requests[0].headers['authorization'], 'Bearer ' + KEY)
        self.assertNotIn('cookie', self.requests[0].headers)
        self.assertNotIn('origin', self.requests[0].headers)
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertFalse(factory.call_args.kwargs['follow_redirects'])
        self.assertEqual(str(self.requests[0].url), 'http://127.0.0.1:59190/status')

    def test_truthful_unconfigured_offline_and_invalid_response_states(self):
        self.vault.values.pop('xiaohongshu-bridge')
        self.assertEqual(self.client.request('status')['state'], 'unconfigured')
        self.assertEqual(self.requests, [])
        with self.assertRaises(XiaohongshuError) as caught:
            self.client.request('start', {})
        self.assertEqual(caught.exception.code, 'not_configured')
        self.vault.set('xiaohongshu-bridge', KEY)
        for bad in (httpx.ConnectError(KEY), httpx.Response(401, text=KEY),
                    {'state': 'ready', 'browser_running': 'true'}, {'state': 'unknown'}):
            self.response = bad
            result = self.client.request('status')
            self.assertEqual(result['state'], 'offline')
            self.assertTrue(result['configured'])
            self.assertFalse(result['available'])
            self.assertNotIn(KEY, json.dumps(result))

    def test_no_redirect_follow_and_no_raw_worker_error_disclosure(self):
        self.response = httpx.Response(307, headers={'Location': 'https://external.example/' + KEY})
        with self.assertRaises(XiaohongshuError) as caught:
            self.client.request('messages')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(caught.exception.code, 'worker_unavailable')
        for error in ({'code': 'busy', 'message': KEY}, {'code': KEY, 'message': KEY},
                      {'code': 'login_not_visible', 'message': KEY},
                      {'code': 'login_state_uncertain', 'message': KEY}):
            self.response = httpx.Response(409, json={'error': error})
            with self.assertRaises(XiaohongshuError) as caught:
                self.client.request('messages')
            self.assertNotIn(KEY, str(caught.exception))
            self.assertNotIn(KEY, caught.exception.message)
            if error['code'].startswith('login_'):
                self.assertEqual(caught.exception.status, 409)
                self.assertEqual(caught.exception.code, error['code'])

    def test_sending_timeout_and_invalid_ack_never_retried_or_claimed_sent(self):
        cases = [httpx.ReadTimeout(KEY), httpx.WriteError(KEY), httpx.ConnectError(KEY),
                 httpx.Response(500, text=KEY), httpx.Response(302, headers={'Location': '/send'}),
                 httpx.Response(200, text=KEY), {**SEND, 'status': 'sent', 'account_id': 'wrong'}]
        for response in cases:
            with self.subTest(response=type(response).__name__):
                self.requests.clear()
                self.response = response
                result = self.client.request('send', SEND)
                self.assertEqual(result['status'], 'needs_review')
                self.assertEqual(result['request_id'], REQUEST_ID)
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn('text', result)
                self.assertNotIn(KEY, json.dumps(result))
        self.response = {**SEND, 'status': 'sent', 'code': KEY, 'cookies': KEY}
        self.assertEqual(self.client.request('send', SEND), {
            'request_id': REQUEST_ID, 'status': 'sent', 'account_id': SEND['account_id'],
            'conversation_id': SEND['conversation_id'], 'code': 'send_confirmed',
            'confirmation': 'send_confirmed', 'created_at': ''})

    def test_startup_diagnostics_are_fixed_codes_without_raw_errors(self):
        for code in ('sandbox_unavailable', 'missing_library', 'navigation_timeout', 'launch_timeout',
                     'hydration_timeout', 'blocked_network', 'runtime_unavailable', 'profile_not_private',
                     'browser_start_failed'):
            with self.subTest(code=code):
                self.response = httpx.Response(503, json={'error': {'code': code, 'message': KEY, 'stack': KEY}})
                with self.assertRaises(XiaohongshuError) as caught:
                    self.client.request('start', {})
                self.assertEqual((caught.exception.code, caught.exception.status), (code, 503))
                self.assertNotIn(KEY, caught.exception.message)
                self.response = {'state': 'error', 'account_id': '', 'account_name': '', 'conversation_id': '',
                                 'browser_running': False, 'detail_code': code, 'raw_error': KEY, 'stack': KEY}
                result = self.client.request('status')
                self.assertEqual(result['state'], 'error')
                self.assertEqual(result['detail_code'], code)
                self.assertTrue(result['available'])
                self.assertFalse(result['browser_running'])
                self.assertNotIn(KEY, json.dumps(result))

    def test_persistent_receipts_are_scoped_and_manual_resolution_is_distinct(self):
        receipt = {'request_id': REQUEST_ID, 'account_id': SEND['account_id'],
                   'conversation_id': SEND['conversation_id'], 'status': 'needs_review',
                   'code': 'interrupted', 'confirmation': 'interrupted',
                   'created_at': '2026-10-11T10:00:00.000Z'}
        self.response = {**MESSAGES, 'pending_receipt': {**receipt, 'text': SEND['text'], 'secret': KEY},
                         'latest_receipt': {**receipt, 'text': SEND['text']}}
        result = self.client.request('messages')
        self.assertEqual(result['pending_receipt']['text'], SEND['text'])
        self.assertNotIn('text', result['latest_receipt'])
        self.assertNotIn(KEY, json.dumps(result))
        self.response['pending_receipt']['account_id'] = 'different-account'
        with self.assertRaises(XiaohongshuError):
            self.client.request('messages')
        resolution = {k: SEND[k] for k in ('request_id', 'account_id', 'conversation_id')}
        resolution.update(resolution='sent', confirmed=True)
        self.response = {**receipt, 'status': 'sent', 'code': 'manual_sent', 'confirmation': 'manual_sent'}
        result = self.client.request('resolve', resolution)
        self.assertEqual(result['confirmation'], 'manual_sent')
        self.response = httpx.ReadTimeout(KEY)
        with self.assertRaises(XiaohongshuError) as caught:
            self.client.request('resolve', resolution)
        self.assertEqual(caught.exception.status, 503)
        self.response = httpx.Response(409, json={'error': {'code': 'unresolved_send', 'message': KEY}})
        with self.assertRaises(XiaohongshuError) as caught:
            self.client.request('send', SEND)
        self.assertEqual((caught.exception.status, caught.exception.code), (409, 'unresolved_send'))
        self.assertNotIn(KEY, caught.exception.message)

    def test_chat_shapes_and_login_png_are_bounded(self):
        self.response = {**MESSAGES, 'cookie': KEY}
        self.assertEqual(self.client.request('messages'), MESSAGES)
        self.response = {'account_id': SEND['account_id'], 'conversations': [
            {'id': 'fixture-conversation', 'kind': 'direct', 'name': '收件人', 'time': '',
             'summary': '第一行\n第二行', 'active': True, 'cookie': KEY}]}
        self.assertNotIn(KEY, json.dumps(self.client.request('conversations')))
        self.response = {'mime': 'image/png', 'data': base64.b64encode(b'\x89PNG\r\n\x1a\nfixture').decode(),
                         'frame_token': FRAME_ID, 'width': 1280, 'height': 900, 'state': 'login_required'}
        self.assertEqual(self.client.request('screenshot'), self.response)
        for changes in ({'mime': 'image/svg+xml'}, {'data': '<svg onload="x"/>'}, {'width': 9000},
                        {'frame_token': KEY}, {'data': 'A' * (4 * 1024 * 1024 + 1)}):
            previous = self.response
            self.response = {**self.response, **changes}
            with self.assertRaises(XiaohongshuError):
                self.client.request('screenshot')
            self.response = previous


class XiaohongshuHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemorySecrets()
        self.vault.set('xiaohongshu-bridge', KEY)
        self.env = patch.dict(os.environ, {'MEDIADOCK_UNIFIED_ADMIN': '0', 'MEDIADOCK_REQUIRE_HTTPS': '1',
                                          'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example',
                                          'MEDIADOCK_PUBLIC_BASE_URL': 'https://cards.example',
                                          'MEDIADOCK_XHS_WORKER_URL': 'http://127.0.0.1:59190'})
        self.env.start()
        self.app = create_app(self.temp.name, self.vault)
        self.requests = []

        def handler(request):
            self.requests.append(request)
            if request.url.path == '/messages/send':
                return httpx.Response(200, json={**json.loads(request.content), 'status': 'needs_review'})
            return httpx.Response(200, json=STATUS)

        self.app.state.xiaohongshu.transport = httpx.MockTransport(handler)
        self.browser = TestClient(self.app, base_url='https://cards.example')

    def tearDown(self):
        self.browser.close()
        self.env.stop()
        self.temp.cleanup()

    def login(self):
        self.assertEqual(self.browser.post('/api/auth/setup', json={'password': 'isolated-admin-password'}).status_code, 200)

    def test_every_fixed_route_requires_admin_and_cross_site_mutations_fail(self):
        for path in ('status', 'screenshot', 'conversations', 'messages'):
            self.assertEqual(self.browser.get('/api/admin/xiaohongshu/' + path).status_code, 401)
        for path, body in (('session/start', {}), ('session/stop', {}),
                           ('login/click', {'x': 10, 'y': 10, 'frame_token': FRAME_ID}),
                           ('conversations/select', {k: SEND[k] for k in ('account_id', 'conversation_id')}),
                           ('messages/resolve', {'request_id': REQUEST_ID, 'account_id': SEND['account_id'],
                                                 'conversation_id': SEND['conversation_id'],
                                                 'resolution': 'sent', 'confirmed': True}),
                           ('messages/send', SEND)):
            self.assertEqual(self.browser.post('/api/admin/xiaohongshu/' + path, json=body).status_code, 401)
        self.assertEqual(self.requests, [])
        self.login()
        for headers in ({'Origin': 'https://evil.example'}, {'Origin': 'http://cards.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            response = self.browser.post('/api/admin/xiaohongshu/messages/send', json=SEND, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.requests, [])
        status = self.browser.get('/api/admin/xiaohongshu/status')
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.headers['cache-control'], 'no-store')
        self.assertNotIn(KEY, status.text)
        self.assertNotIn('cookie', self.requests[-1].headers)
        self.assertNotIn('origin', self.requests[-1].headers)

    def test_fixed_paths_and_input_validation_prevent_arbitrary_browser_access(self):
        self.login()
        for path in ('navigate', 'eval', 'https://external.example', 'status/extra'):
            self.assertEqual(self.browser.post('/api/admin/xiaohongshu/' + path, json={}).status_code, 404)
        for changes in ({'url': 'https://external.example'}, {'text': 'x' * 1001}, {'text': ' \n'},
                        {'text': 'bad\x00text'}, {'request_id': '../path'}, {'account_id': ''},
                        {'conversation_id': 1}, {'text': '\ud800'}):
            response = self.browser.post('/api/admin/xiaohongshu/messages/send', content=json.dumps({**SEND, **changes}),
                                         headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 400, response.text)
        for body in ({'x': True, 'y': 0, 'frame_token': FRAME_ID},
                     {'x': 1280, 'y': 0, 'frame_token': FRAME_ID},
                     {'x': 0, 'y': -1, 'frame_token': FRAME_ID}):
            self.assertEqual(self.browser.post('/api/admin/xiaohongshu/login/click', json=body).status_code, 400)
        self.assertEqual(self.requests, [])
        response = self.browser.post('/api/admin/xiaohongshu/session/start', json={'url': 'https://external.example'})
        self.assertEqual(response.status_code, 400)
        resolution = {k: SEND[k] for k in ('request_id', 'account_id', 'conversation_id')}
        for changes in ({'resolution': 'sent', 'confirmed': False}, {'resolution': {}, 'confirmed': True},
                        {'resolution': 'retry', 'confirmed': True}):
            response = self.browser.post('/api/admin/xiaohongshu/messages/resolve', json={**resolution, **changes})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.requests, [])

    def test_send_is_manual_only_and_does_not_create_orders_or_fulfil_cards(self):
        self.login()
        response = self.browser.post('/api/admin/xiaohongshu/messages/send', json=SEND,
                                     headers={'Origin': 'https://cards.example'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'needs_review')
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads(self.requests[0].content), SEND)
        self.assertEqual(self.browser.get('/api/admin/overview').json()['orders'], [])
        self.assertNotIn('nextemby', self.vault.values)
        self.assertEqual(self.browser.post('/api/admin/xiaohongshu/messages/send', content='x' * 32769,
                                          headers={'Content-Type': 'application/json'}).status_code, 413)
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
