"""Real loopback HTTP with injected updater; no container/runtime execution."""
from contextlib import redirect_stderr
import hashlib
import io
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('bingo_management', ROOT / 'installer/management.py')
management = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(management)
TOKEN = 'a' * 64
SERVICE_HASH = 'b' * 64
ORIGIN = 'http://localhost:3000'


class FakeUpdater:
    def __init__(self):
        self.current_version = 'v3.0.0'
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.check_result = {'currentVersion': self.current_version, 'latestVersion': 'v3.1.0',
                             'available': True, 'releaseUrl': 'https://github.com/JackDelfin/bingo/releases/tag/v3.1.0'}
        self.install_result = {'installedVersion': 'v3.1.0', 'repoPath': '/private/not-for-http'}
        self.error = None

    def check(self):
        self.calls.append(('check',))
        self.entered.set()
        self.release.wait(3)
        if self.error:
            raise RuntimeError(self.error)
        return dict(self.check_result)

    def install(self, expected_version):
        self.calls.append(('install', expected_version))
        self.entered.set()
        self.release.wait(3)
        if self.error:
            raise RuntimeError(self.error)
        return dict(self.install_result)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.updater = FakeUpdater()
        self.service = management.ManagementService(self.updater, TOKEN, SERVICE_HASH)
        self.server = management.create_server(self.service, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.updater.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method='GET', path='/v1/status', body=None, headers=None):
        actual = {'Host': 'localhost:' + str(self.port), 'Origin': ORIGIN, 'X-Bingo-Token': TOKEN}
        if body is not None:
            actual['Content-Type'] = 'application/json'
        for key, value in (headers or {}).items():
            if value is None:
                actual.pop(key, None)
            else:
                actual[key] = value
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=actual)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), json.loads(data) if data else None
        finally:
            connection.close()

    def raw(self, data):
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as stream:
            stream.sendall(data)
            stream.shutdown(socket.SHUT_WR)
            pieces = []
            while True:
                data = stream.recv(65536)
                if not data:
                    break
                pieces.append(data)
        return b''.join(pieces)

    def wait_phase(self, expected):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = self.request()[2]
            if state.get('phase') == expected:
                return state
            time.sleep(.005)
        self.fail('Expected job phase was not reached')

    def test_authenticated_status_is_idle_without_automatic_updater_work(self):
        status, headers, state = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(state, {'currentVersion': 'v3.0.0', 'phase': 'idle', 'latestVersion': None,
                                 'available': False, 'message': 'Ready to check for updates.'})
        self.assertEqual(self.updater.calls, [])
        self.assertEqual(headers.get('Access-Control-Allow-Origin'), ORIGIN)
        self.assertNotIn('Access-Control-Allow-Credentials', headers)
        self.assertNotIn('Set-Cookie', headers)
        self.assertEqual(headers.get('Cache-Control'), 'no-store')

    def test_status_rejects_untrusted_or_ambiguous_http_without_cors(self):
        for headers in ({'Origin': None}, {'Origin': 'null'}, {'Origin': ORIGIN + '/'},
                        {'Origin': 'https://localhost:3000'}, {'Origin': 'http://localhost:3000.evil'},
                        {'Host': 'localhost'}, {'Host': 'localhost:8766'}, {'Host': 'evil.invalid'},
                        {'X-Bingo-Token': None}, {'X-Bingo-Token': 'wrong'}, {'Cookie': 'session=x'}):
            with self.subTest(headerNames=list(headers)):
                status, response_headers, state = self.request(headers=headers)
                self.assertIn(status, (400, 403))
                if headers.get('Origin') not in (None, ORIGIN) or 'Origin' in headers:
                    self.assertNotIn('Access-Control-Allow-Origin', response_headers)
                self.assertEqual(set(state), {'error'})
        for path in ('/v1/status/', '/v1/status?x=1', '/v1/status#x', '//v1/status',
                     'http://localhost/v1/status', '/v1/%73tatus', '/private'):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path)[0], 404)
        prefix = ('GET /v1/status HTTP/1.1\r\nHost: localhost:' + str(self.port) +
                  '\r\nOrigin: ' + ORIGIN + '\r\nX-Bingo-Token: ' + TOKEN + '\r\n')
        for extra in ('Host: localhost:1\r\n', 'x-bingo-token: wrong\r\n', 'Origin: ' + ORIGIN + '\r\n',
                      'X-Other: a\r\nx-other: b\r\n', 'Transfer-Encoding: chunked\r\n',
                      'Content-Length: 1\r\n', 'Content-Length: 0\r\nContent-Length: 0\r\n',
                      'Content-Length : 0\r\n', 'X-Fold: a\r\n b\r\n', 'Expect: 100-continue\r\n'):
            response = self.raw((prefix + extra + '\r\n').encode())
            self.assertTrue(response.startswith(b'HTTP/1.0 400'), 'Ambiguous request was accepted')
        self.assertEqual(self.request(headers={'Origin': 'http://127.0.0.1:3000',
                                              'Host': '127.0.0.1:' + str(self.port)})[0], 200)

    def test_identity_challenge_proves_token_possession_without_disclosing_it(self):
        challenge = 'c' * 32
        status, _, result = self.request(path='/v1/identity', headers={'X-Bingo-Token': None,
                                                                     'X-Bingo-Challenge': challenge})
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {'serviceSha256', 'proof'})
        expected = hmac.new(TOKEN.encode('ascii'), (challenge + '.' + SERVICE_HASH).encode('utf-8'), hashlib.sha256).hexdigest()
        self.assertTrue(hmac.compare_digest(result['proof'], expected))
        self.assertEqual(result['serviceSha256'], SERVICE_HASH)
        self.assertFalse(TOKEN in json.dumps(result))
        for bad in (None, 'C' * 32, 'c' * 31, 'c' * 33, 'g' * 32):
            self.assertEqual(self.request(path='/v1/identity', headers={'X-Bingo-Token': None,
                                                                      'X-Bingo-Challenge': bad})[0], 400)
        self.assertEqual(self.updater.calls, [])

    def test_preflight_is_narrow_and_private_network_only_for_trusted_origin(self):
        headers = {'X-Bingo-Token': None, 'Access-Control-Request-Method': 'POST',
                   'Access-Control-Request-Headers': 'content-type,x-bingo-token',
                   'Access-Control-Request-Private-Network': 'true'}
        status, result, data = self.request('OPTIONS', '/v1/check', headers=headers)
        self.assertEqual(status, 204)
        self.assertIsNone(data)
        self.assertEqual(result.get('Access-Control-Allow-Origin'), ORIGIN)
        self.assertEqual(result.get('Access-Control-Allow-Private-Network'), 'true')
        self.assertEqual(result.get('Access-Control-Allow-Methods'), 'POST')
        self.assertEqual(result.get('Access-Control-Allow-Headers'), 'content-type, x-bingo-token')
        self.assertNotIn('Access-Control-Allow-Credentials', result)
        for changed in ({'Origin': 'http://evil.invalid'}, {'Access-Control-Request-Method': 'DELETE'},
                        {'Access-Control-Request-Headers': 'x-other'},
                        {'Access-Control-Request-Private-Network': 'false'}):
            status, result, _ = self.request('OPTIONS', '/v1/check', headers={**headers, **changed})
            self.assertIn(status, (400, 403, 405))
            self.assertNotIn('Access-Control-Allow-Private-Network', result)
        for method in ('HEAD', 'PUT', 'DELETE', 'PATCH', 'TRACE', 'CONNECT', 'WHATEVER'):
            connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=2)
            try:
                connection.request(method, '/v1/status', headers={'Host': 'localhost:' + str(self.port),
                                                               'Origin': ORIGIN, 'X-Bingo-Token': TOKEN})
                self.assertEqual(connection.getresponse().status, 405)
            finally:
                connection.close()

    def test_explicit_check_then_confirmed_install_runs_asynchronous_exclusive_jobs(self):
        self.updater.release.clear()
        status, _, state = self.request('POST', '/v1/check', b'{}')
        self.assertEqual(status, 202)
        self.assertEqual(state['phase'], 'checking')
        self.assertTrue(self.updater.entered.wait(1))
        self.assertEqual(self.request()[2]['phase'], 'checking')
        for path, body in (('/v1/check', b'{}'), ('/v1/update', b'{"version":"v3.1.0","confirm":true}'),
                           ('/v1/shutdown', b'{}')):
            self.assertEqual(self.request('POST', path, body)[0], 409)
        self.updater.release.set()
        state = self.wait_phase('idle')
        self.assertEqual(state['latestVersion'], 'v3.1.0')
        self.assertIs(state['available'], True)
        self.updater.release.clear()
        self.updater.entered.clear()
        status, _, state = self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')
        self.assertEqual(status, 202)
        self.assertEqual(state['phase'], 'updating')
        self.assertTrue(self.updater.entered.wait(1))
        self.assertEqual(self.request('POST', '/v1/check', b'{}')[0], 409)
        self.updater.release.set()
        state = self.wait_phase('succeeded')
        self.assertEqual(state['currentVersion'], 'v3.1.0')
        self.assertIs(state['available'], False)
        self.assertNotIn('/private/', json.dumps(state))
        self.assertEqual(self.updater.calls, [('check',), ('install', 'v3.1.0')])
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')[0], 409)
        self.assertEqual(self.request('POST', '/v1/shutdown', b'{}')[0], 202)
        self.thread.join(1)
        self.assertFalse(self.thread.is_alive())

    def test_post_requires_small_unambiguous_json_and_explicit_confirmation(self):
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')[0], 409)
        cases = [('check', b'{}', {'Content-Type': 'text/plain'}),
                 ('check', b'{}', {'Content-Type': None}), ('check', b'{"a":1,"a":2}', {}),
                 ('check', b'{"x":{"a":1,"a":2}}', {}), ('check', b'[]', {}),
                 ('check', b'{"x":NaN}', {}), ('check', b'{"extra":true}', {}),
                 ('check', b'{}' + b' ' * 4095, {}), ('check', b'\xff', {}),
                 ('check', b'{} trailing', {}), ('shutdown', b'{"confirm":true}', {})]
        for path, body, headers in cases:
            with self.subTest(path=path, size=len(body), headerNames=list(headers)):
                self.assertIn(self.request('POST', '/v1/' + path, body, headers)[0], (400, 413, 415))
        self.assertEqual(self.updater.calls, [])
        self.assertEqual(self.request('POST', '/v1/check', b'{}' + b' ' * 4094)[0], 202)
        self.wait_phase('idle')
        for body in (b'{"version":"v3.1.0"}', b'{"version":"v3.1.0","confirm":false}',
                     b'{"version":"v3.1.0","confirm":1}', b'{"version":"v3.1.0","confirm":true,"x":1}',
                     b'{"version":"v3.1.0","confirm":true,"confirm":true}',
                     b'{"version":"/private/secret","confirm":true}'):
            self.assertEqual(self.request('POST', '/v1/update', body)[0], 400)
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v9.0.0","confirm":true}')[0], 409)
        self.assertEqual(self.updater.calls, [('check',)])

    def test_failed_or_malformed_checks_invalidate_authorization_and_hide_details(self):
        self.request('POST', '/v1/check', b'{}')
        self.wait_phase('idle')
        self.updater.check_result['latestVersion'] = '/private/secret-' + TOKEN
        self.request('POST', '/v1/check', b'{}')
        state = self.wait_phase('failed')
        self.assertFalse(TOKEN in json.dumps(state))
        self.assertNotIn('/private/', json.dumps(state))
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')[0], 409)
        self.updater.error = '/private/log secret=' + TOKEN
        self.request('POST', '/v1/check', b'{}')
        state = self.wait_phase('failed')
        self.assertEqual(state['message'], 'Update check failed.')
        self.assertFalse(TOKEN in json.dumps(state))
        self.assertEqual(state['currentVersion'], 'v3.0.0')
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')[0], 409)

    def test_unverified_install_never_advances_current_version(self):
        self.request('POST', '/v1/check', b'{}')
        self.wait_phase('idle')
        self.updater.install_result['installedVersion'] = 'v9.0.0'
        self.assertEqual(self.request('POST', '/v1/update', b'{"version":"v3.1.0","confirm":true}')[0], 202)
        state = self.wait_phase('failed')
        self.assertEqual(state['currentVersion'], 'v3.0.0')
        self.assertEqual(state['message'], 'Update failed. Reopen Bingo to retry.')


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='management-tests-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / 'home'
        self.home.mkdir(mode=0o700)
        self.target = self.home / 'installed'
        self.target.mkdir(mode=0o700)
        self.metadata = self.target / '.venue-install'
        self.metadata.mkdir(mode=0o700)
        self.receipt_path = self.metadata / 'management.json'
        self.write_private(self.metadata / 'receipt.json', json.dumps({
            'version': 1, 'status': 'installed', 'target': str(self.target), 'uid': os.geteuid(),
            'payload_sha256': 'fd50b3c289286e0bc596dd63d33838743ce105017aaa1f61f2ae0346c6e087cd'}).encode())
        self.write_private(self.metadata / 'bingo-management.py', Path(management.__file__).read_bytes())
        self.write_private(self.metadata / 'bingo-release-update.py', b'# injected fake updater bytes\n')

    def write_private(self, path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def test_private_receipt_uses_stable_capability_and_binds_both_helper_bytes(self):
        value, digest = management._prepare(self.target, self.home)
        self.assertEqual(set(value), {'schemaVersion', 'target', 'uid', 'token', 'serviceSha256'})
        self.assertTrue(bool(__import__('re').fullmatch(r'[0-9a-f]{64}', value['token'])))
        self.assertEqual(value['schemaVersion'], 1)
        self.assertEqual(value['target'], str(self.target))
        self.assertEqual(value['uid'], os.geteuid())
        self.assertEqual(value['serviceSha256'], digest)
        original = self.receipt_path.read_bytes()
        self.assertEqual(self.receipt_path.stat().st_mode & 0o777, 0o600)
        again, same = management._prepare(self.target, self.home)
        self.assertTrue(again['token'] == value['token'])
        self.assertEqual(same, digest)
        self.assertTrue(self.receipt_path.read_bytes() == original)
        self.write_private(self.metadata / 'bingo-release-update.py', b'# changed injected fake updater\n')
        persisted, changed = management._prepare(self.target, self.home)
        self.assertNotEqual(changed, digest)
        self.assertEqual(persisted['serviceSha256'], digest)
        self.assertTrue(persisted['token'] == value['token'])
        self.assertTrue(self.receipt_path.read_bytes() == original)

    def test_missing_or_unrecognized_installation_never_creates_management_state(self):
        path = self.metadata / 'receipt.json'
        original = path.read_bytes()
        for data in (None, b'{}', b'{"version":1,"version":1}',
                     json.dumps({'version': 1, 'status': 'failed', 'target': str(self.target), 'uid': os.geteuid()}).encode()):
            if data is None:
                path.unlink(missing_ok=True)
            else:
                self.write_private(path, data)
            with self.assertRaises(management.Refusal):
                management._prepare(self.target, self.home)
            self.assertFalse(self.receipt_path.exists())
        self.write_private(path, original)
        with self.assertRaises(management.Refusal):
            management._prepare(Path('relative'), self.home)
        with self.assertRaises(management.Refusal):
            management._prepare(self.target / '..' / 'installed', self.home)

    def test_linked_shared_or_malformed_existing_state_is_never_replaced(self):
        self.write_private(self.receipt_path, json.dumps({'schemaVersion': 1, 'target': str(self.target),
                          'uid': os.geteuid(), 'token': TOKEN, 'serviceSha256': SERVICE_HASH}).encode())
        original = self.receipt_path.read_bytes()
        for candidate in (self.target, self.metadata, self.metadata / 'receipt.json',
                          self.metadata / 'bingo-management.py', self.metadata / 'bingo-release-update.py', self.receipt_path):
            mode = candidate.stat().st_mode & 0o777
            candidate.chmod(mode | 0o020)
            try:
                with self.assertRaises(management.Refusal):
                    management._prepare(self.target, self.home)
            finally:
                candidate.chmod(mode)
        for path in (self.receipt_path, self.metadata / 'bingo-management.py', self.metadata / 'receipt.json'):
            saved = path.with_suffix('.saved')
            path.rename(saved)
            path.symlink_to(saved)
            try:
                with self.assertRaises(management.Refusal):
                    management._prepare(self.target, self.home)
            finally:
                path.unlink()
                saved.rename(path)
        linked = self.base / 'linked'
        os.link(self.receipt_path, linked)
        try:
            with self.assertRaises(management.Refusal):
                management._prepare(self.target, self.home)
        finally:
            linked.unlink()
        for field, bad in (('token', 'A' * 64), ('uid', -1), ('schemaVersion', True),
                           ('target', str(self.base)), ('serviceSha256', 'not-a-hash')):
            state = json.loads(original)
            state[field] = bad
            self.write_private(self.receipt_path, json.dumps(state).encode())
            snapshot = self.receipt_path.read_bytes()
            with self.assertRaises(management.Refusal):
                management._prepare(self.target, self.home)
            self.assertTrue(self.receipt_path.read_bytes() == snapshot)
        self.write_private(self.receipt_path, original)
        source = self.metadata / 'bingo-management.py'
        source.write_bytes(source.read_bytes() + b'\n# mismatched source\n')
        with self.assertRaises(management.Refusal):
            management._prepare(self.target, self.home)

    def run_server(self, server):
        def serve():
            try:
                server.serve_forever(poll_interval=.005)
            finally:
                server.server_close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            thread.join(2)
        self.addCleanup(cleanup)
        return thread

    def free_port(self):
        with socket.socket() as stream:
            stream.bind(('127.0.0.1', 0))
            return stream.getsockname()[1]

    def test_ensure_starts_verified_service_once_and_reuses_stable_capability(self):
        port = self.free_port()
        spawns = []
        def spawn(target, home):
            value, digest = management._prepare(target, home, create=False)
            service = management.ManagementService(FakeUpdater(), value['token'], digest)
            server = management.create_server(service, port=port)
            self.run_server(server)
            spawns.append(server)
        url = management._ensure_started(self.target, self.home, port=port, spawn=spawn)
        self.assertTrue(isinstance(url, str) and url.startswith('http://localhost:3000#bingo-management=' + str(port) + '.'))
        value = json.loads(self.receipt_path.read_bytes())
        self.assertTrue(url.endswith('.' + value['token']))
        snapshot = self.receipt_path.read_bytes()
        self.assertTrue(management._ensure_started(self.target, self.home, port=port, spawn=spawn) == url)
        self.assertEqual(len(spawns), 1)
        self.assertTrue(self.receipt_path.read_bytes() == snapshot)
        self.assertEqual((self.metadata / 'management.lock').stat().st_mode & 0o777, 0o600)
        with mock.patch.object(management, '_ensure_started', return_value='sentinel') as injected:
            self.assertEqual(management.ensure_started(self.target, self.home), 'sentinel')
        injected.assert_called_once_with(self.target, self.home)

    def test_known_old_service_is_authenticated_shutdown_then_replaced_without_token_rotation(self):
        value, old_hash = management._prepare(self.target, self.home)
        old = management.ManagementService(FakeUpdater(), value['token'], old_hash)
        server = management.create_server(old, port=0)
        port = server.server_address[1]
        thread = self.run_server(server)
        self.write_private(self.metadata / 'bingo-release-update.py', b'# new injected core version\n')
        spawns = []
        def spawn(target, home):
            thread.join(1)
            self.assertFalse(thread.is_alive())
            current, digest = management._prepare(target, home, create=False)
            self.assertTrue(current['token'] == value['token'])
            self.assertEqual(current['serviceSha256'], old_hash)
            fresh = management.create_server(management.ManagementService(FakeUpdater(), current['token'], digest), port=port)
            self.run_server(fresh)
            spawns.append(fresh)
        url = management._ensure_started(self.target, self.home, port=port, spawn=spawn)
        self.assertTrue(url.endswith('.' + value['token']))
        self.assertEqual(len(spawns), 1)
        current, digest = management._prepare(self.target, self.home)
        self.assertNotEqual(digest, old_hash)
        self.assertEqual(current['serviceSha256'], digest)
        self.assertTrue(current['token'] == value['token'])
        self.assertTrue(old.closing)

    def test_unknown_listener_receives_only_challenge_never_capability(self):
        value, _ = management._prepare(self.target, self.home)
        seen = []
        owner = self
        class Squatter(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, dict(self.headers)))
                challenge = self.headers.get('X-Bingo-Challenge', '')
                # Even a token-valid proof cannot authorize an unknown hash.
                unknown_hash = 'd' * 64
                proof = hmac.new(value['token'].encode(), (challenge + '.' + unknown_hash).encode(), hashlib.sha256).hexdigest()
                data = json.dumps({'serviceSha256': unknown_hash, 'proof': proof}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Squatter)
        self.run_server(server)
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=server.server_address[1],
                                       spawn=lambda *args: owner.fail('Unknown listener must not cause a spawn'))
        self.assertTrue(seen)
        self.assertTrue(all(path == '/v1/identity' for path, _ in seen))
        self.assertTrue(all('X-Bingo-Token' not in headers for _, headers in seen))
        self.assertFalse(value['token'] in json.dumps(seen))

    def test_checkout_markers_including_dangling_links_refuse_management_state(self):
        (self.target / 'source').mkdir(mode=0o700)
        for path in (self.target / '.git', self.target / 'source/.git'):
            path.symlink_to(self.base / 'missing-checkout')
            try:
                with self.assertRaises(management.Refusal):
                    management._prepare(self.target, self.home)
                self.assertFalse(self.receipt_path.exists())
            finally:
                path.unlink()

    def test_child_never_initializes_missing_management_state(self):
        with self.assertRaises(management.Refusal):
            management._serve(self.target, self.home, server_factory=lambda *args, **kwargs: self.fail('Must not bind'))
        self.assertFalse(self.receipt_path.exists())

    def test_slow_listener_cannot_extend_total_readiness_deadline(self):
        class SlowListener(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    for byte in b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n':
                        self.connection.sendall(bytes([byte]))
                        time.sleep(.01)
                except OSError:
                    pass
            def log_message(self, format, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowListener)
        self.run_server(server)
        started = time.monotonic()
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=server.server_address[1],
                                       readiness_timeout=.08, spawn=lambda *args: self.fail('Do not spawn against a listener'))
        self.assertLess(time.monotonic() - started, .4)

    def test_old_busy_service_refuses_restart_and_preserves_existing_receipt(self):
        value, digest = management._prepare(self.target, self.home)
        original = self.receipt_path.read_bytes()
        updater = FakeUpdater()
        updater.release.clear()
        self.addCleanup(updater.release.set)
        old = management.ManagementService(updater, value['token'], digest)
        server = management.create_server(old, port=0)
        self.run_server(server)
        old.start('check')
        self.assertTrue(updater.entered.wait(1))
        self.write_private(self.metadata / 'bingo-release-update.py', b'# changed injected fake\n')
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=server.server_address[1],
                                       spawn=lambda *args: self.fail('Busy known helper must not be replaced'))
        self.assertFalse(old.closing)
        self.assertTrue(self.receipt_path.read_bytes() == original)
        updater.release.set()

    def test_busy_or_unsafe_startup_lock_never_spawns_or_rewrites_state(self):
        calls = []
        with management.management_lock(self.target):
            with self.assertRaises(management.Refusal):
                management._ensure_started(self.target, self.home, port=self.free_port(), spawn=lambda *args: calls.append(args))
        self.assertEqual(calls, [])
        path = self.metadata / 'management.lock'
        path.chmod(0o640)
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=self.free_port(), spawn=lambda *args: calls.append(args))
        path.chmod(0o600)
        saved = self.base / 'saved-lock'
        path.rename(saved)
        path.symlink_to(saved)
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=self.free_port(), spawn=lambda *args: calls.append(args))
        path.unlink()
        saved.rename(path)
        os.link(path, self.base / 'hardlink-lock')
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=self.free_port(), spawn=lambda *args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertFalse(self.receipt_path.exists())

    def test_known_hash_with_invalid_identity_proof_never_receives_capability(self):
        value, digest = management._prepare(self.target, self.home)
        with mock.patch.object(management, 'local_request', return_value=(200, {'serviceSha256': digest, 'proof': '0' * 64})) as exchange:
            with self.assertRaises(management.Refusal):
                management._ensure_started(self.target, self.home, port=self.free_port(), spawn=lambda *args: self.fail('Must refuse'))
        self.assertEqual(exchange.call_count, 1)
        arguments = exchange.call_args.args
        self.assertEqual(arguments[:2], ('GET', '/v1/identity'))
        self.assertEqual(set(arguments[3]), {'X-Bingo-Challenge'})
        self.assertFalse(value['token'] in json.dumps(arguments))

    def test_child_loads_only_verified_installed_sibling_without_automatic_jobs(self):
        fake = b'''from dataclasses import dataclass\n@dataclass\nclass ReleaseUpdater:\n    target: object\n    cache: object\n    current_version: str\n    def check(self):\n        raise RuntimeError("must not auto-check")\n    def install(self, version):\n        raise RuntimeError("must not auto-install")\n'''
        self.write_private(self.metadata / 'bingo-release-update.py', fake)
        management._prepare(self.target, self.home)
        captured = []
        class Server:
            def serve_forever(self, **kwargs):
                captured.append('served')
            def server_close(self):
                captured.append('closed')
        def factory(service, *, port):
            captured.append((service, port))
            return Server()
        management._serve(self.target, self.home, server_factory=factory)
        service, port = captured[0]
        self.assertEqual(port, 8766)
        self.assertEqual(captured[1:], ['served', 'closed'])
        self.assertEqual(service.updater.target, self.target)
        self.assertEqual(service.updater.cache, self.home / '.local/state/venue-bingo/releases')
        self.assertEqual(service.updater.current_version, 'v3.0.0')
        self.assertEqual(service.snapshot()['phase'], 'idle')

    def test_installed_helper_reconstructs_successful_release_for_http_status_and_check(self):
        (self.home / '.local/state/venue-bingo').mkdir(mode=0o700, parents=True)
        self.write_private(self.metadata / 'bingo-release-update.py',
                           (ROOT / 'installer/release_update.py').read_bytes())
        management._prepare(self.target, self.home)
        files = {name: b'public release fixture\n' for name in (
            'install.sh', 'installer/bootstrap.py', 'installer/desktop.py',
            'installer/release_update.py', 'installer/management.py', 'LICENSE', 'source/main.txt')}
        manifest = {'schemaVersion': 1, 'version': 'v3.1.0', 'repository': 'JackDelfin/bingo',
                    'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as bundle:
            for name, data in files.items():
                bundle.writestr('bingo/' + name, data)
            bundle.writestr('bingo/release.json', json.dumps(manifest))
        archive = output.getvalue()
        payloads = {'bingo-release.zip': archive,
                    'bingo-release.zip.sha256': (hashlib.sha256(archive).hexdigest() + '  bingo-release.zip\n').encode()}
        base = 'https://github.com/JackDelfin/bingo/releases/'
        document = {'tag_name': 'v3.1.0', 'draft': False, 'prerelease': False,
                    'html_url': base + 'tag/v3.1.0',
                    'assets': [{'name': name, 'state': 'uploaded', 'size': len(data),
                                'browser_download_url': base + 'download/v3.1.0/' + name}
                               for name, data in payloads.items()]}
        responses = {base + 'download/v3.1.0/' + name: data for name, data in payloads.items()}
        latest_url = 'https://api.github.com/repos/JackDelfin/bingo/releases/latest'
        responses[latest_url] = json.dumps(document).encode()
        urls = []
        class Response(io.BytesIO):
            status = 200
            headers = {}
        def open_request(request, **kwargs):
            urls.append(request.full_url)
            self.assertIn(request.full_url, responses)
            return Response(responses[request.full_url])
        opener = mock.Mock()
        opener.open.side_effect = open_request
        services = []
        def factory(service, *, port):
            services.append(service)
            return mock.Mock()
        with mock.patch.object(urllib.request, 'build_opener', return_value=opener), \
                mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as runner:
            management._serve(self.target, self.home, server_factory=factory)
            result = services[-1].updater.install('v3.1.0')
            self.assertEqual(result['installedVersion'], 'v3.1.0')
            pointer = self.home / '.local/state/venue-bingo/releases/installed-release.json'
            persisted = pointer.read_bytes()
            self.assertEqual(json.loads(persisted)['installedVersion'], 'v3.1.0')
            self.assertEqual(runner.call_count, 2)
            urls.clear()
            management._serve(self.target, self.home, server_factory=factory)
            self.assertEqual(urls, [], 'Reconstruction must not automatically fetch metadata')
            fresh = services[-1]
            self.assertEqual(fresh.updater.current_version, 'v3.1.0')
            server = management.create_server(fresh, port=0)
            self.run_server(server)
            def exchange(method, path):
                return management.local_request(method, path, server.server_address[1],
                                                {'X-Bingo-Token': fresh.token}, time.monotonic() + 2)
            status, before = exchange('GET', '/v1/status')
            self.assertEqual(status, 200)
            self.assertEqual(exchange('POST', '/v1/check')[0], 202)
            deadline = time.monotonic() + 2
            while True:
                status, after = exchange('GET', '/v1/status')
                self.assertEqual(status, 200)
                if after['phase'] != 'checking' or time.monotonic() >= deadline:
                    break
                time.sleep(.005)
            self.assertEqual((before['currentVersion'], after['currentVersion'], after['phase'],
                              after['latestVersion'], after['available']),
                             ('v3.1.0', 'v3.1.0', 'idle', 'v3.1.0', False))
            self.assertIsNone(fresh.checked_version)
            self.assertEqual(urls, [latest_url])
            self.assertEqual(runner.call_count, 2)
            self.assertEqual(pointer.read_bytes(), persisted)

    def test_cli_accepts_only_serve_with_absolute_installed_target_and_fixed_errors(self):
        errors = io.StringIO()
        with redirect_stderr(errors), mock.patch.object(management, '_serve') as serve:
            for args in ([], ['--serve'], ['--serve', 'relative'], ['--serve', str(self.target), 'extra'], ['--bad-secret']):
                self.assertNotEqual(management.main(args, home=self.home), 0)
            serve.assert_not_called()
            self.assertEqual(management.main(['--serve', str(self.target)], home=self.home), 0)
            serve.assert_called_once_with(self.target, self.home)
        self.assertNotIn(str(self.target), errors.getvalue())
        self.assertNotIn('bad-secret', errors.getvalue())
        with redirect_stderr(errors), mock.patch.object(management, '_serve', side_effect=RuntimeError('/private/secret-' + TOKEN)):
            self.assertNotEqual(management.main(['--serve', str(self.target)], home=self.home), 0)
        self.assertNotIn('/private/', errors.getvalue())
        self.assertFalse(TOKEN in errors.getvalue())

    def test_spawn_uses_fixed_detached_devnull_argument_vector_without_capability(self):
        with mock.patch.object(management.subprocess, 'Popen') as popen, mock.patch.object(management.threading, 'Thread'):
            management.spawn_service(self.target, self.home)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [sys.executable, '-B', str(self.metadata / 'bingo-management.py'), '--serve', str(self.target)])
        self.assertTrue(kwargs['start_new_session'])
        self.assertTrue(kwargs['close_fds'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual([kwargs[key] for key in ('stdin', 'stdout', 'stderr')], [management.subprocess.DEVNULL] * 3)
        self.assertTrue(kwargs['env']['HOME'] == str(self.home))
        self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')

    def test_readiness_is_bounded_without_process_killing(self):
        calls = []
        start = time.monotonic()
        with self.assertRaises(management.Refusal):
            management._ensure_started(self.target, self.home, port=self.free_port(),
                                       spawn=lambda *args: calls.append(args), readiness_timeout=.1)
        self.assertLess(time.monotonic() - start, .8)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
