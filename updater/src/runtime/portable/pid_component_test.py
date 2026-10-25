# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda b: hashlib.sha256(b).hexdigest()


class PidPolicyTests(unittest.TestCase):
    def test_fixed_component_and_registration_cli_targets(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_assert_codec_stopped'), mock.patch.object(P, '_assert_pid_metadata'), mock.patch.object(P, '_install_component') as install, mock.patch.object(P, '_register_pid_compat') as register:
            P.main(['--pid-compat', '/source'])
            self.assertEqual(install.call_args.args[1:], ('/source', Path('/opt/droiddeck')))
            self.assertTrue(install.call_args.kwargs['pid_compat'])
            P.main(['--register-pid-compat'])
            self.assertEqual(register.call_args.args[1:], (Path('/opt/droiddeck/pid-compat'), Path('/etc/systemd/system')))
            with self.assertRaises(ValueError):
                P.main(['--register-pid-compat', '/override'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class PidComponentTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-pid-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'droiddeck'
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder', 'frontend', 'runtime'):
            (self.root / name).mkdir()
        (self.root / 'artifact-layout.json').write_bytes(b'generated protected marker')
        (self.root / 'artifact-layout.json').chmod(0o600)
        self.base_pin = sha(b'generated protected marker')
        self.source = self.base / 'source'
        self.source.mkdir()
        files = []
        for name in P.PID_FILES:
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            data = ('generated non-executed fixture ' + name).encode()
            p.write_bytes(data)
            files.append({'path': name, 'size': len(data), 'sha256': sha(data)})
        marker = {'version': 1, 'product': 'DroidDeck', 'kind': 'pid-startup-component', 'installable': False,
                  'configurationDigest': P.PID_CONFIGURATION_DIGEST, 'files': files}
        encoded = (json.dumps(marker, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(encoded)
        self.pin = sha(encoded)
        self.systemd = self.base / 'systemd'
        self.systemd.mkdir(mode=0o755)

    def install(self):
        return P._install_component(self.copier, str(self.source), self.root, manifest_pin=self.pin, base_pin=self.base_pin, pid_compat=True)

    def register(self, **kw):
        return P._register_pid_compat(self.copier, self.root / 'pid-compat', self.systemd, manifest_pin=self.pin, guard=kw.get('guard', lambda: None))

    def test_separate_component_and_independent_dropin_without_activation_or_replay(self):
        r = self.install()
        self.assertEqual(r['kind'], 'protected-pid-compat-install')
        self.assertIsNone(r['sourceRevision'])
        self.assertFalse(r['androidOperationsExecuted'])
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'guest', 'libgbinder', 'frontend', 'runtime', 'artifact-layout.json', 'pid-compat'})
        registered = self.register()
        self.assertFalse(registered['servicesActivated'])
        self.assertFalse(registered['containerSessionHookRegistered'])
        unit = self.systemd / 'waydroid-container.service.d/40-droiddeck-pid-compat.conf'
        self.assertEqual(unit.read_bytes(), (self.source / P.PID_FILES[1]).read_bytes())
        self.assertEqual((unit.stat().st_uid, unit.stat().st_gid, unit.stat().st_mode & 0o777, unit.stat().st_nlink), (0, 0, 0o644, 1))
        self.assertNotEqual(unit.stat().st_ino, (self.root / 'pid-compat' / P.PID_FILES[1]).stat().st_ino)
        with self.assertRaises(ValueError):
            self.install()
        with self.assertRaises(ValueError):
            self.register()

    def test_corrupted_component_refuses_registration(self):
        self.install()
        (self.root / 'pid-compat/portable/pid-startup.js').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(list(self.systemd.iterdir()), [])

    def test_foreign_dropin_directory_refuses_without_overwrite(self):
        self.install()
        foreign = self.systemd / 'waydroid-container.service.d'
        foreign.mkdir()
        (foreign / 'user.conf').write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual((foreign / 'user.conf').read_bytes(), b'preserve')

    def test_raced_empty_dropin_directory_not_replaced(self):
        self.install()
        rename = P.rename_exclusive
        def race(fd, old, new):
            (self.systemd / new).mkdir()
            return rename(fd, old, new)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.register()
        self.assertEqual(list((self.systemd / 'waydroid-container.service.d').iterdir()), [])

    def test_active_guard_refuses_before_publication(self):
        self.install()
        def fail():
            raise ValueError('generated service active')
        with self.assertRaises(ValueError):
            self.register(guard=fail)
        self.assertEqual(list(self.systemd.iterdir()), [])

    def test_postpublish_sync_uncertainty_retains_dropin(self):
        self.install()
        sync = os.fsync
        def fail(fd):
            if (self.systemd / 'waydroid-container.service.d').exists():
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.register()
        self.assertTrue((self.systemd / 'waydroid-container.service.d/40-droiddeck-pid-compat.conf').is_file())


if __name__ == '__main__':
    unittest.main()
