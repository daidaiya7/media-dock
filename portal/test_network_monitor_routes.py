"""Isolated access-boundary checks; no real probes or production credentials."""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import create_app
from secrets_store import MemorySecrets


@pytest.fixture
def monitor_client(tmp_path):
    environment = {
        'MEDIADOCK_PUBLIC_BASE_URL': 'http://127.0.0.1:59189',
        'MEDIADOCK_REQUIRE_HTTPS': '0',
        'MEDIADOCK_UNIFIED_ADMIN': '0',
        'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example.net',
        'MEDIADOCK_XIANYU_ADMIN_URL': 'http://127.0.0.1:59188',
    }
    with patch.dict(os.environ, environment):
        app = create_app(tmp_path, MemorySecrets())
        with TestClient(app, base_url='http://127.0.0.1:59189') as client:
            yield client, app


def config_body():
    return {'nodes': [{'id': node, 'url': f'https://private-{node}.example.net/health'}
                      for node in ('cdn', 'origin', 'relay-a', 'relay-b', 'relay-c')]}


def login(client):
    assert client.post('/api/auth/setup', json={'password': 'isolated-monitor-password'}).status_code == 200


@pytest.mark.parametrize('route', ['/plans', '/status', '/guide', '/library', '/discover'])
def test_public_information_page_is_read_only_and_has_exact_route_boundary(monitor_client, route):
    client, app = monitor_client
    monitor = app.state.network_monitor
    monitor.save_config(config_body())
    before = monitor.config_path.read_bytes()
    with patch.object(monitor, 'collect', side_effect=AssertionError('page GET must not probe')), \
            patch.object(monitor, 'refresh_probe', side_effect=AssertionError('page GET must not fetch inventory')):
        page = client.get(route)
        assert page.status_code == 200
        assert page.headers['content-type'].startswith('text/html')
        assert '/static/app.js' in page.text
        assert page.headers['Cache-Control'] == 'no-store'
        assert page.headers['X-Frame-Options'] == 'DENY'
        assert "frame-ancestors 'none'" in page.headers['Content-Security-Policy']
        assert not any(row['url'] in page.text for row in config_body()['nodes'])
        redirect = client.get(route + '/', follow_redirects=False)
        assert redirect.status_code == 308
        assert redirect.headers['location'] == route
        assert client.get(route + '/').status_code == 200
        for unknown in (route + '/private', route + 'es', '/unknown-page'):
            assert client.get(unknown).status_code == 404
        assert client.post(route, json={}).status_code == 405
        assert client.get('/api/admin/network-monitor').status_code == 401
        assert client.get('/api/admin/overview').status_code == 401
    assert monitor.config_path.read_bytes() == before


def test_anonymous_visitors_only_receive_safe_snapshot(monitor_client):
    client, app = monitor_client
    app.state.network_monitor.save_config(config_body())
    # A visitor-supplied query must not become an outbound probe or affect the snapshot.
    with patch.object(app.state.network_monitor, 'collect', side_effect=AssertionError('GET must not probe')):
        response = client.get('/api/public/network-status?url=http://169.254.169.254/')
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {'checked_at', 'interval_seconds', 'groups'}
    nodes = [node for group in payload['groups'] for node in group['nodes']]
    assert len(nodes) == 5
    assert all(set(node) == {'id', 'name', 'basis', 'status', 'latency_ms', 'checked_at'} for node in nodes)
    assert not any(word in response.text for word in ('example.net', '169.254', 'https://', 'http://', 'fingerprint'))
    assert client.get('/api/admin/network-monitor').status_code == 401
    assert client.post('/api/admin/network-monitor', json=config_body()).status_code == 401
    assert response.headers['Cache-Control'] == 'no-store'


