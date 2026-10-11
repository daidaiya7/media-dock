"""Read-only NextFind adapter, public projection, cache and authenticated settings."""
from concurrent.futures import ThreadPoolExecutor
import base64
import io
import json
import os
import threading
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

import app as portal_app
import nextfind_catalog as nf
from secrets_store import MemorySecrets


TOKEN = 'TEST_ONLY_NEXTFIND_KEY_123456789'
BASE = 'https://private-catalog.example:888'
NOW = 1791700000
KEYS = {'id', 'type', 'title', 'poster_url', 'year', 'in_library', 'subscribed', 'local_episodes', 'total_episodes'}


def upstream(mode='search', count=2):
    return {'status': 'success', 'data': [dict(id=str(index + 1), tmdb_id=str(index + 1),
        title='影片 ' + str(index + 1), raw_type='movie' if index % 2 else 'tv',
        media_type='movie' if index % 2 else 'tv', poster='https://image.tmdb.org/t/p/w300/Poster1.jpg',
        year='2026', is_in_library=index == 0, is_subscribed=index == 0,
        local_episodes=3, total_episodes=10, username='PRIVATE_USERNAME',
        source='PRIVATE_SOURCE', shared_code='PRIVATE_SHARE', internal_url=BASE, api_key=TOKEN)
        for index in range(count)]}


def dto(count=2):
    return nf.parse_items(upstream(count=count), 'search', 'all')


@pytest.fixture
def catalog(tmp_path):
    clock = [NOW]
    vault = MemorySecrets()
    fetch = Mock(return_value=dto())
    service = nf.NextFindCatalog(tmp_path, vault, now=lambda: clock[0], fetch=fetch)
    return service, vault, fetch, clock


def enable(service):
    return service.save_config({'base_url': BASE, 'enabled': True, 'token': TOKEN})


def test_disabled_default_is_read_only_and_does_not_fetch(catalog):
    service, _, fetch, _ = catalog
    assert service.config() == {'base_url': '', 'enabled': False, 'token_configured': False}
    assert service.public() == {'configured': False, 'items': [], 'stale': False, 'updated_at': None, 'mode': 'search'}
    assert service.public('影片')['mode'] == 'search'
    assert not list(service.data_dir.iterdir())
    fetch.assert_not_called()


@pytest.mark.parametrize('query', ['', ' ', '\t\n'])
@pytest.mark.parametrize('media_type', ['all', 'movie', 'tv'])
@pytest.mark.parametrize('failed', [False, True])
def test_empty_query_never_returns_legacy_subscription_cache_or_fetches(catalog, query, media_type, failed):
    service, _, fetch, clock = catalog
    enable(service)
    service._settings()  # Keep the simulated legacy cache bound to the current config.
    service._cache[('', media_type)] = {'items': [{**dto()[0], 'title': 'PRIVATE_SUBSCRIPTION_TITLE'}],
        'updated_at': NOW - 500, 'retry_at': NOW + 30, 'failed': failed}
    fetch.side_effect = RuntimeError('private provider unavailable')
    with patch.object(nf, 'MAX_INFLIGHT', 0):
        assert service.public(query, media_type) == {
            'configured': True, 'items': [], 'stale': False, 'updated_at': None, 'mode': 'search'}
    clock[0] += 1000
    assert service.public(query, media_type)['items'] == []
    assert service._pending == {}
    fetch.assert_not_called()


def test_empty_query_does_not_serve_previous_search_results(catalog):
    service, _, fetch, _ = catalog
    enable(service)
    assert len(service.public('影片')['items']) == 2
    assert service.public()['items'] == []
    assert len(service.public('影片')['items']) == 2
    fetch.assert_called_once_with(BASE, TOKEN, '影片', 'all')


