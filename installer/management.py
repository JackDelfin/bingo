#!/usr/bin/env python3
"""Owner-only Bingo management service (Python standard library only)."""
from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time

CURRENT_VERSION = 'v3.0.0'
ORIGINS = frozenset(('http://localhost:3000', 'http://127.0.0.1:3000'))
PATHS = frozenset(('/v1/status', '/v1/identity', '/v1/check', '/v1/update', '/v1/shutdown'))
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class Refusal(Exception):
    """A fixed, non-sensitive refusal suitable for the local launcher."""


def require(value, message):
    if not value:
        raise Refusal(message)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'Invalid JSON.')
        value[key] = item
    return value


def invalid_constant(value):
    raise Refusal('Invalid JSON.')


def parse_json(data):
    return json.loads(data.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=invalid_constant)


def version_ok(value):
    return isinstance(value, str) and len(value) <= 64 and re.fullmatch(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', value) is not None


def safe_path(raw):
    path = Path(raw)
    require(path.is_absolute() and '..' not in path.parts and not any(ord(c) < 32 or ord(c) == 127 for c in str(path)), 'Unsafe management path.')
    private_barrier = False
    for part in (*reversed(path.parents), path):
        try:
            item = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(item.st_mode) and item.st_uid in (0, os.geteuid())
                and (private_barrier or not item.st_mode & 0o022), 'Unsafe management parent.')
        # An owner-only directory excludes other users from descendants, as in
        # the preserving installer's existing path policy.
        if stat.S_ISDIR(item.st_mode) and item.st_uid == os.geteuid() and not item.st_mode & 0o077:
            private_barrier = True
    return path


def private_directory(path):
    safe_path(path)
    item = path.lstat()
    require(stat.S_ISDIR(item.st_mode) and item.st_uid == os.geteuid()
            and not item.st_mode & 0o077, 'Management directories must be private and owned.')


def owned_bytes(path, limit=4 * 1024 * 1024):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        item = os.fstat(stream.fileno())
        require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and item.st_uid == os.geteuid()
                and item.st_mode & 0o777 == 0o600 and item.st_size <= limit, 'Unsafe management file.')
        value = stream.read(limit + 1)
        require(len(value) <= limit, 'Management file exceeded its limit.')
        return value


def private_json(path):
    value = parse_json(owned_bytes(path, 65536))
    require(isinstance(value, dict), 'Invalid management state.')
    return value


def atomic_json(path, value, previous=None):
    safe_path(path)
    if previous is not None:
        require(private_json(path) == previous, 'Management state changed.')
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(16))
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        if previous is None:
            os.link(temporary, path, follow_symlinks=False)
        else:
            require(private_json(path) == previous, 'Management state changed.')
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def validate_installation(target, home):
    require(sys.platform == 'linux' and os.geteuid() != 0, 'Use the installed ordinary Linux user.')
    target, home = safe_path(target), safe_path(home)
    private_directory(target)
    private_directory(target / '.venue-install')
    item = home.lstat()
    require(stat.S_ISDIR(item.st_mode) and item.st_uid == os.geteuid() and not item.st_mode & 0o022, 'Unsafe management HOME.')
    markers = (target / '.git', target / 'source/.git', target / 'installer/bootstrap.py')
    require(all(not marker.exists() and not marker.is_symlink() for marker in markers),
            'A checkout is not an installed target.')
    receipt = private_json(target / '.venue-install/receipt.json')
    require(type(receipt.get('version')) is int and receipt['version'] == 1 and receipt.get('status') == 'installed'
            and type(receipt.get('uid')) is int and receipt['uid'] == os.geteuid() and receipt.get('target') == str(target)
            and receipt.get('payload_sha256') in {'fd50b3c289286e0bc596dd63d33838743ce105017aaa1f61f2ae0346c6e087cd',
                                                 '198ba57eaf14633986011dbe03d6876c743d198312693295a843f55fe0b50aa8',
                                                 'e90e4c48a91faf12ee41c46021668ff48d131a576edde277a33ebf44354c2c70'},
            'Unrecognized installed target receipt.')
    return target, home


