"""Private server sampling and explicit browser CDN tests with public aliases."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit


INTERVAL_SECONDS = 60
STALE_SECONDS = 180
PROBE_SECONDS = 5
MAX_BODY_BYTES = 64 * 1024
MAX_PROBE_BODY_BYTES = 1024 * 1024
MAX_CONFIG_BYTES = 256 * 1024
MAX_GROUPS = 12
MAX_NODES = 40
MAX_WORKERS = 8
MAX_PROBE_LATENCY_MS = 60000
NODES = (
    ('cdn', '示例 CDN', 'cdn'),
    ('origin', '源站', 'origin'),
    ('relay-a', '中转线路 A', 'relays'),
    ('relay-b', '中转线路 B', 'relays'),
    ('relay-c', '中转线路 C', 'relays'),
)
GROUPS = (('cdn', 'CDN 监测'), ('origin', '源站监测'), ('relays', '线路机监测'))
NODE_IDS = {node[0] for node in NODES}
SAMPLED_STATUSES = {'healthy', 'degraded', 'down', 'unknown'}
CONFIG_ERROR = '监测配置无效，请检查分组、名称和监测方式；浏览器测速使用无凭据或参数的公网 HTTPS URL，HTTP 使用公网 URL，ICMP 使用裸公网 IP 或域名。'


class MonitorConflict(ValueError):
    """A stale administrator must reload instead of overwriting another edit."""


def _config_revision(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _config_nodes(config):
    return [row for group in config['groups'] for row in group['nodes']]


def _canonical_legacy(config):
    rows = {row['id']: row for row in config['nodes']}
    return {'version': 2, 'groups': [
        {'id': ident, 'name': name, 'nodes': [rows[node_id] for node_id, _, group in NODES if group == ident]}
        for ident, name in GROUPS]}


def _global_address(value):
    address = ipaddress.ip_address(value)
    if (not address.is_global or address.is_multicast or address.is_reserved
            or address.is_unspecified or address.is_loopback or address.is_link_local):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        # Do not allow IPv4 tunnelling/translation to circumvent IPv4 validation.
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network('64:ff9b::/96') or address in ipaddress.ip_network('64:ff9b:1::/48'):
            return False
    return True


def validate_url(value):
    """Validation never resolves a hostname and never includes input in errors."""
    try:
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError()
        value = value.strip()
        if not value:
            return ''
        if any(ord(char) < 33 or ord(char) > 126 for char in value) or '\\' in value:
            raise ValueError()
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        if (parsed.scheme not in ('http', 'https') or not host or parsed.username is not None
                or parsed.password is not None or '?' in value or '#' in value or '%' in host):
            raise ValueError()
        port = parsed.port  # Also validates numeric syntax and range.
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Require a qualified DNS name, exclude known local/internal namespaces.
            host = host.rstrip('.')
            if (len(host) > 253 or '.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host)
                    or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan', '.test', '.invalid'))
                    or host in {'localhost', 'metadata.google', 'metadata.google.internal'}):
                raise ValueError()
            if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
                raise ValueError()
            # Numeric IPv4 spellings accepted by some resolvers must not be DNS names.
            if all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', part) for part in host.split('.')):
                raise ValueError()
        else:
            if not _global_address(str(address)):
                raise ValueError()
            host = str(address)
        netloc = ('[' + host + ']') if ':' in host else host
        if port is not None:
            netloc += ':' + str(port)
        return urlunsplit((parsed.scheme, netloc, parsed.path or '/', '', ''))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(CONFIG_ERROR) from None


def validate_icmp_target(value):
    """Only a numeric public IP or qualified DNS name, never CLI syntax or a URL."""
    try:
        if not isinstance(value, str) or len(value) > 253:
            raise ValueError()
        value = value.strip().lower()
        if not value:
            return ''
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError()
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            host = value.rstrip('.')
            if ('.' not in host or not re.fullmatch(r'[a-z0-9.-]+', host)
                    or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan', '.test', '.invalid'))
                    or host in {'localhost', 'metadata.google', 'metadata.google.internal'}):
                raise ValueError()
            if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in host.split('.')):
                raise ValueError()
            if all(re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)', part) for part in host.split('.')):
                raise ValueError()
            return host
        if not _global_address(str(address)):
            raise ValueError()
        return str(address)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(CONFIG_ERROR) from None


def validate_name(value):
    """Aliases are public; do not let target addresses accidentally become names."""
    if not isinstance(value, str):
        raise ValueError(CONFIG_ERROR)
    value = value.strip()
    normalized = unicodedata.normalize('NFKC', value)
    if (not 1 <= len(value) <= 40 or any(unicodedata.category(char).startswith('C') for char in value)
            or any(char in normalized for char in '/\\@:<>{}[]')
            or re.search(r'\w[.。．]\w', normalized, flags=re.UNICODE)):
        raise ValueError(CONFIG_ERROR)
    return value


def validate_probe_url(value):
    value = validate_url(value)
    if not value:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or parsed.path != '/':
        raise ValueError(CONFIG_ERROR)
    return value.rstrip('/')


def _validate_method_target(method, target):
    if not isinstance(method, str) or method not in ('http', 'icmp', 'mmwx', 'browser'):
        raise ValueError(CONFIG_ERROR)
    if method in ('http', 'browser'):
        value = validate_url(target)
        if method == 'browser' and value and urlsplit(value).scheme != 'https':
            raise ValueError(CONFIG_ERROR)
        return value
    if method == 'icmp':
        return validate_icmp_target(target)
    if not isinstance(target, str) or (target and not re.fullmatch(r'[0-9a-f]{64}', target)):
        raise ValueError(CONFIG_ERROR)
    return target


def validate_v2_config(body):
    """Validate persisted canonical groups without a revision transport field."""
    if (not isinstance(body, dict) or set(body) != {'version', 'groups'}
            or type(body['version']) is not int or body['version'] != 2
            or not isinstance(body['groups'], list) or len(body['groups']) > MAX_GROUPS):
        raise ValueError(CONFIG_ERROR)
    groups, group_ids, node_ids = [], set(), set()
    for group in body['groups']:
        if (not isinstance(group, dict) or set(group) != {'id', 'name', 'nodes'}
                or not isinstance(group['id'], str)
                or not (group['id'] in {ident for ident, _ in GROUPS}
                        or re.fullmatch(r'g-[0-9a-f]{32}', group['id']))
                or group['id'] in group_ids or not isinstance(group['nodes'], list)):
            raise ValueError(CONFIG_ERROR)
        group_ids.add(group['id'])
        nodes = []
        for row in group['nodes']:
            if (not isinstance(row, dict) or set(row) != {'id', 'name', 'method', 'target'}
                    or not isinstance(row['id'], str)
                    or not (row['id'] in NODE_IDS or re.fullmatch(r'n-[0-9a-f]{32}', row['id']))
                    or row['id'] in node_ids):
                raise ValueError(CONFIG_ERROR)
            node_ids.add(row['id'])
            if len(node_ids) > MAX_NODES:
                raise ValueError(CONFIG_ERROR)
            nodes.append({'id': row['id'], 'name': validate_name(row['name']), 'method': row['method'],
                          'target': _validate_method_target(row['method'], row['target'])})
        groups.append({'id': group['id'], 'name': validate_name(group['name']), 'nodes': nodes})
    return {'version': 2, 'groups': groups}


def validate_config(body, previous=None):
    """Compatibility validation for the original fixed-node editor."""
    if not isinstance(body, dict) or set(body) != {'nodes'} or not isinstance(body['nodes'], list) or len(body['nodes']) != len(NODES):
        raise ValueError(CONFIG_ERROR)
    old_names = {row['id']: row['name'] for row in (previous or {}).get('nodes', [])}
    default_names = {ident: name for ident, name, _ in NODES}
    supplied = {}
    shapes = ({'id', 'url'}, {'id', 'method', 'target'}, {'id', 'name', 'method', 'target'})
    for row in body['nodes']:
        if (not isinstance(row, dict) or set(row) not in shapes or not isinstance(row['id'], str)
                or row['id'] not in NODE_IDS or row['id'] in supplied):
            raise ValueError(CONFIG_ERROR)
        method, target = ('http', row['url']) if 'url' in row else (row['method'], row['target'])
        target = _validate_method_target(method, target)
        name = validate_name(row.get('name', old_names.get(row['id'], default_names[row['id']])))
        supplied[row['id']] = {'name': name, 'method': method, 'target': target}
    return {'nodes': [{'id': ident, **supplied[ident]} for ident, _, _ in NODES]}


def _probe_id(source_url, exact_name):
    return hashlib.sha256(json.dumps([source_url, exact_name], ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def _probe_source_fingerprint(source_url):
    return hashlib.sha256(source_url.encode('utf-8')).hexdigest()


def _probe_latency(value):
    return value if type(value) is int and 0 <= value <= MAX_PROBE_LATENCY_MS else None


def _average_probe_latency(series):
    """Match the probe overview's rounded mean without retaining target labels."""
    if not isinstance(series, list):
        return None
    values = [row['current_ms'] for row in series if isinstance(row, dict)
              and type(row.get('current_ms')) in (int, float)
              and 0 <= row['current_ms'] <= MAX_PROBE_LATENCY_MS
              and math.isfinite(row['current_ms'])]
    return math.floor(sum(values) / len(values) + 0.5) if values else None


