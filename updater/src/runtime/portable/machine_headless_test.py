# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('machine_headless_promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda data: hashlib.sha256(data).hexdigest()
CONFIG = {'version': 1, 'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman',
          'lanAddress': '192.168.2.253', 'port': 9443, 'hostname': 'device.teslaandroid.com'}


def build(source, metadata, profile):
    # Fixture-only API build: no CLI guard is bypassed and no headless code runs.
    script = ('const fs=require("node:fs"),c=require("node:crypto");'
              'require(process.argv[1]).buildHeadlessBundle(JSON.parse(fs.readFileSync(process.argv[2])),'
              'c.createHash("sha256").update(fs.readFileSync(process.argv[3])).digest("hex"),process.argv[4]);')
    result = subprocess.run(['/usr/bin/node' if os.name == 'posix' else 'node', '-e', script,
                             str(Path(__file__).with_name('headless-bundle.js')), str(metadata), str(profile), str(source)],
                            capture_output=True, timeout=12)
    if result.returncode:
        raise AssertionError(result.stderr.decode())
    return json.loads((source / 'bundle-manifest.json').read_bytes())


class HeadlessRecipeTests(unittest.TestCase):
    def test_recipe_reproduces_accepted_component(self):
        self.assertTrue(callable(getattr(P, '_headless_recipe_policy', None)), 'Protected headless recipe is missing')
        self.assertEqual(P._headless_recipe_policy(P.PID_CONFIGURATION_DIGEST,
                         '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'),
                         '36c62c463c77d43b81668ba422def8c7323328bed5d8017502181bcdd6cb18f8')

    def test_nondefault_policy_matches_javascript_and_refuses_bad_hashes(self):
        self.assertTrue(callable(getattr(P, '_headless_recipe_policy', None)), 'Protected headless recipe is missing')
        with tempfile.TemporaryDirectory(prefix='dd-headless-recipe-') as directory:
            root = Path(directory)
            metadata, cfg = root / 'runtime.json', root / 'waydroid.cfg'
            metadata.write_text(json.dumps(CONFIG))
            cfg.write_bytes(b'generated Waydroid profile\n')
            m = build(root / 'source', metadata, cfg)
            self.assertEqual(P._headless_recipe_policy(m['configurationDigest'], sha(cfg.read_bytes())),
                             sha((root / 'source/bundle-manifest.json').read_bytes()))
            for bad in (None, True, '', 'A' * 64, 'a' * 65):
                for args in ((bad, 'a' * 64), ('b' * 64, bad)):
                    with self.assertRaises(ValueError):
                        P._headless_recipe_policy(*args)

    def test_cli_fixes_all_destinations_and_exposes_no_policy_override(self):
        self.assertTrue(callable(getattr(P, '_install_machine_headless', None)), 'Protected headless installer is missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_install_machine_headless') as install, \
                mock.patch.object(P, '_register_machine_headless') as register, mock.patch.object(P, '_machine_headless_policy') as inspect:
            P.main(['--machine-headless', '/source'])
            self.assertEqual(install.call_args.args, (copier.return_value, '/source', Path('/opt/droiddeck'), Path('/etc/droiddeck'), Path('/var/lib/waydroid')))
            P.main(['--register-machine-headless'])
            self.assertEqual(register.call_args.args, (copier.return_value, Path('/opt/droiddeck/headless-runtime'), Path('/etc/systemd/system'),
                                                       Path('/etc/droiddeck'), Path('/var/lib/waydroid')))
            P.main(['--machine-headless-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            for args in (['--machine-headless-policy', '/override'], ['--machine-headless', '/source', '/override'],
                         ['--register-machine-headless', '/override']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and Path('/opt/droiddeck/artifact-layout.json').is_file(), 'Root Linux protected artifact receipt required')
class MachineHeadlessTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='dd-machine-headless-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'droiddeck'
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder', 'frontend', 'runtime', 'pid-compat'):
            (self.root / name).mkdir(mode=0o755)
        marker = self.root / 'artifact-layout.json'
        marker.write_bytes(Path('/opt/droiddeck/artifact-layout.json').read_bytes())
        marker.chmod(0o600)
        self.metadata_root, self.profile_root, self.systemd = [self.base / name for name in ('etc-droiddeck', 'waydroid', 'systemd')]
        for directory in (self.metadata_root, self.profile_root, self.systemd):
            directory.mkdir(mode=0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.metadata.write_text(json.dumps(CONFIG, indent=2) + '\n')
        self.metadata.chmod(0o644)
        self.cfg = self.profile_root / 'waydroid.cfg'
        self.cfg.write_bytes(b'[waydroid]\narch = x86_64\n# generated fixture\n')
        self.cfg.chmod(0o644)
        self.source = self.base / 'source'
        manifest = build(self.source, self.metadata, self.cfg)
        self.generation = manifest['configurationDigest']
        self.pin = sha((self.source / 'bundle-manifest.json').read_bytes())

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), CONFIG)
        return {'configurationDigest': self.generation, 'serviceManifestSha256': 'b' * 64}

    def install(self, inactive=None, real=False):
        return P._install_machine_headless(self.copier, str(self.source), self.root, self.metadata_root, self.profile_root,
                                            validate=None if real else self.validate, inactive=inactive or (lambda: None))

    def register(self, inactive=None, real=False):
        return P._register_machine_headless(self.copier, self.root / 'headless-runtime', self.systemd, self.metadata_root, self.profile_root,
                                             validate=None if real else self.validate, inactive=inactive or (lambda: None))

    def test_copy_and_registration_are_new_only_and_do_not_start_anything(self):
        installed, registered = self.install(), self.register()
        self.assertEqual(installed['headlessManifestSha256'], self.pin)
        for receipt in (installed, registered):
            self.assertTrue(receipt['headlessPolicyRegenerated'])
            self.assertFalse(receipt['accountValidated'])
            for key in ('servicesActivated', 'readyForActivation', 'installable', 'androidOperationsExecuted'):
                self.assertFalse(receipt[key])
        target = self.systemd / 'tesla-waydroid-headless-session.service.d/60-droiddeck-headless.conf'
        self.assertEqual(target.read_bytes(), (self.source / 'systemd/60-droiddeck-headless.conf').read_bytes())
        self.assertEqual((target.stat().st_uid, target.stat().st_gid, target.stat().st_mode & 0o777, target.stat().st_nlink), (0, 0, 0o644, 1))
        for operation in (self.install, self.register):
            with self.assertRaises(ValueError):
                operation()

    def test_real_nss_nondefault_copy_and_registration(self):
        for receipt in (self.install(real=True), self.register(real=True)):
            self.assertTrue(receipt['accountValidated'])
            self.assertEqual(receipt['configurationDigest'], self.generation)
            self.assertFalse(receipt['servicesActivated'])

    def test_readonly_policy_uses_real_nss_without_requiring_stopped_services(self):
        with mock.patch.object(P, '_assert_codec_stopped', side_effect=AssertionError('read-only policy must not require downtime')):
            policy = P._machine_headless_policy(self.copier, self.metadata_root, self.profile_root)
        self.assertEqual(policy['headlessManifestSha256'], self.pin)
        self.assertEqual(policy['waydroidConfigurationSha256'], sha(self.cfg.read_bytes()))
        self.assertTrue(policy['accountValidated'])
        self.assertFalse(policy['servicesActivated'])
        self.assertFalse((self.root / 'headless-runtime').exists())

    def test_busy_guard_refuses_before_output(self):
        def busy():
            raise ValueError('generated active graph')
        with self.assertRaises(ValueError):
            self.install(busy)
        self.assertFalse((self.root / 'headless-runtime').exists())

    def test_modified_script_with_rewritten_manifest_refuses(self):
        file = self.source / 'headless-runtime.py'
        file.write_bytes(b'generated nonexecuted sentinel')
        marker = self.source / 'bundle-manifest.json'
        m = json.loads(marker.read_bytes())
        next(r for r in m['files'] if r['path'] == file.name).update(size=file.stat().st_size, sha256=sha(file.read_bytes()))
        marker.write_text(json.dumps(m, indent=2) + '\n')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'headless-runtime').exists())

    def test_writable_or_linked_profile_is_refused(self):
        self.cfg.chmod(0o666)
        with self.assertRaises(ValueError):
            self.install()
        self.cfg.chmod(0o644)
        link = self.base / 'profile-link'
        os.link(self.cfg, link)
        with self.assertRaises(ValueError):
            self.install()
        link.unlink()
        self.cfg.rename(link)
        self.cfg.symlink_to(link)
        with self.assertRaises(OSError):
            self.install()

    def test_profile_mutation_during_copy_prevents_publication(self):
        copy = self.copier.copy_image
        def mutate(*args, **kwargs):
            result = copy(*args, **kwargs)
            self.cfg.write_bytes(b'changed generated profile')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=mutate), self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'headless-runtime').exists())

    def test_profile_mutation_after_publication_is_uncertain(self):
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            self.cfg.write_bytes(b'changed generated profile')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.install()
        self.assertTrue((self.root / 'headless-runtime').is_dir())

    def test_inactive_check_timeout_after_publication_is_uncertain(self):
        def timed_out():
            if (self.root / 'headless-runtime').exists():
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.install(timed_out)
        self.assertTrue((self.root / 'headless-runtime').is_dir())

    def test_existing_dropin_is_preserved(self):
        self.install()
        target = self.systemd / 'tesla-waydroid-headless-session.service.d'
        target.mkdir()
        sentinel = target / 'foreign.conf'
        sentinel.write_bytes(b'generated existing sentinel')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(sentinel.read_bytes(), b'generated existing sentinel')

    def test_profile_mutation_after_registration_is_uncertain(self):
        self.install()
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            self.cfg.write_bytes(b'changed generated profile')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.register()
        self.assertTrue((self.systemd / 'tesla-waydroid-headless-session.service.d/60-droiddeck-headless.conf').is_file())

    def test_profile_change_between_copy_and_registration_refuses(self):
        self.install()
        self.cfg.write_bytes(b'another generated profile')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(list(self.systemd.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
