"""小红书独立浏览器的管理接口，只代理固定操作，不推断付款或自动发卡。"""
import base64
import binascii
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from xianyu_proxy import XianyuProxyConfig, ProxyConfigError
from secrets_store import SecretStoreError
from xhs_automation import AutomationError, XhsAutomation


STATES = {'stopped', 'starting', 'login_required', 'ready', 'needs_attention', 'error'}
OPERATIONS = {
    'status': ('GET', '/status', 20),
    'start': ('POST', '/session/start', 28),
    'stop': ('POST', '/session/stop', 20),
    'screenshot': ('GET', '/screenshot', 20),
    'click': ('POST', '/login/click', 20),
    'conversations': ('GET', '/conversations', 20),
    'select': ('POST', '/conversations/select', 20),
    'messages': ('GET', '/messages', 20),
    'send': ('POST', '/messages/send', 28),
    'send_auto': ('POST', '/messages/send', 28),
    'resolve': ('POST', '/messages/resolve', 20),
    'proxy_apply': ('POST', '/proxy/apply', 28),
}
ERROR_MESSAGES = {
    'login_required': '请先扫码登录小红书账号',
    'not_ready': '小红书会话尚未就绪，请查看登录状态',
    'conversation_changed': '当前会话已变化，请重新选择收件人',
    'account_changed': '当前账号已变化，请刷新后重试',
    'invalid_frame': '登录画面已变化，请刷新画面后再操作',
    'frame_expired': '登录画面已过期，请刷新画面后再操作',
    'send_disabled': '当前页面尚未验证发送能力，请在小红书网页核对',
    'busy': '小红书浏览器正在处理其他操作，请稍后重试',
    'request_conflict': '发送请求编号已使用，请先核对原消息结果',
    'not_found': '未找到指定的小红书会话，请刷新列表',
    'browser_stopped': '小红书浏览器尚未启动，请先连接账号',
    'page_not_allowed': '浏览器当前页面不可用于聊天，请重新连接账号',
    'browser_start_failed': '小红书浏览器启动失败，请检查服务状态',
    'sandbox_unavailable': '服务器浏览器沙箱不可用，请检查运行权限与系统配置',
    'missing_library': '服务器缺少浏览器运行依赖，请检查安装环境',
    'navigation_timeout': '打开小红书网页超时，请检查服务器网络后重新连接',
    'launch_timeout': '服务器浏览器启动超时，请检查服务资源后重新连接',
    'hydration_timeout': '小红书页面未在时限内就绪，请检查网络后重新连接',
    'blocked_network': '服务器未能连接小红书，请检查网络与访问限制',
    'platform_network_restricted': '当前服务器网络被小红书限制，暂时无法登录，请处理网络后重新连接',
    'runtime_unavailable': '服务器浏览器运行程序不可用，请检查安装路径与权限',
    'profile_not_private': '小红书浏览器数据权限不正确，请检查服务配置',
    'proxy_config_invalid': '小红书代理配置无法应用，请检查格式和凭据文件权限',
    'screenshot_too_large': '登录画面过大，请刷新后重试',
    'already_authenticated': '账号已登录，请刷新会话列表',
    'login_not_visible': '当前画面未确认登录入口，请刷新画面后再操作',
    'login_state_uncertain': '账号登录状态无法确认，请断开后重新连接并核对画面',
    'stale_frame': '登录画面已过期，请刷新画面后再操作',
    'invalid_coordinates': '请选择登录画面内的位置',
    'account_mismatch': '当前账号已变化，请刷新后重试',
    'conversation_not_visible': '指定会话不在当前列表中，请刷新列表',
    'draft_present': '小红书网页内已有草稿，请先在网页核对并处理',
    'conversation_ambiguous': '无法唯一确认收件人，请重新选择会话',
    'selection_unconfirmed': '尚未确认所选会话，请刷新后核对收件人',
    'conversation_mismatch': '当前会话已变化，请重新选择收件人',
    'composer_ambiguous': '无法确认聊天输入框，请刷新会话后再操作',
    'idempotency_conflict': '发送请求编号已使用，请先核对原消息结果',
    'unresolved_send': '此会话有待核对的发送记录，请刷新消息并先完成核对',
    'invalid_identity': '小红书账号编号格式不正确',
    'invalid_conversation': '小红书会话编号格式不正确',
    'invalid_request_id': '操作编号格式不正确',
    'invalid_text': '消息内容格式不正确，最多支持 1000 个字符',
    'invalid_resolution': '请先人工核对聊天记录并确认发送结果',
    'receipt_not_found': '未找到待核对的发送记录，请刷新会话',
    'receipt_conflict': '发送记录状态已变化，请刷新后重新核对',
    'invalid_source': '自动回复的触发消息标识无效',
    'source_changed': '触发消息或会话已变化，本次自动回复未提交',
}
RECEIPT_CODES = {'pending', 'interrupted', 'manual_sent', 'manual_not_sent', 'ack_unconfirmed',
                 'history_confirmed', 'send_unconfirmed', 'not_submitted'}


