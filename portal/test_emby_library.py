"""Isolated Emby directory checks. No production URLs, keys, libraries or playback."""
import json
import ssl
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import emby_library as module
from emby_library import EmbyLibrary, EmbyLibraryError, install_routes
from secrets_store import MemorySecrets, SecretStoreError


TOKEN = 'TEST_ONLY_EMBY_TOKEN_123456789'
BASE = 'https://private-media.example/emby'
JPEG = b'\xff\xd8\xff' + b'test-only-jpeg'


def test_library_cover_refresh_uses_current_image_tag_after_cache_expiry(service):
    library, _, clock, upstream = service
    enable(service, ['root-a'])
    version = ['v1']
    def fetch(base, token, path, params=None, **kwargs):
        if path == '/Items' and 'Ids' in params:
            return {'Items': [{'Id': params['Ids'], 'ImageTags': {'Primary': version[0]}}]}
        if '/Images/Primary' in path:
            return JPEG + params['Tag'].encode(), 'image/jpeg'
        return upstream(base, token, path, params, **kwargs)
    library.fetch = fetch
    opaque = library_id(library)
    assert library.cover(opaque)[0] == JPEG + b'v1'
    version[0] = 'v2'
    assert library.cover(opaque)[0] == JPEG + b'v1'
    clock.value += 301
    assert library.cover(opaque)[0] == JPEG + b'v2'
    version[0] = 'invalid?token=secret'
    clock.value += 301
    with pytest.raises(EmbyLibraryError):
        library.cover(opaque)


class Clock:
    def __init__(self):
        self.value = 1800000000

    def __call__(self):
        return self.value


def item(number, kind='Movie', **extra):
    return {'Id': str(number), 'Name': f'影片 {number}', 'Type': kind, 'ProductionYear': 2026,
            'Overview': '<p>一段故事</p>', 'Genres': ['剧情'], 'Path': '/private/media/file.mkv',
            'MediaSources': [{'Path': 'https://private-stream.example/file?token=private'}],
            'UserData': {'Played': True}, 'ServerId': 'server-private', **extra}


class FakeEmby:
    def __init__(self):
        self.calls = []
        self.rows = {'root-a': [item(number) for number in range(1, 31)],
                     'root-b': [item(number, 'Series') for number in range(31, 56)]}
        self.failing = False
        self.image = (JPEG, 'image/jpeg')

    def __call__(self, base, token, path, params=None, **kwargs):
        self.calls.append((base, token, path, dict(params or {}), kwargs))
        if self.failing:
            raise RuntimeError(f'PRIVATE ERROR {base} {token}')
        if path == '/Library/VirtualFolders':
            return [{'ItemId': key, 'Name': f'媒体库 {index}', 'Locations': ['/private/storage'], 'LibraryOptions': {'secret': TOKEN}}
                    for index, key in enumerate(self.rows)]
        if path == '/Items':
            if 'Ids' in params:
                return {'Items': [{'Id': params['Ids'], 'ImageTags': {'Primary': 'test_image_version'}}]}
            assert params['ParentId'] in self.rows
            assert params['EnableUserData'] == 'false'
            assert params['Recursive'] == 'true'
            assert params['Fields'] == 'Overview,Genres,ProductionYear'
            assert (params['SortBy'], params['SortOrder']) in {('SortName', 'Ascending'), ('DateCreated', 'Descending'), ('ProductionYear,SortName', 'Descending')}
            rows = [row for row in self.rows[params['ParentId']] if row.get('Type') in params['IncludeItemTypes'].split(',')]
            if params.get('SearchTerm'):
                rows = [row for row in rows if params['SearchTerm'].casefold() in row['Name'].casefold()]
            start, limit = params['StartIndex'], params['Limit']
            return {'Items': rows[start:start + limit], 'TotalRecordCount': len(rows)}
        if '/Images/Primary' in path:
            assert kwargs.get('image') is True
            return self.image
        raise AssertionError(f'Unexpected internal endpoint: {path}')


@pytest.fixture
def service(tmp_path):
    vault, clock, upstream = MemorySecrets(), Clock(), FakeEmby()
    library = EmbyLibrary(tmp_path, vault, now=clock, fetch=upstream)
    yield library, vault, clock, upstream
    if library._search_pool is not None:
        library._search_pool.shutdown(wait=True, cancel_futures=True)


def enable(service, libraries=None):
    library, _, _, upstream = service
    result = library.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': True,
                                  'library_ids': libraries if libraries is not None else ['root-a', 'root-b']})
    upstream.calls.clear()
    return result


def library_id(service, raw_id=None):
    config = module._read_config(service.config_path)
    raw_id = raw_id or (config['library_ids'][0] if config['library_ids'] else '')
    return service._library_opaque(config, raw_id) if raw_id else ''


def catalog(service, *args, root=None, **kwargs):
    return service.public(*args, library_id=library_id(service, root), **kwargs)


def test_disabled_default_never_contacts_upstream(service):
    library, _, _, upstream = service
    assert library.config() == {'base_url': module.DEFAULT_URL, 'enabled': False, 'library_ids': [], 'token_configured': False}
    assert catalog(library) == {'configured': False, 'items': [], 'page': 1, 'has_more': False, 'updated_at': None, 'stale': False}
    assert not upstream.calls


def test_two_stage_configuration_keeps_key_out_of_config_and_response(service):
    library, vault, _, upstream = service
    result = library.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': False})
    assert result['token_configured'] and not result['enabled']
    assert vault.get('emby-library') == TOKEN
    assert TOKEN not in library.config_path.read_text()
    assert set(json.loads(library.config_path.read_text())) == {'base_url', 'enabled', 'library_ids', 'generation', 'token_hash'}
    assert library.config_path.stat().st_mode & 0o777 == 0o600
    assert not upstream.calls
    folders = library.libraries()
    assert set(folders) == {'libraries'}
    assert all(set(row) == {'id', 'name'} for row in folders['libraries'])
    assert 'private/storage' not in json.dumps(folders) and TOKEN not in json.dumps(folders)
    enabled = library.save_config({'enabled': True, 'library_ids': ['root-a']})
    assert enabled['enabled'] and enabled['library_ids'] == ['root-a']
    assert TOKEN not in json.dumps(enabled)


@pytest.mark.parametrize('body', [
    {'enabled': True}, {'enabled': True, 'token': TOKEN, 'library_ids': []},
    {'enabled': True, 'token': TOKEN, 'library_ids': ['not-a-root']},
    {'enabled': True, 'token': TOKEN, 'library_ids': ['root-a', 'root-a']},
    {'enabled': 'true'}, {'library_ids': ['../private']}, {'library_ids': ['root-a'] * 9},
    {'clear_token': True, 'token': TOKEN}, {'token_configured': True}, {'token': 'bad\r\nheader'},
])
def test_invalid_or_unscoped_configuration_fails_without_storing_key(service, body):
    library, vault, _, _ = service
    with pytest.raises(EmbyLibraryError) as caught:
        library.save_config(body)
    assert caught.value.status == 400
    assert not library.config()['enabled']
    assert not vault.get('emby-library')
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize('value', [
    'http://private.example', 'https://user:secret@private.example', 'https://private.example?q=secret',
    'https://private.example#secret', 'https://private.example?', 'https://private.example/#',
    'https://private.example/../admin', 'https://private.example/%2fadmin', 'https://private.example\\evil',
    'https://private.example/ a', 'https://private.example:\n443', 'https://private.example:99999',
    'https:///private.example', 'https://private..example', 'file:///private/secret'
])
def test_rejects_unsafe_or_non_https_origins_without_echoing_input(value):
    with pytest.raises(EmbyLibraryError) as caught:
        module.normalize_base_url(value)
    assert caught.value.status == 400
    assert value not in str(caught.value)


