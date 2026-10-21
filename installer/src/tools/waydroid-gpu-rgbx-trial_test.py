# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rgbx', Path(__file__).with_name('waydroid-gpu-rgbx-trial.py'))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
tool = wrapper.engine
BASE = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\n'


class RgbxTests(unittest.TestCase):
    def test_separate_profile_backup_lock_and_preboot_rgbx(self):
        self.assertEqual(tool.BACKUP.name, 'gpu-render-rgbx-20261010e')
        self.assertEqual(tool.LOCK.name, '.gpu-render-rgbx-20261010e.lock')
        self.assertEqual(tool.ORIGINAL, tool.BACKUP / 'original.cfg')
        self.assertNotEqual(tool.PROFILE, 'gpu-render-codec4-early-softdecode')
        lines = tool.INIT_CODE.decode().splitlines()
        self.assertEqual(lines[:2], ['on post-fs-data', '    exec -- /vendor/bin/waydroid-init'])
        self.assertEqual(lines[-1], '    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888')
        self.assertEqual(len(lines), 6)
        self.assertNotIn(b'boot_completed', tool.INIT_CODE)
        self.assertEqual(tool.EXPECTED_NATIVE['debug.ffmpeg-codec2.pixel_format'], 'RGBX_8888')
        self.assertEqual(tool.RESTORED_PROPERTIES, {'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'})
        config = tool.candidate(BASE)
        self.assertIn(b'ro.waydroid.software_rendering = 0', config)
        self.assertIn(b'debug.stagefright.ccodec = 4', config)
        self.assertNotIn(b'pixel_format', config)

    def test_native_verification_requires_actual_rgbx_without_leaking_raw_output(self):
        for pixel in ['RGBX_8888', 'YUV_420', 'private-token']:
            with self.subTest(pixel=pixel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mounted = root / 'vendor/etc/init/waydroid-init.rc'
                mounted.parent.mkdir(parents=True)
                mounted.write_bytes(tool.INIT_CODE)
                values = {**tool.EXPECTED_NATIVE, 'debug.ffmpeg-codec2.pixel_format': pixel}
                def run(args, timeout):
                    return values[args[-1]] if args[-2] == '/system/bin/getprop' else 'GLES: Mesa virgl\nprivate-token'
                with patch.object(tool, 'BACKUP', root), patch.object(tool, 'guest_identity', return_value=('200', root)), \
                        patch.object(tool, 'aac_unchanged'), patch.object(tool, 'codec_selection'), \
                        patch.object(tool.trial, 'protected'), patch.object(tool.trial, 'run', side_effect=run):
                    if pixel == 'RGBX_8888':
                        tool.verify_native()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                            tool.verify_native()
                self.assertNotIn('private-token', (root / 'native-state.json').read_text())

    def test_rejected_profile_manifest_cannot_restore_or_remove_anything(self):
        with patch.object(tool.trial, 'stop') as stop:
            with self.assertRaisesRegex(RuntimeError, 'Invalid GPU rendering backup'):
                tool.revert({'profile': 'gpu-render-codec4-early-softdecode'})
            stop.assert_not_called()

    def test_exact_rollback_preserves_original_config_and_checks_restored_buffer_format(self):
        for pixel in ['RGBX_8888', 'YUV_420']:
            with self.subTest(pixel=pixel), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg, original, hook, gpu = [root / name for name in ['cfg', 'original', 'hook', 'gpu']]
                original.write_bytes(BASE)
                cfg.write_bytes(tool.candidate(BASE))
                hook.write_bytes(tool.INIT_CODE)
                gpu.write_bytes(tool.trial.GPU_CODE)
                manifest = {'profile': tool.PROFILE, 'before': tool.trial.digest(BASE),
                    'candidate': tool.trial.digest(tool.candidate(BASE)), 'init': tool.trial.digest(tool.INIT_CODE),
                    'gpu': tool.trial.digest(tool.trial.GPU_CODE), 'aac': tool.AAC_SHA}
                with ExitStack() as stack:
                    for obj, name, value in [(tool, 'ORIGINAL', original), (tool, 'INIT', hook),
                        (tool.trial, 'CFG', cfg), (tool.trial, 'GPU_OVERRIDE', gpu)]:
                        stack.enter_context(patch.object(obj, name, value))
                    for name in ['protected', 'stop', 'start', 'ready']:
                        stack.enter_context(patch.object(tool.trial, name))
                    stack.enter_context(patch.object(tool, 'aac_unchanged'))
                    stack.enter_context(patch.object(tool, 'codec_selection'))
                    stack.enter_context(patch.object(tool, 'guest_identity', return_value=('200', root)))
                    values = {'ro.waydroid.software_rendering': '1', 'debug.stagefright.ccodec': '4',
                              'debug.ffmpeg-codec2.pixel_format': pixel}
                    stack.enter_context(patch.object(tool.trial, 'run', side_effect=lambda args, timeout:
                        values[args[-1]] if args[-2] == '/system/bin/getprop' else 'GLES: ANGLE SwiftShader'))
                    if pixel == 'RGBX_8888':
                        tool.revert(manifest)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'properties not recovered'):
                            tool.revert(manifest)
                self.assertEqual(cfg.read_bytes(), BASE)
                self.assertFalse(hook.exists())
                self.assertFalse(gpu.exists())


if __name__ == '__main__':
    unittest.main()
