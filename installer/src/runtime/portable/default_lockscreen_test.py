# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('lockscreen_defaults_tested', Path(__file__).with_name('default-lockscreen.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
F = D.load('default_media_test')


class Guest:
    def __init__(self):
        self.user, self.sdk, self.identity = '0', '36', '42:123'
        self.secure, self.disabled, self.showing, self.booted = False, False, False, True
        self.calls = []

    def command(self, args):
        self.calls.append(args)
        if args == ['/system/bin/am', 'get-current-user']:
            return self.user
        if args == ['/system/bin/getprop', 'ro.build.version.sdk']:
            return self.sdk
        if args == ['/system/bin/stat', '-c', '%d:%i', '/data']:
            return self.identity
        if args == ['/system/bin/dumpsys', 'window', 'policy']:
            return ('    KeyguardServiceDelegate\n      secure=%s\n      showing=%s\n'
                    '      systemIsReady=true\n      bootCompleted=%s\n      KeyguardStateMonitor\n'
                    '        mCurrentUserId=0\n') % tuple(str(v).lower() for v in
                        (self.secure, self.showing, self.booted))
        if args == D.GET:
            return str(self.disabled).lower()
        if args == D.SET:
            self.disabled = True
            return 'Lock screen disabled set to true'
        raise AssertionError(args)


