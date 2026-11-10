"""Desktop integration uses only isolated fake-runtime PATHs and scratch HOME."""
import hashlib
import fcntl
import pty
import select
import time
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
import test_installer as fixtures

ROOT = fixtures.ROOT
VALIDATOR = shutil.which('desktop-file-validate')


class DesktopTests(unittest.TestCase):
    root: Path
    home: Path
    bin: Path
    target: Path
    state_path: Path
    log: Path
    env: dict
    state: dict
    save = fixtures.InstallerTests.save
    commands = fixtures.InstallerTests.commands
    mutations = fixtures.InstallerTests.mutations
    run_cli = fixtures.InstallerTests.run_cli
    target_python = fixtures.InstallerTests.target_python
    legacy_checkout = fixtures.InstallerTests.legacy_checkout

    def setUp(self):
        fixtures.InstallerTests.setUp(self)
        self.browser = self.root / 'browser.jsonl'
        self.env['FAKE_BROWSER_LOG'] = str(self.browser)
        opener = self.bin / 'xdg-open'
        opener.write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nwith Path(os.environ["FAKE_BROWSER_LOG"]).open("a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\nsys.exit(int(os.environ.get("FAKE_BROWSER_EXIT", "0")))\n')
        opener.chmod(0o755)

    def launch(self):
        return subprocess.run([str(self.bin / 'python3'), '-B', str(self.helper), '--launch'],
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=45)

    def test_legacy_release_update_refreshes_menu_and_preserves_database(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        result = self.run_cli(repo=self.legacy_checkout())
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self.target / '.venue-install/receipt.json'
        self.assertEqual(json.loads(receipt.read_text())['payload_sha256'], fixtures.LEGACY_PAYLOAD_HASH)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        before = json.loads(self.state_path.read_text())
        protected = {self.target / name: (self.target / name).read_bytes()
                     for name in ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv')}
        for args in [(), ('--offline',)]:
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(receipt.read_text())['payload_sha256'], fixtures.CURRENT_PAYLOAD_HASH)
            manifest = json.loads((self.target / '.venue-install/desktop.json').read_text())
            after = json.loads(self.state_path.read_text())
            self.assertEqual(manifest['container_ids'], {name: item['Id'] for name, item in after['containers'].items()})
            self.assertEqual(self.helper.read_bytes(), (ROOT / 'installer/desktop.py').read_bytes())
            self.assertEqual(after['containers']['venue-bingo-db'], before['containers']['venue-bingo-db'])
            self.assertEqual(after['volumes'], before['volumes'])
            self.assertEqual(after['networks'], before['networks'])
            for path, data in protected.items():
                self.assertEqual(path.read_bytes(), data)
            self.log.unlink()
            self.browser.unlink(missing_ok=True)
            result = self.target_python(self.helper, '--launch')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(self.browser.exists())
            self.assertEqual(self.mutations(), [])
            result = self.run_cli('--check')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.mutations(), [])

    def test_podman49_blank_wildcard_allows_menu_start_without_runtime_mutation(self):
        self.assertEqual(self.run_cli().returncode, 0)
        state = json.loads(self.state_path.read_text())
        state['containers']['venue-bingo-frontend']['HostConfig']['PortBindings']['80/tcp'][0]['HostIp'] = ''
        state['containers']['venue-bingo-backend']['NetworkSettings']['Ports'] = {'8999/tcp': None}
        self.state = state
        self.save()
        self.log.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.browser.exists())
        self.assertEqual(self.mutations(), [])

    def test_healthy_click_opens_local_bingo_without_runtime_mutation(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.log.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.browser.exists(), 'Healthy click must open the browser')
        self.assertEqual([json.loads(line) for line in self.browser.read_text().splitlines()], [['http://localhost:3000']])
        self.assertEqual(self.mutations(), [])

    def test_start_refuses_malformed_falsey_portbindings_before_any_start(self):
        self.assertEqual(self.run_cli().returncode, 0)
        baseline = self.state_path.read_text()
        for short in ('db', 'backend'):
            for ports in ([], False, 0, ''):
                with self.subTest(container=short, malformed=repr(ports)):
                    self.state = json.loads(baseline)
                    for item in self.state['containers'].values():
                        item['State']['Running'] = False
                    self.state['containers']['venue-bingo-' + short]['HostConfig']['PortBindings'] = ports
                    self.save()
                    self.log.unlink(missing_ok=True)
                    self.browser.unlink(missing_ok=True)
                    result = self.target_python(self.helper, '--launch')
                    self.assertNotEqual(result.returncode, 0, 'Malformed PortBindings allowed startup')
                    self.assertEqual(self.mutations(), [])
                    self.assertFalse(self.browser.exists())
        for ports in (None, {}):
            self.state = json.loads(baseline)
            for short in ('db', 'backend'):
                self.state['containers']['venue-bingo-' + short]['HostConfig']['PortBindings'] = ports
            self.save()
            self.log.unlink(missing_ok=True)
            result = self.target_python(self.helper, '--launch')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.mutations(), [])

    def test_start_crosschecks_actual_publications_before_any_start(self):
        self.state['frontend_host_ip'] = ''
        self.save()
        self.assertEqual(self.run_cli().returncode, 0)
        baseline = self.state_path.read_text()
        for short, field, ports in fixtures.publication_faults():
            with self.subTest(container=short, field=field, ports=ports):
                self.state = json.loads(baseline)
                for item in self.state['containers'].values():
                    item['State']['Running'] = False
                key = 'PortBindings' if field == 'HostConfig' else 'Ports'
                self.state['containers']['venue-bingo-' + short][field][key] = ports
                self.save()
                self.log.unlink(missing_ok=True)
                self.browser.unlink(missing_ok=True)
                result = self.target_python(self.helper, '--launch')
                self.assertNotEqual(result.returncode, 0, 'Contradictory or malformed publication allowed startup')
                self.assertEqual(self.mutations(), [])
                self.assertFalse(self.browser.exists())
        for host_ip in ('', '0.0.0.0'):
            for actual_ip in ('', '0.0.0.0'):
                self.state = json.loads(baseline)
                frontend = self.state['containers']['venue-bingo-frontend']
                frontend['HostConfig']['PortBindings']['80/tcp'][0]['HostIp'] = host_ip
                frontend['NetworkSettings']['Ports']['80/tcp'][0]['HostIp'] = actual_ip
                frontend['NetworkSettings']['Ports']['81/tcp'] = None
                self.save()
                self.log.unlink(missing_ok=True)
                result = self.target_python(self.helper, '--launch')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.mutations(), [])

    def test_whole_runtime_set_refuses_foreign_missing_replaced_or_unsafe_resources_before_mutation(self):
        self.assertEqual(self.run_cli().returncode, 0)
        baseline = json.loads(self.state_path.read_text())
        cases = []
        def change(label, modify):
            value = json.loads(json.dumps(baseline))
            for item in value['containers'].values():
                item['State']['Running'] = False
            modify(value)
            cases.append((label, value))
        change('late missing frontend', lambda s: s['containers'].pop('venue-bingo-frontend'))
        for name in ('venue-bingo-db', 'venue-bingo-backend', 'venue-bingo-frontend'):
            change('foreign ' + name, lambda s, n=name: s['containers'][n]['Config']['Labels'].clear())
            change('replaced ' + name, lambda s, n=name: s['containers'][n].update(Id='different-instance'))
            change('extra network ' + name, lambda s, n=name: s['containers'][n]['NetworkSettings']['Networks'].update(foreign={}))
        change('foreign volume', lambda s: s['volumes']['venue-bingo-data']['Labels'].clear())
        change('changed volume', lambda s: s['volumes']['venue-bingo-data'].update(Id='different-volume'))
        change('foreign network', lambda s: s['networks']['venue-bingo']['labels'].clear())
        change('changed network', lambda s: s['networks']['venue-bingo'].update(Id='different-network'))
        change('missing image', lambda s: s['images'].pop(s['tags']['localhost/reactbingo-backend:venue']))
        change('changed app image', lambda s: s['containers']['venue-bingo-frontend'].update(Image='sha256:' + 'e' * 64))
        change('changed DB image', lambda s: s['containers']['venue-bingo-db'].update(Image='sha256:' + 'e' * 64))
        change('missing workaround', lambda s: s['containers']['venue-bingo-db']['Config'].update(Env=[]))
        change('DB host port', lambda s: s['containers']['venue-bingo-db']['HostConfig'].update(PortBindings={'27017/tcp': []}))
        change('DB extra mount', lambda s: s['containers']['venue-bingo-db']['Mounts'].append({'Type': 'bind'}))
        change('DB wrong volume', lambda s: s['containers']['venue-bingo-db']['Mounts'][0].update(Name='foreign-data'))
        change('DB read-only data', lambda s: s['containers']['venue-bingo-db']['Mounts'][0].update(RW=False))
        for short in ('backend', 'frontend'):
            key = 'venue-bingo-' + short
            change('RW ' + short, lambda s, n=key: s['containers'][n]['Mounts'][0].update(RW=True))
            change('extra mount ' + short, lambda s, n=key: s['containers'][n]['Mounts'].append({'Type': 'bind'}))
            change('wrong bind ' + short, lambda s, n=key: s['containers'][n]['Mounts'][0].update(Source='/foreign-file'))
            change('tmpfs ' + short, lambda s, n=key: s['containers'][n]['HostConfig'].update(Tmpfs={'/other': 'rw'}))
            change('wrong port ' + short, lambda s, n=key: s['containers'][n]['HostConfig'].update(PortBindings={'999/tcp': []}))
        change('rootful', lambda s: s.update(rootless=False))
        for key, value in (('graphRoot', '/other/store'), ('runRoot', '/other/run'), ('graphDriverName', 'vfs')):
            change('storage ' + key, lambda s, k=key, v=value: s.update(store={'graphRoot': '/fake/private/store', 'runRoot': '/fake/private/run', 'graphDriverName': 'overlay', k: v}))
        for label, state in cases:
            with self.subTest(boundary=label):
                self.state = state
                self.save()
                self.browser.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.launch()
                self.assertNotEqual(result.returncode, 0, 'Unsafe runtime was accepted')
                self.assertFalse(self.browser.exists(), 'Browser opened after refusal')
                self.assertEqual(self.mutations(), [])

    def test_click_refuses_changed_configuration_receipts_and_managed_artifacts(self):
        self.assertEqual(self.run_cli().returncode, 0)
        cases = []
        for name in ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv', '.venue-install/bingo.svg', '.venue-install/bingo-desktop.py'):
            path = self.target / name
            cases.append((name, path, path.read_bytes() + b'\n#changed\n'))
        metadata = self.target / '.venue-install'
        for name, field, bad in (('desktop.json', 'home', '/foreign-home'), ('desktop.json', 'target', '/foreign-target'),
                                 ('desktop.json', 'uid', -1), ('desktop.json', 'status', 'foreign'),
                                 ('receipt.json', 'uid', -1), ('receipt.json', 'target', '/foreign-target'),
                                 ('receipt.json', 'payload_sha256', 'e' * 64),
                                 ('receipt.json', 'store', {}), ('receipt.json', 'config_sha256', {})):
            path = metadata / name
            value = json.loads(path.read_bytes())
            value[field] = bad
            cases.append((name + field, path, json.dumps(value).encode()))
        cases.append(('entry', self.entry, self.entry.read_bytes() + b'Name=Changed\n'))
        for label, path, bad in cases:
            with self.subTest(boundary=label):
                original = path.read_bytes()
                try:
                    path.write_bytes(bad)
                    self.log.unlink(missing_ok=True)
                    self.browser.unlink(missing_ok=True)
                    result = self.launch()
                    self.assertNotEqual(result.returncode, 0, 'Changed config/provenance accepted')
                    self.assertFalse(self.browser.exists())
                    self.assertEqual(self.mutations(), [])
                    self.assertTrue(path.read_bytes() == bad, 'Tampered artifact auto-repaired')
                finally:
                    path.write_bytes(original)
        # Even an edited receipt cannot authorize an unknown launcher pin.
        launcher = self.target / 'bingo.sh'
        original = launcher.read_bytes()
        receipt = metadata / 'receipt.json'
        original_receipt = receipt.read_bytes()
        launcher.write_bytes(b'unsupported launcher\n')
        value = json.loads(original_receipt)
        value['config_sha256']['bingo.sh'] = hashlib.sha256(launcher.read_bytes()).hexdigest()
        receipt.write_text(json.dumps(value))
        self.log.unlink(missing_ok=True)
        self.browser.unlink(missing_ok=True)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [])
        launcher.write_bytes(original)
        receipt.write_bytes(original_receipt)

    def test_click_respects_namespace_directory_locks_and_unfinished_journals(self):
        self.assertEqual(self.run_cli().returncode, 0)
        state_dir = self.home / '.local/state/venue-bingo'
        for path in (state_dir / 'namespace.lock', self.target):
            with self.subTest(lock=path.name):
                fd = os.open(path, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.log.unlink(missing_ok=True)
                    result = self.launch()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(self.browser.exists())
                    self.assertEqual(self.commands(), [])
                finally:
                    os.close(fd)
                    self.browser.unlink(missing_ok=True)
        for name, field, bad in (('operation.json', 'status', 'failed-needs-recovery'),
                                  ('operation.json', 'payload_sha256', 'e' * 64),
                                  ('operation.json', 'target', '/foreign-target'),
                                  ('installation.json', 'target', '/foreign-target'),
                                  ('installation.json', 'uid', -1)):
            with self.subTest(state=name + field):
                path = state_dir / name
                original = path.read_bytes()
                value = json.loads(original)
                value[field] = bad
                path.write_text(json.dumps(value))
                try:
                    self.log.unlink(missing_ok=True)
                    result = self.launch()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(self.browser.exists())
                    self.assertEqual(self.mutations(), [])
                finally:
                    path.write_bytes(original)
                    self.browser.unlink(missing_ok=True)
        pending = self.target / '.bingo-update-incomplete'
        pending.mkdir(mode=0o700)
        (pending / 'metadata.json').write_text('{}')
        self.log.unlink(missing_ok=True)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [])

    def test_stopped_click_starts_only_existing_owned_containers_in_order_without_checkout_access(self):
        self.target = self.home / 'Bingo with spaces'
        self.assertEqual(self.run_cli('--dir', self.target).returncode, 0)
        private = self.target / 'private-links.txt'
        private.write_bytes(os.urandom(64))
        private.chmod(0o600)
        paths = [self.target / name for name in ('bingo.sh', 'reactbingo/nginx.conf', 'reactbingo/backend/cards.csv', 'private-links.txt', '.venue-install/receipt.json')]
        saved = {path: (path.read_bytes(), path.stat().st_mode) for path in paths}
        self.state = json.loads(self.state_path.read_text())
        for item in self.state['containers'].values():
            item['State']['Running'] = False
        self.save()
        before = json.loads(self.state_path.read_text())
        self.log.unlink()
        # Make checkout reads/imports impossible in the helper process. Runtime
        # and browser executables are independent copies in the scratch PATH.
        script = ('import sys,os,runpy; root=' + repr(str(ROOT)) + '; '
                  'exec("def guard(event,args):\\n if event==\'open\' and isinstance(args[0],str) and (args[0]==root or args[0].startswith(root+\'/\')): raise RuntimeError(\'checkout inaccessible\')\\n"); '
                  'sys.addaudithook(guard); sys.argv=[' + repr(str(self.helper)) + ',"--launch"]; runpy.run_path(sys.argv[0],run_name="__main__")')
        result = subprocess.run([str(self.bin / 'python3'), '-I', '-B', *(['-O'] if sys.flags.optimize else []), '-c', script], env=self.env, cwd=self.root, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_starts = [['start', name] for name in ('venue-bingo-db', 'venue-bingo-backend', 'venue-bingo-frontend')]
        self.assertEqual(self.mutations(), expected_starts)
        self.assertTrue(self.browser.exists())
        after = json.loads(self.state_path.read_text())
        for item in before['containers'].values():
            item['State']['Running'] = True
        self.assertEqual(after, before)
        self.assertTrue(saved == {path: (path.read_bytes(), path.stat().st_mode) for path in paths}, 'Existing private/config/launcher bytes or modes changed')
        self.log.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertEqual(len(self.browser.read_text().splitlines()), 2)

    def test_start_failure_or_post_start_identity_state_change_never_opens_browser(self):
        self.assertEqual(self.run_cli().returncode, 0)
        baseline = json.loads(self.state_path.read_text())
        for fault in ('fail_start', 'noop_start', 'replace_on_start'):
            with self.subTest(fault=fault):
                self.state = json.loads(json.dumps(baseline))
                for item in self.state['containers'].values():
                    item['State']['Running'] = False
                self.state[fault] = 'venue-bingo-db'
                self.save()
                self.log.unlink(missing_ok=True)
                self.browser.unlink(missing_ok=True)
                result = self.launch()
                self.assertNotEqual(result.returncode, 0, 'Unverified start accepted')
                self.assertFalse(self.browser.exists())
                self.assertEqual(self.mutations(), [['start', 'venue-bingo-db']])
                after = json.loads(self.state_path.read_text())
                self.assertEqual(after['volumes'], baseline['volumes'])
                self.assertNotIn('suppressed private runtime output', result.stdout + result.stderr)

    def test_click_opens_only_for_bounded_nonredirect_exact_json_readiness(self):
        self.assertEqual(self.run_cli().returncode, 0)
        http_log = self.root / 'http.jsonl'
        self.env['FAKE_HTTP_LOG'] = str(http_log)
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport json,os,sys\nfrom pathlib import Path\nwith Path(os.environ["FAKE_HTTP_LOG"]).open("a") as stream: stream.write(json.dumps(sys.argv[1:])+"\\n")\nprint(os.environ["FAKE_HTTP_BODY"])\nprint(os.environ.get("FAKE_HTTP_STATUS", "200"))\n')
        cases = [('{"games": []}', '200', True), ('{"games": ["public-game"]}', '200', True),
                 ('<html>fallback</html>', '200', False), ('{"games": []}', '302', False),
                 ('{"games": []}', '500', False), ('{"games": [1]}', '200', False),
                 ('{"games": [null]}', '200', False), ('[]', '200', False),
                 ('{"games": [], "extra": 1}', '200', False), ('{"games": {}}', '200', False),
                 ('{"games": 1, "games": []}', '200', False), ('broken-json', '200', False)]
        for body, status, valid in cases:
            with self.subTest(status=status, valid=valid, shape=cases.index((body, status, valid))):
                self.env.update(FAKE_HTTP_BODY=body, FAKE_HTTP_STATUS=status)
                self.browser.unlink(missing_ok=True)
                self.log.unlink(missing_ok=True)
                result = self.launch()
                self.assertEqual(result.returncode == 0, valid, 'Wrong API readiness decision')
                self.assertEqual(self.browser.exists(), valid, 'Browser opened without valid readiness')
                self.assertEqual(self.mutations(), [])
                self.assertNotIn(body, result.stderr)
        self.assertTrue(http_log.exists(), 'A real bounded local readiness command must run')
        for args in map(json.loads, http_log.read_text().splitlines()):
            self.assertEqual(args[-1], 'http://127.0.0.1:3000/api/list')
            for flag in ('--disable', '--noproxy', '--max-redirs', '--max-time', '--max-filesize'):
                self.assertIn(flag, args)
            self.assertNotIn('--location', args)

    def test_transient_http_gateway_statuses_retry_until_valid_readiness(self):
        self.assertEqual(self.run_cli().returncode, 0)
        counter = self.root / 'http-count'
        flags = self.root / 'helper-optimize'
        self.env['FAKE_HTTP_COUNT'] = str(counter)
        (self.bin / 'curl').write_text(
            '#!/usr/bin/env python3\nimport os\nfrom pathlib import Path\n'
            'p=Path(os.environ["FAKE_HTTP_COUNT"])\n'
            'n=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\n'
            'print(\'{"games": []}\' if n>=4 else "upstream initializing")\n'
            'print([502,503,500,504,200][min(n,4)])\n')
        script = ('import sys,runpy; from pathlib import Path; '
                  'Path(' + repr(str(flags)) + ').write_text(str(sys.flags.optimize)); '
                  'sys.argv=[' + repr(str(self.helper)) + ',"--launch"]; '
                  'runpy.run_path(sys.argv[0],run_name="__main__")')
        self.log.unlink()
        result = subprocess.run([str(self.bin / 'python3'), '-B', '-c', script],
                                env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(int(flags.read_text()), sys.flags.optimize, 'Helper optimization did not match the test runner')
        print('DESKTOP_TARGET_FLAGS=' + json.dumps({'optimize': int(flags.read_text())}), flush=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(counter.read_text()), 5, 'Every transient HTTP response must be retried')
        self.assertEqual([json.loads(line) for line in self.browser.read_text().splitlines()], [['http://localhost:3000']])
        self.assertEqual(self.mutations(), [])

    def test_persistent_http_503_exhausts_the_readiness_deadline_without_opening_browser(self):
        spec = fixtures.importlib.util.spec_from_file_location('desktop_deadline', ROOT / 'installer/desktop.py')
        if spec is None or spec.loader is None:
            self.fail('Could not load the desktop readiness implementation')
        desktop = fixtures.importlib.util.module_from_spec(spec)
        spec.loader.exec_module(desktop)
        clock = [0.0]
        calls = []
        def curl(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(arguments, 0, stdout='upstream initializing\n503\n')
        def sleep(seconds):
            clock[0] += seconds
        with fixtures.patch.object(desktop.time, 'monotonic', side_effect=lambda: clock[0]), \
                fixtures.patch.object(desktop.time, 'sleep', side_effect=sleep), \
                fixtures.patch.object(desktop.subprocess, 'run', side_effect=curl):
            with self.assertRaisesRegex(desktop.Refusal, 'within 20 seconds; no browser was opened'):
                desktop.ready()
        self.assertEqual(clock[0], 20)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(args[0] == 'curl' and 0 < kwargs['timeout'] <= 3 for args, kwargs in calls))
        self.assertFalse(self.browser.exists())

    def test_desktop_only_registers_stopped_install_without_updates_or_adopting_replacements(self):
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        self.state = json.loads(self.state_path.read_text())
        for item in self.state['containers'].values():
            item['State']['Running'] = False
        self.save()
        before = self.state_path.read_bytes()
        self.log.unlink()
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.entry.is_file())
        self.assertEqual(self.mutations(), [])
        self.assertTrue(before == self.state_path.read_bytes(), 'Desktop-only changed stopped resources')
        manifest = self.target / '.venue-install/desktop.json'
        original = manifest.read_bytes()
        self.state['containers']['venue-bingo-frontend']['Id'] = 'replaced-container'
        self.save()
        self.log.unlink()
        result = self.run_cli('--desktop-only')
        self.assertNotEqual(result.returncode, 0, 'Desktop-only re-pinned a replaced container')
        self.assertTrue(original == manifest.read_bytes(), 'Manifest changed after failed desktop-only')
        self.assertEqual(self.mutations(), [])

    def test_long_lived_browser_survives_helper_and_terminal_exit_without_holding_locks(self):
        import ctypes
        import signal
        import termios
        libc = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)  # Adopt/reap the detached test child.
        process = None
        opener_pid = None
        master = slave = None
        try:
            self.assertEqual(self.run_cli().returncode, 0)
            marker = self.root / 'browser-survived'
            self.env['FAKE_BROWSER_MARKER'] = str(marker)
            (self.bin / 'xdg-open').write_text(
                '#!/usr/bin/env python3\nimport json,os,sys,time\nfrom pathlib import Path\n'
                'p=Path(os.environ["FAKE_BROWSER_LOG"])\n'
                'p.write_text(json.dumps({"url":sys.argv[1:],"pid":os.getpid(),'
                '"sid":os.getsid(0),"optimize":sys.flags.optimize,'
                '"fds":[os.readlink("/proc/self/fd/"+str(n)) for n in range(3)]}))\n'
                'time.sleep(11)\nPath(os.environ["FAKE_BROWSER_MARKER"]).write_text("survived")\n'
                'time.sleep(15)\n')
            flags = self.root / 'helper-optimize'
            script = ('import sys,runpy; from pathlib import Path; '
                      'Path(' + repr(str(flags)) + ').write_text(str(sys.flags.optimize)); '
                      'sys.argv=[' + repr(str(self.helper)) + ',"--launch"]; '
                      'runpy.run_path(sys.argv[0],run_name="__main__")')
            self.env['PYTHONWARNINGS'] = 'always::ResourceWarning'
            master, slave = pty.openpty()
            terminal_fd = slave
            def terminal_session():
                os.setsid()
                fcntl.ioctl(terminal_fd, termios.TIOCSCTTY, 0)
            process = subprocess.Popen([str(self.bin / 'python3'), '-B', '-c', script],
                                       env=self.env, cwd=self.root, stdin=slave, stdout=slave, stderr=slave,
                                       preexec_fn=terminal_session)
            os.close(slave)
            slave = None
            os.write(master, b'\n')  # Let an expected RED refusal's Enter prompt finish.
            deadline = time.monotonic() + 10
            while not self.browser.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(self.browser.exists(), 'The browser dispatch never started')
            self.assertIsNone(process.poll(), 'Helper exited before the dispatch grace check')
            for path in (self.home / '.local/state/venue-bingo/namespace.lock', self.target):
                fd = os.open(path, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            self.assertIsNone(process.poll(), 'Locks were not checked during opener supervision')
            self.assertEqual(process.wait(timeout=5), 0, 'A successful long-lived opener was killed/refused')
            self.assertEqual(int(flags.read_text()), sys.flags.optimize)
            print('DESKTOP_TARGET_FLAGS=' + json.dumps({'optimize': int(flags.read_text())}), flush=True)
            output = b''
            while select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            self.assertIn(b'Browser launch requested; if no window appears open http://localhost:3000 manually', output)
            self.assertNotIn(b'ResourceWarning', output)
            info = json.loads(self.browser.read_text())
            opener_pid = info['pid']
            self.assertEqual(info['url'], ['http://localhost:3000'])
            self.assertEqual(info['optimize'], sys.flags.optimize)
            self.assertEqual(info['sid'], opener_pid, 'Opener must have an independent session')
            self.assertEqual(info['fds'], ['/dev/null'] * 3, 'Opener inherited terminal/captured pipes')
            os.close(master)  # Actual controlling terminal disappears after helper exit.
            master = None
            for path in (self.home / '.local/state/venue-bingo/namespace.lock', self.target):
                fd = os.open(path, os.O_RDONLY)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
            deadline = time.monotonic() + 13
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(marker.exists(), 'Opener did not survive beyond the former ten-second timeout')
            os.kill(opener_pid, 0)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            if opener_pid is None and self.browser.exists():
                try:
                    opener_pid = json.loads(self.browser.read_text())['pid']
                except (ValueError, KeyError):
                    pass
            if opener_pid is not None:
                try:
                    os.kill(opener_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(opener_pid, 0)
                except ChildProcessError:
                    pass
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
            libc.prctl(36, previous.value, 0, 0, 0)

    def test_browser_dispatch_spawn_and_nonzero_failures_offer_manual_local_url(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.log.unlink()
        self.env['FAKE_BROWSER_EXIT'] = '6'
        for failure in ('nonzero', 'missing-executable'):
            with self.subTest(failure=failure):
                if failure == 'missing-executable':
                    (self.bin / 'xdg-open').unlink()
                self.browser.unlink(missing_ok=True)
                result = self.launch()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('default browser could not be opened', result.stderr)
                self.assertIn('Open http://localhost:3000 manually', result.stderr)
                self.assertNotIn('Browser launch requested', result.stdout)
                self.assertEqual(self.mutations(), [])

    def test_browser_failure_is_clear_and_interactive_error_remains_visible(self):
        self.assertEqual(self.run_cli().returncode, 0)
        self.env['FAKE_BROWSER_EXIT'] = '6'
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('browser', result.stderr.lower())
        self.assertNotIn('Press Enter', result.stderr)
        master, slave = pty.openpty()
        process = subprocess.Popen([str(self.bin / 'python3'), '-B', str(self.helper), '--launch'],
                                   env=self.env, cwd=self.root, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b''
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and b'Press Enter' not in output:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        output += os.read(master, 4096)
                    except OSError:
                        break
                if process.poll() is not None:
                    break
            self.assertIn(b'Press Enter', output, 'Interactive startup error vanished without a prompt')
            os.write(master, b'\n')
            self.assertNotEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            os.close(master)

    def test_click_preflight_refuses_nonboolean_states_and_headless_check_stays_read_only(self):
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        def snapshot():
            return {str(path.relative_to(self.home)): (path.read_bytes(), path.stat().st_mode) for path in self.home.rglob('*') if path.is_file()}
        before = snapshot()
        self.log.unlink()
        result = self.run_cli('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(before == snapshot(), '--check changed the installation or desktop state')
        self.assertFalse(self.entry.exists())
        self.assertFalse(self.helper.exists())
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.run_cli('--desktop-only').returncode, 0)
        self.state = json.loads(self.state_path.read_text())
        for item in self.state['containers'].values():
            item['State']['Running'] = False
        self.state['containers']['venue-bingo-frontend']['State']['Running'] = 0
        self.save()
        self.log.unlink()
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [], 'A malformed late container state allowed an early start')

    def test_transient_readiness_retries_but_hung_readiness_has_a_deadline(self):
        self.assertEqual(self.run_cli().returncode, 0)
        counter = self.root / 'http-count'
        self.env['FAKE_HTTP_COUNT'] = str(counter)
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\np=Path(os.environ["FAKE_HTTP_COUNT"])\nn=int(p.read_text()) if p.exists() else 0\np.write_text(str(n+1))\nif n<2: sys.exit(7)\nprint(\'{"games": []}\\n200\')\n')
        self.log.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(counter.read_text()), 3)
        self.assertTrue(self.browser.exists())
        self.assertEqual(self.mutations(), [])
        self.browser.unlink()
        (self.bin / 'curl').write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
        started = time.monotonic()
        result = self.launch()
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 30, 'Readiness command escaped its overall deadline')
        self.assertIn('20 seconds', result.stderr)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [])

    def test_hung_runtime_command_is_bounded_without_opening_browser(self):
        self.assertEqual(self.run_cli().returncode, 0)
        (self.bin / 'podman').write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
        self.log.unlink()
        started = time.monotonic()
        result = self.launch()
        self.assertLess(time.monotonic() - started, 15, 'Runtime command escaped its timeout')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('timed out', result.stderr)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [])

    def test_user_home_need_not_be_private_when_installation_and_state_are_private(self):
        self.home.chmod(0o755)
        self.assertEqual(self.run_cli().returncode, 0)
        self.log.unlink()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.browser.exists())
        self.assertEqual(self.mutations(), [])
        for path in (self.target, self.target / '.venue-install', self.home / '.local/state/venue-bingo'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.home.stat().st_mode & 0o777, 0o755)

    def test_registration_refuses_nonprivate_existing_target_after_completed_update(self):
        self.assertEqual(self.run_cli('--no-desktop').returncode, 0)
        self.target.chmod(0o755)
        result = self.run_cli('--offline')
        self.assertNotEqual(result.returncode, 0, 'Registration accepted a target that startup must refuse')
        self.assertIn('installation succeeded', result.stderr.lower())
        self.assertFalse(self.entry.exists())
        self.assertFalse(self.helper.exists())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o755, 'Original directory was silently chmodded')
        operation = json.loads((self.home / '.local/state/venue-bingo/operation.json').read_text())
        self.assertEqual(operation['status'], 'installed')

    def test_desktop_only_refreshes_a_proven_headless_update_but_refuses_a_replacement(self):
        self.assertEqual(self.run_cli(repo=self.legacy_checkout()).returncode, 0)
        self.assertEqual(json.loads((self.target / '.venue-install/receipt.json').read_text())['payload_sha256'], fixtures.LEGACY_PAYLOAD_HASH)
        manifest = self.target / '.venue-install/desktop.json'
        original_manifest = manifest.read_bytes()
        self.assertEqual(self.run_cli('--offline', '--no-desktop').returncode, 0)
        self.assertTrue(original_manifest == manifest.read_bytes(), 'Headless update touched desktop artifacts')
        receipt = self.target / '.venue-install/receipt.json'
        saved_receipt = receipt.read_bytes()
        self.assertEqual(json.loads(saved_receipt)['payload_sha256'], fixtures.CURRENT_PAYLOAD_HASH)
        baseline = json.loads(self.state_path.read_text())
        for item in baseline['containers'].values():
            item['State']['Running'] = False
        self.state = json.loads(json.dumps(baseline))
        self.state['containers']['venue-bingo-frontend']['Id'] = 'foreign-after-update'
        self.save()
        self.log.unlink()
        refused = self.run_cli('--desktop-only')
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(original_manifest == manifest.read_bytes(), 'A replaced container was re-pinned')
        self.assertEqual(self.mutations(), [])
        self.state = baseline
        self.save()
        self.log.unlink()
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])
        self.assertTrue(saved_receipt == receipt.read_bytes(), 'Desktop-only rewrote installation receipt')
        value = json.loads(manifest.read_bytes())
        self.assertEqual(value['container_ids'], {name: item['Id'] for name, item in baseline['containers'].items()})
        result = self.target_python(self.helper, '--launch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.browser.exists())
        self.assertEqual(self.mutations(), [['start', name] for name in ('venue-bingo-db', 'venue-bingo-backend', 'venue-bingo-frontend')])

    def test_desktop_only_prerequisite_inspection_is_bounded(self):
        self.assertEqual(self.run_cli().returncode, 0)
        original = self.helper.read_bytes()
        (self.bin / 'podman').write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(16)\nraise SystemExit(123)\n')
        self.log.unlink()
        started = time.monotonic()
        result = self.run_cli('--desktop-only')
        self.assertLess(time.monotonic() - started, 15, 'Desktop-only prerequisite inspection was not bounded')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.helper.read_bytes() == original)
        self.assertFalse(self.browser.exists())
        self.assertEqual(self.mutations(), [])

    @property
    def entry(self):
        return Path(self.env['XDG_DATA_HOME']) / 'applications/bingo.desktop'

    @property
    def helper(self):
        return self.target / '.venue-install/bingo-desktop.py'

    def test_registration_refuses_foreign_entry_without_poisoning_completed_install(self):
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text('[Desktop Entry]\nName=Foreign\n')
        original = self.entry.read_bytes()
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0, 'Foreign entry must not be overwritten')
        self.assertTrue(self.entry.read_bytes() == original, 'Foreign entry modified')
        self.assertIn('installation succeeded', result.stderr.lower())
        self.assertIn('--desktop-only', result.stderr)
        operation = self.home / '.local/state/venue-bingo/operation.json'
        self.assertEqual(json.loads(operation.read_text())['status'], 'installed')
        self.assertTrue((self.target / '.venue-install/receipt.json').is_file())
        self.assertFalse(self.helper.exists())
        self.entry.unlink()
        self.log.unlink()
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.mutations(), [])

    def test_managed_registration_refuses_links_special_files_missing_and_tampering(self):
        self.assertEqual(self.run_cli().returncode, 0)
        manifest = self.target / '.venue-install/desktop.json'
        icon = self.target / '.venue-install/bingo.svg'
        protected = self.home / 'protected'
        protected.write_bytes(os.urandom(40))
        protected.chmod(0o600)
        saved_protected = protected.read_bytes()
        for path in (self.helper, icon, self.entry, manifest):
            original = path.read_bytes()
            for kind in ('symlink', 'hardlink', 'fifo', 'missing', 'tampered'):
                with self.subTest(artifact=path.name, kind=kind):
                    path.unlink()
                    if kind == 'symlink':
                        copy = self.home / 'same-bytes'
                        copy.write_bytes(original)
                        copy.chmod(0o600)
                        path.symlink_to(copy)
                    elif kind == 'hardlink':
                        os.link(protected, path)
                    elif kind == 'fifo':
                        os.mkfifo(path, 0o600)
                    elif kind == 'tampered':
                        path.write_bytes(original + b'changed')
                        path.chmod(0o600)
                    self.log.unlink(missing_ok=True)
                    result = self.run_cli('--desktop-only')
                    self.assertNotEqual(result.returncode, 0, 'Unsafe managed artifact was repaired/adopted')
                    self.assertEqual(self.mutations(), [])
                    self.assertTrue(protected.read_bytes() == saved_protected, 'Private file was modified')
                    path.unlink(missing_ok=True)
                    path.write_bytes(original)
                    path.chmod(0o600)

    def test_ambiguous_desktop_paths_refuse_before_runtime_mutation_with_headless_opt_out(self):
        for character in ('%', '\\', '"', "'", '$', '`', '\u00e9'):
            for field in ('target', 'data'):
                with self.subTest(path=field, character=repr(character)):
                    fixture = fixtures.InstallerTests()
                    fixture.setUp()
                    try:
                        target = fixture.home / ('Bingo ' + character)
                        if field == 'data':
                            fixture.env['XDG_DATA_HOME'] = str(target)
                            target = fixture.home / 'installed'
                        result = fixture.run_cli('--dir', target)
                        self.assertNotEqual(result.returncode, 0, 'Ambiguous desktop path was accepted')
                        self.assertEqual(fixture.mutations(), [])
                        self.assertFalse(target.exists())
                        self.assertIn('--no-desktop', result.stderr)
                    finally:
                        fixture.doCleanups()
        result = self.run_cli('--dir', self.home / 'headless %', '--no-desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.entry.exists())

    def test_opt_out_then_desktop_only_registers_without_runtime_mutations(self):
        result = self.run_cli('--no-desktop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.entry.exists())
        self.assertFalse(self.helper.exists())
        baseline = json.loads(self.state_path.read_text())
        self.log.unlink()
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.entry.is_file())
        self.assertEqual(self.mutations(), [])
        self.assertEqual(json.loads(self.state_path.read_text()), baseline)
        saved = {p: p.read_bytes() for p in (self.entry, self.helper, self.target / '.venue-install/desktop.json')}
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p: p.read_bytes() for p in saved}, saved)
        self.assertEqual(self.mutations(), [])
        help_result = self.run_cli('--help')
        self.assertIn('--desktop-only', help_result.stdout)
        self.assertIn('--no-desktop', help_result.stdout)

    def test_fresh_automatically_registers_durable_bingo_entry_with_spaces(self):
        self.target = self.home / 'Bingo with spaces'
        result = self.run_cli('--dir', self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.entry.is_file(), 'Successful install must register Bingo')
        values = dict(line.split('=', 1) for line in self.entry.read_text().splitlines() if '=' in line)
        self.assertEqual(values['Name'], 'Bingo')
        self.assertEqual(values['Terminal'], 'true')
        self.assertEqual(shlex.split(values['Exec']), [str(Path(sys.executable).resolve()), '-B', str(self.helper), '--launch'])
        self.assertEqual(self.helper.read_bytes(), (ROOT / 'installer/desktop.py').read_bytes())
        icon = Path(values['Icon'])
        self.assertTrue(icon.is_relative_to(self.target))
        self.assertIn('<svg', icon.read_text())
        self.assertNotIn(str(ROOT), self.entry.read_text())
        self.assertEqual((self.target / 'bingo.sh').read_bytes(), (ROOT / 'installer/assets/bingo.sh').read_bytes())
        for p in (self.entry, self.helper, icon, self.target / '.venue-install/desktop.json'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        if VALIDATOR:
            validation = subprocess.run([VALIDATOR, str(self.entry)], env=self.env, capture_output=True, text=True, timeout=10)
            self.assertEqual(validation.returncode, 0, validation.stderr + validation.stdout)


if __name__ == '__main__':
    unittest.main()
