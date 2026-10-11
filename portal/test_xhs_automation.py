"""Isolated automation tests: fake worker, fake AI, no remote requests or real sends."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from app import create_app
from secrets_store import MemorySecrets
from xhs_automation import AutomationError, DEFAULT, XhsAutomation, ai_reply, endpoint
from xiaohongshu import XiaohongshuClient, XiaohongshuError, validate_body


def message(ident, text='价格', **extra):
    return {'id': ident, 'text': text, 'direction': 'incoming', 'content_type': '1', 'status': 'observed', **extra}


class Worker:
    def __init__(self):
        self.session = str(uuid4())
        self.account = 'account-a'
        self.state = 'ready'
        self.current = 'c1'
        self.history = {'c1': [message('old')]}
        self.calls, self.sends = [], []
        self.outcome = 'sent'
        self.before_messages = None
        self.resolve_error = None

    def view(self):
        return {'session_id': self.session, 'account_id': self.account, 'conversation_id': self.current,
                'composer_ready': True, 'messages': deepcopy(self.history[self.current])}

    def request(self, op, body=None):
        self.calls.append(op)
        if op == 'status':
            return {'state': self.state, 'account_id': self.account, 'session_id': self.session, 'browser_running': True}
        if op == 'conversations':
            return {'account_id': self.account, 'session_id': self.session, 'conversations': [
                {'id': ident, 'kind': 'c2c', 'summary': rows[-1]['id'] if rows else '', 'time': ''}
                for ident, rows in self.history.items()]}
        if op == 'select':
            self.current = body['conversation_id']
            return self.view()
        if op == 'messages':
            if self.before_messages:
                self.before_messages()
            return self.view()
        if op == 'send_auto':
            self.sends.append(deepcopy(body))
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return {**body, 'status': self.outcome}
        if op == 'resolve':
            if self.resolve_error:
                raise self.resolve_error
            return {**body, 'status': 'sent' if body['resolution'] == 'sent' else 'failed'}
        raise AssertionError(op)


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'automation.sqlite3'
        self.vault = MemorySecrets()
        self.worker = Worker()
        self.clock = [1000.0]
        self.ai_calls = []
        def fake_ai(config, key, text):
            self.ai_calls.append((deepcopy(config), key, text))
            return '请稍等，人工会为你核对。'
        self.engine = XhsAutomation(self.path, self.vault, self.worker, now=lambda: self.clock[0], ai_call=fake_ai)

    def tearDown(self):
        self.engine.stop()
        self.temp.cleanup()

    def enable(self, *, ai=False, fallback=False, **extra):
        cfg = {'enabled': True, 'account_id': self.worker.account,
               'min_reply_interval_seconds': 30, 'rules': [{'id': 'price', 'name': '价格说明', 'enabled': True,
               'keywords': ['价格'], 'reply_type': 'ai' if ai else 'fixed', 'reply_text': '请按套餐页面价格选择。'}], **extra}
        if ai:
            cfg['ai'] = {'enabled': True, 'base_url': 'https://ai.example/v1', 'model': 'test-model', 'api_key': 'TEST-ONLY-KEY'}
        if fallback:
            cfg['fallback'] = {'enabled': True, 'reply_type': 'fixed', 'reply_text': '稍后人工为你核对。'}
        self.engine.save(cfg)
        self.engine.tick()

    def incoming(self, ident='new', **extra):
        self.worker.history['c1'].append(message(ident, **extra))
        self.clock[0] += 15
        self.engine.tick()

    def test_default_and_read_only_endpoints_cause_no_worker_or_ai_calls(self):
        self.engine.tick()
        cfg = self.engine.read()
        self.assertFalse(cfg['enabled'])
        self.assertFalse(cfg['ai']['enabled'])
        self.assertFalse(cfg['ai']['auth_configured'])
        self.assertEqual(self.engine.preview('价格')['matched'], False)
        self.assertEqual(self.engine.dashboard()['counts']['sent'], 0)
        self.assertEqual(self.worker.calls, [])
        self.assertEqual(self.ai_calls, [])
        self.assertEqual(self.engine.paused_until, 0)

    def test_first_baseline_then_exactly_one_guarded_reply_for_new_text(self):
        self.enable()
        self.assertEqual(self.worker.sends, [])
        self.incoming()
        self.engine.tick()
        self.assertEqual(len(self.worker.sends), 1)
        sent = self.worker.sends[0]
        self.assertEqual(sent['source_session_id'], self.worker.session)
        self.assertEqual(sent['source_message_id'], 'new')
        self.assertEqual(self.engine.dashboard()['counts']['sent'], 1)
        self.assertEqual(self.ai_calls, [])

    def test_restart_and_session_change_only_rebaseline_no_catchup(self):
        self.enable()
        self.worker.history['c1'].append(message('during-restart'))
        restarted = XhsAutomation(self.path, self.vault, self.worker, now=lambda: self.clock[0])
        restarted.tick()
        self.assertEqual(self.worker.sends, [])
        self.worker.session = str(uuid4())
        self.worker.history['c1'].append(message('during-reconnect'))
        restarted.tick()
        self.assertEqual(self.worker.sends, [])

    def test_offline_account_change_and_long_poll_gap_cannot_replay(self):
        self.enable()
        self.worker.state = 'stopped'
        self.engine.tick()
        self.worker.state = 'ready'
        self.worker.history['c1'].append(message('offline-message'))
        self.engine.tick()
        self.worker.account = 'different-account'
        self.incoming('wrong-account')
        self.worker.account = 'account-a'
        self.engine.tick()
        self.clock[0] += 100
        self.incoming('gap-message')
        self.assertEqual(self.worker.sends, [])

    def test_history_prepend_reorder_missing_anchor_and_empty_recovery_do_not_send(self):
        self.enable()
        self.worker.history['c1'].insert(0, message('older-history'))
        self.engine.tick()
        self.worker.history['c1'].reverse()
        self.engine.tick()
        self.worker.history['c1'] = [message('unrelated-window')]
        self.engine.tick()
        self.worker.history['c1'] = []
        self.engine.tick()
        self.worker.history['c1'] = [message('loaded-first-message')]
        self.engine.tick()
        self.assertEqual(self.worker.sends, [])

    def test_non_text_unknown_direction_outgoing_and_ambiguous_ids_never_send(self):
        self.enable()
        for index, extra in enumerate([{'content_type': '2'}, {'content_type': ''}, {'direction': 'outgoing'},
                                        {'direction': 'unknown'}, {'status': 'failed'}, {'text': ''}]):
            self.incoming(str(index), **extra)
        self.worker.history['c1'].append(message('duplicate'))
        self.worker.history['c1'].append(message('duplicate'))
        self.engine.tick()
        self.assertEqual(self.worker.sends, [])

    def test_new_conversations_and_group_chat_do_not_receive_historical_replies(self):
        self.enable()
        self.worker.history['new-conversation'] = [message('historic')]
        self.engine.tick()
        self.assertEqual(self.worker.sends, [])
        original = self.worker.request
        def groups(operation, body=None):
            result = original(operation, body)
            if operation == 'conversations':
                for conversation in result['conversations']:
                    conversation['kind'] = 'group'
            return result
        self.worker.request = groups
        self.incoming('group')
        self.assertEqual(self.worker.sends, [])

    def test_unknown_send_result_is_persistent_and_blocks_following_replies(self):
        self.enable()
        self.worker.outcome = TimeoutError('PRIVATE-DO-NOT-LOG')
        self.incoming()
        self.worker.outcome = 'sent'
        self.incoming('following')
        self.assertEqual(len(self.worker.sends), 1)
        dashboard = self.engine.dashboard()
        self.assertEqual(dashboard['counts']['needs_review'], 1)
        self.assertNotIn('PRIVATE-DO-NOT-LOG', json.dumps(dashboard))
        run = next(row for row in dashboard['recent'] if row['status'] == 'needs_review')
        self.engine.resolved({'request_id': run['request_id'], 'status': 'failed'})
        self.assertEqual(self.engine.dashboard()['counts']['needs_review'], 0)

    def test_global_and_conversation_rate_limits_discard_not_defer(self):
        self.enable(max_replies_hour=1)
        self.incoming()
        self.incoming('next')
        self.assertEqual(len(self.worker.sends), 1)
        self.assertEqual(self.engine.dashboard()['recent'][0]['reason'], 'rate_limited')
        self.clock[0] += 3601
        self.engine.tick()
        self.assertEqual(len(self.worker.sends), 1)

    def test_manual_activity_pauses_and_resume_discards_pause_history(self):
        self.enable()
        with self.engine.manual():
            pass
        self.incoming('during-manual')
        self.assertEqual(self.worker.sends, [])
        self.clock[0] = self.engine.paused_until + 1
        self.engine.tick()
        self.assertEqual(self.worker.sends, [])
        self.incoming('after-baseline')
        self.assertEqual(len(self.worker.sends), 1)

    def test_manual_takeover_during_ai_is_not_blocked_and_cancels_send(self):
        self.enable(ai=True)
        def ai(_cfg, _key, _text):
            with self.engine.manual():
                self.worker.current = 'human-selection'
            return '不得发送此过时回复'
        self.engine.ai_call = ai
        self.incoming()
        self.assertEqual(self.worker.sends, [])
        self.assertEqual(self.engine.runtime['state'], 'paused')
        self.assertEqual(self.engine.dashboard()['recent'][0]['status'], 'skipped')

    def test_config_change_during_ai_cannot_send_or_change_credential_origin(self):
        self.enable(ai=True)
        captured = []
        def ai(cfg, key, _text):
            captured.append((cfg['base_url'], key))
            self.engine.save({'enabled': False, 'ai': {'base_url': 'https://new.example/v1', 'api_key': 'NEW-KEY'}})
            return '配置已变，不应发送'
        self.engine.ai_call = ai
        self.incoming()
        self.assertEqual(captured, [('https://ai.example/v1', 'TEST-ONLY-KEY')])
        self.assertEqual(self.worker.sends, [])
        self.assertEqual(self.engine.runtime['state'], 'disabled')

    def test_ai_disabled_and_failure_do_not_fall_back_or_retry(self):
        self.enable()
        cfg = self.engine.config()
        cfg['rules'][0]['reply_type'] = 'ai'
        self.engine.save(cfg)
        self.engine.tick()
        self.incoming()
        self.assertEqual(self.ai_calls, [])
        self.assertEqual(self.worker.sends, [])
        self.enable(ai=True)
        def failed(*_args):
            raise ValueError('SECRET-RAW-FAILURE')
        self.engine.ai_call = failed
        self.incoming('ai-fails')
        self.engine.tick()
        self.assertEqual(self.worker.sends, [])
        self.assertNotIn('SECRET-RAW-FAILURE', json.dumps(self.engine.dashboard()))

    def test_failed_ai_attempts_also_consume_hourly_budget(self):
        self.enable(ai=True, max_replies_hour=1)
        calls = []
        def failed(*_args):
            calls.append(True)
            raise TimeoutError()
        self.engine.ai_call = failed
        self.incoming('attempt-one')
        self.clock[0] += 30
        self.incoming('attempt-two')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.engine.dashboard()['counts']['ai_calls'], 1)
        self.assertEqual(self.engine.dashboard()['recent'][0]['reason'], 'rate_limited')

    def test_slow_ai_rechecks_unchanged_text_and_new_outgoing_tail(self):
        self.enable(ai=True)
        def changed():
            self.worker.history['c1'][-1]['text'] = '同ID已编辑'
        self.worker.before_messages = changed
        self.incoming()
        self.assertEqual(self.worker.sends, [])
        self.assertEqual(self.engine.dashboard()['recent'][0]['reason'], 'conversation_changed')

    def test_preview_matches_priority_and_fallback_without_ai_or_send(self):
        self.enable(ai=True, fallback=True)
        result = self.engine.preview('问一下价格')
        self.assertTrue(result['requires_ai'])
        self.assertFalse(result['ai_called'])
        self.assertFalse(result['will_send'])
        self.assertEqual(self.engine.preview('其他问题')['rule_id'], 'fallback')
        self.assertEqual(self.ai_calls, [])
        self.assertEqual(self.worker.sends, [])

    def test_ai_preview_requires_explicit_confirmation_and_enabled_ai(self):
        for body in ({'text': '你好'}, {'text': '你好', 'confirmed': False}):
            with self.assertRaises(AutomationError):
                self.engine.generate_preview(body)
        with self.assertRaises(AutomationError):
            self.engine.generate_preview({'text': '你好', 'confirmed': True})
        self.enable(ai=True)
        result = self.engine.generate_preview({'text': '你好', 'confirmed': True})
        self.assertTrue(result['ai_called'])
        self.assertFalse(result['will_send'])
        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(self.worker.sends, [])

    def test_credentials_redacted_clear_and_host_change_guard(self):
        self.engine.save({'ai': {'base_url': 'https://ai.example/v1', 'api_key': 'TEST-PRIVATE-KEY'}})
        self.assertTrue(self.engine.read()['ai']['auth_configured'])
        self.assertNotIn('TEST-PRIVATE-KEY', json.dumps(self.engine.read()))
        self.assertNotIn('TEST-PRIVATE-KEY', self.path.read_bytes().decode(errors='ignore'))
        self.engine.save({'ai': {'base_url': 'https://ai.example/other', 'api_key': ''}})
        self.assertEqual(self.engine.credential(), 'TEST-PRIVATE-KEY')
        with self.assertRaises(AutomationError):
            self.engine.save({'ai': {'base_url': 'https://other.example/v1'}})
        self.engine.save({'ai': {'clear_key': True}})
        self.assertFalse(self.engine.read()['ai']['auth_configured'])
        with self.assertRaises(AutomationError):
            self.engine.save({'ai': {'enabled': True, 'model': 'model'}})

    def test_partial_provider_save_cannot_pair_new_key_with_old_endpoint(self):
        self.enable(ai=True)
        # Simulate a vault write succeeding but the following config DB commit failing.
        self.vault.set('xiaohongshu-ai', json.dumps({'origin': 'new.example', 'key': 'NEW-PRIVATE-KEY'}))
        self.assertEqual(self.engine.config()['ai']['base_url'], 'https://ai.example/v1')
        self.assertEqual(self.engine.credential(), '')
        self.assertFalse(self.engine.read()['ai']['auth_configured'])
        self.incoming('after-partial-save')
        self.assertEqual(self.ai_calls, [])
        self.assertEqual(self.worker.sends, [])

    def test_unsafe_endpoints_and_key_control_chars_rejected_without_network(self):
        for url in ('http://ai.example/v1', 'https://127.0.0.1/v1', 'https://10.0.0.1/v1',
                    'https://localhost/v1', 'https://a:b@ai.example/v1', 'https://ai.example/v1?key=a',
                    'https://ai.example:8443/v1', 'https://ai.example/v1#secret'):
            with self.subTest(url=url), self.assertRaises(AutomationError):
                endpoint(url)
        with self.assertRaises(AutomationError):
            self.engine.save({'ai': {'api_key': 'bad\nheader'}})
        with patch('xhs_automation.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaises(AutomationError):
                ai_reply({'base_url': 'https://ai.example/v1'}, 'TEST', '不应外发')

    def test_account_binding_and_strict_values_fail_closed(self):
        for changes in ({'enabled': 'true'}, {'enabled': True}, {'enabled': True, 'account_id': 'other'},
                        {'max_replies_hour': 0}, {'poll_interval_seconds': 1}, {'manual_pause_seconds': 1},
                        {'rules': [{'id': 'a', 'name': 'bad', 'enabled': True, 'keywords': ['x']}]}):
            with self.subTest(changes=changes), self.assertRaises(AutomationError):
                self.engine.save(changes)
        self.assertEqual(self.worker.sends, [])

    def test_single_process_lock_and_shutdown_disabled_do_not_touch_worker(self):
        self.engine.start()
        other = XhsAutomation(self.path, self.vault, self.worker)
        with self.assertRaises(RuntimeError):
            other.start()
        self.engine.stop()
        self.assertFalse(self.engine.thread.is_alive())
        self.assertEqual(self.worker.calls, [])

    def test_definite_preflight_rejection_does_not_create_unresolvable_unknown(self):
        self.enable()
        self.worker.outcome = XiaohongshuError('固定安全提示', 'source_changed', 409)
        self.incoming()
        self.assertEqual(self.engine.dashboard()['counts']['needs_review'], 0)
        self.assertEqual(self.engine.dashboard()['recent'][0]['status'], 'skipped')
        self.worker.outcome = 'sent'
        self.incoming('later-message')
        self.assertEqual(self.engine.dashboard()['counts']['sent'], 1)

    def test_failed_preflight_read_is_not_a_possibly_sent_message(self):
        self.enable()
        def fail():
            raise TimeoutError('PRIVATE-RAW-EXCEPTION')
        self.worker.before_messages = fail
        self.incoming()
        self.assertEqual(self.worker.sends, [])
        self.assertEqual(self.engine.dashboard()['counts']['needs_review'], 0)
        self.assertEqual(self.engine.dashboard()['recent'][0]['reason'], 'preflight_unavailable')

    def test_explicit_unknown_resolution_works_when_request_never_reached_worker(self):
        self.enable()
        self.worker.outcome = TimeoutError()
        self.incoming()
        run = self.engine.dashboard()['unresolved'][0]
        self.worker.resolve_error = XiaohongshuError('无记录', 'receipt_not_found', 409)
        for body in ({'resolution': 'not_sent'}, {'resolution': 'not_sent', 'confirmed': False}):
            with self.assertRaises(AutomationError):
                self.engine.resolve_run(run['id'], body)
        self.worker.account = 'other-account'
        with self.assertRaises(AutomationError):
            self.engine.resolve_run(run['id'], {'resolution': 'not_sent', 'confirmed': True})
        self.worker.account = 'account-a'
        result = self.engine.resolve_run(run['id'], {'resolution': 'not_sent', 'confirmed': True})
        self.assertFalse(result['will_send'])
        self.assertEqual(self.engine.dashboard()['unresolved_total'], 0)
        self.engine.tick()
        self.assertEqual(len(self.worker.sends), 1)

    def test_resolution_unknown_or_conflict_remains_blocked_and_old_unresolved_stays_visible(self):
        self.enable()
        self.worker.outcome = TimeoutError()
        self.incoming()
        run = self.engine.dashboard()['unresolved'][0]
        self.worker.resolve_error = XiaohongshuError('冲突', 'receipt_conflict', 409)
        with self.assertRaises(AutomationError):
            self.engine.resolve_run(run['id'], {'resolution': 'sent', 'confirmed': True})
        for index in range(55):
            self.engine.record('later-' + str(index), 'c1', None, 'skipped', 'no_rule')
        result = self.engine.dashboard()
        self.assertEqual(len(result['recent']), 50)
        self.assertEqual(result['unresolved_total'], 1)
        self.assertEqual(result['unresolved'][0]['id'], run['id'])

    def test_restart_recovery_only_marks_send_started_as_unknown(self):
        self.engine.save({'account_id': 'account-a'})
        sending = self.engine.record('send-event', 'c1', {'id': 'r', 'name': '规则'}, 'processing', 'send_started')
        generating = self.engine.record('generate-event', 'c1', {'id': 'r', 'name': '规则'}, 'processing', 'matched')
        preview = self.engine.record('', '', {'id': 'ai_preview', 'name': '预览'}, 'processing', 'ai_preview')
        self.engine.start()
        self.engine.stop()
        with self.engine.db() as db:
            states = {row['id']: row['status'] for row in db.execute('SELECT id,status FROM runs')}
        self.assertEqual(states[sending], 'needs_review')
        self.assertEqual(states[generating], 'failed')
        self.assertEqual(states[preview], 'failed')

    def test_ai_transport_rejects_redirect_without_following_or_revealing_body(self):
        class Redirect:
            status = 302
            def read(self, _limit):
                return b'PRIVATE-PROVIDER-ERROR'
        with patch('xhs_automation.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
             patch('xhs_automation.http.client.HTTPSConnection.request') as request, \
             patch('xhs_automation.http.client.HTTPSConnection.getresponse', return_value=Redirect()):
            with self.assertRaises(AutomationError) as failure:
                ai_reply({'base_url': 'https://ai.example/v1', 'model': 'model', 'system_prompt': ''}, 'TEST', '测试')
            self.assertEqual(request.call_count, 1)
            self.assertNotIn('PRIVATE-PROVIDER-ERROR', str(failure.exception))


class HTTPAutomationTests(unittest.TestCase):
    def test_admin_guard_read_preview_and_manual_cannot_inject_auto_send_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, MemorySecrets())
            with TestClient(app, base_url='http://127.0.0.1:59189') as client:
                root = '/api/admin/xiaohongshu'
                for route in ('/automation', '/dashboard'):
                    self.assertEqual(client.get(root + route).status_code, 401)
                self.assertEqual(client.post(root + '/automation', json={}).status_code, 401)
                client.post('/api/auth/setup', json={'password': 'isolated-test-password'})
                self.assertFalse(client.get(root + '/automation').json()['enabled'])
                before = app.state.xhs_automation.paused_until
                self.assertEqual(client.get(root + '/dashboard').status_code, 200)
                self.assertEqual(client.post(root + '/automation/preview', json={'text': '价格'}).status_code, 200)
                self.assertEqual(app.state.xhs_automation.paused_until, before)
                self.assertEqual(client.post(root + '/automation/ai-preview', json={'text': '价格', 'confirmed': True}).status_code, 409)
                self.assertEqual(client.post(root + '/automation', json={}, headers={'Origin': 'https://foreign.example'}).status_code, 403)
                self.assertEqual(app.state.store.list_orders(), [])
        body = {'request_id': str(uuid4()), 'account_id': 'a', 'conversation_id': 'c', 'text': '文字',
                'source_session_id': str(uuid4()), 'source_message_id': 'm'}
        with self.assertRaises(XiaohongshuError):
            validate_body('send', body)
        self.assertEqual(validate_body('send_auto', body), body)

    def test_adapter_retains_worker_session_and_pure_text_contract(self):
        session = str(uuid4())
        client = XiaohongshuClient(MemorySecrets())
        result = client.public_response('messages', {'session_id': session, 'account_id': 'a', 'conversation_id': 'c',
            'header': '会话', 'composer_ready': True, 'messages': [message('m')]}, None)
        self.assertEqual(result['session_id'], session)
        self.assertEqual(result['messages'][0]['content_type'], '1')


if __name__ == '__main__':
    unittest.main()
