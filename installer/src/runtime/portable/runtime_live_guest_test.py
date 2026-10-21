# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('live_guest_tests', Path(__file__).with_name('runtime-live-guest.py'))
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.value = {'version': 1, 'user': 'fixture', 'uid': 1201, 'gid': 1301,
            'lanAddress': '192.168.2.215', 'home': '/home/fixture', 'port': 8443, 'hostname': 'test.invalid'}
        self.raw = (json.dumps(self.value, indent=2) + '\n').encode('ascii')
        self.account = ('fixture', 1201, 1301, '/home/fixture')
        self.authority = {'check': mock.Mock(), 'read_file': mock.Mock(return_value=self.raw)}

    def test_exact_original_canonical_metadata_and_fresh_account_binding(self):
        with mock.patch.object(G.R.K.L, '_account', return_value=self.account) as lookup:
            validate = G.bound_validator(self.authority, 'fixture')
            actual = validate(self.raw)
            self.assertEqual(actual['config'], self.value)
            self.assertEqual(actual['configurationDigest'], G.R._sha(json.dumps(self.value, separators=(',', ':')).encode()))
            lookup.assert_called_with('fixture')
            for raw in (None, bytearray(self.raw), self.raw + b'\n', b'private-value'):
                with self.assertRaises(ValueError): validate(raw)
            self.authority['read_file'].return_value = b'changed'
            with self.assertRaises(ValueError): validate(self.raw)

    def test_account_and_source_drift_refuse(self):
        with mock.patch.object(G.R.K.L, '_account', return_value=self.account) as lookup:
            validate = G.bound_validator(self.authority, 'fixture')
            lookup.return_value = ('fixture', 1202, 1301, '/home/fixture')
            with self.assertRaises(ValueError): validate(self.raw)
        with mock.patch.object(G.R.K.L, '_account', return_value=('other', 1201, 1301, '/home/fixture')):
            with self.assertRaises(ValueError): G.bound_validator(self.authority, 'fixture')
        with mock.patch.object(G.R.K.L, '_account', return_value=self.account):
            self.authority['check'].side_effect = ValueError('generated source drift')
            with self.assertRaises(ValueError): G.bound_validator(self.authority, 'fixture')

    def test_publication_requires_original_committed_registration_and_idle_runtime(self):
        with mock.patch.object(G.R.A, '_root_linux'), mock.patch.object(G.P, '_assert_guest_runtime_inactive') as idle, \
                mock.patch.object(G.R, '_finish', side_effect=ValueError('generated foreign ownership')), \
                mock.patch.object(G.P, '_install_guest_payloads') as copy:
            with self.assertRaises(ValueError): G.publish(self.authority, 'fixture')
            idle.assert_called_once_with()
            copy.assert_not_called()

    def test_held_real_mount_context_is_composed_without_pid_or_data_override(self):
        check = mock.Mock()
        copier = object()
        def copy(actual, *, context):
            self.assertIs(actual, copier)
            held = context('generated-stack', copier)
            held['check']()
            return {'installable': False}
        with mock.patch.object(G.R.A, '_root_linux'), mock.patch.object(G.P, '_assert_guest_runtime_inactive'), \
                mock.patch.object(G.R, '_finish'), mock.patch.object(G.R.K.L, '_account', return_value=self.account), \
                mock.patch.object(G.P, 'load_copier', return_value=copier), \
                mock.patch.object(G.P, '_machine_guest_context', return_value={'check': check}) as mounted, \
                mock.patch.object(G.P, '_install_guest_payloads', side_effect=copy):
            result = G.publish(self.authority, 'fixture')
            self.assertTrue(result['actualMetadataBound'])
            self.assertFalse(result['browserVerified'])
            self.assertEqual(mounted.call_args.args, ('generated-stack', copier))
            self.assertEqual(set(mounted.call_args.kwargs), {'validate'})
            check.assert_called()

    def test_closed_cli_and_redacted_failure(self):
        with mock.patch.object(G.R.A, '_root_linux'):
            for args in ([], ['--recover'], ['--install-new', 'u', 's', 'r', '-', '/data']):
                with self.assertRaises(ValueError): G.main(args)
        result = subprocess.run([sys.executable, '-I', '-B', G.__file__, '--force', 'private-value'], capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertNotIn(b'private-value', result.stderr)


if __name__ == '__main__': unittest.main()