def test_administrator_can_save_private_targets_but_cross_site_cannot(monitor_client):
    client, app = monitor_client
    login(client)
    with patch.object(app.state.network_monitor, 'collect', side_effect=AssertionError('saving must not probe')):
        response = client.post('/api/admin/network-monitor', json=config_body())
    assert response.status_code == 200
    assert response.json()['nodes'][0]['target'] == config_body()['nodes'][0]['url']
    assert response.json()['nodes'][0]['method'] == 'http'
    assert client.get('/api/admin/network-monitor').json()['nodes'] == response.json()['nodes']
    assert client.post('/api/admin/network-monitor', json=config_body(), headers={'Origin': 'https://untrusted.example.net'}).status_code == 403
    assert 'private-cdn.example.net' not in client.get('/api/public/network-status').text
    client.post('/api/auth/logout', json={})
    assert client.get('/api/admin/network-monitor').status_code == 401


def test_invalid_target_errors_never_echo_sensitive_input(monitor_client):
    client, _ = monitor_client
    login(client)
    invalid = config_body()
    invalid['nodes'][0]['url'] = 'https://private-user:secret-value@secret-host.example.net/check?token=private-token'
    response = client.post('/api/admin/network-monitor', json=invalid)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'invalid_monitor_config'
    assert not any(word in response.text for word in ('secret-value', 'secret-host', 'private-token', 'private-user'))
    assert all(not node['target'] for node in client.get('/api/admin/network-monitor').json()['nodes'])


def test_administrator_can_select_icmp_without_exposing_target(monitor_client):
    client, app = monitor_client
    login(client)
    nodes = [{'id': node, 'method': 'icmp' if node == 'cdn' else 'http',
              'target': 'private-ping.example.net' if node == 'cdn' else ''}
             for node in ('cdn', 'origin', 'relay-a', 'relay-b', 'relay-c')]
    with patch.object(app.state.network_monitor, 'collect', side_effect=AssertionError('saving must not probe')):
        response = client.post('/api/admin/network-monitor', json={'nodes': nodes})
    assert response.status_code == 200
    assert response.json()['nodes'][0]['method'] == 'icmp'
    assert response.json()['nodes'][0]['target'] == 'private-ping.example.net'
    public = client.get('/api/public/network-status')
    assert 'private-ping' not in public.text and 'example.net' not in public.text
    assert all(set(n) == {'id', 'name', 'basis', 'status', 'latency_ms', 'checked_at'}
               for g in public.json()['groups'] for n in g['nodes'])


def test_probe_management_is_admin_only_and_same_origin(monitor_client):
    client, app = monitor_client
    monitor = app.state.network_monitor
    with patch.object(monitor, 'probe_config') as read, \
            patch.object(monitor, 'save_probe_config') as save, \
            patch.object(monitor, 'refresh_probe') as refresh:
        assert client.get('/api/admin/network-probe').status_code == 401
        assert client.post('/api/admin/network-probe', json={'url': 'https://private-probe.example.net'}).status_code == 401
        assert client.post('/api/admin/network-probe/refresh', json={}).status_code == 401
        read.assert_not_called()
        save.assert_not_called()
        refresh.assert_not_called()
        login(client)
        assert client.post('/api/admin/network-probe', json={'url': ''}, headers={'Origin': 'https://untrusted.example.net'}).status_code == 403
        assert client.post('/api/admin/network-probe/refresh', json={}, headers={'Origin': 'https://untrusted.example.net'}).status_code == 403
        save.assert_not_called()
        refresh.assert_not_called()


def test_probe_source_save_and_read_do_not_fetch_network(monitor_client):
    client, app = monitor_client
    login(client)
    with patch.object(app.state.network_monitor, 'refresh_probe', side_effect=AssertionError('explicit refresh only')):
        result = client.post('/api/admin/network-probe', json={'url': 'https://private-probe.example.net'})
        assert result.status_code == 200
        assert result.json()['url'] == 'https://private-probe.example.net'
        assert client.get('/api/admin/network-probe').json()['configured'] is True
        public = client.get('/api/public/network-status')
        assert 'private-probe' not in public.text


