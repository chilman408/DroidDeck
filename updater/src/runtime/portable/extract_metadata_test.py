# SPDX-License-Identifier: GPL-3.0-only
import io
import struct
import unittest
from unittest import mock
import zipfile

import extract_images_test as fixtures

EXTRACT = fixtures.EXTRACT


class MetadataReaderTests(unittest.TestCase):
    def test_large_central_directory_rejected_before_underlying_read(self):
        stream = io.BytesIO(b'x' * 32)
        reader = EXTRACT._ZipMetadataReader(stream)
        with mock.patch.object(stream, 'read', wraps=stream.read) as read:
            with self.assertRaisesRegex(ValueError, 'metadata'):
                reader.read(EXTRACT.MAX_ZIP_METADATA + 1)
            read.assert_not_called()

    def test_unbounded_and_cumulative_reads_have_the_same_budget(self):
        stream = io.BytesIO(b'x' * (EXTRACT.MAX_ZIP_METADATA + 1))
        reader = EXTRACT._ZipMetadataReader(stream)
        with mock.patch.object(stream, 'read', wraps=stream.read) as read:
            with self.assertRaisesRegex(ValueError, 'metadata'):
                reader.read()
            read.assert_not_called()
        self.assertEqual(stream.tell(), 0)
        self.assertEqual(len(reader.read(EXTRACT.MAX_ZIP_METADATA)), EXTRACT.MAX_ZIP_METADATA)
        reader.seek(0)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            reader.read(1)

    def test_real_zip_parser_cannot_allocate_declared_large_directory(self):
        # Keep offsets valid so zipfile reaches its eager central-directory read.
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('system.img', b'data')
        data = bytearray(stream.getvalue())
        end = data.rindex(b'PK\x05\x06')
        old_size = struct.unpack_from('<I', data, end + 12)[0]
        padding = EXTRACT.MAX_ZIP_METADATA + 1 - old_size
        data[end:end] = b'x' * padding
        struct.pack_into('<I', data, end + padding + 12, EXTRACT.MAX_ZIP_METADATA + 1)
        stream = io.BytesIO(data)
        reader = EXTRACT._ZipMetadataReader(stream)
        with mock.patch.object(stream, 'read', wraps=stream.read) as read:
            with self.assertRaisesRegex(ValueError, 'metadata'):
                zipfile.ZipFile(reader)
            self.assertTrue(all(call.args[0] <= EXTRACT.MAX_ZIP_METADATA
                                for call in read.call_args_list if call.args and call.args[0] >= 0))

    def test_finished_metadata_budget_does_not_limit_image_streaming(self):
        payload = b'x' * (EXTRACT.MAX_ZIP_METADATA + 1)
        stream = io.BytesIO(payload)
        reader = EXTRACT._ZipMetadataReader(stream)
        reader.metadata_remaining = None
        self.assertEqual(reader.read(), payload)


class MetadataExtractionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ImageExtractionTests(methodName='test_extracts_exact_pair_and_publishes_receipt_last')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_large_valid_directory_refused_without_creating_output(self):
        fixture = self.fixture
        entries = [('extra-' + str(i), b'') for i in range(20000)]
        fixture.archive('system', entries)
        with self.assertRaisesRegex(ValueError, 'metadata'):
            fixture.extract()
        self.assertFalse(fixture.output.exists())

    def test_stored_deflated_and_zip64_still_publish_exact_images(self):
        fixture = self.fixture
        for kind in ('stored', 'deflated', 'zip64'):
            with self.subTest(kind=kind):
                fixture.output = fixture.home / ('new-' + kind)
                compression = zipfile.ZIP_STORED if kind == 'stored' else zipfile.ZIP_DEFLATED
                with mock.patch.object(zipfile, 'ZIP64_LIMIT', 1 if kind == 'zip64' else zipfile.ZIP64_LIMIT):
                    fixture.archive('system', compression=compression)
                    fixture.archive('vendor', compression=compression)
                receipt = fixture.extract()
                self.assertFalse(receipt['installable'])
                for role, payload in fixture.payloads.items():
                    self.assertEqual((fixture.output / (role + '.img')).read_bytes(), payload)

    def test_large_image_does_not_consume_metadata_budget(self):
        fixture = self.fixture
        fixture.payloads['system'] = b'x' * (EXTRACT.MAX_ZIP_METADATA * 2 + 7)
        fixture.archive('system')
        fixture.extract()
        self.assertEqual((fixture.output / 'system.img').read_bytes(), fixture.payloads['system'])

    def test_maximum_archive_comment_is_supported(self):
        fixture = self.fixture
        with zipfile.ZipFile(fixture.filename('system'), 'a') as archive:
            archive.comment = b'c' * 65535
        fixture.pin('system')
        fixture.extract()
        self.assertTrue((fixture.output / EXTRACT.RECEIPT).is_file())


if __name__ == '__main__':
    unittest.main()
