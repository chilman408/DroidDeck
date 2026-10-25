# SPDX-License-Identifier: GPL-3.0-only
import configparser
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('offline_layers', Path(__file__).with_name('waydroid-gpu-offline-layers-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
BASE = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\ndebug.stagefright.ccodec=4\npersist.waydroid.use_subsurface=false\n'
EXTENSION = {'version': 1, 'key': tool.properties.KEY, 'before': 'false', 'candidate': 'true'}


class OfflineLayersTests(unittest.TestCase):
    def test_candidate_changes_only_rendering_and_layers(self):
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(tool.candidate(BASE).decode())
        self.assertEqual(dict(parser['properties']), {'ro.waydroid.software_rendering': '0',
            'debug.stagefright.ccodec': '4', 'persist.waydroid.use_subsurface': 'true'})
        self.assertNotIn(b'setprop persist.', tool.engine.INIT_CODE)
        self.assertNotIn(b'persistent_properties.ready', tool.engine.INIT_CODE)

    def test_stopped_state_must_be_exact(self):
        for state in ['RUNNING', '', 'FROZEN', 'STOPPED']:
            with patch.object(tool.engine.trial, 'run', return_value=state):
                if state == 'STOPPED':
                    tool.assert_stopped()
                else:
                    with self.assertRaises(RuntimeError):
                        tool.assert_stopped()

    def test_foreign_manifest_is_rejected_without_start_or_write(self):
        for extension in [None, {}, {**EXTENSION, 'before': 'true'}, {**EXTENSION, 'extra': 'foreign'}]:
            store = Mock()
            with patch.object(tool, 'store', return_value=store), patch.object(tool.engine.trial, 'start') as start:
                with self.assertRaises(RuntimeError):
                    tool.start_trial({'extension': extension}, restoring=True)
                store.set_value.assert_not_called()
                start.assert_not_called()

    def test_offline_property_change_precedes_start_and_recovery_resets_only_key(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'cfg'
            original, candidate = BASE, tool.candidate(BASE)
            manifest = {'extension': EXTENSION, 'before': tool.engine.trial.digest(original),
                        'candidate': tool.engine.trial.digest(candidate)}
            events = []
            store = Mock()
            store.value.return_value = 'false'
            store.set_value.side_effect = lambda expected, target: events.append((expected, target))
            with patch.object(tool, 'store', return_value=store), patch.object(tool.engine.trial, 'CFG', cfg), \
                    patch.object(tool.engine.trial, 'protected'), \
                    patch.object(tool.engine.trial, 'start', side_effect=lambda: events.append('start')):
                cfg.write_bytes(candidate)
                tool.start_trial(manifest)
                cfg.write_bytes(original)
                store.value.return_value = 'true'
                tool.start_trial(manifest, restoring=True)
            self.assertEqual(events, [(('false',), 'true'), 'start', (('false', 'true'), 'false'), 'start'])

    def test_failed_property_write_never_starts_android(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'cfg'
            cfg.write_bytes(BASE)
            store = Mock()
            store.value.return_value = 'true'
            store.set_value.side_effect = OSError('disk full')
            with patch.object(tool, 'store', return_value=store), patch.object(tool.engine.trial, 'CFG', cfg), \
                    patch.object(tool.engine.trial, 'protected'), patch.object(tool.engine.trial, 'start') as start:
                with self.assertRaises(OSError):
                    tool.start_trial({'extension': EXTENSION, 'before': tool.engine.trial.digest(BASE)}, restoring=True)
                start.assert_not_called()

    def test_foreign_config_refuses_property_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'cfg'
            cfg.write_bytes(b'foreign')
            store = Mock()
            store.value.return_value = 'true'
            with patch.object(tool, 'store', return_value=store), patch.object(tool.engine.trial, 'CFG', cfg), \
                    patch.object(tool.engine.trial, 'protected'), patch.object(tool.engine.trial, 'start') as start:
                with self.assertRaisesRegex(RuntimeError, 'Config'):
                    tool.start_trial({'extension': EXTENSION, 'before': tool.engine.trial.digest(BASE)}, restoring=True)
                store.set_value.assert_not_called()
                start.assert_not_called()

    def test_apply_failure_restores_saved_key_and_preserves_newer_unrelated_setting(self):
        for failure in ['property-write', 'start', 'native-check']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cfg, saved = root / 'cfg', root / 'persistent_properties'
                cfg.write_bytes(BASE)
                document = tool.properties.PropertyDocument()
                for name, value in [(tool.properties.KEY, 'false'), ('unrelated.setting', 'original')]:
                    item = document.properties.add()
                    item.name, item.value = name, value
                saved.write_bytes(document.SerializeToString())
                backup, hook, gpu = root / 'backup', root / 'hook', root / 'gpu'
                guest = root / 'guest'
                source = guest / 'vendor/etc/init/waydroid-init.rc'
                source.parent.mkdir(parents=True)
                source.write_bytes(tool.engine.BASE_INIT)
                stopped, starts = [False], []
                def require_stopped():
                    if not stopped[0]:
                        raise RuntimeError('running')
                protected = lambda p, directory=False: p.lstat()
                original_save = tool.engine.trial.save
                def save(path, data, mode):
                    if path == saved and failure == 'property-write' and tool.properties.parse(data)[1].value == 'true':
                        raise OSError('injected disk full')
                    original_save(path, data, mode)
                real_store = tool.properties.OfflineStore(saved, protected, require_stopped, save)
                def start():
                    restoring = cfg.read_bytes() == BASE
                    self.assertEqual(real_store.value(), 'false' if restoring else 'true')
                    starts.append(restoring)
                    stopped[0] = False
                    if not restoring:
                        newer = tool.properties.PropertyDocument.FromString(saved.read_bytes())
                        newer.properties[1].value = 'newer user setting'
                        saved.write_bytes(newer.SerializeToString())
                        if failure == 'start':
                            raise RuntimeError('injected start failure')
                def run(args, timeout):
                    values = {'ro.waydroid.software_rendering': '1', 'debug.stagefright.ccodec': '4',
                              tool.properties.KEY: 'false', 'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'}
                    return values.get(args[-1], 'GLES: ANGLE SwiftShader')
                with ExitStack() as stack:
                    for obj, name, value in [(tool.engine, 'BACKUP', backup), (tool.engine, 'ORIGINAL', backup / 'original.cfg'),
                        (tool.engine, 'INIT', hook), (tool.engine.trial, 'CFG', cfg),
                        (tool.engine.trial, 'GPU_OVERRIDE', gpu), (tool.engine.trial, 'DECODER_XML', root / 'absent')]:
                        stack.enter_context(patch.object(obj, name, value))
                    stack.enter_context(patch.object(tool, 'store', return_value=real_store))
                    stack.enter_context(patch.object(tool.engine, 'prepare_extension', return_value=EXTENSION))
                    stack.enter_context(patch.object(tool.engine.trial, 'protected', side_effect=protected))
                    stack.enter_context(patch.object(tool.engine.trial, 'service', return_value='active'))
                    stack.enter_context(patch.object(tool.engine.trial, 'preferences', return_value={}))
                    stack.enter_context(patch.object(tool.engine.trial, 'stop', side_effect=lambda: stopped.__setitem__(0, True)))
                    stack.enter_context(patch.object(tool.engine.trial, 'start', side_effect=start))
                    stack.enter_context(patch.object(tool.engine.trial, 'ready'))
                    stack.enter_context(patch.object(tool.engine.trial, 'run', side_effect=run))
                    stack.enter_context(patch.object(tool.engine, 'aac_unchanged'))
                    stack.enter_context(patch.object(tool.engine, 'codec_selection'))
                    stack.enter_context(patch.object(tool.engine, 'guest_identity', return_value=('200', guest)))
                    stack.enter_context(patch.object(tool.engine, 'verify_native', side_effect=RuntimeError('injected native failure')))
                    with self.assertRaisesRegex(RuntimeError, 'baseline restored'):
                        tool.engine.perform('--apply')
                self.assertEqual(cfg.read_bytes(), BASE)
                self.assertEqual(real_store.value(), 'false')
                self.assertEqual(tool.properties.PropertyDocument.FromString(saved.read_bytes()).properties[1].value,
                                 'original' if failure == 'property-write' else 'newer user setting')
                self.assertEqual(starts[-1], True)
                self.assertFalse(hook.exists())
                self.assertFalse(gpu.exists())


if __name__ == '__main__':
    unittest.main()