def test_probe_refresh_errors_and_input_do_not_echo_private_data(monitor_client):
    client, app = monitor_client
    login(client)
    invalid = client.post('/api/admin/network-probe', json={'url': 'https://private-user:private-secret@hidden.example.net/?token=secret-token'})
    assert invalid.status_code == 400
    assert not any(part in invalid.text for part in ('private-user', 'private-secret', 'hidden.example', 'secret-token'))
    with patch.object(app.state.network_monitor, 'refresh_probe', side_effect=OSError('secret-host.example.net credential')):
        failed = client.post('/api/admin/network-probe/refresh', json={})
    assert failed.status_code == 503
    assert 'secret-host' not in failed.text and 'credential' not in failed.text
    with patch.object(app.state.network_monitor, 'refresh_probe') as refresh:
        assert client.post('/api/admin/network-probe/refresh', json={'url': 'http://169.254.169.254/'}).status_code == 400
        refresh.assert_not_called()


def test_administrator_can_customize_public_name_without_losing_targets(monitor_client):
    client, app = monitor_client
    login(client)
    monitor = app.state.network_monitor
    monitor.save_config(config_body())
    previous = monitor.admin_config()['nodes']
    body = {'nodes': [{'id': n['id'], 'name': '我的源站' if n['id'] == 'origin' else n['name'],
                       'method': n['method'], 'target': n['target']} for n in previous]}
    result = client.post('/api/admin/network-monitor', json=body)
    assert result.status_code == 200
    assert [n['target'] for n in result.json()['nodes']] == [n['target'] for n in previous]
    public = client.get('/api/public/network-status')
    assert '我的源站' in public.text and 'example.net' not in public.text
    body['nodes'][1]['name'] = 'hidden-host.example.net'
    assert client.post('/api/admin/network-monitor', json=body).status_code == 400


def v2_body(config):
    return {'version': 2, 'revision': config['revision'], 'groups': [
        {'id': group['id'], 'name': group['name'], 'nodes': [
            {key: node[key] for key in ('id', 'name', 'method', 'target')}
            for node in group['nodes']]} for group in config['groups']]}


def test_dynamic_groups_titles_add_delete_and_empty_are_persisted(monitor_client):
    client, app = monitor_client
    login(client)
    original = client.get('/api/admin/network-monitor').json()
    body = v2_body(original)
    body['groups'][0]['name'] = '加速入口'
    added = {'id': 'g-' + 'a' * 32, 'name': '备用线路', 'nodes': [
        {'id': 'n-' + 'b' * 32, 'name': '备用一号', 'method': 'icmp', 'target': 'private-new.example.net'}]}
    body['groups'].append(added)
    with patch.object(app.state.network_monitor, 'collect', side_effect=AssertionError('save must not probe')):
        saved = client.post('/api/admin/network-monitor', json=body)
    assert saved.status_code == 200
    assert saved.json()['groups'][-1]['id'] == added['id']
    public = client.get('/api/public/network-status')
    assert '备用线路' in public.text and '备用一号' in public.text and '加速入口' in public.text
    assert 'private-new' not in public.text
    updated = v2_body(saved.json())
    updated['groups'][-1]['nodes'] = []
    deleted = client.post('/api/admin/network-monitor', json=updated)
    assert deleted.status_code == 200
    assert '备用一号' not in client.get('/api/public/network-status').text
    empty = {'version': 2, 'revision': deleted.json()['revision'], 'groups': []}
    assert client.post('/api/admin/network-monitor', json=empty).status_code == 200
    assert client.get('/api/admin/network-monitor').json()['groups'] == []
    assert client.get('/api/public/network-status').json()['groups'] == []


def test_dynamic_config_conflict_rejects_stale_and_legacy_forms(monitor_client):
    client, _ = monitor_client
    login(client)
    original = client.get('/api/admin/network-monitor').json()
    body = v2_body(original)
    body['groups'][0]['name'] = '当前已保存标题'
    saved = client.post('/api/admin/network-monitor', json=body)
    assert saved.status_code == 200
    stale = client.post('/api/admin/network-monitor', json=v2_body(original))
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'monitor_config_conflict'
    legacy = client.post('/api/admin/network-monitor', json=config_body())
    assert legacy.status_code == 409
    assert legacy.json()['error']['code'] == 'monitor_config_conflict'
    assert client.get('/api/admin/network-monitor').json()['revision'] == saved.json()['revision']
    cross_site = client.post('/api/admin/network-monitor', json=v2_body(saved.json()), headers={'Origin': 'https://untrusted.example.net'})
    assert cross_site.status_code == 403


