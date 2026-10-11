"""Cached aggregates for the explicitly published Emby libraries; GET-only upstream.

No source names, item identifiers, paths, credentials or subscription data are
persisted or returned. A single background worker reads movie/episode metadata
only; it never opens source files. Series totals use count queries.
"""
import copy
import datetime as dt
import hashlib
import json
import re
import os
import stat
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from emby_library import EmbyLibraryError, _atomic_config

REFRESH_SECONDS = 6 * 3600
RETRY_SECONDS = 15 * 60
MAX_STALE_SECONDS = 48 * 3600
PAGE_SIZE = 500
EPISODE_PAGE_SIZE = 1000
MIN_EPISODE_PAGE_SIZE = 500
MAX_MOVIES = 200000
MAX_EPISODES = 1000000
MAX_PAGES = 2500
JOB_SECONDS = 30 * 60
PAGE_INTERVAL = 0.10
NATIVE_METRICS = ('uhd', 'hdr', 'dolby_vision')
METRICS = (*NATIVE_METRICS, 'remux')
# Exact Emby enum names; an unknown name may silently disable filtering.
# https://dev.emby.media/reference/pluginapi/MediaBrowser.Model.Entities.ExtendedVideoTypes.html
HDR_VIDEO_TYPES = 'Hdr10,Hdr10Plus,HyperLogGamma,DolbyVision'
MAX_COUNT = 100000000


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise ValueError('Invalid aggregate count')
    return value


def unknown_quality():
    return {'denominator': None, 'remux_unknown': None, **{metric: None for metric in METRICS}}


def validate_quality(quality, denominator):
    if (not isinstance(quality, dict) or set(quality) != {'denominator', 'remux_unknown', *METRICS}
            or count(quality['denominator']) != denominator):
        raise ValueError()
    for key in NATIVE_METRICS:
        if quality[key] is not None and count(quality[key]) > denominator:
            raise ValueError()
    if count(quality['remux']) + count(quality['remux_unknown']) > denominator:
        raise ValueError()


def merge_quality(movies, episodes):
    # Unknown input stays unknown; Series containers are never in this sum.
    return {key: None if movies[key] is None or episodes[key] is None else movies[key] + episodes[key]
            for key in ('denominator', *METRICS, 'remux_unknown')}


def remux_flag(row):
    """REMUX is a filename release marker, not a claimed stream property.

    Missing/unrecognizable paths are unknown instead of a fabricated negative.
    No directory, URL hostname, fragment, title or credential is used as a tag.
    """
    path = row.get('Path')
    if (not isinstance(path, str) or not path.strip() or len(path) > 8192
            or any(ord(character) < 32 for character in path)):
        return None
    if re.match(r'^https?://', path, re.I):
        try:
            path = urlsplit(path).path
        except ValueError:
            return None
    name = path.split('?', 1)[0].split('#', 1)[0].rstrip('/\\').replace('\\', '/').rsplit('/', 1)[-1]
    if not re.search(r'\.(?:mkv|mp4|m4v|avi|mov|ts|m2ts|iso|bdmv|strm|webm)$', name, re.I):
        return None
    return bool(re.search(r'(?<![a-z0-9])remux(?![a-z0-9])', name, re.I))


