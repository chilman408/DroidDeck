# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('registration_transaction_tests', Path(__file__).with_name('runtime-registration-transaction.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


class Boundary(unittest.TestCase):
    def test_fixed_targets_no_activation_and_canonical_encoding(self):
        self.assertEqual(len(R.K.REGISTRATIONS), 9)
        self.assertEqual(R.JOURNAL, '/var/lib/droiddeck/registration-transaction')
        self.assertNotEqual(R._encode({'value': True}), R._encode({'value': 1}))
        for args in ([], ['--force'], ['--install-new', 'u', 's', 'r', 'private-value']):
            with mock.patch.object(R.A, '_root_linux'), self.assertRaises(ValueError):
                R.main(args)

    def test_missing_owned_authority_refuses_before_new_files(self):
        with mock.patch.object(R.A, '_root_linux'), self.assertRaises(ValueError):
            R._prepare({}, {'check': lambda: None, 'observe': lambda: {}}, '/not-used')

    def test_cli_failure_redacts_inputs(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(R.__file__)), '--unknown', 'private-value'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertNotIn('private-value', result.stderr)
        self.assertIn('no services activated', result.stderr)

    def test_fixed_inactive_query_never_enables_or_starts_services(self):
        result = mock.Mock(stdout=b'inactive\n\ninactive\ninactive\ninactive\ninactive\n', stderr=b'')
        with mock.patch.object(R.subprocess, 'run', return_value=result) as run:
            R._inactive()
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/systemctl', 'show', '--property=ActiveState', '--value', *R.UNITS])
        self.assertEqual((kwargs['cwd'], kwargs['timeout'], kwargs['check']), ('/', 10, True))
        self.assertNotIn('DBUS_SYSTEM_BUS_ADDRESS', kwargs['env'])
        for raw in (b'', b'inactive\n' * 4, b'inactive\n' * 6, b'active\n' + b'inactive\n' * 4):
            with mock.patch.object(R.subprocess, 'run', return_value=mock.Mock(stdout=raw, stderr=b'')), self.assertRaises(ValueError):
                R._inactive()


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='droiddeck-registration-transaction-', dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = str(self.root / 'transaction')
        self.files, directories, links, self.payload = [], {}, [], {}
        for top in R.K.ROOTS:
            (self.root / top.lstrip('/')).mkdir(mode=0o700, parents=True)

        def directory(name, mode=0o755):
            path = self.root / name.lstrip('/')
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700 if name in R.K.ROOTS else mode)
            directories[name] = {'path': name, 'mode': mode, 'uid': 0, 'gid': 0}

        for name in R.K.ROOTS:
            directory(name)
        directory('/opt/droiddeck/runtime')
        directory('/opt/droiddeck/libgbinder')
        for index in range(250):
            name = '/etc/droiddeck/runtime.json' if index == 0 else '/opt/droiddeck/runtime/f' + str(index)
            raw = ('generated-' + str(index)).encode('ascii')
            path = self.root / name.lstrip('/')
            path.write_bytes(raw)
            path.chmod(0o644)
            self.files.append({'path': name, 'size': len(raw), 'sha256': R._sha(raw),
                'mode': 0o644, 'uid': 0, 'gid': 0, 'registration': False})
        for index in range(4):
            name = '/opt/droiddeck/libgbinder/alias' + str(index)
            target = '../runtime/f1'
            (self.root / name.lstrip('/')).symlink_to(target)
            links.append({'path': name, 'target': target, 'uid': 0, 'gid': 0})
        for index, name in enumerate(sorted(R.K.REGISTRATIONS.values())):
            raw = ('generated inert recipe ' + str(index) + '\n').encode('ascii')
            self.payload[name] = raw
            self.files.append({'path': name, 'size': len(raw), 'sha256': R._sha(raw),
                'mode': 0o644, 'uid': 0, 'gid': 0, 'registration': True})
        self.plan = {'kind': 'final-install-placement-plan', 'files': self.files,
            'registrationTargets': sorted(R.K.REGISTRATIONS.values()),
            'ownedDirectories': list(directories.values()), 'links': links}
        self.authority = {'check': lambda: None, 'read_plan': lambda: R._encode(self.plan),
            'read_file': lambda name: self.payload[name]}
        self.owned = {'check': lambda: None, 'observe': lambda: {'ownedTargetPayloadsVerified': True}}

    def prepare(self):
        return R._prepare(self.authority, self.owned, self.journal, str(self.root))

    def finish(self, **kwargs):
        return R._finish(self.authority, self.journal, str(self.root), **kwargs)

    def destinations(self):
        return [self.root / name.lstrip('/') for name in sorted(self.payload)]

    def test_success_permissions_bytes_and_idempotent_explicit_recovery(self):
        descriptors = set(os.listdir('/proc/self/fd'))
        existing = self.root / 'etc/pam.d'
        existing.mkdir(mode=0o750)
        before = existing.stat().st_mode
        self.prepare()
        self.assertTrue(all(not p.exists() for p in self.destinations()))
        result = self.finish()
        self.assertTrue(result['servicesRegistered'])
        self.assertTrue(result['registrationPublicationRecoveryVerified'])
        self.assertFalse(result['runtimeRecoveryVerified'])
        for key in ('servicesActivated', 'daemonReloaded', 'servicesEnabled', 'readyForActivation', 'installable'):
            self.assertFalse(result[key])
        for p, raw in zip(self.destinations(), [self.payload[n] for n in sorted(self.payload)]):
            self.assertEqual(p.read_bytes(), raw)
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o644)
        self.assertEqual(existing.stat().st_mode, before)
        self.assertEqual(stat.S_IMODE((self.root / 'opt/droiddeck').stat().st_mode), 0o755)
        identities = [(p.stat().st_dev, p.stat().st_ino) for p in self.destinations()]
        self.assertEqual(self.finish(), result)
        self.assertEqual([(p.stat().st_dev, p.stat().st_ino) for p in self.destinations()], identities)
        self.assertEqual(set(os.listdir('/proc/self/fd')), descriptors)

    def test_process_death_after_first_rename_recovers_original_objects(self):
        self.prepare()
        child = os.fork()
        if child == 0:
            try:
                self.finish(after_publish=lambda _: os._exit(73))
            except BaseException:
                os._exit(74)
            os._exit(75)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertEqual(sum(p.exists() for p in self.destinations()), 1)
        first = self.destinations()[0].stat().st_ino
        self.assertEqual(self.finish()['registeredFiles'], 9)
        self.assertEqual(self.destinations()[0].stat().st_ino, first)

    def test_existing_target_is_never_overwritten_or_adopted(self):
        target = self.destinations()[-1]
        target.parent.mkdir(parents=True)
        target.write_bytes(b'foreign')
        target.chmod(0o644)
        with self.assertRaises(R.RegistrationUncertain):
            self.prepare()
        self.assertEqual(target.read_bytes(), b'foreign')
        self.assertFalse(self.destinations()[0].exists())

    def test_foreign_final_target_refuses_before_any_publication_or_root_chmod(self):
        self.prepare()
        target = self.destinations()[-1]
        target.write_bytes(self.payload[sorted(self.payload)[-1]])
        target.chmod(0o644)
        with self.assertRaises(R.RegistrationUncertain):
            self.finish()
        self.assertFalse(self.destinations()[0].exists())
        self.assertEqual(stat.S_IMODE((self.root / 'opt/droiddeck').stat().st_mode), 0o700)

    def test_same_byte_replacement_of_owned_payload_is_not_adopted(self):
        self.prepare()
        p = self.root / 'opt/droiddeck/runtime/f1'
        old = p.with_name('old-inode')
        p.rename(old)
        p.write_bytes(old.read_bytes())
        p.chmod(0o644)
        old.unlink()
        with self.assertRaises(ValueError):
            self.finish()
        self.assertTrue(all(not p.exists() for p in self.destinations()))

    def test_payload_tampering_or_unplanned_extra_file_refuses(self):
        self.prepare()
        (self.root / 'etc/droiddeck/extra').write_bytes(b'foreign')
        with self.assertRaises(ValueError):
            self.finish()

    def test_wrong_plan_or_linked_intent_refuses(self):
        self.prepare()
        intent = Path(self.journal) / 'intent.json'
        intent.rename(intent.with_suffix('.original'))
        intent.symlink_to('intent.original')
        with self.assertRaises(R.RegistrationUncertain):
            self.finish()

    def test_invalid_intent_canonical_json_refuses(self):
        self.prepare()
        intent = Path(self.journal) / 'intent.json'
        intent.write_bytes(intent.read_bytes().rstrip())
        with self.assertRaises(ValueError):
            self.finish()

    def test_changed_registration_parent_refuses(self):
        self.prepare()
        parent = self.destinations()[0].parent
        parent.chmod(0o750)
        with self.assertRaises(R.RegistrationUncertain):
            self.finish()
        self.assertFalse(self.destinations()[0].exists())

    def test_interrupted_prepare_is_preserved_and_not_resumed(self):
        with mock.patch.object(R.J, '_rename_new', side_effect=OSError('generated sync fault')):
            with self.assertRaises(R.RegistrationUncertain):
                self.prepare()
        self.assertTrue((Path(self.journal) / '.intent.pending').is_file())
        with self.assertRaises(R.RegistrationUncertain):
            self.finish()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_mutation_after_publication_poisoned_then_explicit_resume_refuses(self):
        self.prepare()
        def fail(_):
            self.destinations()[0].write_bytes(b'foreign')
        with self.assertRaises(R.RegistrationUncertain):
            self.finish(after_publish=fail)
        with self.assertRaises(R.RegistrationUncertain):
            self.finish()

    def test_worker_activation_during_finalization_preserves_unpublished_files(self):
        self.prepare()
        active = False
        def guard():
            if active:
                raise ValueError('generated active worker')
        def activate(_):
            nonlocal active
            active = True
        with self.assertRaises(R.RegistrationUncertain):
            self.finish(after_publish=activate, check_runtime=guard)
        self.assertEqual(sum(p.exists() for p in self.destinations()), 1)

    def test_parent_sync_failure_after_rename_can_resume_owned_publication(self):
        self.prepare()
        original, calls = os.fsync, []
        def fail(fd):
            calls.append(fd)
            if len(calls) == 3:
                raise OSError('generated registration parent sync failure')
            original(fd)
        with mock.patch.object(R.os, 'fsync', side_effect=fail), self.assertRaises(R.RegistrationUncertain):
            self.finish()
        self.assertEqual(sum(p.exists() for p in self.destinations()), 1)
        self.assertTrue(self.finish()['registrationOwnershipVerified'])


if __name__ == '__main__':
    unittest.main()
