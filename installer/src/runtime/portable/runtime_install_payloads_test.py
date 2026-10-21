# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-install-payloads.py')
spec = importlib.util.spec_from_file_location('install_payload_tests', FILE)
J = importlib.util.module_from_spec(spec)
spec.loader.exec_module(J)
fixture_spec = importlib.util.spec_from_file_location('payload_original_fixture', FILE.with_name('runtime_owned_install_stage_test.py'))
T = importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(T)


class Boundary(unittest.TestCase):
    def test_role_layout_preserves_all_admitted_bytes_without_manifest_collision(self):
        held, _, _, _ = T.fixture()
        plan = held['read_plan']()
        roles = J._roles(plan)
        self.assertEqual(set(roles), {'opt', 'profile'})
        self.assertEqual((len(roles['opt'][0]), len(roles['profile'][0])), (229, 21))
        self.assertIn('recipe/bundle-manifest.json', roles['profile'][0])
        for role, (rows, raw) in roles.items():
            manifest = json.loads(raw)
            self.assertEqual((manifest['role'], manifest['planSha256']), (role, J.E._sha(plan)))
            self.assertEqual(len(manifest['files']), len(rows))
            self.assertIs(manifest['finalPermissionsApplied'], False)
            self.assertIs(manifest['runtimeLinksMaterialized'], False)
            with self.assertRaises(TypeError):
                rows['foreign'] = (1, 'a' * 64)

    def test_combined_budget_counts_both_created_and_verified_trees_without_limit_override(self):
        held, _, _, _ = T.fixture()
        allocations = ({'path': '/opt/.generated'}, {'path': '/etc/.generated'})
        resource = SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(-1, -1)),
                                   setrlimit=mock.Mock(side_effect=AssertionError('no limit change')))
        with mock.patch.dict(sys.modules, {'resource': resource}), mock.patch.object(J.os, 'listdir', return_value=list('abcd')):
            result = J._payload_budget(allocations, J._roles(held['read_plan']()))
            required = result['payloadDescriptorUpperBound']
            self.assertGreater(required, 520)
            resource.getrlimit.return_value = (required, required)
            self.assertEqual(J._payload_budget(allocations, J._roles(held['read_plan']()))['payloadDescriptorLimit'], required)
            resource.getrlimit.return_value = (required - 1, required)
            with self.assertRaises(ValueError):
                J._payload_budget(allocations, J._roles(held['read_plan']()))
            resource.setrlimit.assert_not_called()

    def test_cli_exposes_no_final_publication_recovery_owner_or_receipt_override(self):
        for args in ([], ['--publish'], ['--recover'], ['--resume'],
                     ['--materialize-private-payloads', 'u', '/s', '/r', '/p', '/metadata'],
                     ['--materialize-private-payloads', 'u', '/s', '/r', '/p', '-', '--target', '/foreign']):
            with self.assertRaises(ValueError):
                J.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Private payload materialization refused; no final publication or recovery performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated installation payload fixtures required')
