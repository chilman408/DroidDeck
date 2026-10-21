# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
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


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux machine journal fixture')
class Native(unittest.TestCase):
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