def test_secret_config_atomic_private_no_fetch_and_clear(catalog):
    service, vault, fetch, _ = catalog
    vault.set('nextemby', 'OTHER_SECRET')
    assert enable(service) == {'base_url': BASE, 'enabled': True, 'token_configured': True}
    assert json.loads(service.config_path.read_text()) == {'base_url': BASE, 'enabled': True}
    assert TOKEN not in service.config_path.read_text()
    assert service.config_path.stat().st_mode & 0o777 == 0o600
    assert vault.get(nf.SECRET_NAME) == TOKEN
    old_inode = service.config_path.stat().st_ino
    service.save_config({'token': '', 'enabled': True})
    assert service.config_path.stat().st_ino != old_inode
    assert vault.get(nf.SECRET_NAME) == TOKEN
    service.save_config({'clear_token': True})
    assert service.config()['enabled'] is False and service.config()['token_configured'] is False
    assert vault.get('nextemby') == 'OTHER_SECRET'
    fetch.assert_not_called()


@pytest.mark.parametrize('body', [None, [], {}, {'enabled': 'true'}, {'enabled': True},
    {'base_url': True}, {'base_url': []}, {'base_url': 'http://private.example'},
    {'base_url': BASE + '/api/openapi'}, {'base_url': 'https://x:y@private.example'},
    {'base_url': BASE + '?token=PRIVATE'}, {'base_url': BASE + '#PRIVATE'},
    {'base_url': 'https://private.example\\@evil.example'}, {'base_url': 'https://a.example:0'},
    {'base_url': 'https://a.example:99999'}, {'base_url': 'https://a.example\n'},
    {'token': 123}, {'token': 'PRIVATE\nKEY'}, {'token': 'x' * 9000}, {'clear_token': 1},
    {'token': TOKEN, 'clear_token': True}, {'resource_url': BASE}])
def test_invalid_config_never_echoes_input_or_mutates_config(catalog, body):
    service, vault, fetch, _ = catalog
    with pytest.raises(nf.CatalogError) as error:
        service.save_config(body)
    assert str(error.value) == nf.INVALID
    assert not service.config_path.exists() and vault.values == {}
    fetch.assert_not_called()


def test_atomic_failure_preserves_old_configuration(catalog):
    service, _, _, _ = catalog
    enable(service)
    before = service.config_path.read_bytes()
    with patch.object(nf.os, 'replace', side_effect=OSError('private path')):
        with pytest.raises(OSError):
            service.save_config({'enabled': False})
    assert service.config_path.read_bytes() == before
    assert not list(service.data_dir.glob('.nextfind-catalog-config.json-*'))


def test_failed_final_write_after_credential_change_stays_disabled(catalog):
    service, vault, fetch, _ = catalog
    enable(service)
    original_replace = nf.os.replace
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('private path')
        return original_replace(*args, **kwargs)
    with patch.object(nf.os, 'replace', side_effect=fail_second):
        with pytest.raises(OSError):
            service.save_config({'base_url': 'https://new-private.example', 'token': TOKEN + '_NEW', 'enabled': True})
    assert vault.get(nf.SECRET_NAME) == TOKEN + '_NEW'
    assert not service.config()['enabled'] and not service.public()['configured']
    fetch.assert_not_called()


def test_separate_instances_read_url_and_secret_under_one_file_lock(catalog):
    writer, vault, _, clock = catalog
    enable(writer)
    fetch = Mock(return_value=dto())
    reader = nf.NextFindCatalog(writer.data_dir, vault, now=lambda: clock[0], fetch=fetch)
    entered, release = threading.Event(), threading.Event()
    original_set = vault.set
    def blocking_set(name, value):
        entered.set()
        assert release.wait(3)
        original_set(name, value)
    with patch.object(vault, 'set', side_effect=blocking_set), ThreadPoolExecutor(max_workers=2) as pool:
        changing = pool.submit(writer.save_config, {'base_url': 'https://new-private.example', 'token': TOKEN + '_NEW'})
        assert entered.wait(1)
        reading = pool.submit(reader.public, '影片')
        release.set()
        changing.result(timeout=3)
        assert reading.result(timeout=3)['configured']
    fetch.assert_called_once_with('https://new-private.example', TOKEN + '_NEW', '影片', 'all')


