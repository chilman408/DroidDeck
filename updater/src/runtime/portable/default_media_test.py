# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('media_defaults_tested', Path(__file__).with_name('default-media.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Memory:
    def __init__(self):
        self.values = {}

    def read(self, name):
        return self.values.get(name)

    def write(self, name, value):
        if name in self.values:
            raise FileExistsError(name)
        self.values[name] = value.copy()

    def check(self):
        pass


class Guest:
    def __init__(self, current=5, maximum=15):
        self.current, self.maximum = current, maximum
        self.identity, self.saved, self.user, self.calls = '42:123', str(current), '0', []

    def command(self, args):
        self.calls.append(args)
        if args == ['/system/bin/am', 'get-current-user']:
            return self.user
        if args == ['/system/bin/stat', '-c', '%d:%i', '/data']:
            return self.identity
        if args == D.SAVED:
            return self.saved
        if args == ['/system/bin/dumpsys', 'audio']:
            return '- STREAM_MUSIC:\n   Devices: remote_submix(8000)\n\n- STREAM_ALARM:\n   Devices: speaker(2)\n'
        if args[:len(D.VOLUME)] == D.VOLUME:
            if '--set' in args:
                self.current = int(args[args.index('--set') + 1])
                self.saved = str(self.current)
            return '[V] volume is %d in range [0..%d]' % (self.current, self.maximum)
        raise AssertionError(args)


