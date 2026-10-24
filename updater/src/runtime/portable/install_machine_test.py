# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('machine_tested', Path(__file__).with_name('install-machine.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
IDENTITY = {'user': 'fixture', 'uid': 1000, 'gid': 1000, 'home': '/home/fixture'}
CONFIG = {'version': 1, **IDENTITY, 'lanAddress': '192.168.2.215', 'port': 8443, 'hostname': 'droiddeck-test.invalid'}


class Policy(unittest.TestCase):
    def test_exact_private_endpoint_and_existing_account(self):
        with mock.patch.object(D.U, 'account', return_value=IDENTITY):
            self.assertEqual(D.configuration('fixture', '192.168.2.215', 'droiddeck-test.invalid'), CONFIG)
            for address in ('8.8.8.8', '127.0.0.1', '192.168.2.0', '192.168.2.255', '192.168.002.215'):
                with self.assertRaises(ValueError):
                    D.configuration('fixture', address, 'droiddeck-test.invalid')
            for hostname in ('-foreign.invalid', 'foreign..invalid', 'foreign/path', 'foreign;id'):
                with self.assertRaises(ValueError):
                    D.configuration('fixture', '192.168.2.215', hostname)

    def test_user_builds_have_closed_environment_without_root_home(self):
        command = D.user_command(CONFIG, ['/usr/bin/node', '/protected/build.js'])
        self.assertEqual(command[:6], ['/usr/sbin/runuser', '-u', 'fixture', '--', '/usr/bin/env', '-i'])
        self.assertIn('HOME=/home/fixture', command)
        self.assertNotIn('HOME=/root', command)
        self.assertEqual(command[-2:], ['/usr/bin/node', '/protected/build.js'])

    def test_recipe_preparation_is_outside_closed_pre_registration_state(self):
        machine = D.Install('/source', CONFIG, 99)
        self.assertTrue(D.S.ROOT.is_relative_to('/root'))
        self.assertFalse(D.S.ROOT.is_relative_to('/var/lib/droiddeck'))
        self.assertEqual(machine.recipes, D.S.ROOT / 'recipes')
        self.assertEqual(D.S.GPU_ROOT.as_posix(), '/var/lib/droiddeck/gpu-recovery')

    def test_fixed_gpu_tree_is_published_only_in_late_unit_phase(self):
        machine = D.Install('/source', CONFIG, 99)
        events = []
        with mock.patch.object(machine, 'cli', side_effect=lambda *args: events.append(('cli', args))), \
                mock.patch.object(D.H, 'protected', side_effect=lambda path, **kwargs: 99 if kwargs.get('directory') else b'fixed unit'), \
                mock.patch.object(D.H, 'write_new', side_effect=lambda *args: events.append(('write', args[1]))), \
                mock.patch.object(D.os, 'close'), mock.patch.object(machine, 'run'):
            machine.units()
        self.assertEqual(events[0], ('cli', ('stage-installer-sources.py', '--stage-gpu-sources')))
        self.assertEqual(len([event for event in events if event[0] == 'write']), 2)

    def test_whole_sequence_orders_guest_before_extensions_and_watch_before_gpu(self):
        machine = D.Install('/source', CONFIG, 99)
        called, phases, records = [], [], []
        def run(args, **kwargs):
            called.append([str(item) for item in args])
            return b'{"transaction":"123456abcdef"}' if '--admit' in args else b'{}'
        original = machine.phase_run
        def phase(name, operation):
            phases.append(name)
            return original(name, operation)
        with mock.patch.object(D.H, 'write_new', side_effect=lambda fd, name, raw: records.append(name)), \
                mock.patch.object(machine, 'prepare_directories'), mock.patch.object(machine, 'gpu_preflight'), \
                mock.patch.object(machine, 'units'), mock.patch.object(machine, 'defaults'), \
                mock.patch.object(machine, 'restart_policy'), \
                mock.patch.object(machine, 'swap_reserve'), \
                mock.patch.object(machine, 'verify_gpu'), mock.patch.object(machine, 'cli', return_value=b'{}') as cli, \
                mock.patch.object(machine, 'run', side_effect=run), mock.patch.object(machine, 'phase_run', side_effect=phase):
            result = machine.execute()
        self.assertEqual(result['phasesCompleted'], len(phases))
        self.assertFalse(result['releaseAccepted'])
        self.assertLess(phases.index('21-guest-helpers'), phases.index('23-runtime-extensions'))
        self.assertLess(phases.index('27-start-watcher'), phases.index('28-activate-gpu'))
        self.assertEqual(len([item for item in records if item.endswith('.started.json')]), 35)
        self.assertEqual(len([item for item in records if item.endswith('.complete.json')]), 35)
        enable = [args for args in called if 'enable' in args]
        self.assertEqual(enable, [['/usr/bin/systemctl', 'enable', 'droiddeck-fresh-gpu-watch@123456abcdef.service']])
        self.assertFalse(any('--activate' in args for args in called))
        calls = cli.call_args_list
        guest = next(i for i, call in enumerate(calls) if call.args[0] == 'runtime-live-guest.py')
        extensions = next(i for i, call in enumerate(calls) if call.args[:2] == ('install-runtime.py', '--resume-runtime'))
        self.assertLess(guest, extensions)
        self.assertLess(phases.index('02-private-directories'), phases.index('20-cpu-session'))
        acquire = [call for call in calls if call.args[0] == 'acquire-inputs.py']
        self.assertTrue(all(call.kwargs.get('user') is True for call in acquire))

    def test_no_false_gpu_success_from_zero_exit_code(self):
        machine = D.Install('/source', CONFIG, 99)
        for value in (b'{}', b'{"nativeGpuVerified":false,"status":{"stage":"ready"}}',
                      b'{"nativeGpuVerified":true,"status":{"stage":"restored"}}'):
            with mock.patch.object(machine, 'run', return_value=value), self.assertRaises(ValueError):
                machine.verify_gpu('123456abcdef')
        with mock.patch.object(machine, 'run', return_value=b'{"nativeGpuVerified":true,"status":{"stage":"ready"}}'):
            machine.verify_gpu('123456abcdef')

    def test_no_mutation_for_unsupported_mode(self):
        with mock.patch.object(D.S, 'read_package') as read, mock.patch.object(D.H, 'write_new') as write:
            for args in ([], ['--resume-machine'], ['--install-machine', 'u', 'ip'], ['--force', 'u', 'ip', 'host']):
                with self.assertRaises(ValueError):
                    D.main(args)
            read.assert_not_called()
            write.assert_not_called()

    def test_restart_protection_precedes_directory_and_service_operations(self):
        machine = D.Install('/source', CONFIG, 99)
        with mock.patch.object(machine, 'restart_policy', side_effect=ValueError('foreign policy')), \
                mock.patch.object(D.Path, 'mkdir') as mkdir, mock.patch.object(machine, 'run') as run:
            with self.assertRaises(ValueError):
                machine.prepare_directories()
            mkdir.assert_not_called()
            run.assert_not_called()

    def test_final_verification_refuses_missing_or_changed_policy(self):
        machine = D.Install('/source', CONFIG, 99)
        with mock.patch.object(machine, 'restart_policy', side_effect=ValueError('changed policy')) as policy, \
                mock.patch.object(machine, 'cli') as cli:
            with self.assertRaises(ValueError):
                machine.verify_runtime()
            policy.assert_called_once_with(inspect=True)
            cli.assert_not_called()

    def test_swap_failure_precedes_android_and_final_runtime_verification(self):
        machine = D.Install('/source', CONFIG, 99)
        with mock.patch.object(machine, 'restart_policy'), \
                mock.patch.object(machine, 'swap_reserve', side_effect=ValueError('unsafe swap')) as swap, \
                mock.patch.object(D.Path, 'mkdir') as mkdir, mock.patch.object(machine, 'cli') as cli:
            with self.assertRaises(ValueError):
                machine.prepare_directories()
            swap.assert_called_once_with()
            mkdir.assert_not_called()
            swap.reset_mock()
            with self.assertRaises(ValueError):
                machine.verify_runtime()
            swap.assert_called_once_with(inspect=True)
            cli.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux machine journal fixture')
