"""Host-side public release updater (Python standard library only).

Publisher trust is HTTPS GitHub and its checksum sidecar, NOT an independent
cryptographic signature. Release source is extracted into a managed cache;
no developer checkout or Git history is ever updated.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = 'JackDelfin/bingo'
LATEST_API = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
RELEASE_BASE = 'https://github.com/' + REPOSITORY + '/releases/'
ARCHIVE = 'bingo-release.zip'
SIDECAR = ARCHIVE + '.sha256'
MAX_METADATA = 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_SIDECAR = 128
MAX_EXTRACTED = 256 * 1024 * 1024
MAX_ENTRIES = 10000
MAX_RATIO = 200
MAX_MANIFEST = 4 * 1024 * 1024
REQUIRED_FILES = frozenset(('install.sh', 'installer/bootstrap.py', 'installer/desktop.py',
                            'installer/release_update.py', 'installer/management.py', 'LICENSE'))
FETCH_TIMEOUT = 30


class Refusal(Exception):
    """Safe, fixed-text update refusal. Never includes untrusted input."""


def require(condition, message):
    if not condition:
        raise Refusal(message)


def version_number(value):
    require(isinstance(value, str) and len(value) <= 64 and
            re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value),
            'Invalid stable release version.')
    return tuple(int(part) for part in value[1:].split('.'))


def json_object(data):
    def pairs(items):
        result = {}
        for name, value in items:
            require(name not in result, 'Duplicate JSON fields are forbidden.')
            result[name] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise Refusal('Invalid release JSON.') from None
    require(isinstance(value, dict), 'Invalid release JSON.')
    return value


def safe_path(path):
    require(path.is_absolute() and '..' not in path.parts and
            all(ord(c) >= 32 and ord(c) != 127 and c not in ':\\' for c in str(path)),
            'Unsafe update path.')
    barrier = False
    for item in [*reversed(path.parents), path]:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid in (0, os.geteuid()) and
                (barrier or not info.st_mode & 0o022), 'Unsafe linked or writable update path.')
        if item != path:
            require(stat.S_ISDIR(info.st_mode), 'Unsafe update path parent.')
        if stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077:
            barrier = True
    return path


def private_directory(path, create=False):
    safe_path(path)
    if create and not path.exists():
        path.mkdir(mode=0o700)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            'Update directories must be owned and private.')


def private_json(path):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077 and info.st_size <= MAX_METADATA,
                'Update state must be an owned private regular file.')
        data = stream.read(MAX_METADATA + 1)
    require(len(data) <= MAX_METADATA, 'Update state exceeds its limit.')
    return json_object(data)


@contextmanager
def update_lock(target):
    path = target / '.venue-install/release-update.lock'
    safe_path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and not info.st_mode & 0o077, 'Unsafe release update lock.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('Another release update is running.') from None
        yield
    finally:
        os.close(fd)


def exclusive_write(path, data):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_asset_url(url):
    return isinstance(url, str) and re.fullmatch(
        re.escape(RELEASE_BASE) + r'download/v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)/' +
        r'bingo-release\.zip(?:\.sha256)?', url) is not None


def allowed_redirect(url, original):
    if url == original:
        return True
    if not isinstance(url, str) or len(url) > 8192 or '#' in url or any(ord(c) < 33 or ord(c) > 126 for c in url):
        return False
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc in
            ('release-assets.githubusercontent.com', 'objects.githubusercontent.com') and
            re.fullmatch(r'/github-production-release-asset(?:-2e65be)?/[0-9]+/[A-Za-z0-9._-]+', parsed.path) is not None and
            parsed.path.split('/')[-1] not in ('.', '..'))


def fetch_https(url, *, limit, timeout):
    """Anonymous HTTPS only; explicit redirects, no proxy, finite size/time.

    Redirect query strings are signed GitHub/CDN transport details, never logged
    or forwarded as authorization. This is publisher trust, not signature trust.
    """
    require(url == LATEST_API or public_asset_url(url), 'Untrusted public release URL.')
    require(type(limit) is int and 0 < limit <= MAX_ARCHIVE and
            type(timeout) in (int, float) and 0 < timeout <= FETCH_TIMEOUT, 'Invalid download limits.')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    original = url
    deadline = time.monotonic() + min(120, timeout * 4)
    for attempt in range(4):
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'Release download deadline exceeded.')
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Bingo-Release-Updater/1', 'Accept': 'application/json' if original == LATEST_API else 'application/octet-stream',
            'Accept-Encoding': 'identity'})
        try:
            response = opener.open(request, timeout=min(timeout, remaining))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                require(public_asset_url(original) and attempt < 3 and allowed_redirect(location, original),
                        'Untrusted public release redirect.')
                url = location
                continue
            require(response.status == 200, 'Public release server refused the download.')
            require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity',
                    'Encoded release transport is not supported.')
            length = response.headers.get('Content-Length')
            require(length is None or (re.fullmatch(r'[0-9]{1,10}', length) and int(length) <= limit),
                    'Release response exceeds its download limit.')
            data = bytearray()
            while True:
                require(time.monotonic() < deadline, 'Release download deadline exceeded.')
                chunk = response.read1(min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
                require(len(data) <= limit, 'Release response exceeds its download limit.')
            require(length is None or len(data) == int(length), 'Truncated public release response.')
            return bytes(data)
    raise Refusal('Public release redirect limit exceeded.')


def installer_environment():
    allowed = ('HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TERM', 'DISPLAY',
               'WAYLAND_DISPLAY', 'XAUTHORITY', 'DBUS_SESSION_BUS_ADDRESS',
               'XDG_RUNTIME_DIR', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME')
    result = {name: os.environ[name] for name in allowed if name in os.environ}
    result['PATH'] = '/usr/local/bin:/usr/bin:/bin'
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


FORBIDDEN_PARTS = frozenset(('.git', '.gitmodules', '.ssh', '.gnupg', '.aws', '.docker',
    '.netrc', '.npmrc', '.pypirc', '.venue-install', 'node_modules', 'vendor', 'site-packages',
    '__pycache__', '.venv', 'venv', '.cache', '.pytest_cache', 'db', 'data', 'database', 'mongodb',
    'private', 'backups', 'backup', 'qa', 'staging', 'research', 'meta', 'credentials', 'secrets',
    'secrets.json', 'credentials.json', 'links.json', 'private-links.json', 'id_rsa', 'id_ed25519'))


def member_parts(name):
    require(isinstance(name, str) and 0 < len(name) <= 1024 and
            all(ord(c) >= 32 and ord(c) != 127 and c not in '\\:' for c in name),
            'Unsafe release member path.')
    parts = name.split('/')
    require(len(parts) <= 32 and parts[0] == 'bingo' and
            all(part and part not in ('.', '..') for part in parts), 'Unsafe release member path.')
    for part in parts[1:]:
        lowered = part.casefold()
        require(lowered not in FORBIDDEN_PARTS and not lowered.startswith('.env') and
                not re.search(r'\.(?:db|sqlite(?:3)?)(?:-|$)', lowered) and
                not lowered.endswith(('.log', '.pyc', '.pem', '.key')),
                'Private state, Git history, databases and dependencies are forbidden.')
    return parts


def validate_extra_fields(extra):
    offset = 0
    while offset < len(extra):
        require(offset + 4 <= len(extra), 'Malformed release ZIP extra field.')
        identifier = int.from_bytes(extra[offset:offset + 2], 'little')
        length = int.from_bytes(extra[offset + 2:offset + 4], 'little')
        offset += 4 + length
        require(offset <= len(extra) and identifier in (0x0001, 0x5455, 0x7875),
                'Linked or unsupported release ZIP extra field.')


def validate_local_header(bundle, item):
    # ZIP has two headers per member. The local header must not conceal flags
    # or Unix link metadata that are absent from the central directory.
    stream = bundle.fp
    if stream is None or item.header_offset < 0:
        raise Refusal('Invalid release ZIP local header.')
    stream.seek(item.header_offset)
    header = stream.read(30)
    require(len(header) == 30 and header[:4] == b'PK\x03\x04', 'Invalid release ZIP local header.')
    flags = int.from_bytes(header[6:8], 'little')
    method = int.from_bytes(header[8:10], 'little')
    name_length = int.from_bytes(header[26:28], 'little')
    extra_length = int.from_bytes(header[28:30], 'little')
    name = stream.read(name_length)
    extra = stream.read(extra_length)
    require(flags == item.flag_bits and method == item.compress_type and
            len(name) == name_length and len(extra) == extra_length and
            name.decode('utf-8' if flags & 0x800 else 'cp437') == item.filename,
            'Conflicting release ZIP local header.')
    validate_extra_fields(extra)


def validate_members(bundle):
    entries = bundle.infolist()
    require(0 < len(entries) <= MAX_ENTRIES, 'Release ZIP entry limit exceeded.')
    names = {}
    folded = set()
    total = 0
    for item in entries:
        require(item.orig_filename == item.filename, 'Ambiguous release member name.')
        validate_extra_fields(item.extra)
        validate_local_header(bundle, item)
        name = item.filename[:-1] if item.is_dir() else item.filename
        member_parts(name)
        require(name not in names and name.casefold() not in folded, 'Duplicate or ambiguous release member.')
        names[name] = item
        folded.add(name.casefold())
        kind = stat.S_IFMT(item.external_attr >> 16)
        require(kind in ((0, stat.S_IFDIR) if item.is_dir() else (0, stat.S_IFREG)) and
                not (item.external_attr >> 16) & 0o7000, 'Linked or special release member.')
        require(not item.flag_bits & (1 | 0x40) and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                'Encrypted or unsupported release member.')
        require(not item.is_dir() or item.file_size == 0, 'Invalid release directory member.')
        require(item.file_size >= 0 and item.compress_size >= 0 and
                item.file_size <= MAX_EXTRACTED and item.file_size <= max(1, item.compress_size) * MAX_RATIO,
                'Release ZIP expansion limit exceeded.')
        total += item.file_size
        require(total <= MAX_EXTRACTED, 'Release ZIP expansion limit exceeded.')
    for name in names:
        parts = name.split('/')
        for count in range(1, len(parts)):
            parent = '/'.join(parts[:count])
            require(parent not in names or names[parent].is_dir(), 'Conflicting release file and directory paths.')
    return entries


def extract_release(bundle, entries, inventory, work):
    directories = set()
    for item in entries:
        parts = item.filename.rstrip('/').split('/')
        for count in range(1, len(parts) + (1 if item.is_dir() else 0)):
            directories.add('/'.join(parts[:count]))
    for name in sorted(directories, key=lambda value: (value.count('/'), value)):
        (work / name).mkdir(mode=0o700)  # Exclusive; never adopt even an empty directory.
    total = 0
    for item in entries:
        if item.is_dir():
            continue
        fd = os.open(work / item.filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        checksum = hashlib.sha256()
        count = 0
        with os.fdopen(fd, 'wb') as output, bundle.open(item) as source:
            while True:
                chunk = source.read(65536)
                if not chunk:
                    break
                count += len(chunk)
                total += len(chunk)
                require(count <= item.file_size and total <= MAX_EXTRACTED, 'Release ZIP expansion limit exceeded.')
                checksum.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        require(count == item.file_size, 'Release member length mismatch.')
        if item.filename != 'bingo/release.json':
            require(checksum.hexdigest() == inventory[item.filename.removeprefix('bingo/')],
                    'Release file checksum mismatch.')


def release_manifest(bundle, version):
    info = bundle.getinfo('bingo/release.json')
    require(info.file_size <= MAX_MANIFEST, 'Release manifest exceeds its limit.')
    manifest = json_object(bundle.read(info))
    require(set(manifest) == {'schemaVersion', 'version', 'repository', 'files'} and
            type(manifest.get('schemaVersion')) is int and manifest['schemaVersion'] == 1 and
            manifest.get('version') == version and manifest.get('repository') == REPOSITORY,
            'Release manifest identity mismatch.')
    files = manifest.get('files')
    require(isinstance(files, dict), 'Invalid release file inventory.')
    for name, checksum in files.items():
        require(isinstance(name, str) and isinstance(checksum, str) and
                re.fullmatch(r'[0-9a-f]{64}', checksum), 'Invalid release file checksum.')
    actual = {item.filename.removeprefix('bingo/') for item in bundle.infolist()
              if not item.is_dir() and item.filename != 'bingo/release.json'}
    require(set(files) == actual and REQUIRED_FILES <= actual and
            any(name.startswith('source/') for name in actual), 'Release file inventory mismatch.')
    return files


class ReleaseUpdater:
    """Check/install trusted latest stable releases.

    Optional fetch(url, *, limit, timeout) -> bytes and subprocess.run-compatible
    runner are dependency-injection seams. Injected fetchers must enforce the
    same HTTPS/redirect policy; returned bytes are bounded again here.
    """
    def __init__(self, target: Path, cache: Path, current_version: str, *, fetch=None, runner=None):
        self.target, self.cache, self.current_version = Path(target), Path(cache), current_version
        self.fetch = fetch if fetch is not None else fetch_https
        self.runner = runner if runner is not None else subprocess.run
        self.current_version = self._recorded_version()

    def _fetch(self, url, limit):
        try:
            data = self.fetch(url, limit=limit, timeout=FETCH_TIMEOUT)
        except Exception:
            raise Refusal('Release download failed or timed out.') from None
        require(isinstance(data, bytes) and len(data) <= limit, 'Release download exceeds its limit.')
        return data

    def _latest(self):
        version_number(self.current_version)
        document = json_object(self._fetch(LATEST_API, MAX_METADATA))
        version = document.get('tag_name')
        version_number(version)
        require(document.get('draft') is False and document.get('prerelease') is False,
                'Only published stable releases are supported.')
        require(document.get('html_url') == RELEASE_BASE + 'tag/' + version,
                'Foreign release repository or URL.')
        assets = document.get('assets')
        require(isinstance(assets, list) and len(assets) == 2, 'Ambiguous public release assets.')
        by_name = {}
        for asset in assets:
            require(isinstance(asset, dict), 'Invalid public release asset.')
            name = asset.get('name')
            require(isinstance(name, str) and name in (ARCHIVE, SIDECAR) and name not in by_name,
                    'Ambiguous public release assets.')
            size = asset.get('size')
            require(type(size) is int and 0 < size <= (MAX_ARCHIVE if name == ARCHIVE else MAX_SIDECAR),
                    'Invalid public release asset size.')
            require(asset.get('state') == 'uploaded' and asset.get('browser_download_url') ==
                    RELEASE_BASE + 'download/' + version + '/' + name,
                    'Foreign or unpublished public release asset.')
            by_name[name] = asset
        require(set(by_name) == {ARCHIVE, SIDECAR}, 'Missing public release assets.')
        return document, by_name

    def _target_preflight(self):
        private_directory(self.target)
        private_directory(self.target / '.venue-install')
        module_path = Path(__file__).resolve()
        if module_path.parent.name == 'installer':
            checkout = module_path.parent.parent
            require(not self.target.is_relative_to(checkout) and not checkout.is_relative_to(self.target),
                    'A developer checkout is not an installation target.')
        for parent in (self.target, *self.target.parents):
            require(not (parent / '.git').exists() and not (parent / '.git').is_symlink(),
                    'A developer checkout is not an installation target.')
        require(not (self.target / 'source/.git').exists() and
                not (self.target / 'source/.git').is_symlink() and
                not (self.target / 'installer/bootstrap.py').exists(),
                'A checkout is not an installation target.')
        value = private_json(self.target / '.venue-install/receipt.json')
        require(type(value.get('version')) is int and value['version'] == 1 and
                value.get('status') == 'installed' and type(value.get('uid')) is int and
                value['uid'] == os.geteuid() and value.get('target') == str(self.target),
                'A recognized existing installation receipt is required.')
        safe_path(self.cache)
        for parent in (self.cache, *self.cache.parents):
            marker = parent / '.git'
            require(not marker.exists() and not marker.is_symlink() and
                    not (parent / 'installer/bootstrap.py').exists(),
                    'Update cache must not be inside a developer or release source checkout.')
        require(not self.cache.is_relative_to(self.target) and not self.target.is_relative_to(self.cache),
                'Update cache and installation target must be separate.')

    def _recorded_version(self):
        path = self.cache / 'installed-release.json'
        if not path.exists() and not path.is_symlink():
            return self.current_version
        private_directory(self.cache)
        value = private_json(path)
        require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1 and
                value.get('target') == str(self.target), 'Invalid installed release pointer.')
        version = value.get('installedVersion')
        version_number(version)
        return max((self.current_version, version), key=version_number)

    def install(self, expected_version: str) -> dict:
        """Install only a newer, still-latest exact version on a managed target.

        A zero installer exit is not success until its offline --check also
        succeeds. Failed sources/logs remain private for manual investigation.
        """
        try:
            version_number(expected_version)
            version_number(self.current_version)
            self._target_preflight()
            with update_lock(self.target):
                self._target_preflight()
                baseline = self._recorded_version()
                document, assets = self._latest()
                require(document['tag_name'] == expected_version, 'Latest release changed; check again.')
                require(version_number(expected_version) > version_number(baseline),
                        'Same-version installs and downgrades are forbidden.')
                return self._install_release(document, assets)
        except Refusal:
            raise
        except Exception:
            raise Refusal('Release update failed; preserve the private cache and logs.') from None

    def _install_release(self, document, assets):
        archive = self._fetch(assets[ARCHIVE]['browser_download_url'], MAX_ARCHIVE)
        sidecar = self._fetch(assets[SIDECAR]['browser_download_url'], MAX_SIDECAR)
        require(len(archive) == assets[ARCHIVE]['size'] and len(sidecar) == assets[SIDECAR]['size'],
                'Public release asset length changed.')
        require(re.fullmatch(rb'[0-9a-fA-F]{64}  bingo-release\.zip\n', sidecar) and
                sidecar[:64].decode('ascii').lower() == hashlib.sha256(archive).hexdigest(),
                'Public release checksum verification failed.')
        self._target_preflight()
        private_directory(self.cache, create=True)
        work = Path(tempfile.mkdtemp(prefix='release-', dir=self.cache))
        exclusive_write(work / ARCHIVE, archive)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            entries = validate_members(bundle)
            inventory = release_manifest(bundle, document['tag_name'])
            extract_release(bundle, entries, inventory, work)
        repo = work / 'bingo'
        log = work / 'install.log'
        fd = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 1800
        with os.fdopen(fd, 'wb') as stream:
            for options in (['--offline'], ['--offline', '--check']):
                command = [sys.executable, '-B', str(repo / 'installer/bootstrap.py'), *options,
                           '--dir', str(self.target)]
                self._target_preflight()
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'Release installer timed out; preserve private logs.')
                try:
                    outcome = self.runner(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                          timeout=remaining, shell=False, cwd=str(repo), env=installer_environment())
                except Exception:
                    raise Refusal('Release installer failed or timed out; preserve private logs.') from None
                stream.flush()
                require(type(outcome.returncode) is int and outcome.returncode == 0,
                        'Release installer or verification failed; preserve private logs.')
        result = {'schemaVersion': 1, 'target': str(self.target), 'installedVersion': document['tag_name'],
                  'repoPath': str(repo), 'logPath': str(log)}
        pointer = self.cache / 'installed-release.json'
        if pointer.exists() or pointer.is_symlink():
            private_json(pointer)
        temporary = work / 'installed-release.json'
        exclusive_write(temporary, (json.dumps(result) + '\n').encode())
        os.replace(temporary, pointer)
        directory_fd = os.open(self.cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        self.current_version = document['tag_name']
        return result

    def check(self) -> dict:
        document, _ = self._latest()
        version = document['tag_name']
        return {'currentVersion': self.current_version, 'latestVersion': version,
                'available': version_number(version) > version_number(self.current_version),
                'releaseUrl': document['html_url']}
