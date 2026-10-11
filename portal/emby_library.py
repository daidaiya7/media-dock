"""Read-only, explicitly selected Emby libraries with private server-side credentials.

Official contracts: dev.emby.media/reference/RestAPI/ItemsService/getItems.html
and ImageService/getItemsByIdImagesByType.html. Caches are bounded, per-process;
the deployed portal uses one worker. No playback, user, or media-source endpoints.
"""
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import datetime as dt
import hashlib
import hmac
from html import unescape
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import Body, Request
from fastapi.responses import JSONResponse, Response
from secrets_store import SecretStoreError


SECRET_NAME = 'emby-library'
DEFAULT_URL = 'https://emby.example.com'
PAGE_SIZE = 24
MAX_LIBRARIES = 128
MAX_CONFIG_BYTES = 32 * 1024
CACHE_SECONDS = 300
STALE_SECONDS = 1800
MAX_PAGE_KEYS = 128
MAX_AUTHORIZED_ITEMS = MAX_PAGE_KEYS * PAGE_SIZE
MAX_IMAGE_KEYS = 128
MAX_IMAGE_CACHE_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 2 * 1024 * 1024
SEARCH_PAGE_SIZE = 6
SEARCH_WORKERS = 4
MAX_SEARCH_KEYS = 16
MAX_SEARCH_CACHE_BYTES = 16 * 1024 * 1024
TIMEOUT_SECONDS = 12
ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
OPAQUE_PATTERN = re.compile(r'[a-f0-9]{64}\Z')
ERROR_MESSAGE = '片库暂时无法读取，请稍后重试或联系管理员检查连接。'


class EmbyLibraryError(ValueError):
    def __init__(self, message=ERROR_MESSAGE, status=503, code='library_unavailable'):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def invalid(message='片库配置格式不正确'):
    return EmbyLibraryError(message, 400, 'invalid_library_config')


def normalize_base_url(value):
    if (not isinstance(value, str) or len(value) > 500 or not value.startswith('https://')
            or any(char.isspace() or unicodedata.category(char).startswith('C') for char in value)
            or any(char in value for char in '\\?#')):
        raise invalid('请填写不含账号、密码或查询参数的 HTTPS 服务根地址')
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
        if url.scheme != 'https' or not host or url.username or url.password or (port is not None and not 1 <= port <= 65535):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
            host = f'[{address}]' if address.version == 6 else str(address)
        except ValueError:
            host = host.encode('idna').decode('ascii').lower()
            if len(host) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in host.split('.')):
                raise ValueError()
        prefix = url.path.rstrip('/')
        if prefix and (not prefix.startswith('/') or any(not re.fullmatch(r'[A-Za-z0-9_~.-]+', part) or part in {'.', '..'} for part in prefix[1:].split('/'))):
            raise ValueError()
        return urlunsplit(('https', host + (f':{port}' if port and port != 443 else ''), prefix, '', ''))
    except (ValueError, UnicodeError):
        raise invalid('请填写有效的 HTTPS 服务根地址') from None


def _token(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._~+/=-]{16,4096}', value.strip()):
        raise invalid('请填写有效的 Emby API Key 或访问令牌')
    return value.strip()


def _library_ids(value):
    if (not isinstance(value, list) or len(value) > MAX_LIBRARIES
            or any(not isinstance(item, str) or not ID_PATTERN.fullmatch(item) for item in value)
            or len(set(value)) != len(value)):
        raise invalid(f'请选择最多 {MAX_LIBRARIES} 个不重复的媒体库')
    return list(value)


def _read_config(path):
    fallback = {'base_url': DEFAULT_URL, 'enabled': False, 'library_ids': [], 'generation': 'unconfigured', 'token_hash': ''}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as source:
            raw = source.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            return fallback
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool or not re.fullmatch(r'[a-f0-9]{32}', data.get('generation', '')):
            return fallback
        token_hash = data.get('token_hash', '')
        if not isinstance(token_hash, str) or (token_hash and not OPAQUE_PATTERN.fullmatch(token_hash)):
            return fallback
        return {'base_url': normalize_base_url(data['base_url']), 'enabled': data['enabled'],
                'library_ids': _library_ids(data['library_ids']), 'generation': data['generation'], 'token_hash': token_hash}
    except (OSError, ValueError, KeyError, TypeError):
        return fallback


