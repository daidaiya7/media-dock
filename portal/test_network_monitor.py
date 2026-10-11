"""Isolated monitoring checks: no real DNS, socket, HTTP, or third-party requests."""
import fcntl
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import network_monitor as nm


NOW = 1791700000
TARGET = 'https://cdn.example.com/health'


def config(**targets):
    return {'nodes': [{'id': ident, 'url': targets.get(ident, '')} for ident, _, _ in nm.NODES]}


def modern_config(**targets):
    return {'nodes': [{'id': ident, 'method': targets.get(ident, ('http', ''))[0],
                       'target': targets.get(ident, ('http', ''))[1]} for ident, _, _ in nm.NODES]}


def node(status, ident='cdn'):
    return next(n for group in status['groups'] for n in group['nodes'] if n['id'] == ident)


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.clock = NOW
        self.probe = Mock(return_value={'status': 'healthy', 'latency_ms': 123})
        self.icmp_probe = Mock(return_value={'status': 'healthy', 'latency_ms': 20})
        self.monitor = nm.NetworkMonitor(self.path, now=lambda: self.clock, probe=self.probe, icmp_probe=self.icmp_probe)

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_install_is_five_unconfigured_nodes_and_reads_never_probe(self):
        status = self.monitor.public_status()
        self.assertEqual(set(status), {'checked_at', 'interval_seconds', 'groups'})
        self.assertEqual(status['interval_seconds'], 60)
        self.assertIsNone(status['checked_at'])
        self.assertEqual([g['id'] for g in status['groups']], ['cdn', 'origin', 'relays'])
        self.assertEqual(len([n for g in status['groups'] for n in g['nodes']]), 5)
        for group in status['groups']:
            self.assertEqual(set(group), {'id', 'name', 'nodes'})
            for item in group['nodes']:
                self.assertEqual(set(item), {'id', 'name', 'basis', 'status', 'latency_ms', 'checked_at'})
                self.assertEqual(item['status'], 'unconfigured')
        self.monitor.admin_config()
        self.probe.assert_not_called()

    def test_empty_config_collects_no_targets(self):
        self.monitor.save_config(config())
        self.assertTrue(self.monitor.collect())
        self.probe.assert_not_called()
        self.assertEqual(node(self.monitor.public_status())['status'], 'unconfigured')

    def test_config_and_snapshot_are_private_atomic_files(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.monitor.collect()
        for name in ('network-monitor-config.json', 'network-monitor-status.json', 'network-monitor.lock'):
            self.assertEqual((self.path / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.path.glob('.*-*.json')))
        self.assertEqual(node(self.monitor.public_status())['status'], 'healthy')
        self.assertEqual(node(self.monitor.public_status())['latency_ms'], 123)
        self.assertNotIn('example.com', self.monitor.status_path.read_text())

    def test_expired_sample_becomes_unknown_but_keeps_checked_time(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.monitor.collect()
        checked = node(self.monitor.public_status())['checked_at']
        self.clock += 180
        self.assertEqual(node(self.monitor.public_status())['status'], 'healthy')
        self.clock += 1
        value = node(self.monitor.public_status())
        self.assertEqual(value['status'], 'unknown')
        self.assertEqual(value['checked_at'], checked)
        self.assertIsNone(value['latency_ms'])

    def test_target_change_invalidates_previous_health(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.monitor.collect()
        result = self.monitor.save_config(config(cdn='https://second.example.com/health'))
        self.assertEqual(node(result['status'])['status'], 'unknown')
        self.assertIsNone(node(result['status'])['checked_at'])
        self.assertIsNone(node(result['status'])['latency_ms'])
        self.probe.assert_called_once_with(TARGET)
        self.monitor.save_config(config())
        self.assertEqual(node(self.monitor.public_status())['status'], 'unconfigured')

    def test_address_error_header_body_and_injected_names_never_public(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.probe.return_value = {'status': 'healthy', 'latency_ms': 99,
                                  'url': TARGET, 'error': '203.0.113.10 secret.example.com',
                                  'body': 'private_body', 'headers': {'Location': TARGET}, 'name': TARGET}
        self.monitor.collect()
        snapshot = json.loads(self.monitor.status_path.read_text())
        snapshot.update(name='secret.example.com', url=TARGET, error='private_error')
        snapshot['nodes']['cdn'].update(name=TARGET, url=TARGET, error='private_error',
                                        headers={'Location': TARGET}, body='private_body')
        self.monitor.status_path.write_text(json.dumps(snapshot))
        public = json.dumps(self.monitor.public_status())
        for private in ('example.com', '203.0.113.10', TARGET, 'private_body', 'private_error', 'fingerprint', 'headers'):
            self.assertNotIn(private, public)
        self.assertEqual(node(self.monitor.public_status())['name'], '示例 CDN')
        self.assertEqual(self.monitor.admin_config()['nodes'][0]['url'], TARGET)

    def test_malicious_snapshot_field_types_are_discarded(self):
        self.monitor.save_config(config(cdn=TARGET))
        fingerprint = nm._fingerprint('http', TARGET)
        for invalid in ('secret.example.com', {'url': TARGET}, [TARGET], True, float('nan'), float('inf')):
            with self.subTest(invalid=invalid):
                self.monitor.status_path.write_text(json.dumps({'checked_at': invalid, 'nodes': {'cdn': {
                    'fingerprint': fingerprint, 'status': invalid, 'latency_ms': invalid, 'checked_at': invalid}}}))
                result = self.monitor.public_status()
                self.assertIsNone(result['checked_at'])
                self.assertEqual(node(result)['status'], 'unknown')
                self.assertNotIn('example.com', json.dumps(result))
        self.monitor.status_path.write_text(json.dumps({'nodes': [TARGET]}))
        self.assertEqual(node(self.monitor.public_status())['status'], 'unknown')

    def test_corrupt_oversized_or_invalid_config_snapshot_fails_closed(self):
        for invalid in ('{', 'x' * (nm.MAX_BODY_BYTES + 1), json.dumps({'nodes': [{'name': TARGET}]})):
            with self.subTest(size=len(invalid)):
                self.monitor.config_path.write_text(invalid)
                self.monitor.status_path.write_text(invalid)
                self.assertEqual(self.monitor.public_status()['groups'], [])
                self.assertNotIn('example.com', json.dumps(self.monitor.public_status()))
        self.probe.assert_not_called()

    def test_failed_probe_does_not_persist_exception_text(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.probe.side_effect = RuntimeError('secret.example.com 8.8.8.8 private headers')
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status())['status'], 'down')
        self.assertNotIn('secret', self.monitor.status_path.read_text())
        self.assertNotIn('8.8.8.8', self.monitor.status_path.read_text())

    def test_parallel_collect_is_skipped_without_probe(self):
        self.monitor.save_config(config(cdn=TARGET))
        with (self.path / 'network-monitor.lock').open('w') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(self.monitor.collect())
        self.probe.assert_not_called()

    def test_mid_collection_target_change_cannot_publish_old_result(self):
        self.monitor.save_config(config(cdn=TARGET))
        def changed(url):
            self.monitor.save_config(config(cdn='https://new.example.com/'))
            return {'status': 'healthy', 'latency_ms': 20}
        self.probe.side_effect = changed
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status())['status'], 'unknown')

    def test_legacy_private_config_is_read_without_rewrite_or_losing_target(self):
        original = json.dumps(config(cdn=TARGET), indent=2)
        self.monitor.config_path.write_text(original)
        admin = self.monitor.admin_config()
        self.assertEqual(admin['nodes'][0], {'id': 'cdn', 'name': '示例 CDN',
                                           'url': TARGET, 'method': 'http', 'target': TARGET})
        self.monitor.collect()
        self.probe.assert_called_once_with(TARGET)
        self.icmp_probe.assert_not_called()
        self.assertEqual(self.monitor.config_path.read_text(), original)

    def test_legacy_post_is_still_accepted_for_all_http_installation(self):
        self.monitor.save_config(config(cdn=TARGET))
        saved = json.loads(self.monitor.config_path.read_text())
        self.assertEqual(saved['nodes'][0], {'id': 'cdn', 'name': '示例 CDN', 'method': 'http', 'target': TARGET})
        self.assertEqual(self.monitor.admin_config()['nodes'][0]['url'], TARGET)

    def test_stale_legacy_page_cannot_erase_existing_icmp_target(self):
        self.monitor.save_config(modern_config(cdn=('icmp', '8.8.8.8')))
        before = self.monitor.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, '刷新'):
            self.monitor.save_config(config())
        self.assertEqual(self.monitor.config_path.read_bytes(), before)
        self.assertEqual(self.monitor.admin_config()['nodes'][0]['url'], '')
        self.assertEqual(self.monitor.admin_config()['nodes'][0]['target'], '8.8.8.8')

    def test_icmp_dispatch_and_method_switch_invalidates_old_sample(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.monitor.collect()
        self.monitor.save_config(modern_config(cdn=('icmp', '8.8.8.8')))
        self.assertEqual(node(self.monitor.public_status())['status'], 'unknown')
        self.assertIsNone(node(self.monitor.public_status())['checked_at'])
        self.monitor.collect()
        self.icmp_probe.assert_called_once_with('8.8.8.8')
        self.probe.assert_called_once_with(TARGET)
        self.assertEqual(node(self.monitor.public_status())['latency_ms'], 20)
        self.assertNotIn('8.8.8.8', json.dumps(self.monitor.public_status()))
        self.assertNotIn('8.8.8.8', self.monitor.status_path.read_text())
        self.assertNotEqual(nm._fingerprint('http', '8.8.8.8'), nm._fingerprint('icmp', '8.8.8.8'))
        self.monitor.save_config(modern_config(cdn=('http', TARGET)))
        self.assertEqual(node(self.monitor.public_status())['status'], 'unknown')

    def test_unavailable_ping_capability_stays_unknown_in_public_projection(self):
        self.monitor.save_config(modern_config(cdn=('icmp', 'example.com')))
        self.icmp_probe.return_value = {'status': 'unknown', 'latency_ms': None, 'error': 'example.com permission denied'}
        self.monitor.collect()
        status = self.monitor.public_status()
        self.assertEqual(node(status)['status'], 'unknown')
        self.assertIsNotNone(node(status)['checked_at'])
        self.assertNotIn('example.com', json.dumps(status))
        self.assertNotIn('permission', self.monitor.status_path.read_text())


class ValidationTests(unittest.TestCase):
    def test_accepts_uncredentialed_http_paths_and_public_addresses(self):
        for value in ('https://example.com/health', 'http://example.com:8080/status', 'https://8.8.8.8/',
                      'https://[2606:4700:4700::1111]/', ''):
            with self.subTest(value=value):
                self.assertEqual(nm.validate_url(value), value)

    def test_rejects_credentials_and_ssrf_spellings_without_echo(self):
        values = ('https://user:password@example.com/', 'https://user@example.com/',
                  'file:///etc/passwd', 'ftp://example.com/', 'https://example.com/?apikey=private',
                  'https://example.com/#secret', 'https://example.com/?', 'https://example.com/#',
                  'http://127.0.0.1/', 'http://127.1/', 'http://2130706433/', 'http://0x7f.0.0.1/',
                  'http://169.254.169.254/', 'http://10.0.0.1/', 'http://192.168.1.1/', 'http://100.64.0.1/',
                  'http://localhost/', 'http://x.localhost/', 'http://metadata.google.internal/', 'http://foo.local/',
                  'http://[::1]/', 'http://[fe80::1]/', 'http://[::ffff:8.8.8.8]/',
                  'http://[64:ff9b::808:808]/', 'http://224.0.0.1/', 'http://example.com:0/',
                  'http://example.com:65536/', 'http://example.com:abc/', 'http://bad_host.example.com/',
                  'http://good.example.com\\@127.0.0.1/', 'https://example.com/\r\nHeader: private')
        for value in values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '^' + nm.CONFIG_ERROR + '$'):
                    nm.validate_url(value)

    def test_requires_fixed_exact_five_node_schema(self):
        bodies = ({}, {'nodes': []}, {'nodes': [{'id': 'extra', 'url': TARGET}] * 5},
                  {'nodes': [{'id': 'cdn', 'url': TARGET}] * 5}, config() | {'name': TARGET},
                  {'nodes': [{'id': ident, 'url': '', 'name': TARGET} for ident, _, _ in nm.NODES]})
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    nm.validate_config(body)

    def test_icmp_accepts_only_public_bare_ip_or_qualified_host(self):
        for target in ('8.8.8.8', '2606:4700:4700::1111', 'example.com', ''):
            with self.subTest(target=target):
                self.assertEqual(nm.validate_icmp_target(target), target)
        self.assertEqual(nm.validate_icmp_target('EXAMPLE.COM.'), 'example.com')
        invalid = ('http://example.com', 'https://8.8.8.8/', 'example.com/health', 'example.com:443',
                   '8.8.8.8:80', '[2606:4700:4700::1111]', 'user@example.com', '-c 100', '--help',
                   'example.com;curl attacker.com', '$(touch /tmp/unsafe)', 'example.com|cat',
                   'example.com\nwhoami', 'example.com?api=key', 'example.com#secret',
                   'localhost', 'metadata.google.internal', '127.1', '2130706433', '0x7f.0.0.1',
                   '10.0.0.1', '127.0.0.1', '169.254.169.254', '224.0.0.1', '::1', 'fe80::1',
                   'fe80::1%eth0', '::ffff:8.8.8.8', '64:ff9b::808:808')
        for target in invalid:
            with self.subTest(target=target):
                with self.assertRaises(ValueError) as error:
                    nm.validate_icmp_target(target)
                self.assertEqual(str(error.exception), nm.CONFIG_ERROR)

    def test_method_schema_is_strict_and_does_not_allow_arbitrary_commands(self):
        for method in ('ping', 'tcp', 'shell', ['icmp'], None):
            with self.subTest(method=method):
                with self.assertRaises(ValueError):
                    nm.validate_config(modern_config(cdn=(method, '8.8.8.8')))
        value = modern_config(cdn=('icmp', '8.8.8.8'))
        value['nodes'][0]['url'] = TARGET
        with self.assertRaises(ValueError):
            nm.validate_config(value)


