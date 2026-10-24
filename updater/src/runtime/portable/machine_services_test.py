# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

P = load('machine_promote', 'promote-images.py')
BASE = load('service_fixtures', 'promote_services_test.py')

class MachinePolicyTests(unittest.TestCase):
    def test_new_cli_has_fixed_targets_and_no_activation_or_override(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_install_machine_services') as install, \
                mock.patch.object(P, '_machine_service_policy') as inspect:
            P.main(['--machine-services', '/source'])
            self.assertEqual(install.call_args.args, (copier.return_value, '/source', Path('/etc/droiddeck')))
            P.main(['--machine-service-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            for args in (['--machine-services', '/source', '/override'], ['--machine-service-policy', '/override']):
                with self.assertRaises(ValueError):
                    P.main(args)

    def test_installer_uses_regenerated_not_caller_supplied_pins(self):
        policy = {'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64,
                  'metadataSha256': 'c' * 64, 'accountValidated': False}
        validate = mock.Mock(return_value={'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'b' * 64})
        with mock.patch.object(P, '_machine_service_policy', return_value=policy), \
                mock.patch.object(P, '_install_services', return_value={'pinnedBundleMatched': False, 'protectedBaseMatched': False,
                                  'servicesActivated': False, 'installable': False}) as install:
            result = P._install_machine_services('copier', '/source', Path('/root/generated'), validate=validate)
            self.assertEqual(install.call_args.kwargs['manifest_pin'], 'b' * 64)
            self.assertEqual(install.call_args.kwargs['base_pin'], 'c' * 64)
            callback = install.call_args.kwargs['validate']
            self.assertEqual(callback(b'generated')['configurationDigest'], 'a' * 64)
            validate.return_value = {'configurationDigest': 'd' * 64, 'serviceManifestSha256': 'b' * 64}
            with self.assertRaises(ValueError):
                callback(b'generated')
            validate.return_value = {'configurationDigest': 'a' * 64, 'serviceManifestSha256': 'd' * 64}
            with self.assertRaises(ValueError):
                callback(b'generated')
            self.assertTrue(result['servicePolicyRegenerated'])
            self.assertFalse(result['accountValidated'])
            self.assertNotIn('pinnedBundleMatched', result)

    def test_default_installation_still_uses_existing_fixed_policy(self):
        with mock.patch.object(P, '_install_component') as install:
            P._install_services('copier', '/source', Path('/etc/droiddeck'))
            self.assertEqual(install.call_args.kwargs['manifest_pin'], P.SERVICE_BUNDLE_SHA256)
            self.assertEqual(install.call_args.kwargs['base_pin'], P.CONFIGURATION_SHA256)

    def test_policy_fields_require_two_canonical_hashes(self):
        for value in (None, {}, {'configurationDigest': 'a' * 64},
                      {'configurationDigest': True, 'serviceManifestSha256': 'b' * 64},
                      {'configurationDigest': 'a' * 64, 'serviceManifestSha256': '../override'}):
            with self.assertRaises(ValueError):
                P._service_policy_fields(value)

    def test_canonical_service_generator_accepts_only_pinned_lf_or_crlf_source(self):
        source = Path(__file__).with_name('service-plan.js').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(P._canonical_service_plan(source), source)
        self.assertEqual(P._canonical_service_plan(source.replace(b'\n', b'\r\n')), source)
        for changed in (source + b'\n// changed\n', b'', b'x' * 16385, None):
            with self.assertRaises(ValueError):
                P._canonical_service_plan(changed)

@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux isolated copy fixtures required')
class MachineInstallTests(unittest.TestCase):
    def setUp(self):
        BASE.ServiceInstallTests.setUp(self)

    def validate(self, raw):
        self.assertEqual(raw, b'generated metadata fixture')
        return {'configurationDigest': 'a' * 64, 'serviceManifestSha256': self.pin}

    def install(self, validate=None):
        return P._install_machine_services(self.copier, str(self.source), self.root, validate=validate or self.validate)

    def test_copies_nondefault_generated_service_policy_without_registration(self):
        receipt = self.install()
        self.assertTrue(receipt['servicePolicyRegenerated'])
        self.assertFalse(receipt['accountValidated'])  # Fixture validation is not NSS proof.
        self.assertFalse(receipt['servicesActivated'])
        self.assertFalse(receipt['readyForActivation'])
        self.assertNotEqual(receipt['servicesManifestSha256'], P.SERVICE_BUNDLE_SHA256)
        for name in P.SERVICE_FILES:
            file = self.root / 'services' / name
            self.assertEqual(file.read_bytes(), (self.source / name).read_bytes())
            self.assertEqual((file.stat().st_uid, file.stat().st_gid, file.stat().st_nlink, file.stat().st_mode & 0o777), (0, 0, 1, 0o644))
        self.assertFalse((self.etc / 'systemd/system/tesla-waydroid-runtime.service').exists())

    def test_regenerated_hash_rejects_altered_manifest_before_publication(self):
        def wrong(raw):
            value = self.validate(raw)
            value['serviceManifestSha256'] = '0' * 64
            return value
        with self.assertRaises(ValueError):
            self.install(wrong)
        self.assertFalse((self.root / 'services').exists())

    def test_policy_changes_between_admission_and_copy_refuse(self):
        calls = 0
        def changed(raw):
            nonlocal calls
            calls += 1
            value = self.validate(raw)
            if calls > 1:
                value['configurationDigest'] = 'b' * 64
            return value
        with self.assertRaises(ValueError):
            self.install(changed)
        self.assertFalse((self.root / 'services').exists())

    def test_private_extra_file_and_existing_target_are_never_adopted(self):
        extra = self.source / 'key.pem'
        extra.write_text('generated sentinel')
        with self.assertRaises(ValueError):
            self.install()
        extra.unlink()
        self.install()
        before = (self.root / 'services/bundle-manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((self.root / 'services/bundle-manifest.json').read_bytes(), before)

    def test_metadata_change_during_validation_cannot_publish_services(self):
        calls = 0
        def changed(raw):
            nonlocal calls
            calls += 1
            value = self.validate(raw)
            if calls > 1:
                (self.root / 'runtime.json').write_bytes(b'changed generated metadata')
            return value
        with self.assertRaises(ValueError):
            self.install(changed)
        self.assertFalse((self.root / 'services').exists())

    def test_metadata_symlink_hardlink_and_wrong_mode_refuse(self):
        file = self.root / 'runtime.json'
        file.chmod(0o666)
        with self.assertRaises(ValueError):
            self.install()
        file.chmod(0o644)
        os.link(file, self.base / 'hardlink')
        with self.assertRaises(ValueError):
            self.install()

    def test_metadata_symlink_is_not_followed(self):
        file = self.root / 'runtime.json'
        target = self.base / 'generated-metadata'
        file.rename(target)
        file.symlink_to(target)
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'services').exists())

@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and Path('/etc/droiddeck/services/bundle-manifest.json').is_file(),
                     'Protected installed metadata/base services required')
class MachineValidatorTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.encoded = Path('/etc/droiddeck/runtime.json').read_bytes()

    def test_real_nss_and_generator_match_installed_base_component(self):
        value = P._validate_configuration(self.copier, self.encoded, service_policy=True)
        installed = Path('/etc/droiddeck/services/bundle-manifest.json').read_bytes()
        self.assertEqual(value['configurationDigest'], json.loads(installed)['configurationDigest'])
        self.assertEqual(value['serviceManifestSha256'], hashlib.sha256(installed).hexdigest())

    def test_changed_or_unprotected_generator_never_executes_node(self):
        with tempfile.TemporaryDirectory(prefix='machine-generator-', dir='/root') as root:
            fake = Path(root) / 'service-plan.js'
            fake.write_text('generated non-executed sentinel')
            fake.chmod(0o644)
            with mock.patch.object(P, '__file__', str(Path(root) / 'promote-images.py')), \
                    mock.patch.object(P.subprocess, 'run') as execute:
                with self.assertRaisesRegex(ValueError, 'generator pin mismatch'):
                    P._validate_configuration(self.copier, self.encoded, service_policy=True)
                execute.assert_not_called()
                fake.chmod(0o666)
                with self.assertRaises(ValueError):
                    P._validate_configuration(self.copier, self.encoded, service_policy=True)
                execute.assert_not_called()

    def test_repaired_runtime_admission_is_opt_in_only(self):
        digest = hashlib.sha256(Path('/opt/droiddeck/runtime/bundle-manifest.json').read_bytes()).hexdigest()
        if digest != P.REPAIRED_METADATA_MANIFEST_SHA256:
            self.skipTest('Exact accepted repaired runtime is not installed')
        with self.assertRaisesRegex(ValueError, 'metadata module policy mismatch'):
            P._validate_configuration(self.copier, self.encoded)

if __name__ == '__main__':
    unittest.main()
