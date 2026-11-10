"""Desktop/release-management seam tests, using only isolated runtime fakes."""
import hashlib
import importlib.util
import json
import unittest

import test_desktop as desktop_fixtures
import test_installer as fixtures


class DesktopManagementTests(desktop_fixtures.DesktopTests):

    def test_current_installation_receipt_can_construct_real_idle_updater(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = self.target / '.venue-install'
        receipt = json.loads((metadata / 'receipt.json').read_bytes())
        self.assertEqual(receipt['payload_sha256'], fixtures.CURRENT_PAYLOAD_HASH)
        spec = importlib.util.spec_from_file_location('bingo_composed_management', metadata / 'bingo-management.py')
        if spec is None or spec.loader is None:
            self.fail('Installed management module could not be loaded')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module._prepare(self.target, self.home)
        except module.Refusal as exc:
            self.fail('The selected Settings release must be accepted by the installed helper: ' + str(exc))
        observed = []

        class IdleServer:
            def serve_forever(self, **kwargs):
                observed.append('served')

            def server_close(self):
                observed.append('closed')

        def factory(service, *, port):
            self.assertEqual(port, 8766)
            self.assertEqual(service.snapshot()['phase'], 'idle')
            self.assertEqual(service.snapshot()['currentVersion'], 'v3.0.0')
            self.assertEqual(type(service.updater).__name__, 'ReleaseUpdater')
            return IdleServer()

        module._serve(self.target, self.home, server_factory=factory)
        self.assertEqual(observed, ['served', 'closed'])

    def test_registration_copies_and_pins_both_management_modules(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = self.target / '.venue-install'
        provenance = json.loads((metadata / 'desktop.json').read_bytes())
        self.assertEqual(provenance['version'], 2)
        for installed, source in (
            ('bingo-management.py', 'management.py'),
            ('bingo-release-update.py', 'release_update.py'),
        ):
            path = metadata / installed
            self.assertTrue(path.is_file(), 'Desktop registration must carry its updater with it')
            self.assertEqual(path.read_bytes(), (fixtures.ROOT / 'installer' / source).read_bytes())
            self.assertEqual(provenance['files'][str(path)], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_legacy_desktop_provenance_migrates_without_adopting_foreign_helpers(self):
        self.assertEqual(self.run_cli().returncode, 0)
        metadata = self.target / '.venue-install'
        path = metadata / 'desktop.json'
        value = json.loads(path.read_bytes())
        value['version'] = 1
        for name in ('bingo-management.py', 'bingo-release-update.py'):
            value['files'].pop(str(metadata / name), None)
            (metadata / name).unlink(missing_ok=True)
        path.write_text(json.dumps(value))
        foreign = metadata / 'bingo-management.py'
        foreign.write_bytes(b'foreign-owned-content\n')
        foreign.chmod(0o600)
        result = self.run_cli('--desktop-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(foreign.read_bytes(), b'foreign-owned-content\n')
        self.assertEqual(json.loads(path.read_bytes())['version'], 1)
        foreign.unlink()
        result = self.run_cli('--desktop-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(path.read_bytes())['version'], 2)

    def test_changed_management_copy_refuses_before_start_or_browser_dispatch(self):
        self.assertEqual(self.run_cli().returncode, 0)
        metadata = self.target / '.venue-install'
        for name in ('bingo-management.py', 'bingo-release-update.py'):
            path = metadata / name
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n# changed\n')
                self.log.unlink(missing_ok=True)
                self.browser.unlink(missing_ok=True)
                result = self.target_python(self.helper, '--launch')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.mutations(), [])
                self.assertFalse(self.browser.exists())
            finally:
                path.write_bytes(original)


def load_tests(loader, standard_tests, pattern):
    # Reuse the isolated fixture without rerunning its whole inherited suite.
    return unittest.TestSuite(DesktopManagementTests(name) for name in DesktopManagementTests.__dict__
                              if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
