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
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-install-staging.py')
spec = importlib.util.spec_from_file_location('install_staging_tests', FILE)
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)


class Boundary(unittest.TestCase):
    def test_closed_cli_no_target_recovery_resume_or_receipt_override(self):
        for args in ([], ['--publish'], ['--recover'], ['--resume'],
                     ['--prepare-install-staging', 'u', '/s', '/r', '/p', '/metadata'],
                     ['--prepare-install-staging', 'u', '/s', '/r', '/p', '-', '--receipt', '/foreign']):
            with self.assertRaises(ValueError):
                I.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--recover', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Installation staging refused; no publication or recovery performed')

    def test_budget_counts_parent_chains_and_three_held_nodes_per_target(self):
        import types
        resource = types.SimpleNamespace(RLIMIT_NOFILE=7, RLIM_INFINITY=-1, getrlimit=mock.Mock(return_value=(22, 22)),
                                         setrlimit=mock.Mock(side_effect=AssertionError('no limit changes')))
        paths = (PurePosixPath('/opt/droiddeck'), PurePosixPath('/etc/droiddeck'))
        with mock.patch.dict(sys.modules, {'resource': resource}), mock.patch.object(I.os, 'listdir', return_value=list('abcd')):
            self.assertEqual(I._budget(paths), {'stagingDescriptorLimit': 22, 'stagingDescriptorUpperBound': 22})
            resource.getrlimit.return_value = (21, 22)
            with self.assertRaises(ValueError):
                I._budget(paths)
            resource.getrlimit.return_value = (-1, -1)
            self.assertEqual(I._budget(paths)['stagingDescriptorLimit'], -1)
            resource.setrlimit.assert_not_called()

    def test_original_intent_refusal_propagates_without_allocation(self):
        with ExitStack() as stack, mock.patch.object(I.H, 'prepare_install_intent', side_effect=ValueError('generated refusal')), \
                mock.patch.object(I, '_new_directory', side_effect=AssertionError('no allocation')):
            with self.assertRaises(ValueError):
                I.prepare_install_staging(stack, 'u', '/s', '/r', '/p', b'{}')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated installation staging fixtures required')