def test_strict_local_type_filter_and_white_list():
    result = nf.parse_items(upstream(), 'search', 'tv')
    assert len(result) == 1 and result[0]['type'] == 'tv'
    assert set(result[0]) == KEYS
    assert result[0]['poster_url'] == nf.POSTER_PREFIX + '/Poster1.jpg'
    assert result[0]['year'] == 2026
    assert not any(secret in json.dumps(result) for secret in (BASE, TOKEN, 'PRIVATE_', 'username', 'source', 'api_key'))
    result = nf.parse_items(upstream('subscriptions'), 'subscriptions', 'all')
    assert all(row['subscribed'] for row in result)
    payload = upstream(count=50)
    assert len(nf.parse_items(payload, 'search', 'all')) == 24
    payload['data'][0].pop('raw_type')
    payload['data'][0]['type'] = '剧集'
    assert all(row['id'] != '1' for row in nf.parse_items(payload, 'search', 'all'))


@pytest.mark.parametrize('poster', ['https://private.example/a.jpg', '//evil.example/a.jpg',
    'https://image.tmdb.org.evil.example/t/p/w500/a.jpg', 'https://image.tmdb.org@evil.example/t/p/w500/a.jpg',
    'http://image.tmdb.org/t/p/w500/a.jpg', '/a.jpg?token=secret', '/../a.jpg',
    '/a.svg', '/%2e%2e/a.jpg', '/a.jpg#fragment', 'javascript:alert(1)'])
def test_poster_rejects_non_tmdb_hosts_or_path_injection(poster):
    payload = upstream(count=1)
    payload['data'][0]['poster'] = poster
    assert nf.parse_items(payload, 'search', 'all')[0]['poster_url'] is None


def test_poster_path_fallback_and_invalid_rows_types_numbers():
    payload = upstream(count=1)
    row = payload['data'][0]
    row.update(poster='https://private.example', poster_path='/Safe_poster-1.webp', year=True,
               is_in_library='true', is_subscribed=1, local_episodes=-1, total_episodes=float('inf'))
    result = nf.parse_items(payload, 'search', 'all')[0]
    assert result['poster_url'] == nf.POSTER_PREFIX + '/Safe_poster-1.webp'
    assert result['year'] is None and result['local_episodes'] is None and result['total_episodes'] is None
    assert not result['in_library'] and not result['subscribed']
    for change in ({'id': True}, {'id': '../1'}, {'id': '01'}, {'raw_type': 'private'},
                   {'title': '<script>secret</script>'}, {'title': 'PRIVATE\x00'}, {'title': BASE}):
        assert nf.parse_items({'status':'success','data':[{**row, **change}]}, 'search', 'all') == []


def test_verified_nextfind_image_proxy_path_converts_to_fixed_public_tmdb_only():
    payload = upstream(count=1)
    payload['data'][0]['poster'] = '/api/tmdb/image/SafeNextFindPoster.jpg'
    assert nf.parse_items(payload, 'search', 'all')[0]['poster_url'] == nf.POSTER_PREFIX + '/SafeNextFindPoster.jpg'
    for bad in ('/api/tmdb/image/../secret.jpg', '/api/tmdb/image/secret.jpg?key=PRIVATE',
                BASE + '/api/tmdb/image/secret.jpg', '/unverified/proxy/secret.jpg'):
        payload['data'][0]['poster'] = bad
        assert nf.parse_items(payload, 'search', 'all')[0]['poster_url'] is None


@pytest.mark.parametrize('payload', [None, [], {'status': 'error', 'data': []}, {'status': 'success', 'data': {}},
    {'status': 'success', 'data': [None] * 10001}])
def test_invalid_upstream_payload_sanitized(payload):
    with pytest.raises(nf.CatalogUnavailable) as error:
        nf.parse_items(payload, 'search', 'all')
    assert str(error.value) == nf.UNAVAILABLE


def test_http_get_fixed_paths_auth_header_timeout_and_query_encoded():
    response = Mock(status=200)
    response.read1 = io.BytesIO(json.dumps(upstream()).encode()).read1
    connection = Mock()
    connection.getresponse.return_value = response
    query = '片名&query=other/../resources'
    with patch.object(nf.http.client, 'HTTPSConnection', return_value=connection) as factory:
        result = nf.fetch_items(BASE, TOKEN, query, 'tv')
    assert len(result) == 1
    assert factory.call_args.args == ('private-catalog.example', 888)
    assert factory.call_args.kwargs['timeout'] == 12
    args, kwargs = connection.request.call_args
    assert args[0] == 'GET'
    assert urlsplit(args[1]).path == '/api/openapi/search'
    assert parse_qs(urlsplit(args[1]).query) == {'query': [query], 'type': ['剧集']}
    assert TOKEN not in args[1] and kwargs['headers']['X-API-Key'] == TOKEN
    assert 'body' not in kwargs
    response.read1 = io.BytesIO(json.dumps(upstream()).encode()).read1
    with patch.object(nf.http.client, 'HTTPSConnection', return_value=connection):
        nf.fetch_items(BASE, TOKEN, '', 'all')
    assert connection.request.call_args.args == ('GET', '/api/openapi/subscriptions')
    assert connection.close.call_count == 2


