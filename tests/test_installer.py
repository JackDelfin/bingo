"""Public installer tests. All subprocesses see an allowlisted fake-only PATH."""
import fcntl
import hashlib
import importlib.util
from unittest.mock import patch
import json
import os
from pathlib import Path
import pty
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(os.environ['TMPDIR'])
CURRENT_RELEASE = ROOT / 'installer/releases/settings-v3'
LEGACY_PAYLOAD_HASH = '198ba57eaf14633986011dbe03d6876c743d198312693295a843f55fe0b50aa8'
CURRENT_PAYLOAD_HASH = 'e90e4c48a91faf12ee41c46021668ff48d131a576edde277a33ebf44354c2c70'


def publication_faults():
    frontend = {'80/tcp': [{'HostIp': '', 'HostPort': '3000'}]}
    invalid = [None, {}, [], False, 0, '',
               {'80/tcp': [{'HostIp': '', 'HostPort': '3001'}]},
               {'80/udp': [{'HostIp': '', 'HostPort': '3000'}]},
               {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '3000'}]},
               {'80/tcp': [{'HostIp': '::', 'HostPort': '3000'}]},
               {'80/tcp': frontend['80/tcp'] * 2},
               {**frontend, '81/tcp': [{'HostIp': '', 'HostPort': '3001'}]}]
    for field in ('HostConfig', 'NetworkSettings'):
        for ports in invalid:
            yield 'frontend', field, ports
    for short, port in (('db', '27017/tcp'), ('backend', '8999/tcp')):
        for ports in ([], False, 0, '', {port: []}, {port: [{'HostIp': '', 'HostPort': '3000'}]}):
            yield short, 'NetworkSettings', ports


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='single-bingo-', dir=SCRATCH)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('bash', 'dirname', 'python3'):
            target = sys.executable if name == 'python3' else shutil.which(name)
            self.assertIsNotNone(target)
            (self.bin / name).symlink_to(target)
        for name in ('docker', 'ssh', 'sudo', 'apt-get', 'podman', 'curl', 'sleep'):
            p = self.bin / name
            p.write_text('#!/bin/sh\nexit 123\n')
            p.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=str(self.bin),
                        PYTHONDONTWRITEBYTECODE='1', PYTHONOPTIMIZE=str(sys.flags.optimize),
                        XDG_DATA_HOME=str(self.home / '.local/share'))
        self.env.pop('XDG_STATE_HOME', None)
        self.target = self.home / '.local/share/venue-bingo'
        self.state_path = self.root / 'state.json'
        self.log = self.root / 'runtime.jsonl'
        self.state: dict = {k: {} for k in ('tags', 'images', 'containers', 'networks', 'volumes')}
        self.save()
        self.env.update(FAKE_STATE=str(self.state_path), FAKE_LOG=str(self.log))
        shutil.copyfile(ROOT / 'tests/fake_podman.py', self.bin / 'podman')
        (self.bin / 'podman').chmod(0o755)
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport sys\nprint(\'{"games": []}\')\nif "--write-out" in sys.argv: print("200")\n')
        (self.bin / 'sleep').write_text('#!/bin/sh\nexit 0\n')

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def commands(self):
        return [json.loads(row) for row in self.log.read_text().splitlines()] if self.log.exists() else []

    def mutations(self):
        return [a for a in self.commands() if a[0] in {'build', 'pull', 'tag', 'run', 'start', 'stop', 'rm'} or len(a) > 1 and a[1] in {'create', 'export', 'rm'}]

    def run_cli(self, *args, repo=ROOT):
        return subprocess.run([str(self.bin / 'bash'), str(repo / 'install.sh'), *map(str, args)],
                              env=self.env, text=True, capture_output=True, timeout=60)

    def legacy_checkout(self):
        """Select the preserved v1 authorities in a scratch-only checkout."""
        repo = self.root / 'legacy-checkout'
        shutil.copytree(ROOT / 'installer', repo / 'installer')
        shutil.copyfile(ROOT / 'install.sh', repo / 'install.sh')
        bootstrap = repo / 'installer/bootstrap.py'
        text = bootstrap.read_text()
        text = text.replace("RELEASE = HERE / 'releases/settings-v3'", 'RELEASE = HERE')
        text = text.replace("PAYLOAD_HASH = '" + CURRENT_PAYLOAD_HASH + "'",
                            "PAYLOAD_HASH = '" + LEGACY_PAYLOAD_HASH + "'")
        text = text.replace("FRESH_HASH = '03b5b91fdc60d38fbb6767bec6af14fbe60219cb5f0aea6d86a451ea4a1c6631'",
                            "FRESH_HASH = '4fe7b64f62446f5dcc1904950d65726df0abd090b560e32ec1966de38f9c519b'")
        bootstrap.write_text(text)
        return repo

    def target_python(self, path, *args, guard=None):
        script = ('import json,runpy,sys; '
                  'print("ACTUAL_TARGET_FLAGS="+json.dumps({"optimize":sys.flags.optimize,'
                  '"ignore_environment":sys.flags.ignore_environment,"isolated":sys.flags.isolated,'
                  '"dont_write_bytecode":sys.flags.dont_write_bytecode,"debug":__debug__}),flush=True); '
                  + ('runpy.run_path(' + repr(str(guard)) + '); ' if guard else '')
                  + 'sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")')
        result = subprocess.run([str(self.bin / 'python3'), '-B', '-c', script, str(path), *map(str, args)],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=60)
        flags = [json.loads(line.split('=', 1)[1]) for line in result.stdout.splitlines()
                 if line.startswith('ACTUAL_TARGET_FLAGS=')]
        self.assertEqual(flags, [{'optimize': sys.flags.optimize, 'ignore_environment': 0, 'isolated': 0,
                                 'dont_write_bytecode': 1, 'debug': not bool(sys.flags.optimize)}])
        print('TARGET=' + str(path.name) + ' ACTUAL_TARGET_FLAGS=' + json.dumps(flags[0]), flush=True)
        return result

    def test_default_release_ships_audio_for_fresh_and_same_command_update(self):
        result = self.run_cli('--no-desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        frontend = [a for a in self.commands() if a[0] == 'build' and 'frontend' in a[-1]]
        self.assertEqual(Path(frontend[0][-1]), CURRENT_RELEASE / 'source/frontend',
                         'Fresh install must build the versioned Settings and audio source')
        self.assertEqual(Path(frontend[0][frontend[0].index('-f') + 1]), CURRENT_RELEASE / 'build/Dockerfile.frontend')
        initial = json.loads(self.state_path.read_text())
        for args in [('--no-desktop',), ('--offline', '--no-desktop')]:
            self.log.unlink()
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(any(a[0] == 'pull' for a in self.commands()))
            frontend = [a for a in self.commands() if a[0] == 'build' and 'frontend' in a[a.index('-f') + 1]]
            self.assertEqual(len(frontend), 1)
            stage = Path(frontend[0][-1])
            expected = {p.relative_to(CURRENT_RELEASE / 'payload').as_posix(): p.read_bytes()
                        for p in (CURRENT_RELEASE / 'payload').rglob('*') if p.is_file()}
            self.assertEqual({p.relative_to(stage).as_posix(): p.read_bytes()
                              for p in stage.rglob('*') if p.is_file()}, expected,
                             'Child updater must stage exactly the selected Settings and audio payload')
            self.assertEqual(len(list((stage / 'frontend-dist/audio/calls').glob('*.mp3'))), 75)
            for path in (self.target / '.venue-install/receipt.json',
                         self.home / '.local/state/venue-bingo/operation.json'):
                self.assertEqual(json.loads(path.read_text())['payload_sha256'], CURRENT_PAYLOAD_HASH)
            self.log.unlink()
            checked = self.target_python(ROOT / 'installer/bootstrap.py', '--check')
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(self.mutations(), [])
        after = json.loads(self.state_path.read_text())
        self.assertEqual(initial['containers']['venue-bingo-db'], after['containers']['venue-bingo-db'])
        self.assertEqual(initial['volumes'], after['volumes'])
        self.assertEqual(initial['networks'], after['networks'])

    def test_versioned_release_matches_finished_audio_and_preserved_base(self):
        def files(root):
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in root.rglob('*') if p.is_file()}
        source = files(ROOT / 'installer/releases/controller-audio-v2/source')
        overlay = ['frontend/' + name for name in (
                   'package.json', 'package-lock.json', 'vite.config.ts', 'tsconfig.json',
                   'tsconfig.app.json', 'tsconfig.node.json', 'eslint.config.js', 'index.html',
                   'Dockerfile', '.dockerignore', '.prettierrc.json')
                   if (ROOT / 'source/frontend' / name).is_file()]
        for sub in ('src', 'scripts', 'public/audio'):
            overlay.extend('frontend/' + p.relative_to(ROOT / 'source/frontend').as_posix()
                           for p in (ROOT / 'source/frontend' / sub).rglob('*') if p.is_file())
        for name in overlay:
            source[name] = hashlib.sha256((ROOT / 'source' / name).read_bytes()).hexdigest()
        self.assertEqual(files(CURRENT_RELEASE / 'source'), source,
                         'Only the reviewed Settings/audio frontend overlay may differ from the frozen audio release')
        for sub in ('assets', 'build'):
            self.assertEqual(files(CURRENT_RELEASE / sub), files(ROOT / 'installer' / sub))
        payload = files(ROOT / 'installer/payload')
        payload = {name: digest for name, digest in payload.items()
                   if not name.startswith('frontend-dist/') and name != 'files.sha256'}
        selected = files(CURRENT_RELEASE / 'payload')
        self.assertEqual(selected.pop('files.sha256'), CURRENT_PAYLOAD_HASH)
        manifest = ''.join(digest + '  ' + name + '\n' for name, digest in sorted(selected.items())).encode()
        self.assertEqual((CURRENT_RELEASE / 'payload/files.sha256').read_bytes(), manifest,
                         'Pinned manifest must cover the exact shipped payload bytes and membership')
        self.assertEqual({name: digest for name, digest in selected.items()
                          if not name.startswith('frontend-dist/')}, payload,
                         'Frozen backend and updater must be byte-identical')
        # Public source ZIPs ship the pinned canonical payload, not generated root dist.
        finished = ROOT / 'source/frontend/dist'
        if finished.exists():
            self.assertEqual(files(CURRENT_RELEASE / 'payload/frontend-dist'), files(finished),
                             'Shipped frontend must match the finished workspace dist when present')
        calls = CURRENT_RELEASE / 'source/frontend/public/audio/calls'
        metadata = json.loads((calls / 'metadata.json').read_text())
        self.assertEqual({p.name for p in calls.glob('*.mp3')}, {str(n) + '.mp3' for n in range(1, 76)})
        self.assertEqual(len(metadata['clips']), 75)
        self.assertEqual(metadata['generation']['generator_sha256'],
                         hashlib.sha256((CURRENT_RELEASE / 'source/frontend/scripts/generate-callouts.py').read_bytes()).hexdigest())
        for clip in metadata['clips']:
            self.assertEqual(hashlib.sha256((calls / clip['file']).read_bytes()).hexdigest(), clip['sha256'])
            self.assertEqual((calls / clip['file']).stat().st_size, clip['bytes'])
        self.assertEqual(sum(clip['bytes'] for clip in metadata['clips']), metadata['total_audio_bytes'])
        self.assertFalse(any(part in {'.git', 'node_modules', '__pycache__'} for name in source for part in Path(name).parts))

    def test_selected_release_tampering_never_falls_back_to_legacy(self):
        repo = self.root / 'checkout'
        shutil.copytree(ROOT / 'installer', repo / 'installer')
        shutil.copyfile(ROOT / 'install.sh', repo / 'install.sh')
        release = repo / CURRENT_RELEASE.relative_to(ROOT)
        cases = [('payload/frontend-dist/audio/calls/75.mp3', 'modify'),
                 ('payload/files.sha256', 'modify'), ('payload/release-tools/update.py', 'modify'),
                 ('payload/frontend-dist/unlisted.txt', 'add'), ('payload/frontend-dist/index.html', 'remove'),
                 ('fresh-files.json', 'modify'), ('source/frontend/src/game/useNumberCallouts.ts', 'modify'),
                 ('source/frontend/scripts/generate-callouts.py', 'modify'),
                 ('source/frontend/public/audio/calls/metadata.json', 'modify'),
                 ('source/frontend/public/audio/calls/unlisted.txt', 'add')]
        for name, kind in cases:
            with self.subTest(path=name, fault=kind):
                path = release / name
                original = path.read_bytes() if path.exists() else None
                try:
                    if kind == 'remove':
                        path.unlink()
                    else:
                        path.write_bytes((original or b'') + b'\ncorrupted\n')
                    self.log.unlink(missing_ok=True)
                    result = self.run_cli('--check', repo=repo)
                    self.assertNotEqual(result.returncode, 0, 'Tampered selected release was accepted')
                    self.assertEqual(self.mutations(), [])
                    self.assertFalse(self.target.exists())
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)
        moved = release.with_name('saved-release')
        release.rename(moved)
        try:
            for kind in ('missing', 'linked'):
                with self.subTest(release=kind):
                    if kind == 'linked':
                        release.symlink_to(moved, target_is_directory=True)
                    self.log.unlink(missing_ok=True)
                    result = self.run_cli('--check', repo=repo)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(self.mutations(), [])
                    self.assertFalse(self.target.exists())
        finally:
            release.unlink(missing_ok=True)
            moved.rename(release)

    def test_unknown_release_authority_refuses_before_update_or_refresh(self):
        self.assertEqual(self.run_cli().returncode, 0)
        for path in (self.target / '.venue-install/receipt.json',
                     self.home / '.local/state/venue-bingo/operation.json'):
            original = path.read_bytes()
            value = json.loads(original)
            value['payload_sha256'] = 'e' * 64
            bad = json.dumps(value).encode()
            path.write_bytes(bad)
            try:
                for args in [('--check',), ('--no-desktop',), ('--desktop-only',)]:
                    with self.subTest(state=path.name, args=args):
                        path.write_bytes(bad)
                        self.log.unlink(missing_ok=True)
                        result = self.run_cli(*args)
                        self.assertNotEqual(result.returncode, 0, 'Unknown release pin was accepted')
                        self.assertEqual(self.mutations(), [])
                        self.assertEqual(path.read_bytes(), bad)
            finally:
                path.write_bytes(original)

    def test_podman49_blank_wildcard_allows_fresh_check_and_update(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        result = self.run_cli('--no-desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        before = json.loads(self.state_path.read_text())
        for args in [('--check',), ('--offline', '--no-desktop'), ('--check',)]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(self.state_path.read_text())
        self.assertEqual(before['containers']['venue-bingo-db'], after['containers']['venue-bingo-db'])
        self.assertEqual(before['volumes'], after['volumes'])

    def test_check_refuses_malformed_falsey_portbindings_without_mutation(self):
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        baseline = self.state_path.read_text()
        receipt = self.target / '.venue-install/receipt.json'
        original_receipt = receipt.read_bytes()
        for short in ('db', 'backend'):
            for ports in ([], False, 0, ''):
                with self.subTest(container=short, malformed=repr(ports)):
                    self.state = json.loads(baseline)
                    self.state['containers']['venue-bingo-' + short]['HostConfig']['PortBindings'] = ports
                    self.save()
                    self.log.unlink(missing_ok=True)
                    result = self.target_python(ROOT / 'installer/bootstrap.py', '--check')
                    self.assertNotEqual(result.returncode, 0, 'Malformed PortBindings accepted')
                    self.assertEqual(self.mutations(), [])
                    self.assertEqual(receipt.read_bytes(), original_receipt)
        for ports in (None, {}):
            self.state = json.loads(baseline)
            for short in ('db', 'backend'):
                self.state['containers']['venue-bingo-' + short]['HostConfig']['PortBindings'] = ports
            self.save()
            self.log.unlink(missing_ok=True)
            result = self.target_python(ROOT / 'installer/bootstrap.py', '--check')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.mutations(), [])

    def test_check_crosschecks_actual_publications_and_allows_unpublished_exposures(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        baseline = self.state_path.read_text()
        for short, field, ports in publication_faults():
            with self.subTest(container=short, field=field, ports=ports):
                self.state = json.loads(baseline)
                key = 'PortBindings' if field == 'HostConfig' else 'Ports'
                self.state['containers']['venue-bingo-' + short][field][key] = ports
                self.save()
                self.log.unlink(missing_ok=True)
                result = self.target_python(ROOT / 'installer/bootstrap.py', '--check')
                self.assertNotEqual(result.returncode, 0, 'Contradictory or malformed publication accepted')
                self.assertEqual(self.mutations(), [])
        for host_ip in ('', '0.0.0.0'):
            for actual_ip in ('', '0.0.0.0'):
                self.state = json.loads(baseline)
                frontend = self.state['containers']['venue-bingo-frontend']
                frontend['HostConfig']['PortBindings']['80/tcp'][0]['HostIp'] = host_ip
                frontend['NetworkSettings']['Ports']['80/tcp'][0]['HostIp'] = actual_ip
                frontend['NetworkSettings']['Ports']['81/tcp'] = None
                self.save()
                self.log.unlink(missing_ok=True)
                result = self.target_python(ROOT / 'installer/bootstrap.py', '--check')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.mutations(), [])

    def test_store_identity_survives_telemetry_but_refuses_changed_or_invalid_store(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.state = json.loads(self.state_path.read_text())
        self.state['used_bytes'] = 987654321
        self.save()
        result = self.run_cli('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.target / '.venue-install/receipt.json'
        original_receipt = path.read_bytes()
        expected = {'graphRoot': '/fake/private/store', 'runRoot': '/fake/private/run', 'graphDriverName': 'overlay'}
        self.assertEqual(json.loads(original_receipt)['store'], expected)
        baseline = json.loads(self.state_path.read_text())
        invalid = [None, [], {}, {**expected, 'graphRoot': None}, {**expected, 'runRoot': 42},
                   {**expected, 'graphDriverName': ''}, {**expected, 'graphRoot': 'relative'}]
        changed = [{**expected, key: value} for key, value in
                   [('graphRoot', '/different/store'), ('runRoot', '/different/run'), ('graphDriverName', 'vfs')]]
        for store in [*changed, *invalid]:
            with self.subTest(boundary='changed-or-invalid-runtime-store'):
                self.state = {**baseline, 'store': store}
                self.save()
                self.log.unlink(missing_ok=True)
                result = self.run_cli('--offline')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])
                self.assertTrue(path.read_bytes() == original_receipt, 'Receipt changed on refusal')
        self.state = baseline
        self.save()
        for store in [*invalid, {**expected, 'imageStore': {'number': 0}}]:
            with self.subTest(boundary='invalid-recorded-store-schema'):
                receipt = json.loads(original_receipt)
                receipt['store'] = store
                path.write_text(json.dumps(receipt))
                self.log.unlink(missing_ok=True)
                result = self.run_cli('--offline')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])
        path.write_bytes(original_receipt)

    def test_fresh_public_cards_are_readable_to_nonroot_backend_with_private_parents(self):
        self.assertIn('USER node', (ROOT / 'installer/build/Dockerfile.backend').read_text())
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        cards = self.target / 'reactbingo/backend/cards.csv'
        self.assertEqual(cards.read_bytes(), (ROOT / 'installer/source/backend/cards.csv').read_bytes())
        self.assertEqual(cards.stat().st_mode & 0o777, 0o644)
        for directory in (self.target, cards.parent, cards.parent.parent, self.target / '.venue-install'):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        state = json.loads(self.state_path.read_text())
        backend = state['containers']['venue-bingo-backend']
        self.assertEqual(backend['Config']['User'], 'node')
        self.assertTrue(backend['State']['Running'])
        self.assertFalse(backend['Mounts'][0]['RW'])
        private = self.target / 'private-links.txt'
        private.write_bytes(os.urandom(64))
        private.chmod(0o600)
        # A recognized legacy installation may have customized public cards
        # and modes. Updating must preserve them, never chmod/chown/recopy.
        (self.target / '.venue-install/receipt.json').unlink()
        (self.home / '.local/state/venue-bingo/installation.json').unlink()
        cards.write_bytes(cards.read_bytes() + b'\n')
        cards.chmod(0o604)
        (self.target / 'reactbingo/nginx.conf').chmod(0o400)
        def snapshot():
            return {str(p.relative_to(self.target)): (p.read_bytes(), p.stat().st_mode & 0o777, p.stat().st_uid, p.stat().st_gid)
                    for p in self.target.rglob('*') if p.is_file() and '.venue-install' not in p.parts and not any(x.startswith('.bingo-update-') for x in p.parts)}
        saved = snapshot()
        result = self.run_cli('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(snapshot() == saved, 'Existing bytes/modes/ownership changed')
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['containers']['venue-bingo-db'], state['containers']['venue-bingo-db'])
        self.assertEqual(final['volumes'], state['volumes'])
        # Prove the fake really denies the former 0600 bind-file boundary;
        # even failure must leave that existing private file untouched.
        cards.chmod(0o600)
        (self.target / '.venue-install/receipt.json').unlink()
        (self.home / '.local/state/venue-bingo/installation.json').unlink()
        saved = snapshot()
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(snapshot() == saved, 'Failed update altered existing files')
        final = json.loads(self.state_path.read_text())
        self.assertFalse(final['containers']['venue-bingo-backend']['State']['Running'])
        self.assertEqual(final['containers']['venue-bingo-db'], state['containers']['venue-bingo-db'])

    def test_fresh_start_failure_keeps_database_and_volume_and_refuses_retry(self):
        self.state['fail_app_run'] = 'once'
        self.save()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        before = json.loads(self.state_path.read_text())
        self.assertIn('venue-bingo-data', before['volumes'])
        self.assertIn('venue-bingo-db', before['containers'])
        self.log.unlink()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('recovery', result.stderr.lower())
        self.assertEqual(self.mutations(), [])
        after = json.loads(self.state_path.read_text())
        self.assertEqual(before['volumes'], after['volumes'])
        self.assertEqual(before['containers']['venue-bingo-db'], after['containers']['venue-bingo-db'])

    def test_postbuild_collision_never_publishes_or_adopts_even_owned_volume(self):
        self.state['collide_after_build'] = True
        self.save()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['volumes']['venue-bingo-data']['CreatedAt'], 'unexpected-volume')
        self.assertEqual(final['containers'], {})
        self.assertFalse(any(a[0] == 'tag' for a in self.commands()))
        self.assertFalse(self.target.exists())

    def test_documented_manual_rollback_retains_adapter_and_original_evidence(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        original = json.loads(self.state_path.read_text())
        configs = {name: ((self.target / name).read_bytes(), (self.target / name).stat().st_mode)
                   for name in ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv')}
        self.assertEqual(self.run_cli('--offline', '--no-desktop').returncode, 0)
        backups = list(self.target.glob('.bingo-update-*'))
        self.assertEqual(len(backups), 1)
        backup = backups[0]
        self.assertEqual((backup / 'update.py').read_bytes(), (ROOT / 'installer/payload/release-tools/update.py').read_bytes())
        metadata = json.loads((backup / 'metadata.json').read_text())
        self.state = json.loads(self.state_path.read_text())
        for short in ('backend', 'frontend'):
            self.assertNotEqual(self.state['containers']['venue-bingo-' + short]['Image'], metadata['old_images'][short])
        self.state['fail_build'] = True
        self.save()
        self.assertNotEqual(self.run_cli('--offline', '--no-desktop').returncode, 0)
        journal = self.home / '.local/state/venue-bingo/operation.json'
        self.assertEqual(json.loads(journal.read_text())['status'], 'failed-needs-recovery')
        evidence = [journal, self.target / '.venue-install/receipt.json',
                    self.home / '.local/state/venue-bingo/installation.json',
                    *[p for p in backup.rglob('*') if p.is_file()]]
        saved = {p: p.read_bytes() for p in evidence}
        shutil.copytree(ROOT / 'installer', self.home / 'bingo/installer')
        self.log.unlink(missing_ok=True)
        raw = subprocess.run([str(self.bin / 'bash'), str(backup / 'rollback.sh')],
                             env=self.env, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(raw.returncode, 0)
        self.assertIn('Only the frontend may publish host port 3000', raw.stderr)
        self.assertEqual(self.mutations(), [])
        # Probe sys.flags in the actual pinned updater main while executing the
        # documented shell command unchanged (apart from its explicit backup).
        python = self.bin / 'python3'
        python.unlink()
        probe = ('import json,sys\ndef probe(frame,event,arg):\n'
                 ' if event=="call" and frame.f_code.co_name=="main" and frame.f_code.co_filename.endswith("/update.py"):\n'
                 '  print("MANUAL_UPDATER_FLAGS="+json.dumps({"optimize":sys.flags.optimize,"ignore_environment":sys.flags.ignore_environment,"isolated":sys.flags.isolated,"dont_write_bytecode":sys.flags.dont_write_bytecode,"debug":__debug__}),flush=True)\n'
                 '  sys.setprofile(None)\nsys.setprofile(probe)\n')
        python.write_text('#!' + sys.executable + '\nimport os,sys\na=sys.argv[1:]\n'
                          'if a[:2]==["-B","-c"]: a=["-B","-c",' + repr(probe) + '+a[2],*a[3:]]\n'
                          'os.execv(' + repr(sys.executable) + ',[' + repr(sys.executable) + ',*a])\n')
        python.chmod(0o755)
        api_log = self.root / 'rollback-api-verified'
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n'
            's=json.loads(Path(' + repr(str(self.state_path)) + ').read_text())\n'
            'expected=' + repr(metadata['old_images']) + '\n'
            'if any(s["containers"]["venue-bingo-"+k]["Image"]!=v or not s["containers"]["venue-bingo-"+k]["State"]["Running"] for k,v in expected.items()): sys.exit(22)\n'
            'Path(' + repr(str(api_log)) + ').write_text("old apps running; fake API verified")\n'
            'print(\'{"games": []}\')\nif "--write-out" in sys.argv: print("200")\n')
        documented = re.search(r'<!-- podman49-manual-rollback -->\s*```bash\n(.*?)\n```',
                               (ROOT / 'docs/INSTALL.md').read_text(), re.S)
        # Before this correction the documented private backup instructions
        # lead to rollback.sh. Keep that behavioral RED rather than a text-only assertion.
        command = documented.group(1).replace('/absolute/path/to/installation/.bingo-update-EXACT-SUFFIX', str(backup)) if documented else 'bash "' + str(backup / 'rollback.sh') + '"'
        result = subprocess.run([str(self.bin / 'bash'), '-c', command], env=self.env,
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Rollback completed; old apps verified.', result.stdout)
        flags = [json.loads(line.split('=', 1)[1]) for line in result.stdout.splitlines() if line.startswith('MANUAL_UPDATER_FLAGS=')]
        self.assertEqual(flags, [{'optimize': sys.flags.optimize, 'ignore_environment': 0, 'isolated': 0,
                                 'dont_write_bytecode': 1, 'debug': not bool(sys.flags.optimize)}])
        print('MANUAL_UPDATER_FLAGS=' + json.dumps(flags[0]), flush=True)
        after = json.loads(self.state_path.read_text())
        for short in ('backend', 'frontend'):
            self.assertEqual(after['containers']['venue-bingo-' + short]['Image'], metadata['old_images'][short])
            self.assertEqual(after['tags']['localhost/reactbingo-' + short + ':venue'], metadata['old_images'][short])
        self.assertTrue(api_log.is_file())
        for key in ('volumes', 'networks'):
            self.assertEqual(after[key], original[key])
        self.assertEqual(after['containers']['venue-bingo-db'], original['containers']['venue-bingo-db'])
        for name, snapshot in configs.items():
            self.assertEqual(((self.target / name).read_bytes(), (self.target / name).stat().st_mode), snapshot)
        for path, data in saved.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertFalse(any(a[0] in ('build', 'pull', 'start') or a[:2] in (['volume', 'create'], ['volume', 'export']) or a[0] in ('stop', 'rm', 'run') and 'venue-bingo-db' in a for a in self.mutations()))
        self.log.unlink()
        self.assertNotEqual(self.run_cli('--offline').returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_podman49_blank_wildcard_automatic_rollback_keeps_database(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        self.test_updater_failure_propagates_even_after_successful_app_rollback()

    def test_updater_failure_propagates_even_after_successful_app_rollback(self):
        self.assertEqual(self.run_cli().returncode, 0)
        before = json.loads(self.state_path.read_text())
        before['fail_app_run'] = 'once'
        self.state = before
        self.save()
        self.log.unlink()
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed', result.stderr.lower())
        after = json.loads(self.state_path.read_text())
        self.assertEqual(before['volumes'], after['volumes'])
        self.assertEqual(before['networks'], after['networks'])
        self.assertEqual(before['containers']['venue-bingo-db'], after['containers']['venue-bingo-db'])
        for short in ('backend', 'frontend'):
            tag = 'localhost/reactbingo-' + short + ':venue'
            self.assertEqual(before['tags'][tag], after['tags'][tag])
        private_log = (self.target / '.venue-install/last-update.log').read_text()
        self.assertTrue('old app images and app readiness restored. Update was NOT successful.' in private_log, 'Expected app-only rollback did not verify readiness')
        self.log.unlink()
        self.assertNotEqual(self.run_cli('--offline').returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_existing_foreign_missing_or_partial_runtime_is_refused_before_mutation(self):
        self.assertEqual(self.run_cli().returncode, 0)
        baseline = self.state_path.read_text()
        cases = ('missing-db', 'missing-app', 'missing-volume', 'missing-network', 'foreign-db', 'foreign-volume', 'foreign-network', 'stopped-db', 'wrong-mount', 'replaced-volume')
        for case in cases:
            with self.subTest(case=case):
                self.state = json.loads(baseline)
                if case == 'missing-db': del self.state['containers']['venue-bingo-db']
                elif case == 'missing-app': del self.state['containers']['venue-bingo-frontend']
                elif case == 'missing-volume': self.state['volumes'].clear()
                elif case == 'missing-network': self.state['networks'].clear()
                elif case == 'foreign-db': self.state['containers']['venue-bingo-db']['Config']['Labels'] = {}
                elif case == 'foreign-volume': self.state['volumes']['venue-bingo-data']['Labels'] = {}
                elif case == 'foreign-network': self.state['networks']['venue-bingo']['labels'] = {}
                elif case == 'stopped-db': self.state['containers']['venue-bingo-db']['State']['Running'] = False
                elif case == 'wrong-mount': self.state['containers']['venue-bingo-backend']['Mounts'][0]['Source'] = '/foreign/cards.csv'
                elif case == 'replaced-volume': self.state['volumes']['venue-bingo-data']['CreatedAt'] = 'replacement'
                self.save()
                self.log.unlink(missing_ok=True)
                result = self.run_cli('--offline')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])

    def test_existing_api_check_rejects_html_redirect_wrong_shape_and_numeric_ids(self):
        self.assertEqual(self.run_cli().returncode, 0)
        for body, status in [('<html>not-api</html>', '200'), ('{"games":[]}', '302'), ('{}', '200'), ('[]', '200'), ('{"games":[42]}', '200'), ('{"games":[],"extra":1}', '200')]:
            with self.subTest(case='invalid-api'):
                (self.bin / 'curl').write_text('#!/usr/bin/env python3\nprint(' + repr(body) + ')\nprint(' + repr(status) + ')\n')
                self.log.unlink(missing_ok=True)
                result = self.run_cli('--check')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])

    def test_existing_directory_lock_refuses_without_app_update(self):
        self.assertEqual(self.run_cli().returncode, 0)
        fd = os.open(self.target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.log.unlink()
            result = self.run_cli('--offline')
        finally:
            os.close(fd)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_explicit_target_with_spaces_survives_same_command_update(self):
        target = self.home / 'venue with spaces'
        self.assertEqual(self.run_cli('--dir', target).returncode, 0)
        result = self.run_cli('--dir', target, '--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((target / 'bingo.sh').is_file())
        self.assertFalse(self.target.exists())
        self.log.unlink()
        result = self.run_cli('--dir', self.home / 'wrong', '--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_known_pinned_legacy_home_bingo_is_detected_without_state_pointer(self):
        # Model a legacy install without creating a modern app entry pointing
        # at the old target; refusing that orphaned entry is intentional.
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        legacy = self.home / 'bingo'
        self.target.rename(legacy)
        shutil.rmtree(legacy / '.venue-install')
        shutil.rmtree(self.home / '.local/state/venue-bingo')
        self.state = json.loads(self.state_path.read_text())
        for short in ('backend', 'frontend'):
            mount = self.state['containers']['venue-bingo-' + short]['Mounts'][0]
            mount['Source'] = mount['Source'].replace(str(self.target), str(legacy))
        self.save()
        result = self.run_cli('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Updated', result.stdout)
        pointer = json.loads((self.home / '.local/state/venue-bingo/installation.json').read_text())
        self.assertEqual(pointer['target'], str(legacy))
        self.assertFalse(self.target.exists())

    def test_home_bingo_checkout_is_not_a_live_installation(self):
        checkout = self.home / 'bingo'
        (checkout / '.git').mkdir(parents=True)
        shutil.copyfile(ROOT / 'installer/assets/bingo.sh', checkout / 'bingo.sh')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.target / '.venue-install/receipt.json').is_file())
        self.assertFalse((checkout / '.venue-install').exists())

    def test_missing_python_bootstraps_only_python_via_fake_interactive_apt(self):
        (self.bin / 'python3').unlink()
        packages = self.root / 'python-packages.jsonl'
        (self.bin / 'sudo').write_text('#!' + sys.executable + '\nimport sys,json\nfrom pathlib import Path\na=sys.argv[1:]\n'
                                      'with open(' + repr(str(packages)) + ',"a") as f:f.write(json.dumps(a)+"\\n")\n'
                                      'if a[:2] != ["--","apt-get"]:sys.exit(123)\n'
                                      'if "install" in a:Path(' + repr(str(self.bin / 'python3')) + ').symlink_to(' + repr(sys.executable) + ')\n')
        master, slave = pty.openpty()
        try:
            result = subprocess.run([str(self.bin / 'bash'), str(ROOT / 'install.sh')], env=self.env,
                                    stdin=slave, capture_output=True, text=True, timeout=60)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(x) for x in packages.read_text().splitlines()]
        self.assertEqual(calls, [['--', 'apt-get', 'update'], ['--', 'apt-get', 'install', 'python3']])
        self.assertTrue((self.target / 'bingo.sh').is_file())

    def test_missing_python_offline_and_check_fail_without_package_or_home_mutation(self):
        (self.bin / 'python3').unlink()
        for option in ('--offline', '--check'):
            result = self.run_cli(option)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Missing Python', result.stderr)
            self.assertEqual(list(self.home.iterdir()), [])

    def test_corrupt_bootstrap_dockerfile_refused_without_runtime_mutation(self):
        repo = self.root / 'checkout'
        shutil.copytree(ROOT / 'installer', repo / 'installer')
        shutil.copyfile(ROOT / 'install.sh', repo / 'install.sh')
        (repo / CURRENT_RELEASE.relative_to(ROOT) / 'build/Dockerfile.backend').write_text('FROM unsafe.invalid/unapproved\n')
        result = self.run_cli('--check', repo=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_recorded_installation_missing_receipt_is_not_adopted_as_legacy(self):
        self.assertEqual(self.run_cli().returncode, 0)
        (self.target / '.venue-install/receipt.json').unlink()
        self.log.unlink()
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])

    def test_linked_update_log_refused_before_updates_or_private_file_writes(self):
        self.assertEqual(self.run_cli().returncode, 0)
        protected = self.home / 'private.txt'
        protected.write_bytes(os.urandom(32))
        saved = protected.read_bytes()
        (self.target / '.venue-install/last-update.log').symlink_to(protected)
        self.log.unlink()
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(protected.read_bytes() == saved, 'A private file outside the installation was modified')
        self.assertEqual(self.mutations(), [])

    def test_check_existing_state_directory_never_creates_a_lock_file(self):
        state = self.home / '.local/state/venue-bingo'
        state.mkdir(parents=True, mode=0o700)
        before = sorted(str(p) for p in self.home.rglob('*'))
        self.run_cli('--check')
        self.assertEqual(sorted(str(p) for p in self.home.rglob('*')), before)
        self.assertEqual(self.mutations(), [])

    def test_missing_fresh_dependency_uses_only_interactive_fake_sudo_apt(self):
        spec = importlib.util.spec_from_file_location('installer_under_test', ROOT / 'installer/bootstrap.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        curl = self.bin / 'curl'
        original = curl.read_bytes()
        curl.unlink()
        packages = self.root / 'packages.jsonl'
        sudo = self.bin / 'sudo'
        sudo.write_text('#!' + sys.executable + '\nimport os,sys,json\nfrom pathlib import Path\na=sys.argv[1:]\n'
                        'with open(' + repr(str(packages)) + ',"a") as f: f.write(json.dumps(a)+"\\n")\n'
                        'if a[:2] != ["--","apt-get"]: sys.exit(123)\n'
                        'if "install" in a:\n p=Path(' + repr(str(curl)) + ');p.write_bytes(' + repr(original) + ');p.chmod(0o755)\n')
        with patch.dict(os.environ, self.env, clear=True), patch.object(module.sys.stdin, 'isatty', return_value=True):
            try:
                module.prerequisites(allow_install=True)
            except Exception as error:
                self.fail('Interactive dependency provisioning is missing: ' + type(error).__name__)
        calls = [json.loads(x) for x in packages.read_text().splitlines()]
        self.assertEqual(calls[0], ['--', 'apt-get', 'update'])
        self.assertEqual(calls[1][:3], ['--', 'apt-get', 'install'])
        self.assertIn('curl', calls[1])
        self.assertTrue(all('podman' not in a or a[1] == 'apt-get' for a in calls))

    def test_offline_missing_dependency_does_not_even_create_state(self):
        (self.bin / 'curl').unlink()
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing', result.stderr)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertEqual(self.mutations(), [])

    def test_failed_fresh_build_journals_and_refuses_retry_without_live_resources(self):
        self.state['fail_build'] = True
        self.save()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        journal = self.home / '.local/state/venue-bingo/operation.json'
        self.assertTrue(journal.is_file(), 'A failed fresh build must leave a durable journal')
        value = json.loads(journal.read_text())
        self.assertEqual(value['status'], 'failed-needs-recovery')
        self.assertEqual(value['target'], str(self.target))
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['containers'], {})
        self.assertEqual(final['volumes'], {})
        self.assertNotIn('localhost/reactbingo-backend:venue', final['tags'])
        self.log.unlink()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('recovery', result.stderr.lower())
        self.assertEqual(self.mutations(), [])

    def test_namespace_lock_refuses_different_target_before_runtime_work(self):
        state = self.home / '.local/state/venue-bingo'
        state.mkdir(parents=True, mode=0o700)
        with (state / 'namespace.lock').open('w') as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_cli('--dir', self.home / 'other target')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('lock', result.stderr.lower())
        self.assertEqual(self.mutations(), [])

    def test_same_command_rerun_updates_offline_preserving_database_and_private_files(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        initial = json.loads(self.state_path.read_text())
        private = self.target / 'private-links.txt'
        private.write_bytes(os.urandom(40))
        private.chmod(0o600)
        saved = {str(p.relative_to(self.target)): p.read_bytes() for p in self.target.rglob('*') if p.is_file() and '.venue-install' not in p.parts}
        self.env['XDG_DATA_HOME'] = str(self.home / 'changed-xdg')
        self.log.unlink()
        result = self.run_cli('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Updated', result.stdout)
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['containers']['venue-bingo-db'], initial['containers']['venue-bingo-db'])
        self.assertEqual(final['volumes'], initial['volumes'])
        self.assertEqual(final['networks'], initial['networks'])
        for name, content in saved.items():
            self.assertTrue((self.target / name).read_bytes() == content, 'Installed/private file changed')
        self.assertNotEqual(final['tags']['localhost/reactbingo-backend:venue'], initial['tags']['localhost/reactbingo-backend:venue'])
        self.assertEqual(len(list(self.target.glob('.bingo-update-*'))), 1)
        for a in self.mutations():
            self.assertNotEqual(a[0], 'pull')
            if a[0] == 'build':
                self.assertIn('--pull=never', a)
                self.assertIn('--network=none', a)
            self.assertFalse(a[0] in {'stop', 'rm'} and 'venue-bingo-db' in a)
        self.assertFalse((self.home / 'changed-xdg/venue-bingo').exists())
        self.log.unlink()
        result = self.run_cli('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('healthy', result.stdout)
        self.assertEqual(self.mutations(), [])

    def test_fresh_install_builds_both_candidates_before_publishing_and_attests_resources(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Installed', result.stdout)
        state = json.loads(self.state_path.read_text())
        self.assertEqual(set(state['containers']), {'venue-bingo-db', 'venue-bingo-backend', 'venue-bingo-frontend'})
        self.assertEqual((self.target / 'bingo.sh').read_bytes(), (ROOT / 'installer/assets/bingo.sh').read_bytes())
        self.assertEqual((self.target / 'reactbingo/nginx.conf').read_bytes(), (ROOT / 'installer/source/nginx.conf').read_bytes())
        commands = self.commands()
        builds = [a for a in commands if a[0] == 'build']
        self.assertEqual(len(builds), 2)
        last_build = max(i for i, a in enumerate(commands) if a[0] == 'build')
        first_publish = min(i for i, a in enumerate(commands) if a[0] in {'tag', 'run'} or a[:2] == ['volume', 'create'])
        self.assertLess(last_build, first_publish)
        for a in builds:
            self.assertIn('--pull=always', a)
            self.assertTrue(Path(a[-1]).is_relative_to(CURRENT_RELEASE / 'source'))
        db = state['containers']['venue-bingo-db']
        self.assertIn('GLIBC_TUNABLES=glibc.pthread.rseq=1', db['Config']['Env'])
        self.assertEqual(db['HostConfig']['PortBindings'], {})
        receipt = json.loads((self.target / '.venue-install/receipt.json').read_text())
        self.assertEqual(receipt['target'], str(self.target))
        self.assertEqual(receipt['db_id'], db['Id'])
        self.assertEqual(receipt['status'], 'installed')
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.target / '.venue-install/receipt.json').stat().st_mode & 0o777, 0o600)

    def test_fresh_check_refuses_every_resource_collision_even_owned(self):
        for kind, name in [('volumes', 'venue-bingo-data'), ('networks', 'venue-bingo'), *[('containers', 'venue-bingo-' + x) for x in ('db', 'backend', 'frontend')]]:
            with self.subTest(resource=name):
                self.state[kind][name] = {'Labels': {'org.reactbingo.venue': '1'}}
                self.save()
                result = self.run_cli('--check')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('collision', result.stderr.lower())
                self.assertEqual(self.mutations(), [])
                self.state[kind].clear()
        ident = 'sha256:' + 'e' * 64
        self.state['tags']['localhost/reactbingo-backend:venue'] = ident
        self.state['images'][ident] = {'Id': ident}
        self.save()
        result = self.run_cli('--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('collision', result.stderr.lower())

    def test_unknown_nonempty_target_is_never_adopted(self):
        self.target.mkdir(parents=True)
        (self.target / 'private-links.txt').write_text('private-fixture-not-displayed')
        result = self.run_cli('--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unrecognized', result.stderr.lower())
        self.assertEqual(self.mutations(), [])

    def test_offline_fresh_refuses_before_any_mutation(self):
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('offline', result.stderr.lower())
        self.assertEqual(self.mutations(), [])
        self.assertFalse(self.target.exists())

    def test_check_requires_usable_rootless_prerequisites(self):
        self.state['rootless'] = False
        self.save()
        result = self.run_cli('--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('rootless', result.stderr.lower())
        self.assertEqual(self.mutations(), [])
        self.state['rootless'] = True
        self.save()
        (self.bin / 'curl').unlink()
        result = self.run_cli('--check')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing', result.stderr)
        self.assertFalse(self.target.exists())

    def test_check_verifies_bundled_payload_without_runtime_mutation(self):
        result = self.run_cli('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Verified payload', result.stdout)
        self.assertIn('fresh', result.stdout)
        self.assertFalse(self.target.exists())

    def test_corrupt_payload_is_refused_before_runtime(self):
        repo = self.root / 'checkout'
        repo.mkdir()
        shutil.copytree(ROOT / 'installer', repo / 'installer')
        shutil.copyfile(ROOT / 'install.sh', repo / 'install.sh')
        (repo / CURRENT_RELEASE.relative_to(ROOT) / 'payload/backend-dist/server.js').write_text('corrupted')
        result = self.run_cli('--check', repo=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('payload', result.stderr.lower())

    def test_unsafe_explicit_targets_refused_without_creation(self):
        linked = self.home / 'linked'
        linked.symlink_to(self.root, target_is_directory=True)
        for target in ('relative', ROOT, ROOT / 'qa/install-target', self.home / 'bad:bind', linked / 'child'):
            with self.subTest(kind='unsafe-target'):
                result = self.run_cli('--check', '--dir', target)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('target', result.stderr.lower())
        self.assertFalse((linked / 'child').exists())

    def test_help_explains_same_command_without_runtime_or_home_mutation(self):
        result = self.run_cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--dir', result.stdout)
        self.assertIn('--offline', result.stdout)
        self.assertIn('--check', result.stdout)
        self.assertIn('rerun', result.stdout.lower())
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