def service_sha256(target):
    """SHA256(domain || uint64be(len) || management || uint64be(len) || updater).

    Length framing prevents ambiguous concatenations; the identity proof covers
    the running service's startup bytes, not whatever is on disk after refresh.
    """
    metadata = target / '.venue-install'
    code = owned_bytes(metadata / 'bingo-management.py')
    require(code == Path(__file__).read_bytes(), 'Installed management helper differs from the loaded helper.')
    updater = owned_bytes(metadata / 'bingo-release-update.py')
    digest = hashlib.sha256(b'Bingo management service v1\0')
    for data in (code, updater):
        digest.update(len(data).to_bytes(8, 'big'))
        digest.update(data)
    return digest.hexdigest()


def _prepare(target, home, *, create=True):
    try:
        target, home = validate_installation(target, home)
        digest = service_sha256(target)
        path = target / '.venue-install/management.json'
        if path.exists() or path.is_symlink():
            value = private_json(path)
            require(set(value) == {'schemaVersion', 'target', 'uid', 'token', 'serviceSha256'}
                    and type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1
                    and type(value.get('uid')) is int and value['uid'] == os.geteuid() and value.get('target') == str(target)
                    and isinstance(value.get('token'), str) and re.fullmatch(r'[0-9a-f]{64}', value['token'])
                    and isinstance(value.get('serviceSha256'), str) and re.fullmatch(r'[0-9a-f]{64}', value['serviceSha256']),
                    'Invalid management receipt; it will not be replaced.')
        else:
            require(create, 'Management must first be started from the Bingo launcher.')
            value = {'schemaVersion': 1, 'target': str(target), 'uid': os.geteuid(),
                     'token': secrets.token_hex(32), 'serviceSha256': digest}
            atomic_json(path, value)
        return value, digest
    except (OSError, ValueError, TypeError, RecursionError):
        raise Refusal('Unsafe or missing management installation state.') from None


@contextmanager
def management_lock(target):
    path = target / '.venue-install/management.lock'
    safe_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        item = os.fstat(fd)
        require(stat.S_ISREG(item.st_mode) and item.st_nlink == 1 and item.st_uid == os.geteuid()
                and item.st_mode & 0o777 == 0o600, 'Unsafe management lock.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('Management startup is busy; reopen Bingo shortly.') from None
        yield
    finally:
        os.close(fd)


def local_request(method, path, port, headers, deadline):
    """A bounded direct numeric-loopback exchange, never a proxy or redirect.

    Use a total monotonic deadline, not a resettable timeout on each header
    line, so a slow port squatter cannot hold the launcher indefinitely.
    """
    def timeout():
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Management readiness timed out.')
        return min(.4, remaining)

    wire = (method + ' ' + path + ' HTTP/1.0\r\nHost: localhost:' + str(port)
            + '\r\nOrigin: http://localhost:3000\r\nConnection: close\r\n'
            + ''.join(key + ': ' + value + '\r\n' for key, value in headers.items()))
    wire += 'Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}' if method == 'POST' else '\r\n'
    with socket.create_connection(('127.0.0.1', port), timeout=timeout()) as stream:
        stream.settimeout(timeout())
        stream.sendall(wire.encode('ascii'))
        data = b''
        while b'\r\n\r\n' not in data:
            stream.settimeout(timeout())
            block = stream.recv(4096)
            require(block, 'Unrecognized management listener.')
            data += block
            require(len(data) <= 20480, 'Unrecognized management listener.')
        raw_headers, body = data.split(b'\r\n\r\n', 1)
        require(len(raw_headers) <= 16384, 'Unrecognized management listener.')
        lines = raw_headers.split(b'\r\n')
        match = re.fullmatch(rb'HTTP/1\.[01] ([0-9]{3})(?: [\x20-\x7e]*)?', lines[0])
        if match is None:
            raise Refusal('Unrecognized management listener.')
        status = int(match.group(1))
        response_headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b':')
            key = name.decode('ascii').lower()
            require(separator and HEADER_NAME.fullmatch(key) and key not in response_headers
                    and all(byte >= 32 and byte != 127 for byte in value), 'Unrecognized management listener.')
            response_headers[key] = value.decode('ascii').strip(' ')
        length = response_headers.get('content-length', '')
        require(re.fullmatch(r'0|[1-9][0-9]{0,3}', length) and int(length) <= 4096
                and response_headers.get('content-type') == 'application/json'
                and 'transfer-encoding' not in response_headers, 'Unrecognized management listener.')
        while len(body) < int(length):
            stream.settimeout(timeout())
            block = stream.recv(min(4097 - len(body), int(length) - len(body)))
            require(block, 'Unrecognized management listener.')
            body += block
        require(len(body) == int(length), 'Unrecognized management listener.')
        return status, parse_json(body)


