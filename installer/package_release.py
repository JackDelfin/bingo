#!/usr/bin/env python3
"""Standard-library release freezing and clean public packaging.

CLI (each destination must be new; no build or deployment is performed)::

    python3 -B installer/package_release.py --freeze ROOT ROOT/installer/releases/settings-v3
    python3 -B installer/package_release.py --package ROOT OUTPUT --version v3.0.0

OUTPUT's parent must exist. OUTPUT is created mode 0700, or must already be
an owner-only mode-0700 directory without either fixed-name artifact. Files are
exclusive mode-0600 children. Stable tags use vMAJOR.MINOR.PATCH. JSON receipts
go to stdout; controlled refusals go to stderr with a nonzero exit status.
"""
import argparse
from functools import wraps
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
MAX_ENTRIES = 10000

MODULES = ('bootstrap', 'desktop', 'release_update', 'management', 'package_release')
PUBLIC_TESTS = ('test_installer.py', 'test_desktop.py', 'test_desktop_management.py',
                'test_release_update.py', 'test_management.py', 'test_package_release.py', 'fake_podman.py')
PUBLIC_README = b'''# Bingo\n\nManual 75-ball Bingo with synchronized controller and audience displays.\n\nSee [installation](docs/INSTALL.md), [audio callouts](docs/AUDIO-CALLOUTS.md)\nand [Settings](docs/SETTINGS.md). Run `bash install.sh` as your ordinary Linux\nuser, never with sudo. Internet is needed for a fresh installation. Existing\ninstallations retain their database and private controller links.\n\nThe public release archive contains only reviewed source, installer inputs,\ndocumentation and tests; it contains no Git history or installed/private state.\n`release.json` inventories every other file with SHA-256.\n'''

FRONTEND_TREES = ('src', 'public/audio', 'scripts')
FRONTEND_FILES = (
    'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json',
    'tsconfig.app.json', 'tsconfig.node.json', 'eslint.config.js', 'index.html',
    'Dockerfile', '.dockerignore', '.prettierrc.json',
)


class Refusal(Exception):
    pass


