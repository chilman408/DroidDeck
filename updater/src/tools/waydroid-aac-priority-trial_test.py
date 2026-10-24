# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from contextlib import ExitStack

spec = importlib.util.spec_from_file_location('aac_trial', Path(__file__).with_name('waydroid-aac-priority-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def codecs(aac):
    return {'version': 1, 'selected': [{'mime': mime, 'decoder': name} for mime, name in
            [('video/avc', 'c2.ffmpeg.h264.decoder'), ('video/hevc', 'c2.ffmpeg.hevc.decoder'),
             ('video/av01', 'c2.ffmpeg.av1.decoder'), ('video/x-vnd.on2.vp9', 'c2.ffmpeg.vp9.decoder'),
             ('audio/mp4a-latm', aac), ('audio/ac3', 'c2.ffmpeg.ac3.decoder'), ('audio/eac3', None)]],
            'decoders': [{'name': 'c2.android.aac.decoder', 'mime': 'audio/mp4a-latm',
                          'hardware': False, 'software': True, 'securePlaybackAdvertised': False,
                          'adaptivePlaybackAdvertised': False}]}


class AacPriorityTests(unittest.TestCase):
    def test_candidate_outprioritizes_zero_rank_ffmpeg_aac_without_changing_video(self):
        ranks = {'c2.ffmpeg.aac.decoder': 0, 'c2.android.aac.decoder': 512,
                 'c2.ffmpeg.h264.decoder': 0, 'c2.ffmpeg.hevc.decoder': 0}
        for codec in ET.fromstring(tool.CODE).findall('./Decoders/MediaCodec'):
            self.assertEqual(codec.get('type'), 'audio/mp4a-latm')
            self.assertEqual(codec.get('update'), 'true')
            self.assertIn(codec.get('name'), ranks)
            ranks[codec.get('name')] = int(codec.get('rank'))
        selected = min(['c2.ffmpeg.aac.decoder', 'c2.android.aac.decoder'], key=ranks.get)
        self.assertEqual(selected, 'c2.android.aac.decoder')
        self.assertEqual(ranks['c2.ffmpeg.h264.decoder'], 0)
        self.assertEqual(ranks['c2.ffmpeg.hevc.decoder'], 0)

    def test_truncated_status_cannot_block_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cfg, overlay, staging, backup = root / 'config', root / 'overlay', root / 'staging', root / 'backup'
            cfg.write_bytes(b'config')
            overlay.write_bytes(tool.CODE)
            backup.mkdir()
            manifest = {'profile': 'aac-priority', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config'), 'codecsBefore': codecs('c2.ffmpeg.aac.decoder')}
            (backup / 'manifest.json').write_text(json.dumps(manifest))
            (backup / 'status.json').write_bytes(b'{"phase":')
            calls = []
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', staging),
                    (tool, 'BACKUP', backup), (tool.trial, 'CFG', cfg), (tool.trial, 'BACKUPS', root),
                    (tool.trial, 'protected', lambda *a, **k: None),
                    (tool.trial, 'stop', lambda: calls.append('stop')),
                    (tool.trial, 'start', lambda: calls.append('start')),
                    (tool.trial, 'ready', lambda: calls.append('ready')),
                    (tool, 'read_codecs', lambda: codecs('c2.ffmpeg.aac.decoder'))]:
                stack.enter_context(patch.object(instance, name, value))
            stack.enter_context(patch.object(tool.os, 'getuid', lambda: 0, create=True))
            tool.main(['--rollback'])
            self.assertEqual(calls, ['stop', 'start', 'ready'])
            self.assertFalse(overlay.exists())
            saved = json.loads((backup / 'status.json').read_text())
            self.assertEqual(saved['phase'], 'rolled-back')
            self.assertEqual(saved['failedStage'], 'unavailable')

    def test_status_io_failure_does_not_interrupt_baseline_recovery(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cfg, overlay, staging = root / 'config', root / 'overlay', root / 'staging'
            cfg.write_bytes(b'config')
            overlay.write_bytes(tool.CODE)
            manifest = {'profile': 'aac-priority', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config'), 'codecsBefore': codecs('c2.ffmpeg.aac.decoder')}
            calls = []
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', staging),
                    (tool.trial, 'CFG', cfg), (tool.trial, 'protected', lambda *a, **k: None),
                    (tool.trial, 'stop', lambda: calls.append('stop')),
                    (tool.trial, 'start', lambda: calls.append('start')),
                    (tool.trial, 'ready', lambda: calls.append('ready')),
                    (tool, 'read_codecs', lambda: codecs('c2.ffmpeg.aac.decoder'))]:
                stack.enter_context(patch.object(instance, name, value))
            stack.enter_context(patch.object(tool, 'status', side_effect=OSError('private filesystem detail')))
            with self.assertRaisesRegex(RuntimeError, 'baseline restored; recovery status could not be saved'):
                tool.recover(manifest, failed_stage='starting')
            self.assertEqual(calls, ['stop', 'start', 'ready'])
            self.assertFalse(overlay.exists())
            self.assertEqual(cfg.read_bytes(), b'config')

    def test_explicit_rollback_failure_persists_the_failed_recovery_stage(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cfg, overlay, staging, backup = root / 'config', root / 'overlay', root / 'staging', root / 'backup'
            cfg.write_bytes(b'config')
            backup.mkdir()
            manifest = {'profile': 'aac-priority', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config'), 'codecsBefore': codecs('c2.ffmpeg.aac.decoder')}
            (backup / 'manifest.json').write_text(json.dumps(manifest))
            (backup / 'status.json').write_text(json.dumps({'profile': 'aac-priority',
                'phase': 'recovery-required', 'failedStage': 'starting'}))
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', staging),
                    (tool, 'BACKUP', backup), (tool.trial, 'CFG', cfg), (tool.trial, 'BACKUPS', root),
                    (tool.trial, 'protected', lambda *a, **k: None),
                    (tool.trial, 'stop', lambda: None), (tool.trial, 'start', lambda: None)]:
                stack.enter_context(patch.object(instance, name, value))
            stack.enter_context(patch.object(tool.os, 'getuid', lambda: 0, create=True))
            stack.enter_context(patch.object(tool.trial, 'ready', side_effect=RuntimeError('private command output')))
            with self.assertRaises(RuntimeError):
                tool.main(['--rollback'])
            saved = json.loads((backup / 'status.json').read_text())
            self.assertEqual(saved['phase'], 'recovery-required')
            self.assertEqual(saved['recoveryStage'], 'waiting-h264')
            self.assertEqual(saved['failedStage'], 'starting')
            self.assertNotIn('private command output', (backup / 'status.json').read_text())
            with patch.object(tool.trial, 'ready', lambda: None), \
                    patch.object(tool, 'read_codecs', lambda: codecs('c2.ffmpeg.aac.decoder')):
                tool.main(['--rollback'])
            saved = json.loads((backup / 'status.json').read_text())
            self.assertEqual(saved['phase'], 'rolled-back')
            self.assertEqual(saved['failedStage'], 'starting')

    def test_status_retains_failed_phase_but_rejects_raw_error_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            def save(path, data, mode):
                path.write_bytes(data)
            with patch.object(tool, 'BACKUP', backup), patch.object(tool.trial, 'save', save):
                tool.status('recovery-required', failed_stage='starting', recovery_stage='waiting-h264')
                data = json.loads((backup / 'status.json').read_text())
                self.assertEqual(data['failedStage'], 'starting')
                self.assertEqual(data['recoveryStage'], 'waiting-h264')
                before = (backup / 'status.json').read_bytes()
                with self.assertRaises(ValueError):
                    tool.status('recovery-required', failed_stage='raw token or account data')
                self.assertEqual((backup / 'status.json').read_bytes(), before)

    def test_candidate_updates_only_existing_aac_rank(self):
        tool.validate_baseline(codecs('c2.ffmpeg.aac.decoder'))
        root = ET.fromstring(tool.CODE)
        nodes = root.findall('./Decoders/MediaCodec')
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0].attrib, {'name': 'c2.android.aac.decoder', 'type': 'audio/mp4a-latm',
                                        'update': 'true', 'rank': '1'})
        self.assertEqual(nodes[1].attrib, {'name': 'c2.ffmpeg.aac.decoder', 'type': 'audio/mp4a-latm',
                                        'update': 'true', 'rank': '512'})
        self.assertEqual(len(list(nodes[0])), 0)
        self.assertNotIn(b'video/', tool.CODE)
        self.assertNotIn(b'secure-playback', tool.CODE)

    def test_actual_api_must_report_one_existing_software_stock_aac(self):
        for field, value in [('mime', 'audio/ac3'), ('software', False), ('securePlaybackAdvertised', True)]:
            snapshot = codecs('c2.ffmpeg.aac.decoder')
            snapshot['decoders'][0][field] = value
            with self.assertRaises(RuntimeError):
                tool.validate_baseline(snapshot)
        snapshot = codecs('c2.ffmpeg.aac.decoder')
        snapshot['decoders'] *= 2
        with self.assertRaises(RuntimeError):
            tool.validate_baseline(snapshot)
        with self.assertRaises(RuntimeError):
            tool.validate_baseline(codecs('c2.android.aac.decoder'))

    def test_actual_selection_must_change_only_aac(self):
        before, after = codecs('c2.ffmpeg.aac.decoder'), codecs('c2.android.aac.decoder')
        tool.verify_selection(before, after)
        after['selected'][0]['decoder'] = 'OMX.ffmpeg.h264.decoder'
        with self.assertRaisesRegex(RuntimeError, 'Non-AAC'):
            tool.verify_selection(before, after)

    def test_rank_xml_is_not_proof_of_selected_decoder_or_unchanged_capabilities(self):
        before = codecs('c2.ffmpeg.aac.decoder')
        with self.assertRaisesRegex(RuntimeError, 'AAC selection'):
            tool.verify_selection(before, before)
        after = codecs('c2.android.aac.decoder')
        after['decoders'][0]['securePlaybackAdvertised'] = True
        with self.assertRaisesRegex(RuntimeError, 'capabilities'):
            tool.verify_selection(before, after)
        after = codecs('c2.android.aac.decoder')
        after['selected'].append(after['selected'][0])
        with self.assertRaises(RuntimeError):
            tool.verify_selection(before, after)

    def test_rollback_refuses_changed_config_or_foreign_overlay_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, overlay, staging = root / 'config', root / 'overlay', root / 'staging'
            cfg.write_bytes(b'config')
            overlay.write_bytes(b'foreign')
            manifest = {'profile': 'aac-priority', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config')}
            with patch.object(tool.trial, 'CFG', cfg), patch.object(tool, 'OVERLAY', overlay), \
                    patch.object(tool, 'TEMP', staging), patch.object(tool.trial, 'protected'), \
                    patch.object(tool.trial, 'stop') as stop:
                with self.assertRaisesRegex(RuntimeError, 'changed'):
                    tool.revert(manifest)
                overlay.write_bytes(tool.CODE)
                cfg.write_bytes(b'new config')
                with self.assertRaisesRegex(RuntimeError, 'Config changed'):
                    tool.revert(manifest)
                stop.assert_not_called()

    def test_rollback_removes_only_owned_files_and_never_restores_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg, overlay, staging = root / 'config', root / 'overlay', root / 'staging'
            cfg.write_bytes(b'config')
            overlay.write_bytes(tool.CODE)
            staging.write_bytes(tool.CODE)
            manifest = {'profile': 'aac-priority', 'candidate': tool.trial.digest(tool.CODE),
                        'config': tool.trial.digest(b'config')}
            calls = []
            with patch.object(tool.trial, 'CFG', cfg), patch.object(tool, 'OVERLAY', overlay), \
                    patch.object(tool, 'TEMP', staging), patch.object(tool.trial, 'protected'), \
                    patch.object(tool.trial, 'stop', lambda: calls.append('stop')), \
                    patch.object(tool.trial, 'start', lambda: calls.append('start')), \
                    patch.object(tool.trial, 'ready', lambda: calls.append('ready')):
                tool.revert(manifest)
            self.assertEqual(calls, ['stop', 'start', 'ready'])
            self.assertFalse(overlay.exists())
            self.assertFalse(staging.exists())
            self.assertEqual(cfg.read_bytes(), b'config')

    def test_ineffective_rank_is_automatically_removed_with_persistent_recovery_phase(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            cfg, overlay, staging, backup = root / 'config', root / 'vendor/etc/media_codecs_c2.xml', root / 'staging', root / 'backup'
            original = b'[waydroid]\n[properties]\nro.waydroid.software_rendering=1\n'
            cfg.write_bytes(original)
            main_xml = root / 'vendor/etc/media_codecs.xml'
            main_xml.parent.mkdir(parents=True)
            main_xml.write_bytes(b'<MediaCodecs><Include href="media_codecs_ffmpeg_c2.xml"/>'
                                 b'<Include href="media_codecs_c2.xml"/></MediaCodecs>')
            calls = []
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', staging), (tool, 'BACKUP', backup),
                    (tool.trial, 'CFG', cfg), (tool.trial, 'BACKUPS', root),
                    (tool.trial, 'GPU_OVERRIDE', root / 'no-gpu'), (tool.trial, 'DECODER_XML', root / 'no-video'),
                    (tool, 'guest_path', lambda: ('1234', root)), (tool, 'read_codecs', lambda: codecs('c2.ffmpeg.aac.decoder')),
                    (tool.trial, 'protected', lambda *a, **k: None),
                    (tool.trial, 'service', lambda *a: 'active'), (tool.trial, 'preferences', lambda: {'display': 'current'}),
                    (tool.trial, 'stop', lambda: calls.append('stop')), (tool.trial, 'start', lambda: calls.append('start')),
                    (tool.trial, 'ready', lambda: calls.append('ready'))]:
                stack.enter_context(patch.object(instance, name, value))
            stack.enter_context(patch.object(tool.os, 'getuid', lambda: 0, create=True))
            with self.assertRaisesRegex(RuntimeError, 'original decoder selection and H264 restored'):
                tool.main(['--apply'])
            saved = json.loads((backup / 'status.json').read_text())
            self.assertEqual(saved['phase'], 'rolled-back')
            self.assertEqual(saved['failedStage'], 'verifying-codecs')
            observed = json.loads((backup / 'codecs-after.json').read_text())
            self.assertEqual(tool.selection(observed)['audio/mp4a-latm'], 'c2.ffmpeg.aac.decoder')
            self.assertEqual(calls, ['stop', 'start', 'ready', 'stop', 'start', 'ready'])
            self.assertFalse(overlay.exists())
            self.assertFalse(staging.exists())
            self.assertEqual(cfg.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
