"""Visitor watch requests and explicit, durable administrator approval into NextFind.

Public submissions only write the local queue. A durable per-title delivery record is
committed before every NextFind POST; ambiguous outcomes only permit GET reconciliation.
"""
import base64
from contextlib import contextmanager
import copy
import datetime as dt
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import ssl
import threading
import time
from urllib.parse import urlsplit

from fastapi import Body, Request
from fastapi.responses import JSONResponse

import nextfind_catalog as nf

COOKIE = 'mediadock_want_session'
SESSION_SECONDS = 90 * 86400
CANDIDATE_SECONDS = 1800
MAX_REQUESTS = 10000
MAX_SESSIONS = 20000
MAX_PER_VISITOR = 100
MAX_DAILY_VISITOR = 5
MAX_SUBSCRIPTIONS = 10000
SIGNING_SECRET = 'subscription-candidate-signing'
STATUSES = ('pending', 'approved', 'rejected', 'uncertain')


class RequestError(ValueError):
    def __init__(self, message, code='invalid_subscription_request', status=400):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)


class ProviderReadError(Exception):
    pass


class ProviderWriteUncertain(Exception):
    pass


def _identity(identifier, kind):
    if type(identifier) is int:
        identifier = str(identifier)
    if not isinstance(identifier, str) or not re.fullmatch(r'[1-9][0-9]{0,11}', identifier) or kind not in ('movie', 'tv'):
        raise RequestError('影片信息无效，请刷新影片目录后重试。')
    return identifier, kind


def _note(value):
    if value == '':
        return ''
    clean = nf._text(value, 300)
    if clean is None:
        raise RequestError('备注最多 300 字，请勿填写网址或特殊控制字符。')
    return clean


def _iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _fingerprint(origin, token):
    return hashlib.sha256((origin + '\0' + token).encode()).hexdigest()


