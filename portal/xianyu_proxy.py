"""Private proxy configuration for the Xianyu service; never process-wide proxy state."""
import ipaddress
import json
import re
import secrets
import threading
from pathlib import Path


class ProxyConfigError(Exception):
    """Safe validation failure without submitted credentials."""


class XianyuProxyConfig:
    def __init__(self, vault, status_path=None, *, secret_name='xianyu-proxy', display_name='闲鱼',
                 reject_disabled_socks5_auth=False):
        self.vault = vault
        self.status_path = Path(status_path) if status_path else None
        # 每个服务使用独立凭据名称；旧闲鱼调用的默认值和状态文件语义不变。
        self.secret_name = secret_name
        self.display_name = display_name
        self.reject_disabled_socks5_auth = reject_disabled_socks5_auth
        self.lock = threading.Lock()

    def _read(self):
        raw = self.vault.get(self.secret_name)
        if not raw:
            return {'enabled': False, 'scheme': 'http', 'host': '', 'port': 0,
                    'username': '', 'password': '', 'revision': ''}
        try:
            config = json.loads(raw)
            if not isinstance(config, dict):
                raise ValueError()
            return config
        except (ValueError, TypeError):
            raise ProxyConfigError(f'{self.display_name}代理配置无法读取，请重新保存配置') from None

    @staticmethod
    def _text(value, limit):
        if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ProxyConfigError('代理配置包含无效字符或超出长度限制')
        return value

    def public(self):
        config = self._read()
        active = {}
        if self.status_path:
            try:
                active = json.loads(self.status_path.read_text())
                if not isinstance(active, dict):
                    active = {}
            except (OSError, ValueError):
                pass
        # Only non-sensitive, explicitly selected fields enter the browser.
        return {'enabled': bool(config.get('enabled')), 'scheme': config.get('scheme', 'http'),
                'host': config.get('host', ''), 'port': config.get('port', 0),
                'auth_configured': bool(config.get('username') or config.get('password')),
                'revision': config.get('revision', ''),
                'applied': bool(config.get('revision')) and active.get('revision') == config.get('revision'),
                'active_enabled': bool(active.get('enabled')),
                'restart_required': True}

    def save(self, body):
        with self.lock:
            previous = self._read()
            if type(body.get('enabled')) is not bool:
                raise ProxyConfigError('请选择直连或独立代理')
            scheme = self._text(body.get('scheme', 'http'), 10)
            if scheme not in ('http', 'https', 'socks5'):
                raise ProxyConfigError('代理类型只支持 HTTP、HTTPS 或 SOCKS5')
            host = self._text(body.get('host', ''), 253).strip()
            if '%' in host:
                raise ProxyConfigError('代理服务器不支持带区域标识的 IPv6 地址')
            if host:
                try:
                    host = str(ipaddress.ip_address(host.strip('[]')))
                except ValueError:
                    if (not re.fullmatch(r'(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', host)
                            or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label)
                                   for label in host.split('.'))):
                        raise ProxyConfigError('服务器只填写 IP 或域名，不含协议、端口、路径和账号') from None
            port = body.get('port', 0)
            if type(port) is not int or port < 0 or port > 65535:
                raise ProxyConfigError('代理端口应为 1 至 65535 的整数')
            if body['enabled'] and (not host or not port):
                raise ProxyConfigError('启用代理需要填写服务器与端口')
            username = self._text(body.get('username', ''), 256)
            password = self._text(body.get('password', ''), 512)
            # A different server must never receive credentials saved for the old server.
            same_server = all(previous.get(k) == v for k, v in (('scheme', scheme), ('host', host), ('port', port)))
            clear_auth = body.get('clear_auth', False)
            if type(clear_auth) is not bool:
                raise ProxyConfigError('清除认证选项无效')
            if clear_auth:
                username, password = '', ''
            elif not username and not password and same_server:
                username, password = previous.get('username', ''), previous.get('password', '')
            elif not username and password:
                raise ProxyConfigError('更新代理密码时请同时填写用户名')
            if (body['enabled'] or self.reject_disabled_socks5_auth) and scheme == 'socks5' and (username or password):
                raise ProxyConfigError('SOCKS5 暂不支持账号认证，请使用无认证 SOCKS5 或带认证的 HTTP/HTTPS 代理')
            config = {'enabled': body['enabled'], 'scheme': scheme, 'host': host, 'port': port,
                      'username': username, 'password': password, 'revision': secrets.token_hex(16)}
            self.vault.set(self.secret_name, json.dumps(config, ensure_ascii=True, separators=(',', ':')))
            return self.public()