@pytest.mark.parametrize('status,body', [(302, b'private redirect'), (401, TOKEN.encode()),
    (200, b'x' * (nf.MAX_RESPONSE_BYTES + 1)), (200, b'not-json'), (200, b'{"status":"error","data":[]}')])
def test_no_redirects_no_private_errors_and_bounded_response(status, body):
    response = Mock(status=status)
    response.read1 = io.BytesIO(body).read1
    connection = Mock()
    connection.getresponse.return_value = response
    with patch.object(nf.http.client, 'HTTPSConnection', return_value=connection) as factory:
        with pytest.raises(nf.CatalogUnavailable) as error:
            nf.fetch_items(BASE, TOKEN, '', 'all')
    assert str(error.value) == nf.UNAVAILABLE
    factory.assert_called_once()
    connection.request.assert_called_once()
    connection.close.assert_called_once()


def test_request_deadline_prevents_another_full_timeout_after_connect():
    connection = Mock()
    with patch.object(nf.http.client, 'HTTPSConnection', return_value=connection), \
            patch.object(nf.time, 'monotonic', side_effect=[100, 113]):
        with pytest.raises(nf.CatalogUnavailable):
            nf.fetch_items(BASE, TOKEN, '', 'all')
    connection.getresponse.assert_not_called()
    connection.close.assert_called_once()


def test_cache_ttl_last_good_failure_and_retry_throttle(catalog):
    service, _, fetch, clock = catalog
    enable(service)
    first = service.public('测试', 'all')
    before = service.config_path.read_bytes()
    assert first['configured'] and not first['stale'] and first['mode'] == 'search'
    first['items'][0]['title'] = 'Caller mutation'
    assert service.public('测试')['items'][0]['title'] != 'Caller mutation'
    assert fetch.call_count == 1
    clock[0] += 301
    fetch.side_effect = RuntimeError(TOKEN + BASE)
    failed = service.public('测试')
    assert failed['stale'] and failed['updated_at'] == first['updated_at'] and len(failed['items']) == 2
    assert service.public('测试') == failed and fetch.call_count == 2
    clock[0] += 31
    fetch.side_effect = None
    assert service.public('测试')['stale'] is False
    assert fetch.call_count == 3 and service.config_path.read_bytes() == before


def test_failure_without_cache_generic_and_throttled(catalog):
    service, _, fetch, _ = catalog
    enable(service)
    fetch.side_effect = RuntimeError('PRIVATE_RESPONSE')
    for _ in range(2):
        with pytest.raises(nf.CatalogUnavailable) as error:
            service.public('影片')
        assert str(error.value) == nf.UNAVAILABLE
    assert fetch.call_count == 1


def test_cache_lru_is_bounded_and_config_or_secret_rotation_invalidates(catalog):
    service, vault, fetch, _ = catalog
    enable(service)
    for index in range(130):
        service.public('影片' + str(index))
    assert len(service._cache) == 128
    service.public('影片129')
    assert fetch.call_count == 130
    service.public('影片0')
    assert fetch.call_count == 131
    vault.set(nf.SECRET_NAME, TOKEN + '_ROTATED')
    service.public('影片0')
    assert fetch.call_count == 132 and len(service._cache) == 1
    service.save_config({'enabled': False})
    assert not service.public()['configured'] and not service._cache


