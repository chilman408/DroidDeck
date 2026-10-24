# SPDX-License-Identifier: GPL-3.0-only
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import warnings
import zipfile

MODULE_PATH = Path(__file__).with_name('extract-images.py')
SPEC = importlib.util.spec_from_file_location('extract_images', MODULE_PATH) if MODULE_PATH.exists() else None
EXTRACT = importlib.util.module_from_spec(SPEC) if SPEC else None
if EXTRACT:
    SPEC.loader.exec_module(EXTRACT)
REFERENCE = json.loads((Path(__file__).parents[2] / 'config/portable-image-inputs.json').read_text())


class ImageExtractionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(EXTRACT, 'Safe extraction API is not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-extract-test-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / 'originals'
        self.source.mkdir()
        self.output = self.home / 'new-images'
        self.plan = copy.deepcopy(REFERENCE)
        self.payloads = {'system': b'fixture-system' * 30, 'vendor': b'fixture-vendor' * 20}
        for role in self.payloads:
            self.archive(role)

    def filename(self, role):
        return self.source / self.plan['inputs'][role]['filename']

    def pin(self, role):
        self.plan['inputs'][role]['sha256'] = hashlib.sha256(self.filename(role).read_bytes()).hexdigest()

    def archive(self, role, entries=None, compression=zipfile.ZIP_DEFLATED):
        if entries is None:
            entry = zipfile.ZipInfo(role + '.img')
            entry.external_attr = (stat.S_IFREG | 0o644) << 16
            entries = [(entry, self.payloads[role])]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(self.filename(role), 'w', compression=compression) as archive:
                for entry, payload in entries:
                    if isinstance(entry, zipfile.ZipInfo):
                        entry.compress_type = compression
                    archive.writestr(entry, payload)
        self.pin(role)

    def extract(self, **options):
        return EXTRACT.extract_images(self.source, self.output, plan=self.plan, home=self.home, **options)

    def test_extracts_exact_pair_and_publishes_receipt_last(self):
        receipt = self.extract()
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ['extraction-receipt.json', 'system.img', 'vendor.img'])
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['redistribute'])
        self.assertFalse(receipt['publisherDigestMatched'])
        self.assertEqual(json.loads((self.output / 'extraction-receipt.json').read_text()), receipt)
        for image in receipt['images']:
            role = image['role']
            self.assertEqual((self.output / image['member']).read_bytes(), self.payloads[role])
            self.assertEqual(image['size'], len(self.payloads[role]))
            self.assertEqual(image['sha256'], hashlib.sha256(self.payloads[role]).hexdigest())
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
            for filename in self.output.iterdir():
                self.assertEqual(stat.S_IMODE(filename.stat().st_mode), 0o600)

    def test_existing_output_and_outside_home_are_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_text('untouched')
        with self.assertRaises(ValueError):
            self.extract()
        self.assertEqual(sentinel.read_text(), 'untouched')
        with self.assertRaises(ValueError):
            EXTRACT.extract_images(self.source, self.home.parent / 'not-our-stage', plan=self.plan, home=self.home)

    def test_unsafe_members_fail_before_output_creation(self):
        invalid = [('../system.img', stat.S_IFREG), ('sub/system.img', stat.S_IFREG),
                   ('system.img/', stat.S_IFDIR), ('system.img', stat.S_IFLNK),
                   ('system.img', stat.S_IFIFO), ('SYSTEM.IMG', stat.S_IFREG)]
        for name, kind in invalid:
            with self.subTest(name=name, kind=kind):
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (kind | 0o644) << 16
                self.archive('system', [(entry, b'data')])
                with self.assertRaises(ValueError):
                    self.extract()
                self.assertFalse(self.output.exists())
        for entries in [[('system.img', b'one'), ('system.img', b'two')],
                        [('system.img', b'one'), ('extra', b'two')], []]:
            regular_entries = []
            for name, payload in entries:
                entry = zipfile.ZipInfo(name)
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                regular_entries.append((entry, payload))
            self.archive('system', regular_entries)
            with self.assertRaisesRegex(ValueError, 'members'):
                self.extract()
            self.assertFalse(self.output.exists())

    def test_encrypted_and_unsupported_compression_members_are_refused(self):
        self.archive('system', compression=zipfile.ZIP_STORED)
        data = bytearray(self.filename('system').read_bytes())
        central = data.index(b'PK\x01\x02')
        data[6] |= 1
        data[central + 8] |= 1
        self.filename('system').write_bytes(data)
        self.pin('system')
        with self.assertRaises(ValueError):
            self.extract()
        self.assertFalse(self.output.exists())
        self.archive('system', compression=zipfile.ZIP_BZIP2)
        with self.assertRaises(ValueError):
            self.extract()

    def test_digest_schema_and_size_limits_reject_before_writing(self):
        with self.filename('vendor').open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'digest'):
            self.extract()
        self.assertFalse(self.output.exists())
        self.archive('vendor')
        with self.assertRaises(ValueError):
            self.extract(max_image_bytes=1)
        self.assertFalse(self.output.exists())
        self.plan['redistribute'] = True
        with self.assertRaises(ValueError):
            self.extract()
        self.assertFalse(self.output.exists())

    def test_insufficient_space_leaves_no_output(self):
        with mock.patch.object(EXTRACT, 'check_capacity', side_effect=ValueError('Insufficient extraction space')):
            with self.assertRaisesRegex(ValueError, 'space'):
                self.extract()
        self.assertFalse(self.output.exists())

    @unittest.skipUnless(os.name == 'posix', 'Descriptor filesystem capacity requires POSIX')
    def test_inode_shortage_refuses_before_mkdir_and_uses_held_parent(self):
        info = SimpleNamespace(f_frsize=4096, f_blocks=10**9, f_bfree=10**9,
            f_bavail=10**9, f_files=100, f_ffree=19, f_favail=19, f_flag=0)
        real = os.fstat
        def capacity(fd):
            self.assertEqual((real(fd).st_dev, real(fd).st_ino),
                             (self.home.stat().st_dev, self.home.stat().st_ino))
            return info
        with mock.patch.object(EXTRACT.os, 'fstatvfs', side_effect=capacity), self.assertRaisesRegex(ValueError, 'inodes'):
            self.extract()
        self.assertFalse(self.output.exists())

    def test_corrupt_crc_retains_failed_stage_without_success_receipt(self):
        self.archive('vendor', compression=zipfile.ZIP_STORED)
        data = bytearray(self.filename('vendor').read_bytes())
        payload = data.index(b'fixture-vendor')
        data[payload] ^= 1
        self.filename('vendor').write_bytes(data)
        self.pin('vendor')
        with self.assertRaises(zipfile.BadZipFile):
            self.extract()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    def test_failed_fsync_never_publishes_success(self):
        with mock.patch.object(EXTRACT.os, 'fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.extract()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    def test_oversized_receipt_cannot_exceed_capacity_budget_or_publish(self):
        with mock.patch.object(EXTRACT, 'MAX_RECEIPT_BYTES', 1), self.assertRaisesRegex(ValueError, 'receipt'):
            self.extract()
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ['system.img', 'vendor.img'])

    def test_image_mutation_before_publication_is_rejected(self):
        original = EXTRACT.write_private

        def mutate(filename, **kwargs):
            if Path(filename).name == 'extraction-receipt.partial':
                (self.output / 'system.img').write_bytes(b'replaced contents')
            return original(filename, **kwargs)

        with mock.patch.object(EXTRACT, 'write_private', mutate):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.extract()
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    def test_image_rewrite_before_metadata_baseline_is_rejected(self):
        original = os.fsync
        mutated = False

        def mutate(descriptor):
            nonlocal mutated
            if not mutated:
                (self.output / 'system.img').write_bytes(b'x' * len(self.payloads['system']))
                mutated = True
            return original(descriptor)

        with mock.patch.object(EXTRACT.os, 'fsync', mutate):
            with self.assertRaisesRegex(ValueError, 'hash'):
                self.extract()
        self.assertTrue(mutated)
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    def test_pending_receipt_rewrite_before_commit_is_rejected(self):
        original = os.fsync
        calls = 0

        def mutate(descriptor):
            nonlocal calls
            calls += 1
            if calls == 3:
                (self.output / 'extraction-receipt.partial').write_bytes(b'{"installable":true}')
            return original(descriptor)

        with mock.patch.object(EXTRACT.os, 'fsync', mutate):
            with self.assertRaisesRegex(ValueError, 'hash'):
                self.extract()
        self.assertEqual(calls, 3)
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    def test_cleanup_failure_after_publication_is_still_success(self):
        original = os.unlink

        def fail_pending(filename, *args, **kwargs):
            if Path(filename).name == 'extraction-receipt.partial':
                raise OSError('cleanup failure')
            return original(filename, *args, **kwargs)

        with mock.patch.object(EXTRACT.os, 'unlink', fail_pending):
            receipt = self.extract()
        self.assertEqual(json.loads((self.output / 'extraction-receipt.json').read_text()), receipt)

    @unittest.skipUnless(os.name == 'posix', 'POSIX directory replacement')
    def test_output_replacement_at_receipt_cannot_publish_in_foreign_directory(self):
        original = EXTRACT.write_private
        foreign = self.home / 'foreign-receipt'
        foreign.mkdir()

        def mutate(filename, **kwargs):
            if Path(filename).name == 'extraction-receipt.partial':
                self.output.rename(self.home / 'moved-receipt')
                self.output.symlink_to(foreign, target_is_directory=True)
            return original(filename, **kwargs)

        with mock.patch.object(EXTRACT, 'write_private', mutate):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.extract()
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertFalse((self.home / 'moved-receipt' / 'extraction-receipt.json').exists())

    def test_source_growth_during_extraction_is_detected(self):
        original = zipfile.ZipFile.open
        mutated = False

        def mutate(archive, *args, **kwargs):
            nonlocal mutated
            if not mutated:
                with self.filename('system').open('ab') as stream:
                    stream.write(b'changed during extraction')
                mutated = True
            return original(archive, *args, **kwargs)

        with mock.patch.object(EXTRACT.zipfile.ZipFile, 'open', mutate):
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.extract()
        self.assertFalse((self.output / 'extraction-receipt.json').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX link and FIFO tests')
    def test_output_replacement_cannot_redirect_writes(self):
        foreign = self.home / 'foreign'
        foreign.mkdir()
        original = zipfile.ZipFile.open
        mutated = False

        def mutate(archive, *args, **kwargs):
            nonlocal mutated
            if not mutated:
                self.output.rename(self.home / 'moved-output')
                self.output.symlink_to(foreign, target_is_directory=True)
                mutated = True
            return original(archive, *args, **kwargs)

        with mock.patch.object(EXTRACT.zipfile.ZipFile, 'open', mutate):
            with self.assertRaises(ValueError):
                self.extract()
        self.assertTrue(mutated)
        self.assertEqual(list(foreign.iterdir()), [])
        self.assertFalse((self.home / 'moved-output' / 'extraction-receipt.json').exists())

    @unittest.skipUnless(os.name == 'posix', 'POSIX link and FIFO tests')
    def test_links_and_special_files_are_refused(self):
        archive = self.filename('system')
        original = archive.with_suffix('.original')
        archive.rename(original)
        archive.symlink_to(original)
        with self.assertRaises(ValueError):
            self.extract()
        archive.unlink()
        os.link(original, archive)
        with self.assertRaises(ValueError):
            self.extract()
        archive.unlink()
        os.mkfifo(archive)
        with self.assertRaises(ValueError):
            self.extract()
        self.assertFalse(self.output.exists())


class CapacityTests(unittest.TestCase):
    def info(self, **changes):
        values = dict(f_frsize=4096, f_blocks=1000, f_bfree=1000, f_bavail=1000,
                      f_files=1000, f_ffree=1000, f_favail=1000, f_flag=0)
        return SimpleNamespace(**(values | changes))

    def check(self, info, sizes=(1, 4097), reserve=1):
        with mock.patch.object(EXTRACT.os, 'fstatvfs', return_value=info, create=True) as query, \
                mock.patch.object(EXTRACT.shutil, 'disk_usage', side_effect=AssertionError('no path capacity query')):
            EXTRACT.check_capacity(Path('/generated'), 7, sizes, reserve)
            query.assert_called_once_with(7)

    def test_exact_block_rounding_receipt_directory_and_inode_reserve(self):
        # Images1+2, receipt16, reserve1, directory1 =21 blocks; four inodes +16reserve.
        self.check(self.info(f_bavail=21, f_favail=20))
        for changes in ({'f_bavail': 20}, {'f_favail': 19}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'space or inodes'):
                self.check(self.info(**changes))

    def test_only_unprivileged_available_capacity_counts(self):
        for changes in ({'f_bavail': 0}, {'f_favail': 0}):
            with self.assertRaisesRegex(ValueError, 'space or inodes'): self.check(self.info(**changes))

    def test_readonly_unknown_invalid_and_inconsistent_counts_refuse(self):
        for changes in ({'f_flag': getattr(os, 'ST_RDONLY', 1)}, {'f_files': 0},
                        {'f_frsize': 0}, {'f_frsize': 1024**2 + 1}, {'f_frsize': True},
                        {'f_bavail': -1}, {'f_bavail': 1001}, {'f_bfree': 999},
                        {'f_favail': 1001}, {'f_ffree': 999}, {'f_flag': -1}, {'f_files': 1.0}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'filesystem capacity'):
                self.check(self.info(**changes))

    def test_capacity_io_failure_is_not_a_path_fallback(self):
        with mock.patch.object(EXTRACT.os, 'fstatvfs', side_effect=OSError('generated failure'), create=True), \
                mock.patch.object(EXTRACT.shutil, 'disk_usage', side_effect=AssertionError('no fallback')), \
                self.assertRaisesRegex(OSError, 'generated failure'):
            EXTRACT.check_capacity(Path('/generated'), 7, (1, 1), 0)

    def test_nonposix_development_path_includes_bounded_metadata_space(self):
        required = 2 + EXTRACT.MAX_RECEIPT_BYTES + 4096
        with mock.patch.object(EXTRACT.os, 'fstatvfs', side_effect=AssertionError('no descriptor'), create=True), \
                mock.patch.object(EXTRACT.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(10**9, 0, required)) as query:
            EXTRACT.check_capacity(Path('/generated'), None, (1, 1), 0)
            query.assert_called_once_with(Path('/generated'))
            query.return_value = shutil._ntuple_diskusage(10**9, 0, required - 1)
            with self.assertRaisesRegex(ValueError, 'space'):
                EXTRACT.check_capacity(Path('/generated'), None, (1, 1), 0)

    def test_invalid_capacity_plan_or_descriptor_refuses_before_query(self):
        with mock.patch.object(EXTRACT.os, 'fstatvfs', side_effect=AssertionError('no query'), create=True):
            for sizes, reserve in (([1, 1], 0), ((1,), 0), ((True, 1), 0), ((0, 1), 0),
                                   ((EXTRACT.MAX_BYTES + 1, 1), 0), ((1, 1), -1), ((1, 1), True)):
                with self.assertRaises(ValueError): EXTRACT.check_capacity(Path('/generated'), 7, sizes, reserve)
            for fd in (-1, True, '7'):
                with self.assertRaises(ValueError): EXTRACT.check_capacity(Path('/generated'), fd, (1, 1), 0)


if __name__ == '__main__':
    unittest.main()
