# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('session_tested', Path(__file__).with_name('prepare-user-session.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_fixed_unprivileged_command_environment(self):
        identity = {'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman'}
        with mock.patch.object(D, 'command') as run:
            D.user_command(identity, 'enable', '--now', *D.UNITS)
        args = run.call_args.args[0]
        self.assertEqual(args[:6], ['/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env', '-i'])
        self.assertIn('XDG_RUNTIME_DIR=/run/user/1000', args)
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', args)
        self.assertEqual(args[-5:], ['enable', '--now', *D.UNITS])

    def test_exact_mode_and_no_arbitrary_unit_option(self):
        with mock.patch.object(D, 'command') as run:
            for args in ([], ['--force', 'hilman'], ['--prepare-user-session', 'hilman', 'foreign.service']):
                with self.assertRaises(ValueError):
                    D.main(args)
            run.assert_not_called()

    def test_foreign_unit_overrides_or_inactive_session_refuse(self):
        identity = {'user': 'hilman', 'uid': 1000, 'gid': 1000, 'home': '/home/hilman'}
        def output(_identity, _mode, unit, *args):
            return ('ActiveState=active\nUnitFileState=enabled\nFragmentPath=/usr/lib/systemd/user/'
                    + unit + '\nDropInPaths=')
        with mock.patch.object(D, 'account', return_value=identity), \
                mock.patch.object(D, 'command', return_value='yes'), \
                mock.patch.object(D.H, 'protected'), mock.patch.object(D, 'user_command', side_effect=output):
            D.verify(identity)
            for value in ('ActiveState=inactive', 'DropInPaths=/foreign.conf',
                          'FragmentPath=/home/hilman/.config/systemd/user/pipewire.socket'):
                with mock.patch.object(D, 'user_command', return_value=value), self.assertRaises(ValueError):
                    D.verify(identity)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux session journal fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-session-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(D, 'STATE', self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.identity = {'user': 'fixture', 'uid': 1000, 'gid': 1000, 'home': '/home/fixture'}
        self.intent = {'schema': 1, 'account': self.identity, 'fixture': 'original'}
        D.H.write_new(self.fd, 'intent.json', D.H.encode(self.intent))

    def test_completed_readonly_preserves_records_without_enable(self):
        with mock.patch.object(D, 'account', return_value=self.identity), \
                mock.patch.object(D, 'command'), mock.patch.object(D, 'user_command'), \
                mock.patch.object(D, 'verify'):
            D.finish(self.fd, self.intent, '--resume-user-session')
            original = (self.root / 'complete.json').stat().st_ino
            with mock.patch.object(D, 'command', side_effect=AssertionError('enable replay')), \
                    mock.patch.object(D, 'user_command', side_effect=AssertionError('enable replay')), \
                    mock.patch.object(D.H, 'write_new', side_effect=AssertionError('write replay')):
                self.assertTrue(D.finish(self.fd, self.intent, '--inspect-user-session')['readOnly'])
                D.finish(self.fd, self.intent, '--resume-user-session')
            self.assertEqual(original, (self.root / 'complete.json').stat().st_ino)

    def test_foreign_intent_and_incomplete_inspection_refuse(self):
        with mock.patch.object(D, 'account', return_value=self.identity), \
                mock.patch.object(D, 'command') as run:
            with self.assertRaises(ValueError):
                D.finish(self.fd, self.intent, '--inspect-user-session')
            with self.assertRaises(ValueError):
                D.finish(self.fd, {**self.intent, 'fixture': 'foreign'}, '--resume-user-session')
            run.assert_not_called()

    def test_actual_child_death_after_native_enable_before_completion(self):
        child = '''import importlib.util,os,sys
from pathlib import Path
from unittest import mock
s=importlib.util.spec_from_file_location('s',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
d.STATE=Path(sys.argv[2]);identity={'user':'fixture','uid':1000,'gid':1000,'home':'/home/fixture'}
intent={'schema':1,'account':identity,'fixture':'original'};fd=os.open(d.STATE,os.O_RDONLY|os.O_DIRECTORY)
with mock.patch.object(d,'account',return_value=identity),mock.patch.object(d,'command'),mock.patch.object(d,'user_command'):
 d.finish(fd,intent,'--resume-user-session',after_start=lambda:os._exit(73))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', child, D.__file__, str(self.root)], timeout=20)
        self.assertEqual(result.returncode, 73)
        self.assertFalse((self.root / 'complete.json').exists())
        with mock.patch.object(D, 'account', return_value=self.identity), \
                mock.patch.object(D, 'command') as run, mock.patch.object(D, 'user_command') as user, \
                mock.patch.object(D, 'verify'):
            D.finish(self.fd, self.intent, '--resume-user-session')
            run.assert_any_call(['/usr/bin/loginctl', 'enable-linger', 'fixture'])
            user.assert_any_call(self.identity, 'enable', '--now', *D.UNITS)
        self.assertTrue((self.root / 'complete.json').exists())


if __name__ == '__main__':
    unittest.main()
