"""影视服购卡与管理入口。正式付款与卡密副作用均由受认证操作驱动。"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from core import Store, load_products, CoreError
from nextemby import NextEmbyClient, NextEmbyError, validate_base_url
from secrets_store import default_secret_store, SecretStoreError
from xianyu_proxy import XianyuProxyConfig, ProxyConfigError
from xiaohongshu import install_routes as install_xiaohongshu_routes
from network_monitor import MAX_CONFIG_BYTES, MonitorConflict, NetworkMonitor
from featured_movies import FeaturedMovies, FeaturedMoviesError
from nextfind_catalog import install_routes as install_nextfind_catalog_routes
from emby_library import install_routes as install_emby_library_routes
from subscription_requests import install_routes as install_subscription_request_routes
from purchase_reviews import install_routes as install_purchase_review_routes

ROOT = Path(__file__).resolve().parent
PRODUCT_FILE = ROOT.parent / 'product-mapping.json'
NEXTEMBY_URL = 'https://nextemby.example.com'
ADMIN_USERNAME = os.environ.get('MEDIADOCK_ADMIN_USERNAME', 'admin').strip() or 'admin'
COOKIE = 'mediadock_session'
INTRODUCTION_DEFAULT = {'entry_label': '影视服简介', 'enabled': True, 'title': '好片源，好线路。把时间留给观影。', 'subtitle': '从片库到线路，了解影视服为你准备的观影体验。', 'sections': [{'title': '按部署环境接入线路', 'body': '可接入你自己的反向代理、CDN 或媒体服务线路。实际延迟、带宽和播放体验取决于部署环境与上游服务，请在公开前完成实际测试。'}, {'title': '自托管部署', 'body': '支持在自己的服务器、NAS 或 Docker 环境中部署，数据目录、凭据目录和媒体服务连接均由部署者自行管理。'}, {'title': '同一影片，只保留当前已收录的最高质量版本', 'body': '片库以画质与音质为优先，同一影片不堆叠多个版本，发现更优片源后持续替换升级。减少重复条目和版本选择，让找片、选片更简单。'}, {'title': '兼顾音画品质与常用设备兼容性', 'body': '暂不收录 ISO、BDMV 等光盘镜像或目录格式，优先保留同等音画品质、播放更方便的版本，例如无需重新压制的 REMUX 封装。保留观影品质，也降低播放门槛。'}, {'title': '购买必看！先查片库，再决定购买', 'body': '只是查看片库有没有想看的电影或剧集，直接搜索即可，无需购买试用卡或天卡。\n\n试用卡推荐用于测试实际播放速度、流畅度和客户端体验。体验合适后，再按需选择长期套餐。\n\n先查有没有想看的，再试好不好用。影视服把片源与线路准备好，让你安心享受每一场电影。'}]}


class APIError(Exception):
    def __init__(self, message, code='invalid_request', status=400):
        self.message, self.code, self.status = message, code, status


def introduction_config(value=None, strict=False):
    """Project only public plain-text announcement fields."""
    if value is None and not strict:
        value = INTRODUCTION_DEFAULT
    if not isinstance(value, dict):
        raise APIError('首页公告需要使用正确的配置格式')
    if strict and set(value) - set(INTRODUCTION_DEFAULT):
        raise APIError('首页公告包含不支持的字段')
    def text(value, label, maximum, required=True):
        if not isinstance(value, str):
            raise APIError(label + '必须是文字')
        value = value.replace('\r\n', '\n').replace('\r', '\n').strip()
        if (required and not value) or len(value) > maximum:
            raise APIError(label + '不能为空或超过长度限制')
        if any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in value):
            raise APIError(label + '包含不支持的控制字符')
        return value
    enabled = value.get('enabled', INTRODUCTION_DEFAULT['enabled'])
    if type(enabled) is not bool:
        raise APIError('首页公告开关必须为布尔值')
    sections = value.get('sections', INTRODUCTION_DEFAULT['sections'])
    if not isinstance(sections, list) or not 1 <= len(sections) <= 12:
        raise APIError('首页公告需要 1 至 12 个段落')
    clean = []
    for section in sections:
        if not isinstance(section, dict) or (strict and set(section) != {'title', 'body'}):
            raise APIError('公告段落格式不正确')
        clean.append({'title': text(section.get('title'), '段落标题', 160),
                      'body': text(section.get('body'), '段落正文', 4000)})
    if sum(len(section['body']) for section in clean) > 16000:
        raise APIError('公告正文合计不能超过 16000 字')
    return {'entry_label': text(value.get('entry_label', INTRODUCTION_DEFAULT['entry_label']), '入口名称', 24), 'enabled': enabled, 'title': text(value.get('title', INTRODUCTION_DEFAULT['title']), '公告标题', 120),
            'subtitle': text(value.get('subtitle', INTRODUCTION_DEFAULT['subtitle']), '公告说明', 240, False), 'sections': clean}


def deployment_flag(name):
    value = os.environ.get(name, '0').lower()
    if value not in {'0', '1', 'true', 'false'}:
        raise RuntimeError('部署布尔配置必须使用 0、1、true 或 false')
    return value in {'1', 'true'}


def public_base_url(value, require_https=False):
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in {'http', 'https'} and parts.hostname and parts.port != 0
                 and not parts.username and not parts.password and not parts.query and not parts.fragment
                 and parts.path in {'', '/'}
                 and not any(character.isspace() or ord(character) < 32 for character in value))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise APIError('网站公开地址必须是完整且不含路径、参数或凭证的 HTTP/HTTPS 地址')
    if require_https and parts.scheme != 'https' and parts.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise APIError('公网访问地址需要 HTTPS')
    return value.rstrip('/')


class ControlDB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS public_orders(order_id TEXT PRIMARY KEY,token_hash TEXT NOT NULL,paid_notice INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
            ''')
            columns = {row[1] for row in c.execute('PRAGMA table_info(public_orders)')}
            if 'payment_note' not in columns:
                c.execute("ALTER TABLE public_orders ADD COLUMN payment_note TEXT NOT NULL DEFAULT ''")

    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def get(self, key, default=None):
        with self.db() as c:
            row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key, value):
        with self.db() as c:
            c.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def require_text(value, field, limit=160, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()) or any(ord(c) < 32 for c in value):
        raise APIError(f'{field}格式不正确')
    return value.strip()


def create_app(data_dir=None, secret_store=None, nextemby_factory=NextEmbyClient):
    os.umask(0o077)
    require_https = deployment_flag('MEDIADOCK_REQUIRE_HTTPS')
    unified_admin = deployment_flag('MEDIADOCK_UNIFIED_ADMIN')
    nextemby_url = validate_base_url(os.environ.get('MEDIADOCK_NEXTEMBY_URL') or NEXTEMBY_URL,
                                    require_https=require_https)
    public_override = os.environ.get('MEDIADOCK_PUBLIC_BASE_URL', '')
    if public_override:
        public_override = public_base_url(public_override, require_https)
    xianyu_admin_url = public_base_url(os.environ.get('MEDIADOCK_XIANYU_ADMIN_URL') or 'http://127.0.0.1:59188', require_https)
    data_dir = Path(data_dir or os.environ.get('MEDIADOCK_DATA_DIR') or Path.home() / 'Library/Application Support/MediaDockCardDesk')
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault = secret_store or default_secret_store()
    if unified_admin and not vault.get('admin-integration'):
        raise RuntimeError('聚合后台必须先配置服务端管理网关凭据')
    store = Store(data_dir / 'orders.sqlite3', vault.ensure('card-encryption').encode())
    control = ControlDB(data_dir / 'control.sqlite3')
    network_monitor = NetworkMonitor(data_dir)
    featured_movies = FeaturedMovies(data_dir, vault)
    proxy_config = XianyuProxyConfig(vault, os.environ.get('MEDIADOCK_XIANYU_PROXY_STATUS_FILE'))
    products = load_products(PRODUCT_FILE)
    app = FastAPI(title='影视服 · 观影服务平台', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.control, app.state.vault = store, control, vault
    app.state.network_monitor = network_monitor
    app.state.featured_movies = featured_movies
    app.state.connection = {'configured': bool(vault.get('nextemby')), 'status': 'not_checked'}
    rate_lock = threading.Lock()
    rates = defaultdict(list)

    def settings():
        result = {'redemption_url': '', 'public_base_url': 'http://127.0.0.1:59189', 'payment_wechat_url': '', 'payment_alipay_url': '', 'xianyu_account_id': '', 'xianyu_store_url': '', **control.get('settings', {})}
        result['public_base_url'] = public_base_url(public_override or result['public_base_url'], require_https)
        result['public_base_url_managed'] = bool(public_override)
        result['introduction'] = introduction_config(result.get('introduction'))
        return result

    settings()  # Fail closed on unsafe deployment URLs before serving requests.

    def rate(request, bucket, maximum=30, window=60):
        key = (request.client.host if request.client else '', bucket)
        now = time.time()
        with rate_lock:
            if len(rates) > 4096:
                for old in list(rates):
                    if not rates[old] or rates[old][-1] < now - 3600:
                        del rates[old]
                if len(rates) > 4096:
                    raise APIError('请求过多，请稍后重试', 'rate_limited', 429)
            rates[key] = [v for v in rates[key] if v > now - window]
            if len(rates[key]) >= maximum:
                raise APIError('请求过多，请稍后重试', 'rate_limited', 429)
            rates[key].append(now)

    def authenticated(request):
        token = request.cookies.get(COOKIE, '')
        if not token:
            return False
        with control.db() as c:
            row = c.execute('SELECT expires FROM sessions WHERE token_hash=?', (digest(token),)).fetchone()
        return bool(row and row['expires'] > time.time())

    def admin(request):
        if not authenticated(request):
            raise APIError('请先登录管理后台', 'unauthorized', 401)

    install_xiaohongshu_routes(app, vault, admin, rate, data_dir)
    install_nextfind_catalog_routes(app, vault, admin, rate, data_dir)
    install_emby_library_routes(app, vault, admin, rate, data_dir)
    install_subscription_request_routes(app, vault, admin, rate, data_dir)

    def catalog(admin_mode=False):
        output = []
        bindings = control.get('bindings', {})
        for p in products.values():
            if admin_mode:
                output.append({**p, **bindings.get(p['id'], {})})
            else:
                output.append({k: p[k] for k in ('id', 'name', 'duration_days', 'requires_own_115', 'listed_price_cents')})
        return output

    def xianyu_order_identity(value, account, allow_canonical=False):
        """Keep manual intake and the trusted connector on the same ledger key."""
        if not account:
            raise APIError('请先在渠道设置中绑定闲鱼卖家账号，再录入闲鱼订单', 'binding_mismatch', 409)
        value = require_text(value, '订单号', 128 if allow_canonical else 80)
        prefix = 'XY-' + account + '-'
        if value.startswith('XY-'):
            if not allow_canonical or not value.startswith(prefix):
                raise APIError('订单号的卖家前缀与当前绑定账号不一致，请填写闲鱼原始交易号', 'order_conflict', 409)
            value = value[len(prefix):]
        raw_id = require_text(value, '闲鱼原始交易号', 80)
        if raw_id.startswith('XY-'):
            raise APIError('请填写闲鱼原始交易号，不要重复添加卖家前缀', 'order_conflict', 409)
        return raw_id, require_text(prefix + raw_id, '订单号', 128)

    def guard_legacy_xianyu_order(raw_id):
        # The old raw key has no reliable seller identity. Never migrate it or
        # create a second card under a new canonical key as a side effect.
        if any(order['order_id'] == raw_id for order in store.list_orders()):
            raise APIError('存在同交易号的历史订单，请在订单管理中打开原订单核查；不会另建订单或再次生成卡密', 'order_conflict', 409)

    purchase_reviews = install_purchase_review_routes(app, admin, rate, control, store, products, settings,
                                                      xianyu_order_identity, guard_legacy_xianyu_order)

    def client():
        token = vault.get('nextemby')
        if not token:
            raise APIError('请先在设置中配置 NextEmby 智能体凭证', 'nextemby_not_configured', 409)
        return nextemby_factory(nextemby_url, token)

    def add_session(response, request):
        token = secrets.token_urlsafe(32)
        with control.db() as c:
            c.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
            c.execute('INSERT INTO sessions VALUES(?,?)', (digest(token), time.time() + 86400))
        # The ASGI scheme is trusted only after uvicorn validates the proxy peer.
        # HTTP loopback remains usable through the administrator's SSH tunnel.
        response.set_cookie(COOKIE, token, httponly=True, samesite='strict', max_age=86400,
                            secure=request.url.scheme == 'https')
        return response

    def public_order_auth(request, order_id):
        token = request.headers.get('X-Order-Token', '')
        with control.db() as c:
            row = c.execute('SELECT * FROM public_orders WHERE order_id=?', (order_id,)).fetchone()
        if not row or not hmac.compare_digest(row['token_hash'], digest(token)):
            raise APIError('订单不存在或查询凭证不正确', 'not_found', 404)
        return row

    def claim_url(order_id):
        token = store.create_claim(order_id)
        return settings()['public_base_url'].rstrip('/') + '/claim#' + token

    @app.middleware('http')
    async def boundary(request, call_next):
        host = request.headers.get('host', '').lower()
        allowed = {'127.0.0.1:59189', 'localhost:59189'}
        public = urlsplit(settings()['public_base_url'])
        if public.netloc:
            allowed.add(public.netloc.lower())
        if host not in allowed:
            return JSONResponse({'error': {'message': '访问地址未配置', 'code': 'invalid_host'}}, status_code=400)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin and (urlsplit(origin).netloc.lower() != host or urlsplit(origin).scheme != request.url.scheme):
                return JSONResponse({'error': {'message': '不接受跨站请求', 'code': 'invalid_origin'}}, status_code=403)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'error': {'message': '不接受跨站请求', 'code': 'invalid_origin'}}, status_code=403)
            if 'application/json' not in request.headers.get('content-type', ''):
                return JSONResponse({'error': {'message': '请求需要 JSON 格式', 'code': 'invalid_content_type'}}, status_code=415)
            body_limit = MAX_CONFIG_BYTES if request.method == 'POST' and request.url.path == '/api/admin/network-monitor' else (131072 if request.method == 'POST' and request.url.path == '/api/admin/settings' else 32768)
            try:
                if int(request.headers.get('content-length', '0')) > body_limit:
                    return JSONResponse({'error': {'message': '请求过大', 'code': 'too_large'}}, status_code=413)
            except ValueError:
                return JSONResponse({'error': {'message': '请求格式不正确', 'code': 'invalid_request'}}, status_code=400)
            received = bytearray()
            async for chunk in request.stream():
                received.extend(chunk)
                if len(received) > body_limit:
                    return JSONResponse({'error': {'message': '请求过大', 'code': 'too_large'}}, status_code=413)
            # Starlette 会向下游复用已缓存的请求体，包含未发送 Content-Length 的请求。
            request._body = bytes(received)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        if request.url.path.startswith('/api/public/featured-images/') and response.status_code == 200:
            response.headers['Cache-Control'] = 'public, max-age=604800, immutable'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        browser_origins = ' '.join(network_monitor.browser_test_origins())
        connect_sources = "'self'" + (' ' + browser_origins if browser_origins else '')
        frame_sources = "'self'"
        if request.url.path == '/discover':
            try:
                center = urlsplit(settings()['redemption_url'])
                if (center.scheme == 'https' and center.hostname and not center.username
                        and not center.password and center.port != 0
                        and re.fullmatch(r'[A-Za-z0-9.\-\[\]:]+', center.netloc)):
                    frame_sources += ' https://' + center.netloc.lower()
            except (ValueError, TypeError):
                pass
        response.headers['Content-Security-Policy'] = f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https:; connect-src {connect_sources}; frame-src {frame_sources}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse({'error': {'message': exc.message, 'code': exc.code}}, status_code=exc.status)

    @app.exception_handler(CoreError)
    async def core_error(request, exc):
        return JSONResponse({'error': {'message': str(exc), 'code': type(exc).__name__}}, status_code=409)

    @app.exception_handler(NextEmbyError)
    async def nextemby_error(request, exc):
        return JSONResponse({'error': {'message': 'NextEmby 请求未完成，请检查连接或转人工核对。不会自动重试生成。', 'code': type(exc).__name__}}, status_code=502)

    @app.exception_handler(SecretStoreError)
    async def secret_store_error(request, exc):
        return JSONResponse({'error': {'message': '凭据存储不可用，请检查服务配置和权限', 'code': 'secret_store_unavailable'}}, status_code=503)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'service': 'mediadock-carddesk'}

    @app.get('/internal/xianyu-auth')
    def xianyu_gateway_auth(request: Request):
        # Internal Nginx authorization and connector WebSocket revalidation only.
        # Nginx overwrites the original-method header and fixes the proxy peer.
        if not unified_admin or not request.client or request.client.host not in {'127.0.0.1', '::1'}:
            raise APIError('入口不存在', 'not_found', 404)
        admin(request)
        gateway_key = vault.get('admin-integration')
        supplied_key = request.headers.get('x-mediadock-admin-key')
        if supplied_key is not None and not hmac.compare_digest(supplied_key, gateway_key):
            raise APIError('管理网关验证失败', 'unauthorized', 401)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise APIError('不接受跨站请求', 'invalid_origin', 403)
        method = request.headers.get('x-original-method', '')
        if method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
            raise APIError('请求方式不正确', 'invalid_request', 403)
        origin = request.headers.get('origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.netloc.lower() != request.headers.get('host', '').lower() or parsed.scheme != request.url.scheme:
                raise APIError('不接受跨站请求', 'invalid_origin', 403)
        # Never send this header to a browser: the Nginx location is internal.
        return Response(status_code=204, headers={'X-MediaDock-Admin-Key': gateway_key, 'X-MediaDock-Session': request.cookies[COOKIE]})

    @app.get('/api/auth/status')
    def auth_status(request: Request):
        return {'initialized': control.get('password') is not None, 'authenticated': authenticated(request)}

    @app.post('/api/auth/setup')
    def setup(request: Request, body: dict):
        rate(request, 'login', 10, 300)
        password = require_text(body.get('password'), '密码', 256)
        if len(password) < 8:
            raise APIError('密码至少 8 位')
        with control.lock:
            if control.get('password') is not None:
                raise APIError('管理员已设置，请登录', 'already_initialized', 409)
            salt = secrets.token_bytes(16)
            hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
            control.set('password', {'salt': salt.hex(), 'hash': hashed.hex()})
        return add_session(JSONResponse({'ok': True}), request)

    @app.post('/api/auth/login')
    def login(request: Request, body: dict):
        rate(request, 'login', 10, 300)
        password = require_text(body.get('password'), '密码', 256)
        saved = control.get('password')
        password_ok = bool(saved) and hmac.compare_digest(hashlib.scrypt(password.encode(), salt=bytes.fromhex(saved['salt']), n=16384, r=8, p=1).hex(), saved['hash'])
        username = body.get('username')
        username_ok = isinstance(username, str) and hmac.compare_digest(username.encode(), ADMIN_USERNAME.encode())
        if not password_ok or not username_ok:
            raise APIError('用户名或密码不正确', 'invalid_password', 401)
        return add_session(JSONResponse({'ok': True}), request)

    @app.post('/api/auth/logout')
    def logout(request: Request, body: dict):
        with control.db() as c:
            c.execute('DELETE FROM sessions WHERE token_hash=?', (digest(request.cookies.get(COOKIE, '')),))
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
        return response

    @app.get('/api/public/catalog')
    def public_catalog():
        s = settings()
        return {'products': catalog(), 'payment_configured': bool(s['payment_wechat_url'] or s['payment_alipay_url']), 'payment_qr': {'wechat': s['payment_wechat_url'], 'alipay': s['payment_alipay_url']}, 'redemption_url': s['redemption_url'], 'customer_center_url': s['redemption_url'], 'xianyu_store_url': s['xianyu_store_url'], 'introduction': introduction_config(s['introduction'])}

    @app.get('/api/public/network-status')
    def public_network_status():
        # Public display names and sanitized observations only; no live probes here.
        return network_monitor.public_status()

    @app.get('/api/public/featured-movies')
    def public_featured_movies():
        return featured_movies.public()

    @app.get('/api/public/featured-images/{name}')
    def public_featured_image(name: str):
        try:
            body, mime = featured_movies.image(name)
        except FeaturedMoviesError:
            raise HTTPException(status_code=404, detail='图片不存在') from None
        return Response(body, media_type=mime)

    @app.get('/api/admin/featured-movies')
    def admin_featured_movies(request: Request):
        admin(request)
        return featured_movies.config()

    @app.post('/api/admin/featured-movies')
    def save_featured_movies(request: Request, body: object = Body(...)):
        admin(request)
        rate(request, 'featured_movies_config', 6, 60)
        try:
            return featured_movies.save_config(body)
        except FeaturedMoviesError as error:
            raise APIError(str(error), 'invalid_featured_movies_config') from None

    @app.post('/api/admin/featured-movies/refresh')
    def refresh_featured_movies(request: Request, body: object = Body(...)):
        admin(request)
        rate(request, 'featured_movies_refresh', 3, 60)
        if not isinstance(body, dict) or body:
            raise APIError('刷新请求不接受额外参数')
        try:
            if not featured_movies.refresh():
                raise APIError('请先配置 TMDB 凭据并启用每日更新', 'featured_movies_not_configured', 409)
        except FeaturedMoviesError as error:
            raise APIError(str(error), 'featured_movies_refresh_failed', 502) from None
        return featured_movies.config()

    @app.get('/api/admin/network-monitor')
    def admin_network_monitor(request: Request):
        admin(request)
        return network_monitor.admin_config()

    @app.post('/api/admin/network-monitor')
    def save_network_monitor(request: Request, body: dict):
        admin(request)
        rate(request, 'network_monitor_config', 6, 60)
        try:
            return network_monitor.save_config(body)
        except MonitorConflict:
            raise APIError('监测配置已被其他页面修改，或当前页面版本过旧。请保留未保存内容，重新读取最新配置后再编辑。', 'monitor_config_conflict', 409) from None
        except ValueError:
            raise APIError('请检查分组标题、监测标题与目标：标题限 1–40 字且不含地址；HTTP/HTTPS 填完整公网 URL，浏览器测速填可公开的 HTTPS 地址，Ping 填公网 IP 或域名，妙妙屋 X 从最新探针列表选择。最多 12 个分组、40 个监测项。', 'invalid_monitor_config') from None
        except OSError:
            raise APIError('监测配置暂时无法保存，请稍后重试。', 'monitor_storage_unavailable', 503) from None

    @app.get('/api/admin/network-probe')
    def admin_network_probe(request: Request):
        admin(request)
        return network_monitor.probe_config()

    @app.post('/api/admin/network-probe')
    def save_network_probe(request: Request, body: dict):
        admin(request)
        rate(request, 'network_probe_config', 6, 60)
        try:
            return network_monitor.save_probe_config(body)
        except ValueError:
            raise APIError('请填写妙妙屋 X 外置探针的 HTTPS 首页地址，不含账号、密码、路径、参数或片段；留空可停用接入。', 'invalid_probe_config') from None
        except OSError:
            raise APIError('探针接入设置暂时无法保存，请稍后重试。', 'probe_storage_unavailable', 503) from None

    @app.post('/api/admin/network-probe/refresh')
    def refresh_network_probe(request: Request, body: dict):
        admin(request)
        rate(request, 'network_probe_refresh', 6, 60)
        if body:
            raise APIError('请先保存探针地址，再刷新节点列表。', 'invalid_probe_refresh')
        try:
            return network_monitor.refresh_probe()
        except (OSError, ValueError):
            raise APIError('探针列表暂时无法读取，请检查接入设置并稍后重试。', 'probe_unavailable', 503) from None

    @app.post('/api/public/orders')
    def public_create(request: Request, body: dict):
        rate(request, 'public_create', 5, 3600)
        product_id = require_text(body.get('product_id'), '套餐')
        if product_id not in products:
            raise APIError('套餐不存在')
        payment = public_catalog()
        if not payment['payment_configured']:
            raise APIError('网页收款尚未配置，请通过其它入口完成购买。', 'payment_not_configured', 409)
        note = require_text(body.get('contact_note', ''), '联系备注', 120, False)
        order_id = 'WEB-' + time.strftime('%Y%m%d') + '-' + secrets.token_hex(5).upper()
        token = secrets.token_urlsafe(32)
        store.create_order(order_id, product_id, 'claim', 'web', buyer_note=note)
        with control.db() as c:
            c.execute('INSERT INTO public_orders(order_id,token_hash,paid_notice,created) VALUES(?,?,0,?)', (order_id, digest(token), time.time()))
        return {'order_id': order_id, 'access_token': token, 'status': 'pending_payment', 'product': {k: products[product_id][k] for k in ('name', 'listed_price_cents')}, 'payment_configured': payment['payment_configured'], 'payment_qr': payment['payment_qr']}

    @app.get('/api/public/orders/{order_id}')
    def public_status(order_id: str, request: Request):
        row = public_order_auth(request, order_id)
        order = store.get_order(order_id)
        status = order['status']
        if status == 'pending_review' and not row['paid_notice']:
            status = 'pending_payment'
        result = {'order_id': order_id, 'status': status, 'product_id': order['product_id'], 'payment_qr': public_catalog()['payment_qr']}
        if status == 'issued':
            result['claim_url'] = claim_url(order_id)
        return result

    @app.post('/api/public/orders/{order_id}/paid-notice')
    def paid_notice(order_id: str, request: Request, body: dict):
        rate(request, 'paid_notice', 20, 3600)
        public_order_auth(request, order_id)
        note = require_text(body.get('payment_note', ''), '付款说明', 240, False)
        with control.db() as c:
            c.execute('UPDATE public_orders SET paid_notice=1,payment_note=? WHERE order_id=?', (note, order_id))
        return {'status': 'pending_review', 'message': '已提交付款提示，等待管理员核对到账；不会自动发卡。'}

    @app.post('/api/claim')
    def claim(request: Request, body: dict):
        rate(request, 'claim', 30, 60)
        token = require_text(body.get('token'), '领取凭证', 128)
        result = store.get_claim(token)
        result['redemption_url'] = settings()['redemption_url']
        return result

    @app.get('/api/admin/overview')
    def overview(request: Request):
        admin(request)
        orders = store.list_orders()
        with control.db() as c:
            website_orders = {row['order_id']: dict(row) for row in c.execute('SELECT order_id,paid_notice,payment_note FROM public_orders')}
        for order in orders:
            if order['order_id'] in website_orders:
                order.update(website_orders[order['order_id']])
                order['source'] = 'web'  # Normalize legacy website orders without rewriting the ledger.
        counts = Counter(o['status'] for o in orders)
        return {'products': catalog(True), 'orders': orders, 'purchase_reviews': purchase_reviews.list_admin(), 'stats': {'total': len(orders), 'issued': counts['issued'], 'pending': counts['pending_review'] + counts['eligible'], 'needs_review': counts['needs_review']}, 'connections': {'nextemby': {**app.state.connection, 'configured': bool(vault.get('nextemby'))}, 'xianyu': {'status': 'account_bound' if settings()['xianyu_account_id'] else 'setup_required', 'url': '/xianyu/' if unified_admin else xianyu_admin_url, 'unified_admin': unified_admin}}, 'settings': settings()}

    @app.post('/api/admin/orders')
    def manual_create(request: Request, body: dict):
        admin(request)
        source = require_text(body.get('source', 'manual'), '订单来源', 32)
        if source not in {'manual', 'web', 'xianyu', 'xiaohongshu'}:
            raise APIError('请选择网页、闲鱼、小红书或手工登记来源')
        order_id = require_text(body.get('order_id'), '订单号', 128 if source == 'xianyu' else 100)
        product_id = require_text(body.get('product_id'), '套餐')
        buyer_note = require_text(body.get('buyer_note', ''), '备注', 120, False)
        with control.lock:
            if source == 'xianyu':
                account = settings()['xianyu_account_id']
                if 'account_id' in body and body['account_id'] != account:
                    raise APIError('闲鱼卖家账号与当前绑定账号不一致', 'binding_mismatch', 409)
                raw_id, order_id = xianyu_order_identity(order_id, account, allow_canonical=True)
                guard_legacy_xianyu_order(raw_id)
            return store.create_order(order_id, product_id, body.get('delivery_mode', 'direct'), source, buyer_note=buyer_note)

    @app.post('/api/admin/orders/{order_id}/approve')
    def approve(order_id: str, request: Request, body: dict):
        admin(request)
        return store.approve_order(order_id, require_text(body.get('evidence_note'), '到账核对说明', 240))

    @app.post('/api/admin/orders/{order_id}/issue')
    def issue(order_id: str, request: Request, body: dict):
        admin(request)
        rate(request, 'issue', 20, 60)
        result = store.issue_order(order_id, client(), products)
        return {k: v for k, v in result.items() if k not in ('card', 'claim_token')}

    @app.post('/api/admin/orders/{order_id}/claim')
    def admin_claim(order_id: str, request: Request, body: dict):
        admin(request)
        return {'claim_url': claim_url(order_id)}

    @app.get('/api/admin/orders/{order_id}/delivery')
    def delivery(order_id: str, request: Request):
        admin(request)
        order = store.get_order(order_id, reveal=True)
        if order['status'] != 'issued':
            raise APIError('订单尚未生成卡密', 'not_issued', 409)
        p = order.get('product') or products[order['product_id']]
        message = f"{p['name']}｜{p['duration_days']} 天\n卡密：{order['card']}"
        if settings()['redemption_url']:
            message += '\n兑换地址：' + settings()['redemption_url']
        message += '\n请在影视服用户中心兑换卡密。' + ('此套餐需要自备 115 网盘。' if p['requires_own_115'] else '此套餐使用负载均衡，无需自备 115 网盘。')
        result = {'card': order['card'], 'message': message}
        if order.get('delivery_mode') == 'claim':
            result['claim_url'] = claim_url(order_id)
        return result

    @app.get('/api/admin/xianyu-proxy')
    def read_xianyu_proxy(request: Request):
        admin(request)
        if not unified_admin:
            raise APIError('当前部署未启用独立闲鱼代理', 'not_enabled', 409)
        try:
            return proxy_config.public()
        except ProxyConfigError as error:
            raise APIError(str(error)) from None

    @app.post('/api/admin/xianyu-proxy')
    def save_xianyu_proxy(request: Request, body: dict):
        admin(request)
        if not unified_admin:
            raise APIError('当前部署未启用独立闲鱼代理', 'not_enabled', 409)
        rate(request, 'proxy_config', maximum=4, window=60)
        try:
            return proxy_config.save(body)
        except ProxyConfigError as error:
            raise APIError(str(error)) from None

    @app.post('/api/admin/settings')
    def update_settings(request: Request, body: dict):
        admin(request)
        with control.lock:
            return save_settings(body)

    def save_settings(body):
        s = settings()
        if 'introduction' in body:
            introduction_config(body['introduction'], strict=True)
            s['introduction'] = introduction_config({**s['introduction'], **body['introduction']}, strict=True)
        for key in ('redemption_url', 'public_base_url', 'payment_wechat_url', 'payment_alipay_url', 'xianyu_store_url'):
            if key not in body:
                continue
            value = require_text(body[key], key, 500, key == 'public_base_url')
            if key == 'public_base_url':
                value = public_base_url(value, require_https)
                if public_override and value != public_override:
                    raise APIError('网站公开地址由服务器部署配置管理')
                s[key] = value
                continue
            if key.startswith('payment_') and value.startswith('/static/') and '..' not in value and '?' not in value and '#' not in value:
                s[key] = value
                continue
            if value:
                url = urlsplit(value)
                if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment:
                    raise APIError('请填写完整且不含凭证的 HTTP/HTTPS 地址')
                if key.startswith('payment_') and url.scheme != 'https':
                    raise APIError('收款码图片地址需要 HTTPS')
            s[key] = value
        if 'xianyu_account_id' in body:
            s['xianyu_account_id'] = require_text(body['xianyu_account_id'], '闲鱼账号标识', 100, False)
        if body.get('nextemby_token'):
            token = require_text(body['nextemby_token'], 'NextEmby 凭证', 256)
            if not token.startswith('ne_agent_'):
                raise APIError('请填写 NextEmby 智能体凭证')
            vault.set('nextemby', token)
            app.state.connection = {'configured': True, 'status': 'not_checked'}
        control.set('settings', s)
        return {'ok': True, 'settings': s}

    @app.post('/api/admin/nextemby/check')
    def check_nextemby(request: Request, body: dict):
        admin(request)
        batches = client().read_batches()
        app.state.connection = {'configured': True, 'status': 'connected', 'batch_count': len(batches)}
        return {'ok': True, **app.state.connection}

    @app.post('/api/admin/products/{product_id}')
    def bind_product(product_id: str, request: Request, body: dict):
        admin(request)
        if product_id not in products:
            raise APIError('套餐不存在')
        binding = {key: require_text(body.get(key, ''), key, 240 if key == 'xianyu_item_id' else 100, False) for key in ('xianyu_item_id', 'xianyu_sku_id')}
        item_ids = list(dict.fromkeys(v.strip() for v in binding['xianyu_item_id'].replace('，', ',').split(',') if v.strip()))
        if len(item_ids) > 10 or any(not v.isascii() or not v.isdigit() or len(v) > 32 for v in item_ids):
            raise APIError('商品 ID 应为数字，多个商品用逗号分隔，最多 10 个')
        binding['xianyu_item_id'] = ','.join(item_ids)
        binding['xianyu_item_ids'] = item_ids
        binding['enabled'] = body.get('enabled') is True
        if binding['enabled'] and not binding['xianyu_item_id']:
            raise APIError('启用自动发卡前必须绑定商品 ID；规格名称与规格值按套餐精确核验')
        with control.lock:
            all_bindings = control.get('bindings', {})
            for other_id, other in all_bindings.items():
                other_items = other.get('xianyu_item_ids') or [other.get('xianyu_item_id')]
                if other_id != product_id and binding['enabled'] and other.get('enabled') and set(other_items).intersection(item_ids):
                    if (products[other_id]['xianyu_spec_name'], products[other_id]['xianyu_spec_value']) == (products[product_id]['xianyu_spec_name'], products[product_id]['xianyu_spec_value']):
                        raise APIError('同一商品的完整规格组合不能绑定多个套餐')
            all_bindings[product_id] = binding
            control.set('bindings', all_bindings)
        return {'ok': True}

    @app.get('/api/admin/bridge-config')
    def bridge_config(request: Request):
        admin(request)
        token = vault.ensure('bridge')
        endpoint = 'http://127.0.0.1:59189/api/bridge/fulfill'
        presets = []
        for product in catalog(True):
            for mode in ('direct', 'claim'):
                presets.append({'product_id': product['id'], 'name': product['xianyu_spec_value'], 'delivery_mode': mode,
                    'config': {'url': endpoint, 'method': 'POST', 'timeout_seconds': 30,
                        'headers': {'Authorization': 'Bearer ' + token}, 'params': {},
                        'body': {'product_id': product['id'], 'delivery_mode': mode},
                        'content_type': 'application/json', 'response_path': 'content', 'retry_enabled': False}})
        return {'url': endpoint, 'token': token, 'products': catalog(True), 'presets': presets}

    @app.post('/api/bridge/fulfill')
    def bridge(request: Request, body: dict):
        authorization = request.headers.get('authorization', '')
        expected = vault.get('bridge')
        if not expected or not hmac.compare_digest(authorization, 'Bearer ' + expected):
            raise APIError('连接器认证失败', 'unauthorized', 401)
        rate(request, 'bridge', 30, 60)
        account = require_text(body.get('account_id'), '账号 ID', 100)
        if not settings()['xianyu_account_id'] or account != settings()['xianyu_account_id']:
            raise APIError('闲鱼账号未绑定或不匹配', 'binding_mismatch', 409)
        pid = require_text(body.get('product_id'), '套餐')
        p = next((v for v in catalog(True) if v['id'] == pid), None)
        if not p or not p['enabled']:
            raise APIError('套餐尚未启用闲鱼发卡', 'product_disabled', 409)
        permitted_items = p.get('xianyu_item_ids') or [p.get('xianyu_item_id')]
        if str(body.get('item_id', '')) not in permitted_items:
            raise APIError('订单商品与绑定不一致', 'binding_mismatch', 409)
        for field, expected_value in (('spec_name', p['xianyu_spec_name']), ('spec_value', p['xianyu_spec_value'])):
            if not expected_value or str(body.get(field, '')) != str(expected_value):
                raise APIError('订单商品或规格与绑定不一致', 'binding_mismatch', 409)
        if p.get('xianyu_sku_id') and str(body.get('sku_id', '')) != str(p['xianyu_sku_id']):
            raise APIError('订单规格 ID 与绑定不一致', 'binding_mismatch', 409)
        if str(body.get('quantity', '')) != '1':
            raise APIError('当前只自动处理购买数量为 1 的订单', 'quantity_mismatch', 409)
        try:
            price = Decimal(str(body.get('amount', ''))) * 100
        except InvalidOperation:
            raise APIError('订单金额不正确')
        if not price.is_finite() or price != p['listed_price_cents']:
            raise APIError('实付金额与套餐价格不一致，请人工核对', 'amount_mismatch', 409)
        raw_id, order_id = xianyu_order_identity(body.get('order_id'), account)
        buyer = require_text(body.get('buyer_id'), '买家 ID', 100)
        mode = body.get('delivery_mode', 'direct')
        with control.lock:
            guard_legacy_xianyu_order(raw_id)
            existing = next((o for o in store.list_orders() if o['order_id'] == order_id), None)
            if not existing:
                store.create_order(order_id, pid, mode, 'xianyu', buyer_note=buyer)
                store.approve_order(order_id, '由受信任闲鱼连接器实时核验付款、卖家、买家、商品及规格')
            elif existing['source'] != 'xianyu' or existing['product_id'] != pid or existing.get('buyer_note') != buyer or existing.get('delivery_mode') != mode:
                raise APIError('订单身份信息与历史记录不一致', 'order_conflict', 409)
        store.issue_order(order_id, client(), products)
        # 此接口表示取卡完成，不代表闲鱼消息已送达；送达状态由连接器单独负责。
        o = store.get_order(order_id, reveal=True)
        issued_product = o.get('product') or p
        if mode == 'claim':
            content = f"{issued_product['name']}\n领取地址：{claim_url(order_id)}"
        else:
            content = f"{issued_product['name']}｜{issued_product['duration_days']} 天\n卡密：{o['card']}"
            if settings()['redemption_url']:
                content += '\n兑换地址：' + settings()['redemption_url']
            content += '\n请在影视服用户中心兑换卡密。'
        return {'content': content, 'order_id': raw_id}

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    @app.get('/')
    @app.get('/plans')
    @app.get('/status')
    @app.get('/guide')
    @app.get('/library')
    @app.get('/discover')
    @app.get('/claim')
    @app.get('/admin')
    def page():
        return FileResponse(ROOT / 'static/index.html')

    @app.get('/admin/')
    def admin_page_redirect():
        return RedirectResponse('/admin', status_code=308)

    @app.get('/plans/')
    def plans_page_redirect():
        return RedirectResponse('/plans', status_code=308)

    @app.get('/status/')
    def status_page_redirect():
        return RedirectResponse('/status', status_code=308)

    @app.get('/guide/')
    def guide_page_redirect():
        return RedirectResponse('/guide', status_code=308)

    @app.get('/library/')
    def library_page_redirect():
        return RedirectResponse('/library', status_code=308)

    @app.get('/discover/')
    def discover_page_redirect():
        return RedirectResponse('/discover', status_code=308)

    return app


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(create_app(), host='127.0.0.1', port=59189, access_log=False,
                proxy_headers=True, forwarded_allow_ips='127.0.0.1')