def _probe_nodes(body, source_url):
    if not isinstance(body, dict) or body.get('enabled') is not True or not isinstance(body.get('servers'), list):
        raise ValueError('probe_unavailable')
    if len(body['servers']) > 5000:
        raise ValueError('probe_unavailable')
    candidates, counts = [], {}
    for row in body['servers']:
        if not isinstance(row, dict):
            continue
        name, online = row.get('name'), row.get('online')
        # Provider names stay private and are never used as public aliases.
        if not isinstance(name, str) or not name.strip() or len(name) > 512 or any(unicodedata.category(c).startswith('C') for c in name):
            continue
        counts[name] = counts.get(name, 0) + 1
        if type(online) is bool:
            candidates.append({'id': _probe_id(source_url, name), 'name': name, 'online': online,
                               'latency_ms': _average_probe_latency(row.get('ping')) if online else None})
    return [row for row in candidates if counts[row['name']] == 1]


def _fingerprint(method, target):
    value = json.dumps([method, target], separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _atomic_json(path, body):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(body, output, ensure_ascii=False, separators=(',', ':'))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path, max_bytes=MAX_BODY_BYTES):
    try:
        with path.open('rb') as source:
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _resolve_public(host, port, deadline):
    results, errors, done = [], [], threading.Event()

    def resolve():
        try:
            results.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            errors.append(True)
        finally:
            done.set()

    threading.Thread(target=resolve, daemon=True).start()
    if not done.wait(max(0, min(2, deadline - time.monotonic()))) or errors:
        raise OSError('probe_failed')
    addresses = list(dict.fromkeys(row[4][0] for row in results if row[0] in (socket.AF_INET, socket.AF_INET6)))
    if not addresses or any(not _global_address(address) for address in addresses):
        raise OSError('probe_failed')
    return addresses[0]


