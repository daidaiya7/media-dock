"""Isolated TMDB cache, credential and public/admin route checks."""
import io
import json
import os
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import featured_movies as fm
from app import create_app
from secrets_store import FileSecrets, KeychainSecrets, MemorySecrets, SecretStoreError, SERVICE


NOW = 1791700000
TOKEN = 'TEST_ONLY_TMDB_READ_TOKEN_123456789'


def payload(count=8):
    return {'results': [{'id': index + 1, 'title': f'影片 {index + 1}',
                         'poster_path': f'/Poster{index + 1}.jpg', 'release_date': '2026-10-01',
                         'overview': 'Not public', 'vote_average': 8.8, 'adult': False}
                        for index in range(count)]}


@pytest.fixture
def featured(tmp_path):
    clock = [NOW]
    vault = MemorySecrets()
    fetch = Mock(return_value=fm.parse_movies(payload()))
    service = fm.FeaturedMovies(tmp_path, vault, now=lambda: clock[0], fetch=fetch, fetch_art=lambda url: b'\xff\xd8\xffcached-image')
    return service, vault, fetch, clock


def enable(service):
    return service.save_config({'enabled': True, 'token': TOKEN})


def test_unconfigured_is_disabled_empty_read_only_and_does_not_create_files(featured):
    service, vault, fetch, _ = featured
    assert service.config() == {'enabled': False, 'token_configured': False, 'last_success': None, 'movie_count': 0}
    assert service.public() == {'movies': [], 'updated_at': None, 'stale': True,
                                'source': fm.SOURCE, 'source_url': fm.SOURCE_URL}
    assert list(service.data_dir.iterdir()) == []
    assert service.refresh() is False
    fetch.assert_not_called()


def test_saving_secret_never_fetches_or_writes_token_to_cache_or_config(featured):
    service, vault, fetch, _ = featured
    result = enable(service)
    assert result['enabled'] and result['token_configured']
    assert vault.get(fm.SECRET_NAME) == TOKEN
    assert TOKEN not in json.dumps(result)
    assert service.config_path.read_text() == '{"enabled":true}'
    assert service.config_path.stat().st_mode & 0o777 == 0o600
    assert not service.cache_path.exists()
    fetch.assert_not_called()
    assert service.save_config({'token': '', 'enabled': True})['token_configured']
    assert vault.get(fm.SECRET_NAME) == TOKEN


def test_success_updates_atomically_then_public_only_reads_whitelisted_cache(featured):
    service, _, fetch, clock = featured
    enable(service)
    assert service.refresh()
    first_inode = service.cache_path.stat().st_ino
    assert service.cache_path.stat().st_mode & 0o777 == 0o600
    clock[0] += 86400
    assert service.refresh()
    assert service.cache_path.stat().st_ino != first_inode
    fetch.reset_mock()
    result = service.public()
    assert len(result['movies']) == 6 and result['stale'] is False
    assert result['updated_at'].endswith('Z')
    assert set(result['movies'][0]) == {'id', 'title', 'poster_url', 'url', 'release_date'}
    assert 'Not public' not in json.dumps(result) and TOKEN not in json.dumps(result)
    service.config()
    fetch.assert_not_called()


@pytest.mark.parametrize('bad', [RuntimeError('DO-NOT-EXPOSE-TOKEN'), [], [{'id': 1, 'poster_url': 'https://evil.example'}]])
def test_refresh_failure_preserves_last_good_and_sanitizes_errors(featured, bad):
    service, _, fetch, clock = featured
    enable(service)
    service.refresh()
    previous = service.cache_path.read_bytes()
    clock[0] += 60
    if isinstance(bad, Exception):
        fetch.side_effect = bad
    else:
        fetch.return_value = bad
    with pytest.raises(fm.FeaturedMoviesError) as error:
        service.refresh()
    assert str(error.value) == fm.ERROR_MESSAGE
    assert service.cache_path.read_bytes() == previous
    assert service.public()['movies']


def test_failed_atomic_replace_retains_existing_file_and_cleans_temporary(featured):
    service, _, _, _ = featured
    enable(service)
    service.refresh()
    previous = service.cache_path.read_bytes()
    with patch.object(fm.os, 'replace', side_effect=OSError('private path')):
        with pytest.raises(fm.FeaturedMoviesError):
            service.refresh()
    assert service.cache_path.read_bytes() == previous
    assert not list(service.data_dir.glob('.featured-movies.json-*'))