class Staging(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-staging-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.opt, self.etc, self.var = (self.root / name for name in ('opt', 'etc', 'var'))
        for path in (self.opt, self.etc, self.var):
            path.mkdir(mode=0o700)
        self.journal = self.var / 'droiddeck-install-intent'
        self.targets = tuple(PurePosixPath(str(parent / 'droiddeck')) for parent in (self.opt, self.etc))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(I.H, 'TARGETS', self.targets).start()
        mock.patch.object(I.H, 'JOURNAL', PurePosixPath(str(self.journal))).start()
        self.held = {'check': mock.Mock(), 'read_plan': lambda: b'generated independent admitted plan\n',
            'policy': {**dict.fromkeys(I.E.P.C.L.FALSE_FLAGS, False), 'rootPrivateCopyVerified': False,
                       'sourceRecipeCodeExecuted': True, 'candidateManifestSha256': 'a' * 64,
                       'newOwnedInstallCandidatePublished': False, 'recipeFilesWritten': False}}
        self.gate = mock.patch.object(I.H.G, 'hold_owned_install_candidate', return_value=self.held).start()
        mock.patch.object(I.H.secrets, 'token_hex', return_value='a' * 32).start()
        self.name = '.droiddeck-install-' + 'a' * 32

    def prepare(self, stack):
        return I.prepare_install_staging(stack, 'fixture', '/root/source', '/root/recipes', '/root/candidate', b'{}')

    def test_two_private_same_filesystem_empty_payloads_records_syncs_and_no_publication(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        original, syncs = I.os.fsync, []
        def sync(fd):
            syncs.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            original(fd)
        with ExitStack() as stack, mock.patch.object(I.os, 'fsync', side_effect=sync):
            result = self.prepare(stack)
            self.gate.assert_called_once()
            intent = (self.journal / 'intent.json').read_bytes()
            for index, cap in enumerate(result['allocations']):
                path = Path(cap['path'])
                record = json.loads(result['read_allocation'](str(self.targets[index])))
                self.assertEqual(record['intentSha256'], I.E._sha(intent))
                self.assertEqual(record['planSha256'], I.E._sha(self.held['read_plan']()))
                self.assertEqual(record['stagingInode'], path.stat().st_ino)
                self.assertEqual(record['payloadInode'], os.fstat(cap['payload_fd']).st_ino)
                self.assertEqual(record['payloadDevice'], os.fstat(cap['parent_fd']).st_dev)
                self.assertEqual(set(p.name for p in path.iterdir()), {'payload', 'allocation.json'})
                self.assertEqual(list((path / 'payload').iterdir()), [])
                for entry, mode in ((path, 0o700), (path / 'payload', 0o700), (path / 'allocation.json', 0o600)):
                    info = entry.lstat()
                    self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)), (0, 0, mode))
                with self.assertRaises(TypeError):
                    cap['payload_fd'] = -1
            self.assertEqual(syncs, [False, True, True, False, True, True, True, False, True, True, True])
            self.assertEqual(set(p.name for p in self.journal.iterdir()), {'intent.json'})
            self.assertFalse(any(Path(path).exists() for path in self.targets))
            for flag in ('stagingOwnershipRecorded', 'allocationRecordsDurabilityVerified', 'stagingPayloadsEmpty'):
                self.assertIs(result['policy'][flag], True)
            for flag in ('payloadFilesWritten', 'finalTargetsPublished', 'targetOwnershipClaimed', 'targetReserved',
                         'installationAuthorized', 'recoveryAuthorized', 'servicesActivated', 'rootPrivateCopyVerified'):
                self.assertIs(result['policy'][flag], False)
            for target in (None, '../foreign', '/opt/foreign'):
                with self.assertRaises(ValueError):
                    result['read_allocation'](target)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)

    def test_populated_target_refuses_before_candidate_or_intent(self):
        (self.opt / 'droiddeck').mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)
        self.gate.assert_not_called()
        self.assertFalse(self.journal.exists())
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_existing_second_allocation_is_not_adopted_or_removed(self):
        foreign = self.etc / self.name
        foreign.mkdir(mode=0o700)
        (foreign / 'marker').write_bytes(b'generated foreign')
        inode = foreign.stat().st_ino
        with ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertEqual((foreign / 'marker').read_bytes(), b'generated foreign')
        self.assertEqual(foreign.stat().st_ino, inode)
        self.assertTrue((self.opt / self.name / 'allocation.json').is_file())
        self.assertTrue((self.journal / 'intent.json').is_file())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.prepare(stack)

    def test_racing_foreign_allocation_never_overwritten(self):
        original = I.os.mkdir
        def race(name, mode=0o777, *, dir_fd=None):
            if name == self.name:
                original(name, mode, dir_fd=dir_fd)
                (self.opt / self.name / 'marker').write_bytes(b'generated foreign')
            return original(name, mode, dir_fd=dir_fd)
        with mock.patch.object(I.os, 'mkdir', side_effect=race), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertEqual((self.opt / self.name / 'marker').read_bytes(), b'generated foreign')
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_budget_refusal_preserves_intent_without_allocations(self):
        with mock.patch.object(I, '_budget', side_effect=ValueError('generated budget refusal')), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.journal / 'intent.json').is_file())
        self.assertEqual((list(self.opt.iterdir()), list(self.etc.iterdir())), ([], []))

    def test_partial_allocation_record_retained_without_success_or_retry(self):
        original = I.S._write_private
        def partial(stack, fd, name, raw):
            if name == 'allocation.json':
                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
                try:
                    os.write(target, b'partial')
                finally:
                    os.close(target)
                raise OSError('generated partial record')
            return original(stack, fd, name, raw)
        with mock.patch.object(I.S, '_write_private', side_effect=partial), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertEqual((self.opt / self.name / 'allocation.json').read_bytes(), b'partial')
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_directory_sync_failure_preserves_private_evidence(self):
        original = I.os.fsync
        def fail(fd):
            info = os.fstat(fd)
            path = self.opt / self.name
            if path.exists() and info.st_ino == path.stat().st_ino:
                raise OSError('generated allocation durability failure')
            original(fd)
        with mock.patch.object(I.os, 'fsync', side_effect=fail), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.opt / self.name / 'allocation.json').is_file())
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_source_drift_after_first_record_prevents_second_allocation(self):
        original = I.S._write_private
        def drift(*args):
            result = original(*args)
            if args[2] == 'allocation.json':
                self.held['check'].side_effect = ValueError('generated source drift')
            return result
        with mock.patch.object(I.S, '_write_private', side_effect=drift), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.opt / self.name / 'allocation.json').is_file())
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_payload_inode_replacement_and_unexpected_payload_refuse(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            path = self.opt / self.name / 'payload'
            (path / 'foreign').write_bytes(b'generated')
            with self.assertRaises(I.InstallStagingUncertain):
                result['check']()
            (path / 'foreign').unlink()
            path.rename(path.with_name('original-payload'))
            path.mkdir(mode=0o700)
            path.with_name('original-payload').rmdir()
            with self.assertRaises(I.InstallStagingUncertain):
                result['read_allocation'](str(self.targets[0]))

    def test_record_nonprivate_mode_refuses(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            path = self.opt / self.name / 'allocation.json'
            path.chmod(0o644)
            with self.assertRaises(I.InstallStagingUncertain):
                result['check']()

    def test_record_hardlink_refuses_from_fresh_admission(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            path = self.opt / self.name / 'allocation.json'
            alias = self.root / 'hardlink'
            os.link(path, alias)
            with self.assertRaises(I.InstallStagingUncertain):
                result['check']()

    def test_record_same_byte_replacement_refuses_from_fresh_admission(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            path = self.opt / self.name / 'allocation.json'
            raw = path.read_bytes()
            path.rename(path.with_name('old'))
            path.write_bytes(raw)
            path.chmod(0o600)
            path.with_name('old').unlink()
            with self.assertRaises(I.InstallStagingUncertain):
                result['check']()

    def test_foreign_payload_filesystem_refuses_before_allocation_record(self):
        original = I._new_directory
        def wrong_device(stack, parent, name):
            fd, identity = original(stack, parent, name)
            if name == 'payload':
                identity = (identity[0] + 1, *identity[1:])
            return fd, identity
        with mock.patch.object(I, '_new_directory', side_effect=wrong_device), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertTrue((self.opt / self.name / 'payload').is_dir())
        self.assertFalse((self.opt / self.name / 'allocation.json').exists())
        self.assertEqual(list(self.etc.iterdir()), [])

    def test_parent_target_or_source_change_invalidates_reads(self):
        with ExitStack() as stack:
            result = self.prepare(stack)
            target = self.etc / 'droiddeck'
            target.mkdir(mode=0o700)
            with self.assertRaises(I.InstallStagingUncertain):
                result['check']()
            target.rmdir()
            self.held['check'].side_effect = ValueError('generated account drift')
            with self.assertRaises(I.InstallStagingUncertain):
                result['read_allocation'](str(self.targets[0]))

    def test_allocation_failure_closes_fds_and_does_not_raise_limits(self):
        import resource
        initial, limit = set(os.listdir('/proc/self/fd')), resource.getrlimit(resource.RLIMIT_NOFILE)
        with mock.patch.object(I, '_new_directory', side_effect=OSError('generated allocation failure')), ExitStack() as stack, self.assertRaises(I.InstallStagingUncertain):
            self.prepare(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), initial)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_NOFILE), limit)
        self.assertTrue((self.journal / 'intent.json').is_file())


if __name__ == '__main__':
    unittest.main()
