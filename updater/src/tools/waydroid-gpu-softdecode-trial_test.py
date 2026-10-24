# SPDX-License-Identifier: GPL-3.0-only
import configparser
from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('gpu_soft', Path(__file__).with_name('waydroid-gpu-softdecode-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class GpuSoftDecodeTests(unittest.TestCase):
    def test_explicit_rollback_replaces_stale_ready_status_and_redacts_failure(self):
        for failure in [None, RuntimeError('H264 recovery deadline exceeded'),
                        RuntimeError('private-token=https://secret.invalid')]:
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'manifest.json').write_text(json.dumps({'profile': 'gpu-softdecode'}))
                (root / 'status.json').write_text(json.dumps({'phase': 'verified', 'outcome': 'ready'}))
                def revert(manifest):
                    self.assertEqual(manifest['profile'], 'gpu-softdecode')
                    self.assertEqual(json.loads((root / 'status.json').read_text()),
                                     {'phase': 'explicit-restoration', 'outcome': 'running'})
                    if failure is not None:
                        raise failure
                with patch.object(tool, 'BACKUP', root), patch.object(tool.trial, 'protected'), \
                        patch.object(tool.os, 'getuid', return_value=0, create=True), \
                        patch.object(tool, 'revert', side_effect=revert), redirect_stdout(io.StringIO()):
                    if failure is None:
                        tool.main(['--rollback'])
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Graphics rollback failed') as raised:
                            tool.main(['--rollback'])
                        self.assertNotIn('secret.invalid', str(raised.exception))
                report = json.loads((root / 'status.json').read_text())
                if failure is None:
                    self.assertEqual(report, {'phase': 'explicit-restoration', 'outcome': 'restored'})
                else:
                    self.assertEqual(report, {'phase': 'explicit-restoration', 'outcome': 'failed',
                                             'reason': tool.failure_reason(failure), 'rollback': 'failed'})
                self.assertNotIn('secret.invalid', (root / 'status.json').read_text())

    def test_native_mismatch_saves_all_allowlisted_values_before_recovery(self):
        values = {'ro.hardware.gralloc': 'minigbm', 'debug.stagefright.ccodec': '3',
                  'media.sf.hwaccel': '1', 'debug.ffmpeg-codec2.hwaccel.drm': '1',
                  'debug.ffmpeg-codec2.hwaccel.force': 'private-user-input', 'waydroid.init.done': ''}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def run(args, timeout):
                if args[0] == '/usr/bin/lxc-info':
                    return '200'
                return values[args[-1]]
            with patch.object(tool, 'BACKUP', root), patch.object(tool.trial, 'run', side_effect=run), \
                    patch.object(tool.trial, 'protected'):
                with self.assertRaisesRegex(RuntimeError, 'media.sf.hwaccel'):
                    tool.verify_native()
            self.assertTrue((root / 'native-state.json').exists(), 'Native evidence missing')
            report = json.loads((root / 'native-state.json').read_text())
            self.assertEqual(report['media.sf.hwaccel'], '1')
            self.assertEqual(report['debug.ffmpeg-codec2.hwaccel.drm'], '1')
            self.assertEqual(report['debug.ffmpeg-codec2.hwaccel.force'], 'other')
            self.assertEqual(report['waydroid.init.done'], '')
            self.assertNotIn('private-user-input', (root / 'native-state.json').read_text())

    def failed_trial(self, directory, failure, rollback_failure=None, diagnostic=False, mismatched_backup=False):
        root = Path(directory)
        config = root / 'config'
        config.write_bytes(b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\n')
        backups = root / 'backups'
        backups.mkdir()
        gpu = root / 'units/gpu'
        gpu.parent.mkdir()
        hook = root / 'vendor/etc/init/hook'
        hook.parent.mkdir(parents=True)
        backup = backups / 'trial'
        if diagnostic:
            backup.mkdir()
            original = config.read_bytes()
            (backup / 'original').write_bytes(original)
            (backup / 'manifest.json').write_text(json.dumps({'profile': 'gpu-softdecode',
                'before': 'stale' if mismatched_backup else tool.trial.digest(original),
                'candidate': tool.trial.digest(tool.candidate(original)),
                'preferences': {'display': 'unchanged'}}))
        with ExitStack() as stack:
            for owner, name, value in [(tool.trial, 'CFG', config), (tool.trial, 'BACKUPS', backups),
                                       (tool.trial, 'GPU_OVERRIDE', gpu), (tool, 'BACKUP', backup),
                                       (tool, 'ORIGINAL', backup / 'original'), (tool, 'INIT', hook),
                                       (tool.trial, 'DECODER_XML', root / 'unused')]:
                stack.enter_context(patch.object(owner, name, value))
            stack.enter_context(patch.object(tool.os, 'getuid', return_value=0, create=True))
            stack.enter_context(patch.object(tool.trial, 'protected'))
            stack.enter_context(patch.object(tool.trial, 'service', return_value='active'))
            stack.enter_context(patch.object(tool.trial, 'preferences', return_value={'display': 'unchanged'}))
            stack.enter_context(patch.object(tool.trial, 'stop'))
            stack.enter_context(patch.object(tool.trial, 'start'))
            stack.enter_context(patch.object(tool.trial, 'ready'))
            stack.enter_context(patch.object(tool, 'verify_native', side_effect=failure))
            if rollback_failure:
                stack.enter_context(patch.object(tool, 'revert', side_effect=rollback_failure))
            output = io.StringIO()
            with redirect_stdout(output):
                try:
                    tool.main(['--diagnose-retry' if diagnostic else '--apply'])
                    message = output.getvalue()
                except RuntimeError as error:
                    message = str(error)
        return backup, message, config, gpu, hook

    def test_diagnostic_retry_reuses_only_matching_backup_and_restores_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, config, gpu, hook = self.failed_trial(directory, RuntimeError(
                'Graphics/decoder trial property did not load: media.sf.hwaccel'), diagnostic=True)
            self.assertIn('property-mismatch:media.sf.hwaccel', message)
            self.assertEqual(config.read_bytes(), (backup / 'original').read_bytes())
            self.assertFalse(gpu.exists())
            self.assertFalse(hook.exists())
            self.assertEqual(json.loads((backup / 'status.json').read_text())['rollback'], 'restored')

    def test_successful_diagnostic_also_restores_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, config, gpu, hook = self.failed_trial(directory, None, diagnostic=True)
            self.assertEqual(config.read_bytes(), (backup / 'original').read_bytes())
            self.assertFalse(gpu.exists())
            self.assertFalse(hook.exists())
            self.assertTrue(message.startswith('{'), 'Diagnostic did not complete and report recovery')
            self.assertEqual(json.loads(message)['diagnostic'], 'gpu-softdecode')
            self.assertEqual(json.loads((backup / 'status.json').read_text())['outcome'], 'diagnosed-restored')

    def test_diagnostic_refuses_stale_backup_before_installing_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, config, gpu, hook = self.failed_trial(
                directory, None, diagnostic=True, mismatched_backup=True)
            self.assertIn('Diagnostic backup does not match recovered baseline', message)
            self.assertFalse(gpu.exists())
            self.assertFalse(hook.exists())
            self.assertEqual(config.read_bytes(), (backup / 'original').read_bytes())
            self.assertFalse((backup / 'status.json').exists())

    def test_failed_property_verification_persists_cause_after_real_file_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, config, gpu, hook = self.failed_trial(directory, RuntimeError(
                'Graphics/decoder trial property did not load: ro.hardware.gralloc'))
            self.assertIn('property-mismatch:ro.hardware.gralloc', message)
            report = json.loads((backup / 'status.json').read_text())
            self.assertEqual(report, {'phase': 'native-verification', 'outcome': 'failed',
                                     'reason': 'property-mismatch:ro.hardware.gralloc', 'rollback': 'restored'})
            self.assertEqual(config.read_bytes(), (backup / 'original').read_bytes())
            self.assertFalse(gpu.exists())
            self.assertFalse(hook.exists())

    def test_unexpected_failure_does_not_leak_child_output(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, *_ = self.failed_trial(directory, RuntimeError('private-token=https://secret.invalid'))
            self.assertNotIn('private-token', message)
            self.assertTrue((backup / 'status.json').is_file(), 'Failure report was lost during rollback')
            report = json.loads((backup / 'status.json').read_text())
            self.assertEqual(report['reason'], 'unexpected-error')
            self.assertEqual(report['rollback'], 'restored')
            self.assertNotIn('secret.invalid', (backup / 'status.json').read_text())

    def test_recovery_failure_keeps_original_failure_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            backup, message, *_ = self.failed_trial(directory, RuntimeError(
                'Graphics/decoder trial property did not load: ro.hardware.gralloc'), RuntimeError('private recovery output'))
            self.assertIn('rollback failed', message)
            report = json.loads((backup / 'status.json').read_text())
            self.assertEqual(report['reason'], 'property-mismatch:ro.hardware.gralloc')
            self.assertEqual(report['rollback'], 'failed')
            self.assertNotIn('private recovery output', message)

    def test_candidate_changes_only_renderer_and_stock_decoder_priority(self):
        before = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\nprivate.unrelated=keep\n'
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(tool.candidate(before).decode())
        self.assertEqual(parser['properties']['ro.waydroid.software_rendering'], '0')
        self.assertEqual(parser['properties']['debug.stagefright.ccodec'], '3')
        self.assertEqual(parser['properties']['persist.waydroid.use_subsurface'], 'false')
        self.assertEqual(parser['properties']['private.unrelated'], 'keep')
        self.assertEqual(parser['waydroid']['arch'], 'x86_64')

    def test_init_hook_disables_only_hardware_decode_after_vendor_initialization(self):
        self.assertEqual(tool.INIT_CODE.decode().splitlines(), [
            'on property:sys.boot_completed=1',
            '    setprop media.sf.hwaccel 0',
            '    setprop debug.ffmpeg-codec2.hwaccel.drm 0',
            '    setprop debug.ffmpeg-codec2.hwaccel.force 0'])
        self.assertNotIn(b'ctl.', tool.INIT_CODE)
        self.assertNotIn(b'secure', tool.INIT_CODE)

    def test_candidate_refuses_unrecovered_rendering_or_wrong_codec_baseline(self):
        for data in [b'[waydroid]\n[properties]\nro.waydroid.software_rendering=0\ndebug.stagefright.ccodec=4\n',
                     b'[waydroid]\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=0\n']:
            with self.assertRaises(ValueError):
                tool.candidate(data)

    def test_revert_checks_every_owned_file_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, original, gpu, hook = [root / name for name in ['config', 'original', 'gpu', 'hook']]
            config.write_bytes(b'candidate')
            original.write_bytes(b'original')
            gpu.write_bytes(tool.trial.GPU_CODE)
            hook.write_bytes(b'foreign')
            manifest = {'profile': 'gpu-softdecode', 'before': tool.trial.digest(b'original'),
                        'candidate': tool.trial.digest(b'candidate')}
            with patch.object(tool.trial, 'CFG', config), patch.object(tool, 'BACKUP', root), \
                    patch.object(tool.trial, 'GPU_OVERRIDE', gpu), patch.object(tool, 'INIT', hook), \
                    patch.object(tool, 'ORIGINAL', original), patch.object(tool.trial, 'protected'), \
                    patch.object(tool.trial, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    tool.revert(manifest)
                stop.assert_not_called()
            self.assertEqual(config.read_bytes(), b'candidate')


if __name__ == '__main__':
    unittest.main()
