"""Queue authorization, deduplication and ambiguous external-write recovery."""
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import hashlib
import json
import threading
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import nextfind_catalog as nf
import subscription_requests as sr
from secrets_store import MemorySecrets

NOW = 1791700000
TOKEN = 'TEST_ONLY_PRIVATE_NEXTFIND_123456789'
BASE = 'https://private-catalog.example:888'


@pytest.fixture
def stack(tmp_path):
    clock = [NOW]
    vault = MemorySecrets()
    catalog = nf.NextFindCatalog(tmp_path, vault)
    catalog.save_config({'base_url': BASE, 'enabled': True, 'token': TOKEN})
    read, add = Mock(return_value=set()), Mock(return_value=None)
    service = sr.SubscriptionRequests(tmp_path, vault, catalog, now=lambda: clock[0], read=read, add=add)
    return service, catalog, vault, read, add, clock


def candidate(service, identifier='1396', kind='tv', title='绝命毒师'):
    return service.decorate_catalog({'configured': True, 'stale': False, 'items': [
        {'id': identifier, 'type': kind, 'title': title}]})['items'][0]['request_token']


def submit(service, identifier='1396', kind='tv', title='绝命毒师', visitor=None):
    visitor = visitor or service.visitor('', create=True)[0]
    result = service.submit(visitor, {'request_token': candidate(service, identifier, kind, title)})
    return result['request'], visitor


def test_guest_submit_only_local_private_persistent_and_deduplicated(stack):
    service, catalog, vault, read, add, clock = stack
    record, visitor = submit(service)
    duplicate = service.submit(visitor, {'request_token': candidate(service)})
    assert duplicate['duplicate'] and duplicate['request']['id'] == record['id']
    assert record['status'] == 'pending' and record['requester_count'] == 1
    assert service.mine(visitor)['items'] == [record]
    assert service.mine(service.visitor('', create=True)[0])['items'] == []
    replacement = sr.SubscriptionRequests(service.data_dir, vault, catalog, now=lambda: clock[0], read=read, add=add)
    assert replacement.mine(visitor)['items'] == [record]
    assert service.path.stat().st_mode & 0o777 == 0o600
    assert TOKEN not in service.path.read_bytes().decode('latin1') and BASE not in service.path.read_bytes().decode('latin1')
    read.assert_not_called()
    add.assert_not_called()


def test_same_title_other_visitors_can_request_and_aggregate(stack):
    service = stack[0]
    first, _ = submit(service)
    second, _ = submit(service)
    assert first['id'] != second['id'] and second['requester_count'] == 2
    assert {row['requester_count'] for row in service.listing()['items']} == {2}


def test_candidate_tampering_expiry_config_change_and_no_raw_input(stack):
    service, catalog, _, read, add, clock = stack
    visitor = service.visitor('', create=True)[0]
    token = candidate(service)
    encoded, signature = token.split('.')
    decoded = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
    decoded['title'] = '篡改片名'
    changed = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b'=').decode() + '.' + signature
    for body in ({'request_token': changed}, {'request_token': token, 'title': '篡改'},
                 {'tmdb_id': '1396', 'media_type': 'tv', 'title': '假冒'}, {'request_token': 'x' * 4096}):
        with pytest.raises(sr.RequestError):
            service.submit(visitor, body)
    clock[0] += sr.CANDIDATE_SECONDS
    with pytest.raises(sr.RequestError, match='过期'):
        service.submit(visitor, {'request_token': token})
    token = candidate(service)
    catalog.save_config({'base_url': 'https://changed.example'})
    with pytest.raises(sr.RequestError, match='更新'):
        service.submit(visitor, {'request_token': token})
    assert service.listing()['items'] == []
    read.assert_not_called()
    add.assert_not_called()


def test_stale_disabled_catalog_never_signs_and_disabled_submit_approve_blocked(stack):
    service, catalog, _, read, add, _ = stack
    record, visitor = submit(service)
    token = candidate(service)
    payload = {'configured': True, 'stale': True, 'items': [{'id': '1396', 'type': 'tv', 'title': '绝命毒师'}]}
    assert 'request_token' not in service.decorate_catalog(payload)['items'][0]
    catalog.save_config({'enabled': False})
    payload['stale'] = False
    assert 'request_token' not in service.decorate_catalog(payload)['items'][0]
    assert service.mine(visitor)['configured'] is False
    with pytest.raises(sr.RequestError) as error:
        service.submit(visitor, {'request_token': token})
    assert error.value.code == 'subscriptions_disabled'
    with pytest.raises(sr.RequestError):
        service.approve(record['id'])
    read.assert_not_called()
    add.assert_not_called()


