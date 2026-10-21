# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class SavedLayerCliTests(unittest.TestCase):
    def test_closed_cli_and_restore_mode_without_arbitrary_path(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_configure_saved_layer') as edit:
            for option, restore in [('--saved-gpu-layer', False), ('--restore-saved-gpu-layer', True)]:
                P.main([option])
                self.assertEqual(edit.call_args.kwargs, {'restore': restore})
                with self.assertRaises(ValueError):
                    P.main([option, '/arbitrary-data'])

    def test_missing_protobuf_reports_failure_without_a_traceback(self):
        with mock.patch.object(P, 'main', side_effect=ImportError('dependency absent')), mock.patch.object(P, 'diagnostic') as report:
            self.assertEqual(P.run_cli(['--saved-gpu-layer']), 1)
            self.assertEqual(report.call_args.args, ('Saved GPU layer promotion did not complete; inspect existing or partial output',))


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class SavedLayerTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.layer = P._load_layer_parser(self.copier)
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-layer-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'persistent_properties'
        self.original = self.document('false')
        self.file.write_bytes(self.original)
        self.file.chmod(0o600)

    def document(self, value):
        data = self.layer.PropertyDocument()
        data.properties.add(name='persist.waydroid.use_subsurface', value=value)
        data.properties.add(name='persist.fixture.unrelated', value='generated-only-marker')
        # Unknown top-level varint field; structured rewrite must preserve it too.
        return data.SerializeToString() + b'\x98\x06\x07'

    def edit(self, *, restore=False, guard=lambda: None, context=lambda: None, **kw):
        return P._edit_saved_layer(self.copier, self.layer, self.root,
            expected='true' if restore else 'false', target='false' if restore else 'true',
            guard=guard, check_context=context, **kw)

    def context_fixture(self, stack):
        home = self.root / 'home'
        data = home / '.local/share/waydroid/data'
        data.mkdir(parents=True, mode=0o771)
        data.chmod(0o771)
        (data / 'property').mkdir(mode=0o700)
        receipt_directory = data / 'local/tmp' / P.GUEST_TARGET
        receipt_directory.mkdir(parents=True, mode=0o700)
        receipt = receipt_directory / 'droiddeck-guest-receipt.json'
        receipt.write_bytes(json.dumps({'dataDevice': data.stat().st_dev, 'dataInode': data.stat().st_ino}).encode())
        receipt.chmod(0o600)
        etc = self.root / 'metadata'
        etc.mkdir(mode=0o700)
        metadata = etc / 'runtime.json'
        metadata.write_bytes(b'{}')
        metadata.chmod(0o644)
        real_path = Path
        stack.enter_context(mock.patch.object(P, 'Path', side_effect=lambda value: etc if str(value) == '/etc/droiddeck' else real_path(value)))
        stack.enter_context(mock.patch.object(P, '_assert_codec_stopped'))
        stack.enter_context(mock.patch.object(P, '_validate_configuration', return_value={'config': {'home': str(home), 'uid': 0, 'gid': 0}}))
        stack.enter_context(mock.patch.object(P, 'CONFIGURATION_SHA256', hashlib.sha256(metadata.read_bytes()).hexdigest()))
        stack.enter_context(mock.patch.object(P, 'GUEST_RECEIPT_SHA256', hashlib.sha256(receipt.read_bytes()).hexdigest()))
        return data, receipt, metadata

    def test_actual_data_context_admits_only_pinned_metadata_and_payload_identity(self):
        with ExitStack() as stack:
            data, receipt, metadata = self.context_fixture(stack)
            context = P._offline_layer_context(stack, self.copier)
            self.assertEqual(context['property'], data / 'property')
            self.assertEqual(context['dataInode'], data.stat().st_ino)
            context['check']()
            metadata.write_bytes(b'foreign')
            with self.assertRaises(ValueError):
                context['check']()

    def test_context_rejects_unadmitted_data_inode(self):
        with ExitStack() as stack:
            data, receipt, _metadata = self.context_fixture(stack)
            receipt.write_bytes(json.dumps({'dataDevice': data.stat().st_dev, 'dataInode': data.stat().st_ino + 1}).encode())
            stack.enter_context(mock.patch.object(P, 'GUEST_RECEIPT_SHA256', hashlib.sha256(receipt.read_bytes()).hexdigest()))
            with self.assertRaises(ValueError):
                P._offline_layer_context(stack, self.copier)

    def test_context_rejects_writable_namespace_or_foreign_receipt(self):
        with ExitStack() as stack:
            data, receipt, _metadata = self.context_fixture(stack)
            (data / 'property').chmod(0o777)
            with self.assertRaises(ValueError):
                P._offline_layer_context(stack, self.copier)
            (data / 'property').chmod(0o700)
            receipt.write_bytes(b'foreign')
            with self.assertRaises(ValueError):
                P._offline_layer_context(stack, self.copier)

    def test_exact_round_trip_preserves_other_records_and_unknown_fields_inside_data(self):
        first = self.edit()
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'true')
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), self.original)
        data = self.layer.parse(self.file.read_bytes())[0]
        data.properties.add(name='persist.fixture.later', value='preserve-on-rollback')
        self.file.write_bytes(data.SerializeToString())
        second = self.edit(restore=True)
        result = self.layer.parse(self.file.read_bytes())[0]
        self.assertEqual([(p.name, p.value) for p in result.properties],
            [('persist.waydroid.use_subsurface', 'false'), ('persist.fixture.unrelated', 'generated-only-marker'),
             ('persist.fixture.later', 'preserve-on-rollback')])
        self.assertIn(b'\x98\x06\x07', self.file.read_bytes())
        self.assertEqual(first['after'], 'true')
        self.assertEqual(second['after'], 'false')
        for receipt in (first, second):
            self.assertFalse(receipt['opaqueDocumentExported'])
            self.assertFalse(receipt['readyForActivation'])
            self.assertNotIn('generated-only-marker', str(receipt))
        self.assertEqual(set(p.name for p in self.root.iterdir()),
            {'persistent_properties', 'pending-droiddeck-layer-on', 'pending-droiddeck-layer-off'})
        for file in self.root.iterdir():
            self.assertEqual((file.stat().st_uid, file.stat().st_gid, file.stat().st_mode & 0o777, file.stat().st_nlink), (0, 0, 0o600, 1))

    def test_absent_original_is_removed_on_recovery_not_written_false(self):
        document = self.layer.PropertyDocument()
        document.properties.add(name='persist.fixture.other', value='generated')
        original = document.SerializeToString() + b'\x98\x06\x07'
        self.file.write_bytes(original)
        result = P._edit_saved_layer(self.copier, self.layer, self.root, expected='absent', target='true',
                                    guard=lambda: None, check_context=lambda: None)
        self.assertEqual(result['before'], 'absent')
        self.assertEqual(self.layer.saved_value(self.file.read_bytes()), 'true')
        current = self.layer.PropertyDocument.FromString(self.file.read_bytes())
        current.properties.add(name='persist.fixture.later', value='preserve')
        self.file.write_bytes(current.SerializeToString())
        result = P._edit_saved_layer(self.copier, self.layer, self.root, expected='true', target='absent',
                                    guard=lambda: None, check_context=lambda: None)
        self.assertEqual(result['after'], 'absent')
        self.assertEqual(self.layer.saved_value(self.file.read_bytes()), 'absent')
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), original)
        self.assertEqual([(p.name, p.value) for p in self.layer.PropertyDocument.FromString(self.file.read_bytes()).properties],
            [('persist.fixture.other', 'generated'), ('persist.fixture.later', 'preserve')])
        self.assertIn(b'\x98\x06\x07', self.file.read_bytes())

    def test_on_and_off_replay_refuse(self):
        self.edit()
        with self.assertRaises(ValueError):
            self.edit()
        self.edit(restore=True)
        with self.assertRaises(ValueError):
            self.edit(restore=True)
        with self.assertRaises(FileExistsError):
            self.edit()
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'false')

    def test_restore_requires_private_original_false_recovery(self):
        self.file.write_bytes(self.document('true'))
        with self.assertRaises(FileNotFoundError):
            self.edit(restore=True)
        pending = self.root / 'pending-droiddeck-layer-on'
        pending.write_bytes(self.document('true'))
        pending.chmod(0o600)
        with self.assertRaises(ValueError):
            self.edit(restore=True)

    def test_foreign_pending_preserved(self):
        pending = self.root / 'pending-droiddeck-layer-on'
        pending.write_bytes(b'foreign')
        with self.assertRaises(FileExistsError):
            self.edit()
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(pending.read_bytes(), b'foreign')

    def test_malformed_duplicate_missing_and_oversized_documents_refuse(self):
        document = self.layer.PropertyDocument()
        document.properties.add(name=self.layer.KEY, value='false')
        document.properties.add(name=self.layer.KEY, value='false')
        missing = self.layer.PropertyDocument()
        missing.properties.add(name='persist.fixture.other', value='generated')
        for data in (b'invalid', b'', document.SerializeToString(), missing.SerializeToString(), b'x' * 65537):
            self.file.write_bytes(data)
            with self.assertRaises(ValueError):
                self.edit()
            self.assertEqual(self.file.read_bytes(), data)
            self.assertFalse((self.root / 'pending-droiddeck-layer-on').exists())

    def test_symlink_hardlink_and_wrong_mode_refuse(self):
        other = self.root / 'other'
        os.link(self.file, other)
        with self.assertRaises(ValueError):
            self.edit()
        other.unlink()
        self.file.chmod(0o644)
        with self.assertRaises(ValueError):
            self.edit()
        self.file.chmod(0o600)
        self.file.rename(other)
        self.file.symlink_to(other)
        with self.assertRaises(OSError):
            self.edit()

    def test_active_or_changed_context_refuses_before_staging(self):
        def fail():
            raise ValueError('generated refusal')
        for kw in ({'guard': fail}, {'context': fail}):
            with self.assertRaises(ValueError):
                self.edit(**kw)
            self.assertEqual(list(self.root.iterdir()), [self.file])

    def test_last_guard_mutation_refuses_exchange(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2:
                self.file.write_bytes(b'foreign-race')
        with self.assertRaises(ValueError):
            self.edit(guard=guard)
        self.assertEqual(self.file.read_bytes(), b'foreign-race')

    def test_postexchange_active_or_sync_failure_preserves_private_documents(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 3:
                raise ValueError('generated activation race')
        with self.assertRaises(P.PromotionUncertain):
            self.edit(guard=guard)
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'true')
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), self.original)

    def test_postexchange_sync_or_timeout_is_uncertain_not_uncommitted(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 3:
                raise subprocess.TimeoutExpired('generated-only', 1)
        with self.assertRaises(P.PromotionUncertain):
            self.edit(guard=guard)
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'true')
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), self.original)

    def test_postexchange_sync_failure_keeps_both_private_documents(self):
        sync = os.fsync
        def fail(fd):
            if self.file.read_bytes() != self.original:
                raise OSError('generated sync failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.edit()
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'true')
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), self.original)

    def test_root_race_at_exchange_retains_foreign_document_inside_private_data(self):
        library = P.ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
        rename.argtypes = (P.ctypes.c_int, P.ctypes.c_char_p, P.ctypes.c_int, P.ctypes.c_char_p, P.ctypes.c_uint)
        rename.restype = P.ctypes.c_int
        foreign_bytes = self.document('false') + b'\xa0\x06\x09'
        def race(*args):
            foreign = self.root / 'generated-foreign'
            foreign.write_bytes(foreign_bytes)
            foreign.chmod(0o600)
            os.replace(foreign, self.file)
            return rename(*args)
        with mock.patch.object(P.ctypes, 'CDLL', return_value=mock.Mock(renameat2=race)), self.assertRaises(P.PromotionUncertain):
            self.edit()
        self.assertEqual((self.root / 'pending-droiddeck-layer-on').read_bytes(), foreign_bytes)

    def test_recovery_source_mutation_refuses_before_restore(self):
        self.edit()
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2:
                (self.root / 'pending-droiddeck-layer-on').write_bytes(self.document('true'))
        with self.assertRaises(ValueError):
            self.edit(restore=True, guard=guard)
        self.assertEqual(self.layer.parse(self.file.read_bytes())[1].value, 'true')

    def test_directory_replacement_after_staging_refuses_before_exchange(self):
        count = 0
        moved = self.root.with_name(self.root.name + '-moved')
        self.addCleanup(lambda: shutil.rmtree(moved, ignore_errors=True))
        def context():
            nonlocal count
            count += 1
            if count == 2:
                self.root.rename(moved)
                self.root.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            self.edit(context=context)
        self.assertEqual((moved / 'persistent_properties').read_bytes(), self.original)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_private_api_names_and_boolean_modes_are_closed(self):
        for kw in ({'pending': '../outside'}, {'recovery_source': 'persistent_properties'}, {'pending': '/absolute'}):
            with self.assertRaises(ValueError):
                self.edit(**kw)
        self.assertEqual(self.file.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
