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

SPEC = importlib.util.spec_from_file_location('machine_owned_promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda data: hashlib.sha256(data).hexdigest()
ORIGINAL = b'[waydroid]\narch = x86_64\nimages_path = /etc/waydroid-extra/images\nvendor_type = VANILLA\n\n[properties]\n\n'
CONFIG = {'version': 1, 'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman',
          'lanAddress': '192.168.2.253', 'port': 9443, 'hostname': 'device.teslaandroid.com'}


def build(source, metadata, profiles):
    script = ('const fs=require("node:fs"),c=require("node:crypto"),d=process.argv[1],'
              'config=JSON.parse(fs.readFileSync(process.argv[2])),p=JSON.parse(process.argv[3]);'
              'const r={kind:"protected-machine-gpu-profile-policy",configurationDigest:require(d+"/configuration-generation").configurationDigest(config),'
              'serviceManifestSha256:"b".repeat(64),metadataSha256:c.createHash("sha256").update(fs.readFileSync(process.argv[2])).digest("hex"),'
              'accountValidated:true,servicesActivated:false,installable:false,readyForActivation:false,androidOperationsExecuted:false,'
              'pidManifestSha256:"c".repeat(64),...p,gpuProfilePolicyRegenerated:true,codecsVerified:true,independentRecoveryArmed:false};'
              'require(d+"/headless-owned-bundle").buildOwnedHeadlessBundle(config,r,process.argv[4]);')
    result = subprocess.run(['/usr/bin/node' if os.name == 'posix' else 'node', '-e', script,
                             str(Path(__file__).parent), str(metadata), json.dumps(profiles), str(source)], capture_output=True, timeout=12)
    if result.returncode:
        raise AssertionError(result.stderr.decode())
    return json.loads((source / 'bundle-manifest.json').read_bytes())


class OwnedRecipe(unittest.TestCase):
    def test_independent_recipe_matches_accepted_real_component(self):
        self.assertTrue(callable(getattr(P, '_owned_headless_recipe_policy', None)), 'Protected owned recipe missing')
        self.assertEqual(P._owned_headless_recipe_policy(P.PID_CONFIGURATION_DIGEST,
            '2d38c5fd4407e119a02fc16435c747cc659e231361b27bb4f50aee191dd51d73',
            '40e24715fcd8c3e9bc1003c934fc93466f6cfdac18b8496da35ed13bd6def0ba',
            '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'),
            'd8a42ca6b680d2cd23dab8bbb19e2126127f460d9a717f6a6e06b6ef56aa2318')

    def test_nondefault_recipe_matches_javascript_and_refuses_bad_or_duplicate_hashes(self):
        self.assertTrue(callable(getattr(P, '_owned_headless_recipe_policy', None)), 'Protected owned recipe missing')
        with tempfile.TemporaryDirectory(prefix='dd-owned-recipe-') as directory:
            root = Path(directory)
            metadata = root / 'runtime.json'
            metadata.write_text(json.dumps(CONFIG))
            profiles = P._machine_gpu_profiles(ORIGINAL)
            m = build(root / 'source', metadata, profiles)
            args = [m['configurationDigest'], *profiles.values()]
            self.assertEqual(P._owned_headless_recipe_policy(*args), sha((root / 'source/bundle-manifest.json').read_bytes()))
            for index in range(4):
                for bad in (None, True, '', 'A' * 64):
                    changed = list(args)
                    changed[index] = bad
                    with self.assertRaises(ValueError):
                        P._owned_headless_recipe_policy(*changed)
            with self.assertRaises(ValueError):
                P._owned_headless_recipe_policy(args[0], args[1], args[2], args[2])

    def test_cli_fixed_destinations_and_no_force_hash_or_backup_overrides(self):
        self.assertTrue(callable(getattr(P, '_install_machine_owned_headless', None)), 'Protected owned installer missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_install_machine_owned_headless') as install, \
                mock.patch.object(P, '_register_machine_owned_headless') as register, mock.patch.object(P, '_machine_owned_headless_policy') as inspect:
            P.main(['--machine-owned-headless', '/source'])
            self.assertEqual(install.call_args.args, (copier.return_value, '/source', Path('/opt/droiddeck'),
                Path('/etc/droiddeck'), Path('/var/lib/droiddeck/waydroid-profile')))
            P.main(['--register-machine-owned-headless'])
            self.assertEqual(register.call_args.args, (copier.return_value, Path('/opt/droiddeck/headless-owned-runtime'),
                Path('/etc/systemd/system'), Path('/etc/droiddeck'), Path('/var/lib/droiddeck/waydroid-profile')))
            P.main(['--machine-owned-headless-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            for args in (['--machine-owned-headless', '/source', '--force'], ['--machine-owned-headless-policy', '/override'],
                         ['--register-machine-owned-headless', '/override']):
                with self.assertRaises(ValueError):
                    P.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and Path('/opt/droiddeck/artifact-layout.json').is_file(), 'Root Linux protected artifact receipt required')
class OwnedInstallation(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='dd-machine-owned-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'droiddeck'
        self.root.mkdir(mode=0o755)
        for name in ('guest', 'libgbinder', 'frontend', 'runtime', 'pid-compat'):
            (self.root / name).mkdir(mode=0o755)
        marker = self.root / 'artifact-layout.json'
        marker.write_bytes(Path('/opt/droiddeck/artifact-layout.json').read_bytes())
        marker.chmod(0o600)
        self.metadata_root, self.systemd, self.backup = [self.base / name for name in ('metadata', 'systemd', 'backup')]
        for directory in (self.metadata_root, self.systemd, self.backup):
            directory.mkdir(mode=0o700 if directory == self.backup else 0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.metadata.write_text(json.dumps(CONFIG, indent=2) + '\n')
        self.metadata.chmod(0o644)
        for name, data in {'original.cfg': ORIGINAL, 'baseline.cfg': P.profile_candidate(ORIGINAL),
                           'gpu.cfg': P.profile_candidate(ORIGINAL, gpu=True)}.items():
            file = self.backup / name
            file.write_bytes(data)
            file.chmod(0o600)
        self.source = self.base / 'source'
        self.profiles = P._machine_gpu_profiles(ORIGINAL)
        m = build(self.source, self.metadata, self.profiles)
        self.generation = m['configurationDigest']
        self.pin = sha((self.source / 'bundle-manifest.json').read_bytes())

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), CONFIG)
        return {'configurationDigest': self.generation, 'serviceManifestSha256': 'b' * 64}

    def install(self, inactive=None, real=False, verify=None):
        return P._install_machine_owned_headless(self.copier, str(self.source), self.root, self.metadata_root, self.backup,
            validate=None if real else self.validate, inactive=inactive or (lambda: None), verify_codecs=verify or (lambda _: None))

    def register(self, inactive=None, real=False):
        return P._register_machine_owned_headless(self.copier, self.root / 'headless-owned-runtime', self.systemd,
            self.metadata_root, self.backup, validate=None if real else self.validate,
            inactive=inactive or (lambda: None), verify_codecs=lambda _: None)

    def test_new_only_copy_and_registration_no_legacy_target_or_service_start(self):
        before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()}
        for r in (self.install(), self.register()):
            self.assertEqual(r['ownedHeadlessManifestSha256'], self.pin)
            self.assertTrue(r['ownedHeadlessPolicyRegenerated'])
            self.assertFalse(r['accountValidated'])
            for key in ('servicesActivated', 'readyForActivation', 'installable', 'androidOperationsExecuted', 'codecsVerified'):
                self.assertFalse(r[key])
        self.assertFalse((self.root / 'headless-runtime').exists())
        target = self.systemd / 'tesla-waydroid-headless-session.service.d/60-droiddeck-headless.conf'
        self.assertEqual(target.read_bytes(), (self.source / 'systemd/60-droiddeck-headless.conf').read_bytes())
        self.assertEqual((target.stat().st_uid, target.stat().st_gid, target.stat().st_mode & 0o777, target.stat().st_nlink), (0, 0, 0o644, 1))
        self.assertEqual(before, {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.backup.iterdir()})
        for op in (self.install, self.register):
            with self.assertRaises(ValueError):
                op()

    def test_real_nss_nondefault_identity_but_no_codec_acceptance_from_fixture_override(self):
        for r in (self.install(real=True), self.register(real=True)):
            self.assertTrue(r['accountValidated'])
            self.assertFalse(r['codecsVerified'])

    def test_readonly_policy_needs_no_inactive_services(self):
        with mock.patch.object(P, '_assert_codec_stopped', side_effect=AssertionError('readonly requires no downtime')):
            r = P._machine_owned_headless_policy(self.copier, self.metadata_root, self.backup, verify_codecs=lambda _: None)
        self.assertEqual(r['ownedHeadlessManifestSha256'], self.pin)
        self.assertTrue(r['accountValidated'])
        self.assertFalse(r['codecsVerified'])
        self.assertNotIn('VANILLA', json.dumps(r))

    def test_busy_or_codec_failure_before_any_output(self):
        def deny(*_):
            raise ValueError('generated refusal')
        for kwargs in ({'inactive': deny}, {'verify': deny}):
            with self.assertRaises(ValueError):
                self.install(**kwargs)
            self.assertFalse((self.root / 'headless-owned-runtime').exists())
            self.assertFalse(any(p.name.startswith('pending-') for p in self.root.iterdir()))

    def test_modified_source_manifest_and_legacy_component_are_refused(self):
        file = self.source / 'headless-owned-runtime.py'
        file.write_bytes(b'generated nonexecuted sentinel')
        marker = self.source / 'bundle-manifest.json'
        m = json.loads(marker.read_bytes())
        next(r for r in m['files'] if r['path'] == file.name).update(size=file.stat().st_size, sha256=sha(file.read_bytes()))
        marker.write_text(json.dumps(m, indent=2) + '\n')
        with self.assertRaises(ValueError):
            self.install()
        with self.assertRaises(ValueError):
            P.component_records(marker.read_bytes(), headless_policy={'configurationDigest': self.generation,
                'waydroidConfigurationSha256': self.profiles['gpuConfigurationSha256']})

    def test_linked_metadata_or_backup_refused(self):
        for file in (self.metadata, self.backup / 'original.cfg'):
            link = self.base / 'linked'
            os.link(file, link)
            with self.assertRaises(ValueError):
                self.install()
            link.unlink()
        self.backup.chmod(0o777)
        with self.assertRaises(ValueError):
            self.install()

    def test_foreign_backup_pair_or_inventory_refused(self):
        (self.backup / 'gpu.cfg').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.install()
        (self.backup / 'gpu.cfg').write_bytes(P.profile_candidate(ORIGINAL, gpu=True))
        (self.backup / 'extra').write_bytes(b'generated')
        with self.assertRaises(ValueError):
            self.install()

    def test_metadata_mutation_during_copy_prevents_publication(self):
        copy = self.copier.copy_image
        def mutate(*args, **kwargs):
            result = copy(*args, **kwargs)
            self.metadata.write_bytes(b'changed generated metadata')
            return result
        with mock.patch.object(self.copier, 'copy_image', side_effect=mutate), self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'headless-owned-runtime').exists())

    def test_backup_mutation_after_copy_publication_is_uncertain(self):
        rename = P.rename_exclusive
        def mutate(*args):
            result = rename(*args)
            (self.backup / 'gpu.cfg').write_bytes(b'changed generated backup')
            return result
        with mock.patch.object(P, 'rename_exclusive', side_effect=mutate), self.assertRaises(P.PromotionUncertain):
            self.install()
        self.assertTrue((self.root / 'headless-owned-runtime').is_dir())

    def test_guard_timeout_after_copy_publication_is_uncertain(self):
        def timeout():
            if (self.root / 'headless-owned-runtime').exists():
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.install(inactive=timeout)
        self.assertTrue((self.root / 'headless-owned-runtime').is_dir())

    def test_existing_dropin_preserved(self):
        self.install()
        directory = self.systemd / 'tesla-waydroid-headless-session.service.d'
        directory.mkdir()
        (directory / 'foreign.conf').write_bytes(b'generated sentinel')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual((directory / 'foreign.conf').read_bytes(), b'generated sentinel')

    def test_backup_mutation_between_copy_and_registration_refuses(self):
        self.install()
        (self.backup / 'baseline.cfg').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.register()
        self.assertEqual(list(self.systemd.iterdir()), [])

    def test_guard_timeout_after_registration_is_uncertain(self):
        self.install()
        def timeout():
            if (self.systemd / 'tesla-waydroid-headless-session.service.d').exists():
                raise subprocess.TimeoutExpired(['/usr/bin/systemctl', 'show'], 5)
        with self.assertRaises(P.PromotionUncertain):
            self.register(inactive=timeout)
        self.assertTrue((self.systemd / 'tesla-waydroid-headless-session.service.d/60-droiddeck-headless.conf').is_file())


if __name__ == '__main__':
    unittest.main()
