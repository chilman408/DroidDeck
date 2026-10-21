# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('registration_promote', 'promote-images.py')
BASE = load('registration_fixtures', 'promote_services_test.py')


class RegistrationPolicyTests(unittest.TestCase):
    def test_cli_registration_has_fixed_paths_and_rejects_overrides(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_register_machine_services') as register:
            P.main(['--register-machine-services'])
            self.assertEqual(register.call_args.args, (copier.return_value, Path('/etc')))
            with self.assertRaises(ValueError):
                P.main(['--register-machine-services', '/override'])

    def test_registration_revalidates_both_generation_and_regenerated_manifest(self):
        policy = {'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64,
                  'metadataSha256': 'c' * 64, 'accountValidated': False}
        validate = mock.Mock(return_value={'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64})
        with mock.patch.object(P, '_machine_service_policy', return_value=policy), \
                mock.patch.object(P, '_register_services', return_value={'servicesActivated': False}) as register:
            receipt = P._register_machine_services('copier', Path('/root/generated'), validate=validate)
            self.assertEqual(register.call_args.kwargs['manifest_pin'], 'b' * 64)
            self.assertEqual(register.call_args.kwargs['metadata_pin'], 'c' * 64)
            self.assertNotIn('configuration_digest', register.call_args.kwargs)
            callback = register.call_args.kwargs['validate']
            self.assertEqual(callback(b'fixture')['configurationDigest'], 'a' * 64)
            for key in ('configurationDigest', 'serviceManifestSha256'):
                validate.return_value = {'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64}
                validate.return_value[key] = 'd' * 64
                with self.assertRaises(ValueError):
                    callback(b'fixture')
            self.assertFalse(receipt['accountValidated'])
            self.assertTrue(receipt['protectedMetadataMatched'])
            self.assertTrue(receipt['servicePolicyRegenerated'])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux isolated registration fixtures required')
class MachineRegistrationTests(unittest.TestCase):
    def setUp(self):
        BASE.ServiceInstallTests.setUp(self)
        self.install()

    def validate(self, raw):
        self.assertEqual(raw, b'generated metadata fixture')
        return {'configurationDigest': 'a' * 64, 'serviceManifestSha256': self.pin}

    def install(self):
        return P._install_machine_services(self.copier, str(self.source), self.root, validate=self.validate)

    def register(self, *, validate=None, inactive=None):
        return P._register_machine_services(self.copier, self.etc, validate=validate or self.validate,
                                            inactive=inactive or (lambda: None))

    def assert_unregistered(self):
        for name in P.REGISTERED_SERVICE_FILES:
            self.assertFalse((self.etc / name).exists())

    def test_registers_nondefault_machine_recipe_without_activation_or_container_hook(self):
        receipt = self.register()
        self.assertEqual(receipt['files'], 6)
        self.assertEqual(receipt['configurationDigest'], 'a' * 64)
        self.assertNotEqual(self.pin, P.SERVICE_BUNDLE_SHA256)
        self.assertFalse(receipt['accountValidated'])
        for key in ('servicesActivated', 'servicesEnabled', 'daemonReloaded', 'containerHookRegistered',
                    'readyForActivation', 'installable', 'androidOperationsExecuted'):
            self.assertFalse(receipt[key])
        for name in P.REGISTERED_SERVICE_FILES:
            file = self.etc / name
            self.assertEqual(file.read_bytes(), (self.source / name).read_bytes())
            info = file.stat()
            self.assertEqual((info.st_uid, info.st_gid, info.st_nlink, info.st_mode & 0o777), (0, 0, 1, 0o644))
        self.assertFalse((self.etc / 'systemd/system/waydroid-container.service.d').exists())
        with self.assertRaises(ValueError):
            self.register()

    def test_wrong_recipe_and_generation_refuse_before_registration(self):
        for key in ('serviceManifestSha256', 'configurationDigest'):
            def stale(raw):
                value = self.validate(raw)
                value[key] = 'b' * 64
                return value
            with self.assertRaises(ValueError):
                self.register(validate=stale)
            self.assert_unregistered()

    def test_policy_change_between_admission_and_registration_refuses(self):
        calls = 0
        def changed(raw):
            nonlocal calls
            calls += 1
            value = self.validate(raw)
            if calls > 1:
                value['configurationDigest'] = 'b' * 64
            return value
        with self.assertRaises(ValueError):
            self.register(validate=changed)
        self.assert_unregistered()

    def test_metadata_mutation_after_validation_is_caught_before_publication(self):
        def mutate():
            (self.root / 'runtime.json').write_bytes(b'changed generated metadata')
        with self.assertRaises(ValueError):
            self.register(inactive=mutate)
        self.assert_unregistered()

    def test_writable_linked_and_symlink_metadata_refuse(self):
        file = self.root / 'runtime.json'
        file.chmod(0o666)
        with self.assertRaises(ValueError):
            self.register()
        file.chmod(0o644)
        link = self.base / 'link'
        os.link(file, link)
        with self.assertRaises(ValueError):
            self.register()
        link.unlink()
        file.rename(link)
        file.symlink_to(link)
        with self.assertRaises(ValueError):
            self.register()
        self.assert_unregistered()

    def test_altered_source_and_unexpected_private_file_refuse(self):
        extra = self.root / 'services' / 'private-key.pem'
        extra.write_bytes(b'generated sentinel')
        with self.assertRaises(ValueError):
            self.register()
        extra.unlink()
        (self.root / 'services' / P.SERVICE_FILES[0]).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.register()
        self.assert_unregistered()

    def test_existing_target_is_preserved_before_any_publication(self):
        target = self.etc / P.REGISTERED_SERVICE_FILES[-1]
        target.write_bytes(b'generated existing sentinel')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(target.read_bytes(), b'generated existing sentinel')
        self.assertFalse((self.etc / P.REGISTERED_SERVICE_FILES[0]).exists())

    def test_raced_target_is_not_overwritten(self):
        rename = P.rename_exclusive
        target = self.etc / P.SERVICE_FILES[0]
        def raced(*args):
            target.write_bytes(b'generated raced sentinel')
            return rename(*args)
        with mock.patch.object(P, 'rename_exclusive', side_effect=raced), self.assertRaises(FileExistsError):
            self.register()
        self.assertEqual(target.read_bytes(), b'generated raced sentinel')

    def test_metadata_change_after_first_publication_is_uncertain_and_retained(self):
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            (self.root / 'runtime.json').write_bytes(b'changed generated metadata')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.register()
        self.assertTrue((self.etc / P.SERVICE_FILES[0]).exists())
        self.assertFalse((self.etc / P.SERVICE_FILES[1]).exists())

    def test_busy_host_refuses_without_touching_destinations(self):
        def busy():
            raise ValueError('generated active worker')
        with self.assertRaises(ValueError):
            self.register(inactive=busy)
        self.assert_unregistered()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and Path('/etc/droiddeck/runtime.json').is_file(),
                     'Protected installed metadata and real NSS account required')
class MachineRegistrationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-machine-register-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.etc = self.base / 'etc'
        self.root = self.etc / 'droiddeck'
        self.root.mkdir(parents=True, mode=0o755)
        for name in ('pam.d', 'tmpfiles.d', 'systemd/system'):
            (self.etc / name).mkdir(parents=True, mode=0o755)
        self.config = json.loads(Path('/etc/droiddeck/runtime.json').read_bytes())
        self.config.update(lanAddress='192.168.2.253', port=9443)

    def prepare(self):
        metadata = self.root / 'runtime.json'
        metadata.write_text(json.dumps(self.config, indent=2) + '\n')
        metadata.chmod(0o644)
        self.source = self.base / 'source'
        result = subprocess.run(['/usr/bin/node', str(Path(__file__).with_name('service-bundle.js')),
                                 'build', str(metadata), str(self.source)], capture_output=True, timeout=12,
                                env={'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'})
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_real_generator_nss_and_nondefault_machine_register_six_private_files(self):
        self.prepare()
        installed = P._install_machine_services(self.copier, str(self.source), self.root)
        self.assertTrue(installed['accountValidated'])
        self.assertNotEqual(installed['servicesManifestSha256'], P.SERVICE_BUNDLE_SHA256)
        registered = P._register_machine_services(self.copier, self.etc, inactive=lambda: None)
        self.assertTrue(registered['accountValidated'])
        self.assertEqual(registered['files'], 6)
        self.assertEqual(registered['configurationDigest'], installed['configurationDigest'])
        self.assertFalse(registered['servicesActivated'])
        for name in P.REGISTERED_SERVICE_FILES:
            self.assertEqual((self.etc / name).read_bytes(), (self.source / name).read_bytes())
        self.assertFalse((self.etc / 'systemd/system/waydroid-container.service.d').exists())

    def test_real_nss_wrong_uid_cannot_install_or_register(self):
        self.config['uid'] += 10000
        self.prepare()
        with self.assertRaises(ValueError):
            P._install_machine_services(self.copier, str(self.source), self.root)
        with self.assertRaises(ValueError):
            P._register_machine_services(self.copier, self.etc, inactive=lambda: None)
        self.assertFalse((self.root / 'services').exists())
        self.assertFalse((self.etc / P.REGISTERED_SERVICE_FILES[0]).exists())


if __name__ == '__main__':
    unittest.main()