def test_expired_content_remains_available_but_marked_stale(featured):
    service, _, _, clock = featured
    enable(service)
    service.refresh()
    clock[0] += fm.STALE_SECONDS + 1
    assert len(service.public()['movies']) == 6
    assert service.public()['stale'] is True
    service.save_config({'enabled': False})
    assert service.public()['movies'] == []
    assert service.config()['movie_count'] == 6


def test_clear_really_removes_token_disables_feed_and_preserves_other_secrets(featured):
    service, vault, fetch, _ = featured
    vault.set('nextemby', 'UNCHANGED')
    enable(service)
    service.refresh()
    result = service.save_config({'clear_token': True})
    assert result['enabled'] is False and result['token_configured'] is False
    assert vault.get(fm.SECRET_NAME) is None
    assert vault.get('nextemby') == 'UNCHANGED'
    fetch.reset_mock()
    assert service.refresh() is False
    fetch.assert_not_called()
    assert service.public()['movies'] == []


@pytest.mark.parametrize('bad', [{}, {'enabled': 'true'}, {'enabled': True}, {'token': 123},
    {'token': 'private\nTOKEN'}, {'token': 'https://private.example/token'}, {'clear_token': 'yes'},
    {'token': TOKEN, 'clear_token': True}, {'source_url': 'https://private.example'}])
def test_invalid_configuration_cannot_mutate_or_echo_secret(featured, bad):
    service, vault, fetch, _ = featured
    with pytest.raises(fm.FeaturedMoviesError) as error:
        service.save_config(bad)
    assert 'private' not in str(error.value) and TOKEN not in str(error.value)
    assert vault.values == {}
    assert not service.config_path.exists()
    fetch.assert_not_called()


def test_parser_rejects_wrong_hosts_xss_adult_and_duplicates_preserves_only_valid_dates():
    good = payload(1)['results'][0]
    bad = [dict(good, id=True), dict(good, id=-1), dict(good, title='<script>alert(1)</script>'),
           dict(good, title='A\x00B'), dict(good, poster_path='https://evil.example/poster.jpg'),
           dict(good, poster_path='//evil.example/a.jpg'), dict(good, poster_path='/../a.jpg'),
           dict(good, poster_path='/a.jpg?token=x'), dict(good, poster_path='/a.svg'),
           dict(good, adult=True), dict(good, media_type='tv')]
    result = fm.parse_movies({'results': bad + [dict(good, release_date='2026-02-31'), good]})
    assert len(result) == 1 and result[0]['id'] == 1
    assert 'release_date' not in result[0]
    assert result[0]['poster_url'] == fm.POSTER_PREFIX + '/Poster1.jpg'
    assert result[0]['url'] == fm.MOVIE_PREFIX + '1'


def test_optional_backdrop_round_trips_through_cache_without_extra_fetches(featured):
    service, _, fetch, _ = featured
    rows = payload(2)
    rows['results'][0]['backdrop_path'] = '/Landscape_1-abc.jpg'
    movies = fm.parse_movies(rows)
    assert movies[0]['backdrop_url'] == fm.BACKDROP_PREFIX + '/Landscape_1-abc.jpg'
    assert movies[0]['poster_url'] == fm.POSTER_PREFIX + '/Poster1.jpg'
    assert 'backdrop_url' not in movies[1]
    fetch.return_value = movies
    enable(service)
    service.refresh()
    fetch.assert_called_once_with(TOKEN)
    assert service.public()['movies'][0]['backdrop_url'].startswith(fm.IMAGE_PREFIX)
    assert 'backdrop_url' not in service.public()['movies'][1]
    assert json.loads(service.cache_path.read_text())['movies'] == movies
    fetch.assert_called_once_with(TOKEN)


@pytest.mark.parametrize('path', [None, '', 12, 'https://evil.example/a.jpg',
    '//evil.example/a.jpg', '/../a.jpg', '/a.jpg?token=private', '/a.jpg#fragment',
    '/a.svg', '/a.jpg\n', '/a%2f.jpg'])
def test_invalid_optional_backdrop_is_omitted_without_discarding_movie(path):
    row = dict(payload(1)['results'][0], backdrop_path=path)
    movies = fm.parse_movies({'results': [row]})
    assert len(movies) == 1 and movies[0]['id'] == row['id']
    assert 'backdrop_url' not in movies[0]