def _remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError('probe_failed')
    return value


def _http_fetch(value, deadline, *, max_bytes=MAX_BODY_BYTES, accept='*/*', extra_headers=None):
    parsed = urlsplit(value)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    address = _resolve_public(parsed.hostname, port, deadline)
    active = []

    class PinnedConnection(http.client.HTTPConnection):
        def connect(self):
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            raw = socket.socket(family, socket.SOCK_STREAM)
            active[:] = [raw]
            try:
                raw.settimeout(_remaining(deadline))
                # Connect to the already validated numeric IP; no second DNS lookup.
                raw.connect((address, port, 0, 0) if family == socket.AF_INET6 else (address, port))
                self.sock = raw
                if parsed.scheme == 'https':
                    self.sock = ssl.create_default_context().wrap_socket(
                        raw, server_hostname=parsed.hostname, do_handshake_on_connect=False)
                    active[:] = [self.sock]
                    self.sock.settimeout(_remaining(deadline))
                    self.sock.do_handshake()
                self.sock.settimeout(_remaining(deadline))
            except BaseException:
                raw.close()
                raise

    client = PinnedConnection(parsed.hostname, port, timeout=_remaining(deadline))

    def abort():
        for sock in active[:]:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    timer = threading.Timer(_remaining(deadline), abort)
    timer.daemon = True
    timer.start()
    response = None
    try:
        client.request('GET', parsed.path or '/', headers={
            'User-Agent': 'MediaDock-Health/1.0', 'Accept': accept, 'Accept-Encoding': 'identity', 'Connection': 'close',
            **(extra_headers or {}),
        })
        response = client.getresponse()
        if not 200 <= response.status <= 299:
            return None  # No redirects, authentication attempts, or body logging.
        body = response.read(max_bytes + 1)
        _remaining(deadline)
        return body if len(body) <= max_bytes else None
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        client.close()


