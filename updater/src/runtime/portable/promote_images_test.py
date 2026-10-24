# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('promote-images.py')
SPEC = importlib.util.spec_from_file_location('promote_images', FILE) if FILE.exists() else None
PROMOTE = importlib.util.module_from_spec(SPEC) if SPEC else None
if PROMOTE:
    SPEC.loader.exec_module(PROMOTE)


class PromotionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PROMOTE, 'Protected promotion API missing')

    def test_fixed_layout_and_copier_pin(self):
        self.assertEqual(PROMOTE.SOURCE_PARENT, Path('/var/lib/droiddeck/image-staging'))
        self.assertEqual(PROMOTE.PARENT, Path('/etc/waydroid-extra'))
        self.assertEqual(PROMOTE.WORK, Path('/var/lib/waydroid'))
        self.assertEqual(PROMOTE.COPIER_SHA256, '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a')

    def test_no_cli_path_or_policy_override(self):
        for args in ([], ['new', 'other'], ['../new'], ['/images'], ['a b'], ['A'], ['x' * 65]):
            with self.subTest(args=args), self.assertRaises(ValueError):
                PROMOTE.arguments(args)
        self.assertEqual(PROMOTE.arguments(['fresh-20261013an']), 'fresh-20261013an')

    def test_platform_and_root_guard_precedes_helper_load(self):
        with mock.patch.object(PROMOTE.sys, 'platform', 'win32'), mock.patch.object(PROMOTE, 'load_copier') as loader:
            with self.assertRaises(ValueError):
                PROMOTE.main(['new'])
            loader.assert_not_called()
        with mock.patch.object(PROMOTE.sys, 'platform', 'linux'), mock.patch.object(PROMOTE.os, 'geteuid', return_value=1000, create=True):
            with self.assertRaises(ValueError):
                PROMOTE.main(['new'])

    def test_cli_reporting_flushes_committed_receipt(self):
        class FailedSink(io.RawIOBase):
            def writable(self):
                return True

            def write(self, data):
                raise OSError('fixture broken stdout')

        output = io.TextIOWrapper(io.BufferedWriter(FailedSink()), encoding='utf-8')
        messages = io.StringIO()
        try:
            with mock.patch.object(PROMOTE, 'main', return_value={'promoted': True}), mock.patch.object(PROMOTE.sys, 'stdout', output), mock.patch.object(PROMOTE.sys, 'stderr', messages):
                result = PROMOTE.run_cli(['new'])
            self.assertEqual(result, 2)
            self.assertIn('committed', messages.getvalue())
        finally:
            try:
                output.close()
            except OSError:
                pass

    def test_both_failed_output_sinks_preserve_committed_or_uncertain_status(self):
        with mock.patch.object(PROMOTE, 'main', return_value={'promoted': True}), mock.patch.object(PROMOTE.sys, 'stdout', io.StringIO()), mock.patch.object(PROMOTE.sys, 'stderr', io.StringIO()), mock.patch('builtins.print', side_effect=OSError('fixture both sinks failed')):
            self.assertEqual(PROMOTE.run_cli(['new']), 2)
        with mock.patch.object(PROMOTE, 'main', side_effect=PROMOTE.PromotionUncertain('fixture uncertain')), mock.patch.object(PROMOTE.sys, 'stderr', io.StringIO()), mock.patch('builtins.print', side_effect=OSError('fixture stderr failed')):
            self.assertEqual(PROMOTE.run_cli(['new']), 2)

    def test_real_process_shutdown_preserves_buffered_failure_phase(self):
        program = '''import importlib.util, io, sys
spec = importlib.util.spec_from_file_location('fixture_promote', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.main = lambda args: {'promoted': True}
class FailedSink(io.RawIOBase):
    def writable(self): return True
    def write(self, data): raise OSError('generated buffered sink failure')
sys.stdout = io.TextIOWrapper(io.BufferedWriter(FailedSink()), encoding='utf-8')
sys.stderr = io.TextIOWrapper(io.BufferedWriter(FailedSink()), encoding='utf-8')
sys.exit(module.run_cli(['fixture']))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(FILE)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr.decode(errors='replace'))


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class RootPromotionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(PROMOTE, 'Protected promotion API missing')
        self.copier = PROMOTE.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-promote-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.original = self.base / 'original'
        self.source_parent = self.base / 'stages'
        self.parent = self.base / 'preinstalled'
        self.work = self.base / 'waydroid'
        for path in (self.original, self.source_parent, self.parent, self.work):
            path.mkdir(mode=0o700)
        self.pins = {}
        for name, payload in (('system.img', b'fixture-system' * 80), ('vendor.img', b'fixture-vendor' * 40)):
            (self.original / name).write_bytes(payload)
            self.pins[name] = (len(payload), hashlib.sha256(payload).hexdigest())
        self.copier._stage_images(str(self.original), self.source_parent, 'seed', self.pins)
        self.source = self.source_parent / 'seed'
        self.output = self.parent / 'images'

    def promote(self):
        return PROMOTE._promote(self.copier, self.source_parent, self.parent, self.work, 'seed', self.pins)

    def test_atomic_pair_preserves_source_and_private_modes(self):
        before = {n: (self.source / n).stat().st_ino for n in self.pins}
        original = self.copier.copy_image

        def observed(*args, **kwargs):
            self.assertFalse(self.output.exists(), 'Waydroid must not see partial images')
            return original(*args, **kwargs)

        with mock.patch.object(self.copier, 'copy_image', side_effect=observed):
            receipt = self.promote()
        self.assertTrue(receipt['promoted'])
        self.assertFalse(receipt['publisherPinsMatched'])
        self.assertFalse(receipt['initialized'])
        self.assertFalse(receipt['installable'])
        self.assertEqual(sorted(p.name for p in self.output.iterdir()), ['ready.json', 'system.img', 'vendor.img'])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for n in self.pins:
            self.assertEqual((self.source / n).stat().st_ino, before[n])
            self.assertNotEqual((self.output / n).stat().st_ino, before[n])
            self.assertEqual((self.source / n).read_bytes(), (self.output / n).read_bytes())
        for path in self.output.iterdir():
            info = path.stat()
            self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, 0o600, 1))

    def test_existing_and_raced_empty_destination_never_overwritten(self):
        self.output.mkdir(mode=0o700)
        inode = self.output.stat().st_ino
        with self.assertRaises((ValueError, OSError)):
            self.promote()
        self.assertEqual(self.output.stat().st_ino, inode)
        self.output.rmdir()
        original = PROMOTE.rename_exclusive

        def raced(fd, pending):
            self.output.mkdir(mode=0o700)
            self.raced_inode = self.output.stat().st_ino
            return original(fd, pending)

        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=raced):
            with self.assertRaises(FileExistsError):
                self.promote()
        self.assertEqual(self.output.stat().st_ino, self.raced_inode)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_config_and_stale_state_refuse(self):
        for name in ('waydroid.cfg', 'images', 'rootfs', 'overlay', 'overlay_rw', 'data', 'lxc'):
            marker = self.work / name
            marker.write_text('do not overwrite')
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.promote()
            self.assertEqual(marker.read_text(), 'do not overwrite')
            self.assertFalse(self.output.exists())
            marker.unlink()

    def test_config_appearing_during_copy_prevents_publication(self):
        original = self.copier.copy_image

        def appeared(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.work / 'waydroid.cfg').write_text('new concurrent installation')
            return result

        with mock.patch.object(self.copier, 'copy_image', side_effect=appeared):
            with self.assertRaises(ValueError):
                self.promote()
        self.assertFalse(self.output.exists())

    def test_config_appearing_at_rename_reports_uncertain(self):
        original = PROMOTE.rename_exclusive

        def appeared(fd, pending):
            (self.work / 'waydroid.cfg').write_text('concurrent initialization')
            return original(fd, pending)

        with mock.patch.object(PROMOTE, 'rename_exclusive', side_effect=appeared):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.promote()
        self.assertTrue((self.output / 'ready.json').exists())

    def test_protected_source_and_parent_modes_required(self):
        for path in (self.source / 'system.img', self.source / 'ready.json', self.source, self.parent, self.work):
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(0o777)
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.promote()
            path.chmod(mode)
            self.assertFalse(self.output.exists())

    def test_bad_bytes_or_linked_source_refuse(self):
        target = self.source / 'vendor.img'
        target.write_bytes(b'x' * self.pins['vendor.img'][0])
        with self.assertRaises(ValueError):
            self.promote()
        self.assertFalse(self.output.exists())
        target.unlink()
        target.symlink_to(self.source / 'system.img')
        with self.assertRaises((ValueError, OSError)):
            self.promote()
        self.assertFalse(self.output.exists())

    def test_unpublished_copy_failure_is_not_final_images(self):
        with mock.patch.object(self.copier, 'copy_image', side_effect=OSError('fixture failed copy')):
            with self.assertRaises(OSError):
                self.promote()
        self.assertFalse(self.output.exists())

    def test_late_parent_sync_failure_reports_uncertain_and_retains_images(self):
        original = PROMOTE.os.fsync

        def failed(fd):
            if self.output.exists():
                raise OSError('fixture final parent sync failure')
            return original(fd)

        with mock.patch.object(PROMOTE.os, 'fsync', side_effect=failed):
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                self.promote()
        self.assertTrue((self.output / 'ready.json').exists())

    def test_sibling_helper_hash_and_ownership_reject_mutation(self):
        helper = self.base / 'stage-images.py'
        helper.write_bytes(Path(PROMOTE.__file__).with_name('stage-images.py').read_bytes())
        helper.chmod(0o600)
        PROMOTE._load_copier(helper)
        helper.write_bytes(helper.read_bytes() + b'\n# changed fixture\n')
        with self.assertRaises(ValueError):
            PROMOTE._load_copier(helper)
        helper.unlink()
        helper.symlink_to(Path(PROMOTE.__file__).with_name('stage-images.py'))
        with self.assertRaises((ValueError, OSError)):
            PROMOTE._load_copier(helper)

    def test_helper_owner_writable_ancestor_and_hardlinks_reject(self):
        helper = self.base / 'stage-images.py'
        helper.write_bytes(Path(PROMOTE.__file__).with_name('stage-images.py').read_bytes())
        helper.chmod(0o600)
        os.chown(helper, 1000, 1000)
        with self.assertRaises(ValueError):
            PROMOTE._load_copier(helper)
        os.chown(helper, 0, 0)
        self.base.chmod(0o777)
        with self.assertRaises(ValueError):
            PROMOTE._load_copier(helper)
        self.base.chmod(0o700)
        os.link(helper, self.base / 'alias')
        with self.assertRaises(ValueError):
            PROMOTE._load_copier(helper)

    def test_helper_path_replacement_during_read_rejects(self):
        helper = self.base / 'stage-images.py'
        data = Path(PROMOTE.__file__).with_name('stage-images.py').read_bytes()
        helper.write_bytes(data)
        helper.chmod(0o600)
        original, changed = PROMOTE.os.read, False

        def replaced(fd, size):
            nonlocal changed
            block = original(fd, size)
            if block and not changed:
                changed = True
                helper.unlink()
                helper.write_bytes(data)
                helper.chmod(0o600)
            return block

        with mock.patch.object(PROMOTE.os, 'read', side_effect=replaced):
            with self.assertRaises(ValueError):
                PROMOTE._load_copier(helper)

    def test_helper_executes_held_verified_bytes_not_replacement_path(self):
        helper = self.base / 'stage-images.py'
        helper.write_bytes(Path(PROMOTE.__file__).with_name('stage-images.py').read_bytes())
        helper.chmod(0o600)
        original = compile

        def replaced(data, filename, mode):
            helper.unlink()
            helper.write_text('raise RuntimeError("replacement must not run")\n')
            helper.chmod(0o600)
            return original(data, filename, mode)

        with mock.patch('builtins.compile', side_effect=replaced):
            loaded = PROMOTE._load_copier(helper)
        self.assertEqual(loaded.PINS, self.copier.PINS)


if __name__ == '__main__':
    unittest.main()