def identity(port, token, allowed_hashes, deadline):
    challenge = secrets.token_hex(16)
    try:
        status, value = local_request('GET', '/v1/identity', port, {'X-Bingo-Challenge': challenge}, deadline)
    except ConnectionRefusedError:
        return None
    require(status == 200 and isinstance(value, dict) and set(value) == {'serviceSha256', 'proof'},
            'Unrecognized management listener; capability was not sent.')
    digest, proof = value['serviceSha256'], value['proof']
    require(isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
            and isinstance(proof, str) and re.fullmatch(r'[0-9a-f]{64}', proof),
            'Unrecognized management listener; capability was not sent.')
    expected = hmac.new(token.encode('ascii'), (challenge + '.' + digest).encode('utf-8'), hashlib.sha256).hexdigest()
    require(hmac.compare_digest(expected, proof) and digest in allowed_hashes,
            'Unrecognized management listener; capability was not sent.')
    return digest


def spawn_service(target, home):
    child = subprocess.Popen([sys.executable, '-B', str(target / '.venue-install/bingo-management.py'), '--serve', str(target)],
                             start_new_session=True, close_fds=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env={**os.environ, 'HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1'})
    # No signals, process enumeration, or lifetime coupling to this launcher.
    threading.Thread(target=child.wait, daemon=True).start()


def _ensure_started(target, home, *, port=8766, spawn=None, readiness_timeout=5):
    try:
        target, home = validate_installation(target, home)
        with management_lock(target):
            value, digest = _prepare(target, home)
            deadline = time.monotonic() + min(5, max(.01, readiness_timeout))
            allowed = {digest, value['serviceSha256']}
            current = identity(port, value['token'], allowed, deadline)
            if current is not None and current != digest:
                # Only the freshly challenged, persisted old identity receives
                # the capability. An unknown listener is never sent a token.
                status, state = local_request('POST', '/v1/shutdown', port,
                                              {'X-Bingo-Token': value['token']}, deadline)
                require(status == 202 and isinstance(state, dict), 'Management is busy; reopen Bingo after its job finishes.')
                while time.monotonic() < deadline:
                    try:
                        current = identity(port, value['token'], allowed, deadline)
                    except (ConnectionResetError, TimeoutError):
                        # An acknowledged helper can close an already queued
                        # connection on exit. Retry only token-free challenges;
                        # never interpret a reset as proof the port is free.
                        time.sleep(min(.025, max(0, deadline - time.monotonic())))
                        continue
                    if current is None:
                        break
                    require(current != digest, 'Management listener changed during restart.')
                    time.sleep(min(.025, max(0, deadline - time.monotonic())))
                require(current is None, 'Management shutdown timed out.')
            if current is None:
                (spawn or spawn_service)(target, home)
                while time.monotonic() < deadline:
                    current = identity(port, value['token'], {digest}, deadline)
                    if current is not None:
                        break
                    time.sleep(min(.025, max(0, deadline - time.monotonic())))
                require(current == digest, 'Management did not become ready within five seconds.')
            if value['serviceSha256'] != digest:
                refreshed = {**value, 'serviceSha256': digest}
                atomic_json(target / '.venue-install/management.json', refreshed, previous=value)
            return 'http://localhost:3000#bingo-management=' + str(port) + '.' + value['token']
    except (OSError, ValueError, TypeError, RecursionError):
        raise Refusal('Management is unavailable or its local state is unsafe.') from None


def _serve(target, home, *, server_factory=None):
    value, digest = _prepare(target, home, create=False)
    path = target / '.venue-install/bingo-release-update.py'
    source = owned_bytes(path)
    require(service_sha256(target) == digest, 'Management helper bytes changed during startup.')
    # Compile the verified source bytes, not a stale/unverified .pyc; register
    # the dynamic module for dataclasses and other ordinary import machinery.
    name = '_bingo_installed_release_' + digest
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Refusal('Installed updater cannot be loaded.')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        exec(compile(source, str(path), 'exec'), module.__dict__)
        updater = module.ReleaseUpdater(target, home / '.local/state/venue-bingo/releases', CURRENT_VERSION)
        service = ManagementService(updater, value['token'], digest)
        server = (server_factory or create_server)(service, port=8766)
        try:
            server.serve_forever(poll_interval=.1)
        finally:
            server.server_close()
    finally:
        sys.modules.pop(name, None)