class EmbyStats:
    def __init__(self, library, data_dir, *, now=time.time, launch=None):
        self.library, self.now = library, now
        self.path = Path(data_dir) / 'emby-public-stats.json'
        self._lock = threading.RLock()
        self._launch = launch or (lambda target: threading.Thread(target=target, name='emby-public-stats', daemon=True).start())
        self._running = False
        self._attempted = {}
        self._cache = None
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError()
                raw = source.read(8193)
            if len(raw) > 8192:
                raise ValueError()
            candidate = json.loads(raw)
            self._validate_cache(candidate)
            self._cache = candidate
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def _key(config):
        # Persisted caches must bind the full private selection and credential,
        # including after restart or externally restored configuration files.
        identity = {key: config[key] for key in ('generation', 'base_url', 'library_ids', 'token_hash')}
        identity['schema'] = 5
        return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    def _snapshot(self):
        with self.library._lock:
            config = self.library._config_locked()
            token = self.library._bound_token(config)
            return config, token

    @staticmethod
    def _validate_cache(entry):
        if not isinstance(entry, dict) or set(entry) != {'key', 'created', 'data'}:
            raise ValueError()
        if not isinstance(entry['key'], str) or not re.fullmatch(r'[a-f0-9]{64}', entry['key']):
            raise ValueError()
        if type(entry['created']) not in (int, float) or not 0 < entry['created'] < 1e11:
            raise ValueError()
        data = entry['data']
        if not isinstance(data, dict) or set(data) != {'library_count', 'counts', 'quality', 'quality_by_type', 'updated_at'}:
            raise ValueError()
        count(data['library_count'])
        if not isinstance(data['counts'], dict) or set(data['counts']) != {'movies', 'series', 'episodes'}:
            raise ValueError()
        for value in data['counts'].values():
            count(value)
        validate_quality(data['quality'], data['counts']['movies'])
        groups = data['quality_by_type']
        if not isinstance(groups, dict) or set(groups) != {'movies', 'episodes', 'all'}:
            raise ValueError()
        for group in ('movies', 'episodes'):
            validate_quality(groups[group], data['counts'][group])
        validate_quality(groups['all'], data['counts']['movies'] + data['counts']['episodes'])
        if data['quality'] != groups['movies'] or groups['all'] != merge_quality(groups['movies'], groups['episodes']):
            raise ValueError()
        expected = dt.datetime.fromtimestamp(entry['created'], dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
        if data['updated_at'] != expected:
            raise ValueError()

    def public(self):
        config, token = self._snapshot()
        if not self.library._enabled(config, token):
            return {'configured': False, 'state': 'unconfigured'}
        key = self._key(config)
        launch = False
        with self._lock:
            entry = self._cache if self._cache and self._cache['key'] == key else None
            age = self.now() - entry['created'] if entry else None
            fresh = entry is not None and 0 <= age < REFRESH_SECONDS
            attempted = self._attempted.get(key)
            if not fresh and not self._running and (attempted is None or self.now() - attempted >= RETRY_SECONDS):
                self._running = True
                self._attempted = {key: self.now()}
                launch = True
            if entry and 0 <= age <= MAX_STALE_SECONDS:
                result = {'configured': True, 'state': 'ready', 'stale': not fresh, **copy.deepcopy(entry['data'])}
            else:
                result = {'configured': True, 'state': 'syncing' if self._running else 'unavailable',
                    'library_count': len(config['library_ids']), 'updated_at': None, 'stale': False,
                    'counts': {'movies': None, 'series': None, 'episodes': None},
                    'quality': unknown_quality(),
                    'quality_by_type': {group: unknown_quality() for group in ('movies', 'episodes', 'all')}}
        # Never acquire the library lock (or run an injected launcher) while
        # holding the statistics lock. Browse paths need not wait for the scan.
        if launch:
            try:
                self._launch(lambda: self._refresh(config, token))
            except Exception:
                with self._lock:
                    self._running = False
                if result['state'] == 'syncing':
                    result['state'] = 'unavailable'
        return result

    def _check_current(self, config, deadline):
        if time.monotonic() >= deadline:
            raise ValueError('Deadline')
        current, token = self._snapshot()
        if self._key(current) != self._key(config) or not self.library._enabled(current, token):
            raise ValueError('Configuration changed')

    def _fetch(self, config, token, params, deadline):
        self._check_current(config, deadline)
        result = self.library.fetch(config['base_url'], token, '/Items', params, deadline=min(deadline, time.monotonic()+12))
        self._check_current(config, deadline)
        if not isinstance(result, dict) or not isinstance(result.get('Items'), list):
            raise ValueError('Invalid result')
        total = count(result.get('TotalRecordCount'))
        if len(result['Items']) > params['Limit'] or (total == 0 and result['Items']):
            raise ValueError('Invalid aggregate page')
        return result

    def _collect(self, config, token):
        deadline = time.monotonic() + JOB_SECONDS
        totals = {'movies': 0, 'series': 0, 'episodes': 0}
        states = {group: {'remux_by_id': {}, 'overlap': False,
                          'quality': {metric: 0 for metric in NATIVE_METRICS}}
                  for group in ('movies', 'episodes')}
        page_count = 0
        self._check_current(config, deadline)
        available = {row['id'] for row in self.library._library_list(config, token, deadline=min(deadline,time.monotonic()+12))}
        self._check_current(config, deadline)
        if not set(config['library_ids']) <= available:
            raise ValueError('Selected libraries changed')
        for library_id in config['library_ids']:
            params = {'ParentId': library_id, 'Recursive': 'true', 'EnableImages': 'false', 'EnableUserData': 'false',
                      'EnableTotalRecordCount': 'true', 'GroupItemsIntoCollections': 'false',
                      'SortBy': 'SortName', 'SortOrder': 'Ascending', 'StartIndex': 0, 'Limit': 1}
            payload = self._fetch(config, token, {**params, 'IncludeItemTypes': 'Series'}, deadline)
            totals['series'] += count(payload['TotalRecordCount'])
            count(totals['series']); time.sleep(PAGE_INTERVAL)
            for kind, group, page_size, maximum in (('Movie', 'movies', PAGE_SIZE, MAX_MOVIES),
                                                     ('Episode', 'episodes', EPISODE_PAGE_SIZE, MAX_EPISODES)):
                state = states[group]
                remux_by_id, quality = state['remux_by_id'], state['quality']
                other_ids = states['episodes' if group == 'movies' else 'movies']['remux_by_id']
                # Native filters count real video entries, never their Series
                # containers. HDR is one OR query, including mixed formats once.
                native_counts = {}
                for metric, filters in (('uhd', {'MinWidth': 3840}),
                                        ('hdr', {'ExtendedVideoTypes': HDR_VIDEO_TYPES}),
                                        ('dolby_vision', {'ExtendedVideoTypes': 'DolbyVision'})):
                    try:
                        payload = self._fetch(config, token, {**params, 'IncludeItemTypes': kind, **filters}, deadline)
                        native_counts[metric] = count(payload['TotalRecordCount'])
                    except Exception:
                        self._check_current(config, deadline)
                        native_counts[metric] = None
                    time.sleep(PAGE_INTERVAL)
                offset, expected, local_seen = 0, None, set()
                while expected is None or offset < expected:
                    page_count += 1
                    if page_count > MAX_PAGES:
                        raise ValueError('Page budget')
                    try:
                        payload = self._fetch(config, token, {**params, 'IncludeItemTypes': kind, 'Fields': 'Path',
                                                             'StartIndex': offset, 'Limit': page_size}, deadline)
                    except EmbyLibraryError:
                        self._check_current(config, deadline)
                        # The shared transport intentionally does not expose
                        # whether a failure was body size or network related.
                        # Allow one bounded, smaller read of the same episode
                        # page; no rows/offset have been accepted on failure.
                        if kind != 'Episode' or page_size <= MIN_EPISODE_PAGE_SIZE:
                            raise
                        page_size = MIN_EPISODE_PAGE_SIZE
                        time.sleep(PAGE_INTERVAL)
                        continue
                    total = count(payload['TotalRecordCount'])
                    if expected is None:
                        expected = total
                    if total != expected or expected > maximum:
                        raise ValueError('Library changed during scan')
                    rows = payload['Items']
                    if len(rows) != min(page_size, expected-offset):
                        raise ValueError('Incomplete page')
                    for row in rows:
                        if not isinstance(row, dict) or row.get('Type') != kind:
                            raise ValueError('Invalid video type')
                        item_id = row.get('Id')
                        if (not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', item_id)
                                or item_id in local_seen or item_id in other_ids):
                            raise ValueError('Unstable pagination or video identity')
                        local_seen.add(item_id)
                        present = remux_flag(row)
                        if item_id not in remux_by_id:
                            remux_by_id[item_id] = present
                            if len(remux_by_id) > maximum:
                                raise ValueError('Video budget')
                        else:
                            state['overlap'] = True
                            if remux_by_id[item_id] != present:
                                remux_by_id[item_id] = None
                    offset += len(rows)
                    time.sleep(PAGE_INTERVAL)
                for metric, value in native_counts.items():
                    if value is None or value > expected or quality[metric] is None:
                        quality[metric] = None
                    else:
                        quality[metric] += value
        self._check_current(config, deadline)
        groups = {}
        for group, state in states.items():
            remux_by_id, quality = state['remux_by_id'], state['quality']
            totals[group] = len(remux_by_id)
            if state['overlap']:
                # Native counts cannot deduplicate cross-root overlap. The
                # affected type is unknown; the other type remains independent.
                for metric in NATIVE_METRICS:
                    quality[metric] = None
            groups[group] = {'denominator': totals[group], **quality,
                             'remux': sum(value is True for value in remux_by_id.values()),
                             'remux_unknown': sum(value is None for value in remux_by_id.values())}
        groups['all'] = merge_quality(groups['movies'], groups['episodes'])
        return {'library_count': len(config['library_ids']), 'counts': totals,
                'quality': dict(groups['movies']), 'quality_by_type': groups}

    def _refresh(self, config, token):
        try:
            data = self._collect(config, token)
            created = self.now()
            data['updated_at'] = dt.datetime.fromtimestamp(created, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
            entry = {'key': self._key(config), 'created': created, 'data': data}
            self._validate_cache(entry)
            with self.library._lock:
                current = self.library._config_locked()
                if self._key(current) != self._key(config) or not self.library._enabled(current, self.library._bound_token(current)):
                    return
                _atomic_config(self.path, entry)
                with self._lock:
                    self._cache = entry
        except Exception:
            # Upstream diagnostics can contain paths/credentials; do not expose them.
            pass
        finally:
            with self._lock:
                self._running = False


def install_routes(app, library, rate, data_dir):
    service = EmbyStats(library, data_dir)
    app.state.emby_stats = service

    @app.get('/api/public/library/stats')
    def read_stats(request: Request):
        rate(request, 'public_library_stats', maximum=40, window=60)
        try:
            return service.public()
        except Exception:
            return JSONResponse({'error': {'message': '媒体库统计暂时无法读取，请稍后重试。',
                                           'code': 'library_stats_unavailable'}},
                                status_code=503, headers={'Cache-Control': 'no-store'})

    return service
