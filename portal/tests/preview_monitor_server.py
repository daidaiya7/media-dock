"""Loopback-only UI acceptance with temporary configuration and synthetic probes."""
import json
import sys
import tempfile
from pathlib import Path

from preview_server import Handler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from network_monitor import MonitorConflict, NetworkMonitor, _probe_id


def inventory(url):
    return [{'id': _probe_id(url, name), 'name': name, 'online': online, 'latency_ms': 42 if online else None}
            for name, online in [('演示探针甲', True), ('演示探针乙', False)]]


class MonitorHandler(Handler):
    monitor = None

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/api/admin/network-monitor':
            return self.reply(self.monitor.admin_config())
        if path == '/api/public/network-status':
            return self.reply(self.monitor.public_status())
        if path == '/api/admin/network-probe':
            return self.reply(self.monitor.probe_config())
        super().do_GET()

    def do_POST(self):
        if self.path not in {'/api/admin/network-monitor', '/api/admin/network-probe', '/api/admin/network-probe/refresh'}:
            return self.reply({'error': {'message': '隔离演示禁止业务写入'}}, 409)
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 <= length <= 262144:
            return self.reply({'error': {'message': '请求过大'}}, 413)
        try:
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/admin/network-monitor':
                self.monitor.save_config(body)
                self.monitor.collect()  # Only the synthetic callables supplied below.
                return self.reply(self.monitor.admin_config())
            if self.path == '/api/admin/network-probe':
                return self.reply(self.monitor.save_probe_config(body))
            return self.reply(self.monitor.refresh_probe())
        except MonitorConflict:
            return self.reply({'error': {'code': 'monitor_config_conflict', 'message': '配置已更新，请重新读取。'}}, 409)
        except (ValueError, OSError):
            return self.reply({'error': {'message': '演示配置无效'}}, 400)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='mediadock-monitor-preview-') as temporary:
        MonitorHandler.monitor = NetworkMonitor(
            temporary, probe=lambda _: {'status': 'healthy', 'latency_ms': 30},
            icmp_probe=lambda _: {'status': 'healthy', 'latency_ms': 20}, probe_fetch=inventory)
        MonitorHandler.monitor.save_probe_config({'url': 'https://probe.example.net'})
        MonitorHandler.monitor.refresh_probe()
        if '--latency-demo' in sys.argv:
            config = MonitorHandler.monitor.admin_config()
            config['groups'][0]['nodes'][0].update(method='browser', target='https://probe.example.com/health.png')
            config['groups'][0]['nodes'].append({'id': 'n-' + 'a' * 32, 'name': '示例 CDN', 'method': 'browser', 'target': 'https://edge.example.com/ping'})
            config['groups'][1]['nodes'][0].update(method='mmwx', target=_probe_id('https://probe.example.net', '演示探针甲'))
            MonitorHandler.monitor.save_config({key: config[key] for key in ('version', 'revision', 'groups')})
            MonitorHandler.monitor.collect()
        print('Isolated monitor preview: http://127.0.0.1:59191', flush=True)
        ThreadingHTTPServer(('127.0.0.1', 59191), MonitorHandler).serve_forever()
