"""Read-only NextFind discovery. No resource, subscription-write or playback APIs."""
from collections import OrderedDict
from contextlib import contextmanager
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlencode, urlsplit

from fastapi import Body, Request
from fastapi.responses import JSONResponse, Response
from secrets_store import default_secret_store


SECRET_NAME = 'nextfind-openapi'
TIMEOUT_SECONDS = 12
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 24
CACHE_SECONDS = 300
FAILURE_RETRY_SECONDS = 30
MAX_CACHE_KEYS = 128
MAX_INFLIGHT = 4
POSTER_PREFIX = 'https://image.tmdb.org/t/p/w500'
POSTER_PATH = re.compile(r'/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp)\Z')
POSTER_OPAQUE = re.compile(r'[a-f0-9]{64}\.(?:jpg|png|webp)\Z')
MAX_IMAGE_BYTES = 2 * 1024 * 1024
UNAVAILABLE = '影片目录暂时无法读取，请稍后重试。'
INVALID = '请检查 NextFind 配置或查询格式。'


class CatalogError(ValueError):
    """Sanitized error: never includes credentials, provider bodies or URLs."""


class CatalogUnavailable(CatalogError):
    pass


def base_url(value):
    if not isinstance(value, str) or len(value) > 512 or any(c.isspace() or ord(c) < 32 for c in value):
        raise CatalogError(INVALID)
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.query or parts.fragment
                or parts.path not in ('', '/') or '\\' in value or parts.port == 0):
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii').lower()
        try:
            address = ipaddress.ip_address(host)
            host = '[' + str(address) + ']' if address.version == 6 else str(address)
        except ValueError:
            if (len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                         for label in host.split('.'))):
                raise ValueError()
        return 'https://' + host + (':' + str(parts.port) if parts.port is not None else '')
    except (ValueError, UnicodeError):
        raise CatalogError(INVALID) from None


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{16,4096}', value.strip()):
        raise CatalogError(INVALID)
    return value.strip()


def _text(value, maximum=200):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(c in value for c in '<>') or any(unicodedata.category(c).startswith('C') for c in value)
            or re.search(r'https?://|www\.', value, re.I)):
        return None
    return value.strip()


def validate_query(query, media_type):
    if not isinstance(query, str) or not isinstance(media_type, str) or media_type not in ('all', 'movie', 'tv'):
        raise CatalogError(INVALID)
    query = query.strip()
    if query and (not 2 <= len(query) <= 80 or _text(query, 80) is None):
        raise CatalogError(INVALID)
    return query, media_type


def _integer(value, minimum=0, maximum=100000):
    if type(value) is int and minimum <= value <= maximum:
        return value
    if isinstance(value, str) and re.fullmatch(r'\d{1,6}', value) and minimum <= int(value) <= maximum:
        return int(value)
    return None


def _poster(row):
    for value in (row.get('poster'), row.get('poster_path')):
        if not isinstance(value, str):
            continue
        # NextFind's verified OpenAPI also emits /api/tmdb/image/<TMDB filename>.
        # Extract that filename; never send the private proxy URL to visitors.
        match = re.fullmatch(r'(?:https://image\.tmdb\.org/t/p/(?:w\d{2,4}|original)|/api/tmdb/image)?(/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp))', value)
        if match:
            return POSTER_PREFIX + match[1]
    return None


def parse_items(payload, mode, media_type):
    if (not isinstance(payload, dict) or payload.get('status') not in ('success', 'ok')
            or not isinstance(payload.get('data'), list) or len(payload['data']) > 10000):
        raise CatalogUnavailable(UNAVAILABLE)
    items, seen = [], set()
    for row in payload['data']:
        if not isinstance(row, dict):
            continue
        kind = row.get('media_type') if mode == 'subscriptions' else row.get('raw_type')
        if kind not in ('movie', 'tv') or media_type != 'all' and kind != media_type:
            continue
        identifier = row.get('tmdb_id') if mode == 'subscriptions' else row.get('id')
        if type(identifier) is int:
            identifier = str(identifier)
        title = _text(row.get('title') or row.get('name'))
        if (not isinstance(identifier, str) or not re.fullmatch(r'[1-9][0-9]{0,11}', identifier)
                or not title or (kind, identifier) in seen):
            continue
        seen.add((kind, identifier))
        items.append({'id': identifier, 'type': kind, 'title': title, 'poster_url': _poster(row),
                      'year': _integer(row.get('year'), 1888, 2200),
                      'in_library': row.get('is_in_library') is True,
                      'subscribed': mode == 'subscriptions' or row.get('is_subscribed') is True,
                      'local_episodes': _integer(row.get('local_episodes')),
                      'total_episodes': _integer(row.get('total_episodes'))})
        if len(items) == MAX_ITEMS:
            break
    return items