def test_guest_daily_limits_queue_and_sessions_bounded(stack, monkeypatch):
    service = stack[0]
    visitor = service.visitor('', create=True)[0]
    for number in range(sr.MAX_DAILY_VISITOR):
        submit(service, str(number + 1), visitor=visitor)
    with pytest.raises(sr.RequestError) as error:
        submit(service, '100', visitor=visitor)
    assert error.value.status == 429
    monkeypatch.setattr(sr, 'MAX_REQUESTS', 5)
    with pytest.raises(sr.RequestError) as error:
        submit(service, '100')
    assert error.value.code == 'subscription_queue_full'
    monkeypatch.setattr(sr, 'MAX_SESSIONS', 2)
    with pytest.raises(sr.RequestError) as error:
        service.visitor('', create=True)
    assert error.value.code == 'subscription_queue_full'


def test_session_random_hash_and_expiration(stack):
    service, _, _, _, _, clock = stack
    hashed, token = service.visitor('', create=True)
    assert len(token) == 43 and token != hashed
    assert service.visitor(token) == (hashed, None)
    assert token not in service.path.read_bytes().decode('latin1')
    clock[0] += sr.SESSION_SECONDS
    with pytest.raises(sr.RequestError) as error:
        service.visitor(token)
    assert error.value.status == 401


def test_approval_checks_full_list_and_subscribes_once_all_pending(stack):
    service, _, _, read, add, _ = stack
    first, _ = submit(service)
    second, _ = submit(service)
    assert service.approve(first['id'])['request']['status'] == 'approved'
    assert service.approve(second['id'])['request']['status'] == 'approved'
    later, _ = submit(service)
    assert service.approve(later['id'])['request']['status'] == 'approved'
    assert len(service.listing('approved')['items']) == 3
    read.assert_called_once_with(BASE, TOKEN)
    add.assert_called_once()
    assert {key: add.call_args[0][2][key] for key in ('tmdb_id', 'media_type', 'title')} == {
        'tmdb_id': '1396', 'media_type': 'tv', 'title': '绝命毒师'}


def test_already_subscribed_after_public_24th_item_never_posts(stack):
    service, _, _, read, add, _ = stack
    read.return_value = {(str(n), 'tv') for n in range(1, 50)}
    record, _ = submit(service, '49')
    assert service.approve(record['id'])['request']['status'] == 'approved'
    add.assert_not_called()


def test_concurrent_approvals_across_service_instances_exactly_one_post(stack):
    service, catalog, vault, read, add, clock = stack
    second_service = sr.SubscriptionRequests(service.data_dir, vault, catalog, now=lambda: clock[0], read=read, add=add)
    first, _ = submit(service)
    second, _ = submit(service)
    entered, release = threading.Event(), threading.Event()
    def slow_add(*args):
        entered.set()
        assert release.wait(3)
    add.side_effect = slow_add
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(service.approve, first['id'])
        assert entered.wait(3)
        two = pool.submit(second_service.approve, second['id'])
        release.set()
        assert one.result(timeout=3)['request']['status'] == 'approved'
        assert two.result(timeout=3)['request']['status'] == 'approved'
    add.assert_called_once()


def test_ambiguous_post_persists_across_restart_and_get_reconciliation_never_reposts(stack):
    service, catalog, vault, read, add, clock = stack
    record, _ = submit(service)
    add.side_effect = TimeoutError(TOKEN + BASE)
    with pytest.raises(sr.RequestError) as error:
        service.approve(record['id'])
    assert error.value.code == 'subscription_uncertain' and TOKEN not in str(error.value)
    assert service.listing('uncertain')['items'][0]['id'] == record['id']
    replacement = sr.SubscriptionRequests(service.data_dir, vault, catalog, now=lambda: clock[0], read=read, add=add)
    with pytest.raises(sr.RequestError) as error:
        replacement.approve(record['id'])
    assert error.value.code == 'subscription_uncertain'
    add.assert_called_once()
    read.return_value = {('1396', 'tv')}
    assert replacement.approve(record['id'])['request']['status'] == 'approved'
    add.assert_called_once()