def test_origin_change_disables_and_clears_credentials_and_selected_libraries(service):
    library, vault, _, upstream = service
    enable(service)
    catalog(library)
    result = library.save_config({'base_url': 'https://new-media.example'})
    assert result == {'base_url': 'https://new-media.example', 'enabled': False, 'library_ids': [], 'token_configured': False}
    assert vault.get('emby-library') is None
    assert not library._pages and not library._items and not library._images
    assert all(call[0] == BASE for call in upstream.calls), 'old key must never be sent to a new origin'


def test_initial_config_write_failure_preserves_the_credential(service, monkeypatch):
    library, vault, _, _ = service
    enable(service)
    original = library.config_path.read_bytes()
    monkeypatch.setattr(module, '_atomic_config', lambda *args: (_ for _ in ()).throw(OSError('PRIVATE ERROR')))
    with pytest.raises(SecretStoreError) as caught:
        library.save_config({'token': 'ANOTHER_TEST_ONLY_TOKEN_123456789', 'enabled': False})
    assert vault.get('emby-library') == TOKEN
    assert library.config_path.read_bytes() == original
    assert 'PRIVATE ERROR' not in str(caught.value)


def test_final_config_failure_stays_disabled_and_never_mixes_origin_and_token(service, monkeypatch):
    library, vault, clock, upstream = service
    enable(service)
    catalog(library)
    atomic = module._atomic_config
    writes = []
    def fail_final(path, value):
        writes.append(value)
        if len(writes) == 2:
            raise OSError('PRIVATE ERROR')
        atomic(path, value)
    monkeypatch.setattr(module, '_atomic_config', fail_final)
    new_token = 'ANOTHER_TEST_ONLY_TOKEN_123456789'
    with pytest.raises(SecretStoreError):
        library.save_config({'base_url': 'https://new-media.example', 'token': new_token,
                             'enabled': True, 'library_ids': ['root-a']})
    assert library.config()['enabled'] is False
    assert library.config()['base_url'] == 'https://new-media.example'
    assert vault.get('emby-library') == new_token
    before = len(upstream.calls)
    assert catalog(library)['configured'] is False
    assert len(upstream.calls) == before
    restarted = EmbyLibrary(library.config_path.parent, vault, now=clock, fetch=upstream)
    assert catalog(restarted)['configured'] is False
    restarted.libraries()
    assert upstream.calls[-1][0:2] == ('https://new-media.example', new_token)
    assert not library._items and not library._images


def test_vault_failure_or_external_key_mismatch_cannot_send_old_key_to_new_origin(service, monkeypatch):
    library, vault, clock, upstream = service
    enable(service)
    original_set = vault.set
    monkeypatch.setattr(vault, 'set', lambda *args: (_ for _ in ()).throw(SecretStoreError('save failed')))
    with pytest.raises(SecretStoreError):
        library.save_config({'base_url': 'https://new-media.example', 'token': 'ANOTHER_TEST_ONLY_TOKEN_123456789'})
    assert vault.get('emby-library') == TOKEN
    assert not library.config()['token_configured'] and not library.config()['enabled']
    before = len(upstream.calls)
    with pytest.raises(EmbyLibraryError):
        library.libraries()
    restarted = EmbyLibrary(library.config_path.parent, vault, now=clock, fetch=upstream)
    with pytest.raises(EmbyLibraryError):
        restarted.libraries()
    assert len(upstream.calls) == before
    monkeypatch.setattr(vault, 'set', original_set)


def test_paging_is_deterministic_within_only_the_requested_library(service):
    library, _, _, upstream = service
    enable(service)
    pages = [catalog(library, page=number) for number in (1, 2, 3)]
    assert [len(page['items']) for page in pages] == [24, 6, 0]
    assert [page['has_more'] for page in pages] == [True, False, False]
    assert [row['title'] for page in pages for row in page['items']] == [f'影片 {number}' for number in range(1, 31)]
    identifiers = [row['id'] for page in pages for row in page['items']]
    assert len(set(identifiers)) == 30
    assert all(module.OPAQUE_PATTERN.fullmatch(value) for value in identifiers)
    assert all(call[2] == '/Items' and call[3]['ParentId'] == 'root-a' for call in upstream.calls)
    assert [call[3]['StartIndex'] for call in upstream.calls] == [0, 24, 48]
    assert all(call[3]['Limit'] == 25 for call in upstream.calls)


def test_more_than_eight_explicit_libraries_can_be_saved_without_changing_selection_order(service):
    library, vault, clock, upstream = service
    selected = [f'root-{index:02}' for index in reversed(range(12))]
    upstream.rows = {key: [item(index + 1)] for index, key in enumerate(selected)}
    result = enable(service, selected)
    assert result['enabled'] and result['library_ids'] == selected
    assert library.config()['library_ids'] == selected
    restarted = EmbyLibrary(library.config_path.parent, vault, now=clock, fetch=upstream)
    assert restarted.config()['library_ids'] == selected
    directory = restarted.public_libraries()
    assert [row['id'] for row in directory['libraries']] == [library_id(restarted, key) for key in selected]
    assert [row['name'] for row in directory['libraries']] == [f'媒体库 {index}' for index in range(12)]
    assert [call[2] for call in upstream.calls] == ['/Library/VirtualFolders']


def test_maximum_library_selection_with_long_ids_survives_config_reload(service):
    library, vault, clock, upstream = service
    selected = [f'{index:03}' + 'x' * 125 for index in range(128)]
    upstream.rows = {key: [] for key in selected}
    assert enable(service, selected)['library_ids'] == selected
    assert 8192 < library.config_path.stat().st_size < module.MAX_CONFIG_BYTES
    restarted = EmbyLibrary(library.config_path.parent, vault, now=clock, fetch=upstream)
    assert restarted.config()['enabled'] is True
    assert restarted.config()['token_configured'] is True
    assert restarted.config()['library_ids'] == selected


@pytest.mark.parametrize('selected', [[f'root-{index:03}' for index in range(129)], ['root-a', 'root-b', 'root-a']])
def test_over_128_or_duplicate_selections_are_rejected_without_mutating_saved_configuration(service, selected):
    library, vault, _, upstream = service
    enable(service)
    previous = library.config_path.read_bytes()
    with pytest.raises(EmbyLibraryError) as caught:
        library.save_config({'library_ids': selected})
    assert caught.value.status == 400
    assert '128' in str(caught.value)
    assert library.config_path.read_bytes() == previous
    assert vault.get(module.SECRET_NAME) == TOKEN
    assert not upstream.calls


def test_more_than_eight_libraries_still_require_each_selected_id_to_be_an_actual_root(service):
    library, _, _, upstream = service
    selected = [f'root-{index:02}' for index in range(12)]
    upstream.rows = {key: [] for key in selected}
    enable(service, selected)
    previous = library.config_path.read_bytes()
    with pytest.raises(EmbyLibraryError) as caught:
        library.save_config({'library_ids': selected + ['not-an-actual-root']})
    assert caught.value.status == 400
    assert library.config_path.read_bytes() == previous
    assert [call[2] for call in upstream.calls] == ['/Library/VirtualFolders']


def test_ninth_selected_library_can_be_paged_without_touching_other_libraries(service):
    library, _, _, upstream = service
    selected = [f'root-{index}' for index in range(1, 10)]
    upstream.rows = {key: [item(index * 100 + number) for number in range(1, 31)]
                     for index, key in enumerate(selected)}
    enable(service, selected)
    first = catalog(library, root='root-9', page=1)
    second = catalog(library, root='root-9', page=2)
    assert [row['title'] for row in first['items']] == [f'影片 {index}' for index in range(801, 825)]
    assert [row['title'] for row in second['items']] == [f'影片 {index}' for index in range(825, 831)]
    assert first['has_more'] is True and second['has_more'] is False
    assert [call[3]['ParentId'] for call in upstream.calls] == ['root-9', 'root-9']
    assert not ({row['id'] for row in first['items']} & {row['id'] for row in second['items']})


