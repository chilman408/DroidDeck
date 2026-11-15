# SPDX-License-Identifier: GPL-3.0-only
"""Exercise boot admission/recovery decisions without touching services or media."""
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('boot_preference', Path(__file__).with_name('capture-boot-preference.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

P = {'schema': 1, 'kind': 'droidmate-gpu-boot-preference', 'packageSha256': 'a' * 64,
     'viewToken': 'b' * 32, 'loaderSha256': 'c' * 64, 'machineSha256': 'd' * 64}
BOOT = '12345678-1234-1234-1234-123456789abc'


class Backend:
    def __init__(self):
        self.boot = BOOT
        self.journal = None
        self.events = []
        self.current = {'phase': 'restored', 'mode': 'gpu', 'updaterLeaseHeld': False}
        self.socket = False
        self.bound = True
        self.error = None
        self.recovery_pending = False
        self.exit_delay = 0
        self.clock = 0

    def verify(self):
        self.events.append('verify')
        return self.boot

    def read(self, boot):
        return copy.deepcopy(self.journal)

    def save(self, boot, record):
        self.events.append('save:' + record['phase'])
        self.journal = copy.deepcopy(record)

    def status(self):
        if self.exit_delay:
            self.exit_delay -= 1
            return {'phase': 'restored', 'mode': 'gpu', 'updaterLeaseHeld': True}
        return self.current.copy()

    def socket_exists(self):
        return self.socket

    def preferred(self):
        if not self.bound:
            raise ValueError('Foreign operation')

    def change(self):
        self.events.append('change')
        self.current = {'phase': 'accepted', 'mode': 'gpu', 'updaterLeaseHeld': True}
        self.socket = True
        if self.error:
            if isinstance(self.error, Exception) and not self.recovery_pending:
                # Existing signed controller completes its OWN rollback.
                self.current = {'phase': 'restored', 'mode': 'gpu', 'updaterLeaseHeld': False}
                self.socket = False
            raise self.error

    def now(self):
        return self.clock

    def pause(self):
        self.clock += 1


class BootPreference(unittest.TestCase):
    def test_fresh_boot_admits_once_after_durable_intent(self):
        b = Backend()
        self.assertEqual(B.run_once(b, P)['phase'], 'gpu')
        self.assertLess(b.events.index('save:intent'), b.events.index('change'))
        before = list(b.events)
        self.assertEqual(B.run_once(b, P)['phase'], 'gpu')
        self.assertEqual(b.events.count('change'), 1)
        self.assertEqual(b.events.count('restore'), 0)
        self.assertEqual(before.count('save:gpu'), b.events.count('save:gpu'))

    def test_failed_dispatch_never_claims_terminal_cpu_or_replays(self):
        b = Backend()
        b.error = ValueError('Encoder rejected')
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertNotIn('restore', b.events)
        self.assertEqual(b.journal['phase'], 'intent')
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertEqual(b.events.count('change'), 1)

    def test_timeout_predecessor_cpu_snapshot_can_have_a_later_gpu_publication(self):
        b = Backend()
        b.error = TimeoutError('Dispatch completion unknown')
        with self.assertRaises(TimeoutError):
            B.run_once(b, P)
        self.assertEqual(b.journal['phase'], 'intent')
        self.assertNotIn('save:cpu', b.events)
        b.current = {'phase': 'accepted', 'mode': 'gpu', 'updaterLeaseHeld': True}
        b.socket = True
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertEqual(b.events.count('change'), 1)

    def test_uncertain_intent_never_restores_or_replays_healthy_owner(self):
        b = Backend()
        b.error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            B.run_once(b, P)
        self.assertEqual(b.journal['phase'], 'intent')
        b.error = None
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertEqual(b.events.count('change'), 1)
        self.assertNotIn('restore', b.events)
        self.assertEqual(b.journal['phase'], 'intent')

    def test_changed_preference_or_boot_receipt_is_not_adopted(self):
        for field, value in [('preferenceSha256', 'f' * 64), ('bootId', 'foreign')]:
            b = Backend()
            B.run_once(b, P)
            b.journal[field] = value
            before = list(b.events)
            with self.assertRaises(ValueError):
                B.run_once(b, P)
            self.assertEqual(b.events.count('change'), before.count('change'))
            self.assertNotIn('restore', b.events)

    def test_pending_previous_recovery_prevents_new_intent(self):
        b = Backend()
        b.current = {'phase': 'accepted', 'mode': 'gpu', 'updaterLeaseHeld': True}
        b.socket = True
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertIsNone(b.journal)
        self.assertNotIn('change', b.events)
        self.assertNotIn('restore', b.events)

    def test_foreign_operation_after_change_is_preserved(self):
        b = Backend()
        b.bound = False
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertNotIn('restore', b.events)
        self.assertEqual(b.journal['phase'], 'intent')

    def test_failed_recovery_retains_intent_and_reports_failure(self):
        b = Backend()
        b.error = ValueError('Encoder rejected')
        b.recovery_pending = True
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertEqual(b.journal['phase'], 'intent')
        self.assertNotIn('save:cpu', b.events)

    def test_dead_owner_never_counts_as_healthy_gpu(self):
        b = Backend()
        B.run_once(b, P)
        b.current['updaterLeaseHeld'] = False
        b.socket = False
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertEqual(b.events.count('change'), 1)
        self.assertNotIn('restore', b.events)

    def test_stale_socket_is_not_replaced_or_claimed_cpu(self):
        b = Backend()
        b.socket = True
        with self.assertRaises(ValueError):
            B.run_once(b, P)
        self.assertIsNone(b.journal)

    def test_closed_preference_and_exact_unit_ordering(self):
        for value in [{**P, 'command': 'arbitrary'}, {**P, 'schema': True}, {**P, 'viewToken': '../bad'}]:
            with self.assertRaises(ValueError):
                B.preference(B.encode(value))
        unit = B.unit(B.sha(B.encode(P)), P['packageSha256']).decode()
        recover = 'droiddeck-capture-recover@' + P['packageSha256'] + '.service'
        self.assertIn('After=' + recover, unit)
        self.assertNotIn('Requires=', unit)
        self.assertNotIn('Wants=', unit)
        self.assertNotIn('WantedBy=', unit)
        self.assertIn('ConditionPathExists=' + str(B.ROOT / 'armed.json'), unit)
        self.assertIn('Restart=no', unit)
        self.assertNotIn('RuntimeMaxSec', unit)
        self.assertIn('tesla-waydroid-runtime.service', unit)
        self.assertEqual(B.handoff(), b'[Unit]\nOnSuccess=droidmate-gpu-default.service\nOnSuccessJobMode=fail\n')
        expected = B.SERVICE.parent / (recover + '.d') / '90-droidmate-gpu-default.conf'
        self.assertEqual(B.handoff_path(P['packageSha256']), expected)

    def test_control_cannot_dispatch_restore_recover_or_stage(self):
        b = object.__new__(B.Native)
        b.p = P
        with patch.object(b, 'command') as command:
            for action in ('restore', 'recover', 'stage', 'run', '../change'):
                with self.assertRaises(ValueError):
                    b.control(action)
            command.assert_not_called()

    def test_recovery_requires_real_current_boot_completion_and_effective_command(self):
        command = '/usr/bin/python3 -I -B ' + str(B.LOADER) + ' recover ' + P['packageSha256']
        good = {'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success', 'ExecMainStatus': '0',
                'UnitFileState': 'enabled', 'FragmentPath': str(B.RECOVERY_TEMPLATE),
                'DropInPaths': str(B.handoff_path(P['packageSha256'])),
                'OnSuccess': 'droidmate-gpu-default.service', 'OnSuccessJobMode': 'fail',
                'ConditionResult': 'yes', 'AssertResult': 'yes', 'InvocationID': 'e' * 32,
                'ExecMainStartTimestampMonotonic': '1000000', 'ExecMainExitTimestampMonotonic': '2000000',
                'ExecStart': '{ path=/usr/bin/python3 ; argv[]=' + command
                + ' ; ignore_errors=no ; start_time=[fixture] ; stop_time=[fixture] ; pid=123 ; code=exited ; status=0 }'}
        B.recovered(good, P['packageSha256'], 3)
        for key, value in [('ExecMainStartTimestampMonotonic', '0'), ('ExecMainExitTimestampMonotonic', '4000000'),
                           ('DropInPaths', '/etc/systemd/system/foreign.conf'), ('InvocationID', ''),
                           ('FragmentPath', '/run/systemd/system/replacement.service'),
                           ('ExecStart', good['ExecStart'].replace(' recover ', ' restore ')),
                           ('OnSuccess', 'foreign.service'), ('OnSuccessJobMode', 'replace'),
                           ('ConditionResult', 'no')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                B.recovered({**good, key: value}, P['packageSha256'], 3)

    def test_shared_deadline_refuses_late_success(self):
        b = object.__new__(B.Native)
        b.deadline = 105
        with patch.object(b, 'verify'), patch.object(B, 'collect', return_value=b'{}') as collect:
            with patch.object(B.time, 'monotonic', side_effect=[100, 110]):
                with self.assertRaises(TimeoutError):
                    b.command(['/fixed/control'], 70)
            collect.assert_called_once_with(['/fixed/control'], 5)

    def test_active_admission_retains_completed_recovery_proof_across_reload(self):
        command = '/usr/bin/python3 -I -B ' + str(B.LOADER) + ' recover ' + P['packageSha256']
        good = {'ActiveState': 'inactive', 'SubState': 'dead', 'Result': 'success', 'ExecMainStatus': '0',
                'UnitFileState': 'enabled', 'FragmentPath': str(B.RECOVERY_TEMPLATE),
                'DropInPaths': str(B.handoff_path(P['packageSha256'])),
                'OnSuccess': 'droidmate-gpu-default.service', 'OnSuccessJobMode': 'fail',
                'ConditionResult': 'yes', 'AssertResult': 'yes', 'InvocationID': 'e' * 32,
                'ExecMainStartTimestampMonotonic': '1000000', 'ExecMainExitTimestampMonotonic': '2000000',
                'ExecStart': '{ path=/usr/bin/python3 ; argv[]=' + command
                + ' ; ignore_errors=no ; start_time=[fixture] ; stop_time=[fixture] ; pid=123 ; code=exited ; status=0 }'}
        reset = {**good, 'ExecStart': '{ path=/usr/bin/python3 ; argv[]=' + command
                 + ' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }'}
        def raw(fields):
            return ''.join(k + '=' + v + '\n' for k, v in fields.items()).encode()
        b = object.__new__(B.Native)
        b.p = P
        with patch.object(b, 'verify', return_value=BOOT), patch.object(B.time, 'monotonic', return_value=3), patch.object(b, 'command',
                side_effect=[raw(good), b'{"phase":"restored"}', raw(reset), b'{"phase":"accepted"}']):
            self.assertEqual(b.status()['phase'], 'restored')
            self.assertEqual(b.status()['phase'], 'accepted')
        # An empty per-command history is never initial completion authority.
        b = object.__new__(B.Native)
        b.p = P
        with patch.object(b, 'verify', return_value=BOOT), patch.object(B.time, 'monotonic', return_value=3), patch.object(b, 'command', return_value=raw(reset)):
            with self.assertRaises(ValueError):
                b.status()
        for key, value in [('InvocationID', 'f' * 32), ('ExecMainStartTimestampMonotonic', '1500000'),
                           ('ExecMainExitTimestampMonotonic', '2500000'), ('OnSuccess', 'foreign.service'),
                           ('ExecStart', reset['ExecStart'].replace(' recover ', ' restore '))]:
            b = object.__new__(B.Native)
            b.p = P
            with self.subTest(key=key), patch.object(b, 'verify', return_value=BOOT), patch.object(B.time, 'monotonic', return_value=3), patch.object(b, 'command',
                    side_effect=[raw(good), b'{"phase":"restored"}', raw({**reset, key: value})]):
                b.status()
                with self.assertRaises(ValueError):
                    b.status()


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux protected-file/lock fixture required')
class ProtectedEnrollment(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory(prefix='droidmate-boot-fixture-', dir='/root'))
        self.root = Path(temp)
        self.loader = self.root / 'loader.py'
        self.service = self.root / 'preference.service'
        self.state = self.root / 'state.json'
        for key, value in [('ROOT', self.root), ('LOADER', self.loader), ('SERVICE', self.service),
                           ('STATE', self.state), ('RECOVERY_TEMPLATE', self.root / 'recovery.service'),
                           ('__file__', str(self.root / 'program.py'))]:
            self.stack.enter_context(patch.object(B, key, value))
        self.p = {**P, 'loaderSha256': B.sha(b'fixture-loader\n'),
                  'machineSha256': hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()}
        self.expected = B.sha(B.encode(self.p))
        unit = B.unit(self.expected, self.p['packageSha256'])
        handoff = B.handoff_path(self.p['packageSha256'])
        handoff.parent.mkdir(mode=0o755)
        files = {'program.py': b'# isolated generated fixture, not executable service code\n',
                 'preference.json': B.encode(self.p), 'loader.py': b'fixture-loader\n',
                 'preference.service': unit, 'recovery.service': B.recovery_unit(), 'lock': b'\n'}
        files[handoff.relative_to(self.root).as_posix()] = B.handoff()
        files['complete.json'] = B.encode({'schema': 1, 'kind': 'droidmate-gpu-boot-enrollment',
            'programSha256': B.sha(files['program.py']), 'preferenceSha256': self.expected,
            'unitSha256': B.sha(unit), 'handoffSha256': B.sha(B.handoff())})
        for name, raw in files.items():
            (self.root / name).write_bytes(raw)
            (self.root / name).chmod(0o644 if name.endswith(('.service', '.conf')) else 0o600)
        (self.root / 'boots').mkdir(mode=0o700)

    def native(self):
        return B.Native(self.stack, self.expected)

    def test_real_enrollment_journal_roundtrip_and_exclusive_lock(self):
        b = self.native()
        self.assertRegex(b.verify(), B.UUID)
        self.assertIsNone(b.read(BOOT))
        b.save(BOOT, {'phase': 'intent'})
        self.assertEqual(b.read(BOOT), {'phase': 'intent'})
        b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(b.read(BOOT), {'phase': 'gpu'})
        self.assertEqual(B.document((self.root / 'boots' / (BOOT + '.json')).read_bytes()), {'phase': 'intent'})
        with ExitStack() as other:
            with self.assertRaises(BlockingIOError):
                B.Native(other, self.expected)

    def test_replaced_loader_same_bytes_is_refused_before_execution(self):
        b = self.native()
        other = self.root / 'replacement.py'
        other.write_bytes(self.loader.read_bytes())
        other.chmod(0o600)
        other.replace(self.loader)
        with self.assertRaises(ValueError):
            b.verify()

    def test_changed_program_and_unit_are_refused(self):
        b = self.native()
        (self.root / 'program.py').write_bytes(b'# changed\n')
        with self.assertRaises(ValueError):
            b.verify()
        with self.assertRaises(ValueError):
            self.native()

    def test_recovery_handoff_generation_and_bytes_are_checked(self):
        b = self.native()
        path = B.handoff_path(self.p['packageSha256'])
        path.write_bytes(b'[Unit]\nOnSuccess=foreign.service\n')
        with self.assertRaises(ValueError):
            b.verify()
        with self.assertRaises(ValueError):
            self.native()

    def test_admission_requires_a_completed_enrollment_arming_record(self):
        b = self.native()
        with self.assertRaises(FileNotFoundError):
            b.armed()
        path = self.root / 'armed.json'
        value = B.arming(B.sha((self.root / 'complete.json').read_bytes()), self.expected)
        path.write_bytes(B.encode(value))
        path.chmod(0o600)
        b.armed()
        path.write_bytes(B.encode({**value, 'preferenceSha256': 'f' * 64}))
        with self.assertRaises(ValueError):
            b.verify()

    def test_arming_cannot_adopt_a_different_enrollment(self):
        b = self.native()
        path = self.root / 'armed.json'
        path.write_bytes(B.encode(B.arming('f' * 64, self.expected)))
        path.chmod(0o600)
        with self.assertRaises(ValueError):
            b.armed()

    def test_symlink_preference_and_hardlinked_loader_are_refused(self):
        saved = self.root / 'saved.json'
        (self.root / 'preference.json').rename(saved)
        (self.root / 'preference.json').symlink_to(saved)
        with self.assertRaises(OSError):
            self.native()
        (self.root / 'preference.json').unlink()
        saved.rename(self.root / 'preference.json')
        os.link(self.loader, self.root / 'linked-loader.py')
        with self.assertRaises(ValueError):
            self.native()

    def test_pending_write_is_preserved_without_replacing_journal(self):
        b = self.native()
        b.save(BOOT, {'phase': 'intent'})
        pending = self.root / 'boots' / ('.' + BOOT + '.complete.json.pending')
        pending.write_bytes(b'partial')
        pending.chmod(0o600)
        with self.assertRaises(FileExistsError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(pending.read_bytes(), b'partial')
        self.assertEqual(b.read(BOOT), {'phase': 'intent'})

    def test_foreign_initial_and_replaced_journals_are_never_overwritten(self):
        b = self.native()
        path = self.root / 'boots' / (BOOT + '.json')
        b.read(BOOT)
        path.write_bytes(B.encode({'foreign': True}))
        path.chmod(0o600)
        with self.assertRaises(ValueError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(B.document(path.read_bytes()), {'foreign': True})
        path.write_bytes(B.encode({'phase': 'intent'}))
        b.read(BOOT)
        replacement = self.root / 'replaced.json'
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
        with self.assertRaises(ValueError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(B.document(path.read_bytes()), {'phase': 'intent'})

    def test_atomic_publication_preserves_file_introduced_after_last_check(self):
        b = self.native()
        b.save(BOOT, {'phase': 'intent'})
        target = self.root / 'boots' / (BOOT + '.complete.json')
        original = B.publish_new
        def race(fd, pending, name):
            target.write_bytes(B.encode({'foreign': True}))
            target.chmod(0o600)
            original(fd, pending, name)
        with patch.object(B, 'publish_new', side_effect=race), self.assertRaises(FileExistsError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(B.document(target.read_bytes()), {'foreign': True})

    def test_replacement_after_publication_is_not_adopted(self):
        b = self.native()
        b.save(BOOT, {'phase': 'intent'})
        target = self.root / 'boots' / (BOOT + '.complete.json')
        original = B.publish_new
        def race(fd, pending, name):
            original(fd, pending, name)
            other = self.root / 'foreign.json'
            other.write_bytes(B.encode({'foreign': True}))
            other.chmod(0o600)
            other.replace(target)
        with patch.object(B, 'publish_new', side_effect=race), self.assertRaises(ValueError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual(B.document(target.read_bytes()), {'foreign': True})

    def test_completed_boot_journal_cannot_be_published_twice(self):
        b = self.native()
        b.save(BOOT, {'phase': 'intent'})
        b.save(BOOT, {'phase': 'gpu'})
        before = (self.root / 'boots' / (BOOT + '.complete.json')).read_bytes()
        with self.assertRaises(FileExistsError):
            b.save(BOOT, {'phase': 'gpu'})
        self.assertEqual((self.root / 'boots' / (BOOT + '.complete.json')).read_bytes(), before)

    def test_new_process_refuses_identical_intent_bytes_under_replacement_inode(self):
        with ExitStack() as first:
            b = B.Native(first, self.expected)
            b.save(BOOT, {'phase': 'intent'})
            b.save(BOOT, {'phase': 'gpu'})
        path = self.root / 'boots' / (BOOT + '.json')
        replacement = self.root / 'new-intent.json'
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
        with ExitStack() as second:
            b = B.Native(second, self.expected)
            with self.assertRaises(ValueError):
                b.read(BOOT)

    def test_real_output_bounds_and_deadline_apply_before_return_code(self):
        for script in ["print('x'*9000)", "import sys;sys.stderr.write('x'*9000);sys.exit(1)"]:
            with self.assertRaises(ValueError):
                B.collect(['/usr/bin/python3', '-I', '-B', '-c', script], 2)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            B.collect(['/usr/bin/python3', '-I', '-B', '-c', 'import time;time.sleep(5)'], .1)
        self.assertLess(time.monotonic() - started, 2)

    def test_failed_input_checks_do_not_leak_file_descriptors(self):
        b = self.native()
        bad = self.root / 'invalid.json'
        bad.write_bytes(b'bad\n')
        bad.chmod(0o644)
        before = len(list(Path('/proc/self/fd').iterdir()))
        for _ in range(30):
            with self.assertRaises(ValueError):
                b.read_file(bad, 100)
        self.assertEqual(len(list(Path('/proc/self/fd').iterdir())), before)

    def test_ambiguous_or_wrong_boot_operation_is_not_owned(self):
        b = self.native()
        for value in [{}, {'packageSha256': 'f' * 64},
                      {'packageSha256': self.p['packageSha256'], 'viewToken': self.p['viewToken'], 'boot': BOOT}]:
            self.state.write_bytes(B.encode(value))
            self.state.chmod(0o600)
            with self.assertRaises(ValueError):
                b.preferred()

    def test_never_dispatches_restore_recover_or_stage(self):
        b = self.native()
        with patch.object(b, 'command') as command:
            for action in ('restore', 'recover', 'stage', 'run', '../change'):
                with self.assertRaises(ValueError):
                    b.control(action)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