def _project_items(items):
    """Recheck the public boundary even for injected/test transport adapters."""
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise CatalogUnavailable(UNAVAILABLE)
    output = []
    for row in items:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not re.fullmatch(r'[1-9][0-9]{0,11}', row['id'])
                or row.get('type') not in ('movie', 'tv') or not _text(row.get('title'))):
            raise CatalogUnavailable(UNAVAILABLE)
        poster = row.get('poster_url')
        if poster is not None and (not isinstance(poster, str)
                or not re.fullmatch(re.escape(POSTER_PREFIX) + r'/[A-Za-z0-9_-]{1,160}\.(?:jpg|png|webp)', poster)):
            poster = None
        output.append({'id': row['id'], 'type': row['type'], 'title': _text(row['title']),
                       'poster_url': poster, 'year': _integer(row.get('year'), 1888, 2200),
                       'in_library': row.get('in_library') is True, 'subscribed': row.get('subscribed') is True,
                       'local_episodes': _integer(row.get('local_episodes')),
                       'total_episodes': _integer(row.get('total_episodes'))})
    return output


def fetch_items(origin, token, query, media_type):
    """Only the two verified GET paths. No redirects, arbitrary URL or mutations."""
    origin = base_url(origin)
    token = _token(token)
    query, media_type = validate_query(query, media_type)
    mode = 'search' if query else 'subscriptions'
    endpoint = '/api/openapi/subscriptions'
    if query:
        parameters = {'query': query}
        if media_type != 'all':
            parameters['type'] = {'movie': '电影', 'tv': '剧集'}[media_type]
        endpoint = '/api/openapi/search?' + urlencode(parameters)
    parts = urlsplit(origin)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=TIMEOUT_SECONDS,
                                              context=ssl.create_default_context())
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        connection.request('GET', endpoint, headers={'X-API-Key': token, 'Accept': 'application/json',
                                                     'User-Agent': 'MediaDock-Discovery/1.0'})
        request_socket = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if request_socket is not None:
            request_socket.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            raise CatalogUnavailable(UNAVAILABLE)
        chunks, total = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            # http.client clears connection.sock for Connection: close responses;
            # the response file still owns this socket until body reading ends.
            if request_socket is not None:
                request_socket.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise CatalogUnavailable(UNAVAILABLE)
            chunks.append(chunk)
        return parse_items(json.loads(b''.join(chunks).decode('utf-8')), mode, media_type)
    except Exception:
        raise CatalogUnavailable(UNAVAILABLE) from None
    finally:
        connection.close()