def test_same_query_concurrency_only_one_upstream_and_bounded_other_queries(catalog):
    service, _, fetch, _ = catalog
    enable(service)
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return dto()
    fetch.side_effect = slow
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(service.public, '影片') for _ in range(8)]
        assert entered.wait(1)
        release.set()
        results = [future.result(timeout=3) for future in futures]
    assert fetch.call_count == 1 and all(result == results[0] for result in results)
    assert not service._pending
    service._cache.clear()
    with patch.object(nf, 'MAX_INFLIGHT', 0):
        with pytest.raises(nf.CatalogUnavailable):
            service.public('另一部影片')
    assert fetch.call_count == 1


def test_inflight_old_config_result_is_not_published_after_disable(catalog):
    service, _, fetch, _ = catalog
    enable(service)
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        entered.set()
        assert release.wait(3)
        return dto()
    fetch.side_effect = slow
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.public, '影片')
        assert entered.wait(1)
        service.save_config({'enabled': False})
        release.set()
        with pytest.raises(nf.CatalogUnavailable):
            future.result(timeout=3)
    assert not service.public()['configured'] and not service._cache and not service._pending


def test_public_posters_are_localized_and_proxy_is_fixed_domain(catalog, monkeypatch):
    service, _, fetch, _ = catalog
    enable(service)
    fetch.return_value = dto()
    result = service.public('影片')
    poster = result['items'][0]['poster_url']
    assert poster.startswith('/api/public/discovery/poster/')
    opaque = poster.rsplit('/', 1)[1]
    assert service._poster_paths[opaque].startswith('/')
    response = Mock(status=200)
    response.getheader.side_effect = lambda name, default=None: {'Content-Encoding':'identity','Content-Type':'image/jpeg'}.get(name, default)
    response.read = lambda size: b'jpg'
    connection = Mock(); connection.getresponse.return_value=response
    monkeypatch.setattr(nf.http.client, 'HTTPSConnection', lambda *args, **kwargs: connection)
    body, mime = service.poster(opaque)
    assert body == b'jpg' and mime == 'image/jpeg'
    connection.request.assert_called_once()
    assert connection.request.call_args.args[0] == 'GET'
    assert connection.request.call_args.args[1].startswith('/t/p/w500/')

def test_adapter_results_are_projected_again_at_public_boundary(catalog):
    service, _, fetch, _ = catalog
    enable(service)
    fetch.return_value = [{**dto()[0], 'username': 'PRIVATE_USERNAME', 'source': BASE, 'poster_url': BASE + '/poster'}]
    result = service.public('影片')
    assert set(result['items'][0]) == KEYS and result['items'][0]['poster_url'] is None
    assert 'PRIVATE_' not in json.dumps(result) and BASE not in json.dumps(result)


@pytest.fixture
def client(tmp_path):
    vault = MemorySecrets()
    env = {'MEDIADOCK_PUBLIC_BASE_URL': 'http://127.0.0.1:59189', 'MEDIADOCK_REQUIRE_HTTPS': '0',
           'MEDIADOCK_UNIFIED_ADMIN': '0', 'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example.net',
           'MEDIADOCK_XIANYU_ADMIN_URL': 'http://127.0.0.1:59188'}
    # Reuse real application authentication, rate and origin middleware without editing app.py.
    with patch.dict(os.environ, env), patch.object(portal_app, 'install_xiaohongshu_routes', nf.install_routes):
        app = portal_app.create_app(tmp_path, vault)
        app.state.nextfind_catalog.fetch = Mock(return_value=dto())
        with TestClient(app, base_url='http://127.0.0.1:59189') as browser:
            yield browser, app.state.nextfind_catalog


def login(client):
    assert client.post('/api/auth/setup', json={'password': 'isolated-nextfind-password'}).status_code == 200