def test_legacy_cache_and_foreign_backdrops_keep_validated_posters(featured):
    service, _, fetch, _ = featured
    enable(service)
    service.refresh()
    cache = json.loads(service.cache_path.read_text())
    # The first row represents the pre-backdrop cache schema.
    cache['movies'][1]['backdrop_url'] = 'https://evil.example/private.jpg'
    cache['movies'][2]['backdrop_url'] = fm.BACKDROP_PREFIX + '/../private.jpg'
    cache['movies'][3]['backdrop_url'] = fm.BACKDROP_PREFIX + '/a.jpg?token=private'
    cache['movies'][4]['backdrop_url'] = fm.BACKDROP_PREFIX + '/Landscape.webp'
    cache['movies'][5]['backdrop_url'] = fm.BACKDROP_PREFIX + '.evil.example/a.jpg'
    service.cache_path.write_text(json.dumps(cache))
    fetch.reset_mock()
    movies = service.public()['movies']
    assert len(movies) == len(cache['movies']) == 6
    assert all('poster_url' in movie for movie in movies)
    assert not any('backdrop_url' in movie for movie in movies)
    assert 'private' not in json.dumps(movies) and 'evil.example' not in json.dumps(movies)
    fetch.assert_not_called()


def test_cache_revalidation_drops_foreign_urls_scripts_invalid_time_and_extra_fields(featured):
    service, _, _, _ = featured
    enable(service)
    service.refresh()
    cache = json.loads(service.cache_path.read_text())
    cache['movies'][0]['poster_url'] = 'https://evil.example/a.jpg'
    cache['movies'][1]['title'] = '<img src=x onerror=alert(1)>'
    cache['movies'][2]['url'] = 'javascript:alert(1)'
    cache['movies'][3]['secret'] = TOKEN
    cache['source_url'] = 'https://evil.example/'
    service.cache_path.write_text(json.dumps(cache))
    result = service.public()
    assert len(result['movies']) == 3
    assert not any(value in json.dumps(result) for value in ('evil.example', '<img', 'javascript:', TOKEN))
    for bad_time in (True, float('nan'), NOW + 100, -1):
        cache['updated_at'] = bad_time
        service.cache_path.write_text(json.dumps(cache))
        assert service.public()['movies'] == []
    service.cache_path.write_text('x' * (fm.MAX_CACHE_BYTES + 1))
    assert service.public()['movies'] == []


def test_http_fetch_fixed_origin_auth_header_bounded_body_no_redirects():
    response = Mock(status=200)
    response.read1 = io.BytesIO(json.dumps(payload()).encode()).read1
    connection = Mock()
    connection.getresponse.return_value = response
    with patch.object(fm.http.client, 'HTTPSConnection', return_value=connection) as factory:
        assert len(fm.fetch_movies(TOKEN)) == 6
    assert factory.call_args.args == (fm.API_HOST,)
    assert factory.call_args.kwargs['timeout'] == fm.TIMEOUT_SECONDS
    args, kwargs = connection.request.call_args
    assert args == ('GET', fm.API_PATH)
    assert TOKEN not in args[1]
    assert kwargs['headers']['Authorization'] == 'Bearer ' + TOKEN
    connection.close.assert_called_once()
    for status, body in ((302, b'private'), (401, TOKEN.encode()),
                         (200, b'x' * (fm.MAX_RESPONSE_BYTES + 1)), (200, b'not json')):
        response.status = status
        response.read1 = io.BytesIO(body).read1
        with patch.object(fm.http.client, 'HTTPSConnection', return_value=connection):
            with pytest.raises(fm.FeaturedMoviesError) as error:
                fm.fetch_movies(TOKEN)
        assert str(error.value) == fm.ERROR_MESSAGE


def test_file_vault_delete_only_target_refuses_unsafe_links(tmp_path):
    vault = FileSecrets(tmp_path.resolve() / 'vault')
    vault.set(fm.SECRET_NAME, TOKEN)
    vault.set('nextemby', 'UNCHANGED')
    vault.delete(fm.SECRET_NAME)
    vault.delete(fm.SECRET_NAME)
    assert vault.get(fm.SECRET_NAME) is None and vault.get('nextemby') == 'UNCHANGED'
    (vault.directory / fm.SECRET_NAME).symlink_to(vault.directory / 'nextemby')
    with pytest.raises(SecretStoreError):
        vault.delete(fm.SECRET_NAME)
    assert vault.get('nextemby') == 'UNCHANGED'