def test_128_selected_libraries_still_cost_one_item_request_for_one_library(service):
    library, _, _, upstream = service
    selected = [f'root-{index:03}' for index in range(128)]
    upstream.rows = {key: [item(index * 30 + number) for number in range(1, 31)]
                     for index, key in enumerate(selected)}
    enable(service, selected)
    assert len(catalog(library, root=selected[-1])['items']) == 24
    assert len(upstream.calls) == 1
    assert upstream.calls[0][3]['ParentId'] == selected[-1]
    assert library.config()['library_ids'] == selected


def test_selected_library_search_and_type_filters_are_sent_only_as_fixed_query_fields(service):
    library, _, _, upstream = service
    enable(service, ['root-b'])
    result = catalog(library, query='影片 4', media_type='tv')
    assert [row['title'] for row in result['items']] == [f'影片 {number}' for number in range(40, 50)]
    assert all(row['type'] == 'tv' for row in result['items'])
    assert upstream.calls[-1][3]['IncludeItemTypes'] == 'Series'
    assert upstream.calls[-1][3]['SearchTerm'] == '影片 4'
    assert all(call[3].get('ParentId') == 'root-b' for call in upstream.calls)
    assert catalog(library, media_type='movie')['items'] == []


def test_public_dto_excludes_all_private_fields_and_redacts_configured_origin_and_token(service):
    library, _, _, upstream = service
    upstream.rows['root-a'] = [item(1, Name='公开电影', Overview=f'<p>故事 {TOKEN} {BASE}</p>', Genres=['剧情', TOKEN]),
                               item(2, ProductionYear=True), item(3, Name='')]
    enable(service, ['root-a'])
    result = catalog(library)
    assert set(result) == {'configured', 'items', 'page', 'has_more', 'updated_at', 'stale'}
    assert len(result['items']) == 2
    assert all(set(row) == {'id', 'title', 'type', 'year', 'overview', 'genres', 'poster_url'} for row in result['items'])
    serialized = json.dumps(result)
    for forbidden in (TOKEN, 'private-media.example', 'private-stream.example', '/private/', 'MediaSources', 'Path', 'UserData', 'ServerId', 'root-a'):
        assert forbidden not in serialized
    assert result['items'][1]['year'] is None
    assert all(row['poster_url'] == '/api/public/library/poster/' + row['id'] for row in result['items'])
    assert '<p>' not in result['items'][0]['overview']


@pytest.mark.parametrize('query,kind,page', [('x' * 121, 'all', 1), ('x\x00', 'all', 1), ('', 'private', 1), ('', 'all', 0), ('', 'all', 1001), ('', 'all', True)])
def test_invalid_public_queries_do_not_fetch(service, query, kind, page):
    library, _, _, upstream = service
    enable(service)
    with pytest.raises(EmbyLibraryError) as caught:
        catalog(library, query, kind, page)
    assert caught.value.status == 400
    assert not upstream.calls


def test_pages_cache_for_five_minutes_stale_fallback_is_bounded_and_errors_sanitized(service):
    library, _, clock, upstream = service
    enable(service)
    first = catalog(library)
    count = len(upstream.calls)
    first['items'][0]['title'] = 'mutated by caller'
    assert catalog(library)['items'][0]['title'] != 'mutated by caller'
    assert len(upstream.calls) == count
    clock.value += 301
    upstream.failing = True
    old = catalog(library)
    assert old['stale'] and old['updated_at'] and old['items']
    clock.value += 1800
    with pytest.raises(EmbyLibraryError) as caught:
        catalog(library)
    assert TOKEN not in str(caught.value) and BASE not in str(caught.value)
    assert caught.value.status == 503


def test_page_and_item_caches_have_hard_limits(service, monkeypatch):
    library, _, _, _ = service
    enable(service)
    monkeypatch.setattr(module, 'MAX_PAGE_KEYS', 3)
    monkeypatch.setattr(module, 'MAX_AUTHORIZED_ITEMS', 4)
    for query in ('影片 1', '影片 2', '影片 3', '影片 4', '影片 5'):
        catalog(library, query)
    assert len(library._pages) == 3
    assert len(library._items) == 4


def test_only_returned_items_can_fetch_posters_and_cached_images_are_same_origin(service):
    library, _, _, upstream = service
    enable(service, ['root-a'])
    for untrusted in ('1', 'root-a', '../private', 'a' * 64):
        with pytest.raises(EmbyLibraryError) as caught:
            library.poster(untrusted)
        assert caught.value.status == 404
    assert not upstream.calls
    page = catalog(library)
    opaque = page['items'][0]['id']
    before = len(upstream.calls)
    assert library.poster(opaque) == (JPEG, 'image/jpeg')
    assert library.poster(opaque) == (JPEG, 'image/jpeg')
    assert len(upstream.calls) == before + 1
    assert upstream.calls[-1][2] == '/Items/1/Images/Primary'
    assert upstream.calls[-1][3] == {'MaxWidth': 500, 'Quality': 85}
    # The 25th item was seen only as pagination look-ahead, never publicly returned.
    private_lookahead = library._opaque(module._read_config(library.config_path), '25')
    with pytest.raises(EmbyLibraryError):
        library.poster(private_lookahead)


def test_configuration_generation_revokes_all_old_poster_authorizations(service):
    library, _, _, upstream = service
    enable(service)
    old = catalog(library)['items'][0]['id']
    library.poster(old)
    library.save_config({'library_ids': ['root-b']})
    before = len(upstream.calls)
    with pytest.raises(EmbyLibraryError) as caught:
        library.poster(old)
    assert caught.value.status == 404 and len(upstream.calls) == before
    assert not library._images
    assert catalog(library)['items'][0]['id'] != old
    library.save_config({'clear_token': True})
    assert catalog(library)['configured'] is False


@pytest.mark.parametrize('body,mime', [(b'<svg>secret</svg>', 'image/svg+xml'), (b'<html>secret</html>', 'image/jpeg'),
                                     (b'\xff\xd8\xff' + b'x' * module.MAX_IMAGE_BYTES, 'image/jpeg'),
                                     (b'', 'image/png')])
def test_invalid_images_are_rejected_and_not_cached(service, body, mime):
    library, _, _, upstream = service
    enable(service)
    opaque = catalog(library)['items'][0]['id']
    upstream.image = body, mime
    with pytest.raises(EmbyLibraryError) as caught:
        library.poster(opaque)
    assert caught.value.status == 502
    assert not library._images
    assert 'secret' not in str(caught.value)


def test_image_ttl_item_expiry_and_byte_bound_are_enforced(service, monkeypatch):
    library, _, clock, upstream = service
    enable(service)
    ids = [row['id'] for row in catalog(library)['items'][:3]]
    monkeypatch.setattr(module, 'MAX_IMAGE_CACHE_BYTES', len(JPEG) * 2)
    for opaque in ids:
        library.poster(opaque)
    assert len(library._images) == 2 and library._image_bytes == len(JPEG) * 2
    before = len(upstream.calls)
    clock.value += 301
    library.poster(ids[-1])
    assert len(upstream.calls) == before + 1
    clock.value += 1800
    with pytest.raises(EmbyLibraryError) as caught:
        library.poster(ids[-1])
    assert caught.value.status == 404


def test_late_page_response_cannot_publish_items_after_configuration_changes(service):
    library, _, _, upstream = service
    enable(service)
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        if args[2] == '/Items':
            entered.set(); assert release.wait(3)
        return upstream(*args, **kwargs)
    library.fetch = slow
    errors = []
    def browse():
        try:
            catalog(library)
        except EmbyLibraryError as error:
            errors.append(error)
    worker = threading.Thread(target=browse)
    worker.start(); assert entered.wait(3)
    library.save_config({'enabled': False})
    release.set(); worker.join(3)
    assert not worker.is_alive()
    assert errors and errors[0].status == 409
    assert not library._items and not library._pages


def test_late_poster_response_cannot_repopulate_cache_after_configuration_changes(service):
    library, _, _, upstream = service
    enable(service)
    opaque = catalog(library)['items'][0]['id']
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        if '/Images/Primary' in args[2]:
            entered.set(); assert release.wait(3)
        return upstream(*args, **kwargs)
    library.fetch = slow
    errors = []
    def read():
        try:
            library.poster(opaque)
        except EmbyLibraryError as error:
            errors.append(error)
    worker = threading.Thread(target=read)
    worker.start(); assert entered.wait(3)
    library.save_config({'enabled': False})
    release.set(); worker.join(3)
    assert not worker.is_alive()
    assert errors and errors[0].status == 404
    assert not library._images and not library._items and library._image_bytes == 0


