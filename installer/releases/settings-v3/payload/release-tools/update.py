#!/usr/bin/env python3
"""Offline, app-only transaction for the pinned rootless venue installation."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

LABEL = 'org.reactbingo.venue'
NETWORK = 'venue-bingo'
VOLUME = 'venue-bingo-data'
DB = 'venue-bingo-db'
APPS = ('venue-bingo-frontend', 'venue-bingo-backend')
IMAGES = {'backend': 'localhost/reactbingo-backend:venue', 'frontend': 'localhost/reactbingo-frontend:venue', 'db': 'docker.io/library/mongo:8.0'}
FILES = ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv')
LAUNCHER_SHA256 = 'aa510ee4de96bf13123c6d59ed99f9bc8ba3e410fed7c689abdbf8be635bf0d2'
# Exact repaired venue read-back variant: the staging bytes plus one final LF.
SUPPORTED_LAUNCHER_SHA256 = {LAUNCHER_SHA256, '57fb6cf504447205e3114fdf6d3ddc6f64da0092ab7b445b7cc907dead385340'}
DOCKERFILES = {
    'backend': 'FROM localhost/reactbingo-backend:venue\nCOPY backend-dist/ /app/dist/\n',
    'frontend': 'FROM localhost/reactbingo-frontend:venue\nCOPY frontend-dist/ /usr/share/nginx/html/\n',
}
RUNTIME_TOOLS = {'install-update.sh', 'update.py', 'Dockerfile.backend', 'Dockerfile.frontend', 'README.md'}
OPTIONAL_TOOLS = {'tests/test_update.py', 'tests/fake_podman.py', 'tests/bingo.sh'}
ROOT_DOCUMENTS = {'LICENSE', 'README.md', 'RELEASE.md'}


class Refusal(Exception):
    pass


def require(condition, message):
    # Do not replace with assert: these checks must survive PYTHONOPTIMIZE=1.
    if not condition:
        raise Refusal(message)


def command(args):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        raise Refusal('A required local command could not run.') from None
    require(result.returncode == 0, 'Local command failed: ' + Path(args[0]).name + ' ' + args[1] + '.')
    return result.stdout


def podman(*args):
    return command(['podman', *map(str, args)])


def inspect(kind, name):
    args = ('inspect', name) if kind == 'container' else (kind, 'inspect', name)
    try:
        value = json.loads(podman(*args))
        require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict), 'Unexpected inspection result.')
        return value[0]
    except (ValueError, TypeError):
        raise Refusal('Invalid runtime inspection result.') from None


def canonical_image_id(ident):
    # Podman 4.9 image inspection returns bare hex; other inspections may prefix it.
    require(isinstance(ident, str) and re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', ident), 'Invalid local image ID.')
    return 'sha256:' + ident.removeprefix('sha256:')


def image_id(name):
    return canonical_image_id(inspect('image', name).get('Id'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def verify_release(release):
    actual = set()
    for directory, dirs, files in os.walk(release, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            relative = path.relative_to(release).as_posix()
            mode = path.lstat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Payload contains a link or special file.')
            require(relative.split('/')[0] in {'backend-dist', 'frontend-dist', 'release-tools', 'files.sha256'} | ROOT_DOCUMENTS, 'Unexpected root payload entry.')
            if relative.startswith('release-tools/'):
                require(relative == 'release-tools/tests' or relative.removeprefix('release-tools/') in RUNTIME_TOOLS | OPTIONAL_TOOLS, 'Unexpected release tool entry.')
            if stat.S_ISREG(mode):
                require(path.stat().st_nlink == 1, 'Payload hard links are not supported.')
                actual.add(relative)
    required = {'backend-dist/server.js', 'frontend-dist/index.html', 'files.sha256'} | {
        'release-tools/' + name for name in RUNTIME_TOOLS
    }
    require(required <= actual, 'Required update payload is missing.')
    for name in actual:
        require(name in {'files.sha256'} | ROOT_DOCUMENTS or name.startswith(('backend-dist/', 'frontend-dist/', 'release-tools/')), 'Unexpected root payload file.')
    listed = {}
    for line in (release / 'files.sha256').read_text(encoding='utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64}) [ *]([^\x00-\x1f\x7f]+)', line)
        if match is None:
            raise Refusal('Invalid checksum manifest line.')
        checksum, name = match.groups()
        require(not name.startswith('/') and '\\' not in name and all(part not in ('', '.', '..') for part in name.split('/')), 'Unsafe checksum path.')
        require(name != 'files.sha256' and name not in listed, 'Duplicate or self-referencing checksum entry.')
        listed[name] = checksum
    require(set(listed) == actual - {'files.sha256'}, 'Checksum manifest must cover the exact payload file set.')
    for name, expected in listed.items():
        require(digest(release / name) == expected, 'Payload checksum mismatch.')
    for short, content in DOCKERFILES.items():
        require((release / 'release-tools' / ('Dockerfile.' + short)).read_bytes() == content.encode(), 'Only the supplied COPY-only offline Dockerfiles are supported.')


def preflight(base, allow_missing_apps=False, metadata=None):
    require(base.is_dir(), 'Supply an existing bingo installation directory.')
    require(os.geteuid() != 0, 'Run as the existing rootless user, not root or sudo.')
    for executable in ('podman', 'curl', 'bash'):
        require(shutil.which(executable) is not None, 'Missing required command: ' + executable + '.')
    info = json.loads(podman('info', '--format', 'json'))
    require(info.get('host', {}).get('security', {}).get('rootless') is True, 'Podman must be rootless.')
    for name in FILES:
        require((base / name).is_file() and not (base / name).is_symlink(), 'Missing or linked installation configuration file.')
        require((base / name).resolve().is_relative_to(base), 'Configuration must remain inside this installation.')
    require(digest(base / 'bingo.sh') in SUPPORTED_LAUNCHER_SHA256, 'The existing launcher does not match a supported pinned venue launcher.')
    images = {short: image_id(tag) for short, tag in IMAGES.items()}
    network = inspect('network', NETWORK)
    volume = inspect('volume', VOLUME)
    containers = {}
    for name in (DB, *APPS):
        if allow_missing_apps and name != DB:
            result = subprocess.run(['podman', 'container', 'exists', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            require(result.returncode in (0, 1), 'Cannot inspect app existence.')
            if result.returncode == 1:
                continue
        containers[name] = inspect('container', name)
    # Netavark/Podman 4.9 network JSON uses lowercase `labels`; volumes do not.
    network_labels = network.get('labels', network.get('Labels', {}))
    require(network_labels.get(LABEL) == '1', 'The existing network is not owned by this venue.')
    require(volume.get('Labels', {}).get(LABEL) == '1', 'The existing volume is not owned by this venue.')
    for item in containers.values():
        require(item.get('Config', {}).get('Labels', {}).get(LABEL) == '1', 'An existing container is not owned by this venue.')
        require(set(item.get('NetworkSettings', {}).get('Networks', {})) == {NETWORK}, 'An existing container has unexpected networking.')
    require(containers[DB].get('State', {}).get('Running') is True, 'The existing database must already be running.')
    require(canonical_image_id(containers[DB].get('Image')) == images['db'], 'Database image does not match the existing Mongo image.')
    require('GLIBC_TUNABLES=glibc.pthread.rseq=1' in containers[DB].get('Config', {}).get('Env', []), 'The pinned Mongo kernel workaround is missing; no database changes will be made.')
    require(any(m.get('Type') == 'volume' and m.get('Name') == VOLUME and m.get('Destination') == '/data/db' for m in containers[DB].get('Mounts', [])), 'Database is not using the expected existing data volume.')
    require(not containers[DB].get('HostConfig', {}).get('PortBindings'), 'Database must not publish a host port.')
    for short, source, destination in (('backend', 'reactbingo/backend/cards.csv', '/app/cards.csv'), ('frontend', 'reactbingo/nginx.conf', '/etc/nginx/nginx.conf')):
        item = containers.get('venue-bingo-' + short)
        if item is None:
            continue
        mounts = item.get('Mounts', [])
        require(isinstance(mounts, list) and len(mounts) == 1 and isinstance(mounts[0], dict)
                and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(base / source)
                and mounts[0].get('Destination') == destination
                and not item.get('HostConfig', {}).get('Tmpfs'), 'An app has an unsupported mount set.')
        ports = item.get('HostConfig', {}).get('PortBindings') or {}
        expected_ports = {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]} if short == 'frontend' else {}
        require(ports == expected_ports, 'Only the frontend may publish host port 3000.')
        if metadata is None:
            require(canonical_image_id(item.get('Image')) == images[short], 'App container and existing venue image tag disagree.')
    if metadata:
        require(containers[DB].get('Id') == metadata['db_id'], 'The original database container has changed; refusing rollback.')
        require({name: digest(base / name) for name in FILES} == metadata['config_sha256'], 'Installation configuration changed; refusing rollback.')
        for ident in metadata['old_images'].values():
            require(image_id(ident) == ident, 'An old image is unavailable.')
    return {'images': images, 'containers': containers, 'network': network, 'volume': volume}


def stop_remove_apps():
    for name in APPS:
        result = subprocess.run(['podman', 'container', 'exists', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(result.returncode in (0, 1), 'Cannot inspect app existence.')
        if result.returncode == 0:
            podman('stop', name)
            podman('rm', name)


def api_ready():
    # Do not trust curl's exit status alone: redirects and HTML fallbacks can exit 0.
    # Disable curlrc/proxies, do not follow redirects, and bound every local probe.
    try:
        result = subprocess.run([
            'curl', '--disable', '--fail', '--silent', '--noproxy', '*',
            '--proto', '=http', '--max-redirs', '0', '--max-time', '3',
            '--max-filesize', '1048576', '--write-out', '\n%{http_code}',
            'http://127.0.0.1:3000/api/list',
        ], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4)
        if result.returncode != 0 or len(result.stdout) > 1048576 + 4:
            return False
        body, separator, status_code = result.stdout.rstrip(b'\r\n').rpartition(b'\n')
        if not separator or status_code != b'200':
            return False
        value = json.loads(body.decode('utf-8'))
        # backend/src/server.ts GET /list: { games: games.map(g => g.get('gameId')) }.
        return (isinstance(value, dict) and set(value) == {'games'}
                and isinstance(value['games'], list)
                and all(isinstance(game, str) for game in value['games']))
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        return False


def verify_apps(base, expected, db_id):
    current = preflight(base)
    require(current['containers'][DB]['Id'] == db_id, 'Database identity changed.')
    for short in ('backend', 'frontend'):
        item = current['containers']['venue-bingo-' + short]
        require(canonical_image_id(item.get('Image')) == expected[short] and item.get('State', {}).get('Running') is True, 'App did not start with the expected image.')
        require(current['images'][short] == expected[short], 'App image tag verification failed.')
    require(api_ready(), 'App API readiness check failed.')


def launcher_start(base):
    # The pinned start command only starts an already-running DB, never stops or recreates it.
    command(['bash', str(base / 'bingo.sh'), 'start'])


def recreate_apps_only(base):
    common = ['run', '-d', '--pull=never', '--label', LABEL + '=1', '--network', NETWORK, '--restart', 'unless-stopped']
    podman(*common, '--name', 'venue-bingo-backend', '--network-alias', 'backend', '-v', str(base / 'reactbingo/backend/cards.csv') + ':/app/cards.csv:ro,Z', IMAGES['backend'])
    podman(*common, '--name', 'venue-bingo-frontend', '-p', '0.0.0.0:3000:80', '-v', str(base / 'reactbingo/nginx.conf') + ':/etc/nginx/nginx.conf:ro,Z', IMAGES['frontend'])
    for _ in range(60):
        if api_ready():
            return
        import time
        time.sleep(1)
    raise Refusal('App readiness check timed out.')


def rollback(base, metadata, use_launcher):
    # Preflight the entire resource set again before changing even the first tag.
    preflight(base, allow_missing_apps=True, metadata=metadata)
    for short in ('backend', 'frontend'):
        podman('tag', metadata['old_images'][short], IMAGES[short])
        require(image_id(IMAGES[short]) == metadata['old_images'][short], 'Old image tag did not restore.')
    stop_remove_apps()
    if use_launcher:
        launcher_start(base)
    else:
        recreate_apps_only(base)
    verify_apps(base, metadata['old_images'], metadata['db_id'])


def interrupted(_signum, _frame):
    raise Refusal('Interrupted.')


@contextmanager
def installation_lock(base):
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def install(base, release):
    # Read-only preflight precedes staging, image builds, exports, tags and stops.
    verify_release(release)
    initial = preflight(base)
    backup = Path(tempfile.mkdtemp(prefix='.bingo-update-', dir=base))
    os.chmod(backup, 0o700)
    print('Private update backup: ' + str(backup), flush=True)
    stage = backup / 'release'
    shutil.copytree(release, stage)
    verify_release(stage)
    token = backup.name.removeprefix('.bingo-update-')
    metadata = {
        'version': 1, 'installation': str(base), 'db_id': initial['containers'][DB]['Id'],
        'old_images': {short: initial['images'][short] for short in ('backend', 'frontend')},
        'old_tags': {short: IMAGES[short] for short in ('backend', 'frontend')},
        'config_sha256': {name: digest(base / name) for name in FILES},
        'backup_tags': {short: 'localhost/reactbingo-' + short + ':backup-' + token for short in ('backend', 'frontend')},
    }
    (backup / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    shutil.copyfile(Path(__file__), backup / 'update.py')
    (backup / 'rollback.sh').write_text('#!/usr/bin/env bash\nset -Eeuo pipefail\nHERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)\nexec python3 "$HERE/update.py" --rollback "$HERE"\n')
    (backup / 'rollback.sh').chmod(0o700)
    podman('volume', 'export', '--output', backup / 'database-volume.tar', VOLUME)
    require((backup / 'database-volume.tar').is_file() and (backup / 'database-volume.tar').stat().st_size > 0, 'Volume export is empty or missing.')
    for short in ('backend', 'frontend'):
        podman('tag', metadata['old_images'][short], metadata['backup_tags'][short])
        require(image_id(metadata['backup_tags'][short]) == metadata['old_images'][short], 'Old-image backup tag verification failed.')
    new_images = {}
    for short in ('backend', 'frontend'):
        tag = 'localhost/reactbingo-' + short + ':update-' + token
        podman('build', '--pull=never', '--network=none', '-f', stage / 'release-tools' / ('Dockerfile.' + short), '-t', tag, stage)
        new_images[short] = image_id(tag)
    # Revalidate resources after the long-running backup/build work.
    preflight(base, metadata=metadata)
    try:
        for short in ('backend', 'frontend'):
            podman('tag', new_images[short], IMAGES[short])
            require(image_id(IMAGES[short]) == new_images[short], 'New image tag verification failed.')
        stop_remove_apps()
        launcher_start(base)
        verify_apps(base, new_images, metadata['db_id'])
        (backup / 'update-complete').write_text('App image IDs and readiness verified.\n')
    except (Exception, KeyboardInterrupt):
        # Roll back any failure after production tags may have changed, including
        # unexpected local I/O errors. A second interrupt must not kill rollback.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            rollback(base, metadata, use_launcher=True)
            print('Update failed; old app images and app readiness restored. Update was NOT successful.', file=sys.stderr)
        except Exception:
            print('Update failed; automatic rollback also failed. Keep the private backup and follow the rollback instructions.', file=sys.stderr)
        return 1
    print('Update completed; both apps verified. Reload both browsers. Keep the private backup.')
    return 0


def main():
    os.umask(0o077)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if len(sys.argv) == 3 and sys.argv[1] == '--rollback':
            backup = Path(sys.argv[2]).resolve(strict=True)
            metadata = json.loads((backup / 'metadata.json').read_text())
            base = Path(metadata['installation']).resolve(strict=True)
            with installation_lock(base):
                rollback(base, metadata, use_launcher=False)
            print('Rollback completed; old apps verified. Database was not stopped or recreated.')
            return 0
        require(len(sys.argv) == 2 and not sys.argv[1].startswith('-'), 'Usage: install-update.sh /absolute/path/to/existing/bingo')
        raw = Path(sys.argv[1]).expanduser()
        require(raw.is_absolute(), 'Supply an explicit absolute existing installation path.')
        base = raw.resolve(strict=True)
        release = Path(__file__).resolve().parent.parent
        require(base != release and not release.is_relative_to(base), 'Extract the update outside the existing installation.')
        with installation_lock(base):
            return install(base, release)
    except Refusal as error:
        # All Refusal messages are controlled text; never include captured command output.
        print('Update/rollback refused or failed: ' + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Do not echo runtime output, database contents, environment or capability URLs.
        print('Update/rollback failed: local file/runtime data is invalid or unavailable; no success is claimed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
