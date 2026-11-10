"""Release updater tests: no real network, installer or container runtime."""
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import unittest
from unittest import mock
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('bingo_release_update', ROOT / 'installer/release_update.py')
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('Missing release updater test subject')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def metadata(version='v3.0.0'):
    base = 'https://github.com/JackDelfin/bingo/releases/'
    return {'tag_name': version, 'draft': False, 'prerelease': False,
            'html_url': base + 'tag/' + version,
            'assets': [{'name': name, 'state': 'uploaded', 'size': size,
                        'browser_download_url': base + 'download/' + version + '/' + name}
                       for name, size in [('bingo-release.zip', 1000), ('bingo-release.zip.sha256', 84)]]}


def public_zip(version='v3.0.0', files=None, manifest_change=None, extras=()):
    if files is None:
        files = {name: ('public fixture ' + name + '\n').encode() for name in
                 ('install.sh', 'installer/bootstrap.py', 'installer/desktop.py',
                  'installer/release_update.py', 'installer/management.py', 'LICENSE',
                  'source/frontend/src/main.tsx')}
    manifest = {'schemaVersion': 1, 'version': version, 'repository': 'JackDelfin/bingo',
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    if manifest_change:
        manifest_change(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr('bingo/' + name, data)
        archive.writestr('bingo/release.json', json.dumps(manifest).encode())
        for name, data in extras:
            archive.writestr(name, data)
    return output.getvalue(), files


def rewrite_zip(data, change):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as original, zipfile.ZipFile(output, 'w') as result:
        for item in original.infolist():
            content = original.read(item)
            change(item)
            result.writestr(item, content)
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='release-tests-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.target = self.base / 'target'
        self.cache = self.base / 'cache'
        self.calls = []
        self.invocations = []
        self.document = metadata()
        self.unexpected_io = []
        def forbid_io(*args, **kwargs):
            self.unexpected_io.append(True)
            raise RuntimeError('Unmocked network or subprocess is forbidden')
        for obj, name in ((socket, 'create_connection'), (urllib.request, 'build_opener'),
                          (subprocess, 'run'), (subprocess, 'Popen')):
            patcher = mock.patch.object(obj, name, side_effect=forbid_io)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.assertEqual(self.unexpected_io, [], 'A test attempted unmocked network or subprocess I/O')

    def fetch(self, url, *, limit, timeout):
        self.calls.append((url, limit, timeout))
        if url != release.LATEST_API:
            self.fail('Unexpected network URL: ' + url)
        return json.dumps(self.document).encode()

    def updater(self, current='v2.0.0', fetch=None, runner=None):
        return release.ReleaseUpdater(self.target, self.cache, current, fetch=fetch or self.fetch,
                                      runner=runner or self.no_runner)

    def no_runner(self, *args, **kwargs):
        self.fail('Installer runner must not be invoked in this test')

    def recognized_target(self):
        self.target.mkdir(mode=0o700)
        metadata_dir = self.target / '.venue-install'
        metadata_dir.mkdir(mode=0o700)
        path = metadata_dir / 'receipt.json'
        path.write_text(json.dumps({'version': 1, 'status': 'installed', 'uid': os.geteuid(),
                                    'target': str(self.target)}))
        path.chmod(0o600)

    def asset_fetcher(self, data, sidecar=None, *, version='v3.0.0'):
        self.document = metadata(version)
        if sidecar is None:
            sidecar = (hashlib.sha256(data).hexdigest() + '  bingo-release.zip\n').encode()
        payloads = {'bingo-release.zip': data, 'bingo-release.zip.sha256': sidecar}
        for asset in self.document['assets']:
            asset['size'] = len(payloads[asset['name']])
        def fetch(url, *, limit, timeout):
            self.calls.append((url, limit, timeout))
            if url == release.LATEST_API:
                return json.dumps(self.document).encode()
            for asset in self.document['assets']:
                if url == asset['browser_download_url']:
                    return payloads[asset['name']]
            self.fail('Unexpected URL in release download')
        return fetch

    def test_install_extracts_private_source_then_verifies_before_publication(self):
        self.recognized_target()
        data, expected = public_zip()
        commands = []
        def runner(command, **kwargs):
            commands.append((command, kwargs))
            self.assertFalse((self.cache / 'installed-release.json').exists())
            self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
            self.assertIs(kwargs['shell'], False)
            self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
            self.assertGreater(kwargs['timeout'], 0)
            self.assertLessEqual(kwargs['timeout'], 1800)
            self.assertEqual(os.fstat(kwargs['stdout'].fileno()).st_mode & 0o777, 0o600)
            for key in ('GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'SSH_AUTH_SOCK', 'PYTHONPATH', 'LD_PRELOAD', 'BASH_ENV'):
                self.assertNotIn(key, kwargs['env'])
            kwargs['stdout'].write(b'fixture installer output\n')
            return subprocess.CompletedProcess(command, 0)
        updater = self.updater(fetch=self.asset_fetcher(data), runner=runner)
        with mock.patch.dict(os.environ, {key: 'SECRET' for key in
                             ('GITHUB_TOKEN', 'AWS_SECRET_ACCESS_KEY', 'SSH_AUTH_SOCK', 'PYTHONPATH', 'LD_PRELOAD', 'BASH_ENV')}):
            result = updater.install('v3.0.0')
        self.assertIn('installedVersion', result)
        self.assertEqual(result['installedVersion'], 'v3.0.0')
        repo = Path(result['repoPath'])
        self.assertEqual(repo.name, 'bingo')
        self.assertEqual(repo.parent.parent, self.cache)
        self.assertEqual(len(commands), 2)
        bootstrap = str(repo / 'installer/bootstrap.py')
        self.assertEqual(commands[0][0], [sys.executable, '-B', bootstrap, '--offline', '--dir', str(self.target)])
        self.assertEqual(commands[1][0], [sys.executable, '-B', bootstrap, '--offline', '--check', '--dir', str(self.target)])
        for name, content in expected.items():
            path = repo / name
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_uid, os.geteuid())
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        for path in [self.cache, repo.parent, repo, *[p for p in repo.rglob('*') if p.is_dir()]]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        pointer = json.loads((self.cache / 'installed-release.json').read_text())
        self.assertEqual(pointer['repoPath'], str(repo))
        self.assertEqual(pointer['installedVersion'], 'v3.0.0')
        self.assertEqual(Path(result['logPath']).read_bytes(), b'fixture installer output\n' * 2)
        self.assertEqual(updater.current_version, 'v3.0.0')
        self.assertEqual(len(self.calls), 3)

    def successful_runner(self, command, **kwargs):
        self.invocations.append(command)
        return subprocess.CompletedProcess(command, 0)

    def reject_install(self, updater, expected='v3.0.0'):
        with self.assertRaises(release.Refusal) as caught:
            updater.install(expected)
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(self.invocations, [])
        self.assertFalse((self.cache / 'installed-release.json').exists())

    def test_install_requires_exact_latest_newer_version_before_asset_download(self):
        self.recognized_target()
        data, _ = public_zip()
        for current, expected in [('v2.0.0', 'v2.5.0'), ('v3.0.0', 'v3.0.0'),
                                   ('v4.0.0', 'v3.0.0'), ('v2.0.0', 'v3.0.0-rc1')]:
            with self.subTest(current=current, expected=expected):
                self.cache = self.base / ('cache' + str(len(self.calls)))
                start = len(self.calls)
                self.reject_install(self.updater(current, fetch=self.asset_fetcher(data),
                                                  runner=self.successful_runner), expected)
                self.assertTrue(all(url == release.LATEST_API for url, _, _ in self.calls[start:]))

    def test_install_requires_private_owned_existing_receipt(self):
        data, _ = public_zip()
        for index, kind in enumerate(('missing-target', 'missing-receipt', 'public-target', 'public-metadata',
                                      'public-receipt', 'bad-receipt', 'wrong-uid', 'wrong-target',
                                      'symlink-receipt', 'hardlink-receipt', 'checkout', 'nested-checkout',
                                      'symlink-target', 'foreign-owner')):
            with self.subTest(kind=kind):
                self.target = self.base / ('target-' + str(index))
                self.cache = self.base / ('cache-' + str(index))
                if kind != 'missing-target':
                    self.recognized_target()
                    path = self.target / '.venue-install/receipt.json'
                    if kind == 'missing-receipt':
                        path.unlink()
                    elif kind == 'public-target':
                        self.target.chmod(0o755)
                    elif kind == 'public-metadata':
                        path.parent.chmod(0o755)
                    elif kind == 'public-receipt':
                        path.chmod(0o644)
                    elif kind in ('bad-receipt', 'wrong-uid', 'wrong-target'):
                        doc = json.loads(path.read_text())
                        doc[{'bad-receipt': 'status', 'wrong-uid': 'uid', 'wrong-target': 'target'}[kind]] = 'SECRET'
                        path.write_text(json.dumps(doc))
                    elif kind in ('symlink-receipt', 'hardlink-receipt'):
                        other = self.base / ('receipt-' + str(index))
                        path.rename(other)
                        if kind == 'symlink-receipt':
                            path.symlink_to(other)
                        else:
                            os.link(other, path)
                    elif kind in ('checkout', 'nested-checkout'):
                        directory = self.target if kind == 'checkout' else self.target / 'source'
                        directory.mkdir(exist_ok=True)
                        (directory / '.git').mkdir()
                    elif kind == 'symlink-target':
                        actual = self.base / 'actual-target'
                        self.target.rename(actual)
                        self.target.symlink_to(actual, target_is_directory=True)
                updater = self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner)
                if kind == 'foreign-owner':
                    with mock.patch.object(release.os, 'geteuid', return_value=os.geteuid() + 100):
                        self.reject_install(updater)
                else:
                    self.reject_install(updater)

    def test_install_refuses_busy_target_lock(self):
        import fcntl
        self.recognized_target()
        path = self.target / '.venue-install/release-update.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data, _ = public_zip()
        self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        self.assertEqual(self.calls, [])

    def test_installer_failures_keep_source_and_private_logs_without_publication(self):
        self.recognized_target()
        data, _ = public_zip()
        for index, failure in enumerate(('install', 'check', 'timeout', 'exception')):
            self.cache = self.base / ('failed-' + str(index))
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                kwargs['stdout'].write(b'PRIVATE fixture log\n')
                if failure == 'timeout':
                    raise subprocess.TimeoutExpired(command, kwargs['timeout'], output=b'SECRET')
                if failure == 'exception':
                    raise OSError('SECRET credential')
                return subprocess.CompletedProcess(command, 1 if failure == 'install' or len(calls) == 2 else 0)
            updater = self.updater(fetch=self.asset_fetcher(data), runner=runner)
            with self.subTest(failure=failure):
                with self.assertRaises(release.Refusal) as caught:
                    updater.install('v3.0.0')
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertEqual(len(calls), 2 if failure == 'check' else 1)
                self.assertFalse((self.cache / 'installed-release.json').exists())
                sources = list(self.cache.glob('release-*/bingo/installer/bootstrap.py'))
                self.assertEqual(len(sources), 1)
                logs = list(self.cache.glob('release-*/install.log'))
                self.assertEqual(len(logs), 1)
                self.assertTrue(logs[0].read_bytes().startswith(b'PRIVATE'))
                self.assertEqual(logs[0].stat().st_mode & 0o777, 0o600)
                self.assertEqual(updater.current_version, 'v2.0.0')

    def test_cache_pointer_prevents_stale_version_reinstall(self):
        self.recognized_target()
        data, _ = public_zip()
        result = self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner).install('v3.0.0')
        self.invocations.clear()
        with self.assertRaises(release.Refusal):
            self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner).install('v3.0.0')
        self.assertEqual(self.invocations, [])
        self.assertEqual(json.loads((self.cache / 'installed-release.json').read_text())['repoPath'], result['repoPath'])

    def test_reconstruction_checks_the_maximum_of_installed_and_shipped_versions(self):
        self.recognized_target()
        data, _ = public_zip(version='v3.1.0')
        updater = self.updater(current='v3.0.0', fetch=self.asset_fetcher(data, version='v3.1.0'),
                               runner=self.successful_runner)
        self.assertEqual(updater.install('v3.1.0')['installedVersion'], 'v3.1.0')
        pointer = self.cache / 'installed-release.json'
        persisted = pointer.read_bytes()
        self.assertEqual(json.loads(persisted)['installedVersion'], 'v3.1.0')
        self.assertEqual(len(self.invocations), 2)
        self.calls.clear()
        self.invocations.clear()
        for shipped, effective in (('v3.0.0', 'v3.1.0'), ('v3.2.0', 'v3.2.0')):
            with self.subTest(shipped=shipped):
                reconstructed = self.updater(current=shipped)
                self.assertEqual(reconstructed.check(), {
                    'currentVersion': effective, 'latestVersion': 'v3.1.0', 'available': False,
                    'releaseUrl': release.RELEASE_BASE + 'tag/v3.1.0'})
                self.assertEqual(reconstructed.current_version, effective)
                with self.assertRaisesRegex(release.Refusal, 'Same-version installs and downgrades are forbidden'):
                    reconstructed.install('v3.1.0')
        self.assertTrue(all(url == release.LATEST_API for url, _, _ in self.calls))
        self.assertEqual(self.invocations, [])
        self.assertEqual(pointer.read_bytes(), persisted)

    def test_install_requires_exact_checksum_sidecar_and_asset_lengths(self):
        self.recognized_target()
        data, _ = public_zip()
        good = hashlib.sha256(data).hexdigest().encode()
        bad_values = [b'0' * 64 + b'  bingo-release.zip\n', good + b' bingo-release.zip\n',
                      good + b'  foreign.zip\n', good + b'  bingo-release.zip',
                      good + b'  bingo-release.zip\r\n', good + b'  bingo-release.zip\nextra\n',
                      b'g' * 64 + b'  bingo-release.zip\n']
        for index, value in enumerate(bad_values):
            with self.subTest(sidecar=value):
                self.cache = self.base / ('sidecar-' + str(index))
                self.reject_install(self.updater(fetch=self.asset_fetcher(data, value),
                                                  runner=self.successful_runner))
        self.cache = self.base / 'length-mismatch'
        fetch = self.asset_fetcher(data)
        self.document['assets'][0]['size'] += 1
        self.reject_install(self.updater(fetch=fetch, runner=self.successful_runner))

    def test_manifest_exact_inventory_identity_and_required_files(self):
        self.recognized_target()
        _, files = public_zip()
        changes = [('schema', lambda m: m.update(schemaVersion=2)),
                   ('bool-schema', lambda m: m.update(schemaVersion=True)),
                   ('version', lambda m: m.update(version='v9.0.0')),
                   ('repository', lambda m: m.update(repository='evil/bingo')),
                   ('missing-file', lambda m: m['files'].pop('LICENSE')),
                   ('extra-file', lambda m: m['files'].update({'absent.txt': '0' * 64})),
                   ('self-inventory', lambda m: m['files'].update({'release.json': '0' * 64})),
                   ('bad-digest', lambda m: m['files'].update({'LICENSE': '0' * 64})),
                   ('not-sha256', lambda m: m['files'].update({'LICENSE': 'x' * 64})),
                   ('not-files', lambda m: m.update(files=[]))]
        for index, (label, change) in enumerate(changes):
            with self.subTest(label=label):
                self.cache = self.base / ('manifest-' + str(index))
                data, _ = public_zip(manifest_change=change)
                self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        for index, name in enumerate(files):
            with self.subTest(missing_required=name):
                self.cache = self.base / ('required-' + str(index))
                data, _ = public_zip(files={key: value for key, value in files.items() if key != name})
                self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))

    def test_archive_refuses_unsafe_paths_private_git_database_and_dependencies(self):
        self.recognized_target()
        _, files = public_zip()
        bad_names = ['../escape', 'source/../escape', 'source/./escape', 'source//escape',
                     'source\\escape', '/absolute', 'source/C:escape', 'source/nul\x00hidden',
                     '.git/config', 'source/.git/config', 'source/.env', 'source/.env.local',
                     'source/node_modules/module.js', 'source/db/data', 'source/private/controller.json',
                     'qa/links.json', 'source/save.sqlite3', 'source/__pycache__/module.pyc',
                     'source/.ssh/id_rsa', 'source/backups/backup', 'source/vendor/dependency',
                     'source/secrets.json', 'source/runtime.log', 'LICENSE/child', 'license']
        for index, name in enumerate(bad_names):
            with self.subTest(name=name):
                self.cache = self.base / ('path-' + str(index))
                data, _ = public_zip(files={**files, name: b'private fixture'})
                self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        self.cache = self.base / 'absolute-member'
        destination = str(self.base / 'must-not-write')
        data, _ = public_zip(manifest_change=lambda m: m['files'].update({destination: hashlib.sha256(b'x').hexdigest()}),
                             extras=[(destination, b'x')])
        self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        self.assertFalse(Path(destination).exists())

    def test_archive_refuses_links_special_members_and_bombs_before_extraction(self):
        self.recognized_target()
        data, files = public_zip()
        for index, mode in enumerate((stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600,
                                      stat.S_IFSOCK | 0o600, stat.S_IFBLK | 0o600,
                                      stat.S_IFCHR | 0o600, stat.S_IFREG | 0o4600)):
            with self.subTest(mode=mode):
                self.cache = self.base / ('special-' + str(index))
                def change(item):
                    if item.filename == 'bingo/LICENSE':
                        item.create_system = 3
                        item.external_attr = mode << 16
                changed = rewrite_zip(data, change)
                self.reject_install(self.updater(fetch=self.asset_fetcher(changed), runner=self.successful_runner))
                self.assertEqual(list(self.cache.glob('release-*/bingo')), [])
        for name, limit in [('MAX_ENTRIES', 3), ('MAX_EXTRACTED', 100), ('MAX_MANIFEST', 10)]:
            with self.subTest(limit=name):
                self.cache = self.base / name
                with mock.patch.object(release, name, limit, create=True):
                    self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        self.cache = self.base / 'ratio-bomb'
        bomb, _ = public_zip(files={**files, 'source/bomb.txt': b'0' * (1024 * 1024)})
        self.reject_install(self.updater(fetch=self.asset_fetcher(bomb), runner=self.successful_runner))

    def test_archive_accepts_explicit_safe_directories(self):
        self.recognized_target()
        data, _ = public_zip(extras=[('bingo/', b''), ('bingo/empty/', b'')])
        result = self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner).install('v3.0.0')
        self.assertTrue((Path(result['repoPath']) / 'empty').is_dir())

    def test_default_https_transport_disables_proxy_and_never_forwards_credentials(self):
        opened = []
        payload = json.dumps(metadata()).encode()
        def open_request(request, *, timeout):
            opened.append(request)
            self.assertEqual(request.full_url, release.LATEST_API)
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 30)
            self.assertNotIn('Authorization', request.headers)
            self.assertNotIn('Cookie', request.headers)
            return Response(payload, headers={'Content-Length': str(len(payload))})
        opener = mock.Mock()
        opener.open.side_effect = open_request
        with mock.patch.object(urllib.request, 'build_opener', return_value=opener) as build:
            updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
            try:
                result = updater.check()
            except release.Refusal:
                self.fail('Default bounded HTTPS transport must fetch a valid release')
        self.assertTrue(result['available'])
        self.assertEqual(len(opened), 1)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {} for h in handlers))
        redirect = next(h for h in handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        self.assertIsNone(redirect.redirect_request(opened[0], None, 302, 'redirect', {}, 'https://evil.test/'))

    def test_hardlink_zip_extra_fields_are_refused_before_extraction(self):
        self.recognized_target()
        data, _ = public_zip()
        for identifier in (0x000d, 0x756e):
            with self.subTest(identifier=identifier):
                self.cache = self.base / ('link-extra-' + str(identifier))
                def change(item):
                    if item.filename == 'bingo/LICENSE':
                        payload = b'\0' * 12 + b'../other'
                        item.extra = struct.pack('<HH', identifier, len(payload)) + payload
                self.reject_install(self.updater(fetch=self.asset_fetcher(rewrite_zip(data, change)),
                                                  runner=self.successful_runner))
                self.assertEqual(list(self.cache.glob('release-*/bingo')), [])

    def test_runner_refusals_never_expose_untrusted_exception_text(self):
        self.recognized_target()
        data, _ = public_zip()
        def runner(command, **kwargs):
            raise release.Refusal('SECRET injected exception')
        with self.assertRaises(release.Refusal) as caught:
            self.updater(fetch=self.asset_fetcher(data), runner=runner).install('v3.0.0')
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertFalse((self.cache / 'installed-release.json').exists())

    def test_updater_works_when_copied_beside_installed_desktop_helpers(self):
        self.recognized_target()
        helper = self.target / '.venue-install/release_update.py'
        helper.write_bytes(Path(release.__file__).read_bytes())
        helper.chmod(0o600)
        spec = importlib.util.spec_from_file_location('installed_release_update', helper)
        if spec is None or spec.loader is None:
            raise RuntimeError('Missing installed test helper')
        installed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installed)
        data, _ = public_zip()
        updater = installed.ReleaseUpdater(self.target, self.cache, 'v2.0.0',
                                            fetch=self.asset_fetcher(data), runner=self.successful_runner)
        try:
            result = updater.install('v3.0.0')
        except installed.Refusal:
            self.fail('Installed helper must not mistake its own installation for a developer checkout')
        self.assertEqual(result['installedVersion'], 'v3.0.0')

    def test_cache_inside_a_developer_checkout_is_never_written(self):
        self.recognized_target()
        developer = self.base / 'developer'
        developer.mkdir(mode=0o700)
        (developer / '.git').mkdir(mode=0o700)
        self.cache = developer / 'cache'
        data, _ = public_zip()
        self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))
        self.assertFalse(self.cache.exists())

    def test_publication_is_atomic_and_failures_leave_prior_pointer_untouched(self):
        self.recognized_target()
        data, _ = public_zip()
        for previous in (False, True):
            with self.subTest(previous=previous):
                self.cache = self.base / ('atomic-' + str(previous))
                self.cache.mkdir(mode=0o700)
                pointer = self.cache / 'installed-release.json'
                original = (json.dumps({'schemaVersion': 1, 'target': str(self.target),
                                        'installedVersion': 'v2.0.0', 'repoPath': 'old'}) + '\n').encode()
                if previous:
                    pointer.write_bytes(original)
                    pointer.chmod(0o600)
                with mock.patch.object(release.os, 'replace', side_effect=OSError('SECRET')):
                    with self.assertRaises(release.Refusal) as caught:
                        self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner).install('v3.0.0')
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertEqual(pointer.read_bytes() if previous else pointer.exists(), original if previous else False)
                self.assertEqual(len(list(self.cache.glob('release-*/bingo'))), 1)
                self.assertEqual(len(list(self.cache.glob('release-*/install.log'))), 1)

    def test_local_zip_headers_cannot_hide_encryption_or_link_metadata(self):
        self.recognized_target()
        data, _ = public_zip()
        for index, kind in enumerate(('local-encryption', 'local-link')):
            with self.subTest(kind=kind):
                self.cache = self.base / ('local-' + str(index))
                if kind == 'local-link':
                    def add_extra(item):
                        if item.filename == 'bingo/LICENSE':
                            payload = b'\0' * 12 + b'../other'
                            item.extra = struct.pack('<HH', 0x5455, len(payload)) + payload
                    changed = bytearray(rewrite_zip(data, add_extra))
                else:
                    changed = bytearray(data)
                with zipfile.ZipFile(io.BytesIO(changed)) as archive:
                    offset = archive.getinfo('bingo/LICENSE').header_offset
                if kind == 'local-encryption':
                    flags = struct.unpack_from('<H', changed, offset + 6)[0]
                    struct.pack_into('<H', changed, offset + 6, flags | 1)
                else:
                    name_length = struct.unpack_from('<H', changed, offset + 26)[0]
                    struct.pack_into('<H', changed, offset + 30 + name_length, 0x000d)
                self.reject_install(self.updater(fetch=self.asset_fetcher(bytes(changed)), runner=self.successful_runner))
                self.assertEqual(list(self.cache.glob('release-*/bingo')), [])

    def test_default_transport_accepts_only_explicit_public_asset_cdn_redirects(self):
        original = metadata()['assets'][0]['browser_download_url']
        destinations = [original, 'https://release-assets.githubusercontent.com/github-production-release-asset/123/abc?sig=SECRET',
                        'https://objects.githubusercontent.com/github-production-release-asset-2e65be/123/abc?sig=SECRET']
        for destination in destinations:
            with self.subTest(destination=destination):
                headers = Message()
                headers['Location'] = destination
                redirect = urllib.error.HTTPError(original, 302, 'SECRET', headers, io.BytesIO())
                opener = mock.Mock()
                opener.open.side_effect = [redirect, Response(b'abc')]
                with mock.patch.object(urllib.request, 'build_opener', return_value=opener):
                    updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
                    self.assertEqual(updater._fetch(original, 3), b'abc')
                self.assertEqual(opener.open.call_count, 2)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.full_url, destination)
                self.assertNotIn('Authorization', request.headers)
                self.assertNotIn('Cookie', request.headers)

    def test_default_transport_rejects_foreign_downgraded_ambiguous_redirects(self):
        original = metadata()['assets'][0]['browser_download_url']
        bad = ['http://release-assets.githubusercontent.com/github-production-release-asset/123/abc',
               'https://release-assets.githubusercontent.com.evil.test/github-production-release-asset/123/abc',
               'https://SECRET@release-assets.githubusercontent.com/github-production-release-asset/123/abc',
               'https://release-assets.githubusercontent.com:443/github-production-release-asset/123/abc',
               'https://objects.githubusercontent.com/foreign/path',
               'https://github.com/evil/bingo/releases/download/v3.0.0/bingo-release.zip',
               original + '?token=SECRET', original + '#fragment', '//evil.test/asset', None,
               'https://release-assets.githubusercontent.com/github-production-release-asset/123/../abc']
        for destination in bad:
            with self.subTest(destination=destination):
                opener = mock.Mock()
                opener.open.return_value = Response(b'', 302, {'Location': destination})
                with mock.patch.object(urllib.request, 'build_opener', return_value=opener):
                    updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
                    with self.assertRaises(release.Refusal) as caught:
                        updater._fetch(original, 100)
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertEqual(opener.open.call_count, 1)
        opener = mock.Mock()
        opener.open.return_value = Response(b'', 302, {'Location': original})
        with mock.patch.object(urllib.request, 'build_opener', return_value=opener):
            updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
            with self.assertRaises(release.Refusal):
                updater.check()
        self.assertEqual(opener.open.call_count, 1)

    def test_default_transport_enforces_length_status_deadline_and_redirect_budget(self):
        original = metadata()['assets'][0]['browser_download_url']
        responses = [Response(b'1234'), Response(b'x', headers={'Content-Length': '4'}),
                     Response(b'abc', headers={'Content-Length': '-1'}),
                     Response(b'abc', headers={'Content-Length': '4'}),
                     Response(b'abc', headers={'Content-Encoding': 'gzip'}), Response(b'abc', status=206),
                     Response(b'abc', status=429), Response(b'abc', status=404)]
        for response in responses:
            opener = mock.Mock()
            opener.open.return_value = response
            with self.subTest(status=response.status, headers=response.headers), \
                    mock.patch.object(urllib.request, 'build_opener', return_value=opener):
                updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
                with self.assertRaises(release.Refusal):
                    updater._fetch(original, 3)
            self.assertTrue(response.closed)
        opener = mock.Mock()
        opener.open.side_effect = [Response(b'', 302, {'Location': original}) for _ in range(4)]
        with mock.patch.object(urllib.request, 'build_opener', return_value=opener):
            updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
            with self.assertRaises(release.Refusal):
                updater._fetch(original, 3)
        self.assertEqual(opener.open.call_count, 4)
        opener = mock.Mock()
        response = Response(b'abc')
        opener.open.return_value = response
        with mock.patch.object(urllib.request, 'build_opener', return_value=opener), \
                mock.patch.object(release.time, 'monotonic', side_effect=[0, 0, 121]):
            updater = release.ReleaseUpdater(self.target, self.cache, 'v2.0.0', runner=self.no_runner)
            with self.assertRaises(release.Refusal):
                updater._fetch(original, 3)
        self.assertTrue(response.closed)

    def test_cache_and_lock_paths_refuse_links_public_state_and_unsafe_overlap(self):
        data, _ = public_zip()
        kinds = ('public-cache', 'symlink-cache', 'cache-in-target', 'cache-around-target', 'dot-dot-cache',
                 'public-lock', 'symlink-lock', 'hardlink-lock', 'fifo-lock', 'public-pointer',
                 'symlink-pointer', 'hardlink-pointer', 'malformed-pointer', 'foreign-pointer')
        for index, kind in enumerate(kinds):
            with self.subTest(kind=kind):
                self.target = self.base / ('target-' + str(index))
                self.cache = self.base / ('cache-' + str(index))
                self.recognized_target()
                if kind == 'cache-in-target':
                    self.cache = self.target / 'cache'
                elif kind == 'cache-around-target':
                    self.cache = self.base
                elif kind == 'dot-dot-cache':
                    self.cache = self.base / 'x' / '..' / 'cache'
                elif kind in ('public-cache', 'symlink-cache'):
                    self.cache.mkdir(mode=0o700)
                    if kind == 'public-cache':
                        self.cache.chmod(0o755)
                    else:
                        actual = self.base / 'actual-cache'
                        self.cache.rename(actual)
                        self.cache.symlink_to(actual, target_is_directory=True)
                elif kind.endswith('-lock'):
                    path = self.target / '.venue-install/release-update.lock'
                    other = self.base / ('lock-' + str(index))
                    other.write_bytes(b'unchanged')
                    other.chmod(0o600)
                    if kind == 'symlink-lock':
                        path.symlink_to(other)
                    elif kind == 'hardlink-lock':
                        os.link(other, path)
                    elif kind == 'fifo-lock':
                        os.mkfifo(path, 0o600)
                    else:
                        path.write_bytes(b'')
                        path.chmod(0o644)
                else:
                    self.cache.mkdir(mode=0o700)
                    path = self.cache / 'installed-release.json'
                    doc = {'schemaVersion': 1, 'target': str(self.target), 'installedVersion': 'v2.0.0'}
                    if kind == 'foreign-pointer':
                        doc['target'] = 'SECRET'
                    path.write_text('SECRET invalid json' if kind == 'malformed-pointer' else json.dumps(doc))
                    path.chmod(0o600)
                    if kind == 'public-pointer':
                        path.chmod(0o644)
                    elif kind in ('symlink-pointer', 'hardlink-pointer'):
                        actual = self.base / ('pointer-' + str(index))
                        path.rename(actual)
                        path.symlink_to(actual) if kind == 'symlink-pointer' else os.link(actual, path)
                with self.assertRaises(release.Refusal) as caught:
                    self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner).install('v3.0.0')
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertEqual(self.invocations, [])
                self.assertEqual(list(self.cache.glob('release-*')), [])

    def test_target_receipt_is_revalidated_before_verification(self):
        self.recognized_target()
        data, _ = public_zip()
        calls = []
        def runner(command, **kwargs):
            calls.append(command)
            (self.target / '.venue-install/receipt.json').unlink()
            return subprocess.CompletedProcess(command, 0)
        with self.assertRaises(release.Refusal):
            self.updater(fetch=self.asset_fetcher(data), runner=runner).install('v3.0.0')
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.cache / 'installed-release.json').exists())

    def test_injected_transport_is_bounded_and_sanitizes_all_exceptions(self):
        values = (b'x' * (release.MAX_METADATA + 1), 'SECRET', bytearray(b'SECRET'), None)
        for value in values:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(release.Refusal) as caught:
                    self.updater(fetch=lambda *a, **k: value).check()
                self.assertNotIn('SECRET', str(caught.exception))
        for error in (OSError('SECRET'), release.Refusal('SECRET'), TimeoutError('SECRET')):
            def fetch(*args, **kwargs):
                raise error
            with self.assertRaises(release.Refusal) as caught:
                self.updater(fetch=fetch).check()
            self.assertNotIn('SECRET', str(caught.exception))

    def test_archive_refuses_duplicate_encrypted_and_corrupt_members(self):
        self.recognized_target()
        original, files = public_zip()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            duplicate, _ = public_zip(extras=[('bingo/LICENSE', files['LICENSE'])])
        cases = [('duplicate', duplicate)]
        with zipfile.ZipFile(io.BytesIO(original)) as archive:
            entries = archive.infolist()
            local = archive.getinfo('bingo/LICENSE').header_offset
        central = original.index(b'PK\x01\x02')
        for item in entries:
            if item.filename == 'bingo/LICENSE':
                break
            central += 46 + len(item.filename.encode()) + len(item.extra) + len(item.comment)
        for label, local_offset, central_offset, value, format in (
                ('encrypted', 6, 8, 1, '<H'), ('strong-encryption', 6, 8, 0x40, '<H'),
                ('unsupported-compression', 8, 10, zipfile.ZIP_BZIP2, '<H'), ('bad-crc', 14, 16, 0, '<I')):
            changed = bytearray(original)
            struct.pack_into(format, changed, local + local_offset, value)
            struct.pack_into(format, changed, central + central_offset, value)
            cases.append((label, bytes(changed)))
        for index, (label, data) in enumerate(cases):
            with self.subTest(label=label):
                self.cache = self.base / ('structural-' + str(index))
                self.reject_install(self.updater(fetch=self.asset_fetcher(data), runner=self.successful_runner))

    def test_check_refuses_untrusted_or_ambiguous_metadata(self):
        cases = []
        for key, value in [('draft', True), ('prerelease', True), ('draft', 0),
                           ('prerelease', None), ('html_url', 'https://github.com/evil/bingo/releases/tag/v3.0.0')]:
            doc = metadata()
            doc[key] = value
            cases.append(doc)
        for tag in ('3.0.0', 'v03.0.0', 'v3.0.0-rc1', 'v3.0.0+meta', 'v3.0', 'v3.0.0\n'):
            cases.append(metadata(tag))
        for url in ('http://github.com/JackDelfin/bingo/releases/download/v3.0.0/bingo-release.zip',
                    'https://github.com/evil/bingo/releases/download/v3.0.0/bingo-release.zip',
                    'https://github.com@evil.test/bingo-release.zip',
                    'https://github.com/JackDelfin/bingo/releases/download/v3.0.0/bingo-release.zip?token=SECRET',
                    'https://github.com:443/JackDelfin/bingo/releases/download/v3.0.0/bingo-release.zip'):
            doc = metadata()
            doc['assets'][0]['browser_download_url'] = url
            cases.append(doc)
        for change in ('duplicate', 'missing', 'case-alias', 'huge', 'not-uploaded', 'bad-size'):
            doc = metadata()
            if change == 'duplicate':
                doc['assets'].append(dict(doc['assets'][0]))
            elif change == 'missing':
                doc['assets'].pop()
            elif change == 'case-alias':
                doc['assets'].append({**doc['assets'][0], 'name': 'BINGO-RELEASE.ZIP'})
            elif change == 'huge':
                doc['assets'][0]['size'] = 64 * 1024 * 1024 + 1
            elif change == 'bad-size':
                doc['assets'][0]['size'] = True
            else:
                doc['assets'][0]['state'] = 'new'
            cases.append(doc)
        for doc in cases:
            with self.subTest(document=doc):
                self.document = doc
                with self.assertRaises(release.Refusal):
                    self.updater().check()
        for raw in (b'{"draft":false,"draft":true}', b'not json', b'x' * (1024 * 1024 + 1)):
            with self.subTest(raw=raw[:100]):
                with self.assertRaises(release.Refusal):
                    self.updater(fetch=lambda *a, **k: raw).check()
        def failed(*args, **kwargs):
            raise OSError('SECRET credential')
        with self.assertRaises(release.Refusal) as caught:
            self.updater(fetch=failed).check()
        self.assertNotIn('SECRET', str(caught.exception))
        for current in ('invalid', 'v01.0.0', 'v1.0.0-rc1'):
            with self.assertRaises(release.Refusal):
                self.updater(current=current).check()
        self.assertFalse(self.cache.exists())

    def test_check_uses_semantic_order_and_never_offers_downgrade(self):
        for current, latest, expected in [('v2.9.9', 'v2.10.0', True),
                                           ('v3.0.0', 'v3.0.0', False),
                                           ('v10.0.0', 'v9.0.0', False)]:
            self.document = metadata(latest)
            self.assertEqual(self.updater(current).check()['available'], expected)

    def test_check_stable_version_without_writing(self):
        self.assertEqual(self.updater().check(), {
            'currentVersion': 'v2.0.0', 'latestVersion': 'v3.0.0', 'available': True,
            'releaseUrl': 'https://github.com/JackDelfin/bingo/releases/tag/v3.0.0'})
        self.assertEqual(len(self.calls), 1)
        self.assertGreater(self.calls[0][1], 0)
        self.assertGreater(self.calls[0][2], 0)
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.target.exists())


if __name__ == '__main__':
    print('TARGET_FLAGS ' + str(sys.flags), flush=True)
    for path in (Path(__file__), ROOT / 'installer/release_update.py'):
        print('SOURCE_SHA256 ' + hashlib.sha256(path.read_bytes()).hexdigest() + ' ' + str(path), flush=True)
    unittest.main(verbosity=2)