class ProbeTests(unittest.TestCase):
    def dns(self, *ips):
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '',
                 (ip, 443, 0, 0) if ':' in ip else (ip, 443)) for ip in ips]

    def test_dns_private_mixed_and_rebind_addresses_are_rejected_before_connect(self):
        for addresses in (('127.0.0.1',), ('8.8.8.8', '10.0.0.1'), ('169.254.169.254',), ('224.0.0.1',)):
            with self.subTest(addresses=addresses), patch.object(nm.socket, 'getaddrinfo', return_value=self.dns(*addresses)), \
                    patch.object(nm.socket, 'socket') as connect:
                self.assertEqual(nm.probe_url(TARGET)['status'], 'down')
                connect.assert_not_called()

    def fake_exchange(self, status=200, body=b'ok'):
        socket_mock = Mock()
        class Stream(io.BytesIO):
            def __init__(self, raw):
                super().__init__(raw)
                self.body_reads = []

            def read(self, size=-1):
                self.body_reads.append(size)
                return super().read(size)

            def readinto(self, buffer):
                self.body_reads.append(len(buffer))
                return super().readinto(buffer)

        status_line = str(status).encode() + b' Result'
        stream = Stream(b'HTTP/1.1 ' + status_line + b'\r\nContent-Length: ' + str(len(body)).encode()
                        + b'\r\nLocation: http://127.0.0.1/private\r\n\r\n' + body)
        socket_mock.makefile.return_value = stream
        return socket_mock, stream

    def test_connects_validated_ip_once_without_second_resolution_or_env_proxy(self):
        raw, stream = self.fake_exchange()
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:3128', 'HTTPS_PROXY': 'http://127.0.0.1:3128'}), \
                patch.object(nm.socket, 'getaddrinfo', side_effect=[self.dns('8.8.8.8'), self.dns('127.0.0.1')]) as dns, \
                patch.object(nm.socket, 'socket', return_value=raw):
            self.assertEqual(nm.probe_url('http://example.com/health')['status'], 'healthy')
        dns.assert_called_once()
        raw.connect.assert_called_once_with(('8.8.8.8', 80))
        request = raw.sendall.call_args[0][0]
        self.assertIn(b'GET /health HTTP/1.1', request)
        self.assertIn(b'Host: example.com', request)
        self.assertNotIn(b'Proxy-', request)

    def test_tls_uses_system_verified_context_and_original_hostname(self):
        raw, stream = self.fake_exchange()
        tls = Mock()
        tls.makefile.return_value = stream
        context = Mock()
        context.wrap_socket.return_value = tls
        with patch.object(nm.socket, 'getaddrinfo', return_value=self.dns('8.8.8.8')), \
                patch.object(nm.socket, 'socket', return_value=raw), \
                patch.object(nm.ssl, 'create_default_context', return_value=context) as default_context:
            self.assertEqual(nm.probe_url(TARGET)['status'], 'healthy')
        default_context.assert_called_once_with()
        context.wrap_socket.assert_called_once_with(raw, server_hostname='cdn.example.com', do_handshake_on_connect=False)
        tls.do_handshake.assert_called_once()

    def test_redirect_401_403_and_server_error_are_down_without_follow(self):
        for status in (301, 302, 307, 401, 403, 500):
            raw, stream = self.fake_exchange(status)
            with self.subTest(status=status), patch.object(nm.socket, 'getaddrinfo', return_value=self.dns('8.8.8.8')) as dns, \
                    patch.object(nm.socket, 'socket', return_value=raw):
                self.assertEqual(nm.probe_url('http://example.com/health')['status'], 'down')
                self.assertEqual(raw.sendall.call_count, 1)
                self.assertEqual(dns.call_count, 1)
                self.assertEqual(stream.body_reads, [])

    def test_body_read_is_capped_and_oversized_response_is_down(self):
        raw, stream = self.fake_exchange(body=b'x' * (nm.MAX_BODY_BYTES + 1))
        with patch.object(nm.socket, 'getaddrinfo', return_value=self.dns('8.8.8.8')), \
                patch.object(nm.socket, 'socket', return_value=raw):
            self.assertEqual(nm.probe_url('http://example.com/health')['status'], 'down')
        self.assertEqual(stream.body_reads, [nm.MAX_BODY_BYTES + 1])

    def test_exceptions_never_escape_into_result_and_slow_success_is_degraded(self):
        with patch.object(nm, '_http_probe', side_effect=OSError('secret.example.com private 10.0.0.1')):
            self.assertEqual(nm.probe_url(TARGET), {'status': 'down', 'latency_ms': None})
        with patch.object(nm, '_http_probe', return_value=True), patch.object(nm.time, 'monotonic', side_effect=[1, 2.7]):
            self.assertEqual(nm.probe_url(TARGET), {'status': 'degraded', 'latency_ms': 1700})

    def test_dns_timeout_is_bounded_and_never_connects(self):
        event = Mock()
        event.wait.return_value = False
        with patch.object(nm.threading, 'Event', return_value=event), patch.object(nm.threading, 'Thread'), \
                patch.object(nm.socket, 'socket') as connect:
            self.assertEqual(nm.probe_url(TARGET)['status'], 'down')
            self.assertLessEqual(event.wait.call_args[0][0], 2)
            connect.assert_not_called()

    def test_expired_total_budget_cannot_start_connection(self):
        with patch.object(nm.time, 'monotonic', side_effect=[100, 106]), \
                patch.object(nm, '_resolve_public', return_value='8.8.8.8') as resolver, \
                patch.object(nm.socket, 'socket') as connect:
            self.assertEqual(nm.probe_url(TARGET)['status'], 'down')
            resolver.assert_called_once_with('cdn.example.com', 443, 105)
            connect.assert_not_called()

    def test_sampling_uses_one_total_deadline_and_abort_closes_active_socket(self):
        raw, stream = self.fake_exchange()
        timer = Mock()
        with patch.object(nm.socket, 'getaddrinfo', return_value=self.dns('8.8.8.8')), \
                patch.object(nm.socket, 'socket', return_value=raw), \
                patch.object(nm.threading, 'Timer', return_value=timer) as timer_factory:
            self.assertEqual(nm.probe_url('http://example.com/health')['status'], 'healthy')
        duration, abort = timer_factory.call_args[0]
        self.assertGreater(duration, 0)
        self.assertLessEqual(duration, nm.PROBE_SECONDS)
        timer.start.assert_called_once()
        timer.cancel.assert_called_once()
        abort()
        raw.shutdown.assert_called_once_with(socket.SHUT_RDWR)
        self.assertTrue(raw.close.called)