def main(argv=None, *, home=None):
    try:
        arguments = sys.argv[1:] if argv is None else argv
        require(len(arguments) == 2 and arguments[0] == '--serve', 'Invalid management invocation.')
        target = safe_path(arguments[1])
        selected_home = Path(os.environ.get('HOME', '')) if home is None else Path(home)
        target, selected_home = validate_installation(target, selected_home)
        _serve(target, selected_home)
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # CLI arguments, underlying paths, capabilities and updater/subprocess
        # exception details are never reflected to the desktop terminal.
        print('Bingo management could not start; reopen the installed Bingo app.', file=sys.stderr)
        return 1


def ensure_started(target: Path, home: Path):
    return _ensure_started(target, home)


class ManagementService:
    def __init__(self, updater, token, service_sha256):
        self.updater = updater
        self.token = token
        self.service_sha256 = service_sha256
        self.state = {'currentVersion': updater.current_version, 'phase': 'idle', 'latestVersion': None,
                      'available': False, 'message': 'Ready to check for updates.'}
        self.lock = threading.Lock()
        self.checked_version = None
        self.closing = False

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self, kind, version=None):
        with self.lock:
            require(not self.closing and self.state['phase'] not in ('checking', 'updating'), 'Bingo is busy.')
            if kind == 'update':
                require(version == self.checked_version and self.state['available'], 'Check for this update first.')
            self.state.update(phase='checking' if kind == 'check' else 'updating',
                              message='Checking for updates.' if kind == 'check' else 'Installing update.')
            self.checked_version = None
            state = dict(self.state)
            threading.Thread(target=self._job, args=(kind, version), daemon=True).start()
            return state

    def _job(self, kind, version):
        try:
            if kind == 'check':
                result = self.updater.check()
                require(isinstance(result, dict) and version_ok(result.get('latestVersion'))
                        and result.get('currentVersion') == self.state['currentVersion']
                        and type(result.get('available')) is bool, 'Invalid update metadata.')
                with self.lock:
                    self.checked_version = result['latestVersion'] if result['available'] else None
                    self.state.update(phase='idle', latestVersion=result['latestVersion'], available=result['available'],
                                      message='Update available.' if result['available'] else 'Bingo is up to date.')
            else:
                result = self.updater.install(version)
                require(result.get('installedVersion') == version, 'Update verification failed.')
                with self.lock:
                    self.state.update(phase='succeeded', currentVersion=version, available=False,
                                      message='Update installed. Reload Bingo.')
                    self.updater.current_version = version
        except Exception:
            with self.lock:
                self.checked_version = None
                self.state.update(phase='failed', available=False, message='Update check failed.' if kind == 'check' else 'Update failed. Reopen Bingo to retry.')

    def shutdown(self):
        with self.lock:
            require(not self.closing and self.state['phase'] not in ('checking', 'updating'), 'Bingo is busy.')
            self.closing = True
            return dict(self.state)


class ManagementHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # Never disclose capabilities, private paths, or updater exception text.
        pass


