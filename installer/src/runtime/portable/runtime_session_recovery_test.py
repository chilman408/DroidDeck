# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('session_recovery_tests', Path(__file__).with_name('runtime-session-recovery.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)


class Boundary(unittest.TestCase):
    def test_bounded_failure_only_recipe_has_no_healthy_runtime_deadline(self):
        unit, dropin = S.FILES.values()
        self.assertIn(b'ExecStart=/usr/bin/python3 -I -B /var/lib/droiddeck/gpu-recovery/runtime/portable/fresh-gpu.py --restart-session\n', unit)
        self.assertIn(b'TimeoutStartSec=420s\n', unit)
        self.assertIn(b'StartLimitBurst=2\n', unit)
        self.assertIn(b'RestartMode=direct\n', dropin)
        self.assertIn(b'OnFailure=droiddeck-session-recovery.service\n', dropin)
        for raw in (unit, dropin):
            for text in (b'RuntimeMax', b'OnCalendar', b'OnActiveSec', b'WantedBy', b'rm ', b'sh -c', b'Account'):
                self.assertNotIn(text, raw)
        self.assertEqual(list(S.RESET_FILES.values()),
            [b'[Service]\nExecStartPre=/usr/bin/systemctl reset-failed tesla-waydroid-headless-session.service\n'])

    def test_closed_cli_redacts_arbitrary_inputs(self):
        with mock.patch.object(S.R.A, '_root_linux'):
            for args in ([], ['--force'], ['--install', 'u', 's', 'r', '-', '/foreign']):
                with self.assertRaises(ValueError): S.main(args)
        result = subprocess.run([sys.executable, '-I', '-B', S.__file__, '--foreign', 'private-value'], capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertNotIn(b'private-value', result.stderr)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0, 'Root Linux isolated fixtures required')
class Native(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='droiddeck-session-recovery-', dir='/root')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = str(self.root / 'transaction')
        self.paths = S._paths(str(self.root))
        for path in self.paths.values(): path.parent.mkdir(parents=True, mode=0o755, exist_ok=True)
        self.authority = {'check': lambda: None, 'read_plan': lambda: b'generated original authority'}

    def prepare(self, **kwargs):
        S.prepare(self.authority, self.journal, str(self.root), guard=kwargs.get('guard', lambda: None))

    def finish(self, **kwargs):
        return S.finish(self.authority, self.journal, str(self.root), guard=kwargs.get('guard', lambda: None),
                        after_publish=kwargs.get('after_publish'))

    def test_complete_and_repeated_resume_preserve_owned_inodes_and_parent_modes(self):
        parent = next(iter(self.paths.values())).parent
        parent.chmod(0o750)
        fds = set(os.listdir('/proc/self/fd'))
        self.prepare()
        self.assertTrue(self.finish()['registrationRecoveryVerified'])
        inodes = {name: path.stat().st_ino for name, path in self.paths.items()}
        for name, path in self.paths.items():
            self.assertEqual(path.read_bytes(), S.FILES[name])
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(parent.stat().st_mode & 0o777, 0o750)
        self.assertFalse(self.finish()['runtimeRecoveryVerified'])
        self.assertEqual(inodes, {name: path.stat().st_ino for name, path in self.paths.items()})
        self.assertEqual(set(os.listdir('/proc/self/fd')), fds)

    def test_actual_process_death_after_first_publish_resumes_without_adoption(self):
        self.prepare()
        pid = os.fork()
        if pid == 0:
            self.finish(after_publish=lambda _: os._exit(73))
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        self.assertEqual(sum(path.exists() for path in self.paths.values()), 1)
        self.assertTrue(self.finish()['registrationRecoveryVerified'])

    def test_foreign_same_byte_file_refused_before_second_publication(self):
        self.prepare()
        def interrupted(_): raise OSError('generated crash')
        with self.assertRaises(OSError): self.finish(after_publish=interrupted)
        first = self.paths[sorted(self.paths)[0]]
        saved = first.with_name('saved')
        first.rename(saved)
        first.write_bytes(saved.read_bytes())
        first.chmod(0o644)
        with self.assertRaises(ValueError): self.finish()
        self.assertFalse(self.paths[sorted(self.paths)[1]].exists())

    def test_existing_registration_or_uncommitted_journal_refuses_and_preserves(self):
        target = next(iter(self.paths.values()))
        target.write_bytes(b'foreign')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(target.read_bytes(), b'foreign')
        self.assertFalse(Path(self.journal).exists())
        target.unlink()
        Path(self.journal).mkdir(mode=0o700)
        with self.assertRaises(ValueError): self.finish()

    def test_active_worker_and_changed_authority_refuse(self):
        def active(): raise ValueError('generated active worker')
        with self.assertRaises(ValueError): self.prepare(guard=active)
        self.assertFalse(Path(self.journal).exists())
        self.prepare()
        with self.assertRaises(ValueError): self.finish(guard=active)
        self.authority['read_plan'] = lambda: b'changed'
        with self.assertRaises(ValueError): self.finish()
        self.assertFalse(any(path.exists() for path in self.paths.values()))

    def test_parent_sync_failure_preserves_intent_and_supports_explicit_resume(self):
        self.prepare()
        sync = S.os.fsync
        def fail(_): raise OSError('generated parent sync failure')
        with mock.patch.object(S.os, 'fsync', side_effect=fail), self.assertRaises(OSError): self.finish()
        self.assertEqual(sum(path.exists() for path in self.paths.values()), 1)
        self.assertTrue(self.finish()['registrationRecoveryVerified'])

    def test_symlinked_final_and_missing_staged_object_refuse(self):
        self.prepare()
        record = __import__('json').loads((Path(self.journal) / 'intent.json').read_bytes())
        row = record['rows'][0]
        pending = self.paths[row['path']].parent / row['pending']
        pending.unlink()
        self.paths[row['path']].symlink_to('/absent-generated')
        with self.assertRaises((OSError, ValueError)): self.finish()

    def test_reset_dropin_has_separate_owned_intent_and_keeps_first_generation(self):
        self.prepare()
        self.finish()
        original = {name: (path.stat().st_ino, path.read_bytes()) for name, path in self.paths.items()}
        journal = str(self.root / 'reset-transaction')
        S.prepare(self.authority, journal, str(self.root), guard=lambda: None, files=S.RESET_FILES)
        result = S.finish(self.authority, journal, str(self.root), guard=lambda: None, files=S.RESET_FILES)
        self.assertEqual(result['registeredFiles'], 1)
        self.assertEqual(original, {name: (path.stat().st_ino, path.read_bytes()) for name, path in self.paths.items()})
        self.assertEqual(Path(journal).stat().st_mode & 0o777, 0o700)
        for name, path in S._paths(str(self.root), S.RESET_FILES).items():
            self.assertEqual(path.read_bytes(), S.RESET_FILES[name])
        with self.assertRaises(ValueError):
            S.prepare(self.authority, journal, str(self.root), guard=lambda: None, files=S.RESET_FILES)


if __name__ == '__main__': unittest.main()
