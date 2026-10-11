"""Selected-library aggregate isolation, honest unknowns, bounded scans and cache safety."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path
import threading
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import emby_library as el
import emby_stats as stats
from secrets_store import MemorySecrets

TOKEN = 'TEST_ONLY_EMBY_STATS_TOKEN_12345'
BASE = 'https://private-emby.example/emby'
NOW = 1791800000


def movie(identifier, *, remux=False, uhd=False, dv=False, path=True, video_types=None):
    row = {'Id': identifier, 'Type': 'Movie', 'Name': 'PRIVATE_TITLE_' + identifier,
           'native_uhd': uhd, 'native_video_types': list(video_types or (['DolbyVision'] if dv else [])), 'UserData': {'PRIVATE_USER': True},
           'MediaSources': [{'Path': 'https://private-stream.example?token=' + TOKEN}]}
    if path:
        row['Path'] = '/PRIVATE_STORAGE/' + identifier + ('.REMUX' if remux else '') + '.strm'
    return row


def episode(identifier, **kwargs):
    return {**movie(identifier, **kwargs), 'Type': 'Episode'}


class Upstream:
    def __init__(self):
        self.calls = []
        self.rows = {
            'root-a': [movie('m1', remux=True, uhd=True), movie('m2'), movie('m3', uhd=True, dv=True)],
            'root-b': [movie('m4', dv=True, uhd=True), movie('m5', remux=True)],
            'root-private': [movie('PRIVATE_MOVIE', remux=True, uhd=True, dv=True)],
        }
        self.episodes = {
            'root-a': [episode('e1', uhd=True, dv=True, remux=True), episode('e2', uhd=True, video_types=['Hdr10']),
                       episode('e3', path=False), episode('e4')],
            'root-b': [episode('e5', uhd=True, dv=True), episode('e6', remux=True)],
            'root-private': [episode('PRIVATE_EPISODE', uhd=True, dv=True, remux=True)]}
        self.totals = {'root-a': {'Series': 1}, 'root-b': {'Series': 2}, 'root-private': {'Series': 700}}
        self.fail = False
        self.change = None

    def __call__(self, base, token, endpoint, params=None, **kwargs):
        params = dict(params or {})
        self.calls.append((base, token, endpoint, params, kwargs))
        if self.fail:
            raise RuntimeError('PRIVATE_UPSTREAM ' + TOKEN + BASE)
        if endpoint == '/Library/VirtualFolders':
            return [{'ItemId': key, 'Name': 'PRIVATE_LIBRARY_' + key} for key in self.rows]
        assert endpoint == '/Items'
        root = params['ParentId']; kind = params['IncludeItemTypes']
        assert params['Recursive'] == 'true' and params['EnableUserData'] == 'false'
        if kind == 'Series':
            assert params['Limit'] == 1 and 'Fields' not in params
            total = self.totals[root][kind]
            payload = {'Items': [{'Id': 'sample-' + kind, 'Type': kind}] if total else [], 'TotalRecordCount': total}
        else:
            assert kind in ('Movie', 'Episode')
            rows = self.rows[root] if kind == 'Movie' else self.episodes[root]
            if 'MinWidth' in params:
                assert params['MinWidth'] == 3840 and params['Limit'] == 1 and 'Fields' not in params
                rows = [row for row in rows if row.get('native_uhd')]
            elif 'ExtendedVideoTypes' in params:
                assert params['ExtendedVideoTypes'] in ('DolbyVision', stats.HDR_VIDEO_TYPES) and params['Limit'] == 1 and 'Fields' not in params
                wanted = set(params['ExtendedVideoTypes'].split(','))
                rows = [row for row in rows if wanted.intersection(row.get('native_video_types', []))]
            else:
                assert params['Fields'] == 'Path'
            offset, limit = params['StartIndex'], params['Limit']
            payload = {'Items': copy.deepcopy(rows[offset:offset + limit]), 'TotalRecordCount': len(rows)}
        return self.change(payload, params) if self.change else payload


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, 'PAGE_INTERVAL', 0)
    monkeypatch.setattr(stats, 'PAGE_SIZE', 2)
    monkeypatch.setattr(stats, 'EPISODE_PAGE_SIZE', 2)
    clock = [NOW]
    vault, upstream, jobs = MemorySecrets(), Upstream(), []
    library = el.EmbyLibrary(tmp_path, vault, now=lambda: clock[0], fetch=upstream)
    library.save_config({'base_url': BASE, 'token': TOKEN, 'enabled': True, 'library_ids': ['root-a', 'root-b']})
    upstream.calls.clear()
    service = stats.EmbyStats(library, tmp_path, now=lambda: clock[0], launch=jobs.append)
    return service, library, vault, upstream, jobs, clock


def finish(service, jobs):
    assert service.public()['state'] == 'syncing'
    assert len(jobs) == 1
    jobs.pop()()
    return service.public()


def test_only_selected_roots_get_only_counts_and_movie_path_pages_public_whitelist(configured):
    service, _, _, upstream, jobs, _ = configured
    result = finish(service, jobs)
    assert result['counts'] == {'movies': 5, 'series': 3, 'episodes': 6}
    assert result['quality'] == {'denominator': 5, 'uhd': 3, 'hdr': 2, 'dolby_vision': 2, 'remux': 2, 'remux_unknown': 0}
    assert result['quality_by_type'] == {
        'movies': result['quality'],
        'episodes': {'denominator': 6, 'uhd': 3, 'hdr': 3, 'dolby_vision': 2, 'remux': 2, 'remux_unknown': 1},
        'all': {'denominator': 11, 'uhd': 6, 'hdr': 5, 'dolby_vision': 4, 'remux': 4, 'remux_unknown': 1}}
    assert result['library_count'] == 2 and result['stale'] is False
    assert set(result) == {'configured', 'state', 'stale', 'library_count', 'counts', 'quality', 'quality_by_type', 'updated_at'}
    calls = [call for call in upstream.calls if call[2] == '/Items']
    assert {call[3]['ParentId'] for call in calls} == {'root-a', 'root-b'}
    assert {call[3].get('Fields') for call in calls} == {None, 'Path'}
    pages = [call[3] for call in calls if 'Fields' in call[3] and call[3]['IncludeItemTypes'] == 'Movie']
    assert [(p['ParentId'], p['StartIndex']) for p in pages] == [('root-a', 0), ('root-a', 2), ('root-b', 0)]
    assert all(p['IncludeItemTypes'] == 'Movie' for p in pages)
    episode_pages = [call[3] for call in calls if call[3].get('Fields') == 'Path' and call[3]['IncludeItemTypes'] == 'Episode']
    assert [(p['ParentId'], p['StartIndex']) for p in episode_pages] == [('root-a', 0), ('root-a', 2), ('root-b', 0)]
    assert all(call[2] in ('/Items', '/Library/VirtualFolders') for call in upstream.calls)
    persisted = service.path.read_text()
    for private in (BASE, TOKEN, 'PRIVATE_', 'root-a', 'm1', 'Path', 'MediaSources', 'UserData'):
        assert private not in json.dumps(result) and private not in persisted
    assert service.path.stat().st_mode & 0o777 == 0o600
    result['counts']['movies'] = 999
    assert service.public()['counts']['movies'] == 5


def test_cold_start_unknown_not_false_zero_and_concurrent_singleflight(configured):
    service, _, _, upstream, jobs, _ = configured
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: service.public(), range(30)))
    assert len(jobs) == 1 and upstream.calls == []
    for result in results:
        assert result['state'] == 'syncing'
        assert all(value is None for value in result['counts'].values())
        assert all(value is None for value in result['quality'].values())
        assert all(value is None for group in result['quality_by_type'].values() for value in group.values())
    jobs.pop()()
    assert service.public()['state'] == 'ready'


def test_failure_cooldown_and_keep_last_good_at_most_48h(configured):
    service, _, _, upstream, jobs, clock = configured
    good = finish(service, jobs)
    first_bytes = service.path.read_bytes()
    clock[0] += stats.REFRESH_SECONDS
    upstream.fail = True
    stale = service.public()
    assert stale['state'] == 'ready' and stale['stale'] is True and stale['counts'] == good['counts']
    jobs.pop()()
    assert service.path.read_bytes() == first_bytes
    assert service.public()['stale'] and not jobs
    clock[0] += stats.RETRY_SECONDS - 1
    assert service.public()['stale'] and not jobs
    clock[0] += 1
    assert service.public()['stale'] and len(jobs) == 1
    jobs.pop()()
    clock[0] = NOW + stats.MAX_STALE_SECONDS
    assert service.public()['state'] == 'ready'
    jobs.pop()()
    clock[0] += 1
    unknown = service.public()
    assert unknown['state'] == 'unavailable' and unknown['counts']['movies'] is None
    assert 'PRIVATE_' not in json.dumps(unknown)


def test_first_failure_returns_unavailable_and_retries_after_cooldown(configured):
    service, _, _, upstream, jobs, clock = configured
    upstream.fail = True
    assert service.public()['state'] == 'syncing'
    jobs.pop()()
    result = service.public()
    assert result['state'] == 'unavailable' and result['quality']['uhd'] is None and not jobs
    assert not service.path.exists()
    clock[0] += stats.RETRY_SECONDS
    assert service.public()['state'] == 'syncing' and len(jobs) == 1


def test_valid_cache_reused_after_restart_and_disabled_or_unbound_token_hides_it(configured):
    service, library, vault, upstream, jobs, clock = configured
    good = finish(service, jobs)
    other_jobs = []
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=other_jobs.append)
    upstream.calls.clear()
    assert replacement.public() == good and other_jobs == [] and upstream.calls == []
    vault.set(el.SECRET_NAME, TOKEN + '_ROTATED_WITHOUT_CONFIG')
    assert replacement.public() == {'configured': False, 'state': 'unconfigured'}
    assert other_jobs == []


@pytest.mark.parametrize('change', [{'library_ids': ['root-a']}, {'base_url': 'https://changed-emby.example'},
                                     {'token_hash': 'a' * 64}, {'generation': 'a' * 32}])
def test_persisted_key_binds_complete_config_even_same_generation(configured, change):
    service, library, _, _, jobs, clock = configured
    finish(service, jobs)
    config = el._read_config(library.config_path)
    before_key = service._key(config)
    changed = {**config, **change}
    assert service._key(changed) != before_key
    el._atomic_config(library.config_path, changed)
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    result = replacement.public()
    assert result['state'] != 'ready'
    assert 'counts' not in result or result['counts']['movies'] is None


def test_save_config_invalidates_cache_and_old_job_is_discarded_on_disable(configured):
    service, library, _, upstream, jobs, _ = configured
    finish(service, jobs)
    library.save_config({'library_ids': ['root-a']})
    assert service.public()['state'] == 'syncing'
    callback = jobs.pop()
    library.save_config({'enabled': False})
    before = len(upstream.calls)
    callback()
    assert len(upstream.calls) == before
    assert service.public() == {'configured': False, 'state': 'unconfigured'}
    assert not service._running


def test_disabled_mid_fetch_drops_new_result_and_does_not_persist(configured):
    service, library, _, upstream, jobs, _ = configured
    changed = False
    def disable(payload, params):
        nonlocal changed
        if not changed:
            changed = True
            library.save_config({'enabled': False})
        return payload
    upstream.change = disable
    service.public(); jobs.pop()()
    assert service._cache is None and not service.path.exists()
    assert service.public()['state'] == 'unconfigured'
    assert len([call for call in upstream.calls if call[2] == '/Items']) == 1


def test_background_scan_does_not_hold_library_lock_or_browse_upstream_lock(configured):
    service, library, _, upstream, _, _ = configured
    entered, release = threading.Event(), threading.Event()
    original = upstream.__call__
    def blocked(*args, **kwargs):
        if args[2] == '/Items':
            entered.set()
            assert release.wait(3)
        return original(*args, **kwargs)
    library.fetch = blocked
    threads = []
    def launch(callback):
        thread = threading.Thread(target=callback); threads.append(thread); thread.start()
    service._launch = launch
    try:
        assert service.public()['state'] == 'syncing'
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            directory = pool.submit(library.public_libraries).result(timeout=0.7)
        assert directory['configured'] and len(directory['libraries']) == 2
        assert library._upstream_lock.acquire(blocking=False)
        library._upstream_lock.release()
    finally:
        release.set()
        for thread in threads: thread.join(3)
    assert service.public()['state'] == 'ready'


def test_launcher_outside_stats_lock_and_start_failure_does_not_wedge_singleflight(configured):
    service, _, _, _, jobs, clock = configured
    joined = []
    def joined_launcher(callback):
        worker = threading.Thread(target=callback, daemon=True)
        worker.start(); worker.join(1)
        joined.append(not worker.is_alive())
    service._launch = joined_launcher
    service.public()
    assert joined == [True] and service.public()['state'] == 'ready'
    clock[0] += stats.REFRESH_SECONDS
    service._cache = None
    service._launch = Mock(side_effect=RuntimeError('PRIVATE_LAUNCH'))
    assert service.public()['state'] == 'unavailable'
    assert not service._running
    service.public()
    service._launch.assert_called_once()
    clock[0] += stats.RETRY_SECONDS
    service._launch = jobs.append
    assert service.public()['state'] == 'syncing' and len(jobs) == 1


@pytest.mark.parametrize('path,expected', [
    ('/folder/title.REMUX.2160p.strm', True), ('/folder/REMUX/title.mkv', False),
    ('C:\\movies\\Title.remux.mkv', True), ('/x/Title.NoREMUX.mkv', False),
    ('/x/Title.mkv?secret=REMUX', False), ('/x/Title.mkv#REMUX', False),
    ('https://REMUX.example/title.mp4?key=REMUX', False), ('https://user:REMUX@host/title.DV.REMUX.strm', True),
    (None, None), ('', None), ('/only/directory', None), ('/folder/title.mkv\n', None), ('x' * 9000, None),
])
def test_remux_uses_only_recognizable_filename_not_title_folder_url_or_credentials(path, expected):
    assert stats.remux_flag({'Path': path, 'Name': 'REMUX UHD DV'}) is expected


def test_missing_path_is_unknown_but_native_quality_counts_remain_valid(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.rows['root-a'][1].pop('Path')
    result = finish(service, jobs)
    assert result['counts']['movies'] == 5
    assert result['quality'] == {'denominator': 5, 'uhd': 3, 'hdr': 2, 'dolby_vision': 2, 'remux': 2, 'remux_unknown': 1}


def test_all_unknown_paths_keep_explicit_zero_coverage_not_false_exact_zero(configured):
    service, library, _, upstream, jobs, clock = configured
    for root in ('root-a', 'root-b'):
        for row in upstream.rows[root]:
            row.pop('Path')
    result = finish(service, jobs)
    assert result['quality'] == {'denominator': 5, 'uhd': 3, 'hdr': 2, 'dolby_vision': 2, 'remux': 0, 'remux_unknown': 5}
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    assert replacement.public()['quality'] == result['quality'] and not jobs


@pytest.mark.parametrize('other_path', [None, '/private/m1.strm'])
def test_conflicting_duplicate_remux_keeps_unique_unknown_coverage(configured, other_path):
    service, _, _, upstream, jobs, _ = configured
    duplicate = copy.deepcopy(upstream.rows['root-a'][0])
    duplicate['Path'] = other_path
    upstream.rows['root-b'] = [duplicate, movie('m5', remux=True)]
    result = finish(service, jobs)
    assert result['quality'] == {'denominator': 4, 'uhd': None, 'hdr': None, 'dolby_vision': None, 'remux': 1, 'remux_unknown': 1}


@pytest.mark.parametrize('old_schema', [2, 3, 4])
def test_old_schema_cache_is_not_rebound_to_schema_five(configured, old_schema):
    service, library, _, _, jobs, clock = configured
    finish(service, jobs)
    old = json.loads(service.path.read_text())
    config, _ = service._snapshot()
    identity = {key: config[key] for key in ('generation', 'base_url', 'library_ids', 'token_hash')}
    identity['schema'] = old_schema
    old['key'] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    service.path.write_text(json.dumps(old))
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    result = replacement.public()
    assert result['state'] == 'syncing' and result['quality']['remux_unknown'] is None


def test_production_movie_page_size_is_500():
    assert stats.PAGE_SIZE == 500


def test_five_hundred_row_pages_cover_tail_without_missing_or_double_counting(configured, monkeypatch):
    service, _, _, upstream, jobs, _ = configured
    monkeypatch.setattr(stats, 'PAGE_SIZE', 500)
    upstream.rows['root-a'] = [movie(f'large-{index}', remux=index < 7, path=index < 500) for index in range(501)]
    upstream.rows['root-b'] = []
    result = finish(service, jobs)
    assert result['counts']['movies'] == 501
    assert result['quality'] == {'denominator': 501, 'uhd': 0, 'hdr': 0, 'dolby_vision': 0, 'remux': 7, 'remux_unknown': 1}
    pages = [call[3] for call in upstream.calls if call[3].get('Fields') == 'Path' and call[3]['IncludeItemTypes'] == 'Movie']
    assert [(p['ParentId'], p['StartIndex'], p['Limit']) for p in pages] == [
        ('root-a', 0, 500), ('root-a', 500, 500), ('root-b', 0, 500)]


def test_oversized_upstream_page_keeps_last_complete_snapshot_without_partial_publish(configured, monkeypatch):
    service, _, _, upstream, jobs, clock = configured
    good = finish(service, jobs)
    before = service.path.read_bytes()
    monkeypatch.setattr(stats, 'PAGE_SIZE', 500)
    upstream.rows['root-a'] = [movie(f'large-{index}', remux=True) for index in range(501)]
    def oversized(payload, params):
        if params.get('Fields') == 'Path' and params['StartIndex'] == 500:
            # The shared transport's 1 MiB response cap fails the whole page.
            raise el.EmbyLibraryError('PRIVATE_RESPONSE_OVERSIZE')
        return payload
    upstream.change = oversized
    clock[0] += stats.REFRESH_SECONDS
    service.public(); jobs.pop()()
    after = service.public()
    assert after['stale'] and after['counts'] == good['counts'] and after['quality'] == good['quality']
    assert service.path.read_bytes() == before and 'PRIVATE_RESPONSE' not in json.dumps(after)


def test_native_filter_failure_or_impossible_count_is_unknown_not_false_zero(configured):
    service, _, _, upstream, jobs, _ = configured
    def bad_count(payload, params):
        if 'MinWidth' in params: raise RuntimeError('PRIVATE_FILTER_ERROR')
        if 'ExtendedVideoTypes' in params: payload['TotalRecordCount'] = 500
        return payload
    upstream.change = bad_count
    result = finish(service, jobs)
    assert result['counts']['movies'] == 5
    assert result['quality'] == {'denominator': 5, 'uhd': None, 'hdr': None, 'dolby_vision': None, 'remux': 2, 'remux_unknown': 0}


def test_hdr_union_counts_each_mixed_type_movie_once(configured):
    service, _, _, upstream, jobs, _ = configured
    assert stats.HDR_VIDEO_TYPES == 'Hdr10,Hdr10Plus,HyperLogGamma,DolbyVision'
    kinds = stats.HDR_VIDEO_TYPES.split(',')
    assert len(kinds) == len(set(kinds)) and 'DolbyVision' in kinds
    upstream.rows['root-a'] = [movie('mixed', video_types=kinds),
                               movie('plain'), movie('hdr-only', video_types=[kinds[0]])]
    upstream.rows['root-b'] = [movie('dv-only', dv=True)]
    result = finish(service, jobs)
    assert result['counts']['movies'] == 4 and result['quality']['hdr'] == 3
    assert result['quality']['dolby_vision'] == 2
    queries = [call[3] for call in upstream.calls if call[3].get('ExtendedVideoTypes') == stats.HDR_VIDEO_TYPES and call[3]['IncludeItemTypes'] == 'Movie']
    assert len(queries) == 2 and {q['ParentId'] for q in queries} == {'root-a', 'root-b'}
    assert all(q['IncludeItemTypes'] == 'Movie' and q['Limit'] == 1 and 'Fields' not in q for q in queries)
    # The numerator comes from one union query per root, never the sum of
    # separately counted video types (which would over-count the mixed movie).
    assert not [call for call in upstream.calls
                if call[3].get('ExtendedVideoTypes') not in (None, 'DolbyVision', stats.HDR_VIDEO_TYPES)]


@pytest.mark.parametrize('defect', ['failure', 'impossible', 'missing', 'boolean'])
def test_only_hdr_failure_keeps_other_native_counts_and_unknown_survives_restart(configured, defect):
    service, library, _, upstream, jobs, clock = configured
    def bad_hdr(payload, params):
        if params.get('ExtendedVideoTypes') == stats.HDR_VIDEO_TYPES and params['ParentId'] == 'root-b':
            if defect == 'failure': raise RuntimeError('PRIVATE_HDR_ERROR')
            if defect == 'impossible': payload['TotalRecordCount'] = 999
            if defect == 'missing': payload.pop('TotalRecordCount')
            if defect == 'boolean': payload['TotalRecordCount'] = True
        return payload
    upstream.change = bad_hdr
    result = finish(service, jobs)
    assert result['quality'] == {'denominator': 5, 'uhd': 3, 'hdr': None,
                                 'dolby_vision': 2, 'remux': 2, 'remux_unknown': 0}
    assert 'PRIVATE_HDR' not in json.dumps(result)
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    assert replacement.public()['quality'] == result['quality'] and not jobs


def test_duplicate_movies_between_roots_deduplicated_native_ratios_unknown(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.rows['root-b'] = [copy.deepcopy(upstream.rows['root-a'][0])]
    result = finish(service, jobs)
    assert result['counts']['movies'] == 3
    assert result['quality'] == {'denominator': 3, 'uhd': None, 'hdr': None, 'dolby_vision': None, 'remux': 1, 'remux_unknown': 0}
    assert result['quality_by_type']['episodes']['uhd'] == 3
    assert result['quality_by_type']['all']['uhd'] is None


def test_episode_overlap_deduplicates_and_only_its_native_group_becomes_unknown(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.episodes['root-b'] = [copy.deepcopy(upstream.episodes['root-a'][0])]
    result = finish(service, jobs)
    groups = result['quality_by_type']
    assert result['counts']['episodes'] == 4
    assert groups['episodes'] == {'denominator': 4, 'uhd': None, 'hdr': None,
                                  'dolby_vision': None, 'remux': 1, 'remux_unknown': 1}
    assert groups['movies'] == result['quality'] and groups['movies']['uhd'] == 3
    assert groups['all'] == {'denominator': 9, 'uhd': None, 'hdr': None,
                             'dolby_vision': None, 'remux': 3, 'remux_unknown': 1}


def test_movie_and_episode_identity_conflict_aborts_entire_snapshot(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.episodes['root-b'][0]['Id'] = upstream.rows['root-a'][0]['Id']
    service.public(); jobs.pop()()
    assert service.public()['state'] == 'unavailable' and not service.path.exists()


def test_episode_hdr_uses_union_and_unknown_does_not_poison_movie_group(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.episodes['root-a'][0]['native_video_types'] = stats.HDR_VIDEO_TYPES.split(',')
    def broken(payload, params):
        if params['IncludeItemTypes'] == 'Episode' and 'MinWidth' in params:
            raise el.EmbyLibraryError('PRIVATE_EPISODE_FILTER_ERROR')
        return payload
    upstream.change = broken
    result = finish(service, jobs)
    groups = result['quality_by_type']
    assert groups['episodes']['hdr'] == 3 and groups['episodes']['uhd'] is None
    assert groups['movies']['uhd'] == 3 and groups['all']['uhd'] is None
    assert groups['all']['hdr'] == 5
    query = [call[3] for call in upstream.calls
             if call[3].get('ExtendedVideoTypes') == stats.HDR_VIDEO_TYPES and call[3]['IncludeItemTypes'] == 'Episode']
    assert len(query) == 2 and all(row['Limit'] == 1 and 'Fields' not in row for row in query)


@pytest.mark.parametrize('defect', ['transport', 'duplicate', 'missing', 'changed_total', 'wrong_type'])
def test_bad_episode_page_never_publishes_even_completed_movie_counts(configured, defect):
    service, _, _, upstream, jobs, clock = configured
    good = finish(service, jobs)
    previous = service.path.read_bytes()
    def broken(payload, params):
        if params['IncludeItemTypes'] != 'Episode' or params.get('Fields') != 'Path': return payload
        if defect == 'transport': raise el.EmbyLibraryError('PRIVATE_EPISODE_ERROR')
        if defect == 'duplicate': payload['Items'] = [episode('same')] * len(payload['Items'])
        if defect == 'missing': payload['Items'] = []
        if defect == 'changed_total' and params['StartIndex']: payload['TotalRecordCount'] += 1
        if defect == 'wrong_type' and payload['Items']: payload['Items'][0]['Type'] = 'Series'
        return payload
    upstream.change = broken
    clock[0] += stats.REFRESH_SECONDS
    service.public(); jobs.pop()()
    after = service.public()
    assert after['stale'] and after['quality_by_type'] == good['quality_by_type']
    assert service.path.read_bytes() == previous


def test_config_disabled_during_episode_fetch_discards_every_group(configured):
    service, library, _, upstream, jobs, _ = configured
    def disable(payload, params):
        if params['IncludeItemTypes'] == 'Episode': library.save_config({'enabled': False})
        return payload
    upstream.change = disable
    service.public(); jobs.pop()()
    assert service.public()['state'] == 'unconfigured' and not service.path.exists()


def test_empty_episodes_are_real_zero_and_all_matches_movies_without_series(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.episodes['root-a'] = []; upstream.episodes['root-b'] = []
    result = finish(service, jobs)
    assert result['counts']['episodes'] == 0 and result['counts']['series'] == 3
    assert all(value == 0 for value in result['quality_by_type']['episodes'].values())
    assert result['quality_by_type']['all'] == result['quality']


def test_all_episode_paths_unknown_keeps_exact_counts_and_partial_remux_coverage(configured):
    service, _, _, upstream, jobs, _ = configured
    for rows in upstream.episodes.values():
        for row in rows: row.pop('Path', None)
    result = finish(service, jobs)
    groups = result['quality_by_type']
    assert groups['episodes']['denominator'] == groups['episodes']['remux_unknown'] == 6
    assert groups['episodes']['remux'] == 0
    assert groups['all']['denominator'] == 11 and groups['all']['remux'] == 2 and groups['all']['remux_unknown'] == 6


def test_production_episode_budgets():
    assert stats.EPISODE_PAGE_SIZE == 1000 and stats.MIN_EPISODE_PAGE_SIZE == 500
    assert stats.MAX_EPISODES >= 1000000 and stats.JOB_SECONDS == 30 * 60


@pytest.mark.parametrize('fail_small_page', [False, True])
def test_episode_transport_failure_has_one_smaller_retry_same_offset_and_global_budget(configured, monkeypatch, fail_small_page):
    service, _, _, upstream, jobs, _ = configured
    monkeypatch.setattr(stats, 'EPISODE_PAGE_SIZE', 1000)
    upstream.episodes['root-a'] = [episode(f'ep-{index}', uhd=True, remux=index < 3) for index in range(1001)]
    def oversize(payload, params):
        if params['IncludeItemTypes'] == 'Episode' and params.get('Fields') == 'Path' and params['ParentId'] == 'root-a':
            if params['Limit'] == 1000 or fail_small_page:
                raise el.EmbyLibraryError('PRIVATE_RESPONSE_BODY_LIMIT')
        return payload
    upstream.change = oversize
    result = finish(service, jobs)
    pages = [call[3] for call in upstream.calls if call[3].get('Fields') == 'Path'
             and call[3]['IncludeItemTypes'] == 'Episode' and call[3]['ParentId'] == 'root-a']
    if fail_small_page:
        assert result['state'] == 'unavailable' and not service.path.exists()
        assert [(p['StartIndex'], p['Limit']) for p in pages] == [(0, 1000), (0, 500)]
    else:
        assert result['counts']['episodes'] == 1003
        assert result['quality_by_type']['episodes']['remux'] == 4
        assert [(p['StartIndex'], p['Limit']) for p in pages] == [(0, 1000), (0, 500), (500, 500), (1000, 500)]


def test_smaller_episode_retry_is_charged_to_page_budget(configured, monkeypatch):
    service, _, _, upstream, jobs, _ = configured
    monkeypatch.setattr(stats, 'EPISODE_PAGE_SIZE', 1000)
    monkeypatch.setattr(stats, 'MAX_PAGES', 3)  # Two Movie pages and one failed Episode page.
    def broken(payload, params):
        if params['IncludeItemTypes'] == 'Episode' and params.get('Fields') == 'Path':
            raise el.EmbyLibraryError()
        return payload
    upstream.change = broken
    service.public(); jobs.pop()()
    pages = [call for call in upstream.calls if call[3].get('Fields') == 'Path']
    assert len(pages) == 3 and service.public()['state'] == 'unavailable'


def test_episode_limit_aborts_without_partial_cache(configured, monkeypatch):
    service, _, _, _, jobs, _ = configured
    monkeypatch.setattr(stats, 'MAX_EPISODES', 3)
    service.public(); jobs.pop()()
    assert service.public()['state'] == 'unavailable' and not service.path.exists()


def test_episode_smaller_retry_after_prior_page_preserves_offset_and_unique_ids(configured, monkeypatch):
    service, _, _, upstream, jobs, _ = configured
    monkeypatch.setattr(stats, 'EPISODE_PAGE_SIZE', 1000)
    upstream.episodes['root-a'] = [episode(f'ep-{index}', remux=True) for index in range(2001)]
    def broken(payload, params):
        if params.get('Fields') == 'Path' and params['IncludeItemTypes'] == 'Episode' and params['StartIndex'] == 1000 and params['Limit'] == 1000:
            raise el.EmbyLibraryError()
        return payload
    upstream.change = broken
    result = finish(service, jobs)
    pages = [call[3] for call in upstream.calls if call[3].get('Fields') == 'Path'
             and call[3]['IncludeItemTypes'] == 'Episode' and call[3]['ParentId'] == 'root-a']
    assert [(p['StartIndex'], p['Limit']) for p in pages] == [(0, 1000), (1000, 1000), (1000, 500), (1500, 500), (2000, 500)]
    assert result['counts']['episodes'] == 2003 and result['quality_by_type']['episodes']['remux'] == 2002


def test_malformed_episode_payload_is_not_retried_at_smaller_page_size(configured, monkeypatch):
    service, _, _, upstream, jobs, _ = configured
    monkeypatch.setattr(stats, 'EPISODE_PAGE_SIZE', 1000)
    def malformed(payload, params):
        if params.get('Fields') == 'Path' and params['IncludeItemTypes'] == 'Episode':
            payload['TotalRecordCount'] = True
        return payload
    upstream.change = malformed
    service.public(); jobs.pop()()
    pages = [call[3] for call in upstream.calls if call[3].get('Fields') == 'Path' and call[3]['IncludeItemTypes'] == 'Episode']
    assert len(pages) == 1 and service.public()['state'] == 'unavailable'


@pytest.mark.parametrize('defect', ['missing_groups', 'extra_group', 'missing_episode', 'all_denominator',
                                   'all_native', 'all_unknown', 'all_remux', 'private_field',
                                   'legacy_mismatch', 'invalid_episode_metric'])
def test_persisted_group_whitelist_and_cross_group_consistency(configured, defect):
    service, library, _, _, jobs, clock = configured
    finish(service, jobs)
    entry = json.loads(service.path.read_text())
    groups = entry['data']['quality_by_type']
    if defect == 'missing_groups': entry['data'].pop('quality_by_type')
    elif defect == 'extra_group': groups['series'] = copy.deepcopy(groups['episodes'])
    elif defect == 'missing_episode': groups.pop('episodes')
    elif defect == 'all_denominator': groups['all']['denominator'] = 8
    elif defect == 'all_native': groups['all']['hdr'] = 4
    elif defect == 'all_unknown': groups['episodes']['hdr'] = None
    elif defect == 'all_remux': groups['all']['remux_unknown'] = 0
    elif defect == 'private_field': groups['episodes']['Path'] = '/PRIVATE_PATH'
    elif defect == 'legacy_mismatch': entry['data']['quality']['uhd'] = 2
    elif defect == 'invalid_episode_metric': groups['episodes']['remux_unknown'] = True
    service.path.write_text(json.dumps(entry))
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    assert replacement._cache is None and replacement.public()['state'] == 'syncing'


@pytest.mark.parametrize('defect', ['duplicate', 'missing', 'total_changes', 'wrong_type', 'missing_total', 'extra_rows'])
def test_unstable_or_incomplete_movie_pages_never_publish_partial_counts(configured, defect):
    service, _, _, upstream, jobs, _ = configured
    def broken(payload, params):
        if params.get('Fields') != 'Path': return payload
        if defect == 'duplicate':
            payload['Items'] = [movie('same')] * len(payload['Items'])
        elif defect == 'missing': payload['Items'] = []
        elif defect == 'total_changes' and params['StartIndex']: payload['TotalRecordCount'] += 1
        elif defect == 'wrong_type' and payload['Items']: payload['Items'][0]['Type'] = 'Episode'
        elif defect == 'missing_total': payload.pop('TotalRecordCount')
        elif defect == 'extra_rows': payload['Items'] += [movie('surprise')]
        return payload
    upstream.change = broken
    service.public(); jobs.pop()()
    result = service.public()
    assert result['state'] == 'unavailable' and all(value is None for value in result['counts'].values())
    assert not service.path.exists()


@pytest.mark.parametrize('limit,value', [('MAX_MOVIES', 1), ('MAX_PAGES', 1), ('JOB_SECONDS', 0)])
def test_movie_page_count_and_deadline_budgets_never_publish_partial(configured, monkeypatch, limit, value):
    service, _, _, _, jobs, _ = configured
    monkeypatch.setattr(stats, limit, value)
    service.public(); jobs.pop()()
    assert service.public()['state'] == 'unavailable' and not service.path.exists()


def test_zero_movie_count_is_real_empty_denominator_not_a_percentage(configured):
    service, _, _, upstream, jobs, _ = configured
    upstream.rows['root-a'] = []; upstream.rows['root-b'] = []
    result = finish(service, jobs)
    assert result['counts']['movies'] == 0 and result['quality']['denominator'] == 0
    assert result['quality']['remux'] == 0 and result['counts']['episodes'] == 6


@pytest.mark.parametrize('mutation', ['extra', 'negative', 'bool', 'denominator', 'too_large_quality', 'bad_timestamp', 'raw_path', 'null_data', 'missing_remux_unknown', 'null_remux', 'null_remux_unknown', 'negative_remux_unknown', 'boolean_remux_unknown', 'excessive_remux_unknown', 'missing_hdr', 'negative_hdr', 'boolean_hdr', 'too_large_hdr'])
def test_cache_whitelist_rejects_tampering_on_restart(configured, mutation):
    service, library, _, _, jobs, clock = configured
    finish(service, jobs)
    entry = json.loads(service.path.read_text())
    if mutation == 'extra': entry['secret'] = TOKEN
    elif mutation == 'negative': entry['data']['counts']['movies'] = -1
    elif mutation == 'bool': entry['data']['counts']['movies'] = True
    elif mutation == 'denominator': entry['data']['quality']['denominator'] += 1
    elif mutation == 'too_large_quality': entry['data']['quality']['uhd'] = 999
    elif mutation == 'bad_timestamp': entry['data']['updated_at'] = 'PRIVATE_DATE'
    elif mutation == 'raw_path': entry['data']['quality']['Path'] = '/PRIVATE_PATH'
    elif mutation == 'null_data': entry['data'] = None
    elif mutation == 'missing_remux_unknown': entry['data']['quality'].pop('remux_unknown')
    elif mutation == 'null_remux': entry['data']['quality']['remux'] = None
    elif mutation == 'null_remux_unknown': entry['data']['quality']['remux_unknown'] = None
    elif mutation == 'negative_remux_unknown': entry['data']['quality']['remux_unknown'] = -1
    elif mutation == 'boolean_remux_unknown': entry['data']['quality']['remux_unknown'] = True
    elif mutation == 'excessive_remux_unknown': entry['data']['quality']['remux_unknown'] = 4
    elif mutation == 'missing_hdr': entry['data']['quality'].pop('hdr')
    elif mutation == 'negative_hdr': entry['data']['quality']['hdr'] = -1
    elif mutation == 'boolean_hdr': entry['data']['quality']['hdr'] = True
    elif mutation == 'too_large_hdr': entry['data']['quality']['hdr'] = 6
    service.path.write_text(json.dumps(entry))
    replacement = stats.EmbyStats(library, service.path.parent, now=lambda: clock[0], launch=jobs.append)
    assert replacement.public()['state'] == 'syncing' and replacement._cache is None


def test_cache_future_timestamp_oversize_symlink_and_atomic_write_failure_safe(configured, tmp_path):
    service, library, _, upstream, jobs, clock = configured
    finish(service, jobs)
    clock[0] -= 1
    assert service.public()['state'] == 'unavailable' and service.public()['counts']['movies'] is None
    jobs.clear(); service._running = False
    service.path.write_text('x' * 9000)
    replacement = stats.EmbyStats(library, tmp_path, now=lambda: clock[0], launch=jobs.append)
    assert replacement._cache is None
    private = tmp_path / 'private-cache'; private.write_text(TOKEN)
    service.path.unlink(); service.path.symlink_to(private)
    replacement = stats.EmbyStats(library, tmp_path, now=lambda: clock[0], launch=jobs.append)
    assert replacement._cache is None and private.read_text() == TOKEN
    service.path.unlink(); clock[0] = NOW
    replacement.public(); callback = jobs.pop()
    with patch.object(stats, '_atomic_config', side_effect=OSError('PRIVATE_STORAGE')):
        callback()
    assert replacement._cache is None and replacement.public()['state'] == 'unavailable'


def test_missing_selected_root_fails_closed(configured):
    service, _, _, upstream, jobs, _ = configured
    del upstream.rows['root-b']
    service.public(); jobs.pop()()
    assert service.public()['state'] == 'unavailable'
    assert all(call[2] != '/Items' for call in upstream.calls)


def test_install_hook_route_no_raw_error_and_no_post(tmp_path):
    app, vault = FastAPI(), MemorySecrets()
    rate = Mock()
    library = el.install_routes(app, vault, lambda request: None, rate, tmp_path)
    assert app.state.emby_stats.library is library
    with TestClient(app) as client:
        assert client.get('/api/public/library/stats').json() == {'configured': False, 'state': 'unconfigured'}
        assert client.post('/api/public/library/stats', json={}).status_code == 405
        with patch.object(app.state.emby_stats, 'public', side_effect=RuntimeError(BASE + TOKEN)):
            response = client.get('/api/public/library/stats')
        assert response.status_code == 503 and TOKEN not in response.text and BASE not in response.text
        assert response.headers['Cache-Control'] == 'no-store'
    assert rate.call_args.args[1] == 'public_library_stats'
