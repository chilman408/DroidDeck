# SPDX-License-Identifier: GPL-3.0-only
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('machine_pid_promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda data: hashlib.sha256(data).hexdigest()
CONFIG = {'version': 1, 'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman',
          'lanAddress': '192.168.2.253', 'port': 9443, 'hostname': 'device.teslaandroid.com'}


def build(source, metadata):
    node = '/usr/bin/node' if os.name == 'posix' else 'node'
    result = subprocess.run([node, str(Path(__file__).with_name('pid-bundle.js')), 'build', str(metadata), str(source)],
                            capture_output=True, timeout=12)
    if result.returncode:
        raise AssertionError(result.stderr.decode())
    return json.loads((source / 'bundle-manifest.json').read_bytes())


class PidRecipeTests(unittest.TestCase):
    def test_accepted_machine_recipe_reproduces_existing_manifest_pin(self):
        self.assertTrue(callable(getattr(P, '_pid_recipe_policy', None)), 'Machine PID recipe policy is missing')
        value = P._pid_recipe_policy('2841520938496e5dadc0cc5c4f0a867c3c3ffaf8906e66b3a08992b69b8c76e9')
        self.assertEqual(value, '939582f1ddaaba2fb034fe399f842fa82026087ee4c6a982e55dabf1cf4a248c')

    def test_nondefault_recipe_matches_real_javascript_builder(self):
        self.assertTrue(callable(getattr(P, '_pid_recipe_policy', None)), 'Machine PID recipe policy is missing')
        with tempfile.TemporaryDirectory(prefix='dd-pid-recipe-') as directory:
            root = Path(directory)
            metadata = root / 'runtime.json'
            metadata.write_text(json.dumps(CONFIG))
            manifest = build(root / 'source', metadata)
            self.assertNotEqual(manifest['configurationDigest'], P.PID_CONFIGURATION_DIGEST)
            self.assertEqual(P._pid_recipe_policy(manifest['configurationDigest']), sha((root / 'source/bundle-manifest.json').read_bytes()))
            for bad in (None, True, '', 'A' * 64, '../override', 'a' * 65):
                with self.assertRaises(ValueError):
                    P._pid_recipe_policy(bad)

    def test_dynamic_generation_is_opt_in_and_legacy_pid_manifest_stays_fixed(self):
        with tempfile.TemporaryDirectory(prefix='dd-pid-generation-') as directory:
            root = Path(directory)
            metadata = root / 'runtime.json'
            metadata.write_text(json.dumps(CONFIG))
            manifest = build(root / 'source', metadata)
            encoded = (root / 'source/bundle-manifest.json').read_bytes()
            with self.assertRaises(ValueError):
                P.component_records(encoded, pid_compat=True)
            self.assertEqual(set(P.component_records(encoded, pid_compat=True, configuration_digest=manifest['configurationDigest'])), set(P.PID_FILES))

    def test_machine_pid_cli_has_fixed_destinations_no_policy_overrides(self):
        self.assertTrue(callable(getattr(P, '_install_machine_pid', None)), 'Machine PID installer is missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_install_machine_pid') as install, \
                mock.patch.object(P, '_register_machine_pid') as register, mock.patch.object(P, '_machine_pid_policy') as inspect:
            P.main(['--machine-pid-compat', '/source'])
            self.assertEqual(install.call_args.args, (copier.return_value, '/source', Path('/opt/droiddeck'), Path('/etc/droiddeck')))
            P.main(['--register-machine-pid-compat'])
            self.assertEqual(register.call_args.args, (copier.return_value, Path('/opt/droiddeck/pid-compat'), Path('/etc/systemd/system'), Path('/etc/droiddeck')))
            P.main(['--machine-pid-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            for args in (['--machine-pid-policy', '/override'], ['--machine-pid-compat', '/source', '/override'],
                         ['--register-machine-pid-compat', '/override']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and Path('/opt/droiddeck/artifact-layout.json').is_file(), 'Root Linux protected artifact receipt required')
class MachinePidTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='dd-machine-pid-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'droiddeck'
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder', 'frontend', 'runtime'):
            (self.root / name).mkdir(mode=0o755)
        marker = self.root / 'artifact-layout.json'
        marker.write_bytes(Path('/opt/droiddeck/artifact-layout.json').read_bytes())
        marker.chmod(0o600)
        self.metadata_root = self.base / 'etc-droiddeck'
        self.metadata_root.mkdir(mode=0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.metadata.write_text(json.dumps(CONFIG, indent=2) + '\n')
        self.metadata.chmod(0o644)
        self.source = self.base / 'source'
        manifest = build(self.source, self.metadata)
        self.generation = manifest['configurationDigest']
        self.pin = sha((self.source / 'bundle-manifest.json').read_bytes())
        self.systemd = self.base / 'systemd'
        self.systemd.mkdir(mode=0o755)

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), CONFIG)
        return {'configurationDigest': self.generation, 'serviceManifestSha256': 'b' * 64}

    def install(self, inactive=None):
        return P._install_machine_pid(self.copier, str(self.source), self.root, self.metadata_root,
                                       validate=self.validate, inactive=inactive or (lambda: None))

    def register(self, inactive=None):
        return P._register_machine_pid(self.copier, self.root / 'pid-compat', self.systemd, self.metadata_root,
                                        validate=self.validate, inactive=inactive or (lambda: None))

    def test_nondefault_component_and_dropin_register_without_startup_or_account_claim(self):
        installed = self.install()
        self.assertEqual(installed['pid-compatManifestSha256'], self.pin)
        self.assertTrue(installed['pidPolicyRegenerated'])
        self.assertFalse(installed['accountValidated'])
        registered = self.register()
        self.assertEqual(registered['configurationDigest'], self.generation)
        for receipt in (installed, registered):
            for key in ('servicesActivated', 'readyForActivation', 'installable', 'androidOperationsExecuted'):
                self.assertFalse(receipt[key])
        target = self.systemd / 'waydroid-container.service.d/40-droiddeck-pid-compat.conf'
        self.assertEqual(target.read_bytes(), (self.source / P.PID_FILES[1]).read_bytes())
        self.assertEqual((target.stat().st_uid, target.stat().st_gid, target.stat().st_mode & 0o777, target.stat().st_nlink), (0, 0, 0o644, 1))
        self.assertEqual([p.name for p in target.parent.iterdir()], ['40-droiddeck-pid-compat.conf'])
        with self.assertRaises(ValueError):
            self.install()
        with self.assertRaises(ValueError):
            self.register()

    def test_altered_script_even_with_rewritten_manifest_refuses(self):
        file = self.source / P.PID_FILES[0]
        file.write_bytes(b'changed non-executed sentinel')
        marker = self.source / 'bundle-manifest.json'
        manifest = json.loads(marker.read_bytes())
        manifest['files'][0].update(size=file.stat().st_size, sha256=sha(file.read_bytes()))
        marker.write_text(json.dumps(manifest, indent=2) + '\n')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'pid-compat').exists())

    def test_busy_guard_refuses_before_copy(self):
        def busy():
            raise ValueError('generated active worker')
        with self.assertRaises(ValueError):
            self.install(busy)
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'guest', 'libgbinder', 'frontend', 'runtime', 'artifact-layout.json'})

    def test_metadata_mutation_during_copy_prevents_publication(self):
        copy = self.copier.copy_image
        def mutate(*args, **kwargs):
            result = copy(*args, **kwargs)
            self.metadata.write_bytes(b'changed generated metadata')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=mutate), self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'pid-compat').exists())

    def test_metadata_mutation_after_copy_publication_is_uncertain(self):
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            self.metadata.write_bytes(b'changed generated metadata')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.install()
        self.assertTrue((self.root / 'pid-compat').is_dir())

    def test_service_check_timeout_after_copy_publication_is_uncertain(self):
        def timeout_after_publication():
            if (self.root / 'pid-compat').exists():
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.install(timeout_after_publication)
        self.assertTrue((self.root / 'pid-compat').is_dir())

    def test_metadata_and_existing_dropin_preserved_on_refusal(self):
        self.metadata.chmod(0o666)
        with self.assertRaises(ValueError):
            self.install()
        self.metadata.chmod(0o644)
        self.install()
        target = self.systemd / 'waydroid-container.service.d'
        target.mkdir()
        sentinel = target / 'foreign.conf'
        sentinel.write_bytes(b'generated existing sentinel')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(sentinel.read_bytes(), b'generated existing sentinel')

    def test_hardlinked_or_symlink_metadata_is_never_admitted(self):
        link = self.base / 'metadata-link'
        os.link(self.metadata, link)
        with self.assertRaises(ValueError):
            self.install()
        link.unlink()
        self.metadata.rename(link)
        self.metadata.symlink_to(link)
        with self.assertRaises(OSError) as refusal:
            self.install()
        self.assertEqual(refusal.exception.errno, errno.ELOOP)
        self.assertFalse((self.root / 'pid-compat').exists())

    def test_real_nss_copy_and_registration_for_nondefault_metadata(self):
        installed = P._install_machine_pid(self.copier, str(self.source), self.root, self.metadata_root, inactive=lambda: None)
        self.assertTrue(installed['accountValidated'])
        self.assertEqual(installed['pidManifestSha256'], self.pin)
        registered = P._register_machine_pid(self.copier, self.root / 'pid-compat', self.systemd,
                                             self.metadata_root, inactive=lambda: None)
        self.assertTrue(registered['accountValidated'])
        self.assertEqual(registered['configurationDigest'], self.generation)
        self.assertFalse(registered['servicesActivated'])

    def test_real_nss_wrong_uid_refuses_before_copy_or_registration(self):
        config = {**CONFIG, 'uid': 11000}
        self.metadata.write_text(json.dumps(config, indent=2) + '\n')
        with self.assertRaises(ValueError):
            P._install_machine_pid(self.copier, str(self.source), self.root, self.metadata_root, inactive=lambda: None)
        with self.assertRaises(ValueError):
            P._register_machine_pid(self.copier, self.source, self.systemd, self.metadata_root, inactive=lambda: None)
        self.assertFalse((self.root / 'pid-compat').exists())
        self.assertEqual(list(self.systemd.iterdir()), [])

    def test_changed_generation_refuses_registration(self):
        self.install()
        def changed(encoded):
            value = self.validate(encoded)
            value['configurationDigest'] = 'a' * 64
            return value
        with self.assertRaises(ValueError):
            P._register_machine_pid(self.copier, self.root / 'pid-compat', self.systemd, self.metadata_root,
                                    validate=changed, inactive=lambda: None)
        self.assertEqual(list(self.systemd.iterdir()), [])

    def test_metadata_change_during_dropin_publication_is_uncertain(self):
        self.install()
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            self.metadata.write_bytes(b'changed generated metadata')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.register()
        self.assertTrue((self.systemd / 'waydroid-container.service.d/40-droiddeck-pid-compat.conf').is_file())


if __name__ == '__main__':
    unittest.main()
