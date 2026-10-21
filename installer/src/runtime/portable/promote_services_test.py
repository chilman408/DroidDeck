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
PROMOTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTE)
sha = lambda b: hashlib.sha256(b).hexdigest()


class ServicePolicyTests(unittest.TestCase):
    def test_cli_fixed_plan_registration_destinations_no_overrides(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=0, create=True), mock.patch.object(PROMOTE, 'load_copier'), mock.patch.object(PROMOTE, '_install_services') as install, mock.patch.object(PROMOTE, '_register_services') as register:
            PROMOTE.main(['--services', '/source'])
            self.assertEqual(install.call_args.args[1:], ('/source', Path('/etc/droiddeck')))
            PROMOTE.main(['--register-services'])
            self.assertEqual(register.call_args.args[1:], (Path('/etc/droiddeck/services'), Path('/etc')))
            with self.assertRaises(ValueError):
                PROMOTE.main(['--register-services', '/override'])

    def test_service_vacancy_checks_do_not_enable_start_or_reload(self):
        values = [mock.Mock(returncode=4, stdout=b'not-found\n')] * 4 + [mock.Mock(returncode=0, stdout=b'inactive\n')]
        with mock.patch.object(PROMOTE.subprocess, 'run', side_effect=values) as run:
            PROMOTE._assert_service_vacancy()
            self.assertEqual(run.call_count, 5)
            for call in run.call_args_list:
                self.assertEqual(call.args[0][:2], ['/usr/bin/systemctl', 'show'])

    def test_existing_workers_or_active_container_refuse(self):
        with mock.patch.object(PROMOTE.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'loaded\n')):
            with self.assertRaises(ValueError):
                PROMOTE._assert_service_vacancy()
        values = [mock.Mock(returncode=4, stdout=b'not-found\n')] * 4 + [mock.Mock(returncode=0, stdout=b'active\n')]
        with mock.patch.object(PROMOTE.subprocess, 'run', side_effect=values):
            with self.assertRaises(ValueError):
                PROMOTE._assert_service_vacancy()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class ServiceInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-services-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.etc = self.base / 'etc'
        self.root = self.etc / 'droiddeck'
        self.root.mkdir(parents=True, mode=0o755)
        for d in ('pam.d', 'tmpfiles.d', 'systemd/system'):
            (self.etc / d).mkdir(parents=True, mode=0o755)
        self.source = self.base / 'source'
        self.source.mkdir(mode=0o700)
        raw = b'generated metadata fixture'
        (self.root / 'runtime.json').write_bytes(raw)
        (self.root / 'runtime.json').chmod(0o644)
        self.base_pin = sha(raw)
        records = []
        for name in PROMOTE.SERVICE_FILES:
            p = self.source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            data = ('generated non-executed service fixture ' + name).encode()
            p.write_bytes(data)
            records.append({'path': name, 'size': len(data), 'sha256': sha(data)})
        m = {'version': 1, 'product': 'DroidDeck', 'kind': 'service-component', 'installable': False,
             'sourcePlanSha256': PROMOTE.SERVICE_PLAN_SHA256, 'configurationDigest': 'a' * 64, 'files': records}
        raw = (json.dumps(m, indent=2) + '\n').encode()
        (self.source / 'bundle-manifest.json').write_bytes(raw)
        self.pin = sha(raw)

    def install(self):
        return PROMOTE._install_services(self.copier, str(self.source), self.root, manifest_pin=self.pin,
                                         base_pin=self.base_pin, validate=lambda raw: {'configurationDigest': 'a' * 64})

    def register(self):
        return PROMOTE._register_services(self.copier, self.root / 'services', self.etc,
                                          manifest_pin=self.pin, configuration_digest='a' * 64, inactive=lambda: None)

    def test_installs_registers_regular_single_link_files_without_activation(self):
        self.assertFalse(self.install()['servicesActivated'])
        receipt = self.register()
        self.assertEqual(receipt['files'], 6)
        self.assertFalse(receipt['servicesActivated'])
        self.assertFalse(receipt['daemonReloaded'])
        self.assertFalse(receipt['containerHookRegistered'])
        self.assertFalse((self.etc / 'systemd/system/waydroid-container.service.d/50-droiddeck-session.conf').exists())
        for name in PROMOTE.REGISTERED_SERVICE_FILES:
            p = self.etc / name
            self.assertEqual(p.read_bytes(), (self.source / name).read_bytes())
            self.assertEqual(p.stat().st_mode & 0o777, 0o644)
            self.assertEqual((p.stat().st_uid, p.stat().st_gid, p.stat().st_nlink), (0, 0, 1))
        with self.assertRaises(ValueError):
            self.install()
        with self.assertRaises(ValueError):
            self.register()

    def test_existing_target_preserved_before_any_files_publish(self):
        self.install()
        p = self.etc / PROMOTE.SERVICE_FILES[0]
        p.write_bytes(b'existing generated sentinel')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(p.read_bytes(), b'existing generated sentinel')
        self.assertFalse((self.etc / PROMOTE.SERVICE_FILES[1]).exists())

    def test_changed_unit_refuses_without_registration(self):
        self.install()
        (self.root / 'services' / PROMOTE.SERVICE_FILES[0]).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.register()
        self.assertFalse((self.etc / PROMOTE.SERVICE_FILES[0]).exists())

    def test_raced_target_not_overwritten(self):
        self.install()
        rename = PROMOTE.rename_exclusive
        def raced(fd, pending, target):
            p = self.etc / PROMOTE.SERVICE_FILES[0]
            p.write_bytes(b'generated raced sentinel')
            return rename(fd, pending, target)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced), self.assertRaises(FileExistsError):
            self.register()
        self.assertEqual((self.etc / PROMOTE.SERVICE_FILES[0]).read_bytes(), b'generated raced sentinel')

    def test_partial_registration_is_uncertain_and_preserved(self):
        self.install()
        rename = PROMOTE.rename_exclusive
        calls = 0
        def failed(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('generated second publication failure')
            return rename(*args)
        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=failed), self.assertRaises(PROMOTE.PromotionUncertain):
            self.register()
        self.assertTrue((self.etc / PROMOTE.SERVICE_FILES[0]).exists())
        self.assertFalse((self.etc / PROMOTE.SERVICE_FILES[1]).exists())


if __name__ == '__main__':
    unittest.main()