def _read_config(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            data = source.read(2049)
        if len(data) > 2048:
            raise ValueError()
        saved = json.loads(data)
        if not isinstance(saved, dict):
            raise ValueError()
        origin = base_url(saved['base_url']) if saved.get('base_url') else ''
        return {'base_url': origin, 'enabled': saved.get('enabled') is True}
    except (OSError, ValueError, KeyError, UnicodeError):
        return {'base_url': '', 'enabled': False}


def _atomic_config(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(value, target, separators=(',', ':'), allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class NextFindCatalog:
    def __init__(self, data_dir, vault, *, now=time.time, fetch=fetch_items):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / 'nextfind-catalog-config.json'
        self.vault, self.now, self.fetch = vault, now, fetch
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._pending = {}
        self._identity = None
        self._poster_paths = {}
        self._poster_cache = {}

    @contextmanager
    def _config_lock(self, *, exclusive=True):
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.data_dir / 'nextfind-catalog.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            os.close(descriptor)

    def _snapshot(self):
        # Readers and the seed CLI share a file lock: a URL must never be paired
        # with another configuration's credential during a cross-process update.
        with self._lock:
            if not self.config_path.exists():
                return {'base_url': '', 'enabled': False}, self.vault.get(SECRET_NAME)
            with self._config_lock(exclusive=False):
                return _read_config(self.config_path), self.vault.get(SECRET_NAME)

    def config(self):
        saved, token = self._snapshot()
        return {**saved, 'token_configured': bool(token)}

    def save_config(self, body):
        if (not isinstance(body, dict) or not body or not set(body) <= {'base_url', 'enabled', 'token', 'clear_token'}
                or len(json.dumps(body)) > 8192
                or ('enabled' in body and type(body['enabled']) is not bool)
                or ('clear_token' in body and type(body['clear_token']) is not bool)
                or ('token' in body and not isinstance(body['token'], str))):
            raise CatalogError(INVALID)
        token = _token(body['token']) if body.get('token', '').strip() else None
        clear = body.get('clear_token', False)
        if token and clear:
            raise CatalogError(INVALID)
        if 'base_url' in body:
            origin = base_url(body['base_url']) if body['base_url'] else ''
            if not isinstance(body['base_url'], str):
                raise CatalogError(INVALID)
        with self._lock, self._config_lock():
            saved = _read_config(self.config_path)
            previous = dict(saved)
            saved.update({key: body[key] for key in ('enabled',) if key in body})
            if 'base_url' in body:
                saved['base_url'] = origin
            if clear:
                saved['enabled'] = False
            available = token or (None if clear else self.vault.get(SECRET_NAME))
            if saved['enabled'] and (not saved['base_url'] or not available):
                raise CatalogError(INVALID)
            if token or clear:
                # Credential stores cannot transact with the JSON file. If a
                # process stops or a write fails mid-update, remain disabled.
                _atomic_config(self.config_path, {**previous, 'enabled': False})
            if token:
                self.vault.set(SECRET_NAME, token)
            if clear:
                self.vault.delete(SECRET_NAME)
            _atomic_config(self.config_path, saved)
            self._cache.clear()
            self._identity = None
        return self.config()

    def _settings(self):
        saved, token = self._snapshot()
        identity = (saved['base_url'], hashlib.sha256(token.encode()).hexdigest()) if saved['enabled'] and saved['base_url'] and token else None
        if identity != self._identity:
            self._cache.clear()
            self._identity = identity
        return saved, token, identity

    def _localize_posters(self, items):
        output = []
        for item in items:
            row = copy.deepcopy(item)
            poster = row.get('poster_url')
            if isinstance(poster, str) and poster.startswith(POSTER_PREFIX):
                path = poster[len(POSTER_PREFIX):]
                if POSTER_PATH.fullmatch(path):
                    opaque = hashlib.sha256(path.encode('ascii')).hexdigest() + '.' + path.rsplit('.', 1)[1].lower()
                    self._poster_paths[opaque] = path
                    row['poster_url'] = '/api/public/discovery/poster/' + opaque
                else:
                    row['poster_url'] = None
            output.append(row)
        return output

    def poster(self, opaque):
        if not isinstance(opaque, str) or not POSTER_OPAQUE.fullmatch(opaque):
            raise CatalogUnavailable(UNAVAILABLE)
        with self._lock:
            path = self._poster_paths.get(opaque)
            cached = self._poster_cache.get(opaque)
        if not path:
            raise CatalogUnavailable(UNAVAILABLE)
        if cached:
            return cached
        connection = http.client.HTTPSConnection('image.tmdb.org', timeout=TIMEOUT_SECONDS, context=ssl.create_default_context())
        try:
            connection.request('GET', '/t/p/w500' + path, headers={'Accept': 'image/jpeg,image/png,image/webp', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
                raise CatalogUnavailable(UNAVAILABLE)
            body = response.read(MAX_IMAGE_BYTES + 1)
            if not body or len(body) > MAX_IMAGE_BYTES:
                raise CatalogUnavailable(UNAVAILABLE)
            content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
                raise CatalogUnavailable(UNAVAILABLE)
            with self._lock:
                self._poster_cache[opaque] = (body, content_type)
                while len(self._poster_cache) > MAX_CACHE_KEYS:
                    self._poster_cache.pop(next(iter(self._poster_cache)))
            return body, content_type
        except CatalogUnavailable:
            raise
        except Exception:
            raise CatalogUnavailable(UNAVAILABLE) from None
        finally:
            connection.close()

    @staticmethod
    def _response(entry, mode, stale=False):
        return {'configured': True, 'items': copy.deepcopy(entry['items']), 'stale': stale,
                'updated_at': dt.datetime.fromtimestamp(entry['updated_at'], dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
                'mode': mode}

    def public(self, query='', media_type='all'):
        query, media_type = validate_query(query, media_type)
        mode = 'search'
        cache_key = (query, media_type)
        with self._lock:
            saved, token, identity = self._settings()
            if identity is None or not query:
                # Discovery is search-only. Never expose existing subscriptions,
                # including a fresh or stale empty-query cache from older code.
                return {'configured': identity is not None, 'items': [], 'stale': False, 'updated_at': None, 'mode': mode}
            entry = self._cache.get(cache_key)
            if entry is not None:
                self._cache.move_to_end(cache_key)
                if self.now() < entry['retry_at']:
                    if entry['items'] is None:
                        raise CatalogUnavailable(UNAVAILABLE)
                    result = self._response(entry, mode, entry['failed'])
                    result['items'] = self._localize_posters(result['items'])
                    return result
            pending_key = (identity, cache_key)
            waiter = self._pending.get(pending_key)
            if waiter is None:
                if len(self._pending) >= MAX_INFLIGHT:
                    if entry is not None and entry['items'] is not None:
                        result = self._response(entry, mode, True)
                        result['items'] = self._localize_posters(result['items'])
                        return result
                    raise CatalogUnavailable(UNAVAILABLE)
                self._pending[pending_key] = threading.Event()
        if waiter is not None:
            if not waiter.wait(TIMEOUT_SECONDS + 1):
                raise CatalogUnavailable(UNAVAILABLE)
            return self.public(query, media_type)
        try:
            items = _project_items(self.fetch(saved['base_url'], token, query, media_type))
            items = [row for row in items if media_type == 'all' or row['type'] == media_type]
            good = True
        except Exception:
            items, good = None, False
        with self._lock:
            try:
                _, _, current = self._settings()
                if current != identity:
                    raise CatalogUnavailable(UNAVAILABLE)
                now = self.now()
                if good:
                    entry = {'items': items, 'updated_at': now, 'retry_at': now + CACHE_SECONDS, 'failed': False}
                else:
                    entry = self._cache.get(cache_key) or {'items': None, 'updated_at': None}
                    entry = {**entry, 'retry_at': now + FAILURE_RETRY_SECONDS, 'failed': True}
                self._cache[cache_key] = entry
                self._cache.move_to_end(cache_key)
                while len(self._cache) > MAX_CACHE_KEYS:
                    self._cache.popitem(last=False)
                if entry['items'] is None:
                    raise CatalogUnavailable(UNAVAILABLE)
                result = self._response(entry, mode, not good)
                result['items'] = self._localize_posters(result['items'])
                return result
            finally:
                self._pending.pop(pending_key).set()


def install_routes(app, vault, admin, rate, data_dir):
    if hasattr(app.state, 'nextfind_catalog'):
        return app.state.nextfind_catalog
    service = NextFindCatalog(data_dir, vault)
    app.state.nextfind_catalog = service

    def error(message, status, code):
        return JSONResponse({'error': {'message': message, 'code': code}}, status_code=status)

    @app.get('/api/admin/nextfind-catalog')
    def configuration(request: Request):
        admin(request)
        return service.config()

    @app.post('/api/admin/nextfind-catalog')
    def save_configuration(request: Request, body: object = Body(...)):
        admin(request)
        rate(request, 'nextfind_config', 6, 60)
        try:
            return service.save_config(body)
        except CatalogError:
            return error(INVALID, 400, 'invalid_nextfind_config')
        except OSError:
            return error('目录配置暂时无法保存，请稍后重试。', 503, 'nextfind_config_unavailable')

    @app.get('/api/public/discovery')
    def discovery(request: Request):
        rate(request, 'nextfind_read', 30, 60)
        try:
            parameters = request.query_params
            if not set(parameters) <= {'query', 'type'} or any(len(parameters.getlist(key)) != 1 for key in parameters):
                raise CatalogError(INVALID)
            result = service.public(parameters.get('query', ''), parameters.get('type', 'all'))
            requests = getattr(app.state, 'subscription_requests', None)
            return requests.decorate_catalog(result) if requests else result
        except CatalogUnavailable:
            return error(UNAVAILABLE, 503, 'nextfind_unavailable')
        except CatalogError:
            return error(INVALID, 400, 'invalid_discovery_query')

    @app.get('/api/public/discovery/poster/{opaque}')
    def discovery_poster(request: Request, opaque: str):
        rate(request, 'public_discovery_poster', 240, 60)
        try:
            body, content_type = service.poster(opaque)
            return Response(body, media_type=content_type, headers={'Cache-Control': 'public, max-age=604800, immutable', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})
        except CatalogUnavailable:
            return error(UNAVAILABLE, 404, 'discovery_poster_unavailable')

    return service


def main(argv=None):
    """Seed only non-secret configuration; provision the named secret separately."""
    parser = argparse.ArgumentParser(description='Configure read-only NextFind catalogue (credential stays in SecretStore).')
    parser.add_argument('--base-url')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--enable', action='store_true')
    mode.add_argument('--disable', action='store_true')
    options = parser.parse_args(argv)
    body = {}
    if options.base_url is not None:
        body['base_url'] = options.base_url
    if options.enable or options.disable:
        body['enabled'] = options.enable
    try:
        service = NextFindCatalog(os.environ.get('MEDIADOCK_DATA_DIR') or '/var/lib/mediadock', default_secret_store())
        result = service.save_config(body)
        print('NextFind catalogue configuration saved; enabled=' + str(result['enabled']).lower())
        return 0
    except Exception:
        print('NextFind catalogue configuration was not saved; check URL and SecretStore credential.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