class Policy(unittest.TestCase):
    def test_initialize_maximum_and_keep_later_user_choice(self):
        guest, journal = Guest(), Memory()
        self.assertTrue(D.apply(guest, journal, '--initialize')['changedNow'])
        self.assertEqual((guest.current, guest.saved), (15, '15'))
        guest.current, guest.saved, guest.calls = 7, '7', []
        result = D.apply(guest, journal, '--initialize')
        self.assertTrue(result['initialized'])
        self.assertFalse(result['changedNow'])
        self.assertEqual(result['current'], 7)
        self.assertFalse(any('--set' in call for call in guest.calls))

    def test_inspection_never_mutates_and_reports_missing_initialization(self):
        guest, journal = Guest(), Memory()
        self.assertFalse(D.apply(guest, journal, '--inspect')['initialized'])
        self.assertEqual(journal.values, {})
        self.assertFalse(any('--set' in call for call in guest.calls))

    def test_no_hardcoded_maximum(self):
        guest = Guest(2, 25)
        self.assertEqual(D.apply(guest, Memory(), '--initialize')['maximum'], 25)
        self.assertEqual(guest.current, 25)

    def test_interrupted_intent_never_replays_volume(self):
        guest, journal = Guest(), Memory()
        journal.values['intent.json'] = D.context(guest)[0]
        for mode in ('--initialize', '--resume'):
            with self.assertRaises(ValueError):
                D.apply(guest, journal, mode, sleep=lambda _: None)
        self.assertFalse(any('--set' in call for call in guest.calls))
        guest.current, guest.saved = 15, '15'
        self.assertTrue(D.apply(guest, journal, '--resume')['initialized'])

    def test_volume_must_be_saved_for_browser_route(self):
        guest, journal = Guest(), Memory()
        original = guest.command
        def unsaved(args):
            return '5' if args == D.SAVED else original(args)
        guest.command = unsaved
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize', sleep=lambda _: None)
        self.assertIn('intent.json', journal.values)
        self.assertNotIn('complete.json', journal.values)

    def test_async_android_persistence_is_polled(self):
        guest, journal, calls = Guest(), Memory(), []
        original = guest.command
        def delayed(args):
            if args == D.SAVED:
                calls.append(1)
                return '5' if len(calls) < 3 else '15'
            return original(args)
        guest.command = delayed
        self.assertTrue(D.apply(guest, journal, '--initialize', sleep=lambda _: None)['initialized'])
        self.assertEqual(len(calls), 3)

    def test_user_and_data_generation_must_match(self):
        guest, journal = Guest(), Memory()
        D.apply(guest, journal, '--initialize')
        guest.identity = '42:124'
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')
        guest.user = '10'
        with self.assertRaises(ValueError):
            D.apply(guest, Memory(), '--initialize')

    def test_strict_output_and_completion_require_original_intent(self):
        for output in ('', '[E] failed', '[V] volume is 1 in range [0..0]',
                       '[V] volume is 16 in range [0..15]', '[V] volume is 1 in range [0..101]',
                       '[V] volume is 1 in range [0..15]\n' * 2):
            with self.assertRaises(ValueError):
                D.volume(output)
        guest, journal = Guest(), Memory()
        journal.values['complete.json'] = D.context(guest)[0]
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')

    def test_other_routes_and_unexpected_operations_refuse_before_set(self):
        for route in ('speaker(2)', 'usb_headset(4000000)', 'remote_submix(8000), speaker(2)', ''):
            with self.assertRaises(ValueError):
                D.browser_route('- STREAM_MUSIC:\n   Devices: ' + route + '\n')
        guest = Guest()
        with self.assertRaises(ValueError):
            D.apply(guest, Memory(), '--force')
        self.assertEqual(guest.calls, [])

    def test_lookalike_boolean_journal_refuses(self):
        guest, journal = Guest(), Memory()
        journal.values['intent.json'] = {**D.context(guest)[0], 'version': True}
        with self.assertRaises(ValueError):
            D.apply(guest, journal, '--initialize')


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux journal fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-media-fixture-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'state'

    def test_real_journal_reopen_preserves_preferences(self):
        guest = Guest()
        with D.open_journal(str(self.path), create=True) as journal:
            D.apply(guest, journal, '--initialize')
        guest.current = 4
        with D.open_journal(str(self.path), create=False) as journal:
            self.assertEqual(D.apply(guest, journal, '--initialize')['current'], 4)
        self.assertEqual(set(os.listdir(self.path)), {'intent.json', 'complete.json'})

    def test_crash_after_intent_explicit_resume_never_sets_again(self):
        guest = Guest()
        with D.open_journal(str(self.path), create=True) as journal:
            journal.write('intent.json', D.context(guest)[0])
        with D.open_journal(str(self.path), create=False) as journal:
            with self.assertRaises(ValueError):
                D.apply(guest, journal, '--initialize')
            guest.current, guest.saved = 15, '15'
            self.assertTrue(D.apply(guest, journal, '--resume')['initialized'])
        self.assertFalse(any('--set' in call for call in guest.calls))

    def test_symlink_hardlink_mode_extra_and_corruption_refuse(self):
        with D.open_journal(str(self.path), create=True) as journal:
            journal.write('intent.json', D.context(Guest())[0])
        target = self.path / 'intent.json'
        raw = target.read_bytes()
        target.chmod(0o644)
        with D.open_journal(str(self.path), create=False) as journal, self.assertRaises(ValueError):
            journal.read('intent.json')
        target.chmod(0o600)
        os.link(target, self.path / 'complete.json')
        with D.open_journal(str(self.path), create=False) as journal, self.assertRaises(ValueError):
            journal.read('intent.json')
        (self.path / 'complete.json').unlink()
        target.unlink()
        target.symlink_to('/root/nonexistent')
        with D.open_journal(str(self.path), create=False) as journal, self.assertRaises(OSError):
            journal.read('intent.json')
        target.unlink()
        target.write_bytes(b'{')
        target.chmod(0o600)
        with D.open_journal(str(self.path), create=False) as journal, self.assertRaises(ValueError):
            journal.read('intent.json')
        target.write_bytes(raw)
        (self.path / 'foreign').touch()
        with self.assertRaises(ValueError), D.open_journal(str(self.path), create=False):
            pass

    def test_replaced_inode_and_concurrent_open_refuse(self):
        with D.open_journal(str(self.path), create=True) as journal:
            journal.write('intent.json', D.context(Guest())[0])
            with self.assertRaises(BlockingIOError), D.open_journal(str(self.path), create=False):
                pass
            replacement = self.path.parent / 'replacement'
            replacement.write_bytes((self.path / 'intent.json').read_bytes())
            replacement.chmod(0o600)
            replacement.replace(self.path / 'intent.json')
            with self.assertRaises(ValueError):
                journal.check()
            # Exit must reject the replaced file as well.
            journal.files.clear()


if __name__ == '__main__':
    unittest.main()