def _atomic_config(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.emby-library-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(value, target, ensure_ascii=False, separators=(',', ':'))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def fetch_upstream(base_url, token, path, params=None, *, image=False, deadline=None):
    """GET only, one configured HTTPS origin, verified TLS, no redirects or proxy env."""
    base_url, token = normalize_base_url(base_url), _token(token)
    if path not in {'/Items', '/Library/VirtualFolders'} and not re.fullmatch(r'/Items/[A-Za-z0-9_-]{1,128}/Images/Primary', path):
        raise EmbyLibraryError()
    parsed = urlsplit(base_url)
    deadline = deadline if deadline is not None else time.monotonic() + TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EmbyLibraryError()
    connection = http.client.HTTPSConnection(parsed.hostname, port=parsed.port or 443,
        timeout=min(TIMEOUT_SECONDS, remaining), context=ssl.create_default_context())
    limit = MAX_IMAGE_BYTES if image else MAX_JSON_BYTES
    try:
        target = parsed.path + path + ('?' + urlencode(params) if params else '')
        connection.request('GET', target, headers={'X-Emby-Token': token,
            'Accept': 'image/jpeg,image/png,image/webp' if image else 'application/json',
            'Accept-Encoding': 'identity', 'User-Agent': 'MediaDock-Library/1.0'})
        # getresponse() may detach connection.sock on Connection: close while
        # the response is still reading from that same socket.
        request_socket = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if request_socket is not None:
            request_socket.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise EmbyLibraryError()
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdigit() or int(length) > limit):
            raise EmbyLibraryError()
        content_type = response.getheader('Content-Type', '').split(';', 1)[0].strip().lower()
        if image and content_type not in {'image/jpeg', 'image/png', 'image/webp'}:
            raise EmbyLibraryError()
        if not image and content_type not in {'application/json', 'text/json'}:
            raise EmbyLibraryError()
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if request_socket is not None:
                request_socket.settimeout(remaining)
            chunk = response.read1(min(65536, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise EmbyLibraryError()
            chunks.append(chunk)
        body = b''.join(chunks)
        if image:
            _validate_image(body, content_type)
            return body, content_type
        return json.loads(body.decode('utf-8'))
    except Exception:
        raise EmbyLibraryError() from None
    finally:
        connection.close()


def _validate_image(body, content_type):
    valid = isinstance(body, bytes) and 0 < len(body) <= MAX_IMAGE_BYTES
    signatures = {'image/jpeg': body.startswith(b'\xff\xd8\xff') if isinstance(body, bytes) else False,
                  'image/png': body.startswith(b'\x89PNG\r\n\x1a\n') if isinstance(body, bytes) else False,
                  'image/webp': isinstance(body, bytes) and body[:4] == b'RIFF' and body[8:12] == b'WEBP'}
    if not valid or not signatures.get(content_type):
        raise EmbyLibraryError('海报暂时无法读取', 502, 'poster_unavailable')


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def _text(value, maximum, redactions=()):
    if not isinstance(value, str):
        return ''
    parser = _PlainText()
    parser.feed(value[:max(maximum * 4, 1000)])
    text = ' '.join(''.join(parser.parts).split())
    text = ''.join(char for char in text if not unicodedata.category(char).startswith('C'))
    for secret in redactions:
        if secret:
            text = re.sub(re.escape(secret), '…', text, flags=re.IGNORECASE)
    return unescape(text)[:maximum]


class EmbyLibrary:
    def __init__(self, data_dir, vault, *, now=time.time, fetch=fetch_upstream):
        self.config_path = Path(data_dir) / 'emby-library-config.json'
        self.vault, self.now, self.fetch = vault, now, fetch
        self._lock = threading.RLock()
        self._upstream_lock = threading.Lock()
        self._search_lock = threading.Lock()
        self._search_pool = None
        self._generation = None
        self._pages, self._items, self._images = OrderedDict(), OrderedDict(), OrderedDict()
        self._directory = None
        self._image_bytes = 0
        self._searches, self._search_bytes = OrderedDict(), 0

    def _config_locked(self):
        config = _read_config(self.config_path)
        if config['generation'] != self._generation:
            self._generation = config['generation']
            self._pages.clear(); self._items.clear(); self._images.clear()
            self._directory = None
            self._image_bytes = 0
            self._searches.clear(); self._search_bytes = 0
        return config

    @staticmethod
    def _enabled(config, token):
        return bool(config['enabled'] and config['library_ids'] and token)

    def _bound_token(self, config):
        value = self.vault.get(SECRET_NAME)
        if not isinstance(value, str) or not config['token_hash']:
            return None
        if not hmac.compare_digest(hashlib.sha256(value.encode()).hexdigest(), config['token_hash']):
            return None
        try:
            return _token(value)
        except EmbyLibraryError:
            return None

    def config(self):
        with self._lock:
            config = self._config_locked()
            return {key: copy.deepcopy(config[key]) for key in ('base_url', 'enabled', 'library_ids')} | {'token_configured': bool(self._bound_token(config))}

    def _library_list(self, config, token, *, deadline=None):
        if not token:
            raise invalid('请先保存 Emby 地址与 API 凭据，再读取媒体库')
        if deadline is not None and time.monotonic() >= deadline:
            raise EmbyLibraryError()
        payload = self.fetch(config['base_url'], token, '/Library/VirtualFolders', deadline=deadline)
        if deadline is not None and time.monotonic() >= deadline:
            raise EmbyLibraryError()
        if not isinstance(payload, list) or len(payload) > 512:
            raise EmbyLibraryError()
        result, seen = [], set()
        for row in payload:
            if not isinstance(row, dict):
                continue
            item_id = row.get('ItemId')
            if isinstance(item_id, str) and ID_PATTERN.fullmatch(item_id) and item_id not in seen:
                name = _text(row.get('Name'), 160, (token,))
                if name:
                    result.append({'id': item_id, 'name': name}); seen.add(item_id)
        return result

    def libraries(self):
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
        result = self._library_list(config, token)
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise EmbyLibraryError('配置已更改，请重新读取媒体库', 409, 'library_config_changed')
        return {'libraries': result}

    def save_config(self, body):
        allowed = {'base_url', 'enabled', 'library_ids', 'token', 'clear_token'}
        if (not isinstance(body, dict) or not body or not set(body) <= allowed
                or ('enabled' in body and type(body['enabled']) is not bool)
                or ('clear_token' in body and type(body['clear_token']) is not bool)
                or ('token' in body and not isinstance(body['token'], str))):
            raise invalid()
        supplied = _token(body['token']) if body.get('token', '').strip() else None
        if body.get('clear_token') and supplied:
            raise invalid('清除凭据与填写新凭据不能同时执行')
        with self._lock:
            old = self._config_locked()
            old_token = self._bound_token(old)
        base_url = normalize_base_url(body.get('base_url', old['base_url']))
        base_changed = base_url != old['base_url']
        cleared = body.get('clear_token', False) or (base_changed and not supplied)
        token = supplied or (None if cleared else old_token)
        candidate = {'base_url': base_url, 'enabled': body.get('enabled', False if base_changed else old['enabled']),
                     'library_ids': _library_ids(body.get('library_ids', [] if base_changed else old['library_ids'])),
                     'generation': secrets.token_hex(16),
                     'token_hash': hashlib.sha256(token.encode()).hexdigest() if token else ''}
        if body.get('clear_token'):
            candidate['enabled'] = False
        if candidate['enabled']:
            if not token or not candidate['library_ids']:
                raise invalid('启用前请保存 API 凭据，并明确选择至少一个公开媒体库')
            available = {row['id'] for row in self._library_list(candidate, token)}
            if not set(candidate['library_ids']) <= available:
                raise invalid('所选媒体库不在当前 Emby 根库列表中，请重新选择')
        with self._lock:
            if self._config_locked()['generation'] != old['generation']:
                raise EmbyLibraryError('配置已更改，请刷新后重试', 409, 'library_config_changed')
            try:
                # Persist a disabled, credential-bound generation before changing
                # the vault. A crash or failure cannot pair one origin with a
                # different origin's old/new credential, including after restart.
                _atomic_config(self.config_path, {**candidate, 'enabled': False})
                self._config_locked()
                if token:
                    self.vault.set(SECRET_NAME, token)
                else:
                    self.vault.delete(SECRET_NAME)
                _atomic_config(self.config_path, candidate)
            except Exception:
                self._config_locked()
                raise SecretStoreError('片库配置保存失败，请检查服务端存储权限') from None
            self._config_locked()
            return self.config()

    def _opaque(self, config, raw_id):
        return hmac.new(config['generation'].encode(), raw_id.encode(), hashlib.sha256).hexdigest()

    def _library_opaque(self, config, raw_id):
        # A library link must never also authorize an item/poster with that ID.
        return self._opaque(config, 'library:' + raw_id)

    def _resolve_library(self, config, opaque):
        if isinstance(opaque, str) and OPAQUE_PATTERN.fullmatch(opaque):
            for raw_id in config['library_ids']:
                if hmac.compare_digest(self._library_opaque(config, raw_id), opaque):
                    return raw_id
        raise EmbyLibraryError('媒体库不存在或已更新，请重新选择', 404, 'library_not_found')

    def public_libraries(self):
        empty = {'configured': False, 'libraries': [], 'updated_at': None, 'stale': False}
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
            if not self._enabled(config, token):
                return empty
            entry = self._directory
            if entry and self.now() - entry['created'] < CACHE_SECONDS:
                return copy.deepcopy(entry['data'])
        deadline = time.monotonic() + TIMEOUT_SECONDS
        if not self._upstream_lock.acquire(timeout=TIMEOUT_SECONDS):
            raise EmbyLibraryError()
        try:
            with self._lock:
                config = self._config_locked()
                token = self._bound_token(config)
                if not self._enabled(config, token):
                    return empty
                entry = self._directory
                if entry and self.now() - entry['created'] < CACHE_SECONDS:
                    return copy.deepcopy(entry['data'])
            try:
                fresh = self._fetch_directory(config, token, deadline=deadline)
            except Exception:
                with self._lock:
                    current = self._config_locked()
                    if current['generation'] == config['generation'] and entry and self.now() - entry['created'] <= STALE_SECONDS:
                        return copy.deepcopy(entry['data']) | {'stale': True}
                raise EmbyLibraryError() from None
            with self._lock:
                if self._config_locked()['generation'] != config['generation']:
                    raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed')
                self._directory = fresh
                return copy.deepcopy(fresh['data'])
        finally:
            self._upstream_lock.release()

    def _fetch_directory(self, config, token, *, deadline):
        available = {row['id']: row['name'] for row in self._library_list(config, token, deadline=deadline)}
        redactions = (token, config['base_url'], urlsplit(config['base_url']).hostname)
        libraries = []
        for raw_id in config['library_ids']:
            if raw_id in available:
                opaque = self._library_opaque(config, raw_id)
                libraries.append({'id': opaque, 'name': _text(available[raw_id], 160, redactions),
                                  'cover_url': '/api/public/library/cover/' + opaque})
        timestamp = self.now()
        return {'created': timestamp, 'data': {'configured': True, 'libraries': libraries,
                'updated_at': self._timestamp(timestamp), 'stale': False}}

    def _item(self, row, config, token):
        if not isinstance(row, dict) or row.get('Type') not in {'Movie', 'Series'}:
            return None
        raw_id = row.get('Id')
        if not isinstance(raw_id, str) or not ID_PATTERN.fullmatch(raw_id):
            return None
        redactions = (token, config['base_url'], urlsplit(config['base_url']).hostname)
        title = _text(row.get('Name'), 240, redactions)
        if not title:
            return None
        opaque = self._opaque(config, raw_id)
        year = row.get('ProductionYear')
        genres = row.get('Genres') if isinstance(row.get('Genres'), list) else []
        return {'id': opaque, 'title': title, 'type': 'movie' if row['Type'] == 'Movie' else 'tv',
                'year': year if type(year) is int and 1800 <= year <= 2200 else None,
                'overview': _text(row.get('Overview'), 2400, redactions),
                'genres': [name for name in (_text(value, 60, redactions) for value in genres[:12]) if name],
                'poster_url': '/api/public/library/poster/' + opaque}, raw_id

    @staticmethod
    def _timestamp(timestamp):
        return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

    def _fetch_page(self, config, token, library_id, query, media_type, page, sort, *, deadline=None, page_size=PAGE_SIZE):
        offset, limit = (page - 1) * page_size, page_size + 1
        deadline = deadline if deadline is not None else time.monotonic() + TIMEOUT_SECONDS
        if time.monotonic() >= deadline:
            raise EmbyLibraryError()
        sort_by, sort_order = {'name': ('SortName', 'Ascending'), 'newest': ('DateCreated', 'Descending'),
                              'year': ('ProductionYear,SortName', 'Descending')}[sort]
        params = {'ParentId': library_id, 'IncludeItemTypes': {'all': 'Movie,Series', 'movie': 'Movie', 'tv': 'Series'}[media_type],
                  'Recursive': 'true', 'Fields': 'Overview,Genres,ProductionYear', 'EnableUserData': 'false',
                  'SortBy': sort_by, 'SortOrder': sort_order, 'StartIndex': offset, 'Limit': limit}
        if query:
            params['SearchTerm'] = query
        payload = self.fetch(config['base_url'], token, '/Items', params, deadline=deadline)
        if time.monotonic() >= deadline:
            raise EmbyLibraryError()
        total = payload.get('TotalRecordCount') if isinstance(payload, dict) else None
        rows = payload.get('Items') if isinstance(payload, dict) else None
        has_total = isinstance(payload, dict) and 'TotalRecordCount' in payload
        # Some Emby searches return real Items with TotalRecordCount=0 when
        # counting is disabled. The bounded look-ahead determines pagination;
        # never discard returned matches because that optional count differs.
        if ((has_total and (type(total) is not int or not 0 <= total <= 10_000_000))
                or not isinstance(rows, list) or len(rows) > limit):
            raise EmbyLibraryError()
        output, references, seen = [], [], set()
        for row in rows[:page_size]:
            parsed = self._item(row, config, token)
            if parsed and parsed[0]['id'] not in seen and (media_type == 'all' or parsed[0]['type'] == media_type):
                output.append(parsed[0]); references.append((parsed[0]['id'], parsed[1])); seen.add(parsed[0]['id'])
        timestamp = self.now()
        return {'created': timestamp, 'references': references, 'data': {'configured': True, 'items': output,
            'page': page, 'has_more': len(rows) > page_size,
            'updated_at': self._timestamp(timestamp),
            'stale': False}}

    def _remember(self, entry):
        now = self.now()
        for opaque, raw_id in entry['references']:
            self._items[opaque] = (raw_id, now + STALE_SECONDS)
            self._items.move_to_end(opaque)
        while len(self._items) > MAX_AUTHORIZED_ITEMS:
            self._items.popitem(last=False)

    @staticmethod
    def _empty(page):
        return {'configured': False, 'items': [], 'page': page, 'has_more': False, 'updated_at': None, 'stale': False}

    def public(self, query='', media_type='all', page=1, *, library_id='', sort='name'):
        if (not isinstance(query, str) or len(query) > 120
                or any(unicodedata.category(char).startswith('C') for char in query)
                or not isinstance(media_type, str) or media_type not in {'all', 'movie', 'tv'}
                or type(page) is not int or not 1 <= page <= 1000
                or not isinstance(sort, str) or sort not in {'name', 'newest', 'year'}):
            raise EmbyLibraryError('查询条件无效', 400, 'invalid_library_query')
        query = ' '.join(query.split())
        key = (library_id, query, media_type, page, sort)
        # A fresh cached page must not wait behind an unrelated slow search.
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
            if not self._enabled(config, token):
                return self._empty(page)
            if library_id == '':
                raise EmbyLibraryError('请先选择媒体库', 400, 'library_required')
            raw_library_id = self._resolve_library(config, library_id)
            entry = self._pages.get(key)
            if entry and self.now() - entry['created'] < CACHE_SECONDS:
                self._pages.move_to_end(key); self._remember(entry)
                return copy.deepcopy(entry['data'])
        # Queueing is part of the same 12-second budget as every library request.
        deadline = time.monotonic() + TIMEOUT_SECONDS
        if not self._upstream_lock.acquire(timeout=TIMEOUT_SECONDS):
            raise EmbyLibraryError()
        try:
            with self._lock:
                config = self._config_locked()
                token = self._bound_token(config)
                if not self._enabled(config, token):
                    return self._empty(page)
                raw_library_id = self._resolve_library(config, library_id)
                entry = self._pages.get(key)
                if entry and self.now() - entry['created'] < CACHE_SECONDS:
                    self._pages.move_to_end(key); self._remember(entry)
                    return copy.deepcopy(entry['data'])
            try:
                fresh = self._fetch_page(config, token, raw_library_id, query, media_type, page, sort, deadline=deadline)
            except Exception:
                with self._lock:
                    current = self._config_locked()
                    if current['generation'] == config['generation'] and entry and self.now() - entry['created'] <= STALE_SECONDS:
                        self._remember(entry)
                        return copy.deepcopy(entry['data']) | {'stale': True}
                raise EmbyLibraryError() from None
            with self._lock:
                if self._config_locked()['generation'] != config['generation']:
                    raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed')
                self._pages[key] = fresh; self._pages.move_to_end(key); self._remember(fresh)
                while len(self._pages) > MAX_PAGE_KEYS:
                    self._pages.popitem(last=False)
                return copy.deepcopy(fresh['data'])
        finally:
            self._upstream_lock.release()

    def _search_directory(self, config, token, deadline):
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed')
            entry = self._directory
            if entry and self.now() - entry['created'] < CACHE_SECONDS:
                return entry['data'], False
        try:
            fresh = self._fetch_directory(config, token, deadline=deadline)
        except Exception:
            with self._lock:
                if self._config_locked()['generation'] != config['generation']:
                    raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed') from None
                if entry and self.now() - entry['created'] <= STALE_SECONDS:
                    return entry['data'], True
            raise EmbyLibraryError() from None
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed')
            self._directory = fresh
            return fresh['data'], False

    def _search_one(self, config, token, raw_id, query, deadline):
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise EmbyLibraryError()
        # Workers return data only. They never grant poster access or fill a cache.
        return self._fetch_page(config, token, raw_id, query, 'all', 1, 'name',
                                deadline=deadline, page_size=SEARCH_PAGE_SIZE)

    def _search_selected(self, config, token, query, raw_ids, deadline):
        if self._search_pool is None:
            self._search_pool = ThreadPoolExecutor(max_workers=SEARCH_WORKERS, thread_name_prefix='emby-search')
        remaining, pending, results = iter(raw_ids), {}, {}
        try:
            while time.monotonic() < deadline:
                with self._lock:
                    if self._config_locked()['generation'] != config['generation']:
                        break
                # Do not enqueue all 128 libraries: even after a previous timeout
                # this shared pool has four workers and at most four new tasks.
                while len(pending) < SEARCH_WORKERS and time.monotonic() < deadline:
                    raw_id = next(remaining, None)
                    if raw_id is None:
                        break
                    future = self._search_pool.submit(self._search_one, config, token, raw_id, query, deadline)
                    pending[future] = raw_id
                if not pending:
                    break
                done, _ = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    raw_id = pending.pop(future)
                    try:
                        results[raw_id] = future.result()
                    except Exception:
                        # Individual failures remain absent so partial cannot be
                        # mistaken for an authoritative empty result.
                        pass
            return results
        finally:
            for future in pending:
                future.cancel()

    def search(self, query):
        if (not isinstance(query, str) or len(query) > 80
                or any(unicodedata.category(char).startswith('C') for char in query)):
            raise EmbyLibraryError('搜索词请输入 2–80 个字', 400, 'invalid_library_query')
        query = ' '.join(query.split())
        if len(query) < 2:
            raise EmbyLibraryError('搜索词请输入 2–80 个字', 400, 'invalid_library_query')
        empty = {'configured': False, 'groups': [], 'updated_at': None, 'stale': False, 'partial': False}
        deadline = time.monotonic() + TIMEOUT_SECONDS
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
            if not self._enabled(config, token):
                return empty
            entry = self._searches.get(query)
            if entry and self.now() - entry['created'] < CACHE_SECONDS:
                self._searches.move_to_end(query); self._remember(entry)
                return copy.deepcopy(entry['data'])
        # One fan-out at a time; waiting consumes the same total request budget.
        if not self._search_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise EmbyLibraryError()
        try:
            with self._lock:
                config = self._config_locked()
                token = self._bound_token(config)
                if not self._enabled(config, token):
                    return empty
                entry = self._searches.get(query)
                if entry and self.now() - entry['created'] < CACHE_SECONDS:
                    self._searches.move_to_end(query); self._remember(entry)
                    return copy.deepcopy(entry['data'])
            directory, stale = self._search_directory(config, token, deadline)
            names = {row['id']: row['name'] for row in directory['libraries']}
            selected = [raw_id for raw_id in config['library_ids'] if self._library_opaque(config, raw_id) in names]
            results = self._search_selected(config, token, query, selected, deadline)
            with self._lock:
                if self._config_locked()['generation'] != config['generation']:
                    raise EmbyLibraryError('片库配置已更新，请重新加载', 409, 'library_config_changed')
                if not results:
                    raise EmbyLibraryError()
                groups, references = [], []
                for raw_id in config['library_ids']:
                    page = results.get(raw_id)
                    if page and page['data']['items']:
                        opaque = self._library_opaque(config, raw_id)
                        groups.append({'library': {'id': opaque, 'name': names[opaque]},
                                       'items': page['data']['items'], 'has_more': page['data']['has_more']})
                        references.extend(page['references'])
                timestamp = self.now()
                result = {'configured': True, 'groups': groups, 'updated_at': self._timestamp(timestamp),
                          'stale': stale, 'partial': len(results) != len(config['library_ids'])}
                fresh = {'created': timestamp, 'references': references, 'data': result}
                self._remember(fresh)
                # Partial/failed searches are never cached as empty or complete.
                if not result['partial'] and not stale:
                    fresh['size'] = len(json.dumps(fresh, ensure_ascii=False, separators=(',', ':')).encode())
                    previous = self._searches.pop(query, None)
                    if previous:
                        self._search_bytes -= previous['size']
                    self._searches[query] = fresh
                    self._search_bytes += fresh['size']
                    while len(self._searches) > MAX_SEARCH_KEYS or self._search_bytes > MAX_SEARCH_CACHE_BYTES:
                        self._search_bytes -= self._searches.popitem(last=False)[1]['size']
                return copy.deepcopy(result)
        finally:
            self._search_lock.release()

    def poster(self, opaque):
        unavailable = EmbyLibraryError('海报不存在或已过期', 404, 'poster_not_found')
        if not isinstance(opaque, str) or not OPAQUE_PATTERN.fullmatch(opaque):
            raise unavailable
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
            reference = self._items.get(opaque)
            if not self._enabled(config, token) or not reference or reference[1] < self.now():
                self._items.pop(opaque, None)
                raise unavailable
        return self._image(config, token, reference[0], opaque, unavailable,
                           EmbyLibraryError('海报暂时无法读取', 502, 'poster_unavailable'))

    def cover(self, opaque):
        unavailable = EmbyLibraryError('媒体库封面不存在或已过期', 404, 'library_cover_not_found')
        with self._lock:
            config = self._config_locked()
            token = self._bound_token(config)
            if not self._enabled(config, token):
                raise unavailable
            try:
                raw_id = self._resolve_library(config, opaque)
            except EmbyLibraryError:
                raise unavailable from None
        return self._image(config, token, raw_id, 'cover:' + opaque, unavailable, unavailable, versioned=True)

    def _image(self, config, token, raw_id, key, unavailable, fetch_error, *, versioned=False):
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise unavailable
            cached = self._images.get(key)
            if cached and self.now() - cached[0] < CACHE_SECONDS:
                self._images.move_to_end(key)
                return cached[1], cached[2]
        try:
            params = {'MaxWidth': 500, 'Quality': 85}
            deadline = time.monotonic() + TIMEOUT_SECONDS
            if versioned:
                # The image CDN caches stable URLs. Re-read Emby's image version
                # whenever our bounded image cache expires; never reuse an old URL.
                revision = str(int(self.now() // CACHE_SECONDS))
                metadata = self.fetch(config['base_url'], token, '/Items',
                    {'Ids': raw_id, 'Fields': 'ImageTags', 'EnableImages': 'true',
                     'EnableUserData': 'false', '_mediadock_revision': revision}, deadline=deadline)
                rows = metadata.get('Items') if isinstance(metadata, dict) else None
                if not isinstance(rows, list):
                    raise unavailable
                row = next((row for row in rows if isinstance(row, dict) and row.get('Id') == raw_id), None)
                tags = row.get('ImageTags') if row else None
                tag = tags.get('Primary') if isinstance(tags, dict) else None
                if not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', tag):
                    raise unavailable
                params['Tag'] = tag
            body, content_type = self.fetch(config['base_url'], token,
                '/Items/' + raw_id + '/Images/Primary', params, image=True, deadline=deadline)
            _validate_image(body, content_type)
        except Exception:
            raise fetch_error from None
        with self._lock:
            if self._config_locked()['generation'] != config['generation']:
                raise unavailable
            previous = self._images.pop(key, None)
            if previous:
                self._image_bytes -= len(previous[1])
            self._images[key] = (self.now(), body, content_type)
            self._image_bytes += len(body)
            while len(self._images) > MAX_IMAGE_KEYS or self._image_bytes > MAX_IMAGE_CACHE_BYTES:
                self._image_bytes -= len(self._images.popitem(last=False)[1][1])
        return body, content_type


def install_routes(app, vault, admin, rate, data_dir):
    service = EmbyLibrary(data_dir, vault)
    app.state.emby_library = service
    from emby_stats import install_routes as install_stats_routes
    install_stats_routes(app, service, rate, data_dir)

    @app.exception_handler(EmbyLibraryError)
    async def emby_library_error(_request, error):
        return JSONResponse({'error': {'message': error.message, 'code': error.code}}, status_code=error.status,
                            headers={'Cache-Control': 'no-store'})

    @app.get('/api/admin/emby-library')
    def read_config(request: Request):
        admin(request); rate(request, 'emby_library_admin', maximum=60, window=60)
        return service.config()

    @app.post('/api/admin/emby-library')
    def save_config(request: Request, body: object = Body(...)):
        admin(request)
        origin = request.headers.get('origin')
        if ((origin and origin.rstrip('/') != f'{request.url.scheme}://{request.headers.get("host", "")}')
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            raise EmbyLibraryError('不接受跨站请求', 403, 'invalid_origin')
        rate(request, 'emby_library_config', maximum=6, window=60)
        return service.save_config(body)

    @app.get('/api/admin/emby-library/libraries')
    def read_libraries(request: Request):
        admin(request); rate(request, 'emby_library_inventory', maximum=10, window=60)
        return service.libraries()

    @app.get('/api/public/libraries')
    def read_public_libraries(request: Request):
        rate(request, 'public_libraries', maximum=60, window=60)
        return JSONResponse(service.public_libraries(), headers={'Cache-Control': 'no-store'})

    @app.get('/api/public/library')
    def read_library(request: Request, query: str = '', type: str = 'all', page: int = 1, library: str = '', sort: str = 'name'):
        rate(request, 'public_library', maximum=60, window=60)
        return JSONResponse(service.public(query, type, page, library_id=library, sort=sort), headers={'Cache-Control': 'no-store'})

    @app.get('/api/public/library/search')
    def search_libraries(request: Request, query: str = ''):
        rate(request, 'public_library_search', maximum=20, window=60)
        return JSONResponse(service.search(query), headers={'Cache-Control': 'no-store'})

    @app.get('/api/public/library/cover/{opaque}')
    def read_cover(request: Request, opaque: str):
        rate(request, 'public_library_poster', maximum=240, window=60)
        body, content_type = service.cover(opaque)
        return Response(body, media_type=content_type, headers={'Cache-Control': 'no-store',
                        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})

    @app.get('/api/public/library/poster/{opaque}')
    def read_poster(request: Request, opaque: str):
        rate(request, 'public_library_poster', maximum=240, window=60)
        body, content_type = service.poster(opaque)
        return Response(body, media_type=content_type, headers={'Cache-Control': 'no-store',
                        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})

    return service
