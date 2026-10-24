# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock
import runtime_candidate_admission_test as fixtures

FILE = Path(__file__).with_name('runtime-candidate-stage.py')
P = None
if FILE.exists():
    spec = importlib.util.spec_from_file_location('candidate_stage', FILE)
    P = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(P)


class Boundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'New-directory held-byte candidate publication required')

    def test_privilege_path_owner_and_overlap_refuse_before_admission(self):
        with ExitStack() as stack, mock.patch.object(P.A, 'hold_runtime_candidate', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.sys, 'platform', platform), mock.patch.object(P.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_candidate(stack, '/root/source', '/root/parent')
            with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True):
                for source, parent in (('/root/source', '/'), ('/root/source', '/root//parent'),
                                       ('/root/source', '/root/source'), ('/root/source', '/root/source/child'),
                                       ('/root/source/child', '/root/source')):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_candidate(stack, source, parent)
                for owner in ([1000, 1000], (True, 1000), (0, 1000), (-1, 2)):
                    with self.assertRaises(ValueError):
                        P.publish_runtime_candidate(stack, '/root/source', '/root/parent', source_owner=owner)

    def test_no_cli_install_activation_or_destination_override(self):
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr.strip(), 'Runtime candidate publication is internal-only; no changes performed')

    def test_publication_uncertainty_has_distinct_type(self):
        self.assertTrue(issubclass(P.CandidatePublicationUncertain, RuntimeError))
        self.assertEqual(P.PUBLISHED_NAME, 'candidate')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-only publication fixtures required')