def test_dynamic_route_accepts_large_valid_config_and_keeps_other_limits(monitor_client):
    client, _ = monitor_client
    login(client)
    initial = client.get('/api/admin/network-monitor').json()
    rows = [{'id': 'n-' + format(i, '032x'), 'name': f'线路 {i}', 'method': 'http',
             'target': 'https://private.example.net/' + 'a' * 1950} for i in range(40)]
    body = {'version': 2, 'revision': initial['revision'], 'groups': [
        {'id': 'g-' + 'e' * 32, 'name': '线路列表', 'nodes': rows}]}
    assert len(json.dumps(body)) > 65536
    saved = client.post('/api/admin/network-monitor', json=body)
    assert saved.status_code == 200
    assert len(client.get('/api/admin/network-monitor').json()['groups'][0]['nodes']) == 40
    assert 'private.example' not in client.get('/api/public/network-status').text
    assert client.post('/api/admin/network-monitor', json={'payload': 'a' * 262144}).status_code == 413
    assert client.post('/api/public/orders', json={'payload': 'a' * 32769}).status_code == 413


def test_only_explicit_browser_test_endpoint_is_public_and_in_csp(monitor_client):
    client, app = monitor_client
    login(client)
    monitor = app.state.network_monitor
    monitor.save_config(config_body())
    monitor.save_probe_config({'url': 'https://private-probe.example.net'})
    body = v2_body(monitor.admin_config())
    body['groups'][0]['nodes'][0].update(method='browser', target='https://public-speed.example.net:8443/check')
    with patch.object(monitor, 'collect', side_effect=AssertionError('save/read must not probe')):
        saved = client.post('/api/admin/network-monitor', json=body)
        assert saved.status_code == 200
        public = client.get('/api/public/network-status')
        page = client.get('/')
    cdn = public.json()['groups'][0]['nodes'][0]
    assert cdn['basis'] == 'browser' and cdn['status'] == 'client_pending'
    assert cdn['test_url'] == 'https://public-speed.example.net:8443/check'
    assert cdn['latency_ms'] is None
    csp = page.headers['Content-Security-Policy']
    assert "connect-src 'self' https://public-speed.example.net:8443;" in csp
    assert '/check' not in csp
    for private in ('private-origin.example.net', 'private-relay', 'private-probe.example.net'):
        assert private not in csp and private not in public.text
    assert 'https:' not in csp.split('connect-src ', 1)[1].split(';', 1)[0].replace('https://public-speed.example.net:8443', '')
    changed = v2_body(saved.json())
    changed['groups'][0]['nodes'][0]['method'] = 'http'
    assert client.post('/api/admin/network-monitor', json=changed).status_code == 200
    assert 'public-speed' not in client.get('/').headers['Content-Security-Policy']
    assert 'public-speed' not in client.get('/api/public/network-status').text


@pytest.mark.parametrize('target', [
    'http://private.example.net/check',
    'https://user:password@private.example.net/check',
    'https://private.example.net/check?token=secret',
    'https://private.example.net/check#secret',
    'https://private.example.net/; connect-src *',
    'https://127.0.0.1/check',
])
def test_browser_target_rejects_unsafe_or_private_address_without_leak(monitor_client, target):
    client, app = monitor_client
    login(client)
    body = v2_body(app.state.network_monitor.admin_config())
    body['groups'][0]['nodes'][0].update(method='browser', target=target)
    result = client.post('/api/admin/network-monitor', json=body)
    assert result.status_code == 400
    assert 'private.example.net' not in result.text
    assert 'private.example.net' not in client.get('/').headers['Content-Security-Policy']
