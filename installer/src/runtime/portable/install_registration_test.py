# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('install_registration_tested', Path(__file__).with_name('install-registration.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
spec = importlib.util.spec_from_file_location('install_registration_fixture', Path(__file__).with_name('runtime_registration_transaction_test.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class Policy(unittest.TestCase):
    def test_exact_cli_no_force_adoption_or_activation(self):
        with mock.patch.object(D.R.A, '_root_linux'):
            for args in ([], ['--force'], ['--resume'], ['--install-registration', 'u', 'a', 'r', 'secret']):
                with self.assertRaises(ValueError):
                    D.main(args)
        self.assertEqual(D.MODES, ('--install-registration', '--resume-registration'))

    def test_failure_redacts_inputs(self):
        result = subprocess.run([sys.executable, '-I', '-B', D.__file__, '--force', 'private-value'],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertNotIn(b'private-value', result.stderr)
        self.assertIn(b'preserve all original targets', result.stderr)

    def test_active_worker_refuses_before_phase_calls(self):
        guard = mock.Mock(side_effect=ValueError('active'))
        with mock.patch.object(D.R, '_finish') as finish, self.assertRaises(ValueError):
            D.finish_phases({}, guard=guard)
        finish.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux inert registration fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        self.fixture = F.Native('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.root = self.fixture.root
        (self.root / 'var/lib/droiddeck').mkdir(parents=True)
        Path(self.fixture.journal).rename(D.mapped(str(self.root), D.R.JOURNAL))

    def finish(self, **kwargs):
        return D.finish_phases(self.fixture.authority, prefix=str(self.root), guard=lambda: None, **kwargs)

    def identities(self):
        targets = (*D.R.K.REGISTRATIONS.values(), *D.S.FILES, *D.S.RESET_FILES)
        return {name: Path(D.mapped(str(self.root), name)).stat().st_ino for name in targets}

    def test_all_twelve_and_repeat_preserve_original_objects(self):
        result = self.finish()
        self.assertEqual(result['registeredFiles'], 12)
        self.assertTrue(result['registrationRecoveryVerified'])
        for flag in ('servicesActivated', 'servicesEnabled', 'daemonReloaded', 'androidOperationsExecuted',
                     'preIntentRecoverySupported', 'wholeInstallationVerified', 'installable'):
            self.assertFalse(result[flag])
        before = self.identities()
        self.assertEqual(self.finish(), result)
        self.assertEqual(self.identities(), before)

    def test_real_death_between_each_phase_resumes_without_copying(self):
        original = None
        for phase in ('original', 'session-recovery'):
            child = os.fork()
            if child == 0:
                try:
                    self.finish(after_phase=lambda name: os._exit(73) if name == phase else None)
                except BaseException:
                    os._exit(74)
                os._exit(75)
            _, status = os.waitpid(child, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 73)
            observed = {str(p): p.stat().st_ino for p in self.fixture.destinations()}
            if original is None:
                original = observed
            self.assertEqual(observed, original)
            self.assertFalse(Path(D.mapped(str(self.root), D.S.RESET_JOURNAL)).exists())
        self.assertEqual(self.finish()['registeredFiles'], 12)
        self.assertEqual({str(p): p.stat().st_ino for p in self.fixture.destinations()}, original)

    def test_foreign_recovery_file_refuses_without_overwrite(self):
        target = Path(D.mapped(str(self.root), next(iter(D.S.FILES))))
        target.write_bytes(b'foreign fixture')
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(target.read_bytes(), b'foreign fixture')
        self.assertFalse(Path(D.mapped(str(self.root), D.S.JOURNAL)).exists())

    def test_empty_recovery_journal_is_not_reinitialized(self):
        journal = Path(D.mapped(str(self.root), D.S.JOURNAL))
        journal.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.finish()
        self.assertEqual(list(journal.iterdir()), [])

    def test_missing_original_intent_does_not_start_later_phases(self):
        Path(D.mapped(str(self.root), D.R.JOURNAL), 'intent.json').unlink()
        with self.assertRaises(D.R.RegistrationUncertain):
            self.finish()
        self.assertFalse(Path(D.mapped(str(self.root), D.S.JOURNAL)).exists())

    def test_cooperative_lock_refuses_second_installer(self):
        with ExitStack() as first, ExitStack() as second:
            check = D.lock(first, str(self.root))
            check()
            with self.assertRaises(BlockingIOError):
                D.lock(second, str(self.root))


if __name__ == '__main__':
    unittest.main()