def _http_json(origin, token, method, path, body=None):
    """Fixed HTTPS endpoints, bounded bytes/deadline, no redirects and no error echo."""
    parts = urlsplit(nf.base_url(origin))
    token = nf._token(token)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, timeout=nf.TIMEOUT_SECONDS,
                                             context=ssl.create_default_context())
    try:
        deadline = time.monotonic() + nf.TIMEOUT_SECONDS
        headers = {'X-API-Key': token, 'Accept': 'application/json', 'User-Agent': 'MediaDock-Requests/1.0'}
        encoded = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            encoded = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode()
        connection.request(method, path, body=encoded, headers=headers)
        sock = connection.sock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if sock is not None:
            sock.settimeout(remaining)
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError()
        chunks, total = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read1(min(65536, nf.MAX_RESPONSE_BYTES - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > nf.MAX_RESPONSE_BYTES:
                raise ValueError()
            chunks.append(chunk)
        result = json.loads(b''.join(chunks).decode('utf-8'))
        if not isinstance(result, dict) or result.get('status') not in ('success', 'ok'):
            raise ValueError()
        return result
    finally:
        connection.close()


def read_subscriptions(origin, token):
    """Read the complete verified list, never the public catalogue's 24-item projection."""
    try:
        payload = _http_json(origin, token, 'GET', '/api/openapi/subscriptions')
        rows = payload.get('data')
        if not isinstance(rows, list) or len(rows) >= MAX_SUBSCRIPTIONS:
            raise ValueError()
        # Refuse partially enumerated responses if a future provider adds pagination.
        for container in (payload, payload.get('pagination', {}), payload.get('meta', {})):
            if not isinstance(container, dict):
                raise ValueError()
            if container.get('has_more') or container.get('has_next') or container.get('next') or container.get('next_page'):
                raise ValueError()
            if any(field in container for field in ('page', 'page_size', 'limit', 'offset', 'cursor')):
                # This integration only knows the documented unpaginated endpoint.
                # Do not silently treat a future paginated response as complete.
                raise ValueError()
            for field in ('total', 'total_count', 'totalCount'):
                if field in container and (type(container[field]) is not int or container[field] > len(rows)):
                    raise ValueError()
        identities = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError()
            identities.add(_identity(row.get('tmdb_id'), row.get('media_type')))
        return identities
    except Exception:
        raise ProviderReadError() from None


def add_subscription(origin, token, item):
    try:
        _http_json(origin, token, 'POST', '/api/openapi/subscriptions/add',
                   {'tmdb_id': item['tmdb_id'], 'media_type': item['media_type'], 'title': item['title']})
    except Exception:
        # Even an HTTP error can follow a server-side commit. No automatic POST retry.
        raise ProviderWriteUncertain() from None


def same_origin(request):
    try:
        origin = urlsplit(request.headers.get('origin', ''))
        expected = urlsplit(str(request.url))
        if (not origin.scheme or origin.scheme != expected.scheme or origin.netloc.lower() != expected.netloc.lower()
                or origin.path not in ('', '/') or origin.query or origin.fragment or origin.username or origin.password
                or request.headers.get('sec-fetch-site') == 'cross-site'):
            raise ValueError()
        if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
            raise RequestError('请求需要 JSON 格式。', 'invalid_content_type', 415)
    except ValueError as error:
        if isinstance(error, RequestError):
            raise
        raise RequestError('请从本站页面提交操作。', 'invalid_origin', 403) from None


class SubscriptionRequests:
    def __init__(self, data_dir, vault, catalog, *, now=time.time, read=read_subscriptions, add=add_subscription):
        self.data_dir, self.vault, self.catalog = Path(data_dir), vault, catalog
        self.now, self.read, self.add = now, read, add
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.data_dir / 'subscription-requests.sqlite3'
        if self.path.is_symlink():
            raise OSError('Request storage unavailable')
        self._locks = [threading.RLock() for _ in range(64)]
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS visitor_sessions (
                    token_hash TEXT PRIMARY KEY, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watch_requests (
                    id TEXT PRIMARY KEY, visitor_hash TEXT NOT NULL,
                    tmdb_id TEXT NOT NULL, media_type TEXT NOT NULL, title TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                    review_note TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(visitor_hash, tmdb_id, media_type)
                );
                CREATE INDEX IF NOT EXISTS watch_requests_title ON watch_requests(tmdb_id,media_type);
                CREATE INDEX IF NOT EXISTS watch_requests_status ON watch_requests(status,created_at);
                CREATE TABLE IF NOT EXISTS deliveries (
                    tmdb_id TEXT NOT NULL, media_type TEXT NOT NULL, state TEXT NOT NULL,
                    config_id TEXT NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY(tmdb_id,media_type)
                );
                CREATE TABLE IF NOT EXISTS request_audit (
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, event TEXT NOT NULL, happened_at REAL NOT NULL
                );
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self, write=False):
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _settings(self):
        saved, token = self.catalog._snapshot()
        if not saved['enabled'] or not saved['base_url'] or not token:
            raise RequestError('订阅服务尚未开启，请稍后再试。', 'subscriptions_disabled', 503)
        origin, token = nf.base_url(saved['base_url']), nf._token(token)
        return origin, token, _fingerprint(origin, token)

    def configured(self):
        try:
            self._settings()
            return True
        except (RequestError, nf.CatalogError):
            return False

    def decorate_catalog(self, payload):
        result = copy.deepcopy(payload)
        if not isinstance(result, dict) or not result.get('configured') or result.get('stale'):
            return result
        try:
            _, _, config_id = self._settings()
        except (RequestError, nf.CatalogError):
            return result
        secret = self.vault.ensure(SIGNING_SECRET).encode()
        for row in result.get('items', [])[:nf.MAX_ITEMS]:
            identifier, kind = _identity(row.get('id'), row.get('type'))
            title = nf._text(row.get('title'))
            if not title:
                continue
            encoded = base64.urlsafe_b64encode(json.dumps({'id': identifier, 'type': kind, 'title': title,
                'iat': int(self.now()), 'exp': int(self.now()) + CANDIDATE_SECONDS, 'config': config_id},
                ensure_ascii=False, separators=(',', ':')).encode()).rstrip(b'=').decode()
            signature = hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()
            row['request_token'] = encoded + '.' + signature
        return result

    def _candidate(self, token):
        try:
            if not isinstance(token, str) or len(token) > 2048 or not re.fullmatch(r'[A-Za-z0-9_-]+\.[0-9a-f]{64}', token):
                raise ValueError()
            encoded, signature = token.split('.')
            secret = self.vault.get(SIGNING_SECRET)
            if not secret or not hmac.compare_digest(signature, hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()):
                raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
            if not isinstance(data, dict) or set(data) != {'id', 'type', 'title', 'iat', 'exp', 'config'}:
                raise ValueError()
            if (type(data['iat']) is not int or type(data['exp']) is not int or data['iat'] > self.now() + 5
                    or data['exp'] <= self.now() or data['exp'] - data['iat'] != CANDIDATE_SECONDS):
                raise ValueError()
            identifier, kind = _identity(data['id'], data['type'])
            title = nf._text(data['title'])
            if not title:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise RequestError('影片信息已过期，请刷新影片目录后重试。', 'candidate_expired') from None
        if self._settings()[2] != data['config']:
            raise RequestError('影片目录已更新，请刷新页面后重试。', 'candidate_expired')
        return {'tmdb_id': identifier, 'media_type': kind, 'title': title}

    def visitor(self, token, *, create=False):
        valid = isinstance(token, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', token)
        hashed = hashlib.sha256(token.encode()).hexdigest() if valid else None
        with self._db(write=True) as db:
            now = self.now()
            db.execute('DELETE FROM visitor_sessions WHERE expires<=?', (now,))
            row = db.execute('SELECT expires FROM visitor_sessions WHERE token_hash=?', (hashed,)).fetchone() if hashed else None
            if row:
                return hashed, None
            if not create:
                raise RequestError('请先刷新页面再提交申请。', 'visitor_session_required', 401)
            if db.execute('SELECT COUNT(*) FROM visitor_sessions').fetchone()[0] >= MAX_SESSIONS:
                raise RequestError('申请服务繁忙，请稍后重试。', 'subscription_queue_full', 503)
            token = secrets.token_urlsafe(32)
            hashed = hashlib.sha256(token.encode()).hexdigest()
            db.execute('INSERT INTO visitor_sessions VALUES (?,?)', (hashed, now + SESSION_SECONDS))
            return hashed, token

    @staticmethod
    def _project(row):
        output = {key: row[key] for key in ('id', 'tmdb_id', 'media_type', 'title', 'note', 'status', 'review_note')}
        output.update(created_at=_iso(row['created_at']), updated_at=_iso(row['updated_at']),
                      requester_count=row['requester_count'] if 'requester_count' in row.keys() else 1)
        return output

    @staticmethod
    def _select():
        return '''SELECT r.*, (SELECT COUNT(*) FROM watch_requests a
            WHERE a.tmdb_id=r.tmdb_id AND a.media_type=r.media_type) AS requester_count FROM watch_requests r'''

    @staticmethod
    def _audit(db, request_id, event, now):
        # Only state transitions are audited; repeated GET reconciliation does not grow storage.
        db.execute('INSERT INTO request_audit(request_id,event,happened_at) VALUES (?,?,?)', (request_id, event, now))

    def mine(self, visitor_hash):
        with self._db() as db:
            rows = db.execute(self._select() + ' WHERE visitor_hash=? ORDER BY created_at DESC LIMIT ?',
                              (visitor_hash, MAX_PER_VISITOR)).fetchall()
        return {'items': [self._project(row) for row in rows], 'configured': self.configured()}

    def submit(self, visitor_hash, body):
        if not isinstance(body, dict) or not set(body) <= {'request_token', 'note'} or 'request_token' not in body:
            raise RequestError('请从影片目录选择要申请的影片。')
        item, note = self._candidate(body['request_token']), _note(body.get('note', ''))
        with self._db(write=True) as db:
            existing = db.execute(self._select() + ' WHERE visitor_hash=? AND tmdb_id=? AND media_type=?',
                                  (visitor_hash, item['tmdb_id'], item['media_type'])).fetchone()
            if existing:
                return {'request': self._project(existing), 'duplicate': True}
            if db.execute('SELECT COUNT(*) FROM watch_requests').fetchone()[0] >= MAX_REQUESTS:
                raise RequestError('申请队列暂时已满，请稍后重试。', 'subscription_queue_full', 503)
            count = db.execute('SELECT COUNT(*) FROM watch_requests WHERE visitor_hash=?', (visitor_hash,)).fetchone()[0]
            daily = db.execute('SELECT COUNT(*) FROM watch_requests WHERE visitor_hash=? AND created_at>?',
                               (visitor_hash, self.now() - 86400)).fetchone()[0]
            if count >= MAX_PER_VISITOR or daily >= MAX_DAILY_VISITOR:
                raise RequestError('申请数量已达上限，请稍后再试。', 'subscription_limit', 429)
            request_id, now = secrets.token_hex(16), self.now()
            db.execute('''INSERT INTO watch_requests(id,visitor_hash,tmdb_id,media_type,title,note,created_at,updated_at)
                          VALUES (?,?,?,?,?,?,?,?)''',
                       (request_id, visitor_hash, item['tmdb_id'], item['media_type'], item['title'], note, now, now))
            self._audit(db, request_id, 'submitted', now)
            row = db.execute(self._select() + ' WHERE r.id=?', (request_id,)).fetchone()
            return {'request': self._project(row), 'duplicate': False}

    def listing(self, status='pending', page=1):
        if status not in (*STATUSES, 'all') or type(page) is not int or not 1 <= page <= 200:
            raise RequestError('审核列表查询无效。')
        where, values = (' WHERE status=?', [status]) if status != 'all' else ('', [])
        with self._db() as db:
            rows = db.execute(self._select() + where + ' ORDER BY created_at DESC LIMIT 51 OFFSET ?',
                              (*values, (page - 1) * 50)).fetchall()
            counts = {state: 0 for state in STATUSES}
            counts.update({row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) n FROM watch_requests GROUP BY status')})
        return {'items': [self._project(row) for row in rows[:50]], 'page': page, 'has_more': len(rows) > 50, 'counts': counts}

    def _get(self, request_id):
        if not isinstance(request_id, str) or not re.fullmatch(r'[0-9a-f]{32}', request_id):
            raise RequestError('申请不存在。', 'request_not_found', 404)
        with self._db() as db:
            row = db.execute(self._select() + ' WHERE r.id=?', (request_id,)).fetchone()
        if row is None:
            raise RequestError('申请不存在。', 'request_not_found', 404)
        return dict(row)

    @contextmanager
    def _title_lock(self, item):
        stripe = int(hashlib.sha256((item['tmdb_id'] + ':' + item['media_type']).encode()).hexdigest()[:4], 16) % 64
        with self._locks[stripe]:
            descriptor = os.open(self.data_dir / ('subscription-lock-' + str(stripe)),
                                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                os.close(descriptor)

    def _mark(self, item, state, review_note, event, *, config_id=None):
        with self._db(write=True) as db:
            now = self.now()
            rows = db.execute('''SELECT id,status FROM watch_requests WHERE tmdb_id=? AND media_type=?
                                 AND status IN ('pending','uncertain')''', (item['tmdb_id'], item['media_type'])).fetchall()
            for row in rows:
                if row['status'] != state:
                    db.execute('UPDATE watch_requests SET status=?,review_note=?,updated_at=? WHERE id=?',
                               (state, review_note, now, row['id']))
                    self._audit(db, row['id'], event, now)
            if config_id is not None:
                delivery_state = 'confirmed' if state == 'approved' else 'uncertain'
                db.execute('''INSERT INTO deliveries VALUES (?,?,?,?,?) ON CONFLICT(tmdb_id,media_type)
                              DO UPDATE SET state=excluded.state,config_id=excluded.config_id,updated_at=excluded.updated_at''',
                           (item['tmdb_id'], item['media_type'], delivery_state, config_id, now))

    def approve(self, request_id):
        item = self._get(request_id)
        with self._title_lock(item):
            item = self._get(request_id)
            if item['status'] == 'approved':
                return {'request': self._project(item)}
            if item['status'] == 'rejected':
                raise RequestError('此申请已驳回。', 'request_rejected', 409)
            # The catalog uses this same shared/exclusive flock for configuration
            # changes: a credential cannot be paired with another origin mid-write.
            with self.catalog._lock, self.catalog._config_lock(exclusive=False):
                origin, token, _ = self._settings()
                # A token rotation leaves the same external task store. Ledger
                # scope follows the canonical origin so read-only reconciliation
                # remains possible; signed candidates still bind the full config.
                config_id = hashlib.sha256(origin.encode()).hexdigest()
                with self._db() as db:
                    delivery = db.execute('SELECT * FROM deliveries WHERE tmdb_id=? AND media_type=?',
                                          (item['tmdb_id'], item['media_type'])).fetchone()
                if delivery and delivery['state'] == 'confirmed' and delivery['config_id'] == config_id:
                    self._mark(item, 'approved', '已加入追更，请关注入库进度。', 'confirmed_existing', config_id=config_id)
                    return {'request': self._project(self._get(request_id))}
                ambiguous = delivery and delivery['state'] in ('sending', 'uncertain')
                if ambiguous and delivery['config_id'] != config_id:
                    self._mark(item, 'uncertain', '订阅连接已变化，请管理员核对原系统中的任务。', 'uncertain')
                    raise RequestError('订阅连接已变化，请先核对原 NextFind 任务；系统不会重复发送。', 'subscription_uncertain', 409)
                try:
                    subscriptions = self.read(origin, token)
                    if not isinstance(subscriptions, set) or len(subscriptions) > MAX_SUBSCRIPTIONS:
                        raise ProviderReadError()
                    for identifier, kind in subscriptions:
                        _identity(identifier, kind)
                except Exception:
                    if ambiguous:
                        self._mark(item, 'uncertain', '订阅结果待管理员核对，系统不会重复发送。', 'uncertain')
                    raise RequestError('暂时无法核对 NextFind 订阅列表，未发送新的订阅。', 'nextfind_read_unavailable', 503) from None
                if (item['tmdb_id'], item['media_type']) in subscriptions:
                    self._mark(item, 'approved', '已加入追更，请关注入库进度。', 'confirmed_existing', config_id=config_id)
                    return {'request': self._project(self._get(request_id))}
                if ambiguous:
                    self._mark(item, 'uncertain', '订阅结果待管理员核对，系统不会重复发送。', 'uncertain')
                    raise RequestError('上次订阅结果尚未确认，请在 NextFind 核对；系统不会重复发送。', 'subscription_uncertain', 409)
                # Commit intent BEFORE network I/O. A process kill after this point
                # leaves 'sending', which is treated as uncertain on every restart.
                with self._db(write=True) as db:
                    db.execute('''INSERT INTO deliveries VALUES (?,?,?,?,?) ON CONFLICT(tmdb_id,media_type)
                                  DO UPDATE SET state=excluded.state,config_id=excluded.config_id,updated_at=excluded.updated_at''',
                               (item['tmdb_id'], item['media_type'], 'sending', config_id, self.now()))
                    db.execute("UPDATE watch_requests SET status='uncertain',review_note=?,updated_at=? WHERE tmdb_id=? AND media_type=? AND status='pending'",
                               ('订阅结果正在确认，系统不会重复发送。', self.now(), item['tmdb_id'], item['media_type']))
                    self._audit(db, request_id, 'sending', self.now())
                try:
                    self.add(origin, token, item)
                except Exception:
                    self._mark(item, 'uncertain', '订阅结果待管理员核对，系统不会重复发送。', 'uncertain', config_id=config_id)
                    raise RequestError('订阅结果暂时无法确认，请在 NextFind 核对；系统不会重复发送。', 'subscription_uncertain', 409) from None
                self._mark(item, 'approved', '已加入追更，请关注入库进度。', 'confirmed_added', config_id=config_id)
                return {'request': self._project(self._get(request_id))}

    def reject(self, request_id, body):
        if not isinstance(body, dict) or not set(body) <= {'note'}:
            raise RequestError('审核备注无效。')
        note = _note(body.get('note', '')) or '暂未通过审核。'
        item = self._get(request_id)
        with self._title_lock(item), self._db(write=True) as db:
            row = db.execute('SELECT status FROM watch_requests WHERE id=?', (request_id,)).fetchone()
            if row['status'] == 'rejected':
                pass
            elif row['status'] != 'pending':
                raise RequestError('只有待审核申请可以驳回。', 'request_not_pending', 409)
            else:
                db.execute("UPDATE watch_requests SET status='rejected',review_note=?,updated_at=? WHERE id=?",
                           (note, self.now(), request_id))
                self._audit(db, request_id, 'rejected', self.now())
        return {'request': self._project(self._get(request_id))}


def install_routes(app, vault, admin, rate, data_dir, *, catalog=None, now=time.time,
                   read=read_subscriptions, add=add_subscription):
    if hasattr(app.state, 'subscription_requests'):
        return app.state.subscription_requests
    service = SubscriptionRequests(data_dir, vault, catalog or app.state.nextfind_catalog, now=now, read=read, add=add)
    app.state.subscription_requests = service

    def run(action):
        try:
            return action()
        except RequestError as error:
            return JSONResponse({'error': {'message': error.message, 'code': error.code}}, status_code=error.status)
        except Exception:
            return JSONResponse({'error': {'message': '申请服务暂时不可用，请稍后重试。', 'code': 'subscription_unavailable'}}, status_code=503)

    @app.get('/api/public/subscription-requests')
    def mine(request: Request):
        rate(request, 'subscription_session', 30, 60)
        def action():
            if request.headers.get('sec-fetch-site') == 'cross-site':
                raise RequestError('请从本站页面查看申请。', 'invalid_origin', 403)
            visitor_hash, token = service.visitor(request.cookies.get(COOKIE, ''), create=True)
            response = JSONResponse(service.mine(visitor_hash))
            if token:
                response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                                    secure=request.url.scheme == 'https', samesite='strict', path='/')
            response.headers['Cache-Control'] = 'no-store'
            return response
        return run(action)

    @app.post('/api/public/subscription-requests')
    def submit(request: Request, body: object = Body(...)):
        rate(request, 'subscription_submit', 10, 3600)
        def action():
            same_origin(request)
            visitor_hash, _ = service.visitor(request.cookies.get(COOKIE, ''))
            return service.submit(visitor_hash, body)
        return run(action)

    @app.get('/api/admin/subscription-requests')
    def listing(request: Request):
        admin(request)
        rate(request, 'subscription_review_list', 60, 60)
        def action():
            params = request.query_params
            if not set(params) <= {'status', 'page'} or any(len(params.getlist(key)) != 1 for key in params):
                raise RequestError('审核列表查询无效。')
            page = params.get('page', '1')
            if not re.fullmatch(r'[1-9][0-9]{0,2}', page):
                raise RequestError('审核列表查询无效。')
            return service.listing(params.get('status', 'pending'), int(page))
        return run(action)

    @app.post('/api/admin/subscription-requests/{request_id}/approve')
    def approve(request_id: str, request: Request, body: object = Body(...)):
        admin(request)
        rate(request, 'subscription_approve', 20, 60)
        def action():
            same_origin(request)
            if body != {}:
                raise RequestError('审核参数无效。')
            return service.approve(request_id)
        return run(action)

    @app.post('/api/admin/subscription-requests/{request_id}/reject')
    def reject(request_id: str, request: Request, body: object = Body(...)):
        admin(request)
        rate(request, 'subscription_reject', 30, 60)
        def action():
            same_origin(request)
            return service.reject(request_id, body)
        return run(action)

    return service
