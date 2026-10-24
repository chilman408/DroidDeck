# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('layer_property', Path(__file__).with_name('waydroid-layer-property.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def fixture(value='false'):
    document = tool.PropertyDocument()
    for name, item in [('unrelated.setting', 'keep'), (tool.KEY, value)]:
        record = document.properties.add()
        record.name, record.value = name, item
    return document


class PropertyTests(unittest.TestCase):
    def test_optional_missing_round_trip_preserves_unknown_and_later_records(self):
        document = fixture()
        del document.properties[1]
        original = document.SerializeToString() + b'\x98\x06\x01'
        self.assertEqual(tool.saved_value(original), 'absent')
        changed = tool.rewrite_optional(original, ('absent',), 'true')
        self.assertEqual(tool.saved_value(changed), 'true')
        newer = tool.PropertyDocument.FromString(changed)
        newer.properties.add(name='unrelated.later', value='preserve')
        restored = tool.rewrite_optional(newer.SerializeToString(), ('true',), 'absent')
        self.assertEqual(tool.saved_value(restored), 'absent')
        self.assertEqual([(p.name, p.value) for p in tool.PropertyDocument.FromString(restored).properties],
                         [('unrelated.setting', 'keep'), ('unrelated.later', 'preserve')])
        self.assertIn(b'\x98\x06\x01', restored)
        with self.assertRaises(ValueError):
            tool.parse(restored)

    def test_optional_api_rejects_duplicate_foreign_and_nonclosed_policy(self):
        duplicate = fixture()
        duplicate.properties.add().CopyFrom(duplicate.properties[1])
        for data in (duplicate.SerializeToString(), fixture('foreign').SerializeToString()):
            with self.assertRaises(ValueError):
                tool.saved_value(data)
        for expected, target in [(('foreign',), 'true'), (('false',), 'foreign'), (('absent',), 'true')]:
            with self.assertRaises(ValueError):
                tool.rewrite_optional(fixture().SerializeToString(), expected, target)
        with self.assertRaises(ValueError):
            tool.parse(fixture().SerializeToString(), allow_missing=1)

    def test_changes_only_fixed_key_and_preserves_unknown_fields(self):
        original = fixture().SerializeToString() + b'\x98\x06\x01'
        changed = tool.rewrite(original, ('false',), 'true')
        document = tool.PropertyDocument.FromString(changed)
        self.assertEqual([(p.name, p.value) for p in document.properties],
                         [('unrelated.setting', 'keep'), (tool.KEY, 'true')])
        self.assertIn(b'\x98\x06\x01', changed)
        self.assertEqual(tool.rewrite(changed, ('true',), 'false'), original)

    def test_restore_preserves_new_unrelated_settings(self):
        document = fixture('true')
        document.properties[0].value = 'newer user setting'
        restored = tool.PropertyDocument.FromString(tool.rewrite(document.SerializeToString(), ('true',), 'false'))
        self.assertEqual(restored.properties[0].value, 'newer user setting')

    def test_rejects_missing_duplicate_foreign_and_invalid_documents(self):
        missing, duplicate, incomplete = fixture(), fixture(), fixture()
        del missing.properties[1]
        duplicate.properties.add().CopyFrom(duplicate.properties[1])
        incomplete.properties[0].ClearField('value')
        for data in [missing.SerializeToString(), duplicate.SerializeToString(), incomplete.SerializeToString(),
                     fixture('other').SerializeToString(), b'\x0a\xff', b'x' * (tool.MAX_BYTES + 1)]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                tool.rewrite(data, ('false', 'true'), 'false')

    def test_stale_value_and_non_boolean_request_are_rejected(self):
        for expected, target in [(('true',), 'false'), (('false',), 'anything'), (('anything',), 'true')]:
            with self.assertRaises(ValueError):
                tool.rewrite(fixture().SerializeToString(), expected, target)

    def store(self, path, stopped, save=None):
        protected = Mock(side_effect=lambda p, directory=False: p.lstat())
        save = save or Mock(side_effect=lambda p, data, mode: p.write_bytes(data))
        return tool.OfflineStore(path, protected, stopped, save), save

    def test_requires_stopped_container_before_read_and_before_save(self):
        for fail_at in [1, 2]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'persistent_properties'
                data = fixture().SerializeToString()
                path.write_bytes(data)
                stopped = Mock(side_effect=[RuntimeError('running')] if fail_at == 1 else [None, RuntimeError('running')])
                store, save = self.store(path, stopped)
                with self.assertRaises(RuntimeError):
                    store.set_value(('false',), 'true')
                save.assert_not_called()
                self.assertEqual(path.read_bytes(), data)

    def test_failed_atomic_save_leaves_original_and_does_not_restart_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'persistent_properties'
            original = fixture().SerializeToString()
            path.write_bytes(original)
            save = Mock(side_effect=OSError('disk full'))
            store, _ = self.store(path, Mock(), save)
            with self.assertRaises(OSError):
                store.set_value(('false',), 'true')
            self.assertEqual(path.read_bytes(), original)
            save.assert_called_once_with(path, tool.rewrite(original, ('false',), 'true'), 0o600)

    def test_already_restored_value_needs_no_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'persistent_properties'
            path.write_bytes(fixture().SerializeToString())
            store, save = self.store(path, Mock())
            store.set_value(('false', 'true'), 'false')
            save.assert_not_called()

    def test_file_replacement_between_checks_refuses_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'persistent_properties'
            path.write_bytes(fixture().SerializeToString())
            def stopped():
                if stopped.calls:
                    replacement = path.with_name('replacement')
                    replacement.write_bytes(fixture('true').SerializeToString())
                    replacement.replace(path)
                stopped.calls += 1
            stopped.calls = 0
            store, save = self.store(path, stopped)
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                store.set_value(('false',), 'true')
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
