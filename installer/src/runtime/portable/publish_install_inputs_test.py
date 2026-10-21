# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('publication_tested', Path(__file__).with_name('publish-install-inputs.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_exact_modes_before_publication(self):
        with mock.patch.object(D, 'finish') as finish:
            for args in ([], ['--force'], ['--publish-install-inputs', 'u', 'p', 'o', '--force']):
                with self.assertRaises(ValueError):
                    D.main(args)
        finish.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux private generated publication fixtures only')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-publication-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'new'
        self.calls = []
        self.held = []
        def publish(kind):
            def call(stack, user, *args):
                self.assertEqual(self.held, [], 'previous phase descriptors must be released')
                self.held.append(kind)
                stack.callback(self.held.pop)
                self.calls.append(kind)
                destination = Path(args[-1]) / ('candidate' if kind == 'candidate' else 'assembly')
                destination.mkdir(mode=0o700)
                return {'check': lambda: None}
            return call
        for target, name, replacement in (
            (D.A, '_account', lambda user: ('fixture', 1000, 1000, '/home/fixture')),
            (D.W.S, 'publish_user_candidate', publish('candidate')),
            (D.W, 'publish_runtime_assembly', publish('runtime')),
            (D.C, 'publish_runtime_artifact_assembly', publish('artifacts')),
            (D.A, 'hold_runtime_candidate', lambda *a: {'check': lambda: None}),
            (D.W, 'hold_runtime_assembly', lambda *a: {'check': lambda: None}),
            (D.C, 'hold_runtime_artifact_assembly', lambda *a: {'check': lambda: None})):
            patch = mock.patch.object(target, name, replacement)
            patch.start()
            self.addCleanup(patch.stop)

    def run_phase(self, **options):
        return D.finish('fixture', '/home/fixture/prepared', str(self.output), **options)

    def test_composition_order_and_completed_readonly_verification(self):
        result = self.run_phase()
        self.assertEqual(self.calls, ['candidate', 'runtime', 'artifacts'])
        self.assertFalse(result['servicesActivated'])
        before = {str(p): p.stat().st_ino for p in self.output.rglob('*')}
        self.calls.clear()
        with mock.patch.object(D.os, 'mkdir', side_effect=AssertionError('readonly mkdir')), \
                mock.patch.object(D.S, '_write_private', side_effect=AssertionError('readonly write')):
            self.assertTrue(self.run_phase(verify=True)['readOnly'])
        self.assertEqual(self.calls, [])
        self.assertEqual(before, {str(p): p.stat().st_ino for p in self.output.rglob('*')})
        with self.assertRaises(FileExistsError):
            self.run_phase()

    def test_partial_publication_is_preserved_and_never_adopted(self):
        with mock.patch.object(D.W, 'publish_runtime_assembly', side_effect=RuntimeError('fixture interruption')):
            with self.assertRaises(RuntimeError):
                self.run_phase()
        self.assertTrue((self.output / 'candidate-stage/candidate').is_dir())
        self.assertFalse((self.output / 'complete.json').exists())
        with self.assertRaises(FileNotFoundError):
            self.run_phase(verify=True)
        with self.assertRaises(FileExistsError):
            self.run_phase()

    def test_foreign_same_path_inode_refuses_completed_verification(self):
        self.run_phase()
        original = self.output / 'candidate-stage/candidate'
        original.rename(self.output / 'displaced')
        original.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.run_phase(verify=True)

    def test_system_target_namespace_refuses_before_creation(self):
        with self.assertRaises(ValueError):
            D.finish('fixture', '/home/fixture/prepared', '/opt/droiddeck')
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
