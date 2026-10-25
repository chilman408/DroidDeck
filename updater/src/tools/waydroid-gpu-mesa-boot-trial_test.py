# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
import stat
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('mesa_boot_test', Path(__file__).with_name('waydroid-gpu-mesa-boot-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class BootTests(unittest.TestCase):
    def test_distinct_identity_and_one_recognized_property(self):
        self.assertEqual(tool.CODE, b'ro.hardware.gralloc=minigbm_gbm_mesa\n')
        self.assertEqual(tool.engine.BACKUP.name, 'gpu-render-mesa-boot-20261011o2')
        self.assertTrue(tool.engine.LOCK.name.endswith('20261011o2.lock'))
        self.assertEqual(tool.FILE.name, 'waydroid_settings')
        self.assertTrue(tool.TEMP.endswith('20261011o2.tmp'))

    def test_misc_directory_requires_actual_android_sticky_mode_and_group(self):
        from types import SimpleNamespace
        expected = dict(st_mode=stat.S_IFDIR | 0o1771, st_uid=1000, st_gid=9998, st_dev=12, st_ino=34)
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(**expected)):
            self.assertEqual(tool.parent_metadata()['parentInode'], 34)
        for change in ({'st_mode': stat.S_IFDIR | 0o771}, {'st_gid': 1000}, {'st_uid': 0},
                       {'st_mode': stat.S_IFLNK | 0o1771}):
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(**{**expected, **change})):
                with self.assertRaises(RuntimeError): tool.parent_metadata()

    def test_manifest_cannot_broaden_file_or_parent_identity(self):
        good = {'version': 1, 'beforeAbsent': True, 'candidate': tool.engine.trial.digest(tool.CODE),
                'parentDevice': 12, 'parentInode': 34}
        tool.validate_metadata(good)
        for change in ({'candidate': 'other'}, {'beforeAbsent': False}, {'path': '/data/foreign'},
                       {'parentDevice': True}, {'parentInode': -1}):
            with self.assertRaises(RuntimeError):
                tool.validate_metadata({**good, **change})

    @unittest.skipUnless(os.name == 'posix', 'Linux atomic publication and exact rollback test')
    def test_atomic_override_install_and_absence_restore_preserve_foreign_files(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = root / 'config'; cfg.write_bytes(b'candidate')
            info = root.stat()
            meta = {'version': 1, 'beforeAbsent': True, 'candidate': tool.engine.trial.digest(tool.CODE),
                    'parentDevice': info.st_dev, 'parentInode': info.st_ino}
            layer = {'version': 1, 'key': 'persist.waydroid.use_subsurface', 'before': 'false', 'candidate': 'true'}
            manifest = {'before': tool.engine.trial.digest(b'before'), 'candidate': tool.engine.trial.digest(b'candidate'),
                        'extension': {'layerProperty': layer, 'allocatorOverride': meta}}
            original_fstat = os.fstat
            def rooted(fd):
                value = original_fstat(fd)
                return SimpleNamespace(st_mode=value.st_mode, st_uid=0, st_nlink=value.st_nlink,
                    st_size=value.st_size, st_dev=value.st_dev, st_ino=value.st_ino)
            def start(layered, restoring=False):
                self.assertEqual(layered['extension'], layer)
                tool.engine.validate_extension(layered)
            with patch.object(tool, 'FILE', root / 'waydroid_settings'), \
                 patch.object(tool, 'parent_metadata', return_value=meta), \
                 patch.object(tool.os, 'fstat', side_effect=rooted), \
                 patch.object(tool.engine.trial, 'CFG', cfg), patch.object(tool.engine.trial, 'protected'), \
                 patch.object(tool.allocator.layers, 'assert_stopped'), \
                 patch.object(tool, 'baseline_validate'), patch.object(tool, 'baseline_start', side_effect=start) as launch:
                tool.start_trial(manifest)
                self.assertEqual(tool.FILE.read_bytes(), tool.CODE)
                self.assertFalse((root / tool.TEMP).exists())
                cfg.write_bytes(b'before')
                tool.start_trial(manifest, restoring=True)
                self.assertFalse(tool.FILE.exists())
                self.assertEqual(launch.call_count, 2)
                tool.FILE.write_bytes(b'foreign'); tool.FILE.chmod(0o600)
                with self.assertRaises(RuntimeError): tool.start_trial(manifest, restoring=True)
                self.assertEqual(tool.FILE.read_bytes(), b'foreign')
                self.assertEqual(launch.call_count, 2)

    @unittest.skipUnless(os.name == 'posix', 'Linux dir-fd ownership and nofollow test')
    def test_foreign_and_partial_final_files_refused_but_partial_owned_stage_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            # Native test user is hilman; production checks must still require root.
            original_fstat = os.fstat
            def rooted(file):
                info = original_fstat(file)
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_nlink=info.st_nlink, st_size=info.st_size)
            try:
                with patch.object(tool, 'FILE', root / 'waydroid_settings'), patch.object(tool.os, 'fstat', side_effect=rooted):
                    self.assertFalse(tool.owned_file(fd))
                    target = root / 'waydroid_settings'
                    target.write_bytes(tool.CODE)
                    target.chmod(0o600)
                    self.assertTrue(tool.owned_file(fd))
                    target.write_bytes(tool.CODE[:10])
                    with self.assertRaises(RuntimeError): tool.owned_file(fd)
                    stage = root / tool.TEMP
                    stage.write_bytes(tool.CODE[:10]); stage.chmod(0o600)
                    self.assertTrue(tool.owned_file(fd, temporary=True))
                    stage.write_bytes(b'foreign')
                    with self.assertRaises(RuntimeError): tool.owned_file(fd, temporary=True)
                    stage.unlink(); stage.symlink_to(target)
                    with self.assertRaises(OSError): tool.owned_file(fd, temporary=True)
            finally:
                os.close(fd)

if __name__ == '__main__':
    unittest.main()
