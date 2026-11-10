#!/usr/bin/env python3
"""Bingo user app-menu registration and durable rootless startup (stdlib only)."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import stat
import re
import uuid
import subprocess
import fcntl
import time
import threading
from contextlib import contextmanager

ICON = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect width="128" height="128" rx="26" fill="#153950"/><circle cx="64" cy="64" r="46" fill="#f6c453"/><text x="64" y="85" text-anchor="middle" font-family="sans-serif" font-size="62" font-weight="bold" fill="#153950">B</text></svg>\n'''


class Refusal(Exception):
    pass


def require(value, message):
    if not value:
        raise Refusal(message)


def safe_path(raw):
    path = Path(raw)
    require(path.is_absolute() and '..' not in path.parts, 'Unsafe desktop/state path.')
    private_barrier = False
    for item in [*reversed(path.parents), path]:
        require(not item.is_symlink(), 'Linked desktop/state path is forbidden.')
        if item.exists():
            st = item.lstat()
            require(st.st_uid in (0, os.geteuid()) and (private_barrier or not st.st_mode & 0o022), 'Unsafe writable desktop/state parent.')
            if stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077:
                private_barrier = True
    return path


def owned_bytes(path, private=True):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.geteuid()
                and (st.st_mode & 0o777 == 0o600 if private else not st.st_mode & 0o022), 'Unsafe owned regular file.')
        return stream.read()


def owned_json(path):
    value = json.loads(owned_bytes(path))
    require(isinstance(value, dict), 'Invalid private state.')
    return value


def atomic(path, data):
    safe_path(path)
    existed = path.exists()
    if existed:
        owned_bytes(path)
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if existed:
            owned_bytes(path)
            os.replace(temporary, path)
        else:
            # Exclusive publication: a late collision is never adopted.
            os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def desktop_paths(target, home):
    # Refuse ambiguous freedesktop escaping rather than rely on shell parsing.
    # Quoted Exec arguments support spaces; percent field codes in quotes do not.
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    for path in (target, home, data, Path(sys.executable).resolve()):
        require(all(32 <= ord(c) < 127 and c not in '%\\\"\'`$' for c in str(path)),
                'Unsupported desktop path characters; use --no-desktop for a headless install.')
        safe_path(path)
    return data


def managed(target, home):
    metadata = target / '.venue-install'
    value = owned_json(metadata / 'desktop.json')
    require(value.get('version') in (1, 2) and value.get('status') == 'installed' and value.get('uid') == os.geteuid()
            and value.get('target') == str(target) and value.get('home') == str(home), 'Invalid desktop provenance.')
    entry = Path(value['entry'])
    require(entry.name == 'bingo.desktop' and entry.parent.name == 'applications', 'Invalid desktop entry identity.')
    expected = {str(metadata / 'bingo-desktop.py'), str(metadata / 'bingo.svg'), str(entry)}
    if value['version'] == 2:
        expected.update(str(metadata / name) for name in ('bingo-management.py', 'bingo-release-update.py'))
    require(isinstance(value.get('files'), dict) and set(value['files']) == expected, 'Invalid managed artifact set.')
    for name, digest in value['files'].items():
        require(hashlib.sha256(owned_bytes(Path(name))).hexdigest() == digest, 'Managed desktop artifact changed; refusing repair.')
    return value


def register(target, home, preserve_runtime=False):
    desktop_paths(target, home)
    metadata = target / '.venue-install'
    private_directory(target)
    private_directory(metadata)
    helper = metadata / 'bingo-desktop.py'
    icon = metadata / 'bingo.svg'
    entry = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share'))) / 'applications/bingo.desktop'
    manifest = metadata / 'desktop.json'
    old = managed(target, home) if manifest.exists() or manifest.is_symlink() else None
    if old is not None:
        entry = Path(old['entry'])
    text = ('[Desktop Entry]\nType=Application\nName=Bingo\nComment=Start Bingo and open the local board\n'
            'Exec="' + str(Path(sys.executable).resolve()) + '" -B "' + str(helper) + '" --launch\n'
            'Icon=' + str(icon) + '\nTerminal=true\nCategories=Game;\nStartupNotify=false\n')
    files = {helper: Path(__file__).read_bytes(), icon: ICON, entry: text.encode(),
             metadata / 'bingo-management.py': Path(__file__).with_name('management.py').read_bytes(),
             metadata / 'bingo-release-update.py': Path(__file__).with_name('release_update.py').read_bytes()}
    for path in files:
        safe_path(path)
    if old is not None:
        require(set(old['files']) <= {str(path) for path in files}, 'Invalid managed artifact set.')
    for path in files:
        if old is None or str(path) not in old['files']:
            require(not path.exists() and not path.is_symlink(), 'Foreign desktop artifact collision; nothing will be adopted.')
        else:
            require(hashlib.sha256(owned_bytes(path)).hexdigest() == old['files'].get(str(path)), 'Managed desktop artifact changed; refusing repair.')
    containers = runtime_preflight(target, old if preserve_runtime else None, refresh_from_receipt=preserve_runtime)
    entry.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path, data in files.items():
        atomic(path, data)
    value = {'version': 2, 'uid': os.geteuid(), 'target': str(target), 'home': str(home), 'status': 'installed', 'entry': str(entry),
             'container_ids': {name: item['Id'] for name, item in containers.items()}, 'db_mounts': containers[DB]['Mounts'],
             'files': {str(path): hashlib.sha256(data).hexdigest() for path, data in files.items()}}
    atomic(metadata / 'desktop.json', (json.dumps(value, indent=2) + '\n').encode())
    print('Bingo app-menu entry registered.')
    return 0


def command(arguments, timeout=10):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Refusal('A required local command is unavailable or timed out.') from None
    require(result.returncode == 0, 'Local command failed: ' + arguments[0] + '.')
    return result.stdout


LABEL = 'org.reactbingo.venue'
NETWORK = 'venue-bingo'
VOLUME = 'venue-bingo-data'
DB = 'venue-bingo-db'
CONTAINERS = (DB, 'venue-bingo-backend', 'venue-bingo-frontend')
IMAGES = {'db': 'docker.io/library/mongo:8.0', 'backend': 'localhost/reactbingo-backend:venue', 'frontend': 'localhost/reactbingo-frontend:venue'}
FILES = ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv')
LAUNCHERS = {'aa510ee4de96bf13123c6d59ed99f9bc8ba3e410fed7c689abdbf8be635bf0d2', '57fb6cf504447205e3114fdf6d3ddc6f64da0092ab7b445b7cc907dead385340'}
# Recognize only the shipped release authorities. Normal registration
# refreshes helper/container provenance after a completed app-only update.
PAYLOAD_HASHES = {'198ba57eaf14633986011dbe03d6876c743d198312693295a843f55fe0b50aa8',
                  'fd50b3c289286e0bc596dd63d33838743ce105017aaa1f61f2ae0346c6e087cd',
                  'e90e4c48a91faf12ee41c46021668ff48d131a576edde277a33ebf44354c2c70'}


def image_id(value):
    require(isinstance(value, str) and re.fullmatch(r'(?:sha256:)?[0-9a-f]{64}', value), 'Invalid image identity.')
    return 'sha256:' + value.removeprefix('sha256:')


def inspect(kind, name):
    args = ['inspect', name] if kind == 'container' else [kind, 'inspect', name]
    value = json.loads(command(['podman', *args]))
    require(isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict), 'Invalid runtime inspection.')
    return value[0]


def storage(value, recorded=False):
    fields = {'graphRoot', 'runRoot', 'graphDriverName'}
    require(isinstance(value, dict) and fields <= value.keys() and (not recorded or set(value) == fields), 'Invalid runtime storage schema.')
    for field in fields:
        part = value[field]
        require(isinstance(part, str) and part and part == part.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in part), 'Invalid storage identity.')
        require(Path(part).is_absolute() if field != 'graphDriverName' else bool(re.fullmatch(r'[\w.-]+', part, re.ASCII)), 'Invalid storage identity.')
    return {field: value[field] for field in fields}


def runtime_preflight(target, pins=None, refresh_from_receipt=False):
    """Inspect the entire existing resource set, allowing a stopped database.

    Independent of the frozen updater: its preflight requires a running DB.
    This validator never repairs, creates, tags, pulls or invokes a launcher.
    """
    require(sys.platform == 'linux' and os.geteuid() != 0, 'Use the existing ordinary Linux rootless user, never sudo.')
    record = owned_json(target / '.venue-install/receipt.json')
    require(record.get('version') == 1 and record.get('status') == 'installed' and record.get('uid') == os.geteuid()
            and record.get('target') == str(target) and record.get('payload_sha256') in PAYLOAD_HASHES, 'Installation receipt/owner mismatch.')
    config = {name: hashlib.sha256(owned_bytes(target / name, private=False)).hexdigest() for name in FILES}
    require(config == record.get('config_sha256'), 'Installed configuration changed.')
    require(config['bingo.sh'] in LAUNCHERS, 'Unknown launcher pin; launcher will not be run.')
    info = json.loads(command(['podman', 'info', '--format', 'json']))
    require(info.get('host', {}).get('security', {}).get('rootless') is True, 'Podman must be rootless.')
    require(storage(info.get('store')) == storage(record.get('store'), recorded=True), 'Runtime store changed.')
    images = {short: image_id(inspect('image', tag).get('Id')) for short, tag in IMAGES.items()}
    require(images == record.get('images'), 'Installed image identities changed.')
    network, volume = inspect('network', NETWORK), inspect('volume', VOLUME)
    require(network == record.get('network') and volume == record.get('volume'), 'Recorded network/volume changed.')
    require(network.get('labels', network.get('Labels', {})).get(LABEL) == '1' and volume.get('Labels', {}).get(LABEL) == '1', 'Foreign network/volume.')
    containers = {name: inspect('container', name) for name in CONTAINERS}
    for name, item in containers.items():
        require(isinstance(item.get('Id'), str) and item['Id'], 'Invalid container identity.')
        require(item.get('Config', {}).get('Labels', {}).get(LABEL) == '1', 'Foreign container.')
        require(set(item.get('NetworkSettings', {}).get('Networks', {})) == {NETWORK}, 'Unexpected container networking.')
        require(isinstance(item.get('State', {}).get('Running'), bool), 'Invalid container state.')
        require(not item.get('HostConfig', {}).get('Tmpfs'), 'Unexpected temporary mounts.')
        short = name.removeprefix('venue-bingo-')
        require(image_id(item.get('Image')) == images[short], 'Unexpected container image.')
        expected_ports = {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]} if short == 'frontend' else {}
        ports = item.get('HostConfig', {}).get('PortBindings')
        require(ports is None or isinstance(ports, dict), 'Invalid host PortBindings schema.')
        ports = {} if ports is None else ports
        if short == 'frontend' and ports == {'80/tcp': [{'HostIp': '', 'HostPort': '3000'}]}:
            ports = expected_ports  # Podman 4.9's equivalent wildcard address.
        require(ports == expected_ports, 'Unexpected host ports.')
        actual = item.get('NetworkSettings', {}).get('Ports')
        require(actual is None or isinstance(actual, dict), 'Invalid runtime Ports schema.')
        # Exposed-but-unpublished image ports are null; do not mistake them
        # for bindings or ignore any non-null actual publication.
        actual = {} if actual is None else {key: binding for key, binding in actual.items() if binding is not None}
        if short == 'frontend' and actual == {'80/tcp': [{'HostIp': '', 'HostPort': '3000'}]}:
            actual = expected_ports
        require(actual == expected_ports, 'Unexpected runtime host publications.')
        mounts = item.get('Mounts')
        require(isinstance(mounts, list) and all(isinstance(m, dict) for m in mounts), 'Invalid mount set.')
        if short == 'db':
            require(item['Id'] == record.get('db_id'), 'Original database was replaced.')
            require('GLIBC_TUNABLES=glibc.pthread.rseq=1' in item.get('Config', {}).get('Env', []), 'Pinned Mongo workaround is missing.')
            data = [m for m in mounts if m.get('Destination') == '/data/db']
            require(len(data) == 1 and data[0].get('Type') == 'volume' and data[0].get('Name') == VOLUME and data[0].get('RW') is True, 'Unexpected database data volume.')
            # The stock Mongo image may also declare its anonymous configdb volume.
            extra = [m for m in mounts if m.get('Destination') != '/data/db']
            require(len(extra) <= 1 and all(m.get('Type') == 'volume' and m.get('Destination') == '/data/configdb'
                    and m.get('Name') and m.get('Name') != VOLUME and m.get('RW') is True for m in extra), 'Unexpected database mounts.')
        else:
            source, dest = ('reactbingo/backend/cards.csv', '/app/cards.csv') if short == 'backend' else ('reactbingo/nginx.conf', '/etc/nginx/nginx.conf')
            require(len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(target / source)
                    and mounts[0].get('Destination') == dest and mounts[0].get('RW') is False, 'Unexpected app mount (must be exact read-only bind).')
    identities = {name: item['Id'] for name, item in containers.items()}
    recorded_ids = record.get('container_ids')
    if recorded_ids is not None:
        require(recorded_ids == identities, 'An installation-receipt container was replaced.')
    if pins is not None:
        # A completed ordinary headless update can supply new, explicitly
        # receipted app IDs. Desktop-only may refresh those pins, never adopt
        # arbitrary replacements. Legacy receipts fall back to desktop pins.
        require((pins.get('container_ids') == identities or refresh_from_receipt and recorded_ids == identities)
                and pins.get('db_mounts') == containers[DB]['Mounts'], 'An installed container was replaced or changed.')
    return containers


def private_directory(path):
    safe_path(path)
    st = path.lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077, 'Installation/state directory must remain private and owned.')


@contextmanager
def locks(target, home):
    state = home / '.local/state/venue-bingo'
    safe_path(home)
    home_stat = home.lstat()
    require(stat.S_ISDIR(home_stat.st_mode) and home_stat.st_uid == os.geteuid() and not home_stat.st_mode & 0o022, 'Unsafe HOME ownership/permissions.')
    for path in (target, target / '.venue-install', state):
        private_directory(path)
    descriptors = []
    try:
        # Match bootstrap order: HOME namespace, then installation-directory flock.
        for path, directory in ((state / 'namespace.lock', False), (target, True)):
            safe_path(path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK))
            descriptors.append(fd)
            st = os.fstat(fd)
            require(st.st_uid == os.geteuid() and not st.st_mode & 0o077
                    and (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode) and st.st_nlink == 1), 'Unsafe startup lock.')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal('Bingo is busy with another startup/install/update; retry shortly.') from None
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def state_preflight(target, home):
    state = home / '.local/state/venue-bingo'
    pointer = owned_json(state / 'installation.json')
    require(pointer.get('version') == 1 and pointer.get('uid') == os.geteuid() and pointer.get('target') == str(target), 'Installation pointer mismatch.')
    journal = owned_json(state / 'operation.json')
    require(journal.get('version') == 1 and journal.get('status') == 'installed' and journal.get('uid') == os.geteuid()
            and journal.get('target') == str(target) and journal.get('payload_sha256') in PAYLOAD_HASHES, 'Failed/unfinished installation journal; manual recovery is required.')
    for backup in target.glob('.bingo-update-*'):
        private_directory(backup)
        # A failed frozen update/rollback is never silently treated as a healthy
        # desktop install. Keep its backups and require explicit investigation.
        owned_bytes(backup / 'update-complete')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key.')
        result[key] = value
    return result


def ready():
    deadline = time.monotonic() + 20
    args = ['curl', '--disable', '--silent', '--show-error', '--noproxy', '*', '--proto', '=http', '--max-redirs', '0',
            '--connect-timeout', '1', '--max-time', '2', '--max-filesize', '1048576', '--write-out', '\n%{http_code}',
            'http://127.0.0.1:3000/api/list']
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(args, text=True, capture_output=True, timeout=min(3, max(0.01, deadline - time.monotonic())))
        except OSError:
            raise Refusal('The local HTTP readiness command is unavailable.') from None
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            output = result.stdout.strip()
            require(len(output) <= 1048576, 'Readiness response exceeded the limit.')
            body, separator, status = output.rpartition('\n')
            if separator and status in {'500', '502', '503', '504'}:
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
                continue
            require(separator and status == '200', 'Local Bingo API did not return HTTP 200 (redirects are refused).')
            try:
                data = json.loads(body, object_pairs_hook=unique_object)
            except (ValueError, Refusal):
                raise Refusal('Local Bingo API did not return the expected JSON.') from None
            require(isinstance(data, dict) and set(data) == {'games'} and isinstance(data['games'], list)
                    and all(isinstance(item, str) for item in data['games']), 'Local Bingo API returned an unexpected JSON shape.')
            return
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise Refusal('Bingo did not become ready within 20 seconds; no browser was opened.')


def management_url(target, home):
    """Load only the independently pinned installed helper, never checkout code."""
    pins = managed(target, home)
    if pins['version'] == 1:
        return 'http://localhost:3000'
    path = target / '.venue-install/bingo-management.py'
    spec = importlib.util.spec_from_file_location('bingo_installed_management', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    url = module.ensure_started(target, home)
    require(isinstance(url, str) and re.fullmatch(r'http://localhost:3000#bingo-management=8766\.[0-9a-f]{64}', url),
            'Invalid local management launch response.')
    return url


def launch():
    target = Path(__file__).absolute().parent.parent
    home = safe_path(os.environ['HOME'])
    with locks(target, home):
        state_preflight(target, home)
        pins = managed(target, home)
        current = runtime_preflight(target, pins)
        for name in CONTAINERS:
            if current[name]['State']['Running'] is False:
                command(['podman', 'start', name])
                current = runtime_preflight(target, pins)
                require(current[name]['State']['Running'] is True, 'Container did not remain running after start.')
        current = runtime_preflight(target, pins)
        require(all(item['State']['Running'] is True for item in current.values()), 'The installed runtime set is not running.')
        ready()
        current = runtime_preflight(target, pins)
        require(all(item['State']['Running'] is True for item in current.values()), 'Runtime changed during readiness.')
    # Management is optional for ordinary/offline play and never holds the
    # installation locks. Capability fragments stay out of server HTTP logs.
    try:
        url = management_url(target, home)
    except Exception:
        url = 'http://localhost:3000'
        print('Bingo is ready. Local updates are unavailable; reopen the Bingo app to retry.', file=sys.stderr)
    # No maintenance locks or terminal/captured pipes may follow the browser.
    try:
        opener = subprocess.Popen(['xdg-open', url], start_new_session=True,
                                  close_fds=True, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise Refusal('Bingo is ready, but the default browser could not be opened. Open http://localhost:3000 manually.') from None
    try:
        status = opener.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # Reap if it exits before this short-lived helper; otherwise the
        # independent session is safely orphaned when the helper exits.
        threading.Thread(target=opener.wait, daemon=True).start()
    else:
        require(status == 0, 'Bingo is ready, but the default browser could not be opened. Open http://localhost:3000 manually.')
    print('Browser launch requested; if no window appears open http://localhost:3000 manually')
    return 0


def main():
    try:
        require(sys.argv[1:] == ['--launch'], 'Use --launch from the Bingo app entry.')
        return launch()
    except Exception as error:
        message = str(error) if isinstance(error, Refusal) else 'Unsafe or missing installation/runtime state.'
        print('Bingo startup refused: ' + message, file=sys.stderr)
        if sys.stdin.isatty() and sys.stderr.isatty():
            try:
                input('Press Enter to close this window. ')
            except (EOFError, KeyboardInterrupt):
                pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