def _http_probe(value, deadline):
    return _http_fetch(value, deadline) is not None


def fetch_probe_nodes(source_url):
    """Fetch only the documented endpoint, retaining only a private allowlist."""
    source_url = validate_probe_url(source_url)
    if not source_url:
        raise ValueError('probe_unavailable')
    raw = _http_fetch(source_url + '/api/probe', time.monotonic() + PROBE_SECONDS,
                      max_bytes=MAX_PROBE_BODY_BYTES, accept='application/json', extra_headers={
                          'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
                          'User-Agent': 'MediaDock-Network-Monitor/1.0',
                      })
    if raw is None:
        raise ValueError('probe_unavailable')
    return _probe_nodes(json.loads(raw), source_url)


def probe_url(value):
    started = time.monotonic()
    try:
        value = validate_url(value)
        if not value:
            return {'status': 'unconfigured', 'latency_ms': None}
        success = _http_probe(value, started + PROBE_SECONDS)
        elapsed = max(0, int(round((time.monotonic() - started) * 1000)))
        return {'status': ('degraded' if elapsed > 1500 else 'healthy') if success else 'down', 'latency_ms': elapsed}
    except Exception:
        return {'status': 'down', 'latency_ms': None}


def _ping_binary():
    for value in ('/usr/bin/ping', '/bin/ping'):
        if Path(value).is_file() and os.access(value, os.X_OK):
            return value
    return None


def probe_icmp(value):
    started = time.monotonic()
    try:
        value = validate_icmp_target(value)
        if not value:
            return {'status': 'unconfigured', 'latency_ms': None}
        binary = _ping_binary()
        if not binary:
            return {'status': 'unknown', 'latency_ms': None}
        deadline = started + PROBE_SECONDS
        address = _resolve_public(value, 0, deadline)
        # Only a validated numeric address reaches the child process. No shell or
        # hostname is used, so neither command injection nor a second DNS lookup
        # can alter the destination. A single packet produces a bounded output.
        timeout = _remaining(deadline)
        try:
            result = subprocess.run(
                [binary, '-n', '-c', '1', '-W', '3', '--', address],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False,
                env={'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'},
            )
        except OSError:
            return {'status': 'unknown', 'latency_ms': None}
        _remaining(deadline)
        if result.returncode == 1:
            return {'status': 'down', 'latency_ms': None}
        if result.returncode != 0:
            # iputils returns 2 for local execution/capability errors. They are
            # not evidence of a target outage and must not appear as down.
            return {'status': 'unknown', 'latency_ms': None}
        raw = result.stdout
        if not isinstance(raw, bytes) or len(raw) > MAX_BODY_BYTES:
            return {'status': 'unknown', 'latency_ms': None}
        match = re.search(rb'\btime[=<]([0-9]+(?:\.[0-9]+)?)\s*ms\b', raw)
        measured = float(match[1]) if match else None
        if measured is not None and (not math.isfinite(measured) or measured > PROBE_SECONDS * 1000 + 1000):
            return {'status': 'unknown', 'latency_ms': None}
        latency = _latency(int(round(measured))) if measured is not None else None
        return {'status': 'degraded' if latency is not None and latency > 1500 else 'healthy', 'latency_ms': latency}
    except (subprocess.TimeoutExpired, TimeoutError):
        return {'status': 'down', 'latency_ms': None}
    except (FileNotFoundError, PermissionError):
        return {'status': 'unknown', 'latency_ms': None}
    except Exception:
        return {'status': 'down', 'latency_ms': None}


