# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

MODULE = Path(__file__).with_name('stage-images.py')
SPEC = importlib.util.spec_from_file_location('stage_images', MODULE) if MODULE.exists() else None
STAGE = importlib.util.module_from_spec(SPEC) if SPEC else None
if STAGE:
    SPEC.loader.exec_module(STAGE)


class StagePolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(STAGE, 'Protected image staging API missing')

    def test_fixed_publisher_policy(self):
        self.assertEqual(STAGE.PARENT, Path('/var/lib/droiddeck/image-staging'))
        self.assertEqual(STAGE.PINS['system.img'], (1802956800, '582629757cb379f3e6699d1681e3358d2c6f82cf08b8ce1f1c1584e22d409aff'))
        self.assertEqual(STAGE.PINS['vendor.img'], (721117184, '63e002329543ef42a67cc6cfcd40655060ccdd6d00f87bbcc7510683cf07cf72'))

    def test_name_policy(self):
        self.assertEqual(STAGE.stage_name('fresh-20261013an'), 'fresh-20261013an')
        for value in ('', '.', '..', '../old', '/old', 'a/b', 'a\\b', 'a b', 'A', '-new', 'n' * 65, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                STAGE.stage_name(value)

    def test_canonical_absolute_source(self):
        self.assertEqual(str(STAGE.canonical('/home/hilman/images')), '/home/hilman/images')
        for value in ('relative', '/home/../images', '/home//images', '/home/images/', '', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                STAGE.canonical(value)

    def test_cli_requires_root_linux_and_exact_arguments(self):
        with mock.patch.object(STAGE.sys, 'platform', 'win32'):
            with self.assertRaises(ValueError):
                STAGE.main(['/images', 'new'])
        with mock.patch.object(STAGE.sys, 'platform', 'linux'), mock.patch.object(STAGE.os, 'geteuid', return_value=1000, create=True):
            with self.assertRaises(ValueError):
                STAGE.main(['/images', 'new'])
        for args in ([], ['/images'], ['/images', 'new', 'override']):
            with self.assertRaises(ValueError):
                STAGE.main(args)

    def test_cli_output_failure_does_not_claim_committed_stage_is_unready(self):
        messages = []

        def failed_stdout(value, **kwargs):
            if kwargs.get('file') is STAGE.sys.stderr:
                messages.append(value)
            else:
                raise BrokenPipeError('generated stdout failure')

        with mock.patch.object(STAGE, 'main', return_value={'readyForInitialization': True}), mock.patch('builtins.print', side_effect=failed_stdout):
            self.assertEqual(STAGE.run_cli(['/images', 'new']), 2)
        self.assertEqual(messages, ['Protected stage committed; receipt output failed; inspect protected stage'])

    def test_cli_success_and_uncertain_failure_are_distinct(self):
        with mock.patch.object(STAGE, 'main', return_value={'readyForInitialization': True}), mock.patch('builtins.print'):
            self.assertEqual(STAGE.run_cli(['/images', 'new']), 0)
        with mock.patch.object(STAGE, 'main', side_effect=RuntimeError('fixture rejected')), mock.patch('builtins.print'):
            self.assertEqual(STAGE.run_cli(['/images', 'new']), 1)

    def test_buffered_stdout_is_flushed_inside_commit_reporting(self):
        class FailedSink(io.RawIOBase):
            def writable(self):
                return True

            def write(self, data):
                raise OSError('generated buffered write failure')

        output = io.TextIOWrapper(io.BufferedWriter(FailedSink()), encoding='utf-8')
        messages = io.StringIO()
        try:
            with mock.patch.object(STAGE, 'main', return_value={'readyForInitialization': True}), mock.patch.object(STAGE.sys, 'stdout', output), mock.patch.object(STAGE.sys, 'stderr', messages):
                result = STAGE.run_cli(['/images', 'new'])
            self.assertEqual(result, 2)
            self.assertIn('Protected stage committed', messages.getvalue())
        finally:
            try:
                output.close()
            except OSError:
                pass


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux private fixtures required')
class RootStageTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(STAGE, 'Protected image staging API missing')
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-image-stage-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'source'
        self.source.mkdir(mode=0o700)
        self.parent = self.base / 'stages'
        self.parent.mkdir(mode=0o700)
        self.payloads = {'system.img': b'generated-system' * 80, 'vendor.img': b'generated-vendor' * 40}
        self.pins = {}
        for name, payload in self.payloads.items():
            (self.source / name).write_bytes(payload)
            self.pins[name] = (len(payload), hashlib.sha256(payload).hexdigest())
        self.output = self.parent / 'new'

    def stage(self):
        return STAGE._stage_images(str(self.source), self.parent, 'new', self.pins)

    def assert_unready(self):
        self.assertFalse((self.output / 'ready.json').exists())

    def test_private_pair_copied_and_fixture_not_publisher_ready(self):
        before = {n: (self.source / n).stat() for n in self.pins}
        receipt = self.stage()
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ['ready.json', 'system.img', 'vendor.img'])
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['publisherPinsMatched'])
        self.assertFalse(receipt['readyForInitialization'])
        self.assertFalse(receipt['androidOperationsExecuted'])
        self.assertEqual(json.loads((self.output / 'ready.json').read_text()), receipt)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for name, payload in self.payloads.items():
            self.assertEqual((self.output / name).read_bytes(), payload)
            after = (self.source / name).stat()
            self.assertEqual((before[name].st_size, before[name].st_mtime_ns, before[name].st_ino), (after.st_size, after.st_mtime_ns, after.st_ino))
        for path in self.output.iterdir():
            info = path.stat()
            self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, 0o600, 1))

    def test_existing_stage_and_link_are_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_text('unchanged')
        with self.assertRaises((ValueError, OSError)):
            self.stage()
        self.assertEqual(sentinel.read_text(), 'unchanged')
        sentinel.unlink()
        self.output.rmdir()
        self.output.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.stage()
        self.assertEqual((self.source / 'system.img').read_bytes(), self.payloads['system.img'])

    def test_wrong_size_and_hash_rejected_before_output(self):
        target = self.source / 'vendor.img'
        for payload in (b'wrong', b'x' * len(self.payloads['vendor.img'])):
            target.write_bytes(payload)
            with self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.output.exists())

    def test_leaf_links_and_fifo_rejected(self):
        target = self.source / 'vendor.img'
        target.unlink()
        for kind in ('symlink', 'hardlink', 'fifo'):
            if kind == 'symlink':
                target.symlink_to(self.source / 'system.img')
            elif kind == 'hardlink':
                target.write_bytes(self.payloads['vendor.img'])
                os.link(target, self.base / 'vendor-alias')
            else:
                os.mkfifo(target)
            with self.assertRaises((ValueError, OSError)):
                self.stage()
            self.assertFalse(self.output.exists())
            target.unlink()

    def test_unsafe_destination_and_source_ancestor_rejected(self):
        self.parent.chmod(0o777)
        with self.assertRaises(ValueError):
            self.stage()
        self.parent.chmod(0o700)
        linked = self.base / 'linked'
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            STAGE._stage_images(str(linked), self.parent, 'new', self.pins)
        self.assertFalse(self.output.exists())

    def test_capacity_and_fsync_failure_have_no_receipt(self):
        with mock.patch.object(STAGE.os, 'fstatvfs', return_value=type('Space', (), {'f_bavail': 1, 'f_frsize': 1})()):
            with self.assertRaisesRegex(ValueError, 'space'):
                self.stage()
        self.assertFalse(self.output.exists())
        with mock.patch.object(STAGE.os, 'fsync', side_effect=OSError('generated disk failure')):
            with self.assertRaises(OSError):
                self.stage()
        self.assert_unready()

    def test_source_replaced_in_prepublication_fsync_rejects(self):
        original = STAGE.os.fsync
        count = 0

        def changed(fd):
            nonlocal count
            count += 1
            if count == 4:
                target = self.source / 'system.img'
                target.unlink()
                target.write_bytes(self.payloads['system.img'])
            return original(fd)

        with mock.patch.object(STAGE.os, 'fsync', side_effect=changed):
            with self.assertRaises(ValueError):
                self.stage()
        self.assert_unready()

    def test_final_fsync_failure_rolls_back_durably(self):
        original = STAGE.os.fsync
        count = 0

        def failed(fd):
            nonlocal count
            count += 1
            if count == 6:
                raise OSError('generated final sync failure')
            return original(fd)

        with mock.patch.object(STAGE.os, 'fsync', side_effect=failed):
            with self.assertRaises(OSError):
                self.stage()
        self.assert_unready()
        self.assertEqual(count, 7, 'Rollback deletion must be synced')

    def test_failed_rollback_sync_reports_uncertain_publication(self):
        original = STAGE.os.fsync
        count = 0

        def failed(fd):
            nonlocal count
            count += 1
            if count >= 6:
                raise OSError('generated persistent sync failure')
            return original(fd)

        with mock.patch.object(STAGE.os, 'fsync', side_effect=failed):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.stage()
        self.assert_unready()

    def test_failed_rollback_unlink_reports_uncertain_publication(self):
        original_sync, original_unlink = STAGE.os.fsync, STAGE.os.unlink
        count = 0

        def failed_sync(fd):
            nonlocal count
            count += 1
            if count == 6:
                raise OSError('generated final sync failure')
            return original_sync(fd)

        def failed_unlink(path, **kwargs):
            if path == 'ready.json':
                raise OSError('generated rollback unlink failure')
            return original_unlink(path, **kwargs)

        with mock.patch.object(STAGE.os, 'fsync', side_effect=failed_sync), mock.patch.object(STAGE.os, 'unlink', side_effect=failed_unlink):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.stage()
        self.assertTrue((self.output / 'ready.json').exists())

    def test_source_same_byte_replacement_during_copy_rejects(self):
        original = STAGE.copy_image

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            target = self.source / 'system.img'
            target.unlink()
            target.write_bytes(self.payloads['system.img'])
            return result

        with mock.patch.object(STAGE, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.stage()
        self.assert_unready()

    def test_source_ancestor_replacement_during_copy_rejects(self):
        original = STAGE.copy_image

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            if self.source.exists():
                self.source.rename(self.base / 'old-source')
                self.source.mkdir(mode=0o700)
                for name, payload in self.payloads.items():
                    (self.source / name).write_bytes(payload)
            return result

        with mock.patch.object(STAGE, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.stage()
        self.assert_unready()

    def test_corrupted_output_before_publication_rejects(self):
        original = STAGE.copy_image

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.output / 'system.img').write_bytes(b'x' * len(self.payloads['system.img']))
            return result

        with mock.patch.object(STAGE, 'copy_image', side_effect=changed):
            with self.assertRaises(ValueError):
                self.stage()
        self.assert_unready()


if __name__ == '__main__':
    unittest.main()