class IcmpTests(unittest.TestCase):
    def setUp(self):
        self.binary = patch.object(nm, '_ping_binary', return_value='/usr/bin/ping')
        self.binary.start()
        self.addCleanup(self.binary.stop)

    def dns(self, *ips):
        return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '',
                 (ip, 0, 0, 0) if ':' in ip else (ip, 0)) for ip in ips]

    def result(self, returncode=0, output=b'64 bytes from 8.8.8.8: icmp_seq=1 ttl=64 time=12.7 ms\n'):
        return subprocess.CompletedProcess([], returncode, output)

    def test_runs_single_numeric_ping_with_no_shell_and_no_second_dns(self):
        with patch.object(nm.socket, 'getaddrinfo', side_effect=[self.dns('8.8.8.8'), self.dns('127.0.0.1')]) as dns, \
                patch.object(nm.subprocess, 'run', return_value=self.result()) as run:
            self.assertEqual(nm.probe_icmp('example.com'), {'status': 'healthy', 'latency_ms': 13})
        dns.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/ping', '-n', '-c', '1', '-W', '3', '--', '8.8.8.8'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
        self.assertLessEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['env'], {'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
        self.assertNotIn('example.com', str(run.call_args))

    def test_ipv6_pin_is_supported_and_slow_rtt_is_degraded(self):
        with patch.object(nm.socket, 'getaddrinfo', return_value=self.dns('2606:4700:4700::1111')), \
                patch.object(nm.subprocess, 'run', return_value=self.result(output=b'icmp_seq=1 time=1700 ms')) as run:
            self.assertEqual(nm.probe_icmp('example.com'), {'status': 'degraded', 'latency_ms': 1700})
        self.assertEqual(run.call_args.args[0][-1], '2606:4700:4700::1111')

    def test_private_mixed_or_link_local_dns_never_runs_ping(self):
        for addresses in (('127.0.0.1',), ('8.8.8.8', '192.168.1.1'), ('169.254.169.254',), ('fe80::1',)):
            with self.subTest(addresses=addresses), patch.object(nm.socket, 'getaddrinfo', return_value=self.dns(*addresses)), \
                    patch.object(nm.subprocess, 'run') as run:
                self.assertEqual(nm.probe_icmp('example.com')['status'], 'down')
                run.assert_not_called()

    def test_invalid_target_never_resolves_or_executes(self):
        with patch.object(nm.socket, 'getaddrinfo') as dns, patch.object(nm.subprocess, 'run') as run:
            self.assertEqual(nm.probe_icmp('example.com; touch /tmp/unsafe')['status'], 'down')
        dns.assert_not_called()
        run.assert_not_called()

    def test_missing_ping_is_unknown_and_does_not_resolve(self):
        with patch.object(nm, '_ping_binary', return_value=None), patch.object(nm.socket, 'getaddrinfo') as dns:
            self.assertEqual(nm.probe_icmp('example.com'), {'status': 'unknown', 'latency_ms': None})
        dns.assert_not_called()

    def test_permission_and_local_command_failures_are_unknown_not_outages(self):
        with patch.object(nm, '_resolve_public', return_value='8.8.8.8'):
            for value in (self.result(2, b'8.8.8.8 Operation not permitted'), self.result(-9),
                          PermissionError('example.com private'), FileNotFoundError('private path')):
                with self.subTest(value=type(value).__name__), patch.object(nm.subprocess, 'run') as run:
                    if isinstance(value, Exception):
                        run.side_effect = value
                    else:
                        run.return_value = value
                    self.assertEqual(nm.probe_icmp('example.com'), {'status': 'unknown', 'latency_ms': None})

    def test_timeout_and_no_reply_are_down_and_never_expose_output(self):
        with patch.object(nm, '_resolve_public', return_value='8.8.8.8'):
            for value in (self.result(1, b'private.example.com 8.8.8.8 timeout'),
                          subprocess.TimeoutExpired(['ping', 'private.example.com'], 3, output=b'private 8.8.8.8')):
                with self.subTest(value=type(value).__name__), patch.object(nm.subprocess, 'run') as run:
                    if isinstance(value, Exception):
                        run.side_effect = value
                    else:
                        run.return_value = value
                    self.assertEqual(nm.probe_icmp('example.com'), {'status': 'down', 'latency_ms': None})

    def test_total_budget_includes_dns_and_prevents_late_command(self):
        with patch.object(nm.time, 'monotonic', side_effect=[100, 106]), \
                patch.object(nm, '_resolve_public', return_value='8.8.8.8') as resolver, \
                patch.object(nm.subprocess, 'run') as run:
            self.assertEqual(nm.probe_icmp('example.com')['status'], 'down')
        resolver.assert_called_once_with('example.com', 0, 105)
        run.assert_not_called()

    def test_ping_output_only_contributes_whitelisted_numeric_latency(self):
        with patch.object(nm, '_resolve_public', return_value='8.8.8.8'):
            for output, latency in ((b'private.example.com 8.8.8.8 time=42.2 ms error=secret', 42),
                                    (b'time<1 ms', 1), (b'private.example.com time=NaN ms', None)):
                with self.subTest(output=output), patch.object(nm.subprocess, 'run', return_value=self.result(output=output)):
                    self.assertEqual(nm.probe_icmp('example.com'), {'status': 'healthy', 'latency_ms': latency})
            with patch.object(nm.subprocess, 'run', return_value=self.result(output=b'x' * (nm.MAX_BODY_BYTES + 1))):
                self.assertEqual(nm.probe_icmp('example.com')['status'], 'unknown')

    def test_service_grants_only_required_raw_socket_capability(self):
        service = (Path(__file__).parent.parent / 'deploy/mediadock-network-monitor.service').read_text()
        self.assertIn('NoNewPrivileges=true', service)
        self.assertIn('AmbientCapabilities=CAP_NET_RAW', service)
        self.assertIn('CapabilityBoundingSet=CAP_NET_RAW', service)
        self.assertNotIn('CAP_NET_ADMIN', service)



SOURCE = 'https://monitor.example.com'


def named_config(**targets):
    return {'nodes': [{'id': ident, 'name': targets.get(ident, (name, 'http', ''))[0],
                       'method': targets.get(ident, (name, 'http', ''))[1],
                       'target': targets.get(ident, (name, 'http', ''))[2]} for ident, name, _ in nm.NODES]}


def inventory(source=SOURCE, *names):
    return [{'id': nm._probe_id(source, name), 'name': name, 'online': True} for name in (names or ('Private Origin',))]


class ProbeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.clock = NOW
        self.fetch = Mock(return_value=inventory())
        self.http = Mock(return_value={'status': 'healthy', 'latency_ms': 11})
        self.monitor = nm.NetworkMonitor(self.path, now=lambda: self.clock, probe=self.http, probe_fetch=self.fetch)
        self.remote_id = inventory()[0]['id']

    def tearDown(self):
        self.temporary.cleanup()

    def connect(self):
        self.monitor.save_probe_config({'url': SOURCE})
        self.monitor.refresh_probe()
        self.monitor.save_config(named_config(origin=('公开源站', 'mmwx', self.remote_id)))

    def test_source_is_unconfigured_and_admin_public_reads_never_fetch(self):
        self.assertEqual(self.monitor.probe_config(), {'provider': 'mmwx', 'url': '', 'configured': False,
                                                       'checked_at': None, 'status': 'unconfigured', 'nodes': []})
        self.monitor.public_status()
        self.monitor.admin_config()
        self.fetch.assert_not_called()

    def test_source_save_never_probes_selects_node_or_changes_targets(self):
        self.monitor.save_config(config(cdn=TARGET))
        before = self.monitor.config_path.read_bytes()
        self.assertEqual(self.monitor.save_probe_config({'url': SOURCE})['status'], 'unavailable')
        self.assertEqual(self.monitor.config_path.read_bytes(), before)
        self.monitor.collect()
        self.fetch.assert_not_called()
        self.http.assert_called_once_with(TARGET)

    def test_explicit_inventory_refresh_retains_only_allowlist_and_private_modes(self):
        self.fetch.return_value[0].update(ip='8.8.8.8', token='secret', url='https://private.example.com')
        self.monitor.save_probe_config({'url': SOURCE})
        value = self.monitor.refresh_probe()
        self.assertEqual(value['status'], 'ready')
        self.assertEqual(set(value['nodes'][0]), {'id', 'name', 'online', 'latency_ms'})
        for path in (self.monitor.probe_config_path, self.monitor.probe_cache_path):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        cache = self.monitor.probe_cache_path.read_text()
        for forbidden in ('8.8.8.8', 'secret', 'private.example.com', SOURCE):
            self.assertNotIn(forbidden, cache)
        self.assertNotIn(SOURCE, json.dumps(self.monitor.public_status()))

    def test_alias_is_public_and_previous_alias_preserved_by_old_admin_shape(self):
        self.monitor.save_config(named_config(cdn=('香港优选', 'http', TARGET)))
        self.assertEqual(node(self.monitor.public_status())['name'], '香港优选')
        self.monitor.save_config(config(cdn=TARGET))
        self.assertEqual(node(self.monitor.public_status())['name'], '香港优选')
        self.monitor.save_config(modern_config(cdn=('icmp', '8.8.8.8')))
        self.assertEqual(node(self.monitor.public_status())['name'], '香港优选')

    def test_new_selection_requires_fresh_inventory_and_does_not_overwrite_config(self):
        self.monitor.save_config(config(cdn=TARGET))
        before = self.monitor.config_path.read_bytes()
        with self.assertRaises(ValueError):
            self.monitor.save_config(named_config(origin=('源站', 'mmwx', self.remote_id)))
        self.assertEqual(self.monitor.config_path.read_bytes(), before)
        self.monitor.save_probe_config({'url': SOURCE})
        self.monitor.refresh_probe()
        self.clock += 181
        with self.assertRaises(ValueError):
            self.monitor.save_config(named_config(origin=('源站', 'mmwx', self.remote_id)))
        self.assertEqual(self.monitor.config_path.read_bytes(), before)

    def test_missing_offline_existing_selection_is_preserved_on_save(self):
        self.connect()
        self.fetch.return_value = []
        self.monitor.refresh_probe()
        self.monitor.save_config(named_config(origin=('新的公开名称', 'mmwx', self.remote_id)))
        self.assertEqual(self.monitor.admin_config()['nodes'][1]['target'], self.remote_id)
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')

    def test_legacy_pages_cannot_erase_configured_probe_selection(self):
        self.connect()
        before = self.monitor.config_path.read_bytes()
        for body in (config(), modern_config()):
            with self.assertRaisesRegex(ValueError, '刷新'):
                self.monitor.save_config(body)
        self.assertEqual(self.monitor.config_path.read_bytes(), before)

    def test_legacy_save_cannot_reset_an_unselected_probe_method(self):
        self.monitor.save_config(named_config(origin=('源站', 'mmwx', '')))
        before = self.monitor.config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, '刷新'):
            self.monitor.save_config(modern_config())
        self.assertEqual(self.monitor.config_path.read_bytes(), before)

    def test_oversized_or_malformed_private_cache_fails_closed(self):
        self.connect()
        self.monitor.collect()
        cache = json.loads(self.monitor.probe_cache_path.read_text())
        for invalid in ('x' * (nm.MAX_PROBE_BODY_BYTES + 1), json.dumps(cache | {'nodes': [dict(cache['nodes'][0], token='private')]})):
            self.monitor.probe_cache_path.write_text(invalid)
            self.assertEqual(self.monitor.probe_config()['status'], 'unavailable')
            self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
            self.assertNotIn('private', json.dumps(self.monitor.public_status()))

    def test_collect_fetches_once_for_all_selected_nodes_and_never_exposes_provider_data(self):
        self.connect()
        self.monitor.save_config(named_config(cdn=('主线路', 'mmwx', self.remote_id), origin=('源站', 'mmwx', self.remote_id)))
        self.fetch.reset_mock()
        self.monitor.collect()
        self.fetch.assert_called_once_with(SOURCE)
        self.http.assert_not_called()
        public = self.monitor.public_status()
        for ident in ('cdn', 'origin'):
            self.assertEqual(node(public, ident)['status'], 'healthy')
            self.assertEqual(node(public, ident)['basis'], 'mmwx')
            self.assertIsNone(node(public, ident)['latency_ms'])
        for forbidden in (SOURCE, self.remote_id, 'Private Origin', 'online', 'servers', 'fingerprint'):
            self.assertNotIn(forbidden, json.dumps(public))
        self.assertNotIn('Private Origin', self.monitor.status_path.read_text())

    def test_explicit_offline_means_down_but_outage_disabled_missing_and_rename_are_unknown(self):
        self.connect()
        self.fetch.return_value[0]['online'] = False
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'down')
        for case in (RuntimeError('secret.example.com private response'), [], inventory(SOURCE, 'Renamed Origin')):
            if isinstance(case, Exception):
                self.fetch.side_effect = case
            else:
                self.fetch.side_effect = None
                self.fetch.return_value = case
            self.monitor.collect()
            self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
            for forbidden in ('secret.example.com', 'private response', 'Renamed Origin'):
                self.assertNotIn(forbidden, self.monitor.status_path.read_text())

    def test_failure_and_stale_inventory_immediately_invalidate_previous_green_sample(self):
        self.connect()
        self.monitor.collect()
        self.fetch.side_effect = OSError('private source exception')
        self.monitor.refresh_probe()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.fetch.side_effect = None
        self.monitor.collect()
        self.clock += 181
        self.assertEqual(self.monitor.probe_config()['status'], 'unavailable')
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')

    def test_source_changes_never_rebind_same_named_node_or_reuse_previous_green(self):
        self.connect()
        self.monitor.collect()
        original = self.monitor.config_path.read_bytes()
        alternate = 'https://another.example.com'
        self.fetch.return_value = inventory(alternate)
        self.monitor.save_probe_config({'url': alternate})
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.assertNotEqual(self.monitor.probe_config()['nodes'][0]['id'], self.remote_id)
        self.assertEqual(self.monitor.config_path.read_bytes(), original)
        self.monitor.save_probe_config({'url': ''})
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.monitor.save_probe_config({'url': SOURCE})
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')

    def test_alias_change_preserves_valid_sample_without_fetch(self):
        self.connect()
        self.monitor.collect()
        self.fetch.reset_mock()
        self.monitor.save_config(named_config(origin=('独立源站', 'mmwx', self.remote_id)))
        result = node(self.monitor.public_status(), 'origin')
        self.assertEqual(result['name'], '独立源站')
        self.assertEqual(result['status'], 'healthy')
        self.fetch.assert_not_called()

    def test_mid_request_source_change_cannot_publish_old_source_result(self):
        self.connect()
        def switch(url):
            self.monitor.save_probe_config({'url': 'https://new.example.com'})
            return inventory()
        self.fetch.side_effect = switch
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.assertEqual(self.monitor.probe_config()['status'], 'unavailable')

    def test_probe_average_above_server_timeout_limit_is_public_without_private_fields(self):
        payload = {'enabled': True, 'servers': [{'name': 'Private Origin', 'online': True,
            'ping': [{'key': '10.0.0.1', 'label': 'https://private.example.com/', 'current_ms': 12000},
                     {'key': 'private', 'current_ms': 12001}], 'ip': '8.8.8.8'}]}
        self.fetch.return_value = nm._probe_nodes(payload, SOURCE)
        self.connect()
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['latency_ms'], 12001)
        stored = json.loads(self.monitor.status_path.read_text())['nodes']['origin']
        self.assertEqual(stored['latency_ms'], 12001)
        for content in (self.monitor.probe_cache_path.read_text(), self.monitor.status_path.read_text(),
                        json.dumps(self.monitor.public_status())):
            for forbidden in ('10.0.0.1', 'private.example.com', '8.8.8.8', 'current_ms', 'label', 'ping'):
                self.assertNotIn(forbidden, content)
        self.assertNotIn('Private Origin', json.dumps(self.monitor.public_status()))

    def test_probe_latency_invalid_offline_failed_and_stale_are_never_displayed(self):
        self.fetch.return_value[0]['latency_ms'] = 60000
        self.connect()
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['latency_ms'], 60000)
        for bad in (-1, 60001, 2.5, True, float('nan'), float('inf'), '35', {}, None):
            with self.subTest(bad=bad):
                self.fetch.return_value[0]['latency_ms'] = bad
                self.monitor.collect()
                self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'healthy')
                self.assertIsNone(node(self.monitor.public_status(), 'origin')['latency_ms'])
        self.fetch.return_value[0].update(latency_ms=15, online=False)
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'down')
        self.assertIsNone(node(self.monitor.public_status(), 'origin')['latency_ms'])
        self.fetch.return_value[0].update(latency_ms=15, online=True)
        self.monitor.collect()
        self.fetch.side_effect = OSError('private endpoint failed')
        self.monitor.refresh_probe()
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.assertIsNone(node(self.monitor.public_status(), 'origin')['latency_ms'])
        self.fetch.side_effect = None
        self.monitor.collect()
        self.clock += nm.STALE_SECONDS + 1
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'unknown')
        self.assertIsNone(node(self.monitor.public_status(), 'origin')['latency_ms'])

    def test_old_probe_cache_remains_usable_but_cannot_invent_latency(self):
        self.fetch.return_value[0]['latency_ms'] = 38
        self.connect()
        self.monitor.collect()
        cached = json.loads(self.monitor.probe_cache_path.read_text())
        del cached['nodes'][0]['latency_ms']
        self.monitor.probe_cache_path.write_text(json.dumps(cached))
        self.assertEqual(self.monitor.probe_config()['status'], 'ready')
        value = node(self.monitor.public_status(), 'origin')
        self.assertEqual(value['status'], 'healthy')
        self.assertIsNone(value['latency_ms'])
        for bad in (True, 60001, float('nan')):
            cached['nodes'][0]['latency_ms'] = bad
            self.monitor.probe_cache_path.write_text(json.dumps(cached))
            self.assertEqual(self.monitor.probe_config()['status'], 'ready')
            self.assertIsNone(node(self.monitor.public_status(), 'origin')['latency_ms'])