def create_server(service, *, port=8766):
    """Bind only IPv4 loopback; port=0 is the isolated-test injection seam."""
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def respond(self, status, data):
            body = json.dumps(data, separators=(',', ':')).encode('utf-8')
            self.send_response_only(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Connection', 'close')
            origin = getattr(self, 'headers', {}).get('origin')
            if origin in ORIGINS:
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def send_error(self, code, message=None, explain=None):
            # BaseHTTPRequestHandler's defaults contain attacker-controlled text.
            self.respond(405 if code == 501 else code, {'error': 'Request refused.'})

        def parse_request(self):
            # Parse one deliberately small HTTP/1.x request, close after every
            # response. Reject obs-fold, duplicates and alternate framing rather
            # than rely on email-style HTTPMessage normalization.
            self.command = None
            self.request_version = 'HTTP/1.0'
            self.close_connection = True
            self.headers = {}
            self.requestline = self.raw_requestline.decode('iso-8859-1').rstrip('\r\n')
            match = re.fullmatch(rb"([!#$%&'*+.^_`|~0-9A-Za-z-]+) ([^\x00-\x20\x7f]+) HTTP/1\.[01]\r\n", self.raw_requestline)
            if match is None:
                self.send_error(400)
                return False
            self.command, self.path = (part.decode('iso-8859-1') for part in match.groups())
            total = 0
            for _ in range(65):
                line = self.rfile.readline(16385)
                total += len(line)
                if total > 16384 or not line.endswith(b'\r\n'):
                    self.send_error(400)
                    return False
                if line == b'\r\n':
                    break
                name, separator, value = line[:-2].partition(b':')
                key = name.decode('iso-8859-1').lower()
                if (not separator or not HEADER_NAME.fullmatch(key) or key in self.headers
                        or any(byte < 32 and byte != 9 or byte == 127 for byte in value)):
                    self.send_error(400)
                    return False
                self.headers[key] = value.decode('iso-8859-1').strip(' \t')
            else:
                self.send_error(400)
                return False
            if ('transfer-encoding' in self.headers or 'expect' in self.headers or 'trailer' in self.headers
                    or 'content-encoding' in self.headers or 'cookie' in self.headers):
                self.send_error(400)
                return False
            length = self.headers.get('content-length')
            if length is not None and (not re.fullmatch(r'0|[1-9][0-9]{0,9}', length)
                                       or self.command != 'POST' and length != '0'):
                self.send_error(400)
                return False
            if self.headers.get('origin') not in ORIGINS or self.headers.get('host') not in {
                    'localhost:' + str(self.server.server_address[1]), '127.0.0.1:' + str(self.server.server_address[1])}:
                self.send_error(403)
                return False
            if self.path not in PATHS:
                self.send_error(404)
                return False
            supplied = self.headers.get('x-bingo-token', '')
            if not (self.command == 'OPTIONS' or self.command == 'GET' and self.path == '/v1/identity'):
                if not hmac.compare_digest(supplied.encode('utf-8'), service.token.encode('ascii')):
                    self.send_error(403)
                    return False
            return True

        def do_GET(self):
            if self.path == '/v1/identity':
                challenge = self.headers.get('x-bingo-challenge', '')
                if not re.fullmatch(r'[0-9a-f]{32}', challenge):
                    self.send_error(400)
                    return
                proof = hmac.new(service.token.encode('ascii'),
                                 (challenge + '.' + service.service_sha256).encode('utf-8'), hashlib.sha256).hexdigest()
                self.respond(200, {'serviceSha256': service.service_sha256, 'proof': proof})
            elif self.path == '/v1/status':
                self.respond(200, service.snapshot())
            else:
                self.send_error(405)

        def do_POST(self):
            if self.path not in ('/v1/check', '/v1/update', '/v1/shutdown'):
                self.send_error(405)
                return
            if self.headers.get('content-type') != 'application/json':
                self.send_error(415)
                return
            length = self.headers.get('content-length')
            if length is None:
                self.send_error(400)
                return
            if int(length) > 4096:
                self.send_error(413)
                return
            try:
                body = self.rfile.read(int(length))
                require(len(body) == int(length), 'Invalid JSON.')
                data = parse_json(body)
                require(isinstance(data, dict), 'Invalid JSON.')
                if self.path == '/v1/update':
                    require(set(data) == {'version', 'confirm'} and data['confirm'] is True
                            and version_ok(data['version']), 'Invalid update request.')
                else:
                    require(data == {}, 'Invalid request.')
            except (ValueError, OSError, Refusal, RecursionError):
                self.send_error(400)
                return
            try:
                if self.path == '/v1/shutdown':
                    state = service.shutdown()
                else:
                    state = service.start('check' if self.path == '/v1/check' else 'update', data.get('version'))
            except Refusal:
                self.send_error(409)
                return
            self.respond(202, state)
            if self.path == '/v1/shutdown':
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        def do_OPTIONS(self):
            method = 'GET' if self.path in ('/v1/status', '/v1/identity') else 'POST'
            requested = self.headers.get('access-control-request-method')
            names = [name.strip().lower() for name in self.headers.get('access-control-request-headers', '').split(',') if name.strip()]
            private = self.headers.get('access-control-request-private-network')
            if requested != method:
                self.send_error(405)
                return
            if (len(names) != len(set(names)) or not set(names) <= {'content-type', 'x-bingo-token', 'x-bingo-challenge'}
                    or private not in (None, 'true')):
                self.send_error(400)
                return
            self.send_response_only(204)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', self.headers['origin'])
            self.send_header('Vary', 'Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network')
            self.send_header('Access-Control-Allow-Methods', method)
            if names:
                self.send_header('Access-Control-Allow-Headers', ', '.join(sorted(names)))
            if private == 'true':
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()

        def log_message(self, format, *args):
            pass
    return ManagementHTTPServer(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    raise SystemExit(main())