def test_library_request_has_one_total_deadline(service, monkeypatch):
    library, _, _, upstream = service
    enable(service)
    upstream.rows['root-a'] = []
    monotonic = [0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: monotonic[0])
    deadlines = []
    def slow(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        monotonic[0] += 5
        return upstream(*args, **kwargs)
    library.fetch = slow
    catalog(library)
    assert deadlines == [12]
    library._pages.clear()
    monotonic[0] = 0
    deadlines.clear()
    def expired(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        monotonic[0] += 13
        return upstream(*args, **kwargs)
    library.fetch = expired
    with pytest.raises(EmbyLibraryError):
        catalog(library)
    assert deadlines == [12], 'the whole page request has one total timeout'


def test_queue_wait_is_included_in_outbound_time_budget(service, monkeypatch):
    library, _, _, upstream = service
    enable(service)
    monotonic, released = [0], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: monotonic[0])
    class WaitedLock:
        def acquire(self, timeout):
            assert timeout == 12
            monotonic[0] += 13
            return True
        def release(self):
            released.append(True)
    library._upstream_lock = WaitedLock()
    with pytest.raises(EmbyLibraryError):
        catalog(library)
    assert not upstream.calls and released == [True]


def test_fresh_cache_does_not_wait_for_an_unrelated_upstream_request(service):
    library, _, _, _ = service
    enable(service)
    expected = catalog(library)
    class BlockedLock:
        def acquire(self, timeout):
            raise AssertionError('cache hit must not wait for upstream')
    library._upstream_lock = BlockedLock()
    assert catalog(library) == expected


def test_malformed_upstream_rows_cannot_authorize_arbitrary_images(service):
    library, _, _, _ = service
    enable(service, ['root-a'])
    library.fetch = lambda *args, **kwargs: {'Items': [item('../secret'), item(2, 'Episode'), item(3)], 'TotalRecordCount': 3}
    result = catalog(library)
    assert [row['title'] for row in result['items']] == ['影片 3']
    assert len(library._items) == 1


class FakeHTTPResponse:
    def __init__(self, body=b'{"Items":[]}', status=200, content_type='application/json', headers=None):
        self.body, self.status, self.reads = body, status, 0
        self.headers = {'Content-Type': content_type, **(headers or {})}

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read1(self, size):
        self.reads += 1
        result, self.body = self.body[:size], self.body[size:]
        return result


def http_mock(monkeypatch, reply, *, detach_socket=False):
    events = {'requests': [], 'timeouts': [], 'closed': False}
    class Socket:
        def settimeout(self, timeout):
            events['timeouts'].append(timeout)
    class Connection:
        sock = Socket()
        def __init__(self, host, **options):
            events['host'], events['options'] = host, options
        def request(self, method, target, headers):
            events['requests'].append((method, target, headers))
        def getresponse(self):
            if detach_socket:
                self.sock = None
            return reply
        def close(self):
            events['closed'] = True
    monkeypatch.setattr(module.http.client, 'HTTPSConnection', Connection)
    return events


def test_transport_uses_verified_https_header_token_and_encoded_fixed_path(monkeypatch):
    events = http_mock(monkeypatch, FakeHTTPResponse())
    assert module.fetch_upstream(BASE, TOKEN, '/Items', {'ParentId': 'root-a', 'SearchTerm': '词 &/?'}) == {'Items': []}
    assert events['host'] == 'private-media.example'
    assert events['options']['port'] == 443
    assert events['options']['timeout'] <= 12
    assert events['options']['context'].verify_mode == ssl.CERT_REQUIRED
    assert events['options']['context'].check_hostname
    method, target, headers = events['requests'][0]
    assert method == 'GET' and target.startswith('/emby/Items?')
    assert parse_qs(urlsplit(target).query)['SearchTerm'] == ['词 &/?']
    assert TOKEN not in target and headers['X-Emby-Token'] == TOKEN
    assert 'Cookie' not in headers and 'Authorization' not in headers
    assert events['closed']


@pytest.mark.parametrize('reply', [
    FakeHTTPResponse(status=302, headers={'Location': 'https://attacker.example/?secret=x'}),
    FakeHTTPResponse(status=401), FakeHTTPResponse(content_type='text/html'),
    FakeHTTPResponse(headers={'Content-Encoding': 'gzip'}),
    FakeHTTPResponse(headers={'Content-Length': str(module.MAX_JSON_BYTES + 1)}),
    FakeHTTPResponse(body=b'x' * (module.MAX_JSON_BYTES + 1)),
    FakeHTTPResponse(body=b'invalid json'),
])
def test_transport_refuses_redirects_bad_types_encoding_and_oversized_bodies(monkeypatch, reply):
    events = http_mock(monkeypatch, reply)
    with pytest.raises(EmbyLibraryError) as caught:
        module.fetch_upstream(BASE, TOKEN, '/Items')
    assert len(events['requests']) == 1
    assert events['closed']
    assert 'attacker' not in str(caught.value) and TOKEN not in str(caught.value)


@pytest.mark.parametrize('body,mime', [(JPEG, 'image/jpeg'), (b'\x89PNG\r\n\x1a\nbody', 'image/png'), (b'RIFF0000WEBPbody', 'image/webp')])
def test_transport_allows_only_bounded_valid_raster_image_types(monkeypatch, body, mime):
    http_mock(monkeypatch, FakeHTTPResponse(body, content_type=mime))
    assert module.fetch_upstream(BASE, TOKEN, '/Items/123/Images/Primary', image=True) == (body, mime)


def test_transport_deadline_is_checked_during_stream_reads(monkeypatch):
    clock = [0]
    class SlowResponse(FakeHTTPResponse):
        def read1(self, size):
            clock[0] += 20
            return super().read1(size)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    events = http_mock(monkeypatch, SlowResponse())
    with pytest.raises(EmbyLibraryError):
        module.fetch_upstream(BASE, TOKEN, '/Items', deadline=12)
    assert events['closed']


def test_connection_close_response_keeps_applying_the_remaining_socket_timeout(monkeypatch):
    events = http_mock(monkeypatch, FakeHTTPResponse(), detach_socket=True)
    module.fetch_upstream(BASE, TOKEN, '/Items')
    assert len(events['timeouts']) >= 3
    assert all(0 < timeout <= 12 for timeout in events['timeouts'])


@pytest.fixture
def route_client(tmp_path):
    app, vault, upstream, buckets = FastAPI(), MemorySecrets(), FakeEmby(), []
    def admin(request):
        if request.headers.get('x-test-admin') != 'yes':
            raise HTTPException(401, 'Test login required')
    def rate(request, bucket, **options):
        buckets.append((bucket, options))
    service = install_routes(app, vault, admin, rate, tmp_path)
    service.fetch = upstream
    with TestClient(app, base_url='https://portal.example') as client:
        yield client, service, upstream, buckets
    if service._search_pool is not None:
        service._search_pool.shutdown(wait=True, cancel_futures=True)


def test_routes_require_admin_and_refuse_cross_origin_configuration(route_client):
    client, service, upstream, buckets = route_client
    for path in ('/api/admin/emby-library', '/api/admin/emby-library/libraries'):
        assert client.get(path).status_code == 401
    assert client.post('/api/admin/emby-library', json={'enabled': False}).status_code == 401
    for headers in ({'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'cross-site'}):
        denied = client.post('/api/admin/emby-library', json={'token': TOKEN}, headers={'x-test-admin': 'yes', **headers})
        assert denied.status_code == 403
    assert not service.config()['token_configured'] and not upstream.calls
    accepted = client.post('/api/admin/emby-library', json={'base_url': BASE, 'token': TOKEN, 'enabled': False},
                           headers={'x-test-admin': 'yes', 'Origin': 'https://portal.example'})
    assert accepted.status_code == 200 and TOKEN not in accepted.text
    inventory = client.get('/api/admin/emby-library/libraries', headers={'x-test-admin': 'yes'})
    assert inventory.status_code == 200 and set(inventory.json()) == {'libraries'}
    assert 'Locations' not in inventory.text
    assert any(bucket == 'emby_library_inventory' for bucket, _ in buckets)


@pytest.mark.parametrize('body', [[TOKEN], TOKEN, 123, {'token': [TOKEN]}])
def test_invalid_config_body_never_reflects_submitted_credentials(route_client, body):
    client, _, _, _ = route_client
    result = client.post('/api/admin/emby-library', json=body, headers={'x-test-admin': 'yes'})
    assert result.status_code == 400
    assert TOKEN not in result.text


def test_public_routes_only_serve_scoped_dtos_and_authorized_poster_bytes(route_client):
    client, service, upstream, buckets = route_client
    service.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': True, 'library_ids': ['root-a']})
    upstream.calls.clear()
    assert client.get('/api/public/library?url=https://attacker.example&libraryId=root-b').status_code == 400
    page = client.get('/api/public/library', params={'library': library_id(service), 'url': 'https://attacker.example', 'libraryId': 'root-b'})
    assert page.status_code == 200 and page.headers['Cache-Control'] == 'no-store'
    assert all(call[3].get('ParentId') == 'root-a' for call in upstream.calls)
    assert TOKEN not in page.text and BASE not in page.text
    poster = client.get(page.json()['items'][0]['poster_url'])
    assert poster.status_code == 200 and poster.content == JPEG
    assert poster.headers['Content-Type'] == 'image/jpeg'
    assert poster.headers['X-Content-Type-Options'] == 'nosniff'
    assert poster.headers['Cache-Control'] == 'no-store'
    assert 'Location' not in poster.headers
    assert client.get('/api/public/library/poster/1').status_code == 404
    assert client.post('/api/public/library', json={}).status_code == 405
    assert client.get('/api/public/library?type=private').status_code == 400
    assert client.get('/api/public/library?page=0').status_code == 400
    assert client.get('/api/public/library?page=no').status_code == 422
    assert any(bucket == 'public_library_poster' for bucket, _ in buckets)


def test_public_directory_is_disabled_without_contacting_emby(service):
    library, _, _, upstream = service
    assert library.public_libraries() == {'configured': False, 'libraries': [], 'updated_at': None, 'stale': False}
    with pytest.raises(EmbyLibraryError) as caught:
        library.cover('a' * 64)
    assert caught.value.status == 404
    assert not upstream.calls


def test_public_directory_contains_only_selected_roots_in_configured_order(service):
    library, _, _, upstream = service
    upstream.rows['root-private'] = [item(99)]
    enable(service, ['root-b', 'root-a'])
    directory = library.public_libraries()
    assert directory['configured'] is True and directory['updated_at'] and not directory['stale']
    assert [row['id'] for row in directory['libraries']] == [library_id(library, raw) for raw in ('root-b', 'root-a')]
    assert [row['name'] for row in directory['libraries']] == ['媒体库 1', '媒体库 0']
    assert all(set(row) == {'id', 'name', 'cover_url'} for row in directory['libraries'])
    assert all(row['cover_url'] == '/api/public/library/cover/' + row['id'] for row in directory['libraries'])
    assert [call[2] for call in upstream.calls] == ['/Library/VirtualFolders']
    serialized = json.dumps(directory)
    for secret in (TOKEN, BASE, 'root-a', 'root-b', 'root-private', 'Locations', 'LibraryOptions', '/private/'):
        assert secret not in serialized
    assert not library._pages and not library._items, 'directory does not load or authorize every movie'


def test_public_directory_redacts_origin_and_token_from_names(service):
    library, _, _, _ = service
    enable(service, ['root-a'])
    library.fetch = lambda *args, **kwargs: [{'ItemId': 'root-a', 'Name': f'<b>精选</b> {BASE} {TOKEN}'}]
    directory = library.public_libraries()
    assert '精选' in directory['libraries'][0]['name']
    for secret in (TOKEN, BASE, 'private-media.example', '<b>'):
        assert secret not in json.dumps(directory)


def test_directory_cache_is_detached_bounded_and_cleared_when_library_order_changes(service):
    library, _, clock, upstream = service
    enable(service)
    first = library.public_libraries()
    old_ids = [row['id'] for row in first['libraries']]
    first['libraries'][0]['name'] = 'caller mutation'
    assert library.public_libraries()['libraries'][0]['name'] != 'caller mutation'
    assert len(upstream.calls) == 1
    clock.value += 301
    upstream.failing = True
    stale = library.public_libraries()
    assert stale['stale'] and len(stale['libraries']) == 2
    clock.value += 1800
    with pytest.raises(EmbyLibraryError) as caught:
        library.public_libraries()
    assert TOKEN not in str(caught.value) and BASE not in str(caught.value)
    upstream.failing = False
    library.save_config({'library_ids': ['root-b', 'root-a']})
    assert library._directory is None
    result = library.public_libraries()
    assert [row['name'] for row in result['libraries']] == ['媒体库 1', '媒体库 0']
    assert not set(old_ids) & {row['id'] for row in result['libraries']}


def test_public_list_requires_one_current_selected_library_and_never_falls_back_to_mixed_items(service):
    library, _, _, upstream = service
    enable(service, ['root-a'])
    for opaque, code in (('', 400), ('root-a', 404), ('root-b', 404), ('a' * 64, 404),
                         (library_id(library, 'root-b'), 404), (None, 404), ([], 404)):
        with pytest.raises(EmbyLibraryError) as caught:
            library.public(library_id=opaque)
        assert caught.value.status == code
    assert not upstream.calls
    old = library_id(library)
    catalog(library)
    library.save_config({'library_ids': ['root-b']})
    upstream.calls.clear()
    with pytest.raises(EmbyLibraryError) as caught:
        library.public(library_id=old)
    assert caught.value.status == 404
    assert not upstream.calls and not library._pages


@pytest.mark.parametrize('sort,by,direction', [('name', 'SortName', 'Ascending'),
                                            ('newest', 'DateCreated', 'Descending'),
                                            ('year', 'ProductionYear,SortName', 'Descending')])
def test_sort_maps_only_to_fixed_emby_fields_and_preserves_upstream_order(service, sort, by, direction):
    library, _, _, upstream = service
    enable(service, ['root-a'])
    expected = {'name': [1, 2, 3], 'newest': [3, 1, 2], 'year': [2, 3, 1]}[sort]
    def sorted_items(*args, **kwargs):
        params = args[3]
        assert params['SortBy'] == by and params['SortOrder'] == direction
        assert params['ParentId'] == 'root-a' and params['StartIndex'] == 0 and params['Limit'] == 25
        return {'Items': [item(number) for number in expected], 'TotalRecordCount': 3}
    library.fetch = sorted_items
    result = catalog(library, sort=sort)
    assert [row['title'] for row in result['items']] == [f'影片 {number}' for number in expected]


@pytest.mark.parametrize('sort', ['random', 'SortName;Delete', '', None, [], 1])
def test_invalid_sort_is_rejected_before_any_request(service, sort):
    library, _, _, upstream = service
    enable(service)
    with pytest.raises(EmbyLibraryError) as caught:
        catalog(library, sort=sort)
    assert caught.value.status == 400
    assert not upstream.calls


def test_page_cache_is_partitioned_by_library_and_sort(service):
    library, _, _, upstream = service
    enable(service)
    for root in ('root-a', 'root-b'):
        for sort in ('name', 'newest', 'year'):
            assert catalog(library, root=root, sort=sort)['items']
    assert len(upstream.calls) == len(library._pages) == 6
    first = catalog(library, root='root-a', sort='name')
    second = catalog(library, root='root-b', sort='name')
    assert len(upstream.calls) == 6
    assert all(row['type'] == 'movie' for row in first['items'])
    assert all(row['type'] == 'tv' for row in second['items'])


def test_cover_only_authorizes_current_selected_roots_in_separate_namespace(service):
    library, _, _, upstream = service
    enable(service, ['root-a'])
    opaque = library_id(library)
    for value in ('root-a', 'root-b', '../root-a', 'a' * 64, library_id(library, 'root-b')):
        with pytest.raises(EmbyLibraryError) as caught:
            library.cover(value)
        assert caught.value.status == 404
    assert not upstream.calls
    assert library.cover(opaque) == (JPEG, 'image/jpeg')
    assert library.cover(opaque) == (JPEG, 'image/jpeg')
    assert len(upstream.calls) == 2 and upstream.calls[1][2] == '/Items/root-a/Images/Primary'
    assert upstream.calls[1][3] == {'MaxWidth': 500, 'Quality': 85, 'Tag': 'test_image_version'}
    with pytest.raises(EmbyLibraryError):
        library.poster(opaque)
    poster_id = catalog(library)['items'][0]['id']
    with pytest.raises(EmbyLibraryError):
        library.cover(poster_id)
    assert library._opaque(module._read_config(library.config_path), 'root-a') != opaque
    library.save_config({'library_ids': ['root-b']})
    upstream.calls.clear()
    with pytest.raises(EmbyLibraryError) as caught:
        library.cover(opaque)
    assert caught.value.status == 404 and not upstream.calls and not library._images


@pytest.mark.parametrize('image', [None, (b'<svg>PRIVATE</svg>', 'image/svg+xml'),
                                 (b'<html>PRIVATE</html>', 'image/jpeg'),
                                 (b'\xff\xd8\xff' + b'x' * module.MAX_IMAGE_BYTES, 'image/jpeg')])
def test_missing_invalid_or_oversized_cover_returns_generic_404_for_ui_fallback(service, image):
    library, _, _, upstream = service
    enable(service)
    upstream.image = image
    with pytest.raises(EmbyLibraryError) as caught:
        library.cover(library_id(library))
    assert caught.value.status == 404
    for secret in ('PRIVATE', TOKEN, BASE, 'root-a'):
        assert secret not in str(caught.value)
    assert not library._images


def test_cover_and_poster_share_the_total_image_cache_byte_limit(service, monkeypatch):
    library, _, clock, upstream = service
    enable(service)
    opaque = library_id(library)
    poster_id = catalog(library)['items'][0]['id']
    monkeypatch.setattr(module, 'MAX_IMAGE_CACHE_BYTES', len(JPEG))
    library.cover(opaque)
    library.poster(poster_id)
    assert len(library._images) == 1 and library._image_bytes == len(JPEG)
    before = len(upstream.calls)
    library.cover(opaque)
    assert len(upstream.calls) == before + 2
    clock.value += 301
    library.cover(opaque)
    assert len(upstream.calls) == before + 4


@pytest.mark.parametrize('resource', ['directory', 'cover'])
def test_late_directory_and_cover_cannot_repopulate_cache_after_configuration_changes(service, resource):
    library, _, _, upstream = service
    enable(service)
    opaque = library_id(library)
    entered, release = threading.Event(), threading.Event()
    def slow(*args, **kwargs):
        if args[2] == '/Library/VirtualFolders' or '/Images/Primary' in args[2]:
            entered.set(); assert release.wait(3)
        return upstream(*args, **kwargs)
    library.fetch = slow
    errors = []
    def read():
        try:
            library.public_libraries() if resource == 'directory' else library.cover(opaque)
        except EmbyLibraryError as error:
            errors.append(error)
    worker = threading.Thread(target=read)
    worker.start(); assert entered.wait(3)
    library.save_config({'enabled': False})
    release.set(); worker.join(3)
    assert not worker.is_alive()
    assert errors and errors[0].status == (409 if resource == 'directory' else 404)
    assert library._directory is None and not library._images and library._image_bytes == 0


def test_directory_queue_and_upstream_share_the_same_total_deadline(service, monkeypatch):
    library, _, _, upstream = service
    enable(service)
    monotonic, deadlines, released = [0], [], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: monotonic[0])
    class WaitedLock:
        def acquire(self, timeout):
            assert timeout == 12
            monotonic[0] += 10
            return True
        def release(self):
            released.append(True)
    def slow(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        monotonic[0] += 3
        return upstream(*args, **kwargs)
    library._upstream_lock, library.fetch = WaitedLock(), slow
    with pytest.raises(EmbyLibraryError):
        library.public_libraries()
    assert deadlines == [12] and released == [True]
    assert library._directory is None


def test_public_directory_and_cover_routes_do_not_expose_raw_roots_or_secret_fields(route_client):
    client, service, upstream, buckets = route_client
    service.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': True, 'library_ids': ['root-b']})
    upstream.calls.clear()
    response = client.get('/api/public/libraries')
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    assert set(response.json()) == {'configured', 'libraries', 'updated_at', 'stale'}
    assert len(response.json()['libraries']) == 1
    row = response.json()['libraries'][0]
    assert set(row) == {'id', 'name', 'cover_url'}
    assert TOKEN not in response.text and BASE not in response.text and 'root-b' not in response.text
    cover = client.get(row['cover_url'])
    assert cover.status_code == 200 and cover.content == JPEG and cover.headers['Cache-Control'] == 'no-store'
    assert cover.headers['Content-Type'] == 'image/jpeg' and cover.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'Location' not in cover.headers
    page = client.get('/api/public/library', params={'library': row['id'], 'sort': 'newest', 'page': 2})
    assert page.status_code == 200 and len(page.json()['items']) == 1
    assert upstream.calls[-1][3]['ParentId'] == 'root-b'
    assert upstream.calls[-1][3]['SortBy'] == 'DateCreated' and upstream.calls[-1][3]['StartIndex'] == 24
    assert client.get('/api/public/library', params={'library': 'root-b'}).status_code == 404
    assert client.get('/api/public/library').json()['error']['code'] == 'library_required'
    assert client.get('/api/public/library/cover/root-b').status_code == 404
    assert client.get('/api/public/library', params={'library': row['id'], 'sort': 'arbitrary'}).status_code == 400
    assert any(bucket == 'public_libraries' for bucket, _ in buckets)


def test_disabled_search_does_not_start_workers_or_request_emby(service):
    library, _, _, upstream = service
    assert library.search('影片') == {'configured': False, 'groups': [], 'updated_at': None, 'stale': False, 'partial': False}
    assert not upstream.calls and library._search_pool is None


@pytest.mark.parametrize('query', ['', 'a', ' a ', 'x' * 81, 'a\nword', 'a\x00word', None, [], 1])
def test_global_search_requires_a_safe_two_to_eighty_character_query(service, query):
    library, _, _, upstream = service
    enable(service)
    with pytest.raises(EmbyLibraryError) as caught:
        library.search(query)
    assert caught.value.status == 400
    assert not upstream.calls and library._search_pool is None


def test_search_is_grouped_in_selected_order_and_only_returned_six_items_authorize_posters(service):
    library, _, _, upstream = service
    upstream.rows['private-root'] = [item(999)]
    enable(service, ['root-b', 'root-a'])
    result = library.search('  影片  ')
    assert set(result) == {'configured', 'groups', 'updated_at', 'stale', 'partial'}
    assert result['configured'] and not result['stale'] and not result['partial']
    groups = result['groups']
    assert [group['library']['id'] for group in groups] == [library_id(library, root) for root in ('root-b', 'root-a')]
    assert [group['library']['name'] for group in groups] == ['媒体库 1', '媒体库 0']
    assert all(len(group['items']) == 6 and group['has_more'] for group in groups)
    assert [row['title'] for row in groups[0]['items']] == [f'影片 {number}' for number in range(31, 37)]
    assert [row['title'] for row in groups[1]['items']] == [f'影片 {number}' for number in range(1, 7)]
    item_calls = [call for call in upstream.calls if call[2] == '/Items']
    assert {call[3]['ParentId'] for call in item_calls} == {'root-a', 'root-b'}
    assert all(call[3]['SearchTerm'] == '影片' and call[3]['Limit'] == 7 and call[3]['StartIndex'] == 0 for call in item_calls)
    serialized = json.dumps(result)
    for forbidden in (TOKEN, BASE, 'private-root', 'root-a', 'root-b', 'Locations', 'MediaSources', '/private/', 'UserData'):
        assert forbidden not in serialized
    assert len(library._items) == 12
    assert library.poster(groups[0]['items'][0]['id']) == (JPEG, 'image/jpeg')
    with pytest.raises(EmbyLibraryError):
        library.poster(library._opaque(module._read_config(library.config_path), '37'))


def test_search_omits_empty_groups_and_full_empty_is_distinct_from_partial_failure(service):
    library, _, _, upstream = service
    enable(service)
    result = library.search('影片 55')
    assert len(result['groups']) == 1 and result['groups'][0]['library']['id'] == library_id(library, 'root-b')
    assert len(result['groups'][0]['items']) == 1 and not result['groups'][0]['has_more']
    empty = library.search('完全不存在')
    assert empty['groups'] == [] and empty['partial'] is False
    library._searches.clear(); library._search_bytes = 0
    def one_fails(*args, **kwargs):
        if args[2] == '/Items' and args[3]['ParentId'] == 'root-a':
            raise RuntimeError('PRIVATE FAILURE ' + TOKEN)
        return upstream(*args, **kwargs)
    library.fetch = one_fails
    partial = library.search('完全不存在')
    assert partial['groups'] == [] and partial['partial'] is True
    assert not library._searches
    assert TOKEN not in json.dumps(partial)


def test_search_partial_preserves_successes_and_does_not_cache_the_failure(service):
    library, _, _, upstream = service
    enable(service)
    def one_fails(*args, **kwargs):
        if args[2] == '/Items' and args[3]['ParentId'] == 'root-b':
            raise RuntimeError('PRIVATE FAILURE')
        return upstream(*args, **kwargs)
    library.fetch = one_fails
    result = library.search('影片')
    assert result['partial'] and not result['stale']
    assert [group['library']['id'] for group in result['groups']] == [library_id(library, 'root-a')]
    assert len(library._items) == 6 and not library._searches
    library.fetch = upstream
    complete = library.search('影片')
    assert not complete['partial'] and len(complete['groups']) == 2


def test_search_all_failures_return_503_not_a_false_empty_or_old_complete_result(service):
    library, _, clock, upstream = service
    enable(service)
    library.search('影片')
    clock.value += 301
    upstream.failing = True
    with pytest.raises(EmbyLibraryError) as caught:
        library.search('影片')
    assert caught.value.status == 503
    assert TOKEN not in str(caught.value) and BASE not in str(caught.value)


def test_search_cache_reuses_normalized_query_without_upstream_and_enforces_both_limits(service, monkeypatch):
    library, _, clock, upstream = service
    enable(service)
    result = library.search('  影片  ')
    before = len(upstream.calls)
    result['groups'][0]['items'][0]['title'] = 'mutated'
    assert library.search('影片')['groups'][0]['items'][0]['title'] != 'mutated'
    assert len(upstream.calls) == before
    monkeypatch.setattr(module, 'MAX_SEARCH_KEYS', 2)
    for query in ('影片 1', '影片 2', '影片 3'):
        library.search(query)
    assert len(library._searches) == 2
    assert library._search_bytes == sum(entry['size'] for entry in library._searches.values())
    monkeypatch.setattr(module, 'MAX_SEARCH_CACHE_BYTES', 1)
    library.search('影片 4')
    assert not library._searches and library._search_bytes == 0
    clock.value += 301
    library.search('影片')
    assert len(upstream.calls) > before


def test_search_cached_result_does_not_wait_behind_an_unrelated_search(service):
    library, _, _, _ = service
    enable(service)
    expected = library.search('影片')
    class BusyLock:
        def acquire(self, timeout):
            raise AssertionError('fresh search cache must not wait')
    library._search_lock = BusyLock()
    assert library.search('影片') == expected


def test_search_reuses_current_directory_and_marks_stale_names_if_refresh_fails(service):
    library, _, clock, upstream = service
    enable(service)
    library.public_libraries()
    upstream.calls.clear()
    assert not library.search('影片')['stale']
    assert all(call[2] == '/Items' for call in upstream.calls)
    clock.value += 301
    def stale_directory(*args, **kwargs):
        if args[2] == '/Library/VirtualFolders':
            raise RuntimeError('directory temporarily unavailable')
        return upstream(*args, **kwargs)
    library.fetch = stale_directory
    result = library.search('影片 1')
    assert result['stale'] and not result['partial'] and result['groups']
    assert '影片 1' not in library._searches


def test_search_respects_queue_time_and_does_not_start_work_after_deadline(service, monkeypatch):
    library, _, _, upstream = service
    enable(service)
    elapsed, released = [0], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: elapsed[0])
    class DelayedLock:
        def acquire(self, timeout):
            assert timeout == 12
            elapsed[0] += 13
            return True
        def release(self):
            released.append(True)
    library._search_lock = DelayedLock()
    with pytest.raises(EmbyLibraryError) as caught:
        library.search('影片')
    assert caught.value.status == 503 and released == [True]
    assert not upstream.calls and library._search_pool is None


def test_search_uses_at_most_four_workers_and_returns_groups_in_config_order(service):
    library, _, _, upstream = service
    selected = [f'root-{number}' for number in range(9)]
    upstream.rows = {root: [item(number)] for number, root in enumerate(selected)}
    enable(service, selected)
    library.public_libraries()
    entered, release, state_lock = threading.Event(), threading.Event(), threading.Lock()
    state = {'active': 0, 'peak': 0, 'started': 0}
    def controlled(*args, **kwargs):
        if args[2] != '/Items':
            return upstream(*args, **kwargs)
        with state_lock:
            state['active'] += 1; state['started'] += 1
            state['peak'] = max(state['peak'], state['active'])
            if state['active'] == 4:
                entered.set()
        try:
            assert release.wait(3)
            return upstream(*args, **kwargs)
        finally:
            with state_lock:
                state['active'] -= 1
    library.fetch = controlled
    output, errors = [], []
    def run():
        try:
            output.append(library.search('影片'))
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start(); assert entered.wait(3)
    assert state['started'] == 4, 'remaining selected libraries must not start yet'
    release.set(); worker.join(3)
    assert not worker.is_alive() and not errors and output
    assert state['peak'] == 4 and state['started'] == 9
    assert [group['library']['id'] for group in output[0]['groups']] == [library_id(library, root) for root in selected]


def test_simultaneous_searches_share_the_same_four_workers_and_bounded_fanout(service):
    library, _, _, upstream = service
    selected = [f'root-{number}' for number in range(8)]
    upstream.rows = {root: [item(number)] for number, root in enumerate(selected)}
    enable(service, selected)
    library.public_libraries()
    entered, release, state_lock = threading.Event(), threading.Event(), threading.Lock()
    state = {'active': 0, 'peak': 0, 'started': []}
    def controlled(*args, **kwargs):
        if args[2] != '/Items':
            return upstream(*args, **kwargs)
        with state_lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
            state['started'].append(args[3]['SearchTerm'])
            if state['active'] == 4:
                entered.set()
        try:
            assert release.wait(3)
            return upstream(*args, **kwargs)
        finally:
            with state_lock:
                state['active'] -= 1
    library.fetch = controlled
    output, errors = [], []
    def run(query):
        try:
            output.append(library.search(query))
        except Exception as error:
            errors.append(error)
    first = threading.Thread(target=run, args=('影片',))
    second = threading.Thread(target=run, args=('影片 1',))
    first.start(); assert entered.wait(3)
    second.start()
    assert state['started'] == ['影片'] * 4
    release.set(); first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert len(output) == 2 and state['peak'] == 4
    assert state['started'][:8] == ['影片'] * 8
    assert state['started'][8:] == ['影片 1'] * 8


def test_search_directory_and_all_workers_receive_the_same_deadline(service, monkeypatch):
    library, _, _, upstream = service
    enable(service)
    elapsed, deadlines = [0], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: elapsed[0])
    def elapsed_fetch(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        elapsed[0] += 2
        return upstream(*args, **kwargs)
    library.fetch = elapsed_fetch
    result = library.search('影片')
    assert not result['partial'] and len(result['groups']) == 2
    assert deadlines == [12, 12, 12] and elapsed[0] == 6


def test_configuration_change_clears_cached_search_and_revokes_its_poster_authorizations(service):
    library, _, _, upstream = service
    enable(service)
    result = library.search('影片')
    old_poster = result['groups'][0]['items'][0]['id']
    assert library._searches and library._search_bytes > 0
    library.save_config({'library_ids': ['root-b']})
    assert not library._searches and not library._items and library._search_bytes == 0
    upstream.calls.clear()
    with pytest.raises(EmbyLibraryError):
        library.poster(old_poster)
    result = library.search('影片')
    assert len(result['groups']) == 1
    assert result['groups'][0]['library']['id'] == library_id(library, 'root-b')
    assert all(call[3]['ParentId'] == 'root-b' for call in upstream.calls if call[2] == '/Items')


def test_search_timeout_returns_partial_and_late_workers_never_grant_poster_access(service, monkeypatch):
    library, _, _, upstream = service
    selected = [f'root-{number}' for number in range(8)]
    upstream.rows = {root: [item(number)] for number, root in enumerate(selected)}
    enable(service, selected)
    library.public_libraries()
    release = threading.Event()
    monkeypatch.setattr(module, 'TIMEOUT_SECONDS', 0.15)
    def controlled(*args, **kwargs):
        if args[2] == '/Items' and args[3]['ParentId'] != selected[0]:
            assert release.wait(3)
        return upstream(*args, **kwargs)
    library.fetch = controlled
    started = time.monotonic()
    try:
        result = library.search('影片')
        assert time.monotonic() - started < 1
        assert result['partial'] and len(result['groups']) == 1
        assert len(library._items) == 1 and not library._searches
        calls = [call for call in upstream.calls if call[2] == '/Items']
        assert [call[3]['ParentId'] for call in calls] == [selected[0]]
    finally:
        release.set()
        library._search_pool.shutdown(wait=True, cancel_futures=True)
    assert len(library._items) == 1, 'late worker results must never change public authorization'


def test_late_search_after_configuration_change_cannot_publish_results_or_authorize_images(service):
    library, _, _, upstream = service
    enable(service)
    library.public_libraries()
    entered, release = threading.Event(), threading.Event()
    def controlled(*args, **kwargs):
        if args[2] == '/Items':
            entered.set(); assert release.wait(3)
        return upstream(*args, **kwargs)
    library.fetch = controlled
    errors = []
    def run():
        try:
            library.search('影片')
        except EmbyLibraryError as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start(); assert entered.wait(3)
    library.save_config({'enabled': False})
    release.set(); worker.join(3)
    assert not worker.is_alive() and errors and errors[0].status == 409
    assert not library._searches and not library._items and library._search_bytes == 0


def test_public_search_route_validates_query_and_returns_only_public_fields(route_client):
    client, service, upstream, buckets = route_client
    service.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': True, 'library_ids': ['root-a']})
    response = client.get('/api/public/library/search', params={'query': '影片', 'library': 'root-b', 'url': 'https://attacker.example'})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    result = response.json()
    assert len(result['groups']) == 1 and len(result['groups'][0]['items']) == 6
    assert set(result['groups'][0]) == {'library', 'items', 'has_more'}
    assert set(result['groups'][0]['library']) == {'id', 'name'}
    assert TOKEN not in response.text and BASE not in response.text and 'root-a' not in response.text
    assert all(call[3]['ParentId'] == 'root-a' for call in upstream.calls if call[2] == '/Items')
    assert client.get('/api/public/library/search').status_code == 400
    assert client.get('/api/public/library/search', params={'query': 'x'}).status_code == 400
    assert client.get('/api/public/library/search', params={'query': 'x' * 81}).status_code == 400
    assert any(bucket == 'public_library_search' and options['maximum'] == 20 for bucket, options in buckets)


@pytest.mark.parametrize('matches,shown,has_more', [(7, 6, True), (1, 1, False)])
def test_search_accepts_real_matches_when_emby_reports_zero_total(service, matches, shown, has_more):
    library, _, _, upstream = service
    upstream.rows['root-a'] = [item(number, Name=f'哆啦 {number}') for number in range(1, matches + 1)]
    enable(service)
    def zero_total(*args, **kwargs):
        payload = upstream(*args, **kwargs)
        return {**payload, 'TotalRecordCount': 0} if args[2] == '/Items' else payload
    library.fetch = zero_total
    result = library.search('哆啦')
    assert not result['partial'] and len(result['groups']) == 1
    group = result['groups'][0]
    assert group['library']['id'] == library_id(library, 'root-a')
    assert len(group['items']) == shown and group['has_more'] is has_more
    assert len(library._items) == shown
    assert all(call[3]['Limit'] == 7 and 'EnableTotalRecordCount' not in call[3]
               for call in upstream.calls if call[2] == '/Items')


@pytest.mark.parametrize('omit_total', [False, True])
@pytest.mark.parametrize('query', ['', '哆啦'])
def test_library_pagination_with_and_without_search_uses_25th_item_when_total_is_zero_or_absent(service, omit_total, query):
    library, _, _, upstream = service
    upstream.rows['root-a'] = [item(number, Name=f'哆啦 {number}') for number in range(1, 31)]
    enable(service, ['root-a'])
    def uncounted(*args, **kwargs):
        payload = upstream(*args, **kwargs)
        if args[2] == '/Items':
            payload['TotalRecordCount'] = 0
            if omit_total:
                payload.pop('TotalRecordCount')
        return payload
    library.fetch = uncounted
    first = catalog(library, query=query, page=1)
    second = catalog(library, query=query, page=2)
    assert len(first['items']) == 24 and first['has_more'] is True
    assert len(second['items']) == 6 and second['has_more'] is False
    assert not {row['id'] for row in first['items']} & {row['id'] for row in second['items']}
    assert all(call[3]['Limit'] == 25 and 'EnableTotalRecordCount' not in call[3] for call in upstream.calls)


def test_shorter_results_are_valid_when_library_changes_during_a_query(service):
    library, _, _, _ = service
    enable(service, ['root-a'])
    library.fetch = lambda *args, **kwargs: {'Items': [item(1)], 'TotalRecordCount': 300}
    result = catalog(library, query='影片', page=2)
    assert len(result['items']) == 1 and result['has_more'] is False


@pytest.mark.parametrize('payload', [None, [], {}, {'Items': None}, {'Items': {}},
                                    {'Items': [item(number) for number in range(26)]},
                                    {'Items': [], 'TotalRecordCount': -1},
                                    {'Items': [], 'TotalRecordCount': 10_000_001},
                                    {'Items': [], 'TotalRecordCount': True},
                                    {'Items': [], 'TotalRecordCount': None},
                                    {'Items': [], 'TotalRecordCount': '0'}])
def test_optional_total_does_not_weaken_row_bounds_or_present_count_validation(service, payload):
    library, _, _, _ = service
    enable(service, ['root-a'])
    library.fetch = lambda *args, **kwargs: payload
    with pytest.raises(EmbyLibraryError) as caught:
        catalog(library, query='影片')
    assert caught.value.status == 503
    assert not library._items and not library._pages


def test_search_rejects_more_than_seven_rows_even_with_zero_total(service):
    library, _, _, _ = service
    enable(service, ['root-a'])
    library.public_libraries()
    library.fetch = lambda *args, **kwargs: {'Items': [item(number) for number in range(8)], 'TotalRecordCount': 0}
    with pytest.raises(EmbyLibraryError) as caught:
        library.search('影片')
    assert caught.value.status == 503
    assert not library._items and not library._searches
