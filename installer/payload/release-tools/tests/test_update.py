#!/usr/bin/env python3
"""Transaction/guard tests against a fake runtime, never the live Podman store."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

TOOLS = Path(__file__).resolve().parents[1]
LABEL = {'org.reactbingo.venue': '1'}
BACKEND = 'localhost/reactbingo-backend:venue'
FRONTEND = 'localhost/reactbingo-frontend:venue'
MONGO = 'docker.io/library/mongo:8.0'
VALID_API = (200, b'{"games": []}', 'application/json')
INVALID_API = (
    ('redirect', 302, b'{"games": []}', 'application/json'),
    ('html', 200, b'<!doctype html><title>CAPABILITYSECRET</title>', 'text/html'),
    ('malformed-json', 200, b'{"games": [', 'application/json'),
    ('top-level-array', 200, b'[]', 'application/json'),
    ('missing-games', 200, b'{}', 'application/json'),
    ('not-an-array', 200, b'{"games": {}}', 'application/json'),
    ('not-string-items', 200, b'{"games": [7]}', 'application/json'),
)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bingo-update-tests-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bundle = self.root / 'release'
        self.bundle.mkdir()
        shutil.copytree(TOOLS, self.bundle / 'release-tools', ignore=shutil.ignore_patterns('__pycache__'))
        for name, text in [('backend-dist/server.js', '// compiled test fixture\n'), ('frontend-dist/index.html', '<!doctype html>\n')]:
            path = self.bundle / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
        self.manifest()
        self.install = self.root / 'existing bingo'
        (self.install / 'reactbingo/backend').mkdir(parents=True)
        shutil.copyfile(TOOLS / 'tests/bingo.sh', self.install / 'bingo.sh')
        (self.install / 'reactbingo/nginx.conf').write_text('# existing pinned config\n')
        (self.install / 'reactbingo/backend/cards.csv').write_text('existing cards\n')
        (self.install / 'private-links.txt').write_text('CAPABILITYSECRET\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        # Whitelist the harness executables: no fallback to a real container runtime.
        for name, target in [('python3', sys.executable), ('bash', shutil.which('bash')), ('dirname', shutil.which('dirname'))]:
            self.assertIsNotNone(target, 'Missing harness executable: ' + name)
            (self.bin / name).symlink_to(target)
        for name in ('docker', 'ssh'):
            path = self.bin / name
            path.write_text('#!/bin/sh\nexit 123\n')
            path.chmod(0o755)
        shutil.copyfile(TOOLS / 'tests/fake_podman.py', self.bin / 'podman')
        (self.bin / 'podman').chmod(0o755)
        for command in ['curl', 'sleep']:
            path = self.bin / command
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport sys\nprint(\'{"games": []}\')\nif "--write-out" in sys.argv: print("200")\n')
        self.state_path = self.root / 'state.json'
        self.log_path = self.root / 'commands.jsonl'
        old = ['sha256:' + c * 64 for c in 'abf']
        self.state = {
            'tags': dict(zip([BACKEND, FRONTEND, MONGO], old)),
            'images': {ident: {'Id': ident} for ident in old},
            'networks': {'venue-bingo': {'Labels': dict(LABEL)}},
            'volumes': {'venue-bingo-data': {'Labels': dict(LABEL)}},
            'containers': {},
        }
        for short, image in zip(['backend', 'frontend', 'db'], old):
            mounts = ([{'Type': 'volume', 'Name': 'venue-bingo-data', 'Destination': '/data/db'}] if short == 'db' else [{
                'Type': 'bind', 'Source': str(self.install / ('reactbingo/backend/cards.csv' if short == 'backend' else 'reactbingo/nginx.conf')),
                'Destination': '/app/cards.csv' if short == 'backend' else '/etc/nginx/nginx.conf',
            }])
            self.state['containers']['venue-bingo-' + short] = {
                'Id': 'old-' + short, 'Image': image,
                'Config': {'Labels': dict(LABEL), 'Env': ['GLIBC_TUNABLES=glibc.pthread.rseq=1'] if short == 'db' else []},
                'State': {'Running': True}, 'Mounts': mounts,
                'NetworkSettings': {'Networks': {'venue-bingo': {}}},
                'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '3000'}]} if short == 'frontend' else {}},
            }
        self.save_state()
        self.env = dict(os.environ, PATH=str(self.bin), FAKE_STATE=str(self.state_path), FAKE_LOG=str(self.log_path), PYTHONDONTWRITEBYTECODE='1')
        self.original_files = {str(p.relative_to(self.install)): p.read_bytes() for p in self.install.rglob('*') if p.is_file()}

    def manifest(self):
        rows = [hashlib.sha256(p.read_bytes()).hexdigest() + '  ' + p.relative_to(self.bundle).as_posix() for p in sorted(self.bundle.rglob('*')) if p.is_file() and p.name != 'files.sha256']
        (self.bundle / 'files.sha256').write_text('\n'.join(rows) + '\n')

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state))

    def run_update(self):
        return subprocess.run(['bash', str(self.bundle / 'release-tools/install-update.sh'), str(self.install)], env=self.env, text=True, capture_output=True)

    def commands(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()] if self.log_path.exists() else []

    def mutations(self):
        return [args for args in self.commands() if args[0] in {'build', 'tag', 'run', 'stop', 'rm', 'start', 'rename', 'create'} or args[:2] == ['volume', 'export'] or ('create' in args or 'rm' in args)]

    def backups(self):
        return sorted(self.install.glob('.bingo-update-*'))

    def real_http_readiness(self):
        # Only this exact URL is rewritten, and only to an ephemeral loopback server.
        real_curl = os.environ.get('TEST_REAL_CURL') or shutil.which('curl')
        self.assertIsNotNone(real_curl, 'Real curl is required for HTTP readiness tests')
        self.http_responses: dict[str, tuple[int, bytes, str]] = {'new': VALID_API, 'old': VALID_API}
        self.http_requests = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                state = json.loads(case.state_path.read_text())
                generation = 'new' if state['tags'][BACKEND] == 'sha256:' + 'c' * 64 else 'old'
                status_code, body, content_type = case.http_responses[generation]
                case.http_requests.append((generation, self.path, status_code))
                self.send_response(status_code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                if status_code == 302:
                    self.send_header('Location', '/not-an-api')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()

        def close_server():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close_server)
        url = 'http://127.0.0.1:' + str(server.server_port) + '/api/list'
        (self.bin / 'curl').write_text(
            '#!/usr/bin/env python3\nimport os,sys\n'
            'expected="http://127.0.0.1:3000/api/list"\nargs=sys.argv[1:]\n'
            'if args.count(expected) != 1 or any(a.startswith(("http:","https:")) and a != expected for a in args): sys.exit(123)\n'
            'args=[' + repr(url) + ' if a == expected else a for a in args]\n'
            'os.execv(' + repr(real_curl) + ', [' + repr(real_curl) + ', "--disable", "--noproxy", "*", *args])\n'
        )

    def rollback_without_delay(self, backup):
        # Exercise the saved standalone main; remove retry sleeps only in this child.
        code = 'import runpy,sys,time; time.sleep=lambda _:None; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name="__main__")'
        return subprocess.run(['python3', '-B', '-c', code, str(backup / 'update.py'), '--rollback', str(backup)],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def assert_preserved(self):
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])
        self.assertEqual(final['volumes'], self.state['volumes'])
        self.assertEqual(final['networks'], self.state['networks'])
        for name, content in self.original_files.items():
            self.assertEqual((self.install / name).read_bytes(), content)

    def test_real_http_empty_games_api_allows_update_and_standalone_rollback(self):
        self.real_http_readiness()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        self.assertTrue((backup / 'update-complete').is_file())
        shutil.rmtree(self.bundle)
        result = self.rollback_without_delay(backup)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Rollback completed;', result.stdout)
        self.assertTrue(self.http_requests)
        self.assertTrue(all(path == '/api/list' for _, path, _ in self.http_requests))
        self.assert_preserved()

    def test_real_http_non_api_responses_cannot_complete_update_or_rollback(self):
        self.real_http_readiness()
        for name, status_code, body, content_type in INVALID_API:
            with self.subTest(response=name):
                self.save_state()
                self.http_requests.clear()
                self.http_responses = {'new': (status_code, body, content_type), 'old': (status_code, body, content_type)}
                previous = set(self.backups())
                result = self.run_update()
                backup, = set(self.backups()) - previous
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((backup / 'update-complete').exists())
                self.assertNotIn('Update completed;', result.stdout + result.stderr)
                self.assertNotIn('app readiness restored', result.stdout + result.stderr)
                self.assertIn('automatic rollback also failed', result.stderr)
                self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)
                self.assertTrue(any(generation == 'new' for generation, _, _ in self.http_requests))
                self.assertTrue(any(generation == 'old' for generation, _, _ in self.http_requests))
                self.assertTrue(all(path == '/api/list' for _, path, _ in self.http_requests))
                self.assert_preserved()

    def test_real_http_automatic_rollback_cannot_claim_invalid_api_is_ready(self):
        self.real_http_readiness()
        self.state['fail_app_run'] = 'once'
        for name, status_code, body, content_type in INVALID_API:
            with self.subTest(response=name):
                self.save_state()
                self.http_responses['old'] = (status_code, body, content_type)
                previous = set(self.backups())
                result = self.run_update()
                backup, = set(self.backups()) - previous
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('automatic rollback also failed', result.stderr)
                self.assertNotIn('app readiness restored', result.stdout + result.stderr)
                self.assertNotIn('Update completed;', result.stdout + result.stderr)
                self.assertFalse((backup / 'update-complete').exists())
                self.assert_preserved()

    def test_real_http_standalone_rollback_rejects_non_api_responses(self):
        self.real_http_readiness()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        marker = (backup / 'update-complete').stat().st_mtime_ns
        shutil.rmtree(self.bundle)
        for name, status_code, body, content_type in INVALID_API:
            with self.subTest(response=name):
                self.http_responses['old'] = (status_code, body, content_type)
                result = self.rollback_without_delay(backup)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn('Rollback completed;', result.stdout + result.stderr)
                self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)
                self.assertEqual((backup / 'update-complete').stat().st_mtime_ns, marker)
                self.assert_preserved()

    def test_real_http_valid_old_api_can_be_verified_after_failed_update(self):
        self.real_http_readiness()
        self.http_responses['new'] = (200, b'<html>not the API</html>', 'text/html')
        self.http_responses['old'] = (200, b'{"games": ["VenueBingo", "hi", "hi2"]}', 'application/json')
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('app readiness restored', result.stderr)
        self.assertNotIn('Update completed;', result.stdout + result.stderr)
        backup, = self.backups()
        self.assertFalse((backup / 'update-complete').exists())
        self.assert_preserved()

    def test_exact_proven_venue_launcher_newline_variant_updates_and_rolls_back(self):
        fixture = (TOOLS / 'tests/bingo.sh').read_bytes()
        self.assertEqual(hashlib.sha256(fixture).hexdigest(), 'aa510ee4de96bf13123c6d59ed99f9bc8ba3e410fed7c689abdbf8be635bf0d2')
        venue_launcher = fixture + b'\n'
        self.assertEqual(hashlib.sha256(venue_launcher).hexdigest(), '57fb6cf504447205e3114fdf6d3ddc6f64da0092ab7b445b7cc907dead385340')
        (self.install / 'bingo.sh').write_bytes(venue_launcher)
        self.original_files['bingo.sh'] = venue_launcher
        self.real_http_readiness()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        self.assertTrue((backup / 'update-complete').is_file())
        shutil.rmtree(self.bundle)
        result = subprocess.run(['bash', str(backup / 'rollback.sh')], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Rollback completed;', result.stdout)
        self.assert_preserved()

    def test_other_launcher_byte_variants_remain_refused_before_mutation(self):
        fixture = (TOOLS / 'tests/bingo.sh').read_bytes()
        for suffix in (b'\n\n', b' \n'):
            with self.subTest(suffix=suffix):
                (self.install / 'bingo.sh').write_bytes(fixture + suffix)
                self.log_path.unlink(missing_ok=True)
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.mutations(), [])
                self.assertEqual(self.backups(), [])

    def test_foreign_resources_refused_before_any_mutation(self):
        for kind, name, nested in [('containers', 'venue-bingo-backend', True), ('containers', 'venue-bingo-frontend', True), ('containers', 'venue-bingo-db', True), ('networks', 'venue-bingo', False), ('volumes', 'venue-bingo-data', False)]:
            with self.subTest(resource=name):
                original = json.loads(json.dumps(self.state))
                item = self.state[kind][name]
                (item['Config'] if nested else item)['Labels'] = {'org.reactbingo.venue': 'foreign'}
                self.save_state()
                self.log_path.unlink(missing_ok=True)
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.mutations(), [], self.commands())
                self.assertEqual(self.backups(), [])
                self.state = original

    def test_invalid_payloads_refused_before_runtime_calls(self):
        pristine = self.root / 'pristine'
        shutil.copytree(self.bundle, pristine)
        cases = ('missing-backend', 'missing-frontend', 'missing-dockerfile', 'missing-manifest', 'tamper', 'unlisted', 'missing-rehashed', 'escape', 'duplicate', 'symlink', 'extra-root', 'extra-empty-directory', 'extra-tool', 'changed-dockerfile')
        for case in cases:
            with self.subTest(case=case):
                shutil.rmtree(self.bundle)
                shutil.copytree(pristine, self.bundle)
                self.log_path.unlink(missing_ok=True)
                if case.startswith('missing-'):
                    name = {'missing-backend': 'backend-dist/server.js', 'missing-frontend': 'frontend-dist/index.html', 'missing-dockerfile': 'release-tools/Dockerfile.backend', 'missing-manifest': 'files.sha256', 'missing-rehashed': 'backend-dist/server.js'}[case]
                    (self.bundle / name).unlink()
                    if case == 'missing-rehashed':
                        self.manifest()
                elif case == 'tamper':
                    (self.bundle / 'frontend-dist/index.html').write_text('changed')
                elif case == 'unlisted':
                    (self.bundle / 'frontend-dist/not-in-manifest.js').write_text('extra')
                elif case == 'escape':
                    with (self.bundle / 'files.sha256').open('a') as stream:
                        stream.write('0' * 64 + '  ../outside\n')
                elif case == 'duplicate':
                    content = (self.bundle / 'files.sha256').read_text()
                    (self.bundle / 'files.sha256').write_text(content + content.splitlines()[0] + '\n')
                elif case == 'symlink':
                    target = self.bundle / 'backend-dist/server.js'
                    target.unlink()
                    target.symlink_to(self.install / 'private-links.txt')
                    self.manifest()
                elif case == 'extra-root':
                    (self.bundle / 'private-links.txt').write_text('CAPABILITYSECRET')
                    self.manifest()
                elif case == 'extra-empty-directory':
                    (self.bundle / 'unexpected-source').mkdir()
                elif case == 'extra-tool':
                    (self.bundle / 'release-tools/unexpected.txt').write_text('extra')
                    self.manifest()
                elif case == 'changed-dockerfile':
                    (self.bundle / 'release-tools/Dockerfile.backend').write_text('FROM alpine:latest\nRUN wget example.com\n')
                    self.manifest()
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.commands(), [])
                self.assertEqual(self.backups(), [])
                self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)

    def test_wrong_existing_deployment_is_refused_without_mutations(self):
        variants = ('db-volume', 'db-network', 'db-workaround', 'backend-mount', 'frontend-port', 'base-image-disagrees', 'modified-launcher')
        original = json.loads(json.dumps(self.state))
        for variant in variants:
            with self.subTest(variant=variant):
                self.state = json.loads(json.dumps(original))
                shutil.copyfile(TOOLS / 'tests/bingo.sh', self.install / 'bingo.sh')
                if variant == 'db-volume':
                    self.state['containers']['venue-bingo-db']['Mounts'][0]['Name'] = 'other-data'
                elif variant == 'db-network':
                    self.state['containers']['venue-bingo-db']['NetworkSettings']['Networks'] = {'foreign-network': {}}
                elif variant == 'db-workaround':
                    self.state['containers']['venue-bingo-db']['Config']['Env'] = []
                elif variant == 'backend-mount':
                    self.state['containers']['venue-bingo-backend']['Mounts'][0]['Source'] = '/other/cards.csv'
                elif variant == 'frontend-port':
                    self.state['containers']['venue-bingo-frontend']['HostConfig']['PortBindings']['80/tcp'][0]['HostPort'] = '9000'
                elif variant == 'base-image-disagrees':
                    self.state['containers']['venue-bingo-backend']['Image'] = self.state['tags'][FRONTEND]
                elif variant == 'modified-launcher':
                    with (self.install / 'bingo.sh').open('a') as stream:
                        stream.write('# changed launcher\n')
                self.save_state()
                self.log_path.unlink(missing_ok=True)
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.mutations(), [], self.commands())
                self.assertEqual(self.backups(), [])

    def test_unexpected_app_mounts_refused_before_backup_or_runtime_mutation(self):
        original = json.loads(json.dumps(self.state))
        variants = (
            {'Type': 'volume', 'Name': 'other-persistent-data', 'Destination': '/app/data'},
            {'Type': 'bind', 'Source': '/other/data', 'Destination': '/app/data'},
            {'Type': 'tmpfs', 'Destination': '/app/cache'},
            {'Type': 'bind', 'Source': '/other/config', 'Destination': '/app/cards.csv'},
            'tmpfs-host-config',
        )
        for short in ('backend', 'frontend'):
            for extra in variants:
                with self.subTest(app=short, extra=extra):
                    self.state = json.loads(json.dumps(original))
                    item = self.state['containers']['venue-bingo-' + short]
                    if extra == 'tmpfs-host-config':
                        item['HostConfig']['Tmpfs'] = {'/app/cache': 'rw,size=64m'}
                    else:
                        item['Mounts'].append(extra)
                    self.save_state()
                    self.log_path.unlink(missing_ok=True)
                    result = self.run_update()
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(self.mutations(), [], self.commands())
                    self.assertEqual(self.backups(), [])
                    self.assertEqual(json.loads(self.state_path.read_text())['containers'], self.state['containers'])

    def test_standalone_rollback_rejects_extra_app_mount_before_tags_or_stops(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        installed = json.loads(self.state_path.read_text())
        shutil.rmtree(self.bundle)
        for short in ('backend', 'frontend'):
            with self.subTest(app=short):
                self.state = json.loads(json.dumps(installed))
                self.state['containers']['venue-bingo-' + short]['Mounts'].append({
                    'Type': 'volume', 'Name': 'other-persistent-data', 'Destination': '/app/data',
                })
                self.save_state()
                self.log_path.unlink()
                result = subprocess.run(['bash', str(backup / 'rollback.sh')], env=self.env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.mutations(), [])
                self.assertEqual(json.loads(self.state_path.read_text())['containers'], self.state['containers'])
                self.assertNotIn('Rollback completed;', result.stdout + result.stderr)

    def test_automatic_rollback_revalidates_app_mounts_before_restoring_tags(self):
        self.state['fail_app_run'] = 'once'
        self.state['extra_mount_before_rollback'] = True
        self.save_state()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('automatic rollback also failed', result.stderr)
        self.assertNotIn('app readiness restored', result.stdout + result.stderr)
        commands = self.commands()
        failed_run = max(i for i, args in enumerate(commands) if args[0] == 'run')
        self.assertFalse(any(args[0] in {'tag', 'stop', 'rm', 'run', 'start'} for args in commands[failed_run + 1:]))
        final = json.loads(self.state_path.read_text())
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])
        self.assertEqual(final['containers']['venue-bingo-backend']['Mounts'][-1]['Name'], 'other-persistent-data')
        backup, = self.backups()
        self.assertFalse((backup / 'update-complete').exists())

    def test_supported_mount_inspection_fields_are_not_rejected(self):
        for short in ('backend', 'frontend'):
            self.state['containers']['venue-bingo-' + short]['Mounts'][0].update({
                'Driver': '', 'Mode': 'ro,Z', 'Options': ['rbind'], 'RW': False, 'Propagation': 'rprivate',
            })
        self.save_state()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_startup_failure_restores_old_tags_and_returns_failure(self):
        self.state['fail_app_run'] = 'once'
        self.save_state()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])
        for name in ('venue-bingo-backend', 'venue-bingo-frontend'):
            self.assertEqual(final['containers'][name]['Image'], self.state['containers'][name]['Image'])
            self.assertTrue(final['containers'][name]['State']['Running'])
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])
        self.assertIn('Update failed; old app images and app readiness restored.', result.stderr)
        self.assertNotIn('Update completed;', result.stdout + result.stderr)
        self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)
        backup, = self.backups()
        self.assertFalse((backup / 'update-complete').exists())

    def test_rollback_startup_failure_is_not_claimed_successful(self):
        self.state['fail_app_run'] = 'always'
        self.save_state()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('automatic rollback also failed', result.stderr)
        self.assertNotIn('app readiness restored', result.stdout + result.stderr)
        self.assertNotIn('Update completed;', result.stdout + result.stderr)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])

    def test_manual_rollback_is_standalone_and_does_not_act_on_database(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        shutil.rmtree(self.bundle)
        self.log_path.unlink()
        result = subprocess.run(['bash', str(backup / 'rollback.sh')], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])
        for args in self.mutations():
            self.assertNotIn('venue-bingo-db', args)
            self.assertFalse(args[:1] == ['volume'])
        self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)

    def test_backup_and_build_failure_leave_apps_and_venue_tags_alone(self):
        original = json.loads(json.dumps(self.state))
        for flag in ('fail_export', 'fail_build'):
            with self.subTest(failure=flag):
                self.state = json.loads(json.dumps(original))
                self.state[flag] = True
                self.save_state()
                self.log_path.unlink(missing_ok=True)
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0)
                final = json.loads(self.state_path.read_text())
                for tag in (BACKEND, FRONTEND):
                    self.assertEqual(final['tags'][tag], original['tags'][tag])
                self.assertEqual(final['containers'], original['containers'])
                self.assertFalse(any(a[0] in {'stop', 'rm', 'run', 'start'} for a in self.commands()))
                self.assertNotIn('Update completed;', result.stdout + result.stderr)

    def test_missing_resources_and_config_refuse_before_mutations(self):
        original = json.loads(json.dumps(self.state))
        variants = [('containers', name) for name in ('venue-bingo-db', *('venue-bingo-' + short for short in ('backend', 'frontend')))] + [('networks', 'venue-bingo'), ('volumes', 'venue-bingo-data')] + [('tags', tag) for tag in (BACKEND, FRONTEND, MONGO)]
        for kind, name in variants:
            with self.subTest(missing=name):
                self.state = json.loads(json.dumps(original))
                del self.state[kind][name]
                self.save_state()
                self.log_path.unlink(missing_ok=True)
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])
                self.assertEqual(self.backups(), [])
        self.state = original
        self.save_state()
        (self.install / 'reactbingo/nginx.conf').unlink()
        self.log_path.unlink(missing_ok=True)
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.backups(), [])

    def test_manual_rollback_refuses_foreign_resources_before_tags_or_stops(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        installed = json.loads(self.state_path.read_text())
        variants = [('containers', name) for name in ('venue-bingo-db', 'venue-bingo-backend', 'venue-bingo-frontend')] + [('networks', 'venue-bingo'), ('volumes', 'venue-bingo-data')]
        for kind, name in variants:
            with self.subTest(foreign=name):
                self.state = json.loads(json.dumps(installed))
                item = self.state[kind][name]
                labels = item['Config'] if kind == 'containers' else item
                labels['Labels'] = {}
                self.save_state()
                self.log_path.unlink()
                result = subprocess.run(['bash', str(backup / 'rollback.sh')], env=self.env, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])

    def test_readiness_failure_rolls_back_instead_of_claiming_success(self):
        curl = self.bin / 'curl'
        curl.write_text('#!/usr/bin/env python3\nimport json, os, sys\ns = json.load(open(os.environ["FAKE_STATE"]))\nprint(\'{"games": []}\')\nif "--write-out" in sys.argv: print("200")\nsys.exit(1 if s["tags"]["localhost/reactbingo-backend:venue"] == "sha256:" + "c" * 64 else 0)\n')
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('app readiness restored', result.stderr)
        self.assertNotIn('Update completed;', result.stdout + result.stderr)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])

    def test_podman49_bare_image_ids_are_accepted_and_rollback_works(self):
        network = self.state['networks']['venue-bingo']
        network['labels'] = network.pop('Labels')
        self.state['tags'] = {tag: ident.removeprefix('sha256:') for tag, ident in self.state['tags'].items()}
        self.state['images'] = {ident.removeprefix('sha256:'): {'Id': ident.removeprefix('sha256:')} for ident in self.state['images']}
        for item in self.state['containers'].values():
            item['Image'] = item['Image'].removeprefix('sha256:')
        self.save_state()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup, = self.backups()
        result = subprocess.run(['bash', str(backup / 'rollback.sh')], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])

    def test_podman49_lowercase_network_labels_are_accepted(self):
        network = self.state['networks']['venue-bingo']
        network['labels'] = network.pop('Labels')
        self.save_state()
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_podman49_foreign_lowercase_network_labels_refuse_without_mutations(self):
        network = self.state['networks']['venue-bingo']
        network['labels'] = {'org.reactbingo.venue': 'foreign'}
        del network['Labels']
        self.save_state()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.backups(), [])

    def test_completion_write_failure_also_rolls_back(self):
        self.state['fail_marker'] = True
        self.save_state()
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        final = json.loads(self.state_path.read_text())
        for tag in (BACKEND, FRONTEND):
            self.assertEqual(final['tags'][tag], self.state['tags'][tag])
        self.assertIn('app readiness restored', result.stderr)
        self.assertNotIn('Update completed;', result.stdout + result.stderr)

    def test_success_updates_only_apps_after_backup(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        final = json.loads(self.state_path.read_text())
        self.assertNotEqual(final['tags'][BACKEND], self.state['tags'][BACKEND])
        self.assertNotEqual(final['tags'][FRONTEND], self.state['tags'][FRONTEND])
        self.assertEqual(final['containers']['venue-bingo-db'], self.state['containers']['venue-bingo-db'])
        self.assertEqual(final['volumes'], self.state['volumes'])
        for args in self.commands():
            self.assertFalse(args[0] in {'stop', 'rm'} and 'venue-bingo-db' in args, args)
            self.assertNotEqual(args[:2], ['volume', 'rm'])
        for name, content in self.original_files.items():
            self.assertEqual((self.install / name).read_bytes(), content)
        backup, = self.backups()
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertTrue((backup / 'database-volume.tar').is_file())
        self.assertTrue((backup / 'rollback.sh').is_file())
        commands = self.commands()
        export_index = next(i for i, a in enumerate(commands) if a[:2] == ['volume', 'export'])
        stop_index = next(i for i, a in enumerate(commands) if a[0] == 'stop')
        self.assertLess(export_index, stop_index)
        builds = [a for a in commands if a[0] == 'build']
        self.assertEqual(len(builds), 2)
        for args in builds:
            self.assertIn('--pull=never', args)
            self.assertIn('--network=none', args)
        venue_tag_indices = [i for i, a in enumerate(commands) if a[0] == 'tag' and a[-1] in {BACKEND, FRONTEND}]
        self.assertGreater(min(venue_tag_indices), max(i for i, a in enumerate(commands) if a[0] == 'build'))
        self.assertNotIn('CAPABILITYSECRET', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
