# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('installation_defaults_tested', Path(__file__).with_name('installation-defaults.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Defaults(unittest.TestCase):
    def setUp(self):
        self.guest, self.apps, self.media, self.journal = (mock.Mock() for _ in range(4))
        self.lockscreen = mock.Mock()
        self.lockscreen.operate.return_value = {'initialized': True, 'secureCredential': False}
        self.guest.inspect.return_value = {'defaultsSatisfied': True}
        self.apps.install_missing.return_value = {'defaultsSatisfied': True, 'existingAppPreserved': True}
        self.media.context.return_value = ({'maximum': 15}, 15)
        self.media.apply.return_value = {'initialized': True, 'current': 7, 'changedNow': False}
        @contextmanager
        def opened(_path, *, create):
            yield self.journal
        self.media.open_journal.side_effect = opened

    def execute(self, args):
        return D.execute(self.guest, self.apps, self.media, args, lockscreen=self.lockscreen)

    def test_inspect_requires_both_defaults_without_installing(self):
        self.assertTrue(self.execute(['--inspect'])['defaultsSatisfied'])
        self.apps.install_missing.assert_not_called()
        self.media.apply.assert_called_once_with(self.guest, self.journal, '--inspect')
        self.lockscreen.operate.assert_called_once_with(self.guest, '--inspect')

    def test_missing_lockscreen_default_fails_gate(self):
        self.lockscreen.operate.return_value = {'initialized': False, 'secureCredential': False}
        self.assertFalse(self.execute(['--inspect'])['defaultsSatisfied'])

    def test_uninitialized_secure_lock_refuses_before_mutations(self):
        self.lockscreen.operate.return_value = {'initialized': False, 'secureCredential': True}
        with self.assertRaises(ValueError):
            self.execute(['--initialize', '/protected/apk'])
        self.apps.install_missing.assert_not_called()
        self.media.apply.assert_not_called()

    def test_later_secure_user_choice_remains_valid_after_initialization(self):
        self.lockscreen.operate.return_value = {'initialized': True, 'secureCredential': True}
        self.assertTrue(self.execute(['--initialize', '/protected/apk'])['defaultsSatisfied'])

    def test_missing_app_or_media_requirement_fails_gate(self):
        self.guest.inspect.return_value = {'defaultsSatisfied': False}
        self.assertFalse(self.execute(['--inspect'])['defaultsSatisfied'])
        self.guest.inspect.return_value = {'defaultsSatisfied': True}
        self.media.apply.return_value = {'initialized': False}
        self.assertFalse(self.execute(['--inspect'])['defaultsSatisfied'])

    def test_missing_journal_inspection_does_not_create_it(self):
        self.media.open_journal.side_effect = FileNotFoundError()
        result = self.execute(['--inspect'])
        self.assertFalse(result['defaultsSatisfied'])
        self.assertFalse(result['media']['initialized'])
        self.media.open_journal.assert_called_once_with(self.media.STATE, create=False)
        self.media.apply.assert_not_called()

    def test_initialize_composes_existing_policies_and_same_guest(self):
        result = self.execute(['--initialize', '/protected/apk'])
        self.assertTrue(result['defaultsSatisfied'])
        self.assertEqual(result['media']['current'], 7)
        self.apps.install_missing.assert_called_once_with(self.guest, '/protected/apk')
        self.media.apply.assert_called_once_with(self.guest, self.journal, '--initialize')
        self.assertEqual(self.lockscreen.operate.call_args_list,
                         [mock.call(self.guest, '--inspect'), mock.call(self.guest, '--initialize')])

    def test_invalid_route_refuses_before_app_install(self):
        self.media.context.side_effect = ValueError('not browser route')
        with self.assertRaises(ValueError):
            self.execute(['--initialize', '/protected/apk'])
        self.apps.install_missing.assert_not_called()
        self.media.open_journal.assert_not_called()

    def test_package_failure_never_initializes_volume(self):
        self.apps.install_missing.side_effect = ValueError('wrong publisher')
        with self.assertRaises(ValueError):
            self.execute(['--initialize', '/protected/apk'])
        self.media.apply.assert_not_called()

    def test_corrupt_journal_is_not_misreported_as_missing(self):
        self.media.open_journal.side_effect = ValueError('foreign journal')
        with self.assertRaises(ValueError):
            self.execute(['--inspect'])

    def test_no_force_update_or_unknown_operation(self):
        for args in ([], ['--force'], ['--initialize'], ['--initialize', '/apk', '--replace']):
            with self.assertRaises(ValueError):
                self.execute(args)
        self.media.context.assert_not_called()
        self.apps.install_missing.assert_not_called()


if __name__ == '__main__':
    unittest.main()