def _controlled(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except OSError:
            raise Refusal('Public input or exclusive output filesystem operation failed.') from None
    return call


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def _public_name(name):
    forbidden = {'node_modules', '__pycache__', 'database', 'db', 'qa', 'private',
                 'secrets', 'credentials', 'state', '.venue-install', '.ssh'}
    safe_hidden = {'.dockerignore', '.prettierrc.json', '.gitignore'}
    parts = name.split('/')
    for part in parts:
        lower = part.lower()
        if (not part or part in ('.', '..') or '\\' in part or ':' in part or
                any(ord(c) < 32 or ord(c) == 127 for c in part) or
                lower in forbidden or (part.startswith('.') and part not in safe_hidden) or
                lower.startswith(('controller-links', 'controller_links', 'private-', 'secret-')) or
                lower.endswith(('.pyc', '.pyo', '.sqlite', '.sqlite3', '.db', '.bson', '.pem', '.key'))):
            raise Refusal('Forbidden or unsafe public member.')


def _path(path):
    path = Path(path)
    if '..' in path.parts or any(ord(c) < 32 or ord(c) == 127 for c in str(path)):
        raise Refusal('Unsafe filesystem path.')
    path = path.absolute()
    for parent in (*reversed(path.parents), path):
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise Refusal('Linked filesystem path is forbidden.')
    return path


def _open_directory(path):
    # Anchor every ancestor with O_NOFOLLOW, not just the final filename. An
    # lstat walk alone does not prevent an ancestor being swapped after checking.
    path = _path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_file(path, flags):
    directory = _open_directory(path.parent)
    try:
        return os.open(path.name, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    finally:
        os.close(directory)


def _regular(metadata):
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise Refusal('Only single-link regular public files are permitted.')


def _public_content(data):
    # Deny high-confidence credentials/capability links, not source-code route
    # templates or deliberately short public test routes. Never echo contents.
    patterns = (
        rb'https?://[^\s\x22\x27<>`]+/control/[A-Za-z0-9_%.-]+/[A-Za-z0-9_%.-]+',
        rb'/control/[A-Za-z0-9_%.-]+/[A-Za-z0-9_%-]{16,}',
        rb'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----',
        rb'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[A-Z0-9]{16})\b',
        rb'(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql)://[^\s/@:]+:[^\s/@]+@',
        rb'(?:^|\n)[ \t]*(?:export[ \t]+)?[A-Z_]*(?:PASSWORD|SECRET|TOKEN|API_KEY)[A-Z_]*[ \t]*=[ \t]*[\x22\x27]?[A-Za-z0-9_+/=-]{24,}',
    )
    if any(re.search(pattern, data) for pattern in patterns):
        raise Refusal('Private content is forbidden in public release inputs.')


def _read(path):
    path = _path(path)
    checked = path.lstat()
    _regular(checked)
    fd = _open_file(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _regular(before)
        if _identity(checked) != _identity(before):
            raise Refusal('Public input changed before being read.')
        if before.st_size > MAX_EXTRACTED_BYTES:
            raise Refusal('Public input exceeds the extracted-byte limit.')
        data = stream.read(MAX_EXTRACTED_BYTES + 1)
        if len(data) > MAX_EXTRACTED_BYTES:
            raise Refusal('Public input exceeds the extracted-byte limit.')
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise Refusal('Public input changed while being read.')
        _regular(after)
    if _identity(_path(path).lstat()) != _identity(after):
        raise Refusal('Public input changed after being read.')
    _public_content(data)
    return data


def _tree(path):
    path = _path(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise Refusal('Public input tree must be a directory.')
    files, pending, total = {}, [path], 0
    while pending:
        directory = pending.pop()
        _path(directory)
        for p in sorted(directory.iterdir()):
            name = p.relative_to(path).as_posix()
            _public_name(name)
            mode = p.lstat().st_mode
            if stat.S_ISDIR(mode):
                pending.append(p)
            else:
                files[name] = _read(p)
                total += len(files[name])
                if len(files) > MAX_ENTRIES or total > MAX_EXTRACTED_BYTES:
                    raise Refusal('Public input tree exceeds release size limits.')
    return files


def _manifests(files):
    payload = {name.removeprefix('payload/'): _sha(data)
               for name, data in files.items()
               if name.startswith('payload/') and name != 'payload/files.sha256'}
    files['payload/files.sha256'] = ''.join(
        checksum + '  ' + name + '\n' for name, checksum in sorted(payload.items())).encode('utf-8')
    fresh = {name: _sha(data) for name, data in files.items()
             if name.split('/')[0] in ('assets', 'build', 'source')}
    files['fresh-files.json'] = _json(fresh)


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise Refusal('Duplicate JSON manifest member.')
        result[name] = value
    return result


def _verify_release(files):
    required = {'payload/release-tools/update.py', 'payload/frontend-dist/index.html',
                'assets/bingo.sh', 'build/Dockerfile.frontend', 'build/Dockerfile.backend',
                'source/LICENSE', 'source/frontend/package.json', 'source/backend/package.json'}
    if not required <= files.keys() or not any(name.startswith('payload/backend-dist/') for name in files):
        raise Refusal('Frozen release is missing required public inputs.')
    expected = dict(files)
    _manifests(expected)
    try:
        fresh = json.loads(files['fresh-files.json'], object_pairs_hook=_unique_object)
        expected_fresh = json.loads(expected['fresh-files.json'])
        if fresh != expected_fresh or files['payload/files.sha256'] != expected['payload/files.sha256']:
            raise Refusal('Frozen release checksum or exact membership mismatch.')
    except (KeyError, ValueError, UnicodeError):
        raise Refusal('Missing or malformed frozen release inventories.') from None


def _mutable_settings(name):
    return (name in ('fresh-files.json', 'payload/files.sha256') or
            name.startswith('payload/frontend-dist/') or
            any(name.startswith('source/frontend/' + sub + '/') for sub in FRONTEND_TREES) or
            name in {'source/frontend/' + sub for sub in FRONTEND_FILES})


def _verify_settings(baseline, settings):
    if any(baseline.get(name) != settings.get(name)
           for name in baseline.keys() | settings.keys() if not _mutable_settings(name)):
        raise Refusal('Settings release changed a protected baseline member.')


def _check_limits(files):
    if len(files) > MAX_ENTRIES or sum(map(len, files.values())) > MAX_EXTRACTED_BYTES:
        raise Refusal('Public release exceeds the entry or extracted-byte limit.')


@_controlled
def freeze_release(root: Path, destination: Path):
    root, destination = _path(root), _path(destination)
    if destination != root / 'installer/releases/settings-v3' or destination.exists():
        raise Refusal('Freeze destination must be the new, absent installer/releases/settings-v3.')
    baseline = root / 'installer/releases/controller-audio-v2'
    files = _tree(baseline)
    _verify_release(files)
    frontend = root / 'source/frontend'
    overlay = {}
    for sub in FRONTEND_TREES:
        overlay.update({sub + '/' + name: data for name, data in _tree(frontend / sub).items()})
    for name in FRONTEND_FILES:
        path = frontend / name
        if name in ('package.json', 'package-lock.json', 'vite.config.ts', 'index.html') or os.path.lexists(path):
            overlay[name] = _read(path)
    files.update({'source/frontend/' + name: data for name, data in overlay.items()})
    files = {name: data for name, data in files.items()
             if not name.startswith('payload/frontend-dist/')}
    dist = _tree(frontend / 'dist')
    if 'index.html' not in dist:
        raise Refusal('Build the reviewed frontend first; dist/index.html is required.')
    files.update({'payload/frontend-dist/' + name: data for name, data in dist.items()})
    _manifests(files)
    _check_limits(files)
    destination.mkdir(mode=0o700)
    identity = _identity(destination.lstat())
    try:
        for name, data in sorted(files.items()):
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _write_new(path, data)
    except BaseException:
        if _same_inode(destination, identity):
            shutil.rmtree(destination)
        raise
    return {
        'payloadManifestSha256': _sha(files['payload/files.sha256']),
        'freshManifestSha256': _sha(files['fresh-files.json']),
        'sourceFileCount': sum(name.startswith('source/') for name in files),
        'frontendOverlayFileCount': len(overlay),
        'frontendDistFileCount': sum(name.startswith('payload/frontend-dist/') for name in files),
    }


def _output_preflight(root, output):
    _path(output)
    if output == root or root.is_relative_to(output) or any(
            output.is_relative_to(root / sub) for sub in ('installer', 'source', 'tests', 'docs')):
        raise Refusal('Output directory must not overlap public input trees.')
    private_barrier = False
    for parent in (*reversed(output.parents), output):
        if not parent.exists():
            continue
        metadata = parent.lstat()
        if metadata.st_uid not in (0, os.geteuid()) or (not private_barrier and metadata.st_mode & 0o022):
            raise Refusal('Unsafe writable output parent.')
        if stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077:
            private_barrier = True
    if output.exists():
        metadata = output.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or
                stat.S_IMODE(metadata.st_mode) != 0o700):
            raise Refusal('Output must be an owner-only mode-0700 directory.')
    elif not output.parent.is_dir():
        raise Refusal('Output parent must already exist.')
    for name in ('bingo-release.zip', 'bingo-release.zip.sha256'):
        if os.path.lexists(output / name):
            raise Refusal('Output artifact collision; previous artifacts are never overwritten.')


def _identity(metadata):
    return metadata.st_dev, metadata.st_ino


def _same_inode(path, identity):
    try:
        return _identity(path.lstat()) == identity
    except FileNotFoundError:
        return False


def _unlink_owned(path, identity):
    if _same_inode(path, identity):
        path.unlink()


def _write_new(path, data):
    fd = _open_file(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    identity = _identity(os.fstat(fd))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _unlink_owned(path, identity)
        raise
    return identity


@_controlled
def package_release(root: Path, output: Path, version='v3.0.0'):
    root, output = _path(root), _path(output)
    _output_preflight(root, output)
    if not isinstance(version, str) or not re.fullmatch(r'v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version):
        raise Refusal('Version must be a stable vMAJOR.MINOR.PATCH tag.')
    files = {'install.sh': _read(root / 'install.sh'), 'README.md': PUBLIC_README,
             'LICENSE': _read(root / 'source/LICENSE')}
    for name in MODULES:
        relative = 'installer/' + name + '.py'
        files[relative] = _read(root / relative)
    for name in PUBLIC_TESTS:
        relative = 'tests/' + name
        files[relative] = _read(root / relative)
    for name in ('INSTALL.md', 'AUDIO-CALLOUTS.md', 'SETTINGS.md'):
        relative = 'docs/' + name
        if name == 'INSTALL.md' or os.path.lexists(root / relative):
            files[relative] = _read(root / relative)
    legacy = {}
    for sub in ('payload', 'assets', 'build', 'source'):
        legacy.update({sub + '/' + name: data for name, data in _tree(root / 'installer' / sub).items()})
    legacy['fresh-files.json'] = _read(root / 'installer/fresh-files.json')
    _verify_release(legacy)
    files.update({'installer/' + name: data for name, data in legacy.items()})
    frozen_releases = {}
    for release in ('controller-audio-v2', 'settings-v3'):
        relative = 'installer/releases/' + release
        frozen = _tree(root / relative)
        _verify_release(frozen)
        frozen_releases[release] = frozen
        files.update({relative + '/' + name: data for name, data in frozen.items()})
        if release == 'settings-v3':
            files.update({name: data for name, data in frozen.items() if name.startswith('source/')})
    _verify_settings(frozen_releases['controller-audio-v2'], frozen_releases['settings-v3'])
    files['release.json'] = _json({'schemaVersion': 1, 'repository': 'JackDelfin/bingo',
                                  'version': version,
                                  'files': {name: _sha(data) for name, data in sorted(files.items())}})
    _check_limits(files)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9,
                         allowZip64=False) as zipped:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo('bingo/' + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if name.endswith('.sh') else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            zipped.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    archive_data = buffer.getvalue()
    if len(archive_data) > MAX_ARCHIVE_BYTES:
        raise Refusal('Public archive exceeds the ZIP-byte limit.')
    checksum_data = (_sha(archive_data) + '  bingo-release.zip\n').encode('ascii')
    _output_preflight(root, output)
    output.mkdir(mode=0o700, exist_ok=True)
    _output_preflight(root, output)
    archive, checksum = output / 'bingo-release.zip', output / 'bingo-release.zip.sha256'
    created = []
    try:
        for path, data in ((archive, archive_data), (checksum, checksum_data)):
            created.append((path, _write_new(path, data)))
    except BaseException:
        for path, identity in reversed(created):
            _unlink_owned(path, identity)
        raise
    return {'archive': archive, 'checksum': checksum, 'fileCount': len(files), 'sha256': _sha(archive_data)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--freeze', nargs=2, metavar=('ROOT', 'DESTINATION'), type=Path,
                        help='freeze reviewed frontend inputs into a new settings-v3 release')
    action.add_argument('--package', nargs=2, metavar=('ROOT', 'OUTPUT'), type=Path,
                        help='create the clean public archive in an exclusive private output directory')
    parser.add_argument('--version', default='v3.0.0', help='stable release tag for --package')
    arguments = parser.parse_args(argv)
    try:
        result = (freeze_release(*arguments.freeze) if arguments.freeze else
                  package_release(*arguments.package, version=arguments.version))
    except Refusal as error:
        print('Release packaging refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