def test_keychain_delete_is_idempotent_and_does_not_expose_provider_exception():
    class DeleteError(Exception):
        pass
    backend = SimpleNamespace(delete_password=Mock(), get_password=Mock(return_value=None),
                              errors=SimpleNamespace(PasswordDeleteError=DeleteError))
    with patch.dict('sys.modules', {'keyring': backend}):
        KeychainSecrets().delete(fm.SECRET_NAME)
        backend.delete_password.assert_called_once_with(SERVICE, fm.SECRET_NAME)
        backend.delete_password.side_effect = DeleteError('do not disclose')
        KeychainSecrets().delete(fm.SECRET_NAME)
        backend.get_password.return_value = TOKEN
        with pytest.raises(SecretStoreError) as error:
            KeychainSecrets().delete(fm.SECRET_NAME)
        assert 'disclose' not in str(error.value) and TOKEN not in str(error.value)


def test_cli_uses_configured_server_vault_and_skips_disabled_without_network(tmp_path, capsys):
    service = Mock()
    service.refresh.return_value = False
    vault = MemorySecrets()
    with patch.dict(os.environ, {'MEDIADOCK_DATA_DIR': str(tmp_path)}), \
            patch.object(fm, 'default_secret_store', return_value=vault), \
            patch.object(fm, 'FeaturedMovies', return_value=service) as constructor:
        assert fm.main() == 0
        constructor.assert_called_once_with(str(tmp_path), vault)
        assert 'skipped' in capsys.readouterr().out
        service.refresh.return_value = True
        assert fm.main() == 0
        service.refresh.side_effect = OSError(TOKEN)
        assert fm.main() == 1
        assert TOKEN not in capsys.readouterr().out


@pytest.fixture
def featured_client(tmp_path):
    environment = {'MEDIADOCK_PUBLIC_BASE_URL': 'http://127.0.0.1:59189', 'MEDIADOCK_REQUIRE_HTTPS': '0',
                   'MEDIADOCK_UNIFIED_ADMIN': '0', 'MEDIADOCK_NEXTEMBY_URL': 'https://nextemby.example.net',
                   'MEDIADOCK_XIANYU_ADMIN_URL': 'http://127.0.0.1:59188'}
    with patch.dict(os.environ, environment):
        app = create_app(tmp_path, MemorySecrets())
        with TestClient(app, base_url='http://127.0.0.1:59189') as client:
            yield client, app