def test_crash_sending_intent_blocks_post_on_recovery_and_config_change(stack):
    service, catalog, _, read, add, _ = stack
    record, _ = submit(service)
    with service._db(write=True) as db:
        db.execute('INSERT INTO deliveries VALUES (?,?,?,?,?)', ('1396', 'tv', 'sending', hashlib.sha256(BASE.encode()).hexdigest(), NOW))
    with pytest.raises(sr.RequestError) as error:
        service.approve(record['id'])
    assert error.value.code == 'subscription_uncertain'
    catalog.save_config({'base_url': 'https://replacement.example'})
    read.reset_mock()
    with pytest.raises(sr.RequestError) as error:
        service.approve(record['id'])
    assert error.value.code == 'subscription_uncertain'
    read.assert_not_called()
    add.assert_not_called()


def test_uncertain_reconciles_after_same_origin_credential_rotation(stack):
    service, catalog, _, read, add, _ = stack
    record, _ = submit(service)
    add.side_effect = TimeoutError()
    with pytest.raises(sr.RequestError):
        service.approve(record['id'])
    catalog.save_config({'token': TOKEN + '_ROTATED'})
    read.return_value = {('1396', 'tv')}
    assert service.approve(record['id'])['request']['status'] == 'approved'
    assert read.call_args[0] == (BASE, TOKEN + '_ROTATED')
    add.assert_called_once()


def test_read_error_leaves_pending_and_does_not_post(stack):
    service, _, _, read, add, _ = stack
    record, _ = submit(service)
    read.side_effect = RuntimeError(TOKEN)
    with pytest.raises(sr.RequestError) as error:
        service.approve(record['id'])
    assert error.value.code == 'nextfind_read_unavailable' and TOKEN not in str(error.value)
    assert service.listing()['items'][0]['status'] == 'pending'
    add.assert_not_called()


def test_reject_sanitized_note_only_pending_and_durable_dedup(stack):
    service, _, _, _, add, _ = stack
    record, visitor = submit(service)
    for note in ('https://private.example/key', '<script>', 'x\nprivate', 'x' * 301):
        with pytest.raises(sr.RequestError):
            service.reject(record['id'], {'note': note})
    result = service.reject(record['id'], {'note': '暂未找到合适资源'})
    assert result['request']['status'] == 'rejected'
    assert service.mine(visitor)['items'][0]['review_note'] == '暂未找到合适资源'
    assert service.submit(visitor, {'request_token': candidate(service)})['duplicate'] is True
    with pytest.raises(sr.RequestError):
        service.approve(record['id'])
    add.assert_not_called()


def test_pagination_and_audit_bounded_by_transitions(stack):
    service = stack[0]
    for number in range(53):
        submit(service, str(number + 1))
    assert len(service.listing()['items']) == 50 and service.listing()['has_more']
    assert len(service.listing(page=2)['items']) == 3 and not service.listing(page=2)['has_more']
    record = service.listing()['items'][0]
    for _ in range(20):
        service.reject(record['id'], {})
    with service._db() as db:
        assert db.execute('SELECT COUNT(*) FROM request_audit WHERE request_id=?', (record['id'],)).fetchone()[0] == 2
    for status, page in [('secret', 1), ('all', 0), ('pending', 201), ('pending', True)]:
        with pytest.raises(sr.RequestError):
            service.listing(status, page)


@pytest.fixture
def clients(tmp_path):
    app = FastAPI()
    vault = MemorySecrets()
    catalog = nf.NextFindCatalog(tmp_path, vault)
    catalog.save_config({'base_url': BASE, 'enabled': True, 'token': TOKEN})
    read, add = Mock(return_value=set()), Mock(return_value=None)
    def admin(request):
        if request.headers.get('x-test-admin') != 'yes':
            raise HTTPException(401, 'login required')
    service = sr.install_routes(app, vault, admin, lambda *args: None, tmp_path, catalog=catalog, read=read, add=add)
    return TestClient(app, base_url='https://site.example'), service, read, add