class Native(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='droiddeck-restart-fixture-', dir='/root')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.etc = self.root / 'etc'
        self.etc.mkdir(mode=0o755)
        self.source = self.root / 'source'
        self.source.mkdir(mode=0o700)
        self.raw = Path(__file__).with_name('needrestart-droiddeck.conf').read_bytes()
        (self.source / 'needrestart-droiddeck.conf').write_bytes(self.raw)
        self.machine = D.Install('/source', CONFIG, 99)
        self.machine.source = self.source
        self.patch = mock.patch.object(D, 'ETC', self.etc)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def target(self):
        return self.etc / 'needrestart/conf.d/90-droiddeck.conf'

    def test_new_policy_and_readonly_inspection_preserve_other_configuration(self):
        directory = self.etc / 'needrestart/conf.d'
        directory.mkdir(parents=True)
        other = directory / '50-vendor.conf'
        other.write_bytes(b'vendor configuration\n')
        self.machine.restart_policy()
        target = self.target()
        self.assertEqual(target.read_bytes(), self.raw)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
        before = target.stat()
        self.machine.restart_policy(inspect=True)
        after = target.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(other.read_bytes(), b'vendor configuration\n')

    def test_absent_needrestart_directories_are_created_without_installing_package(self):
        with mock.patch.object(self.machine, 'run') as run:
            self.machine.restart_policy()
            self.machine.restart_policy(inspect=True)
            run.assert_not_called()

    def test_existing_exact_policy_is_not_adopted_or_overwritten(self):
        self.machine.restart_policy()
        before = self.target().stat()
        with self.assertRaises(FileExistsError):
            self.machine.restart_policy()
        self.assertEqual(self.target().read_bytes(), self.raw)
        self.assertEqual(self.target().stat().st_ino, before.st_ino)

    def test_readonly_inspection_never_creates_missing_directories(self):
        with self.assertRaises(FileNotFoundError):
            self.machine.restart_policy(inspect=True)
        self.assertFalse((self.etc / 'needrestart').exists())

    def test_symlink_directory_is_refused_without_touching_destination(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.etc / 'needrestart').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.machine.restart_policy()
        self.assertEqual(list(outside.iterdir()), [])

    def test_writable_directory_is_refused(self):
        parent = self.etc / 'needrestart'
        parent.mkdir()
        parent.chmod(0o777)
        with self.assertRaises(ValueError):
            self.machine.restart_policy()
        self.assertFalse((parent / 'conf.d').exists())

    def test_symlink_or_hardlink_policy_is_refused_by_inspection(self):
        self.machine.restart_policy()
        self.target().unlink()
        original = self.root / 'original.conf'
        original.write_bytes(self.raw)
        self.target().symlink_to(original)
        with self.assertRaises(OSError):
            self.machine.restart_policy(inspect=True)
        self.target().unlink()
        os.link(original, self.target())
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)
        self.assertEqual(original.read_bytes(), self.raw)

    def test_fifo_policy_is_refused_without_blocking(self):
        self.machine.restart_policy()
        self.target().unlink()
        os.mkfifo(self.target(), mode=0o600)
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)

    def test_changed_or_writable_policy_fails_final_inspection(self):
        self.machine.restart_policy()
        self.target().write_bytes(b'foreign policy\n')
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)
        self.target().write_bytes(self.raw)
        self.target().chmod(0o666)
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)

    def test_wrong_mode_or_owner_is_not_silently_repaired(self):
        self.machine.restart_policy()
        self.target().chmod(0o600)
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)
        self.assertEqual(stat.S_IMODE(self.target().stat().st_mode), 0o600)
        self.target().chmod(0o644)
        os.chown(self.target(), 65534, 65534)
        with self.assertRaises(ValueError):
            self.machine.restart_policy(inspect=True)

    def test_actual_process_death_cannot_replay_uncertain_phase(self):
        with tempfile.TemporaryDirectory(prefix='droiddeck-machine-fixture-', dir='/root') as temporary:
            root = Path(temporary)
            code = '''import importlib.util,os,sys
s=importlib.util.spec_from_file_location('m',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
fd=os.open(sys.argv[2],os.O_RDONLY|os.O_DIRECTORY)
m=d.Install('/source',{'home':'/home/fixture'},fd)
m.phase_run('one-time',lambda:os._exit(73))
'''
            result = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(root)], timeout=20)
            if result.returncode != 73:
                self.fail('Child did not reach the interruption fixture')
            self.assertTrue((root / 'one-time.started.json').exists())
            self.assertFalse((root / 'one-time.complete.json').exists())
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                machine = D.Install('/source', CONFIG, fd)
                operation = mock.Mock()
                with self.assertRaises(FileExistsError):
                    machine.phase_run('one-time', operation)
                operation.assert_not_called()
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