def test_routes_public_never_fetch_admin_requires_auth_and_same_origin(featured_client):
    client, app = featured_client
    feature = app.state.featured_movies
    feature.fetch_art = Mock(return_value=b'\xff\xd8\xffcached-image')
    feature.fetch = Mock(return_value=fm.parse_movies(payload()))
    assert client.get('/api/public/featured-movies').status_code == 200
    assert client.get('/api/public/featured-movies').json()['movies'] == []
    assert client.get('/api/admin/featured-movies').status_code == 401
    assert client.post('/api/admin/featured-movies', json={'enabled': True, 'token': TOKEN}).status_code == 401
    assert client.post('/api/admin/featured-movies/refresh', json={}).status_code == 401
    feature.fetch.assert_not_called()
    assert client.post('/api/auth/setup', json={'password': 'ISOLATED-featured-password'}).status_code == 200
    assert client.post('/api/admin/featured-movies/refresh', json={}).status_code == 409
    assert client.post('/api/admin/featured-movies', json={'enabled': True, 'token': TOKEN},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.post('/api/admin/featured-movies', json={'enabled': True, 'token': TOKEN})
    assert response.status_code == 200 and TOKEN not in response.text
    assert client.get('/api/admin/featured-movies').json()['token_configured'] is True
    feature.fetch.assert_not_called()
    assert client.post('/api/admin/featured-movies/refresh', json={'target': 'https://evil.example'}).status_code == 400
    assert client.post('/api/admin/featured-movies/refresh', json={},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/admin/featured-movies/refresh', json={}).status_code == 200
    feature.fetch.assert_called_once_with(TOKEN)
    feature.fetch.reset_mock()
    public = client.get('/api/public/featured-movies?url=https://evil.example')
    assert public.status_code == 200 and len(public.json()['movies']) == 6
    assert TOKEN not in public.text and 'evil.example' not in public.text
    assert public.headers['Cache-Control'] == 'no-store'
    feature.fetch.assert_not_called()


def test_refresh_route_errors_are_sanitized_and_preserve_previous_cache(featured_client):
    client, app = featured_client
    feature = app.state.featured_movies
    feature.fetch_art = Mock(return_value=b'\xff\xd8\xffcached-image')
    enable(feature)
    feature.fetch = Mock(return_value=fm.parse_movies(payload()))
    feature.refresh()
    before = feature.cache_path.read_bytes()
    client.post('/api/auth/setup', json={'password': 'ISOLATED-featured-password'})
    feature.fetch.side_effect = OSError('secret ' + TOKEN)
    response = client.post('/api/admin/featured-movies/refresh', json={})
    assert response.status_code == 502 and TOKEN not in response.text
    assert feature.cache_path.read_bytes() == before
    assert len(client.get('/api/public/featured-movies').json()['movies']) == 6


def test_invalid_root_payload_never_echoes_a_credential(featured_client):
    client, _ = featured_client
    client.post('/api/auth/setup', json={'password': 'ISOLATED-featured-password'})
    for value in (TOKEN, [TOKEN], 123):
        response = client.post('/api/admin/featured-movies', json=value)
        assert response.status_code == 400 and TOKEN not in response.text


def test_refresh_only_accepts_an_empty_object_without_echoing_bad_payload(featured_client):
    client, app = featured_client
    client.post('/api/auth/setup', json={'password': 'ISOLATED-featured-password'})
    with patch.object(app.state.featured_movies, 'refresh') as refresh:
        for value in ([], [TOKEN], False):
            response = client.post('/api/admin/featured-movies/refresh', json=value)
            assert response.status_code == 400 and TOKEN not in response.text
        refresh.assert_not_called()


def test_image_failure_retains_entire_previous_batch_and_public_never_downloads(featured):
    service, _, fetch, clock = featured
    enable(service)
    service.refresh()
    before = service.cache_path.read_bytes()
    old = service.public()
    fetch.return_value = fm.parse_movies(payload(3))
    service.fetch_art = Mock(side_effect=OSError('private upstream failure'))
    clock[0] += 86400
    with pytest.raises(fm.FeaturedMoviesError):
        service.refresh()
    assert service.cache_path.read_bytes() == before
    service.fetch_art.reset_mock()
    result = service.public()
    assert result['movies'] == old['movies']
    for row in result['movies']:
        assert row['poster_url'].startswith(fm.IMAGE_PREFIX)
        body, mime = service.image(row['poster_url'].removeprefix(fm.IMAGE_PREFIX))
        assert body.startswith(b'\xff\xd8\xff') and mime == 'image/jpeg'
    service.fetch_art.assert_not_called()
    for name in ('../secrets/token', 'https://evil.example/a.jpg', 'a'*64+'.jpg'):
        with pytest.raises(fm.FeaturedMoviesError):
            service.image(name)


def test_public_image_route_serves_only_cached_bytes_with_immutable_url(featured_client):
    client, app = featured_client
    feature = app.state.featured_movies
    feature.fetch = Mock(return_value=fm.parse_movies(payload(3)))
    feature.fetch_art = Mock(return_value=b'\xff\xd8\xffcached-image')
    enable(feature); feature.refresh()
    feature.fetch_art.reset_mock()
    url=client.get('/api/public/featured-movies').json()['movies'][0]['poster_url']
    response=client.get(url)
    assert response.status_code == 200 and response.content == b'\xff\xd8\xffcached-image'
    assert response.headers['Content-Type'] == 'image/jpeg'
    assert response.headers['Cache-Control'] == 'public, max-age=604800, immutable'
    assert client.get(fm.IMAGE_PREFIX+'a'*64+'.jpg').status_code == 404
    feature.fetch_art.assert_not_called()


@pytest.mark.parametrize('url', ['http://image.tmdb.org/t/p/w500/x.jpg', 'https://evil.example/x.jpg', 'https://image.tmdb.org/t/p/w500/../x.jpg', 'https://image.tmdb.org/t/p/w500/x.jpg?key=secret'])
def test_image_downloader_rejects_untrusted_targets_before_network(url):
    with patch.object(fm.http.client, 'HTTPSConnection') as connection:
        with pytest.raises(fm.FeaturedMoviesError):
            fm.fetch_image(url)
        connection.assert_not_called()