class Policy(unittest.TestCase):
    def test_first_initialization_uses_only_swipe_to_none_setter(self):
        guest, journal = Guest(), F.Memory()
        result = D.apply(guest, journal, '--initialize')
        self.assertTrue(result['initialized'])
        self.assertTrue(result['changedNow'])
        self.assertTrue(result['lockscreenDisabled'])
        self.assertEqual([call for call in guest.calls if call[0] == '/system/bin/locksettings'],
                         [D.GET, D.GET, D.SET, D.GET])

    def test_inspect_never_changes_or_records_defaults(self):
        guest, journal = Guest(), F.Memory()
        self.assertFalse(D.apply(guest, journal, '--inspect')['initialized'])
        self.assertEqual(journal.values, {})
        self.assertNotIn(D.SET, guest.calls)

    def test_secure_credential_is_never_queried_verified_or_cleared(self):
        guest, journal = Guest(), F.Memory()
        guest.secure = True
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')
        self.assertEqual(journal.values, {})
        self.assertFalse(any(call[0] == '/system/bin/locksettings' for call in guest.calls))

    def test_later_swipe_choice_is_preserved(self):
        guest, journal = Guest(), F.Memory()
        D.apply(guest, journal, '--initialize')
        guest.disabled, guest.calls = False, []
        result = D.apply(guest, journal, '--initialize')
        self.assertTrue(result['initialized'])
        self.assertFalse(result['lockscreenDisabled'])
        self.assertNotIn(D.SET, guest.calls)

    def test_later_pin_is_preserved_without_any_locksettings_command(self):
        guest, journal = Guest(), F.Memory()
        D.apply(guest, journal, '--initialize')
        guest.secure, guest.calls = True, []
        result = D.apply(guest, journal, '--initialize')
        self.assertTrue(result['initialized'])
        self.assertTrue(result['secureCredential'])
        self.assertIsNone(result['lockscreenDisabled'])
        self.assertFalse(any(call[0] == '/system/bin/locksettings' for call in guest.calls))

    def test_pending_intent_requires_confirm_only_resume(self):
        guest, journal = Guest(), F.Memory()
        journal.values['intent.json'] = D.context(guest)[0]
        for mode in ('--initialize', '--resume'):
            with self.assertRaises(ValueError):
                D.apply(guest, journal, mode, sleep=lambda _: None)
        self.assertNotIn(D.SET, guest.calls)
        guest.disabled = True
        self.assertTrue(D.apply(guest, journal, '--resume')['initialized'])
        self.assertNotIn(D.SET, guest.calls)

    def test_credential_appearing_before_setter_blocks_mutation(self):
        guest, journal = Guest(), F.Memory()
        original = journal.write
        def changed(name, value):
            original(name, value)
            guest.secure = True
        journal.write = changed
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')
        self.assertNotIn(D.SET, guest.calls)
        self.assertNotIn('complete.json', journal.values)

    def test_already_disabled_initialization_does_not_set_again(self):
        guest = Guest()
        guest.disabled = True
        self.assertTrue(D.apply(guest, F.Memory(), '--initialize')['initialized'])
        self.assertNotIn(D.SET, guest.calls)

    def test_wrong_user_sdk_data_and_incomplete_boot_refuse(self):
        for key, value in (('user', '10'), ('sdk', '35'), ('identity', 'invalid'), ('booted', False)):
            guest, journal = Guest(), F.Memory()
            setattr(guest, key, value)
            with self.assertRaises(ValueError):
                D.apply(guest, journal, '--initialize')
            self.assertNotIn(D.SET, guest.calls)
            self.assertEqual(journal.values, {})
        guest, journal = Guest(), F.Memory()
        D.apply(guest, journal, '--initialize')
        guest.identity = '42:124'
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')

    def test_ambiguous_or_missing_keyguard_and_get_response_refuse(self):
        output = Guest().command(['/system/bin/dumpsys', 'window', 'policy'])
        for raw in ('', output * 2, output.replace('secure=false', 'secure=unknown'),
                    output.replace('secure=false', 'secure=false\n      secure=false')):
            with self.assertRaises(ValueError):
                D.keyguard(raw)
        guest = Guest()
        original = guest.command
        guest.command = lambda args: 'maybe' if args == D.GET else original(args)
        with self.assertRaises(ValueError):
            D.apply(guest, F.Memory(), '--initialize')

    def test_failed_set_or_incomplete_readback_keeps_intent(self):
        for response in ('Error', 'Lock screen disabled set to true'):
            guest, journal = Guest(), F.Memory()
            original = guest.command
            guest.command = lambda args: response if args == D.SET else original(args)
            with self.assertRaises(ValueError):
                D.apply(guest, journal, '--initialize', sleep=lambda _: None)
            self.assertIn('intent.json', journal.values)
            self.assertNotIn('complete.json', journal.values)

    def test_original_intent_and_exact_operations_required(self):
        guest, journal = Guest(), F.Memory()
        journal.values['complete.json'] = D.context(guest)[0]
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')
        for mode in ('--force', '--clear', '--verify'):
            with self.assertRaises(ValueError):
                D.apply(guest, F.Memory(), mode)
        journal.values.clear()
        journal.values['intent.json'] = {**D.context(guest)[0], 'version': True}
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux journal fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-lockscreen-fixture-')
        self.addCleanup(temporary.cleanup)
        patch = mock.patch.object(D, 'STATE', str(Path(temporary.name) / 'state'))
        patch.start()
        self.addCleanup(patch.stop)

    def test_initialize_and_reopen_keep_later_secure_user_choice(self):
        guest = Guest()
        self.assertFalse(D.operate(guest, '--inspect')['initialized'])
        self.assertFalse(Path(D.STATE).exists())
        self.assertTrue(D.operate(guest, '--initialize')['initialized'])
        guest.secure, guest.calls = True, []
        self.assertTrue(D.operate(guest, '--initialize')['initialized'])
        self.assertFalse(any(call[0] == '/system/bin/locksettings' for call in guest.calls))

    def test_actual_child_death_after_synced_intent_is_confirm_only(self):
        guest = Guest()
        child = os.fork()
        if child == 0:
            try:
                with D.M.open_journal(D.STATE, create=True) as journal:
                    journal.write('intent.json', D.context(guest)[0])
                    os._exit(73)
            except BaseException:
                os._exit(74)
        _, status = os.waitpid(child, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
        with self.assertRaises(ValueError):
            D.operate(guest, '--initialize')
        guest.disabled = True
        self.assertTrue(D.operate(guest, '--resume')['initialized'])
        self.assertNotIn(D.SET, guest.calls)

    def test_corrupt_original_journal_is_not_adopted(self):
        guest = Guest()
        D.operate(guest, '--initialize')
        Path(D.STATE, 'complete.json').write_bytes(b'{')
        with self.assertRaises(ValueError):
            D.operate(guest, '--inspect')


if __name__ == '__main__':
    unittest.main()