def _timestamp(value, now):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or value > now + 5:
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except (OverflowError, OSError, ValueError):
        return None


def _latency(value):
    return value if type(value) is int and 0 <= value <= PROBE_SECONDS * 1000 + 1000 else None


def _sampled_status(value):
    return value if isinstance(value, str) and value in SAMPLED_STATUSES else None


class NetworkMonitor:
    def __init__(self, data_dir, *, now=time.time, probe=probe_url, icmp_probe=probe_icmp, probe_fetch=fetch_probe_nodes):
        self.data_dir = Path(data_dir)
        self.config_path = self.data_dir / 'network-monitor-config.json'
        self.status_path = self.data_dir / 'network-monitor-status.json'
        self.probe_config_path = self.data_dir / 'network-probe-config.json'
        self.probe_cache_path = self.data_dir / 'network-probe-cache.json'
        self.now, self.probe, self.icmp_probe, self.probe_fetch = now, probe, icmp_probe, probe_fetch

    def _config(self):
        if not self.config_path.exists():
            return _canonical_legacy({'nodes': [{'id': ident, 'name': name, 'method': 'http', 'target': ''}
                                               for ident, name, _ in NODES]})
        raw = _read_json(self.config_path, MAX_CONFIG_BYTES)
        try:
            if 'version' in raw or 'groups' in raw:
                return validate_v2_config(raw)
            return _canonical_legacy(validate_config(raw))
        except ValueError:
            # Only a genuinely new installation gets defaults. Corrupt or
            # oversized persisted data must never resurrect removed monitors.
            return {'version': 2, 'groups': []}

    def _probe_url(self):
        try:
            return validate_probe_url(_read_json(self.probe_config_path).get('url', ''))
        except ValueError:
            return ''

    def _probe_cache(self, url=None):
        url = self._probe_url() if url is None else url
        cache = _read_json(self.probe_cache_path, MAX_PROBE_BODY_BYTES)
        now = self.now()
        checked = _timestamp(cache.get('checked_at'), now)
        valid = (bool(url) and cache.get('source') == _probe_source_fingerprint(url)
                 and checked is not None and now - cache['checked_at'] <= STALE_SECONDS
                 and cache.get('status') == 'ready' and isinstance(cache.get('nodes'), list))
        if not valid:
            return {'status': 'unavailable' if url else 'unconfigured', 'checked_at': checked if cache.get('source') == _probe_source_fingerprint(url) else None, 'nodes': []}
        nodes, ids = [], set()
        for row in cache['nodes']:
            if (not isinstance(row, dict) or set(row) not in ({'id', 'name', 'online'}, {'id', 'name', 'online', 'latency_ms'})
                    or not isinstance(row['name'], str)
                    or not row['name'].strip() or len(row['name']) > 512
                    or any(unicodedata.category(c).startswith('C') for c in row['name'])
                    or not isinstance(row['id'], str) or row['id'] != _probe_id(url, row['name'])
                    or row['id'] in ids or type(row['online']) is not bool):
                return {'status': 'unavailable', 'checked_at': checked, 'nodes': []}
            ids.add(row['id'])
            nodes.append({'id': row['id'], 'name': row['name'], 'online': row['online'],
                          'latency_ms': _probe_latency(row.get('latency_ms')) if row['online'] else None})
        return {'status': 'ready', 'checked_at': checked, 'nodes': nodes}

    def probe_config(self):
        url = self._probe_url()
        return {'provider': 'mmwx', 'url': url, 'configured': bool(url), **self._probe_cache(url)}

    def save_probe_config(self, body):
        if not isinstance(body, dict) or set(body) != {'url'}:
            raise ValueError(CONFIG_ERROR)
        url = validate_probe_url(body['url'])
        changed = url != self._probe_url()
        _atomic_json(self.probe_config_path, {'url': url})
        if changed:
            _atomic_json(self.probe_cache_path, {'source': _probe_source_fingerprint(url), 'status': 'unavailable', 'nodes': []})
        return self.probe_config()

    def refresh_probe(self):
        url = self._probe_url()
        if not url:
            return self.probe_config()
        snapshot = {'source': _probe_source_fingerprint(url), 'checked_at': self.now(), 'status': 'unavailable', 'nodes': []}
        try:
            rows = self.probe_fetch(url)
            # Dependency injection also passes through the same strict whitelist.
            if not isinstance(rows, list) or len(rows) > 5000:
                raise ValueError('probe_unavailable')
            verified, ids = [], set()
            for row in rows:
                if (not isinstance(row, dict) or not isinstance(row.get('name'), str)
                        or not row['name'].strip() or len(row['name']) > 512
                        or any(unicodedata.category(c).startswith('C') for c in row['name'])
                        or row.get('id') != _probe_id(url, row['name']) or row['id'] in ids
                        or type(row.get('online')) is not bool):
                    raise ValueError('probe_unavailable')
                ids.add(row['id'])
                verified.append({**{key: row[key] for key in ('id', 'name', 'online')},
                                 'latency_ms': _probe_latency(row.get('latency_ms')) if row['online'] else None})
            if len(json.dumps(verified, ensure_ascii=False).encode('utf-8')) > MAX_PROBE_BODY_BYTES - 1024:
                raise ValueError('probe_unavailable')
            snapshot.update(status='ready', nodes=verified)
        except Exception:
            pass
        snapshot['checked_at'] = self.now()
        _atomic_json(self.probe_cache_path, snapshot)
        return self.probe_config()

    def _target_fingerprint(self, row, source_url=None):
        if row['method'] == 'mmwx':
            source = self._probe_url() if source_url is None else source_url
            return _fingerprint('mmwx', [source, row['target']])
        return _fingerprint(row['method'], row['target'])

    def public_status(self, config=None):
        config = self._config() if config is None else config
        snapshot, now = _read_json(self.status_path, MAX_CONFIG_BYTES), self.now()
        samples = snapshot.get('nodes') if isinstance(snapshot.get('nodes'), dict) else {}
        source_url = self._probe_url()
        cache = self._probe_cache(source_url)
        probe_nodes = {row['id']: row for row in cache['nodes']}
        groups = []
        for group in config['groups']:
            nodes = []
            for target in group['nodes']:
                ident = target['id']
                row = {'id': ident, 'name': target['name'], 'basis': target['method'],
                       'status': 'unconfigured' if not target['target'] else 'unknown',
                       'latency_ms': None, 'checked_at': None}
                if target['method'] == 'browser':
                    # Only this explicit method opts its configured test URL into
                    # the public response. Server-side sampling is never a proxy
                    # for the visitor's own route through the CDN.
                    if target['target']:
                        row.update(status='client_pending', test_url=target['target'])
                    nodes.append(row)
                    continue
                sample = samples.get(ident)
                if target['target'] and isinstance(sample, dict) and sample.get('fingerprint') == self._target_fingerprint(target, source_url):
                    checked = _timestamp(sample.get('checked_at'), now)
                    row['checked_at'] = checked
                    if checked and now - sample['checked_at'] <= STALE_SECONDS and _sampled_status(sample.get('status')):
                        row.update(status=sample['status'], latency_ms=_latency(sample.get('latency_ms')))
                    # An unavailable or changed inventory must immediately invalidate
                    # last-known green status, even before the next timer tick.
                    if target['method'] == 'mmwx':
                        remote = probe_nodes.get(target['target'])
                        row['latency_ms'] = None
                        if cache['status'] != 'ready' or remote is None:
                            row['status'] = 'unknown'
                        elif row['status'] in ('healthy', 'down'):
                            row['status'] = 'healthy' if remote['online'] else 'down'
                            row['latency_ms'] = remote['latency_ms'] if remote['online'] else None
                nodes.append(row)
            groups.append({'id': group['id'], 'name': group['name'], 'nodes': nodes})
        return {'checked_at': _timestamp(snapshot.get('checked_at'), now), 'interval_seconds': INTERVAL_SECONDS, 'groups': groups}

    def admin_config(self):
        config = self._config()
        return {**config, 'revision': _config_revision(config),
                'nodes': [{**row, 'url': row['target'] if row['method'] == 'http' else ''}
                          for row in _config_nodes(config)], 'status': self.public_status(config)}

    def browser_test_origins(self):
        """Only explicitly public browser targets may extend the page's CSP."""
        return sorted({urlunsplit((*urlsplit(row['target'])[:2], '', '', ''))
                       for row in _config_nodes(self._config())
                       if row['method'] == 'browser' and row['target']})

    def save_config(self, body):
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.data_dir / 'network-monitor-config.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            previous = self._config()
            old_rows = _config_nodes(previous)
            raw = _read_json(self.config_path, MAX_CONFIG_BYTES)
            if isinstance(body, dict) and ('version' in body or 'groups' in body or 'revision' in body):
                if (set(body) != {'version', 'revision', 'groups'} or not isinstance(body['revision'], str)
                        or not re.fullmatch(r'[0-9a-f]{64}', body['revision'])):
                    raise ValueError(CONFIG_ERROR)
                if body['revision'] != _config_revision(previous):
                    raise MonitorConflict('监测配置已被修改，请刷新页面后再保存。')
                config = validate_v2_config({key: body[key] for key in ('version', 'groups')})
                persisted = config
            else:
                if 'version' in raw or 'groups' in raw:
                    raise MonitorConflict('监测配置已升级，请刷新页面后再保存。')
                persisted = validate_config(body, {'nodes': old_rows})
                if ((any('url' in row for row in body['nodes'])
                     and any(row['method'] in ('icmp', 'mmwx', 'browser') and row['target'] for row in old_rows))
                        or (any('name' not in row for row in body['nodes'])
                            and any(row['method'] == 'mmwx' for row in old_rows))):
                    raise MonitorConflict('监测方式已更新，请刷新管理页面后再保存。')
                config = _canonical_legacy(persisted)
            selectable = {row['id'] for row in self._probe_cache()['nodes']}
            old_targets = {row['id']: row for row in old_rows}
            for row in _config_nodes(config):
                if row['method'] == 'mmwx' and row['target'] and row['target'] not in selectable:
                    old = old_targets.get(row['id'], {})
                    if old.get('method') != 'mmwx' or old.get('target') != row['target']:
                        raise ValueError('请先刷新探针列表并选择节点。')
            if len(json.dumps(persisted, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > MAX_CONFIG_BYTES:
                raise ValueError(CONFIG_ERROR)
            _atomic_json(self.config_path, persisted)
            return self.admin_config()
        finally:
            os.close(descriptor)

    def collect(self):
        """Timer/CLI only. Public reads never execute this or make network requests."""
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.data_dir / 'network-monitor.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            rows = _config_nodes(self._config())
            source_url = self._probe_url()
            inventory = self.refresh_probe() if any(row['method'] == 'mmwx' and row['target'] for row in rows) else None
            probe_nodes = {row['id']: row for row in inventory['nodes']} if inventory and inventory['status'] == 'ready' else {}

            def sample(row):
                try:
                    if row['method'] == 'mmwx':
                        remote = probe_nodes.get(row['target'])
                        result = {'status': ('healthy' if remote['online'] else 'down') if remote else 'unknown',
                                  'latency_ms': remote['latency_ms'] if remote and remote['online'] else None}
                    else:
                        probe = self.probe if row['method'] == 'http' else self.icmp_probe
                        result = probe(row['target'])
                except Exception:
                    result = {}
                result = result if isinstance(result, dict) else {}
                return {'fingerprint': self._target_fingerprint(row, source_url), 'checked_at': self.now(),
                        'status': _sampled_status(result.get('status')) or ('unknown' if row['method'] == 'mmwx' else 'down'),
                        'latency_ms': (_probe_latency if row['method'] == 'mmwx' else _latency)(result.get('latency_ms'))}

            selected = [row for row in rows if row['target'] and row['method'] != 'browser']
            # Each sampler has a five-second budget; forty targets use at most
            # five batches. The separately fetched probe inventory is read once.
            with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='network-monitor') as executor:
                samples = dict(zip((row['id'] for row in selected), executor.map(sample, selected)))
            _atomic_json(self.status_path, {'checked_at': self.now(), 'nodes': samples})
            return True
        finally:
            os.close(descriptor)
