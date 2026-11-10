"""Stdlib-only public packaging tests; every tree is synthetic and scratch-only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bingo_package_release', ROOT / 'installer/package_release.py')
if spec is None or spec.loader is None:
    raise RuntimeError('Packaging module cannot be loaded')
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(root, name, data=b'public fixture\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def inventory(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob('*')) if p.is_file()}


def manifests(release):
    payload = inventory(release / 'payload')
    payload.pop('files.sha256', None)
    write(release, 'payload/files.sha256', ''.join(
        sha(data) + '  ' + name + '\n' for name, data in sorted(payload.items())).encode())
    fresh = {name: sha(data) for name, data in inventory(release).items()
             if name.split('/')[0] in ('assets', 'build', 'source')}
    write(release, 'fresh-files.json', (json.dumps(fresh, indent=2, sort_keys=True) + '\n').encode())


class PackagingFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bingo-package-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.scratch = Path(self.tmp.name)
        self.root = self.scratch / 'checkout'
        self.root.mkdir(mode=0o700)
        self.base = self.root / 'installer/releases/controller-audio-v2'
        self.destination = self.root / 'installer/releases/settings-v3'
        for name, data in {
            'README.md': b'# Frozen audio baseline\n',
            'assets/bingo.sh': b'#!/bin/sh\n# frozen launcher\n',
            'build/Dockerfile.frontend': b'FROM public-frontend\n',
            'build/Dockerfile.backend': b'FROM public-backend\n',
            'source/LICENSE': b'Public fixture license\n',
            'source/README.md': b'Public source readme\n',
            'source/nginx.conf': b'public nginx config\n',
            'source/backend/src/server.ts': b'// frozen backend\n',
            'source/backend/package.json': b'{"name":"backend"}\n',
            'source/frontend/src/main.tsx': b'// old frontend\n',
            'source/frontend/public/manifest.json': b'{}\n',
            'source/frontend/public/audio/calls/1.mp3': b'old audio',
            'source/frontend/scripts/generate.py': b'# old generator\n',
            'source/frontend/package.json': b'{"name":"old"}\n',
            'source/frontend/vite.config.ts': b'// old vite\n',
            'payload/release-tools/update.py': b'# immutable updater\n',
            'payload/release-tools/Dockerfile.frontend': b'FROM frozen\n',
            'payload/backend-dist/server.js': b'// frozen runtime backend\n',
            'payload/frontend-dist/index.html': b'old index\n',
            'payload/frontend-dist/assets/old.js': b'old asset\n',
        }.items():
            write(self.base, name, data)
        manifests(self.base)
        for name, data in {
            'src/main.tsx': b'// reviewed settings frontend\n',
            'src/settings/SettingsPanel.tsx': b'// public settings\n',
            'public/audio/calls/1.mp3': b'new audio',
            'scripts/generate.py': b'# reviewed generator\n',
            'package.json': b'{"name":"settings"}\n',
            'package-lock.json': b'{"lockfileVersion":3}\n',
            'vite.config.ts': b'// reviewed vite\n',
            'index.html': b'<html></html>\n',
            'dist/index.html': b'new index\n',
            'dist/assets/new.js': b'new runtime\n',
        }.items():
            write(self.root / 'source/frontend', name, data)
        write(self.root, 'source/LICENSE', b'Public fixture license\n')


    def run_cli(self, *arguments):
        return subprocess.run([sys.executable, '-B', *(['-O'] if sys.flags.optimize else []),
                               str(ROOT / 'installer/package_release.py'), *map(str, arguments)],
                              cwd=self.scratch, text=True, capture_output=True, timeout=30,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))


class FreezeTests(PackagingFixture):
    def test_freeze_overlays_reviewed_frontend_without_touching_baseline(self):
        before = inventory(self.base)
        live_before = inventory(self.root / 'source')
        result = pack.freeze_release(self.root, self.destination)
        self.assertTrue((self.destination / 'payload/frontend-dist/index.html').is_file(),
                        'freeze_release must create a new release')
        frozen = inventory(self.destination)
        self.assertEqual(frozen['README.md'], before['README.md'])
        self.assertEqual(inventory(self.base), before)
        self.assertEqual(inventory(self.root / 'source'), live_before)
        for name in ('assets/bingo.sh', 'build/Dockerfile.backend', 'build/Dockerfile.frontend',
                     'source/backend/src/server.ts', 'payload/backend-dist/server.js',
                     'payload/release-tools/update.py'):
            self.assertEqual(frozen[name], before[name], name)
        self.assertEqual(frozen['source/frontend/src/main.tsx'], live_before['frontend/src/main.tsx'])
        self.assertEqual(frozen['source/frontend/package.json'], live_before['frontend/package.json'])
        self.assertEqual(frozen['payload/frontend-dist/assets/new.js'], b'new runtime\n')
        self.assertNotIn('payload/frontend-dist/assets/old.js', frozen)
        self.assertEqual(frozen['source/frontend/public/manifest.json'], b'{}\n')
        expected_payload = {name.removeprefix('payload/'): sha(data) for name, data in frozen.items()
                            if name.startswith('payload/') and name != 'payload/files.sha256'}
        expected_lines = ''.join(value + '  ' + name + '\n'
                                 for name, value in sorted(expected_payload.items()))
        self.assertEqual(frozen['payload/files.sha256'].decode(), expected_lines)
        fresh = json.loads(frozen['fresh-files.json'])
        expected_fresh = {name: sha(data) for name, data in frozen.items()
                          if name.split('/')[0] in ('assets', 'build', 'source')}
        self.assertEqual(fresh, expected_fresh)
        self.assertEqual(list(fresh), sorted(fresh))
        self.assertEqual(result['payloadManifestSha256'], sha(frozen['payload/files.sha256']))
        self.assertEqual(result['freshManifestSha256'], sha(frozen['fresh-files.json']))
        self.assertEqual(result['sourceFileCount'], sum(n.startswith('source/') for n in frozen))

    def test_freeze_refuses_forbidden_names_in_every_permitted_tree(self):
        for tree in (self.base, self.root / 'source/frontend/src',
                     self.root / 'source/frontend/public/audio',
                     self.root / 'source/frontend/scripts', self.root / 'source/frontend/dist'):
            for name in ('.env', '.git/config', 'node_modules/accidental.js',
                         '__pycache__/cached.pyc', 'database/dump.bson',
                         'qa/receipt.json', 'private/notes.txt', 'controller-links.txt'):
                with self.subTest(tree=tree.relative_to(self.root), name=name):
                    path = write(tree, name)
                    try:
                        with self.assertRaises(pack.Refusal):
                            pack.freeze_release(self.root, self.destination)
                        self.assertFalse(self.destination.exists())
                    finally:
                        if self.destination.exists():
                            shutil.rmtree(self.destination)
                        path.unlink()
                        while path.parent != tree and not any(path.parent.iterdir()):
                            path.parent.rmdir()
                            path = path.parent

    def test_freeze_refuses_links_and_special_members_without_following_them(self):
        public_target = write(self.scratch, 'public-target', b'public outside fixture\n')
        for tree in (self.base, self.root / 'source/frontend/src',
                     self.root / 'source/frontend/dist'):
            for kind in ('symlink', 'directory-link', 'hardlink', 'fifo'):
                with self.subTest(tree=tree.relative_to(self.root), kind=kind):
                    rogue = tree / 'rogue.tsx'
                    if kind == 'symlink':
                        rogue.symlink_to(public_target)
                    elif kind == 'directory-link':
                        rogue.symlink_to(self.scratch, target_is_directory=True)
                    elif kind == 'hardlink':
                        os.link(public_target, rogue)
                    else:
                        os.mkfifo(rogue)
                    try:
                        with self.assertRaises(pack.Refusal):
                            pack.freeze_release(self.root, self.destination)
                        self.assertFalse(self.destination.exists())
                    finally:
                        if self.destination.exists():
                            shutil.rmtree(self.destination)
                        rogue.unlink()
        frontend = self.root / 'source/frontend'
        saved = frontend.with_name('frontend-saved')
        frontend.rename(saved)
        frontend.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(pack.Refusal):
            pack.freeze_release(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_freeze_refuses_private_content_even_under_a_public_filename(self):
        for data in (
            b'open https://' + b'venue.invalid/' + b'control/Friday/' + b'0123456789abcdef0123456789abcdef',
            b'-----BEGIN ' + b'OPENSSH PRIVATE KEY-----\nfixture only',
            b'ghp_' + b'A' * 36,
            b'mongodb://' + b'fixture:password@venue.invalid/db',
            b'ADMIN_API_TOKEN=' + b'fixture_token_value_' * 2 + b'\n',
        ):
            with self.subTest(sample=sha(data)):
                rogue = write(self.root, 'source/frontend/src/notes.txt', data)
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.freeze_release(self.root, self.destination)
                    self.assertFalse(self.destination.exists())
                finally:
                    if self.destination.exists():
                        shutil.rmtree(self.destination)
                    rogue.unlink()

    def test_freeze_destination_is_exclusive_and_only_the_new_settings_release(self):
        write(self.destination, 'sentinel.txt', b'keep existing release\n')
        before = inventory(self.destination)
        try:
            pack.freeze_release(self.root, self.destination)
        except Exception as error:
            self.assertIsInstance(error, pack.Refusal, 'Existing destinations require a controlled refusal')
        else:
            self.fail('Existing release destination was accepted')
        self.assertEqual(inventory(self.destination), before)
        for destination in (self.base, self.root / 'source/frontend',
                            self.scratch / 'other-new-release'):
            with self.subTest(destination=destination):
                with self.assertRaises(pack.Refusal):
                    pack.freeze_release(self.root, destination)
        self.assertFalse((self.scratch / 'other-new-release').exists())

    def test_freeze_checks_exact_frozen_manifest_membership_before_overlay(self):
        for name, replacement in (
            ('source/backend/src/server.ts', b'changed without updating manifest'),
            ('payload/backend-dist/server.js', b'changed runtime'),
            ('source/frontend/extra.txt', b'unlisted fresh input'),
            ('payload/frontend-dist/extra.js', b'unlisted payload input'),
            ('fresh-files.json', b'{"duplicate":0,"duplicate":1}\n'),
            ('payload/files.sha256', b'invalid inventory\n'),
        ):
            with self.subTest(name=name):
                path = self.base / name
                old = path.read_bytes() if path.exists() else None
                write(self.base, name, replacement)
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.freeze_release(self.root, self.destination)
                    self.assertFalse(self.destination.exists())
                finally:
                    if self.destination.exists():
                        shutil.rmtree(self.destination)
                    if old is None:
                        path.unlink()
                    else:
                        path.write_bytes(old)

    def test_freeze_refuses_incomplete_inputs_and_never_reads_unreviewed_live_files(self):
        os.mkfifo(self.root / 'source/.env')
        write(self.root, 'source/backend/private-state.txt', b'not public input')
        write(self.root, 'source/frontend/public/unreviewed.txt', b'not reviewed')
        write(self.root, 'source/frontend/unreviewed.config', b'not reviewed')
        for name in ('payload/release-tools/update.py', 'assets/bingo.sh'):
            path = self.base / name
            saved = path.read_bytes()
            path.unlink()
            manifests(self.base)
            try:
                with self.assertRaises(pack.Refusal):
                    pack.freeze_release(self.root, self.destination)
                self.assertFalse(self.destination.exists())
            finally:
                if self.destination.exists():
                    shutil.rmtree(self.destination)
                path.write_bytes(saved)
                manifests(self.base)
        index = self.root / 'source/frontend/dist/index.html'
        index.unlink()
        with self.assertRaises(pack.Refusal):
            pack.freeze_release(self.root, self.destination)
        self.assertFalse(self.destination.exists())
        index.write_bytes(b'new index\n')
        pack.freeze_release(self.root, self.destination)
        frozen = inventory(self.destination)
        self.assertNotIn('source/.env', frozen)
        self.assertNotIn('source/backend/private-state.txt', frozen)
        self.assertNotIn('source/frontend/public/unreviewed.txt', frozen)
        self.assertNotIn('source/frontend/unreviewed.config', frozen)

    def test_freeze_requires_reviewed_main_configs_and_rejects_broken_optional_links(self):
        frontend = self.root / 'source/frontend'
        for name in ('package.json', 'package-lock.json', 'vite.config.ts', 'index.html'):
            with self.subTest(missing=name):
                path = frontend / name
                saved = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.freeze_release(self.root, self.destination)
                    self.assertFalse(self.destination.exists())
                finally:
                    path.write_bytes(saved)
                    if self.destination.exists():
                        shutil.rmtree(self.destination)
        (frontend / '.dockerignore').symlink_to(self.scratch / 'missing-optional')
        with self.assertRaises(pack.Refusal):
            pack.freeze_release(self.root, self.destination)
        self.assertFalse(self.destination.exists())

    def test_freeze_rolls_back_its_own_partial_new_release_on_io_failure(self):
        before = inventory(self.base)
        with patch.object(pack.os, 'fsync', side_effect=OSError('fixture write failure')):
            with self.assertRaises(pack.Refusal):
                pack.freeze_release(self.root, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(inventory(self.base), before)


    def test_freeze_cli_reports_actual_pins_and_refuses_second_invocation(self):
        result = self.run_cli('--freeze', self.root, self.destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.destination / 'fresh-files.json').exists(), 'CLI must really freeze the fixture')
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['freshManifestSha256'], sha((self.destination / 'fresh-files.json').read_bytes()))
        again = self.run_cli('--freeze', self.root, self.destination)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('refused', again.stderr.lower())
        self.assertNotIn('Traceback', again.stderr)

    def test_freeze_refuses_overlarge_members_before_creating_destination(self):
        write(self.root, 'source/frontend/src/oversize.ts', b'x' * 8192)
        with patch.object(pack, 'MAX_EXTRACTED_BYTES', 4096):
            with self.assertRaises(pack.Refusal):
                pack.freeze_release(self.root, self.destination)
        self.assertFalse(self.destination.exists())


    def test_freeze_does_not_read_through_an_ancestor_symlink_race(self):
        tree = self.root / 'source/frontend/src'
        target = tree / 'main.tsx'
        inode = (target.stat().st_dev, target.stat().st_ino)
        private_data = b'fixture outside the reviewed tree; must not be read'
        outside = self.scratch / 'outside'
        write(outside, 'main.tsx', private_data)
        saved = tree.with_name('src-saved')
        original_regular, original_screen = pack._regular, pack._public_content
        raced, observed = False, False
        def swap_after_check(metadata):
            nonlocal raced
            original_regular(metadata)
            if not raced and (metadata.st_dev, metadata.st_ino) == inode:
                raced = True
                tree.rename(saved)
                tree.symlink_to(outside, target_is_directory=True)
        def watch_screen(data):
            nonlocal observed
            observed |= data == private_data
            original_screen(data)
        with (patch.object(pack, '_regular', side_effect=swap_after_check),
              patch.object(pack, '_public_content', side_effect=watch_screen)):
            with self.assertRaises(pack.Refusal):
                pack.freeze_release(self.root, self.destination)
        self.assertTrue(raced)
        self.assertFalse(observed, 'The reader followed a replaced ancestor into an unreviewed directory')
        self.assertFalse(self.destination.exists())


class PackageTests(PackagingFixture):
    def setUp(self):
        super().setUp()
        pack.freeze_release(self.root, self.destination)
        for sub in ('payload', 'assets', 'build', 'source'):
            shutil.copytree(self.base / sub, self.root / 'installer' / sub)
        shutil.copyfile(self.base / 'fresh-files.json', self.root / 'installer/fresh-files.json')
        for name in ('bootstrap', 'desktop', 'release_update', 'management'):
            write(self.root, 'installer/' + name + '.py', b'# public module fixture\n')
        write(self.root, 'installer/package_release.py', (ROOT / 'installer/package_release.py').read_bytes())
        for name in ('test_installer.py', 'test_desktop.py', 'test_desktop_management.py',
                     'test_release_update.py', 'test_management.py', 'fake_podman.py'):
            write(self.root, 'tests/' + name, b'# public test fixture\n')
        write(self.root, 'tests/test_package_release.py', (ROOT / 'tests/test_package_release.py').read_bytes())
        write(self.root, 'install.sh', b'#!/bin/sh\npython3 installer/bootstrap.py "$@"\n')
        for name in ('INSTALL.md', 'AUDIO-CALLOUTS.md', 'SETTINGS.md'):
            write(self.root, 'docs/' + name, b'# Public instructions\n')
        self.output = self.scratch / 'output'

    def test_package_is_a_deterministic_clean_public_checkout_with_exact_inventory(self):
        os.mkfifo(self.root / 'source/.env')
        write(self.root, 'source/backend/private-state.txt', b'not a public input')
        write(self.root, '.git/config', b'private local git fixture')
        write(self.root, 'qa/local.json', b'private local QA fixture')
        write(self.root, 'README.md', b'private workspace readme: must not be bundled')
        write(self.root, 'tests/unreviewed.py', b'not an allowlisted test')
        before = inventory(self.destination)
        first = pack.package_release(self.root, self.output)
        self.assertTrue((self.output / 'bingo-release.zip').is_file(), 'package_release must produce the archive')
        archive = Path(first['archive'])
        checksum = Path(first['checksum'])
        self.assertEqual(archive.name, 'bingo-release.zip')
        self.assertEqual(checksum.name, 'bingo-release.zip.sha256')
        data = archive.read_bytes()
        self.assertEqual(checksum.read_text(), sha(data) + '  bingo-release.zip\n')
        self.assertEqual(first['sha256'], sha(data))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        with zipfile.ZipFile(archive) as zipped:
            names = zipped.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(all(name.startswith('bingo/') and not name.endswith('/') for name in names))
            members = {name.removeprefix('bingo/'): zipped.read(name) for name in names}
            release = json.loads(members.pop('release.json'))
            self.assertEqual(release, {'schemaVersion': 1, 'repository': 'JackDelfin/bingo',
                                      'version': 'v3.0.0',
                                      'files': {name: sha(value) for name, value in members.items()}})
            self.assertEqual(list(release['files']), sorted(release['files']))
            self.assertEqual(first['fileCount'], len(names))
            for info in zipped.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.create_system, 3)
                mode = info.external_attr >> 16
                self.assertTrue(stat.S_ISREG(mode))
                self.assertIn(stat.S_IMODE(mode), (0o644, 0o755))
                self.assertEqual(stat.S_IMODE(mode), 0o755 if info.filename.endswith('.sh') else 0o644)
        for sub in ('payload', 'assets', 'build', 'source'):
            expected = inventory(self.root / 'installer' / sub)
            actual = {name.removeprefix('installer/' + sub + '/'): value for name, value in members.items()
                      if name.startswith('installer/' + sub + '/')}
            self.assertEqual(actual, expected)
        for release_name in ('controller-audio-v2', 'settings-v3'):
            prefix = 'installer/releases/' + release_name + '/'
            actual = {name.removeprefix(prefix): value for name, value in members.items() if name.startswith(prefix)}
            self.assertEqual(actual, inventory(self.root / 'installer/releases' / release_name))
        self.assertEqual({name.removeprefix('source/'): value for name, value in members.items()
                          if name.startswith('source/')}, inventory(self.destination / 'source'))
        self.assertEqual(members['LICENSE'], (self.root / 'source/LICENSE').read_bytes())
        self.assertNotIn(b'private workspace', members['README.md'])
        self.assertEqual({name for name in members if name.startswith('tests/')},
                         {'tests/' + name for name in ('test_installer.py', 'test_desktop.py',
                          'test_desktop_management.py', 'test_release_update.py', 'test_management.py',
                          'test_package_release.py', 'fake_podman.py')})
        self.assertTrue({'docs/INSTALL.md', 'docs/AUDIO-CALLOUTS.md', 'docs/SETTINGS.md',
                         'installer/bootstrap.py', 'installer/desktop.py', 'installer/release_update.py',
                         'installer/management.py', 'installer/package_release.py'} <= members.keys())
        self.assertFalse(any(name.startswith(('.git/', 'qa/')) or name.endswith('.env') for name in members))
        for p in self.destination.rglob('*'):
            os.utime(p, (1700000000, 1700000000))
            if p.is_file():
                p.chmod(0o755)
        second = pack.package_release(self.root, self.scratch / 'second-output')
        self.assertEqual(Path(second['archive']).read_bytes(), data)
        self.assertEqual(inventory(self.destination), before)

    def test_package_requires_private_nonoverlapping_outputs_and_refuses_collisions(self):
        for name in ('bingo-release.zip', 'bingo-release.zip.sha256'):
            with self.subTest(collision=name):
                self.output.mkdir(mode=0o700, exist_ok=True)
                sentinel = write(self.output, name, b'previous artifact: preserve me\n')
                before = inventory(self.output)
                try:
                    try:
                        pack.package_release(self.root, self.output)
                    except Exception as error:
                        self.assertIsInstance(error, pack.Refusal)
                    else:
                        self.fail('Existing artifact was accepted')
                    self.assertEqual(inventory(self.output), before)
                finally:
                    shutil.rmtree(self.output)
        for path in (self.scratch / 'public-output', self.root / 'source/new-output',
                     self.root / 'installer/releases/settings-v3/new-output'):
            with self.subTest(unsafe_output=path):
                if path.name == 'public-output':
                    path.mkdir(mode=0o755)
                    path.chmod(0o755)  # Keep this unsafe fixture independent of the ambient umask.
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.package_release(self.root, path)
                    self.assertFalse((path / 'bingo-release.zip').exists())
                finally:
                    if path.exists():
                        shutil.rmtree(path)
        target = self.scratch / 'linked-output'
        target.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(pack.Refusal):
            pack.package_release(self.root, target)
        result = pack.package_release(self.root, self.output)
        for field in ('archive', 'checksum'):
            self.assertEqual(stat.S_IMODE(Path(result[field]).stat().st_mode), 0o600)

    def test_package_enforces_all_three_size_bounds_before_creating_outputs(self):
        reference = pack.package_release(self.root, self.scratch / 'reference')
        with zipfile.ZipFile(reference['archive']) as archive:
            limits = {'MAX_ARCHIVE_BYTES': Path(reference['archive']).stat().st_size,
                      'MAX_EXTRACTED_BYTES': sum(info.file_size for info in archive.infolist()),
                      'MAX_ENTRIES': len(archive.infolist())}
        for key, value in limits.items():
            with self.subTest(limit=key):
                output = self.scratch / ('limit-' + key)
                with patch.object(pack, key, value - 1, create=True):
                    with self.assertRaises(pack.Refusal):
                        pack.package_release(self.root, output)
                self.assertFalse(output.exists())
        with (patch.object(pack, 'MAX_ARCHIVE_BYTES', limits['MAX_ARCHIVE_BYTES'], create=True),
              patch.object(pack, 'MAX_EXTRACTED_BYTES', limits['MAX_EXTRACTED_BYTES'], create=True),
              patch.object(pack, 'MAX_ENTRIES', limits['MAX_ENTRIES'], create=True)):
            accepted = pack.package_release(self.root, self.output)
        self.assertEqual(Path(accepted['archive']).read_bytes(), Path(reference['archive']).read_bytes())

    def test_package_rejects_unsafe_version_strings_without_outputs(self):
        versions = ('../v3', 'v3.0.0\n', '3.0.0', 'v03.0.0', '', None)
        for index, version in enumerate(versions):
            with self.subTest(version=repr(version)):
                output = self.scratch / ('version-' + str(index))
                with self.assertRaises(pack.Refusal):
                    pack.package_release(self.root, output, version=version)
                self.assertFalse(output.exists())
        result = pack.package_release(self.root, self.output, version='v3.1.2')
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(json.loads(archive.read('bingo/release.json'))['version'], 'v3.1.2')

    def test_package_rejects_broken_optional_docs_and_rolls_back_write_failures(self):
        optional = self.root / 'docs/SETTINGS.md'
        saved = optional.read_bytes()
        optional.unlink()
        optional.symlink_to(self.scratch / 'missing-doc')
        try:
            with self.assertRaises(pack.Refusal):
                pack.package_release(self.root, self.output)
            self.assertFalse(self.output.exists())
        finally:
            optional.unlink()
            optional.write_bytes(saved)
            if self.output.exists():
                shutil.rmtree(self.output)
        for fail_at in (1, 2):
            calls = 0
            def failing_sync(fd):
                nonlocal calls
                calls += 1
                if calls == fail_at:
                    raise OSError('fixture disk failure')
            with self.subTest(failing_fsync=fail_at):
                with patch.object(pack.os, 'fsync', side_effect=failing_sync):
                    try:
                        pack.package_release(self.root, self.output)
                    except Exception as error:
                        self.assertIsInstance(error, pack.Refusal)
                    else:
                        self.fail('Injected filesystem failure was not refused')
                self.assertEqual(inventory(self.output), {})
        optional.unlink()
        result = pack.package_release(self.root, self.output)
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertNotIn('bingo/docs/SETTINGS.md', archive.namelist())

    def test_package_cli_creates_actual_artifacts_and_emits_json_paths(self):
        result = self.run_cli('--package', self.root, self.output, '--version', 'v3.0.0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.output / 'bingo-release.zip').is_file(), 'CLI must really package the fixture')
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['sha256'], sha(Path(receipt['archive']).read_bytes()))
        again = self.run_cli('--package', self.root, self.output)
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('refused', again.stderr.lower())
        self.assertNotIn('Traceback', again.stderr)

    def test_package_rechecks_protected_baseline_bytes_even_with_regenerated_inventories(self):
        for name in ('assets/bingo.sh', 'build/Dockerfile.backend', 'source/backend/src/server.ts',
                     'payload/backend-dist/server.js', 'payload/release-tools/update.py', 'source/nginx.conf'):
            with self.subTest(protected=name):
                path = self.destination / name
                saved = path.read_bytes()
                path.write_bytes(b'changed protected public bytes\n')
                manifests(self.destination)
                output = self.scratch / ('altered-' + name.replace('/', '-'))
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.package_release(self.root, output)
                    self.assertFalse(output.exists())
                finally:
                    path.write_bytes(saved)
                    manifests(self.destination)

    def test_package_refuses_forbidden_names_and_unsafe_file_types_in_every_full_input_tree(self):
        public_target = write(self.scratch, 'public-target', b'outside fixture\n')
        trees = [self.root / 'installer' / sub for sub in ('payload', 'assets', 'build', 'source')]
        trees += [self.base, self.destination]
        for tree in trees:
            for name in ('.env', '.git/config', 'node_modules/x.js', '__pycache__/x.pyc',
                         'database/dump.bson', 'qa/receipt.json', 'private/notes.txt', 'controller-links.txt'):
                with self.subTest(tree=tree.relative_to(self.root), forbidden=name):
                    path = write(tree, name)
                    try:
                        with self.assertRaises(pack.Refusal):
                            pack.package_release(self.root, self.output)
                        self.assertFalse(self.output.exists())
                    finally:
                        path.unlink()
                        while path.parent != tree and not any(path.parent.iterdir()):
                            path.parent.rmdir()
                            path = path.parent
            for kind in ('symlink', 'directory-link', 'broken-link', 'hardlink', 'fifo'):
                with self.subTest(tree=tree.relative_to(self.root), kind=kind):
                    rogue = tree / 'rogue.ts'
                    if kind == 'symlink':
                        rogue.symlink_to(public_target)
                    elif kind == 'directory-link':
                        rogue.symlink_to(self.scratch, target_is_directory=True)
                    elif kind == 'broken-link':
                        rogue.symlink_to(self.scratch / 'missing-link-target')
                    elif kind == 'hardlink':
                        os.link(public_target, rogue)
                    else:
                        os.mkfifo(rogue)
                    try:
                        with self.assertRaises(pack.Refusal):
                            pack.package_release(self.root, self.output)
                        self.assertFalse(self.output.exists())
                    finally:
                        rogue.unlink()

    def test_package_validates_frozen_inventories_and_never_reads_forbidden_content(self):
        private_target = write(self.scratch, 'must-not-read', b'public fixture target')
        for relative in ('install.sh', 'source/LICENSE', 'installer/bootstrap.py',
                         'tests/test_installer.py', 'docs/INSTALL.md'):
            with self.subTest(input=relative):
                path = self.root / relative
                saved = path.read_bytes()
                path.unlink()
                path.symlink_to(private_target)
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.package_release(self.root, self.output)
                    self.assertFalse(self.output.exists())
                finally:
                    path.unlink()
                    path.write_bytes(saved)
        for release in (self.root / 'installer', self.base, self.destination):
            for name in ('fresh-files.json', 'payload/files.sha256', 'source/backend/package.json'):
                with self.subTest(release=release.relative_to(self.root), input=name):
                    path = release / name
                    saved = path.read_bytes()
                    path.write_bytes(b'uninventoried public change\n')
                    try:
                        with self.assertRaises(pack.Refusal):
                            pack.package_release(self.root, self.output)
                        self.assertFalse(self.output.exists())
                    finally:
                        path.write_bytes(saved)
        for relative in ('docs/SETTINGS.md', 'tests/test_installer.py',
                         'installer/desktop.py', 'installer/releases/settings-v3/notes.txt'):
            with self.subTest(private_content_path=relative):
                path = self.root / relative
                saved = path.read_bytes() if path.exists() else None
                write(self.root, relative, b'-----BEGIN ' + b'RSA PRIVATE KEY-----\nfixture')
                try:
                    with self.assertRaises(pack.Refusal):
                        pack.package_release(self.root, self.output)
                    self.assertFalse(self.output.exists())
                finally:
                    if saved is None:
                        path.unlink()
                    else:
                        path.write_bytes(saved)

    def test_package_does_not_write_through_a_replaced_output_directory(self):
        outside = self.scratch / 'outside-output'
        outside.mkdir(mode=0o700)
        saved = self.scratch / 'output-saved'
        original = pack._output_preflight
        calls = 0
        def swap_after_check(root, output):
            nonlocal calls
            calls += 1
            original(root, output)
            if calls == 3:
                output.rename(saved)
                output.symlink_to(outside, target_is_directory=True)
        with patch.object(pack, '_output_preflight', side_effect=swap_after_check):
            with self.assertRaises(pack.Refusal):
                pack.package_release(self.root, self.output)
        self.assertEqual(inventory(outside), {}, 'No file may be written through the replaced output directory')
        self.assertEqual(inventory(saved), {})


if __name__ == '__main__':
    unittest.main()