class ProbeSourceValidationTests(unittest.TestCase):
    def test_probe_latency_mean_matches_overview_half_up_and_rejects_invalid_values(self):
        cases = [([0], 0), ([1, 2], 2), ([12.5], 13), ([60000], 60000),
                 ([20, 40, -1, True, False, float('nan'), float('inf'), '50', 60001], 30),
                 ([-1, -2, True, float('-inf'), None], None), ([], None)]
        for values, expected in cases:
            with self.subTest(values=values):
                series = [{'current_ms': value, 'label': 'private.example.com'} for value in values]
                series.extend([None, 'invalid', {}])
                body = {'enabled': True, 'servers': [{'name': 'Private', 'online': True, 'ping': series}]}
                row = nm._probe_nodes(body, SOURCE)[0]
                self.assertEqual(row['latency_ms'], expected)
                self.assertEqual(set(row), {'id', 'name', 'online', 'latency_ms'})
                body['servers'][0]['online'] = False
                self.assertIsNone(nm._probe_nodes(body, SOURCE)[0]['latency_ms'])
        for invalid in (None, '33', {}, 12):
            self.assertIsNone(nm._average_probe_latency(invalid))

    def test_public_alias_rejects_addresses_and_controls_without_echo(self):
        for value in ('源站', 'HK 01', '中转线路 A', '香港 · 一线', ' CDN '):
            self.assertEqual(nm.validate_name(value), value.strip())
        for value in ('', 'a' * 41, 'https://secret.example.com/', 'secret.example.com', '源站 8.8.8.8',
                      '2001:4860:4860::8888', 'Ａ.ＣＯＭ', '例子.中国', 'x\nsecret', 'test\u202eabc', '<script>'):
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                nm.validate_name(value)
            self.assertEqual(str(error.exception), nm.CONFIG_ERROR)

    def test_probe_source_accepts_only_https_root_and_uses_fixed_path(self):
        self.assertEqual(nm.validate_probe_url(SOURCE + '/'), SOURCE)
        for value in ('http://monitor.example.com/', 'https://monitor.example.com/api/probe',
                      'https://user:secret@example.com', 'https://example.com/?token=secret',
                      'https://127.0.0.1', 'https://example.com/#secret'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                nm.validate_probe_url(value)
        with patch.object(nm, '_http_fetch', return_value=b'{"enabled": true,"servers": []}') as fetch:
            self.assertEqual(nm.fetch_probe_nodes(SOURCE), [])
        args, kwargs = fetch.call_args
        self.assertEqual(args[0], SOURCE + '/api/probe')
        self.assertEqual(kwargs['max_bytes'], 1024 * 1024)
        self.assertEqual(kwargs['accept'], 'application/json')
        self.assertEqual(kwargs['extra_headers']['Cache-Control'], 'no-cache')

    def test_probe_parser_drops_duplicate_invalid_and_extra_data_never_uses_array_position(self):
        body = {'enabled': True, 'servers': [
            {'name': 'Duplicated', 'online': True}, {'name': 'Duplicated', 'online': False},
            {'name': 'bad bool', 'online': 'true'}, {'name': 'Good', 'online': True, 'ip': '8.8.8.8'},
            {'name': 'Other', 'online': False, 'expires_at': 'tomorrow'},
        ]}
        expected = [{'id': nm._probe_id(SOURCE, 'Good'), 'name': 'Good', 'online': True, 'latency_ms': None},
                    {'id': nm._probe_id(SOURCE, 'Other'), 'name': 'Other', 'online': False, 'latency_ms': None}]
        self.assertEqual(nm._probe_nodes(body, SOURCE), expected)
        body['servers'].reverse()
        self.assertEqual(sorted(nm._probe_nodes(body, SOURCE), key=lambda r:r['id']), sorted(expected, key=lambda r:r['id']))
        for invalid in ({'enabled': False, 'servers': []}, {'enabled': 1, 'servers': []}, {'enabled': True, 'servers': {}}):
            with self.assertRaises(ValueError):
                nm._probe_nodes(invalid, SOURCE)

    def test_secure_probe_http_get_pins_numeric_ip_and_checks_tls(self):
        helper = ProbeTests()
        raw, stream = helper.fake_exchange(body=b'{"enabled":true,"servers":[]}')
        tls, context = Mock(), Mock()
        tls.makefile.return_value = stream
        context.wrap_socket.return_value = tls
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:3128'}), \
                patch.object(nm.socket, 'getaddrinfo', side_effect=[helper.dns('8.8.8.8'), helper.dns('127.0.0.1')]) as dns, \
                patch.object(nm.socket, 'socket', return_value=raw), \
                patch.object(nm.ssl, 'create_default_context', return_value=context):
            self.assertEqual(nm.fetch_probe_nodes(SOURCE), [])
        dns.assert_called_once()
        raw.connect.assert_called_once_with(('8.8.8.8', 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname='monitor.example.com', do_handshake_on_connect=False)
        request = tls.sendall.call_args.args[0]
        self.assertIn(b'GET /api/probe HTTP/1.1', request)
        self.assertIn(b'Cache-Control: no-cache', request)
        self.assertNotIn(b'Proxy-', request)
        self.assertTrue(stream.body_reads)
        self.assertLessEqual(max(stream.body_reads), 1024 * 1024 + 1)

    def test_probe_does_not_follow_redirect_and_rejects_oversized_invalid_json(self):
        helper = ProbeTests()
        for status, body in ((302, b''), (200, b'x' * (1024 * 1024 + 1)), (200, b'not json')):
            raw, stream = helper.fake_exchange(status=status, body=body)
            with self.subTest(status=status, length=len(body)), \
                    patch.object(nm.socket, 'getaddrinfo', return_value=helper.dns('8.8.8.8')) as dns, \
                    patch.object(nm.socket, 'socket', return_value=raw), \
                    patch.object(nm.ssl, 'create_default_context') as tls:
                tls.return_value.wrap_socket.return_value = raw
                with self.assertRaises(ValueError):
                    nm.fetch_probe_nodes(SOURCE)
                dns.assert_called_once()
                self.assertEqual(raw.sendall.call_count, 1)


class BrowserMonitoringTests(unittest.TestCase):
    setUp = ProjectionTests.setUp
    tearDown = ProjectionTests.tearDown

    def draft(self):
        current = self.monitor.admin_config()
        return {key: copy.deepcopy(current[key]) for key in ('version', 'revision', 'groups')}

    def test_only_explicit_browser_url_is_public_and_no_server_probe_runs_for_it(self):
        draft = self.draft()
        # A CDN group does not silently opt private HTTP targets into publication.
        draft['groups'][0]['nodes'][0].update(method='http', target='https://private.example.com/health')
        draft['groups'][1]['nodes'][0].update(method='browser', target=TARGET)
        self.monitor.save_config(draft)
        public = self.monitor.public_status()
        self.assertNotIn('test_url', node(public, 'cdn'))
        self.assertNotIn('private.example.com', json.dumps(public))
        value = node(public, 'origin')
        self.assertEqual((value['basis'], value['status'], value['test_url']), ('browser', 'client_pending', TARGET))
        self.assertIsNone(value['latency_ms'])
        self.assertIsNone(value['checked_at'])
        self.monitor.collect()
        self.probe.assert_called_once_with('https://private.example.com/health')
        self.icmp_probe.assert_not_called()
        self.assertNotIn('origin', json.loads(self.monitor.status_path.read_text())['nodes'])
        self.assertEqual(node(self.monitor.public_status(), 'origin')['status'], 'client_pending')

    def test_empty_browser_target_is_unconfigured_and_never_has_test_url(self):
        draft = self.draft()
        draft['groups'][0]['nodes'][0].update(method='browser', target='')
        self.monitor.save_config(draft)
        self.monitor.collect()
        value = node(self.monitor.public_status())
        self.assertEqual(value['status'], 'unconfigured')
        self.assertNotIn('test_url', value)
        self.assertEqual(self.monitor.browser_test_origins(), [])
        self.probe.assert_not_called()
        self.icmp_probe.assert_not_called()

    def test_browser_target_requires_credential_free_public_https_and_uses_existing_revision(self):
        before = self.draft()
        for invalid in ('http://cdn.example.com/test', 'https://user:secret@cdn.example.com/',
                        'https://cdn.example.com/?token=secret', 'https://cdn.example.com/#secret',
                        'https://127.0.0.1/', 'https://[::1]/', 'https://private.local/',
                        'https://cdn.example.com/\r\nx', 'javascript:alert(1)'):
            draft = self.draft()
            draft['groups'][0]['nodes'][0].update(method='browser', target=invalid)
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.monitor.save_config(draft)
            self.assertEqual(self.draft(), before)
        valid = self.draft()
        valid['groups'][0]['nodes'][0].update(method='browser', target=TARGET)
        self.monitor.save_config(valid)
        with self.assertRaises(nm.MonitorConflict):
            self.monitor.save_config(before)

    def test_browser_to_private_method_immediately_stops_publishing_url(self):
        draft = self.draft()
        draft['groups'][0]['nodes'][0].update(method='browser', target=TARGET)
        self.monitor.save_config(draft)
        self.assertEqual(node(self.monitor.public_status())['test_url'], TARGET)
        draft = self.draft()
        draft['groups'][0]['nodes'][0]['method'] = 'http'
        self.monitor.save_config(draft)
        self.assertNotIn('test_url', node(self.monitor.public_status()))
        self.assertNotIn(TARGET, json.dumps(self.monitor.public_status()))
        self.assertEqual(self.monitor.browser_test_origins(), [])

    def test_csp_origins_include_only_unique_browser_origins_and_never_paths(self):
        draft = self.draft()
        rows = [row for group in draft['groups'] for row in group['nodes']]
        rows[0].update(method='browser', target='https://cdn.example.com/a')
        rows[1].update(method='browser', target='https://cdn.example.com/b')
        rows[2].update(method='browser', target='https://[2606:4700:4700::1111]:8443/ping')
        rows[3].update(method='http', target='https://private.example.com/health')
        rows[4].update(method='icmp', target='8.8.8.8')
        self.monitor.save_probe_config({'url': SOURCE})
        self.monitor.save_config(draft)
        with patch.object(nm.socket, 'getaddrinfo', side_effect=AssertionError('must not resolve')):
            self.assertEqual(self.monitor.browser_test_origins(),
                             ['https://[2606:4700:4700::1111]:8443', 'https://cdn.example.com'])
        self.probe.assert_not_called()
        self.icmp_probe.assert_not_called()


class DynamicConfigurationTests(unittest.TestCase):
    setUp = ProjectionTests.setUp
    tearDown = ProjectionTests.tearDown

    def draft(self):
        value = self.monitor.admin_config()
        return {key: copy.deepcopy(value[key]) for key in ('version', 'revision', 'groups')}

    def dynamic_node(self, index, target=TARGET, method='http'):
        return {'id': 'n-' + format(index, '032x'), 'name': '监测节点 ' + str(index),
                'method': method, 'target': target}

    def dynamic_group(self, index, nodes=None):
        return {'id': 'g-' + format(index, '032x'), 'name': '监测分组 ' + str(index), 'nodes': nodes or []}

    def test_legacy_migration_preserves_alias_methods_targets_and_does_not_write(self):
        original = named_config(cdn=('边缘线路', 'http', TARGET), origin=('主源站', 'mmwx', 'a' * 64),
                                **{'relay-a': ('低延迟节点', 'icmp', '8.8.8.8')})
        raw = json.dumps(original, ensure_ascii=False, indent=2)
        self.monitor.config_path.write_text(raw)
        result = self.monitor.admin_config()
        self.assertEqual(result['version'], 2)
        self.assertRegex(result['revision'], r'^[0-9a-f]{64}$')
        rows = {row['id']: row for group in result['groups'] for row in group['nodes']}
        for expected in original['nodes']:
            self.assertEqual(rows[expected['id']], expected)
        self.monitor.public_status()
        self.assertEqual(self.monitor.config_path.read_text(), raw)
        self.probe.assert_not_called()

    def test_rename_move_and_reorder_preserve_healthy_sample_and_target(self):
        self.monitor.save_config(config(cdn=TARGET))
        self.monitor.collect()
        before = node(self.monitor.public_status())
        draft = self.draft()
        moved = draft['groups'][0]['nodes'].pop()
        moved['name'] = '香港优选线路'
        draft['groups'][2]['name'] = '我的线路组'
        draft['groups'][2]['nodes'].insert(1, moved)
        draft['groups'].reverse()
        result = self.monitor.save_config(draft)
        self.assertEqual([group['id'] for group in result['groups']], ['relays', 'origin', 'cdn'])
        self.assertEqual(result['groups'][0]['name'], '我的线路组')
        self.assertEqual(result['groups'][0]['nodes'][1]['id'], 'cdn')
        value = node(result['status'])
        self.assertEqual(value['name'], '香港优选线路')
        self.assertEqual((value['status'], value['latency_ms'], value['checked_at']),
                         (before['status'], before['latency_ms'], before['checked_at']))
        self.probe.assert_called_once_with(TARGET)
        self.assertEqual(set(json.loads(self.monitor.config_path.read_text())), {'version', 'groups'})

    def test_add_and_delete_are_immediate_and_removed_targets_are_not_sampled_again(self):
        self.monitor.save_config(config(cdn=TARGET))
        draft = self.draft()
        new = self.dynamic_node(1, '8.8.8.8', 'icmp')
        draft['groups'].append(self.dynamic_group(1, [new]))
        self.monitor.save_config(draft)
        self.monitor.collect()
        self.assertEqual(node(self.monitor.public_status(), new['id'])['status'], 'healthy')
        draft = self.draft()
        draft['groups'] = [draft['groups'][-1]]
        result = self.monitor.save_config(draft)
        self.assertEqual([row['id'] for row in result['nodes']], [new['id']])
        self.assertNotIn('示例 CDN', json.dumps(result['status'], ensure_ascii=False))
        self.probe.reset_mock()
        self.icmp_probe.reset_mock()
        self.monitor.collect()
        self.probe.assert_not_called()
        self.icmp_probe.assert_called_once_with('8.8.8.8')
        self.assertNotIn('cdn', json.loads(self.monitor.status_path.read_text())['nodes'])

    def test_delete_all_and_empty_groups_survive_reload_without_default_resurrection(self):
        draft = self.draft()
        draft['groups'] = [self.dynamic_group(1)]
        self.monitor.save_config(draft)
        reloaded = nm.NetworkMonitor(self.path, now=lambda: NOW)
        self.assertEqual(reloaded.admin_config()['groups'], draft['groups'])
        draft = self.draft()
        draft['groups'] = []
        self.monitor.save_config(draft)
        self.monitor.collect()
        self.assertEqual(self.monitor.public_status()['groups'], [])
        self.assertEqual(reloaded.admin_config()['nodes'], [])
        self.probe.assert_not_called()
        self.icmp_probe.assert_not_called()
        self.assertEqual(json.loads(self.monitor.status_path.read_text())['nodes'], {})

    def test_stale_revision_and_legacy_editor_cannot_overwrite_v2(self):
        first, stale = self.draft(), self.draft()
        first['groups'][0]['name'] = '边缘监测'
        self.monitor.save_config(first)
        before = self.monitor.config_path.read_bytes()
        for body in (stale, config(), modern_config(), named_config()):
            with self.subTest(body=list(body)), self.assertRaises(nm.MonitorConflict):
                self.monitor.save_config(body)
            self.assertEqual(self.monitor.config_path.read_bytes(), before)
        self.assertEqual((self.path / 'network-monitor-config.lock').stat().st_mode & 0o777, 0o600)

    def test_two_concurrent_writers_with_same_revision_have_exactly_one_winner(self):
        first, second = self.draft(), self.draft()
        first['groups'][0]['name'] = '第一个标题'
        second['groups'][0]['name'] = '第二个标题'
        barrier, results = threading.Barrier(2), []
        def save(body):
            barrier.wait(timeout=2)
            try:
                results.append(('saved', self.monitor.save_config(body)['groups'][0]['name']))
            except nm.MonitorConflict:
                results.append(('conflict', None))
        threads = [threading.Thread(target=save, args=(body,)) for body in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(row[0] for row in results), ['conflict', 'saved'])
        self.assertEqual(self.monitor.admin_config()['groups'][0]['name'], next(name for state, name in results if state == 'saved'))

    def test_admin_configuration_revision_and_projection_use_one_read(self):
        value = self.monitor._config()
        with patch.object(self.monitor, '_config', side_effect=[value, {'version': 2, 'groups': []}]) as read:
            admin = self.monitor.admin_config()
        read.assert_called_once_with()
        self.assertEqual(admin['revision'], nm._config_revision(value))
        self.assertEqual([group['name'] for group in admin['groups']], [group['name'] for group in admin['status']['groups']])

    def test_all_forty_long_http_targets_fit_private_storage_and_read_back(self):
        target = 'https://example.com/' + 'a' * 2000
        draft = self.draft()
        draft['groups'] = [self.dynamic_group(1, [self.dynamic_node(index, target) for index in range(40)])]
        saved = self.monitor.save_config(draft)
        self.assertGreater(self.monitor.config_path.stat().st_size, nm.MAX_BODY_BYTES)
        self.assertLess(self.monitor.config_path.stat().st_size, nm.MAX_CONFIG_BYTES)
        self.assertEqual(len(saved['nodes']), 40)
        self.assertEqual(self.monitor.admin_config()['groups'], draft['groups'])
        self.monitor.collect()
        self.assertEqual(self.probe.call_count, 40)
        self.assertEqual(len(self.monitor.public_status()['groups'][0]['nodes']), 40)
        self.assertNotIn('example.com', json.dumps(self.monitor.public_status()))

    def test_group_node_limits_duplicate_ids_and_address_like_ids_are_rejected(self):
        valid = self.draft()
        valid['groups'] = [self.dynamic_group(index) for index in range(12)]
        valid['groups'][0]['nodes'] = [self.dynamic_node(index, '') for index in range(40)]
        nm.validate_v2_config({key: valid[key] for key in ('version', 'groups')})
        invalids = []
        too_many_groups = copy.deepcopy(valid)
        too_many_groups['groups'].append(self.dynamic_group(12))
        invalids.append(too_many_groups)
        too_many_nodes = copy.deepcopy(valid)
        too_many_nodes['groups'][1]['nodes'].append(self.dynamic_node(40, ''))
        invalids.append(too_many_nodes)
        duplicate = copy.deepcopy(valid)
        duplicate['groups'][1]['nodes'].append(copy.deepcopy(duplicate['groups'][0]['nodes'][0]))
        invalids.append(duplicate)
        duplicate_group = copy.deepcopy(valid)
        duplicate_group['groups'][1]['id'] = duplicate_group['groups'][0]['id']
        invalids.append(duplicate_group)
        for field in ('id', 'name'):
            for level in ('group', 'node'):
                bad = copy.deepcopy(valid)
                row = bad['groups'][0] if level == 'group' else bad['groups'][0]['nodes'][0]
                row[field] = 'private.example.com'
                invalids.append(bad)
        for body in invalids:
            with self.subTest(groups=len(body['groups'])), self.assertRaises(ValueError):
                self.monitor.save_config(body)
        self.assertFalse(self.monitor.config_path.exists())

    def test_sample_concurrency_is_bounded_to_eight_for_forty_nodes(self):
        draft = self.draft()
        draft['groups'] = [self.dynamic_group(1, [self.dynamic_node(index, TARGET if index % 2 else '8.8.8.8',
                           'http' if index % 2 else 'icmp') for index in range(40)])]
        self.monitor.save_config(draft)
        lock, eight_started, release = threading.Lock(), threading.Event(), threading.Event()
        counts = {'active': 0, 'maximum': 0, 'total': 0}
        def sample(target):
            with lock:
                counts['active'] += 1
                counts['total'] += 1
                counts['maximum'] = max(counts['maximum'], counts['active'])
                if counts['active'] == 8:
                    eight_started.set()
            release.wait(timeout=3)
            with lock:
                counts['active'] -= 1
            return {'status': 'healthy', 'latency_ms': 10}
        self.probe.side_effect = self.icmp_probe.side_effect = sample
        collector = threading.Thread(target=self.monitor.collect)
        collector.start()
        try:
            self.assertTrue(eight_started.wait(timeout=2))
        finally:
            release.set()
            collector.join(timeout=3)
        self.assertFalse(collector.is_alive())
        self.assertEqual(counts, {'active': 0, 'maximum': 8, 'total': 40})
        self.assertEqual((self.probe.call_count, self.icmp_probe.call_count), (20, 20))

    def test_probe_bindings_move_without_reselection_and_delete_never_mutates_provider(self):
        fetch = Mock(return_value=inventory())
        self.monitor.probe_fetch = fetch
        self.monitor.save_probe_config({'url': SOURCE})
        self.monitor.refresh_probe()
        draft = self.draft()
        selected = self.dynamic_node(1, inventory()[0]['id'], 'mmwx')
        draft['groups'] = [self.dynamic_group(1, [selected]), self.dynamic_group(2)]
        self.monitor.save_config(draft)
        fetch.reset_mock()
        self.monitor.collect()
        fetch.assert_called_once_with(SOURCE)
        self.assertEqual(node(self.monitor.public_status(), selected['id'])['status'], 'healthy')
        probe_config_before = self.monitor.probe_config_path.read_bytes()
        self.monitor.probe_cache_path.unlink()
        draft = self.draft()
        moved = draft['groups'][0]['nodes'].pop()
        draft['groups'][1]['nodes'].append(moved)
        self.monitor.save_config(draft)
        draft = self.draft()
        draft['groups'] = []
        self.monitor.save_config(draft)
        fetch.reset_mock()
        self.monitor.collect()
        fetch.assert_not_called()
        self.assertEqual(self.monitor.probe_config_path.read_bytes(), probe_config_before)

    def test_invalid_or_oversized_persisted_v2_does_not_restore_original_nodes(self):
        for raw in ('{', json.dumps({'version': 2, 'groups': 'bad'}), 'x' * (nm.MAX_CONFIG_BYTES + 1)):
            self.monitor.config_path.write_text(raw)
            self.assertEqual(self.monitor.admin_config()['groups'], [])
            self.assertEqual(self.monitor.public_status()['groups'], [])
            self.monitor.collect()
        self.probe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