class Materialization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-payload-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opt, self.etc, self.var, self.snapshot = (self.root / name for name in ('opt', 'etc', 'var', 'snapshot'))
        for path in (self.opt, self.etc, self.var, self.snapshot):
            path.mkdir(mode=0o700)
        self.journal = self.var / 'droiddeck-install-intent'
        self.targets = tuple(PurePosixPath(str(path / 'droiddeck')) for path in (self.opt, self.etc))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(J.H, 'TARGETS', self.targets).start()
        mock.patch.object(J.H, 'JOURNAL', PurePosixPath(str(self.journal))).start()
        mock.patch.object(J.H.secrets, 'token_hex', return_value='c' * 32).start()
        self.name = '.droiddeck-install-' + 'c' * 32
        self.held, self.value, self.runtime, self.recipe = T.fixture()
        self.plan = self.held['read_plan']()
        self.gate = mock.patch.object(J.E, 'hold_owned_install_plan', return_value=self.held).start()
        records, readers = J.F._layout(self.plan)
        files = {'plan.json': self.plan, J.A.MANIFEST: J.F._manifest(self.plan, records)}
        for name, (role, source) in readers.items():
            files[name] = self.runtime[source] if role == 'runtime' else self.recipe[source]
        self.candidate = self.snapshot / 'candidate'
        self.candidate.mkdir(mode=0o700)
        for name, raw in files.items():
            path = self.candidate / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for parent in path.parents:
                if parent == self.candidate:
                    break
                parent.chmod(0o700)
            path.write_bytes(raw)
            path.chmod(0o600)

    def prepare(self, stack):
        return J.materialize_install_payloads(stack, 'fixture', '/root/original-source', '/root/recipe-source', str(self.snapshot), b'{}')

    def test_all_252_role_pairs_copied_once_private_no_aliases_or_final_targets(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with ExitStack() as stack:
            result = self.prepare(stack)
            self.gate.assert_called_once()
            count = 0
            for role, records in result['expectedRecords'].items():
                for name, expected in records.items():
                    raw = result['read_payload'](role, name)
                    self.assertEqual((len(raw), J.E._sha(raw)), expected)
                    count += 1
            self.assertEqual(count, 252)
            for parent in (self.opt, self.etc):
                for path in (parent / self.name).rglob('*'):
                    info = path.lstat()
                    self.assertFalse(path.is_symlink())
                    self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)),
                                     (0, 0, 0o700 if stat.S_ISDIR(info.st_mode) else 0o600))
                self.assertFalse((parent / 'droiddeck').exists())
            self.assertIs(result['policy']['rootPrivateCopyVerified'], True)
            self.assertIs(result['policy']['payloadFilesWritten'], True)
            self.assertIs(result['policy']['candidateSnapshotDescriptorsReleased'], True)
            for flag in ('candidateContinuouslyHeld', 'stagingPayloadsEmpty', 'finalPermissionsApplied',
                         'runtimeLinksMaterialized', 'finalTargetsPublished', 'installationAuthorized', 'recoveryAuthorized', 'servicesActivated'):
                self.assertIs(result['policy'][flag], False)
            for role, name in ((None, 'x'), ('foreign', 'x'), ('opt', '../outside')):
                with self.assertRaises(ValueError):
                    result['read_payload'](role, name)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)

    def test_snapshot_fd_phase_closes_before_source_reads_and_does_not_claim_continuous_candidate_hold(self):
        initial = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = J._hold_snapshot_sources(stack, 'fixture', '/root/original-source', '/root/recipe-source', str(self.snapshot), b'{}')
            self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
            name = next(iter(self.runtime))
            row = next(row for row in self.value['files'] if row['source'] == name)
            (self.candidate / 'opt' / row['path']).write_bytes(b'changed after snapshot')
            self.assertEqual(held['read']('opt/' + row['path']), self.runtime[name])
            self.assertIs(held['policy']['candidateContinuouslyHeld'], False)
            self.held['check'].side_effect = ValueError('generated original source drift')
            with self.assertRaises(ValueError):
                held['read']('opt/' + row['path'])

    def test_repaired_snapshot_manifest_refuses_before_intent_and_closes_snapshot_fds(self):
        initial = set(os.listdir('/proc/self/fd'))
        value = json.loads((self.candidate / J.A.MANIFEST).read_bytes())
        value['ownedInstallPlanSha256'] = 'd' * 64
        (self.candidate / J.A.MANIFEST).write_bytes(J.E._encode(value, compact=True))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.assertFalse(self.journal.exists())
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)

    def test_populated_target_refuses_before_original_source_admission(self):
        (self.opt / 'droiddeck').mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.gate.assert_not_called()
        self.assertFalse(self.journal.exists())

    def test_combined_budget_refusal_preserves_intent_allocations_without_payload_copy(self):
        with mock.patch.object(J, '_payload_budget', side_effect=ValueError('generated budget refusal')), ExitStack() as stack, self.assertRaises(J.I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.journal / 'intent.json').is_file())
        for parent in (self.opt, self.etc):
            self.assertEqual(list((parent / self.name / 'payload').iterdir()), [])

    def test_changed_original_bytes_never_copied_under_repaired_snapshot_authority(self):
        name = next(iter(self.runtime))
        self.runtime[name] = b'changed original bytes'
        with ExitStack() as stack, self.assertRaises(J.I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.journal / 'intent.json').is_file())
        self.assertEqual(list((self.opt / self.name / 'payload').iterdir()), [])

    def test_second_role_partial_failure_keeps_first_complete_and_partial_attempt(self):
        original = J.S._write_private
        def partial(stack, fd, name, raw):
            directory = os.readlink('/proc/self/fd/' + str(fd))
            if str(self.etc / self.name / 'payload') in directory:
                original(stack, fd, name, raw[:5])
                raise OSError('generated second-role interruption')
            return original(stack, fd, name, raw)
        with mock.patch.object(J.S, '_write_private', side_effect=partial), ExitStack() as stack, self.assertRaises(J.I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.opt / self.name / 'payload/candidate/bundle-manifest.json').is_file())
        self.assertFalse((self.etc / self.name / 'payload/candidate').exists())
        self.assertEqual(len(list((self.etc / self.name / 'payload').iterdir())), 1)
        self.assertFalse(any(Path(path).exists() for path in self.targets))

    def test_foreign_payload_racing_publisher_is_retained_without_adoption(self):
        original = J._payload_budget
        def race(allocations, roles):
            result = original(allocations, roles)
            (self.opt / self.name / 'payload/foreign').write_bytes(b'generated foreign marker')
            return result
        with mock.patch.object(J, '_payload_budget', side_effect=race), ExitStack() as stack, self.assertRaises(J.I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertEqual((self.opt / self.name / 'payload/foreign').read_bytes(), b'generated foreign marker')
        self.assertFalse((self.opt / self.name / 'payload/candidate').exists())

    def test_later_same_byte_file_replacement_invalidates_all_payload_reads(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            path = self.opt / self.name / 'payload/candidate/bundle-manifest.json'
            raw = path.read_bytes()
            path.rename(path.with_name('old'))
            path.write_bytes(raw)
            path.chmod(0o600)
            path.with_name('old').unlink()
            with self.assertRaises(J.I.InstallStagingUncertain):
                result['read_payload']('profile', 'bundle-manifest.json')

    def test_later_original_account_drift_invalidates_materialized_reads(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            self.held['check'].side_effect = ValueError('generated original account drift')
            with self.assertRaises(J.I.InstallStagingUncertain):
                result['check']()


if __name__ == '__main__':
    unittest.main()