class Publication(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(P, 'New-directory held-byte candidate publication required')
        fixtures.HeldCandidate.setUp(self)
        self.parent = self.root / 'publication'
        self.parent.mkdir(mode=0o700)

    def publish(self, stack, **kwargs):
        return P.publish_runtime_candidate(stack, self.source, self.parent, **kwargs)

    def test_new_root_copy_all_bytes_independent_holds_and_cleanup(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = self.publish(stack)
            target = self.parent / 'candidate'
            self.assertEqual(set(os.listdir(self.parent)), {'candidate'})
            self.assertEqual(len(held['files']), 77)
            self.assertTrue(held['policy']['rootPrivateCopyVerified'])
            self.assertTrue(held['policy']['newSourceCandidatePublished'])
            for flag in ('sourceFilesInstalled', 'installable', 'readyForActivation', 'servicesActivated', 'androidOperationsExecuted'):
                self.assertFalse(held['policy'][flag])
            for name in held['files']:
                source, copy = self.source / name, target / name
                self.assertEqual(copy.read_bytes(), source.read_bytes())
                self.assertNotEqual((source.stat().st_dev, source.stat().st_ino), (copy.stat().st_dev, copy.stat().st_ino))
                self.assertEqual((copy.stat().st_uid, copy.stat().st_gid, copy.stat().st_mode & 0o777), (0, 0, 0o600))
            held['check']()
            self.assertEqual(held['read']('bundle-manifest.json'), (target / 'bundle-manifest.json').read_bytes())
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_existing_parent_entries_never_overwritten(self):
        existing = self.parent / 'candidate'
        existing.mkdir(mode=0o700)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(set(os.listdir(self.parent)), {'candidate'})
        self.assertEqual(os.listdir(existing), [])

    def test_corrupt_source_unsafe_parent_and_parent_alias_refuse_before_writes(self):
        file = self.source / 'portable/gpu-access.js'
        raw = file.read_bytes()
        file.write_bytes(raw + b'changed')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(os.listdir(self.parent), [])
        file.write_bytes(raw)
        self.parent.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.parent.chmod(0o700)
        alias = self.root / 'alias'
        alias.symlink_to(self.parent, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises(OSError):
            P.publish_runtime_candidate(stack, self.source, alias)
        self.assertEqual(os.listdir(self.parent), [])

    def test_partial_write_retains_attempt_without_manifest_and_retry_refuses(self):
        original = P._write_private
        count = 0

        def write(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('generated failure')
            return original(*args)

        with mock.patch.object(P, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        attempts = list(self.parent.iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].name.startswith('.candidate-attempt-'))
        self.assertFalse((attempts[0] / 'bundle-manifest.json').exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)
        self.assertEqual(list(self.parent.iterdir()), attempts)

    def test_short_writes_complete_and_manifest_is_last(self):
        original, written = os.write, []
        real = P._write_private

        def write(stack, directory, name, raw):
            written.append(name)
            return real(stack, directory, name, raw)

        with mock.patch.object(P.os, 'write', side_effect=lambda fd, raw: original(fd, raw[:max(1, len(raw) // 2)])), \
                mock.patch.object(P, '_write_private', side_effect=write), ExitStack() as stack:
            held = self.publish(stack)
            held['check']()
        self.assertEqual(len(written), 77)
        self.assertEqual(written[-1], 'bundle-manifest.json')

    def test_source_drift_during_copy_leaves_uncertainty(self):
        real = P._write_private
        changed = False

        def write(*args):
            nonlocal changed
            result = real(*args)
            if not changed:
                changed = True
                file = self.source / 'portable/gpu-access.js'
                file.write_bytes(file.read_bytes() + b'drift')
            return result

        with mock.patch.object(P, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'candidate').exists())
        self.assertEqual(len(os.listdir(self.parent)), 1)

    def test_original_attempt_replacement_is_not_adopted(self):
        real = P._write_private
        changed = False

        def write(*args):
            nonlocal changed
            result = real(*args)
            if not changed:
                changed = True
                attempt = next(self.parent.iterdir())
                attempt.rename(self.parent / 'retained')
                attempt.mkdir(mode=0o700)
            return result

        with mock.patch.object(P, '_write_private', side_effect=write), ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'retained').exists())
        self.assertFalse((self.parent / 'candidate').exists())

    def test_atomic_rename_collision_never_replaces_existing_destination(self):
        real = P._rename_new

        def rename(directory, old):
            destination = self.parent / 'candidate'
            destination.mkdir(mode=0o700)
            (destination / 'marker').write_bytes(b'foreign')
            return real(directory, old)

        with mock.patch.object(P, '_rename_new', side_effect=rename), ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertEqual((self.parent / 'candidate/marker').read_bytes(), b'foreign')
        self.assertEqual(len(os.listdir(self.parent)), 2)

    def test_post_rename_sync_failure_retains_final_without_success_receipt(self):
        real, sync = P._rename_new, os.fsync
        renamed = False

        def rename(*args):
            nonlocal renamed
            result = real(*args)
            renamed = True
            return result

        def flush(fd):
            if renamed:
                raise OSError('generated post-rename sync failure')
            return sync(fd)

        with mock.patch.object(P, '_rename_new', side_effect=rename), mock.patch.object(P.os, 'fsync', side_effect=flush), \
                ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertTrue((self.parent / 'candidate/bundle-manifest.json').exists())
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.publish(stack)

    def test_missing_native_rename_entry_point_preserves_uncertainty_without_fallback(self):
        with mock.patch.object(P, '_rename_new', side_effect=AttributeError('generated missing native entry point')), \
                ExitStack() as stack, self.assertRaises(P.CandidatePublicationUncertain):
            self.publish(stack)
        self.assertFalse((self.parent / 'candidate').exists())
        attempts = list(self.parent.iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertTrue((attempts[0] / 'bundle-manifest.json').exists())

    def test_published_original_generation_is_rechecked(self):
        with ExitStack() as stack:
            held = self.publish(stack)
            file = self.parent / 'candidate/portable/gpu-access.js'
            file.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                held['read']('portable/gpu-access.js')

    def test_user_source_copied_to_root_and_later_source_edit_cannot_change_copy(self):
        for file in [self.source, *self.source.rglob('*')]:
            os.chown(file, 1000, 1000)
        with ExitStack() as stack:
            held = self.publish(stack, source_owner=(1000, 1000))
            self.assertTrue(held['policy']['unprivilegedSourceAdmitted'])
            self.assertTrue(held['policy']['rootPrivateSourceCandidate'])
            (self.source / 'portable/gpu-access.js').write_bytes(b'changed after publication')
            held['check']()
            self.assertEqual(len(held['read']('portable/gpu-access.js')), 11805)


if __name__ == '__main__':
    unittest.main()