def test_routes_admin_auth_origin_safe_errors_and_no_public_writes(client):
    browser, service = client
    assert browser.get('/api/admin/nextfind-catalog').status_code == 401
    assert browser.post('/api/admin/nextfind-catalog', json={'enabled': False}).status_code == 401
    assert browser.get('/api/public/discovery').json()['configured'] is False
    assert browser.post('/api/public/discovery', json={}).status_code == 405
    assert browser.get('/api/public/discovery/private').status_code == 404
    login(browser)
    assert browser.post('/api/admin/nextfind-catalog', json={'base_url': BASE, 'token': TOKEN, 'enabled': True},
                        headers={'Origin': 'https://evil.example'}).status_code == 403
    result = browser.post('/api/admin/nextfind-catalog', json={'base_url': BASE, 'token': TOKEN, 'enabled': True})
    assert result.status_code == 200 and TOKEN not in result.text
    service.fetch.assert_not_called()
    before = service.config_path.read_bytes()
    response = browser.get('/api/public/discovery', params={'query': '影片'})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    assert set(response.json()) == {'configured','items','stale','updated_at','mode'}
    for item in response.json()['items']:
        assert set(item) == KEYS | {'request_token'}
        encoded, signature = item['request_token'].split('.')
        signed = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        assert (signed['id'], signed['type'], signed['title']) == (item['id'], item['type'], item['title'])
        assert not any(private in json.dumps(signed) for private in (BASE, TOKEN, 'username', 'source'))
        assert len(signature) == 64 and signed['exp'] - signed['iat'] == 1800
    assert not any(private in response.text for private in (BASE, TOKEN, 'username', 'source'))
    assert service.config_path.read_bytes() == before
    for malformed in ([TOKEN], TOKEN, {'token': TOKEN, 'unknown': BASE}):
        result = browser.post('/api/admin/nextfind-catalog', json=malformed)
        assert result.status_code == 400 and TOKEN not in result.text and BASE not in result.text


@pytest.mark.parametrize('params', [{}, {'query': ''}, {'query': '  '}, {'type': 'tv'}, {'query': '', 'type': 'movie'}])
def test_public_empty_query_is_search_only_no_existing_subscriptions(client, params):
    browser, service = client
    enable(service)
    service._settings()
    service._cache[('', params.get('type', 'all'))] = {
        'items': [{**dto()[0], 'title': 'PRIVATE_SUBSCRIPTION_TITLE'}],
        'updated_at': NOW, 'retry_at': 9999999999, 'failed': True}
    service.fetch.side_effect = RuntimeError('PRIVATE_RESPONSE')
    response = browser.get('/api/public/discovery', params=params)
    assert response.status_code == 200
    assert response.json() == {'configured': True, 'items': [], 'stale': False, 'updated_at': None, 'mode': 'search'}
    assert 'request_token' not in response.text and 'PRIVATE_' not in response.text and TOKEN not in response.text
    assert response.headers['Cache-Control'] == 'no-store'
    service.fetch.assert_not_called()


@pytest.mark.parametrize('query', ['?url=https://private.example', '?type=person', '?query=x', '?query='+('x'*81),
    '?query=%3Cscript%3E', '?query=https://private.example', '?type=all&type=tv', '?query=ab%00cd'])
def test_public_query_validation_never_echoes_input_or_calls_provider(client, query):
    browser, service = client
    enable(service)
    result = browser.get('/api/public/discovery' + query)
    assert result.status_code == 400
    assert 'private.example' not in result.text and '<script' not in result.text
    service.fetch.assert_not_called()


def test_rate_limit_and_uncached_failure_status(client):
    browser, service = client
    enable(service)
    service.fetch.side_effect = RuntimeError(TOKEN)
    result = browser.get('/api/public/discovery', params={'query': '影片'})
    assert result.status_code == 503 and TOKEN not in result.text
    for _ in range(29):
        assert browser.get('/api/public/discovery', params={'query': '影片'}).status_code == 503
    assert browser.get('/api/public/discovery', params={'query': '影片'}).status_code == 429
    assert service.fetch.call_count == 1


def test_seed_cli_only_nonsecret_config(tmp_path, capsys):
    vault = MemorySecrets()
    vault.set(nf.SECRET_NAME, TOKEN)
    with patch.dict(os.environ, {'MEDIADOCK_DATA_DIR': str(tmp_path)}), patch.object(nf, 'default_secret_store', return_value=vault):
        assert nf.main(['--base-url', BASE, '--enable']) == 0
    assert TOKEN not in capsys.readouterr().out
    assert nf.NextFindCatalog(tmp_path, vault).config()['enabled'] is True


def test_base_url_canonical_https_ipv6():
    assert nf.base_url('https://[2001:db8::1]:888/') == 'https://[2001:db8::1]:888'
    assert nf.base_url('https://EXAMPLE.COM/') == 'https://example.com'