class XiaohongshuError(Exception):
    """只携带固定提示，禁止将浏览器异常、凭据或页面源码返回前端。"""
    def __init__(self, message, code='invalid_request', status=400):
        self.message, self.code, self.status = message, code, status


def worker_url(value):
    """部署地址只能指向本机字面量，禁止重定向、凭据及 DNS 解析。"""
    try:
        parts = urlsplit(value)
        valid = (parts.scheme == 'http' and parts.hostname in {'127.0.0.1', '::1'}
                 and parts.port is not None and 1 <= parts.port <= 65535
                 and not parts.username and not parts.password and not parts.query
                 and not parts.fragment and parts.path in {'', '/'}
                 and not any(c.isspace() or ord(c) < 32 for c in value))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RuntimeError('小红书浏览器地址必须为带端口的本机 HTTP 地址')
    return value.rstrip('/')


def text_field(value, limit, *, empty=False, multiline=False):
    """校验输入，不把提交内容拼入报错或日志。"""
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any((ord(c) < 32 and not (multiline and c in '\n\t')) or ord(c) == 127
                   or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise XiaohongshuError('小红书请求参数格式不正确')
    return value


def uuid_field(value):
    """操作编号使用规范 UUID，供浏览器工作进程防止重复发送。"""
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise XiaohongshuError('操作编号格式不正确') from None
    return value


def identity_field(value):
    """账号和会话标识只使用工作进程认可的 ASCII 字符，不接受路径。"""
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', value):
        raise XiaohongshuError('小红书账号或会话编号格式不正确')
    return value


def validate_body(operation, body):
    """固定字段白名单，禁止任意导航、脚本或浏览器控制命令。"""
    required = {
        'start': set(), 'stop': set(), 'proxy_apply': set(),
        'click': {'x', 'y', 'frame_token'},
        'select': {'account_id', 'conversation_id'},
        'send': {'request_id', 'account_id', 'conversation_id', 'text'},
        'send_auto': {'request_id', 'account_id', 'conversation_id', 'text', 'source_session_id', 'source_message_id'},
        'resolve': {'request_id', 'account_id', 'conversation_id', 'resolution', 'confirmed'},
    }[operation]
    if not isinstance(body, dict) or set(body) != required:
        raise XiaohongshuError('小红书请求参数格式不正确')
    result = {}
    for key in ('account_id', 'conversation_id'):
        if key in required:
            result[key] = identity_field(body[key])
    if operation == 'click':
        for key, maximum in (('x', 1279), ('y', 899)):
            if type(body[key]) is not int or not 0 <= body[key] <= maximum:
                raise XiaohongshuError('请选择登录画面内的位置')
            result[key] = body[key]
        result['frame_token'] = uuid_field(body['frame_token'])
    if operation in {'send', 'send_auto'}:
        result['request_id'] = uuid_field(body['request_id'])
        result['text'] = text_field(body['text'], 1000, multiline=True)
    if operation == 'send_auto':
        result['source_session_id'] = uuid_field(body['source_session_id'])
        source = text_field(body['source_message_id'], 300)
        if any(c.isspace() for c in source):
            raise XiaohongshuError('自动回复的触发消息标识无效', 'invalid_source')
        result['source_message_id'] = source
    if operation == 'resolve':
        if (not isinstance(body['resolution'], str) or body['resolution'] not in {'sent', 'not_sent'}
                or body['confirmed'] is not True):
            raise XiaohongshuError('请先人工核对聊天记录并确认发送结果')
        result.update(request_id=uuid_field(body['request_id']), resolution=body['resolution'], confirmed=True)
    return result


def response_text(body, key, limit=256, *, multiline=False):
    """只读取已约定的展示字段，不透传工作进程的任意 JSON 数据。"""
    return text_field(body.get(key, ''), limit, empty=True, multiline=multiline)


def receipt_fields(body, *, pending=False):
    """待核对消息仅向已登录管理员返回，手动核对与网页历史确认分开标记。"""
    if body is None:
        return None
    if not isinstance(body, dict) or body.get('status') not in {'sent', 'failed', 'needs_review'}:
        raise ValueError()
    if pending and body['status'] != 'needs_review':
        raise ValueError()
    created = response_text(body, 'created_at', 40)
    if created and not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', created):
        raise ValueError()
    code = body.get('code', '')
    code = code if isinstance(code, str) and code in RECEIPT_CODES else {
        'sent': 'send_confirmed', 'failed': 'send_failed', 'needs_review': 'send_result_unknown'}[body['status']]
    result = {'request_id': uuid_field(body.get('request_id')), 'account_id': identity_field(body.get('account_id')),
              'conversation_id': identity_field(body.get('conversation_id')), 'status': body['status'],
              'code': code, 'confirmation': code, 'created_at': created}
    if pending:
        result['text'] = text_field(body.get('text', ''), 1000, empty=True, multiline=True)
    return result


def contextual_receipt(body, key, account, conversation, *, pending=False):
    """刷新页面后只展示属于当前账号和会话的回执。"""
    result = receipt_fields(body.get(key), pending=pending)
    if result and (result['account_id'] != account or result['conversation_id'] != conversation):
        raise ValueError()
    return result


def status_fields(body):
    """ready 只代表网页登录状态，不代表订单或消息已成功交付。"""
    if body.get('state') not in STATES or type(body.get('browser_running')) is not bool:
        raise ValueError()
    result = {key: response_text(body, key) for key in ('account_id', 'account_name', 'conversation_id')}
    result.update(state=body['state'], browser_running=body['browser_running'])
    if 'session_id' in body:
        result['session_id'] = uuid_field(body['session_id']) if body['session_id'] else ''
    if 'latest_receipt' in body:
        result['latest_receipt'] = contextual_receipt(body, 'latest_receipt', result['account_id'], result['conversation_id'])
    if isinstance(body.get('detail_code'), str) and body['detail_code'] in ERROR_MESSAGES:
        result['detail_code'] = body['detail_code']
    if 'proxy_revision' in body or 'proxy_enabled' in body:
        revision = body.get('proxy_revision')
        if (not isinstance(revision, str) or not re.fullmatch(r'(?:[a-f0-9]{32})?', revision)
                or type(body.get('proxy_enabled')) is not bool):
            raise ValueError()
        result.update(proxy_revision=revision, proxy_enabled=body['proxy_enabled'])
    return result


def message_fields(body):
    """保留可见聊天记录，但过滤原始 DOM、Cookie 和非约定字段。"""
    result = {key: response_text(body, key) for key in ('account_id', 'conversation_id', 'header')}
    if 'session_id' in body:
        result['session_id'] = uuid_field(body['session_id']) if body['session_id'] else ''
    if type(body.get('composer_ready')) is not bool:
        raise ValueError()
    messages = body.get('messages')
    if not isinstance(messages, list) or len(messages) > 200:
        raise ValueError()
    result.update(composer_ready=body['composer_ready'], messages=[])
    for item in messages:
        if (not isinstance(item, dict) or item.get('direction') not in {'incoming', 'outgoing', 'unknown'}
                or item.get('status') not in {'observed', 'sending', 'failed', 'unknown'}):
            raise ValueError()
        result['messages'].append({
            'id': response_text(item, 'id', 300), 'text': response_text(item, 'text', 16384, multiline=True),
            'direction': item['direction'], 'time': response_text(item, 'time'), 'status': item['status'],
            **({'content_type': response_text(item, 'content_type', 64)} if 'content_type' in item else {}),
        })
    for key in ('latest_receipt', 'pending_receipt'):
        if key in body:
            result[key] = contextual_receipt(body, key, result['account_id'], result['conversation_id'],
                                             pending=key == 'pending_receipt')
    return result


class XiaohongshuClient:
    """隔离代理配置，不继承 HTTP_PROXY，也不跟随任何上游重定向。"""
    def __init__(self, vault, url=None, transport=None):
        self.vault = vault
        self.url = worker_url(url or os.environ.get('MEDIADOCK_XHS_WORKER_URL') or 'http://127.0.0.1:59190')
        self.transport = transport

    @staticmethod
    def unavailable(configured):
        """缺少配置与暂时离线分开呈现，避免展示虚假的已连接状态。"""
        return {'state': 'offline' if configured else 'unconfigured', 'configured': configured,
                'available': False, 'browser_running': False, 'account_id': '',
                'account_name': '', 'conversation_id': ''}

    @staticmethod
    def uncertain(body):
        """请求可能已到达网页时停止重试，由操作员核对会话。"""
        return {'request_id': body['request_id'], 'status': 'needs_review',
                'account_id': body['account_id'], 'conversation_id': body['conversation_id'],
                'code': 'send_result_unknown'}

    def request(self, operation, body=None):
        """每次手动操作只请求一次；超时和结果异常均不得重发聊天。"""
        if operation not in OPERATIONS:
            raise XiaohongshuError('不支持的小红书操作', 'not_found', 404)
        method, path, read_timeout = OPERATIONS[operation]
        if method == 'POST':
            body = validate_body(operation, body)
        key = self.vault.get('xiaohongshu-bridge')
        if not key:
            if operation == 'status':
                return self.unavailable(False)
            raise XiaohongshuError('小红书浏览器服务尚未配置', 'not_configured', 409)
        try:
            # 响应大小有界；聊天历史最多 200 条，截图另校验解码后的 3 MiB 上限。
            limit = (5 * 1024 * 1024 if operation == 'screenshot' else
                     8 * 1024 * 1024 if operation in {'messages', 'select'} else 512 * 1024)
            with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                              timeout=httpx.Timeout(read_timeout, connect=3, write=5, pool=3)) as client:
                with client.stream(method, self.url + path, json=body if method == 'POST' else None,
                                   headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > limit:
                            raise ValueError()
                    if not 200 <= response.status_code < 300:
                        if operation in {'send', 'send_auto'} and (response.status_code >= 500 or 300 <= response.status_code < 400):
                            return self.uncertain(body)
                        self.upstream_error(response.status_code, bytes(raw))
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError()
            result = self.public_response(operation, payload, body)
            if operation == 'status':
                result.update(configured=True, available=True)
            return result
        except XiaohongshuError as error:
            if operation == 'status':
                return self.unavailable(True)
            if error.code != 'invalid_request':
                raise
            if operation in {'send', 'send_auto'}:
                return self.uncertain(body)
            raise XiaohongshuError('小红书浏览器返回了无效结果，请刷新后检查', 'invalid_worker_response', 502) from None
        except (httpx.HTTPError, ValueError, TypeError, KeyError, binascii.Error):
            if operation == 'status':
                return self.unavailable(True)
            if operation in {'send', 'send_auto'}:
                return self.uncertain(body)
            raise XiaohongshuError('小红书浏览器暂时不可用，请检查服务状态', 'worker_unavailable', 503) from None

    @staticmethod
    def upstream_error(status, raw):
        """上游只允许有限错误代码，错误正文和堆栈一律舍弃。"""
        try:
            code = json.loads(raw).get('error', {}).get('code')
        except (ValueError, AttributeError, TypeError):
            code = None
        if isinstance(code, str) and code in ERROR_MESSAGES and 400 <= status < 600:
            raise XiaohongshuError(ERROR_MESSAGES[code], code, status if status in {400, 429, 503} else 409)
        raise XiaohongshuError('小红书浏览器未完成操作，请检查连接状态', 'worker_unavailable', 503)

    def public_response(self, operation, payload, body):
        """响应按功能映射，禁止任意代理接口或返回浏览器凭据。"""
        if operation in {'status', 'start', 'stop', 'click', 'proxy_apply'}:
            return status_fields(payload)
        if operation in {'messages', 'select'}:
            return message_fields(payload)
        if operation == 'conversations':
            rows = payload.get('conversations')
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError()
            result = {'account_id': response_text(payload, 'account_id'), 'conversations': []}
            if 'session_id' in payload:
                result['session_id'] = uuid_field(payload['session_id']) if payload['session_id'] else ''
            for item in rows:
                if not isinstance(item, dict) or type(item.get('active')) is not bool:
                    raise ValueError()
                result['conversations'].append({
                    'id': response_text(item, 'id'), 'kind': response_text(item, 'kind', 64),
                    'name': response_text(item, 'name'), 'time': response_text(item, 'time'),
                    'summary': response_text(item, 'summary', 4096, multiline=True), 'active': item['active'],
                })
            return result
        if operation == 'screenshot':
            if (payload.get('mime') != 'image/png' or payload.get('state') not in STATES
                    or type(payload.get('width')) is not int or type(payload.get('height')) is not int
                    or not 1 <= payload['width'] <= 1280 or not 1 <= payload['height'] <= 900):
                raise ValueError()
            data = payload.get('data')
            if not isinstance(data, str) or len(data) > 4 * 1024 * 1024:
                raise ValueError()
            image = base64.b64decode(data, validate=True)
            if len(image) > 3 * 1024 * 1024 or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError()
            return {key: payload[key] for key in ('mime', 'data', 'width', 'height', 'state')} | {
                'frame_token': uuid_field(payload.get('frame_token')),
                **({'session_id': uuid_field(payload['session_id']) if payload['session_id'] else ''} if 'session_id' in payload else {})}
        if operation in {'send', 'send_auto', 'resolve'}:
            if (payload.get('request_id') != body['request_id']
                    or payload.get('account_id') != body['account_id']
                    or payload.get('conversation_id') != body['conversation_id']
                    or payload.get('status') not in {'sent', 'failed', 'needs_review'}):
                raise ValueError()
            if operation == 'resolve' and payload['status'] != ('sent' if body['resolution'] == 'sent' else 'failed'):
                raise ValueError()
            return receipt_fields(payload)
        raise ValueError()


class XiaohongshuProxyConfig:
    """独立保存小红书代理，再请求工作进程有序应用，不修改闲鱼配置。"""
    def __init__(self, vault, client):
        self.config = XianyuProxyConfig(vault, secret_name='xiaohongshu-proxy', display_name='小红书',
                                       reject_disabled_socks5_auth=True)
        self.client = client
        self.lock = threading.Lock()

    @staticmethod
    def public(config, status):
        """版本一致只说明浏览器载入配置，不能作为平台或代理连通性检测。"""
        running = bool(status.get('browser_running'))
        available = bool(status.get('available'))
        known = 'proxy_revision' in status and 'proxy_enabled' in status
        applied = bool(available and running and known and status['proxy_revision'] == config['revision']
                       and status['proxy_enabled'] == config['enabled'])
        return {key: config[key] for key in ('enabled', 'scheme', 'host', 'port', 'auth_configured', 'revision')} | {
            'applied': applied, 'active_enabled': bool(available and running and status.get('proxy_enabled')),
            'active_revision': status.get('proxy_revision', '') if available and running else '',
            'reconnect_required': not applied, 'worker_available': available, 'browser_running': running,
            'worker_state': status.get('state', 'offline'),
        }

    def read(self):
        """读取脱敏配置及浏览器实际版本，不触发启动、重连或探测外部网站。"""
        return self.public(self.config.public(), self.client.request('status'))

    def save(self, body):
        """串行保存和应用；应用失败仍返回已保存，避免误导用户再次提交凭据。"""
        allowed = {'enabled', 'scheme', 'host', 'port', 'username', 'password', 'clear_auth'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ProxyConfigError('代理配置包含不支持的字段')
        with self.lock:
            saved = self.config.save(body)
            try:
                status = self.client.request('proxy_apply', {})
                status.update(available=True)
                result = self.public(saved, status)
                outcome = ('applied' if result['applied'] else
                           'pending_start' if not result['browser_running'] else 'not_confirmed')
                return {**result, 'saved': True, 'application_status': outcome}
            except (XiaohongshuError, SecretStoreError) as error:
                # 不在超时后叠加第二次请求；后续读取可确认后台是否已经载入新配置。
                if isinstance(error, SecretStoreError):
                    error = XiaohongshuError('小红书连接凭据暂时无法读取，请检查服务配置', 'secret_store_unavailable', 503)
                result = self.public(saved, self.client.unavailable(True))
                return {**result, 'saved': True, 'application_status': 'failed',
                        'application_error': {'code': error.code, 'message': error.message}}


def install_routes(app, vault, admin, rate, data_dir):
    """沿用聚合后台会话、同源防护与限速，工作进程凭据不进入浏览器。"""
    client = XiaohongshuClient(vault)
    app.state.xiaohongshu = client
    automation = XhsAutomation(data_dir / 'xhs-automation.sqlite3', vault, client)
    app.state.xhs_automation = automation
    previous_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            automation.start()
            try:
                yield state
            finally:
                automation.stop()
    app.router.lifespan_context = lifespan
    proxy_config = XiaohongshuProxyConfig(vault, client)
    app.state.xiaohongshu_proxy = proxy_config
    router = APIRouter(prefix='/api/admin/xiaohongshu')

    @app.exception_handler(XiaohongshuError)
    async def xiaohongshu_error(_request, error):
        return JSONResponse({'error': {'message': error.message, 'code': error.code}}, status_code=error.status)

    app.add_exception_handler(AutomationError, xiaohongshu_error)

    def read_endpoint(operation):
        def endpoint(request: Request):
            admin(request)
            rate(request, 'xiaohongshu_read', maximum=180, window=60)
            if operation in {'messages', 'conversations'}:
                with automation.manual():
                    return client.request(operation)
            return client.request(operation)
        return endpoint

    def write_endpoint(operation):
        def endpoint(request: Request, body: dict):
            admin(request)
            rate(request, 'xiaohongshu_' + operation, maximum=20 if operation == 'send' else 30, window=60)
            with automation.manual():
                result = client.request(operation, body)
                if operation == 'resolve':
                    automation.resolved(result)
                return result
        return endpoint

    @router.get('/proxy')
    def read_proxy(request: Request):
        admin(request)
        rate(request, 'xiaohongshu_read', maximum=180, window=60)
        try:
            return proxy_config.read()
        except ProxyConfigError as error:
            raise XiaohongshuError(str(error)) from None

    @router.post('/proxy')
    def save_proxy(request: Request, body: dict):
        admin(request)
        rate(request, 'xiaohongshu_proxy_config', maximum=4, window=60)
        try:
            with automation.manual():
                return proxy_config.save(body)
        except ProxyConfigError as error:
            raise XiaohongshuError(str(error)) from None

    for operation, (method, path, _timeout) in OPERATIONS.items():
        if operation in {'proxy_apply', 'send_auto'}:
            continue  # 仅允许保存配置流程调用本机重连，浏览器没有任意代理应用入口。
        router.add_api_route(path, read_endpoint(operation) if method == 'GET' else write_endpoint(operation),
                             methods=[method], name='xiaohongshu_' + operation)

    @router.get('/automation')
    def read_automation(request: Request):
        admin(request)
        return automation.read()

    @router.post('/automation')
    def save_automation(request: Request, body: dict):
        admin(request)
        rate(request, 'xiaohongshu_automation_config', maximum=10, window=60)
        return automation.save(body)

    @router.post('/automation/preview')
    def preview_automation(request: Request, body: dict):
        admin(request)
        if set(body) != {'text'}:
            raise AutomationError('请仅提供待匹配的文字')
        rate(request, 'xiaohongshu_automation_preview', maximum=30, window=60)
        return automation.preview(body['text'])

    @router.post('/automation/ai-preview')
    def preview_ai(request: Request, body: dict):
        admin(request)
        rate(request, 'xiaohongshu_ai_preview', maximum=3, window=60)
        return automation.generate_preview(body)

    @router.get('/dashboard')
    def automation_dashboard(request: Request):
        admin(request)
        return automation.dashboard()

    @router.post('/automation/runs/{run_id}/resolve')
    def resolve_automation_run(run_id: str, request: Request, body: dict):
        admin(request)
        rate(request, 'xiaohongshu_automation_resolve', maximum=10, window=60)
        return automation.resolve_run(run_id, body)
    app.include_router(router)
