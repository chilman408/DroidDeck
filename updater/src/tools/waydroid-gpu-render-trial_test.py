# SPDX-License-Identifier: GPL-3.0-only
import configparser
import importlib.util
import io
import json
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('gpu_render', Path(__file__).with_name('waydroid-gpu-render-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
test_spec = importlib.util.spec_from_file_location('save_tests', Path(__file__).with_name('waydroid-render-trial_test.py'))
save_tests = importlib.util.module_from_spec(test_spec)
test_spec.loader.exec_module(save_tests)
BASE = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\nunrelated=https://example/%20\n'


class GpuRenderTests(unittest.TestCase):
    def test_config_changes_only_software_rendering(self):
        before, after = [configparser.ConfigParser(interpolation=None) for _ in range(2)]
        before.read_string(BASE.decode())
        after.read_string(tool.candidate(BASE).decode())
        self.assertEqual(after['properties']['ro.waydroid.software_rendering'], '0')
        after.set('properties', 'ro.waydroid.software_rendering', '1')
        self.assertEqual({s: dict(before[s]) for s in before}, {s: dict(after[s]) for s in after})

    def test_refuses_wrong_baseline_and_explicit_gpu_overrides(self):
        for data in [BASE.replace(b'ccodec=4', b'ccodec=3'), BASE.replace(b'software_rendering=1', b'software_rendering=0'),
                     BASE.replace(b'subsurface=false', b'subsurface=true'), BASE + b'ro.hardware.egl=swiftshader\n',
                     b'x' * 65537, b'not config']:
            with self.assertRaises(ValueError):
                tool.candidate(data)

    def test_decode_flags_are_set_after_synchronous_initializer_not_after_boot(self):
        self.assertEqual(tool.INIT_CODE.decode().splitlines(), ['on post-fs-data',
            '    exec -- /vendor/bin/waydroid-init', '    setprop media.sf.hwaccel 0',
            '    setprop debug.ffmpeg-codec2.hwaccel.drm 0', '    setprop debug.ffmpeg-codec2.hwaccel.force 0'])
        self.assertNotIn(b'boot_completed', tool.INIT_CODE)
        self.assertNotIn(b'ctl.', tool.INIT_CODE)

    def test_foreign_aac_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'aac'
            path.write_bytes(b'foreign')
            with patch.object(tool, 'AAC', path), patch.object(tool.trial, 'protected'):
                with self.assertRaisesRegex(RuntimeError, 'AAC overlay changed'):
                    tool.aac_unchanged()
            self.assertEqual(path.read_bytes(), b'foreign')

    def test_selected_video_and_stock_aac_are_required(self):
        expected = {'video/avc': 'c2.ffmpeg.h264.decoder', 'video/hevc': 'c2.ffmpeg.hevc.decoder',
                    'video/av01': 'c2.ffmpeg.av1.decoder', 'video/x-vnd.on2.vp9': 'c2.ffmpeg.vp9.decoder',
                    'audio/mp4a-latm': 'c2.android.aac.decoder', 'audio/ac3': 'c2.ffmpeg.ac3.decoder', 'audio/eac3': None}
        for wrong in [None, 'audio/mp4a-latm', 'video/avc']:
            values = dict(expected)
            if wrong:
                values[wrong] = 'changed'
            snapshot = json.dumps({'version': 1, 'selected': [{'mime': k, 'decoder': v} for k, v in values.items()]})
            with patch.object(tool.trial, 'protected'), patch.object(tool.trial, 'run', return_value=snapshot):
                if wrong:
                    with self.assertRaisesRegex(RuntimeError, 'selection changed'):
                        tool.codec_selection()
                else:
                    self.assertEqual(tool.codec_selection(), expected)

    def files(self, root):
        cfg, original, hook, gpu = [root / name for name in ['cfg', 'original', 'hook', 'gpu']]
        original.write_bytes(BASE)
        cfg.write_bytes(tool.candidate(BASE))
        hook.write_bytes(tool.INIT_CODE)
        gpu.write_bytes(tool.trial.GPU_CODE)
        manifest = {'profile': tool.PROFILE, 'before': tool.trial.digest(BASE), 'candidate': tool.trial.digest(tool.candidate(BASE)),
                    'init': tool.trial.digest(tool.INIT_CODE), 'gpu': tool.trial.digest(tool.trial.GPU_CODE), 'aac': tool.AAC_SHA}
        return cfg, original, hook, gpu, manifest

    def context(self, root, cfg, original, hook, gpu):
        stack = ExitStack()
        for obj, name, value in [(tool, 'BACKUP', root), (tool, 'ORIGINAL', original), (tool, 'INIT', hook),
                                  (tool.trial, 'CFG', cfg), (tool.trial, 'GPU_OVERRIDE', gpu),
                                  (tool.trial, 'DECODER_XML', root / 'decoder.xml')]:
            stack.enter_context(patch.object(obj, name, value))
        for name in ['protected', 'stop', 'start', 'ready']:
            stack.enter_context(patch.object(tool.trial, name))
        stack.enter_context(patch.object(tool, 'aac_unchanged'))
        stack.enter_context(patch.object(tool, 'codec_selection'))
        stack.enter_context(patch.object(tool, 'guest_identity', return_value=('200', root)))
        stack.enter_context(patch.object(tool.trial, 'run', side_effect=lambda args, timeout:
            '1' if args[-1] == 'ro.waydroid.software_rendering' else
            '4' if args[-1] == 'debug.stagefright.ccodec' else 'GLES: ANGLE SwiftShader'))
        return stack

    def test_foreign_decoder_overlay_refused_without_service_mutation(self):
        for dangling in [False, True]:
            with self.subTest(dangling=dangling), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg, original, hook, gpu, manifest = self.files(root)
                overlay = root / 'decoder.xml'
                if dangling:
                    try:
                        overlay.symlink_to(root / 'missing')
                    except OSError:
                        continue  # Windows requires elevated symlink privileges; native test covers this.
                else:
                    overlay.write_bytes(b'foreign decoder overlay')
                with self.context(root, cfg, original, hook, gpu):
                    with self.assertRaisesRegex(RuntimeError, 'decoder overlay'):
                        tool.revert(manifest)
                    tool.trial.stop.assert_not_called()
                    tool.trial.start.assert_not_called()
                self.assertEqual(cfg.read_bytes(), tool.candidate(BASE))
                self.assertTrue(overlay.is_symlink() if dangling else overlay.read_bytes() == b'foreign decoder overlay')

    def test_partial_install_write_allows_exact_baseline_recovery(self):
        for failed_file in ['cfg', 'hook', 'gpu']:
            with self.subTest(failed_file=failed_file), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg, original, hook, gpu, manifest = self.files(root)
                cfg.write_bytes(BASE)
                hook.unlink()
                gpu.unlink()
                target = root / failed_file
                with self.context(root, cfg, original, hook, gpu):
                    for path, data in [(cfg, tool.candidate(BASE)), (hook, tool.INIT_CODE), (gpu, tool.trial.GPU_CODE)]:
                        if path == target:
                            with patch.object(io, 'open', side_effect=save_tests.interrupted_open(io.open)):
                                with self.assertRaises(OSError):
                                    tool.trial.save(path, data, 0o644)
                            break
                        tool.trial.save(path, data, 0o644)
                    tool.revert(manifest)
                    tool.trial.start.assert_called_once()
                self.assertEqual(cfg.read_bytes(), BASE)
                self.assertFalse(hook.exists())
                self.assertFalse(gpu.exists())

    def test_disk_full_recovery_restarts_when_baseline_config_is_already_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, original, hook, gpu, manifest = self.files(root)
            cfg.write_bytes(BASE)
            hook.unlink()
            gpu.unlink()
            with self.context(root, cfg, original, hook, gpu), \
                    patch.object(tool.trial, 'save', side_effect=OSError('persistent disk full')):
                tool.revert(manifest)
                tool.trial.start.assert_called_once()
                tool.trial.ready.assert_called_once()
            self.assertEqual(cfg.read_bytes(), BASE)

    def test_recovery_restores_exact_config_and_removes_only_owned_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, original, hook, gpu, manifest = self.files(root)
            with self.context(root, cfg, original, hook, gpu):
                tool.revert(manifest)
            self.assertEqual(cfg.read_bytes(), BASE)
            self.assertFalse(hook.exists())
            self.assertFalse(gpu.exists())

    def test_stale_config_or_owned_code_refused_before_stopping(self):
        for altered in ['cfg', 'hook', 'gpu', 'original']:
            with self.subTest(altered=altered), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg, original, hook, gpu, manifest = self.files(root)
                (root / altered).write_bytes(b'foreign')
                with self.context(root, cfg, original, hook, gpu):
                    with self.assertRaises((RuntimeError, ValueError)):
                        tool.revert(manifest)
                    tool.trial.stop.assert_not_called()

    def test_reporting_failure_cannot_block_restoration(self):
        with patch.object(tool, 'status', side_effect=OSError('disk full')), patch.object(tool, 'revert') as revert:
            tool.recover({'profile': tool.PROFILE})
        revert.assert_called_once()

    def test_apply_verifies_before_ready_and_recovers_native_failure(self):
        for failure in [None, RuntimeError('private native failure')]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg = root / 'cfg'
                cfg.write_bytes(BASE)
                init = root / 'vendor/etc/init/waydroid-init.rc'
                init.parent.mkdir(parents=True)
                source = root / 'guest/vendor/etc/init/waydroid-init.rc'
                source.parent.mkdir(parents=True)
                source.write_bytes(tool.BASE_INIT)
                backup = root / 'backup'
                gpu = root / 'gpu'
                with ExitStack() as stack:
                    for obj, name, value in [(tool, 'BACKUP', backup), (tool, 'ORIGINAL', backup / 'original.cfg'),
                        (tool, 'INIT', init), (tool.trial, 'CFG', cfg), (tool.trial, 'GPU_OVERRIDE', gpu),
                        (tool.trial, 'DECODER_XML', root / 'absent')]:
                        stack.enter_context(patch.object(obj, name, value))
                    stack.enter_context(patch.object(tool.trial, 'protected'))
                    stack.enter_context(patch.object(tool.trial, 'service', return_value='active'))
                    stack.enter_context(patch.object(tool.trial, 'preferences', return_value={'display': 'unchanged'}))
                    stack.enter_context(patch.object(tool.trial, 'stop'))
                    def start():
                        self.assertEqual(init.read_bytes(), tool.INIT_CODE)
                        self.assertEqual(gpu.read_bytes(), tool.trial.GPU_CODE)
                    stack.enter_context(patch.object(tool.trial, 'start', side_effect=start))
                    stack.enter_context(patch.object(tool.trial, 'ready'))
                    stack.enter_context(patch.object(tool, 'aac_unchanged'))
                    stack.enter_context(patch.object(tool, 'codec_selection'))
                    stack.enter_context(patch.object(tool, 'guest_identity', return_value=('200', root / 'guest')))
                    def verify():
                        self.assertEqual(json.loads((backup / 'status.json').read_text())['stage'], 'verifying-native')
                        if failure:
                            raise failure
                    stack.enter_context(patch.object(tool, 'verify_native', side_effect=verify))
                    revert = stack.enter_context(patch.object(tool, 'revert'))
                    with redirect_stdout(io.StringIO()):
                        if failure:
                            with self.assertRaisesRegex(RuntimeError, 'baseline restored'):
                                tool.perform('--apply')
                        else:
                            tool.perform('--apply')
                    self.assertEqual(revert.call_count, 1 if failure else 0)
                    self.assertEqual(json.loads((backup / 'status.json').read_text())['stage'], 'restored' if failure else 'ready')
                    self.assertNotIn('private', (backup / 'status.json').read_text())

    def test_recovery_failure_is_reported_without_private_child_output(self):
        reports = []
        with patch.object(tool, 'status', side_effect=lambda stage: reports.append(stage)), \
                patch.object(tool, 'revert', side_effect=RuntimeError('private-token')):
            with self.assertRaisesRegex(RuntimeError, 'requires inspection') as raised:
                tool.recover({})
        self.assertNotIn('private-token', str(raised.exception))
        self.assertEqual(reports, ['recovering', 'recovery-required'])

    def test_extension_validation_precedes_stop_and_restoration_precedes_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, original, hook, gpu, manifest = self.files(root)
            events = []
            with self.context(root, cfg, original, hook, gpu), \
                    patch.object(tool, 'validate_extension', side_effect=lambda m: events.append('validate')), \
                    patch.object(tool, 'start_trial', side_effect=lambda m, restoring=False:
                        events.append(('start', restoring, cfg.read_bytes() == BASE))):
                tool.trial.stop.side_effect = lambda: events.append('stop')
                tool.revert(manifest)
            self.assertEqual(events, ['validate', 'stop', ('start', True, True)])

    def test_invalid_extension_refuses_rollback_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, original, hook, gpu, manifest = self.files(root)
            with self.context(root, cfg, original, hook, gpu), \
                    patch.object(tool, 'validate_extension', side_effect=RuntimeError('invalid extension')):
                with self.assertRaisesRegex(RuntimeError, 'invalid extension'):
                    tool.revert(manifest)
                tool.trial.stop.assert_not_called()

    def test_native_checks_require_actual_android_virgl_and_preboot_decode_flags(self):
        for gpu, late_flags in [(True, False), (False, False), (True, True)]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                mounted = root / 'vendor/etc/init/waydroid-init.rc'
                mounted.parent.mkdir(parents=True)
                mounted.write_bytes(tool.INIT_CODE)
                values = {'ro.hardware.gralloc': 'minigbm', 'debug.stagefright.ccodec': '4',
                          'ro.waydroid.software_rendering': '0', 'persist.waydroid.use_subsurface': 'false',
                          'media.sf.hwaccel': '1' if late_flags else '0', 'debug.ffmpeg-codec2.hwaccel.drm': '0',
                          'debug.ffmpeg-codec2.hwaccel.force': '0', 'sys.boot_completed': '1'}
                def run(args, timeout):
                    return values[args[-1]] if args[-2] == '/system/bin/getprop' else (
                        'private app info\nGLES: Mesa virgl\nprivate token' if gpu else 'GLES: ANGLE SwiftShader')
                with patch.object(tool, 'BACKUP', root), patch.object(tool, 'guest_identity', return_value=('200', root)), \
                        patch.object(tool, 'aac_unchanged'), patch.object(tool, 'codec_selection'), \
                        patch.object(tool.trial, 'protected'), patch.object(tool.trial, 'run', side_effect=run):
                    if gpu and not late_flags:
                        tool.verify_native()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                            tool.verify_native()
                report = (root / 'native-state.json').read_text()
                self.assertNotIn('private', report)


if __name__ == '__main__':
    unittest.main()
