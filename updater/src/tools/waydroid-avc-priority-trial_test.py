# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('avc_trial', Path(__file__).with_name('waydroid-avc-priority-trial.py'))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def codecs(avc='c2.ffmpeg.h264.decoder'):
    return {'version': 1, 'selected': [{'mime': mime, 'decoder': name} for mime, name in
        [('video/avc', avc), ('video/hevc', 'c2.ffmpeg.hevc.decoder'), ('video/av01', 'c2.ffmpeg.av1.decoder'),
         ('video/x-vnd.on2.vp9', 'c2.ffmpeg.vp9.decoder'), ('audio/mp4a-latm', 'c2.android.aac.decoder'),
         ('audio/ac3', 'c2.ffmpeg.ac3.decoder'), ('audio/eac3', None)]],
        'decoders': ([{'name': 'c2.android.avc.decoder', 'mime': 'video/avc', 'software': True, 'hardware': False,
                      'securePlaybackAdvertised': False, 'adaptivePlaybackAdvertised': True}]
                     if avc == 'c2.android.avc.decoder' else [])}


class AvcPriorityTests(unittest.TestCase):
    def test_framework_allocator_exclusion_is_a_read_only_preflight_gate(self):
        for force in ['', '0', 'false']: tool.validate_allocator('minigbm', force)
        for gralloc, force in [('default', ''), ('default', '0'), ('minigbm', '1'), ('minigbm', 'true')]:
            with self.assertRaisesRegex(RuntimeError, 'policy excludes stock video'): tool.validate_allocator(gralloc, force)

    def test_include_parser_accepts_comment_examples_but_rejects_real_dtds(self):
        source = b'<MediaCodecs><!-- <!DOCTYPE MediaCodecs [<!ENTITY sample "x">]> --><Include href="media_codecs_c2.xml"/></MediaCodecs>'
        self.assertEqual(tool.parse_include(source).tag, 'MediaCodecs')
        for source in [b'<!DOCTYPE MediaCodecs><MediaCodecs/>',
                b'<!DOCTYPE MediaCodecs [<!ENTITY a "x">]><MediaCodecs>&a;</MediaCodecs>',
                b'x' * 65537]:
            with self.assertRaises(ValueError): tool.parse_include(source)

    def test_candidate_preserves_accepted_aac_and_only_updates_existing_avc_ranks(self):
        before = ET.fromstring(tool.BASELINE).findall('./Decoders/MediaCodec')
        after = ET.fromstring(tool.CODE).findall('./Decoders/MediaCodec')
        self.assertEqual([e.attrib for e in after[:2]], [e.attrib for e in before])
        self.assertEqual([e.attrib for e in after[2:]], [
            {'name': 'c2.android.avc.decoder', 'type': 'video/avc', 'rank': '1'},
            {'name': 'c2.ffmpeg.h264.decoder', 'type': 'video/avc', 'update': 'true', 'rank': '512'}])

    def test_verification_refuses_changed_aac_other_codecs_or_capabilities(self):
        before, after = codecs(), codecs('c2.android.avc.decoder')
        tool.verify_selection(before, after)
        after['selected'][4]['decoder'] = 'c2.ffmpeg.aac.decoder'
        with self.assertRaisesRegex(RuntimeError, 'Non-AVC'): tool.verify_selection(before, after)
        after = codecs('c2.android.avc.decoder')
        after['decoders'][0]['securePlaybackAdvertised'] = True
        with self.assertRaisesRegex(RuntimeError, 'capabilities'): tool.verify_selection(before, after)
        with self.assertRaisesRegex(RuntimeError, 'selection'): tool.verify_selection(before, before)

    def test_preflight_requires_missing_avc_declaration_and_accepted_aac(self):
        tool.validate_baseline(codecs())
        baseline = codecs(); baseline['decoders'] = codecs('c2.android.avc.decoder')['decoders']
        with self.assertRaisesRegex(RuntimeError, 'Stock AVC'): tool.validate_baseline(baseline)
        baseline = codecs(); baseline['selected'][4]['decoder'] = 'c2.ffmpeg.aac.decoder'
        with self.assertRaisesRegex(RuntimeError, 'AAC'): tool.validate_baseline(baseline)

    def test_stock_declaration_requires_exact_existing_limits_alias_and_features(self):
        document = ET.Element('Included'); decoders = ET.SubElement(document, 'Decoders')
        stock = ET.fromstring(ET.tostring(tool.STOCK)); decoders.append(stock)
        tool.validate_stock_source(ET.tostring(document))
        stock.find('Feature').set('name', 'secure-playback')
        with self.assertRaisesRegex(RuntimeError, 'exact installed'): tool.validate_stock_source(ET.tostring(document))
        stock.find('Feature').set('name', 'adaptive-playback'); stock.find('Limit').set('max', '8192x8192')
        with self.assertRaisesRegex(RuntimeError, 'exact installed'): tool.validate_stock_source(ET.tostring(document))

    def test_recovery_restores_exact_aac_overlay_even_when_status_write_fails(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); cfg, overlay, temp = root/'config', root/'overlay', root/'temp'
            cfg.write_bytes(b'config'); overlay.write_bytes(tool.CODE)
            (root/'vendor/etc').mkdir(parents=True)
            (root/'vendor/etc/media_codecs_c2.xml').write_bytes(tool.BASELINE)
            calls = []
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', temp),
                (tool.trial, 'CFG', cfg), (tool.trial, 'protected', lambda *a, **k: None),
                (tool.trial, 'stop', lambda: calls.append('stop')),
                (tool.trial, 'start', lambda: calls.append('start')),
                (tool.trial, 'ready', lambda: calls.append('ready')),
                (tool, 'read_codecs', lambda: codecs()),
                (tool.aac, 'guest_path', lambda: ('1234', root)),
                (tool, 'status', lambda *a, **k: (_ for _ in ()).throw(OSError('private')))]:
                stack.enter_context(patch.object(instance, name, value))
            with self.assertRaisesRegex(RuntimeError, 'baseline restored; status unavailable'):
                tool.recover(tool.manifest(codecs(), {}), 'verifying-codecs')
            self.assertEqual(overlay.read_bytes(), tool.BASELINE)
            self.assertEqual(calls, ['stop', 'start', 'ready'])

    def test_recovery_refuses_foreign_overlay_or_changed_config_before_stopping(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); cfg, overlay, temp = root/'config', root/'overlay', root/'temp'
            cfg.write_bytes(b'config'); overlay.write_bytes(tool.CODE)
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', temp),
                (tool.trial, 'CFG', cfg), (tool.trial, 'protected', lambda *a, **k: None)]:
                stack.enter_context(patch.object(instance, name, value))
            saved = tool.manifest(codecs(), {})
            with patch.object(tool.trial, 'stop') as stop:
                overlay.write_bytes(b'foreign')
                with self.assertRaisesRegex(RuntimeError, 'overlay changed'): tool.revert(saved)
                overlay.write_bytes(tool.CODE); cfg.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Config changed'): tool.revert(saved)
                stop.assert_not_called()

    def test_recovery_requires_verified_original_decoder_selection(self):
        with patch.object(tool, 'revert'), patch.object(tool, 'status') as status, \
                patch.object(tool, 'read_codecs', return_value=codecs('c2.android.avc.decoder')):
            saved = {'codecsBefore': codecs()}
            with self.assertRaisesRegex(RuntimeError, 'recovery requires inspection'): tool.recover(saved, 'starting')
            self.assertEqual(status.call_args.args[0], 'recovery-required')

    def test_ineffective_apply_saves_observed_codecs_and_recovers_accepted_aac(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory); cfg, overlay, temp, backup = root/'config', root/'overlay', root/'temp', root/'backup'
            cfg.write_bytes(b'config'); overlay.write_bytes(tool.BASELINE)
            (root/'vendor/etc').mkdir(parents=True)
            (root/'vendor/etc/media_codecs_c2.xml').write_bytes(tool.CODE)
            calls = []
            def revert(saved):
                calls.append('recover'); overlay.write_bytes(tool.BASELINE)
            for instance, name, value in [(tool, 'OVERLAY', overlay), (tool, 'TEMP', temp), (tool, 'BACKUP', backup),
                (tool.trial, 'CFG', cfg), (tool.trial, 'BACKUPS', root),
                (tool.trial, 'protected', lambda *a, **k: None), (tool.trial, 'preferences', lambda: {}),
                (tool.trial, 'stop', lambda: calls.append('stop')), (tool.trial, 'start', lambda: calls.append('start')),
                (tool.trial, 'ready', lambda: calls.append('ready')), (tool, 'preflight', lambda: codecs()),
                (tool, 'read_codecs', lambda: codecs()), (tool.aac, 'guest_path', lambda: ('1234', root)),
                (tool, 'revert', revert)]:
                stack.enter_context(patch.object(instance, name, value))
            stack.enter_context(patch.object(tool.os, 'getuid', lambda: 0, create=True))
            with self.assertRaisesRegex(RuntimeError, 'accepted AAC baseline restored'): tool.main(['--apply'])
            self.assertEqual(overlay.read_bytes(), tool.BASELINE)
            self.assertEqual(json.loads((backup/'codecs-after.json').read_text()), codecs())
            saved = json.loads((backup/'status.json').read_text())
            self.assertEqual((saved['phase'], saved['stage']), ('rolled-back', 'verifying-codecs'))
            self.assertEqual(calls, ['stop', 'start', 'ready', 'recover'])


if __name__ == '__main__': unittest.main()
