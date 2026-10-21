# SPDX-License-Identifier: GPL-3.0-only
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('machine-gpu-package.py')
spec = importlib.util.spec_from_file_location('stage_cli_package', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
ACCOUNT = ('builder', 1000, 1000, '/home/builder')


class StageCli(unittest.TestCase):
    def setUp(self):
        self.assertTrue(callable(getattr(P, 'stage_user_source', None)), 'Closed source-staging command required')

    def test_closed_command_dispatch_and_sanitized_policy(self):
        output = io.StringIO()
        policy = {'stagingOnly': True, **dict.fromkeys(P.FALSE_FLAGS, False)}
        with mock.patch.object(P, 'stage_user_source', return_value=policy) as stage, redirect_stdout(output):
            P.main(['--stage-user-package', 'builder', '/home/builder/source', 'candidate'])
        stage.assert_called_once_with('builder', '/home/builder/source', 'candidate')
        self.assertEqual(json.loads(output.getvalue()), policy)
        with mock.patch.object(P, 'stage_user_source', side_effect=AssertionError('no dispatch')):
            for args in (['--stage-user-package'], ['--stage-user-package', 'builder', '/home/builder/source'],
                         ['--stage-user-package', 'builder', '/home/builder/source', 'candidate', '--force'],
                         ['--install'], ['--activate']):
                with self.assertRaises(ValueError):
                    P.main(args)

    def test_root_platform_names_and_stage_gate_precede_nss_or_source_reads(self):
        with mock.patch.object(P, '_source_account', side_effect=AssertionError('no NSS lookup')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.stage_user_source('builder', '/home/builder/source', 'candidate')
            with mock.patch.object(P, '_root_linux'):
                for user, name in (('root', 'candidate'), ('--root', 'candidate'), ('BUILDER', 'candidate'),
                                   ('builder', '../escape'), ('builder', '/elsewhere'), ('builder', 'a' * 65)):
                    with self.assertRaises(ValueError):
                        P.stage_user_source(user, '/home/builder/source', name)

    def test_nss_record_is_independent_exact_unprivileged_and_home_bounded(self):
        record = dict(pw_name='builder', pw_uid=1000, pw_gid=1000, pw_dir='/home/builder')
        def resolve(value):
            with mock.patch.dict(sys.modules, {'pwd': SimpleNamespace(getpwnam=lambda _: SimpleNamespace(**value))}):
                return P._source_account('builder')
        self.assertEqual(resolve(record), ACCOUNT)
        for field, value in (('pw_name', 'alias'), ('pw_uid', 0), ('pw_gid', 0), ('pw_uid', True),
                             ('pw_uid', 2**31), ('pw_dir', '/root'), ('pw_dir', '/home/../root'),
                             ('pw_dir', '/home/builder/'), ('pw_dir', '/home/builder.')):
            with self.assertRaises(ValueError):
                resolve({**record, field: value})

    def test_source_outside_actual_home_or_noncanonical_refuses_before_copy(self):
        with mock.patch.object(P, '_root_linux'), mock.patch.object(P, '_source_account', return_value=ACCOUNT), \
                mock.patch.object(P, 'publish_source_package', side_effect=AssertionError('no publication')):
            for source in ('/root/source', '/home/other/source', '/home/builder',
                           '/home/builder/../other', '/home/builder//source'):
                with self.assertRaises(ValueError):
                    P.stage_user_source('builder', source, 'candidate')

    def test_fixed_destination_owner_and_no_installer_activation_claim(self):
        check = mock.Mock()
        policy = {'rootPrivateSourcePackage': True, **dict.fromkeys(P.FALSE_FLAGS, False)}
        with mock.patch.object(P, '_root_linux'), mock.patch.object(P, '_source_account', return_value=ACCOUNT) as account, \
                mock.patch.object(P, 'publish_source_package', return_value={'check': check, 'policy': policy}) as publish:
            result = P.stage_user_source('builder', '/home/builder/source', 'candidate')
        args, kwargs = publish.call_args
        self.assertEqual(args[1:], (PurePosixPath('/home/builder/source'),
            PurePosixPath('/var/lib/droiddeck/machine-gpu-source-staging/candidate'), 'package'))
        self.assertEqual(kwargs, {'source_owner': (1000, 1000)})
        self.assertEqual(account.call_count, 2)
        check.assert_called_once_with()
        self.assertTrue(result['stagingOnly'])
        self.assertTrue(result['sourceAccountMatched'])
        for key in P.FALSE_FLAGS:
            self.assertFalse(result[key])

    def test_account_drift_after_copy_reports_uncertainty_without_retry(self):
        with mock.patch.object(P, '_root_linux'), \
                mock.patch.object(P, '_source_account', side_effect=[ACCOUNT, ('builder', 1001, 1000, '/home/builder')]), \
                mock.patch.object(P, 'publish_source_package', return_value={'check': mock.Mock(), 'policy': {}}) as publish:
            with self.assertRaises(P.SourcePublicationUncertain):
                P.stage_user_source('builder', '/home/builder/source', 'candidate')
            self.assertEqual(publish.call_count, 1)

    def test_postpublication_check_fault_is_uncertain_but_prewrite_refusal_is_not(self):
        with mock.patch.object(P, '_root_linux'), mock.patch.object(P, '_source_account', return_value=ACCOUNT):
            with mock.patch.object(P, 'publish_source_package', return_value={'check': mock.Mock(side_effect=OSError('fixture fault'))}):
                with self.assertRaises(P.SourcePublicationUncertain):
                    P.stage_user_source('builder', '/home/builder/source', 'candidate')
            with mock.patch.object(P, 'publish_source_package', side_effect=ValueError('fixture prewrite refusal')):
                with self.assertRaises(ValueError):
                    P.stage_user_source('builder', '/home/builder/source', 'candidate')

    def test_postpublication_stack_close_fault_is_uncertain(self):
        for partial in (False, True):
            def published(stack, *_args, **_kwargs):
                def failed_close():
                    raise OSError('generated held-fd close failure')
                stack.callback(failed_close)
                if partial:
                    raise P.SourcePublicationUncertain('generated partial write')
                return {'check': mock.Mock(), 'policy': {}}
            with mock.patch.object(P, '_root_linux'), mock.patch.object(P, '_source_account', return_value=ACCOUNT), \
                    mock.patch.object(P, 'publish_source_package', side_effect=published) as publish:
                try:
                    P.stage_user_source('builder', '/home/builder/source', 'candidate')
                except (OSError, ValueError, RuntimeError) as error:
                    self.assertIsInstance(error, P.SourcePublicationUncertain)
                else:
                    self.fail('Postpublication cleanup must report uncertainty')
                self.assertEqual(publish.call_count, 1)


if __name__ == '__main__':
    unittest.main()
