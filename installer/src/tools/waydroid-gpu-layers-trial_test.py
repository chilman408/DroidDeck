# SPDX-License-Identifier: GPL-3.0-only
import configparser
import importlib.util
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('layers', Path(__file__).with_name('waydroid-gpu-layers-trial.py'))
wrapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wrapper)
tool = wrapper.engine
BASE = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\n'


class LayersTests(unittest.TestCase):
    def test_held_candidate_cli_fails_closed_before_any_runtime_action(self):
        for action in ['--apply', '--rollback']:
            result = subprocess.run([sys.executable, str(Path(wrapper.__file__)), action],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Held unsafe candidate', result.stderr)

    def test_layer_override_runs_after_saved_properties_are_loaded(self):
        actions, commands = {}, None
        for line in tool.INIT_CODE.decode().splitlines():
            if line.startswith('on '):
                commands = actions.setdefault(line[3:], [])
            elif line.strip():
                commands.append(line.strip().split())
        properties = {'persist.waydroid.use_subsurface': 'false'}
        # Exercise the generated init action contract; native verification remains mandatory.
        for command in actions.get('property:ro.persistent_properties.ready=true', []):
            self.assertEqual(command[0], 'setprop')
            self.assertEqual(len(command), 3)
            properties[command[1]] = command[2]
        self.assertEqual(properties, {'persist.waydroid.use_subsurface': 'true'})

    def test_only_two_config_properties_change_and_guards_remain(self):
        before, after = configparser.ConfigParser(), configparser.ConfigParser()
        before.read_string(BASE.decode())
        after.read_string(tool.candidate(BASE).decode())
        self.assertEqual(dict(before['waydroid']), dict(after['waydroid']))
        self.assertEqual(dict(after['properties']), {**dict(before['properties']),
            'ro.waydroid.software_rendering': '0', 'persist.waydroid.use_subsurface': 'true'})
        self.assertEqual(tool.EXPECTED_NATIVE['persist.waydroid.use_subsurface'], 'true')
        for key in ['media.sf.hwaccel', 'debug.ffmpeg-codec2.hwaccel.drm', 'debug.ffmpeg-codec2.hwaccel.force']:
            self.assertEqual(tool.EXPECTED_NATIVE[key], '0')
            self.assertIn(('    setprop ' + key + ' 0\n').encode(), tool.INIT_CODE)
        self.assertEqual(tool.EXPECTED_NATIVE['debug.ffmpeg-codec2.pixel_format'], 'RGBX_8888')
        self.assertEqual(tool.RESTORED_PROPERTIES['persist.waydroid.use_subsurface'], 'false')

    def test_rejected_or_foreign_baseline_is_refused(self):
        for data in [BASE.replace(b'rendering=1', b'rendering=0'),
                     BASE.replace(b'ccodec=4', b'ccodec=3'),
                     BASE.replace(b'subsurface=false', b'subsurface=true'),
                     BASE + b'ro.hardware.gralloc=minigbm\n']:
            with self.assertRaises(ValueError):
                tool.candidate(data)

    def test_private_identity_cannot_reuse_rejected_backups(self):
        self.assertEqual(tool.BACKUP.name, 'gpu-render-layers-20261010g3')
        self.assertEqual(tool.LOCK.name, '.gpu-render-layers-20261010g3.lock')
        self.assertEqual(tool.ORIGINAL, tool.BACKUP / 'original.cfg')
        for profile in ['gpu-render-codec4-rgbx-early-softdecode', 'gpu-render-codec4-early-softdecode',
                        'gpu-render-codec4-rgbx-layers-early-softdecode']:
            with patch.object(tool.trial, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'Invalid GPU rendering backup'):
                    tool.revert({'profile': profile})
                stop.assert_not_called()

    def test_native_verification_requires_layers_and_sanitizes_unknown_values(self):
        for subsurface in ['true', 'false', 'private-token']:
            with self.subTest(subsurface=subsurface), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                hook = root / 'vendor/etc/init/waydroid-init.rc'
                hook.parent.mkdir(parents=True)
                hook.write_bytes(tool.INIT_CODE)
                values = {**tool.EXPECTED_NATIVE, 'persist.waydroid.use_subsurface': subsurface}
                with ExitStack() as stack:
                    stack.enter_context(patch.object(tool, 'BACKUP', root))
                    stack.enter_context(patch.object(tool, 'guest_identity', return_value=('200', root)))
                    for name in ['aac_unchanged', 'codec_selection']:
                        stack.enter_context(patch.object(tool, name))
                    stack.enter_context(patch.object(tool.trial, 'protected'))
                    stack.enter_context(patch.object(tool.trial, 'run', side_effect=lambda args, timeout:
                        values[args[-1]] if args[-2] == '/system/bin/getprop' else 'GLES: Mesa virgl\nprivate-token'))
                    if subsurface == 'true':
                        tool.verify_native()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                            tool.verify_native()
                self.assertNotIn('private-token', (root / 'native-state.json').read_text())

    def test_rollback_restores_exact_config_and_checks_layer_mode(self):
        for subsurface in ['false', 'true']:
            with self.subTest(subsurface=subsurface), tempfile.TemporaryDirectory() as directory:
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
                    for name in ['aac_unchanged', 'codec_selection']:
                        stack.enter_context(patch.object(tool, name))
                    stack.enter_context(patch.object(tool, 'guest_identity', return_value=('200', root)))
                    values = {'ro.waydroid.software_rendering': '1', 'debug.stagefright.ccodec': '4',
                              'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888',
                              'persist.waydroid.use_subsurface': subsurface}
                    stack.enter_context(patch.object(tool.trial, 'run', side_effect=lambda args, timeout:
                        values[args[-1]] if args[-2] == '/system/bin/getprop' else 'GLES: ANGLE SwiftShader'))
                    if subsurface == 'false':
                        tool.revert(manifest)
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'properties not recovered'):
                            tool.revert(manifest)
                self.assertEqual(cfg.read_bytes(), BASE)
                self.assertFalse(hook.exists())
                self.assertFalse(gpu.exists())


if __name__ == '__main__':
    unittest.main()