def test_routes_cookie_security_auth_own_scope_and_csrf(clients):
    client, service, read, add = clients
    result = client.get('/api/public/subscription-requests')
    assert result.status_code == 200 and result.json()['items'] == []
    cookie = result.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=strict' in cookie
    assert client.get('/api/public/subscription-requests', headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    token = candidate(service)
    for headers in ({}, {'origin': 'https://evil.example'}, {'origin': 'https://site.example', 'sec-fetch-site': 'cross-site'}):
        result = client.post('/api/public/subscription-requests', json={'request_token': token}, headers=headers)
        assert result.status_code == 403
    result = client.post('/api/public/subscription-requests', json={'request_token': token}, headers={'origin': 'https://site.example'})
    assert result.status_code == 200
    record = result.json()['request']
    assert record['id'] == client.get('/api/public/subscription-requests').json()['items'][0]['id']
    assert 'visitor_hash' not in result.text and TOKEN not in result.text and BASE not in result.text
    assert client.get('/api/admin/subscription-requests').status_code == 401
    assert client.post('/api/admin/subscription-requests/' + record['id'] + '/approve', json={}).status_code == 401
    assert client.post('/api/admin/subscription-requests/' + record['id'] + '/reject', json={}).status_code == 401
    headers = {'origin': 'https://site.example', 'x-test-admin': 'yes'}
    approved = client.post('/api/admin/subscription-requests/' + record['id'] + '/approve', json={}, headers=headers)
    assert approved.status_code == 200 and approved.json()['request']['status'] == 'approved'
    assert client.get('/api/admin/subscription-requests?status=all', headers=headers).json()['counts']['approved'] == 1
    client.cookies.clear()
    assert client.get('/api/public/subscription-requests').json()['items'] == []
    add.assert_called_once()


def test_no_guest_session_no_submission_and_sanitized_storage_failure(clients):
    client, service, read, add = clients
    result = client.post('/api/public/subscription-requests', json={'request_token': candidate(service)}, headers={'origin': 'https://site.example'})
    assert result.status_code == 401
    with patch.object(service, 'mine', side_effect=RuntimeError(TOKEN + BASE)):
        result = client.get('/api/public/subscription-requests')
    assert result.status_code == 503 and TOKEN not in result.text and BASE not in result.text
    add.assert_not_called()


def test_admin_bad_query_and_origin_and_approved_cannot_reject(clients):
    client, service, read, add = clients
    record, _ = submit(service)
    headers = {'x-test-admin': 'yes'}
    for query in ('?page=0', '?page=1&page=2', '?url=https://secret.example', '?status=unknown'):
        assert client.get('/api/admin/subscription-requests' + query, headers=headers).status_code == 400
    assert client.post('/api/admin/subscription-requests/' + record['id'] + '/approve', json={}, headers=headers).status_code == 403
    headers['origin'] = 'https://site.example'
    assert client.post('/api/admin/subscription-requests/' + record['id'] + '/approve', json={}, headers=headers).status_code == 200
    assert client.post('/api/admin/subscription-requests/' + record['id'] + '/reject', json={}, headers=headers).status_code == 409


class FakeConnection:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls, self.sock = payload, status, [], None
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
    def getresponse(self):
        stream = io.BytesIO(json.dumps(self.payload).encode())
        stream.status = self.status
        return stream
    def close(self):
        pass


def test_full_subscription_transport_not_24_truncated_and_strict_validation():
    payload = {'status': 'success', 'data': [{'tmdb_id': str(i), 'media_type': 'tv'} for i in range(1, 50)]}
    connection = FakeConnection(payload)
    with patch.object(sr.http.client, 'HTTPSConnection', return_value=connection):
        result = sr.read_subscriptions(BASE, TOKEN)
    assert len(result) == 49 and ('49', 'tv') in result
    assert connection.calls[0][0] == ('GET', '/api/openapi/subscriptions')
    for broken in ({**payload, 'has_more': True}, {**payload, 'total': 50}, {**payload, 'pagination': {'page': 1}},
                   {**payload, 'data': [{'tmdb_id': '1396', 'media_type': 'secret'}]},
                   {'status': 'error', 'data': []}, {'status': 'success', 'data': {'items': []}}):
        with patch.object(sr.http.client, 'HTTPSConnection', return_value=FakeConnection(broken)):
            with pytest.raises(sr.ProviderReadError) as error:
                sr.read_subscriptions(BASE, TOKEN)
            assert TOKEN not in str(error.value)


def test_add_transport_fixed_path_json_and_failure_ambiguous():
    connection = FakeConnection({'status': 'success'})
    with patch.object(sr.http.client, 'HTTPSConnection', return_value=connection):
        sr.add_subscription(BASE, TOKEN, {'tmdb_id': '1396', 'media_type': 'tv', 'title': '绝命毒师'})
    args, kwargs = connection.calls[0]
    assert args == ('POST', '/api/openapi/subscriptions/add')
    assert json.loads(kwargs['body']) == {'tmdb_id': '1396', 'media_type': 'tv', 'title': '绝命毒师'}
    assert kwargs['headers']['X-API-Key'] == TOKEN
    for status in (301, 400, 500):
        with patch.object(sr.http.client, 'HTTPSConnection', return_value=FakeConnection({'status': 'error', 'message': TOKEN}, status)):
            with pytest.raises(sr.ProviderWriteUncertain):
                sr.add_subscription(BASE, TOKEN, {'tmdb_id': '1396', 'media_type': 'tv', 'title': '绝命毒师'})
