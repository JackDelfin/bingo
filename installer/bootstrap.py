#!/usr/bin/env python3
"""Single-command rootless venue installer; Python standard library only."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import os
import json
import shutil
import subprocess
from pathlib import Path
import stat
import sys
import uuid

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
# One pinned default for fresh builds and the separately loaded update child.
# Historical trees stay immutable; never fall back to them on verification failure.
RELEASE = HERE / 'releases/settings-v3'
PAYLOAD_HASH = 'e90e4c48a91faf12ee41c46021668ff48d131a576edde277a33ebf44354c2c70'
APPROVED_PAYLOAD_HASHES = {PAYLOAD_HASH, 'fd50b3c289286e0bc596dd63d33838743ce105017aaa1f61f2ae0346c6e087cd',
                         '198ba57eaf14633986011dbe03d6876c743d198312693295a843f55fe0b50aa8'}
FRESH_HASH = '03b5b91fdc60d38fbb6767bec6af14fbe60219cb5f0aea6d86a451ea4a1c6631'
UPDATER_HASH = '7e671ccaa85ee0b4e7853a4140e5845ae7f5e3905e7553d910e73bf8f0865bff'


class Refusal(Exception):
    pass


def require(value, message):
    if not value:
        raise Refusal(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_path(raw, kind='target'):
    p = Path(raw)
    require(p.is_absolute() and ':' not in str(p) and not any(ord(c) < 32 or ord(c) == 127 for c in str(p)), 'Unsafe ' + kind + ' path.')
    require('..' not in p.parts, 'Unsafe ' + kind + ' path.')
    private_barrier = False
    for item in [*reversed(p.parents), p]:
        require(not item.is_symlink(), 'Linked ' + kind + ' path is unsupported.')
        if item.exists():
            s = item.stat()
            require(s.st_uid in (0, os.geteuid()) and (private_barrier or not s.st_mode & 0o022), 'Unsafe writable ' + kind + ' parent.')
            if stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid() and not s.st_mode & 0o077:
                private_barrier = True
    return p.resolve()


def target_path(raw):
    p = safe_path(raw)
    require(p != REPO and not p.is_relative_to(REPO) and not REPO.is_relative_to(p), 'The repository/release/QA tree is not an installation target.')
    require(not (p / '.git').exists() and not (p / 'installer/bootstrap.py').exists(), 'A checkout is not an installation target.')
    return p


def trusted_payload():
    require(not RELEASE.parent.is_symlink() and not RELEASE.is_symlink(), 'Linked release is forbidden.')
    payload = RELEASE / 'payload'
    require(not payload.is_symlink(), 'Linked payload is forbidden.')
    tool = payload / 'release-tools/update.py'
    require(tool.is_file() and not tool.is_symlink() and digest(tool) == UPDATER_HASH, 'Untrusted payload updater pin.')
    require((payload / 'files.sha256').is_file() and digest(payload / 'files.sha256') == PAYLOAD_HASH, 'Untrusted payload manifest pin.')
    spec = importlib.util.spec_from_file_location('venue_frozen_update', tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.verify_release(payload)
    except module.Refusal:
        raise Refusal('Invalid payload: checksums or exact membership failed.') from None
    # Podman 4.9 reports the wildcard IPv4 publication with an empty HostIp.
    # Adapt only this exact frontend dictionary; never change frozen bytes or
    # relax the backend/DB, address, protocol, port or duplicate-binding checks.
    raw_inspect = module.inspect
    def compatible_inspect(kind, name):
        value = raw_inspect(kind, name)
        if kind == 'container' and name in (module.DB, *module.APPS):
            ports = value.get('HostConfig', {}).get('PortBindings')
            require(ports is None or isinstance(ports, dict), 'Invalid host PortBindings schema.')
            actual = value.get('NetworkSettings', {}).get('Ports')
            require(actual is None or isinstance(actual, dict), 'Invalid runtime Ports schema.')
            # Null values describe exposed but unpublished image ports, not
            # host bindings. Every non-null publication must match exactly.
            actual = {} if actual is None else {key: binding for key, binding in actual.items() if binding is not None}
            expected = {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]} if name == 'venue-bingo-frontend' else {}
            if name == 'venue-bingo-frontend' and actual == {'80/tcp': [{'HostIp': '', 'HostPort': '3000'}]}:
                actual = expected
            require(actual == expected, 'Unexpected runtime host publications.')
        if kind == 'container' and name == 'venue-bingo-frontend':
            host = value.get('HostConfig', {})
            if host.get('PortBindings') == {'80/tcp': [{'HostIp': '', 'HostPort': '3000'}]}:
                value = {**value, 'HostConfig': {**host, 'PortBindings': {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]}}}
        return value
    setattr(module, 'inspect', compatible_inspect)
    return module


def prerequisites(allow_install=False, timeout=None):
    require(sys.platform == 'linux' and os.geteuid() != 0, 'Use an ordinary Linux rootless user, never sudo.')
    missing = [name for name in ('podman', 'curl', 'bash') if not shutil.which(name)]
    if missing:
        require(allow_install and sys.stdin.isatty(), 'Missing prerequisites: ' + ', '.join(missing) + '. Run online in an interactive terminal to install Ubuntu/Debian packages.')
        distro = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        require(distro.get('ID', '').strip('\"') in ('ubuntu', 'debian'), 'Automatic packages support Ubuntu/Debian only.')
        require(shutil.which('sudo') and shutil.which('apt-get'), 'Missing sudo/apt-get; install prerequisites with your system administrator.')
        packages = sorted(set(missing) | ({'uidmap', 'slirp4netns', 'fuse-overlayfs'} if 'podman' in missing else set()))
        print('Missing OS prerequisites. sudo is used ONLY for apt packages; never for Podman.', flush=True)
        for command in (['sudo', '--', 'apt-get', 'update'], ['sudo', '--', 'apt-get', 'install', *packages]):
            require(subprocess.run(command).returncode == 0, 'OS prerequisite installation failed; no runtime changes were attempted.')
        require(all(shutil.which(name) for name in missing), 'Missing prerequisites after package installation.')
    result = subprocess.run(['podman', 'info', '--format', 'json'], capture_output=True, timeout=timeout)
    require(result.returncode == 0, 'Podman is unavailable; configure rootless Podman first.')
    info = json.loads(result.stdout)
    require(info.get('host', {}).get('security', {}).get('rootless') is True, 'Podman must be rootless.')
    store_identity(info.get('store'))
    return info


def exists(kind, name):
    result = subprocess.run(['podman', kind, 'exists', name], capture_output=True)
    require(result.returncode in (0, 1), 'Runtime existence check failed.')
    return result.returncode == 0


def fresh_preflight(target, module):
    require(not target.exists() or target.is_dir() and not any(target.iterdir()), 'Unrecognized nonempty target; no files will be overwritten.')
    for kind, name in [('network', module.NETWORK), ('volume', module.VOLUME), *[('container', n) for n in (module.DB, *module.APPS)], *[('image', module.IMAGES[x]) for x in ('backend', 'frontend')]]:
        require(not exists(kind, name), 'Fresh installation resource/tag collision; preserve all existing data and investigate.')


def private_dir(path):
    safe_path(path, 'state')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(path.is_dir() and path.stat().st_uid == os.geteuid() and path.stat().st_mode & 0o077 == 0, 'State/target directory must be private and owned by this user.')


def atomic_json(path, value):
    require(not path.is_symlink(), 'Linked state file is forbidden.')
    temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_fresh_files():
    require(not RELEASE.parent.is_symlink() and not RELEASE.is_symlink(), 'Linked release is forbidden.')
    manifest = RELEASE / 'fresh-files.json'
    require(digest(manifest) == FRESH_HASH, 'Fresh source manifest pin mismatch.')
    expected = json.loads(manifest.read_text())
    actual = set()
    for sub in ('source', 'assets', 'build'):
        require(not (RELEASE / sub).is_symlink(), 'Linked fresh source is forbidden.')
        for directory, dirs, files in os.walk(RELEASE / sub, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                mode = path.lstat().st_mode
                require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Linked/special fresh file.')
                if path.is_file():
                    require(path.stat().st_nlink == 1, 'Hardlinked fresh file.')
                    actual.add(path.relative_to(RELEASE).as_posix())
    require(set(expected) == actual, 'Fresh source file set mismatch.')
    for name, checksum in expected.items():
        require(digest(RELEASE / name) == checksum, 'Fresh source checksum mismatch.')


def store_identity(store, recorded=False):
    fields = {'graphRoot', 'runRoot', 'graphDriverName'}
    require(isinstance(store, dict) and fields <= store.keys(), 'Invalid runtime storage identity schema.')
    require(not recorded or store.keys() == fields, 'Invalid recorded storage identity schema.')
    for name in fields:
        value = store[name]
        require(isinstance(value, str) and value and value == value.strip() and
                not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Invalid runtime storage identity field.')
        require(Path(value).is_absolute() if name != 'graphDriverName' else
                all(c.isalnum() or c in '-_.' for c in value), 'Invalid runtime storage identity field.')
    return {name: store[name] for name in fields}


def receipt(target, module, info, current):
    return {'version': 1, 'status': 'installed', 'target': str(target), 'uid': os.geteuid(),
            'store': store_identity(info.get('store')), 'payload_sha256': PAYLOAD_HASH,
            'db_id': current['containers'][module.DB]['Id'], 'volume': current['volume'],
            'container_ids': {name: item['Id'] for name, item in current['containers'].items()},
            'network': current['network'], 'images': current['images'],
            'config_sha256': {name: digest(target / name) for name in module.FILES}}


@contextmanager
def namespace_lock(state, check):
    # A genuinely read-only --check on a pristine account creates no state.
    # All mutating runs use this same HOME-anchored namespace despite XDG changes.
    if check and not (state / 'namespace.lock').exists():
        require(not (state / 'namespace.lock').is_symlink(), 'Linked namespace lock.')
        yield
        return
    if not check:
        private_dir(state)
    safe_path(state / 'namespace.lock', 'lock')
    fd = os.open(state / 'namespace.lock', (os.O_RDONLY if check else os.O_RDWR | os.O_CREAT) | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == os.geteuid() and not s.st_mode & 0o077, 'Unsafe namespace lock.')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refusal('Namespace lock is busy; another install/update is running.') from None
        yield
    finally:
        os.close(fd)


def unfinished(state):
    path = state / 'operation.json'
    if path.exists() or path.is_symlink():
        value = private_json(path)
        require(value.get('version') == 1 and value.get('status') == 'installed', 'Unfinished/failed operation requires manual recovery; preserve all volumes and private backups. Inspect operation.json.')
        require(value.get('payload_sha256') in APPROVED_PAYLOAD_HASHES, 'Unknown operation payload pin; manual recovery is required.')


def journal_write(state, value):
    atomic_json(state / 'operation.json', value)


@contextmanager
def operation(state, target, kind):
    value = {'version': 1, 'status': 'preparing', 'operation': kind, 'target': str(target),
             'uid': os.geteuid(), 'payload_sha256': PAYLOAD_HASH, 'token': uuid.uuid4().hex,
             'created': {}, 'intent': {}}
    journal_write(state, value)
    try:
        yield value
    except BaseException:
        value['status'] = 'failed-needs-recovery'
        journal_write(state, value)
        raise
    value['status'] = 'installed'
    journal_write(state, value)


def fresh_install(target, module, info, state, journal):
    verify_fresh_files()
    token = journal['token']
    candidates = {}
    journal['intent'] = {'network': module.NETWORK, 'volume': module.VOLUME, 'containers': [module.DB, *module.APPS],
                         'candidate_tags': {s: 'localhost/reactbingo-' + s + ':install-' + token for s in ('backend', 'frontend')}}
    journal_write(state, journal)
    module.podman('pull', module.IMAGES['db'])
    mongo_id = module.image_id(module.IMAGES['db'])
    for short in ('backend', 'frontend'):
        tag = 'localhost/reactbingo-' + short + ':install-' + token
        require(not exists('image', tag), 'Candidate image collision.')
        module.podman('build', '--pull=always', '-f', RELEASE / 'build' / ('Dockerfile.' + short),
                      '-t', tag, RELEASE / 'source' / short)
        candidates[short] = module.image_id(tag)
    fresh_preflight(target, module)
    require(module.image_id(module.IMAGES['db']) == mongo_id, 'Mongo image changed during builds.')
    private_dir(target)
    with module.installation_lock(target):
        provision(target, module, info, state, journal, candidates)
    print('Installed and verified Bingo: http://localhost:3000')
    return 0


def provision(target, module, info, state, journal, candidates):
    journal['status'] = 'provisioning'
    journal['created']['images'] = candidates
    journal_write(state, journal)
    private_dir(target / '.venue-install')
    (target / 'reactbingo/backend').mkdir(parents=True, mode=0o700)
    for source, dest in [('assets/bingo.sh', 'bingo.sh'), ('source/nginx.conf', 'reactbingo/nginx.conf'),
                         ('source/backend/cards.csv', 'reactbingo/backend/cards.csv')]:
        shutil.copyfile(RELEASE / source, target / dest)
        # Only this newly copied public sample needs nonroot container read
        # access under default rootless UID mapping. Host parents stay private;
        # existing installations never enter provision or have modes changed.
        (target / dest).chmod(0o700 if dest == 'bingo.sh' else 0o644 if dest.endswith('/cards.csv') else 0o600)
    for short, ident in candidates.items():
        module.podman('tag', ident, module.IMAGES[short])
        require(module.image_id(module.IMAGES[short]) == ident, 'Published image ID mismatch.')
    # Create and verify the whole resource set ourselves; do not rely on the
    # launcher's progressive preflight or its curl-exit-only readiness test.
    module.podman('network', 'create', '--label', module.LABEL + '=1', module.NETWORK)
    journal['created']['network'] = module.inspect('network', module.NETWORK)
    journal_write(state, journal)
    module.podman('volume', 'create', '--label', module.LABEL + '=1', module.VOLUME)
    journal['created']['volume'] = module.inspect('volume', module.VOLUME)
    journal_write(state, journal)
    common = ['run', '-d', '--pull=never', '--label', module.LABEL + '=1', '--network', module.NETWORK, '--restart', 'unless-stopped']
    module.podman(*common, '--name', module.DB, '--network-alias', 'db', '-v', module.VOLUME + ':/data/db',
                  '--env', 'GLIBC_TUNABLES=glibc.pthread.rseq=1', module.IMAGES['db'])
    journal['created']['db_id'] = module.inspect('container', module.DB).get('Id')
    journal_write(state, journal)
    module.recreate_apps_only(target)
    current = module.preflight(target)
    module.verify_apps(target, candidates, current['containers'][module.DB]['Id'])
    atomic_json(target / '.venue-install/receipt.json', receipt(target, module, info, current))
    private_dir(state)
    atomic_json(state / 'installation.json', {'version': 1, 'target': str(target), 'uid': os.geteuid()})


def private_json(path):
    safe_path(path, 'state')
    s = path.stat()
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid == os.geteuid() and not s.st_mode & 0o077, 'State files must be private regular files owned by this user.')
    value = json.loads(path.read_text())
    require(isinstance(value, dict), 'Invalid installation state.')
    return value


def is_checkout(path):
    return path == REPO or (path / '.git').exists() or (path / 'source/.git').exists() or (path / 'installer/bootstrap.py').exists()


def detect_target(args, home, state):
    pointer = state / 'installation.json'
    recorded = None
    if pointer.exists() or pointer.is_symlink():
        value = private_json(pointer)
        require(value.get('version') == 1 and value.get('uid') == os.geteuid(), 'Invalid installation pointer owner/version.')
        recorded = target_path(value['target'])
        require(recorded.is_dir(), 'Recorded installation target is missing; refusing a new install.')
        require((recorded / '.venue-install/receipt.json').is_file(), 'Recorded installation receipt is missing; manual recovery required.')
    if args.dir:
        target = target_path(args.dir)
        require(recorded is None or target == recorded, 'Explicit target disagrees with the recorded installation; refusing.')
        return target
    if recorded is not None:
        return recorded
    data = safe_path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')), 'data')
    default = target_path(data / 'venue-bingo')
    legacy = home / 'bingo'
    candidates = [default]
    if legacy != default and legacy.exists() and not is_checkout(legacy):
        candidates.append(target_path(legacy))
    populated = [p for p in candidates if p.is_dir() and any(p.iterdir())]
    require(len(populated) <= 1, 'Multiple possible installations; use an explicit target after investigating.')
    return populated[0] if populated else default


def existing_preflight(target, module, info):
    metadata = target / '.venue-install'
    safe_path(metadata, 'state')
    if metadata.exists():
        require(metadata.is_dir() and metadata.stat().st_uid == os.geteuid() and not metadata.stat().st_mode & 0o077, 'Installation metadata must be private and owned.')
    log = metadata / 'last-update.log'
    safe_path(log, 'state')
    if log.exists():
        s = log.stat()
        require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and s.st_nlink == 1 and not s.st_mode & 0o077, 'Unsafe installation log file.')
    for name in module.FILES:
        safe_path(target / name, 'configuration')
    current = module.preflight(target)
    path = target / '.venue-install/receipt.json'
    if path.exists() or path.is_symlink():
        old = private_json(path)
        require(old.get('payload_sha256') in APPROVED_PAYLOAD_HASHES, 'Unknown installation payload pin; refusing update.')
        require(old.get('version') == 1 and old.get('target') == str(target) and old.get('uid') == os.geteuid() and store_identity(old.get('store'), recorded=True) == store_identity(info.get('store')), 'Installation receipt/store/target mismatch.')
        require(old.get('db_id') == current['containers'][module.DB]['Id'] and old.get('volume') == current['volume'] and old.get('network') == current['network'], 'Recorded database/volume/network identity changed; preserve all data.')
        require(old.get('config_sha256') == {name: digest(target / name) for name in module.FILES}, 'Recorded installation configuration changed; investigate without overwriting it.')
    return current


def update_install(target, module, info, state, check):
    with module.installation_lock(target):
        initial = existing_preflight(target, module, info)
        if check:
            module.verify_apps(target, initial['images'], initial['containers'][module.DB]['Id'])
            print('Verified payload; existing installation is healthy.')
            return 0
    # The frozen subprocess opens/flocks the directory itself. Never keep our
    # independent directory descriptor locked across this call.
    private_dir(target / '.venue-install')
    # Load the same pinned module/inspection adapter in the child, including
    # automatic rollback. Keep its independent directory lock and release path.
    script = ('import runpy,sys; b=runpy.run_path(sys.argv[1]); '
              'm=b["trusted_payload"](); target=sys.argv[2]; '
              'sys.argv=[str(m.__file__),target]; sys.exit(m.main())')
    result = subprocess.run([sys.executable, '-B', '-c', script, str(HERE / 'bootstrap.py'), str(target)], capture_output=True)
    log = target / '.venue-install/last-update.log'
    with log.open('wb') as stream:
        stream.write(result.stdout + result.stderr)
    log.chmod(0o600)
    require(result.returncode == 0, 'Frozen app update failed (even if rollback succeeded). Preserve its private backups; see .venue-install/last-update.log.')
    with module.installation_lock(target):
        current = existing_preflight(target, module, info)
        module.verify_apps(target, current['images'], initial['containers'][module.DB]['Id'])
        atomic_json(target / '.venue-install/receipt.json', receipt(target, module, info, current))
        private_dir(state)
        atomic_json(state / 'installation.json', {'version': 1, 'target': str(target), 'uid': os.geteuid()})
    print('Updated and verified Bingo: http://localhost:3000 — reload both browsers.')
    return 0


def load_desktop():
    spec = importlib.util.spec_from_file_location('bingo_desktop', HERE / 'desktop.py')
    desktop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(desktop)
    return desktop


def desktop_registration(target, home, module, desktop_only=False):
    try:
        with module.installation_lock(target):
            desktop = load_desktop()
            if desktop_only:
                desktop.private_directory(target)
                desktop.private_directory(target / '.venue-install')
                desktop.state_preflight(target, home)
            return desktop.register(target, home, preserve_runtime=desktop_only)
    except Exception:
        print('Bingo app installation succeeded, but shortcut registration failed. Preserve foreign/changed files and retry with --desktop-only after investigating.', file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description='Install Bingo; rerun the same command to update the checked-out release.')
    parser.add_argument('--dir', help='Explicit absolute installation directory (not the checkout).')
    parser.add_argument('--offline', action='store_true', help='Existing app-only update; never install packages or pull images.')
    parser.add_argument('--check', action='store_true', help='Read-only deployment/prerequisite check.')
    desktop_options = parser.add_mutually_exclusive_group()
    desktop_options.add_argument('--desktop-only', action='store_true', help='Register/refresh Bingo for an existing recognized installation; no update, packages or runtime starts.')
    desktop_options.add_argument('--no-desktop', action='store_true', help='Skip app-menu registration (headless installation/update).')
    args = parser.parse_args()
    module = None
    try:
        home = safe_path(os.environ['HOME'], 'home')
        state = safe_path(home / '.local/state/venue-bingo', 'state')
        module = trusted_payload()
        require(sys.platform == 'linux' and os.geteuid() != 0, 'Use an ordinary Linux rootless user, never sudo.')
        if args.offline or args.check:
            prerequisites(timeout=10 if args.desktop_only else None)
        os.umask(0o077)
        with namespace_lock(state, args.check):
            unfinished(state)
            target = detect_target(args, home, state)
            if not args.no_desktop and not args.check:
                desktop = load_desktop()
                try:
                    desktop.desktop_paths(target, home)
                except desktop.Refusal as error:
                    raise Refusal(str(error)) from None
            populated = target.is_dir() and any(target.iterdir())
            if args.desktop_only:
                require(not args.check, '--desktop-only cannot be combined with --check.')
                require((target / '.venue-install/receipt.json').is_file(), '--desktop-only requires a recognized existing installation receipt.')
                prerequisites(timeout=10)
                return desktop_registration(target, home, module, desktop_only=True)
            require(not populated or (target / 'bingo.sh').is_file(), 'Unrecognized nonempty target; no files will be overwritten.')
            info = prerequisites(allow_install=not populated and not args.offline and not args.check)
            if populated:
                require((target / 'bingo.sh').is_file(), 'Unrecognized nonempty target; no files will be overwritten.')
                with module.installation_lock(target):
                    existing_preflight(target, module, info)
                if args.check:
                    return update_install(target, module, info, state, True)
                with operation(state, target, 'update'):
                    update_install(target, module, info, state, False)
                return 0 if args.no_desktop else desktop_registration(target, home, module)
            fresh_preflight(target, module)
            require(not args.offline, 'Offline fresh installation is unsupported; initial images/packages/npm need internet.')
            verify_fresh_files()
            if args.check:
                print('Verified payload; fresh installation would use: ' + str(target))
                return 0
            with operation(state, target, 'fresh') as journal:
                fresh_install(target, module, info, state, journal)
            return 0 if args.no_desktop else desktop_registration(target, home, module)
    except Refusal as error:
        print('Bingo refused: ' + str(error), file=sys.stderr)
        return 1
    except Exception as error:
        if module is not None and isinstance(error, module.Refusal):
            print('Bingo refused: ' + str(error), file=sys.stderr)
        else:
            print('Bingo refused: unsafe target or invalid payload/local prerequisites.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
