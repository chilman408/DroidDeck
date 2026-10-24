# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module('machine_guest_promote', 'promote-images.py')
G = module('legacy_guest_fixtures', 'guest_payloads_test.py')
sha = lambda data: hashlib.sha256(data).hexdigest()


class MachineGuestCli(unittest.TestCase):
    def test_fixed_readonly_and_install_commands_have_no_data_pid_or_hash_overrides(self):
        self.assertTrue(callable(getattr(P, '_machine_guest_data_policy', None)), 'Machine guest-data policy missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_machine_guest_data_policy') as inspect, \
                mock.patch.object(P, '_install_machine_guest_payloads') as install:
            P.main(['--machine-guest-data-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            P.main(['--machine-guest-payloads'])
            self.assertEqual(install.call_args.args, (copier.return_value,))
            for option in ('--machine-guest-data-policy', '--machine-guest-payloads'):
                for extra in ('--force', '/arbitrary/data', '123', 'a' * 64):
                    with self.assertRaises(ValueError):
                        P.main([option, extra])

    def test_failure_output_never_exposes_private_paths_or_metadata(self):
        self.assertTrue(callable(getattr(P, '_machine_guest_context', None)), 'Machine guest context missing')
        with mock.patch.object(P, 'main', side_effect=ValueError('generated private path')), mock.patch.object(P, 'diagnostic') as report:
            self.assertEqual(P.run_cli(['--machine-guest-data-policy']), 1)
            self.assertNotIn('private path', report.call_args.args[0])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated guest fixtures required')
class MachineGuestContext(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='dd-machine-guest-context-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.data = self.home / '.local/share/waydroid/data'
        self.tmp = self.data / 'local/tmp'
        self.tmp.mkdir(parents=True)
        self.data.chmod(0o771)
        os.chown(self.data, 1500, 1600)
        self.tmp.chmod(0o771)
        os.chown(self.tmp, 2000, 2000)
        self.metadata_root = self.root / 'metadata'
        self.metadata_root.mkdir(mode=0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.config = {'version': 1, 'user': 'generated', 'uid': 1500, 'gid': 1600,
                       'home': str(self.home), 'lanAddress': '192.168.50.5', 'port': 9443, 'hostname': 'droiddeck.example.com'}
        self.metadata.write_text(json.dumps(self.config))
        self.metadata.chmod(0o644)
        self.identity = (123, 456)

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), self.config)
        return {'config': self.config, 'configurationDigest': 'a' * 64}

    def mounted(self, pid, relative):
        self.assertEqual(pid, self.identity[0])
        return (self.data if relative == 'data' else self.tmp).stat()

    def context(self, stack, **kw):
        return P._machine_guest_context(stack, self.copier, self.metadata_root, validate=self.validate,
            process=kw.get('process', lambda: self.identity), mounted=kw.get('mounted', self.mounted),
            runtime_idle=kw.get('runtime_idle', lambda: None))

    def test_dynamic_nondefault_home_and_uid_bind_held_metadata_and_actual_mount_fixture(self):
        with ExitStack() as stack:
            ctx = self.context(stack)
            self.assertEqual(ctx['dataDevice'], self.data.stat().st_dev)
            self.assertEqual(ctx['dataInode'], self.data.stat().st_ino)
            self.assertEqual(ctx['metadataSha256'], sha(self.metadata.read_bytes()))
            self.assertEqual(ctx['configurationDigest'], 'a' * 64)
            self.assertFalse(ctx['accountValidated'])
            self.assertFalse(ctx['guestMountMatched'])
            ctx['check']()
            self.assertEqual(list(self.tmp.iterdir()), [])

    def test_process_birth_change_refuses_stale_mount(self):
        calls = 0
        def process():
            nonlocal calls
            calls += 1
            return (123, 456 if calls == 1 else 457)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.context(stack, process=process)

    def test_foreign_mounted_data_or_tmp_inode_refuses(self):
        other = self.root / 'other'
        other.mkdir()
        for selected in ('data', 'data/local/tmp'):
            def mounted(pid, relative):
                return other.stat() if relative == selected else self.mounted(pid, relative)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.context(stack, mounted=mounted)

    def test_metadata_mutation_or_same_bytes_inode_replacement_refuses_held_generation(self):
        with ExitStack() as stack:
            ctx = self.context(stack)
            original = self.metadata.read_bytes()
            self.metadata.write_bytes(b'changed generated')
            with self.assertRaises(ValueError):
                ctx['check']()
            self.metadata.write_bytes(original)
        with ExitStack() as stack:
            ctx = self.context(stack)
            replacement = self.metadata_root / 'replacement'
            replacement.write_bytes(original)
            replacement.chmod(0o644)
            os.replace(replacement, self.metadata)
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_linked_metadata_or_data_directory_and_writable_metadata_namespace_refuse(self):
        link = self.root / 'linked'
        os.link(self.metadata, link)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.context(stack)
        link.unlink()
        self.metadata_root.chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.context(stack)
        self.metadata_root.chmod(0o755)
        self.data.rename(link)
        self.data.symlink_to(link, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
            self.context(stack)

    def test_wrong_data_or_tmp_ownership_refuses(self):
        for directory, original in ((self.data, (1500, 1600)), (self.tmp, (2000, 2000))):
            os.chown(directory, 1501, 1601)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.context(stack)
            os.chown(directory, *original)

    def test_runtime_busy_or_malformed_process_identity_refuses(self):
        def busy():
            raise ValueError('generated busy runtime')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.context(stack, runtime_idle=busy)
        for identity in ((0, 1), (123, 0), (True, 1), (123,), ('123', 456)):
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.context(stack, process=lambda: identity)

    def test_readonly_policy_exports_identity_hashes_only_and_no_operations(self):
        with ExitStack() as stack:
            ctx = self.context(stack)
            with mock.patch.object(P, '_machine_guest_context', return_value=ctx):
                policy = P._machine_guest_data_policy(self.copier)
        self.assertEqual(policy['configurationDigest'], 'a' * 64)
        self.assertFalse(policy['accountValidated'])
        self.assertFalse(policy['guestMountMatched'])
        self.assertNotIn(str(self.home), json.dumps(policy))
        for key in ('servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(policy[key])
        self.assertEqual(list(self.tmp.iterdir()), [])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated guest fixtures required')
class MachineGuestInstall(unittest.TestCase):
    def setUp(self):
        self.fixture = G.GuestInstallTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def context(self, stack, copier):
        ctx = self.fixture.context(stack, copier)
        return {**ctx, 'configurationDigest': 'a' * 64, 'metadataSha256': 'b' * 64,
                'accountValidated': False, 'guestMountMatched': False}

    def install(self, context=None):
        return P._install_machine_guest_payloads(self.fixture.copier, context=context or self.context, sources=self.fixture.sources)

    def test_v2_receipt_new_namespace_and_legacy_namespace_untouched(self):
        legacy = self.fixture.tmp / P.GUEST_TARGET
        legacy.mkdir()
        (legacy / 'sentinel').write_bytes(b'generated legacy sentinel')
        result = self.install()
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['configurationDigest'], 'a' * 64)
        self.assertFalse(result['accountValidated'])
        self.assertFalse(result['guestMountMatched'])
        root = self.fixture.tmp / P.MACHINE_GUEST_TARGET
        self.assertEqual(json.loads((root / 'droiddeck-guest-receipt.json').read_bytes()), result)
        self.assertEqual((legacy / 'sentinel').read_bytes(), b'generated legacy sentinel')
        for name, (_, digest, mode) in self.fixture.pins.items():
            file = root / name
            self.assertEqual(sha(file.read_bytes()), digest)
            self.assertEqual((file.stat().st_uid, file.stat().st_gid, file.stat().st_mode & 0o777), (0, 0, mode))
        for key in ('helperProcessesStarted', 'accountsOrDrmChanged', 'browserPngFallback', 'readyForActivation', 'installable'):
            self.assertFalse(result[key])
        with self.assertRaises(ValueError):
            self.install()

    def test_existing_final_and_pending_refuse_without_overwrite(self):
        for name in (P.MACHINE_GUEST_TARGET, 'pending-droiddeck-machine-guest'):
            directory = self.fixture.tmp / name
            directory.mkdir()
            with self.assertRaises(ValueError):
                self.install()
            self.assertEqual(list(directory.iterdir()), [])
            directory.rmdir()

    def test_metadata_change_during_copy_prevents_publication(self):
        def context(stack, copier):
            ctx = self.context(stack, copier)
            def check():
                if (self.fixture.tmp / 'pending-droiddeck-machine-guest').exists():
                    raise ValueError('generated metadata changed')
            return {**ctx, 'check': check}
        with self.assertRaises(ValueError):
            self.install(context)
        self.assertFalse((self.fixture.tmp / P.MACHINE_GUEST_TARGET).exists())

    def test_postpublication_context_timeout_is_uncertain_and_retains_payload(self):
        def context(stack, copier):
            ctx = self.context(stack, copier)
            def check():
                if (self.fixture.tmp / P.MACHINE_GUEST_TARGET).exists():
                    raise P.subprocess.TimeoutExpired('generated-context', 5)
            return {**ctx, 'check': check}
        with self.assertRaises(P.PromotionUncertain):
            self.install(context)
        self.assertTrue((self.fixture.tmp / P.MACHINE_GUEST_TARGET / 'droiddeck-guest-receipt.json').is_file())


if __name__ == '__main__':
    unittest.main()
