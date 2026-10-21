# SPDX-License-Identifier: GPL-3.0-only
import configparser
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('trial', Path(__file__).with_name('waydroid-render-trial.py'))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class InterruptedWriter:
    def __init__(self, stream):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()

    def write(self, data):
        self.stream.write(data[:max(1, len(data) // 2)])
        self.stream.flush()
        raise OSError('injected disk full')

    def __getattr__(self, name):
        return getattr(self.stream, name)


def interrupted_open(real_open):
    def open_file(*args, **kwargs):
        stream = real_open(*args, **kwargs)
        mode = args[1] if len(args) > 1 else kwargs.get('mode', 'r')
        return InterruptedWriter(stream) if mode == 'wb' else stream
    return open_file


class ConfigTest(unittest.TestCase):
    def test_failed_sync_or_replace_preserves_baseline_and_cleans_staging(self):
        for operation in ['fsync', 'replace']:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config'
                path.write_bytes(b'accepted baseline')
                with patch.object(trial, 'protected'), patch.object(trial.os, operation, side_effect=OSError('injected failure')):
                    with self.assertRaises(OSError):
                        trial.save(path, b'candidate', 0o600)
                self.assertEqual(path.read_bytes(), b'accepted baseline')
                self.assertEqual([p.name for p in path.parent.iterdir()], ['config'])

    def test_save_installs_complete_contents_and_requested_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config'
            path.write_bytes(b'previous longer contents')
            with patch.object(trial, 'protected'):
                trial.save(path, b'candidate', 0o600)
            self.assertEqual(path.read_bytes(), b'candidate')
            self.assertEqual([p.name for p in path.parent.iterdir()], ['config'])
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_interrupted_save_preserves_previous_file_or_absence(self):
        for present in [True, False]:
            with self.subTest(present=present), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config'
                if present:
                    path.write_bytes(b'accepted baseline')
                with patch.object(trial, 'protected'), patch.object(io, 'open', side_effect=interrupted_open(io.open)):
                    with self.assertRaises(OSError):
                        trial.save(path, b'new candidate contents', 0o644)
                self.assertEqual(path.read_bytes() if path.exists() else None,
                                 b'accepted baseline' if present else None)
                self.assertEqual(sorted(p.name for p in path.parent.iterdir()), ['config'] if present else [])

    def test_offline_restart_refreshes_manager_before_starting_android(self):
        commands = []
        with patch.object(trial, 'run', side_effect=lambda args, *extra: commands.append(args) or ''):
            trial.start()
        refresh = ['/usr/bin/systemctl', 'restart', 'waydroid-container.service']
        self.assertIn(refresh, commands, 'Exhausted manager survived offline restart')
        self.assertLess(commands.index(refresh), commands.index(['/usr/bin/systemctl', 'start', trial.SESSION]))
        self.assertGreater(commands.index(refresh), commands.index(
            ['/usr/bin/node', trial.ROOT + '/runtime/waydroid-display/pid-compat.js']))

    def test_manager_restart_failure_does_not_launch_session_or_stream(self):
        commands = []
        def run(args, *extra):
            commands.append(args)
            if args == ['/usr/bin/systemctl', 'restart', 'waydroid-container.service']:
                raise RuntimeError('manager unavailable')
            return ''
        with patch.object(trial, 'run', side_effect=run):
            with self.assertRaises(RuntimeError):
                trial.start()
        self.assertNotIn(['/usr/bin/systemctl', 'start', trial.SESSION], commands)
        self.assertNotIn(['/usr/bin/systemctl', 'start', trial.RUNTIME], commands)

    def test_readiness_allows_observed_slow_recovery_but_remains_bounded(self):
        for ready_at in [200, None]:
            elapsed = [0]
            def run(args, timeout):
                if args[0] == '/usr/bin/lxc-info':
                    return '200'
                if ready_at is not None and elapsed[0] >= ready_at:
                    return 'READY'
                raise RuntimeError('Runtime worker not ready')
            with patch.object(trial, 'run', side_effect=run), \
                    patch.object(trial.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                    patch.object(trial.time, 'sleep', side_effect=lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds)):
                failure = None
                try:
                    trial.ready()
                except RuntimeError as error:
                    failure = str(error)
            if ready_at is not None:
                self.assertIsNone(failure, 'Known healthy 200-second startup was rejected')
                self.assertGreaterEqual(elapsed[0], 200)
            else:
                self.assertEqual(failure, 'H264 recovery deadline exceeded')
            self.assertLessEqual(elapsed[0], 300, 'Unhealthy startup exceeded the recovery budget')

    def parse(self, data):
        result = configparser.ConfigParser(interpolation=None)
        result.read_string(data.decode())
        return result

    def test_decoder_trial_preserves_all_other_values(self):
        original = b'[waydroid]\narch=x86_64\n[properties]\nro.waydroid.software_rendering=1\npersist.waydroid.width=980\nurl=https://example/%20\n'
        before = self.parse(original)
        after = self.parse(trial.candidate(original, 'codec2'))
        self.assertEqual(after['properties'].pop('debug.stagefright.ccodec'), '4')
        self.assertEqual(dict(before['properties']), dict(after['properties']))
        self.assertEqual(dict(before['waydroid']), dict(after['waydroid']))

    def test_gpu_trial_only_changes_rendering_not_decoder_or_preferences(self):
        after = self.parse(trial.candidate(b'[waydroid]\narch=x86_64\n[properties]\ndebug.stagefright.ccodec=4\n', 'gpu'))
        self.assertEqual(after['properties']['debug.stagefright.ccodec'], '4')
        self.assertEqual(after['properties']['ro.waydroid.software_rendering'], '0')
        self.assertEqual(after['properties']['persist.waydroid.use_subsurface'], 'true')
        self.assertNotIn('--renderer=pixman', trial.GPU_CODE.decode())
        self.assertIn('--backend=pipewire', trial.GPU_CODE.decode())

    def test_invalid_config_or_profile_refused(self):
        for data, profile in [(b'[properties]\nx=1', 'gpu'), (b'x=1', 'codec2'), (b'x' * 65537, 'gpu'), (b'', 'arbitrary')]:
            with self.assertRaises(ValueError):
                trial.candidate(data, profile)

    def test_offline_regeneration_reinstalls_pid_hook_before_start(self):
        commands = []
        with patch.object(trial, 'run', side_effect=lambda args, *extra: commands.append(args) or ''):
            trial.start()
        self.assertEqual(commands[0], ['/usr/bin/waydroid', 'upgrade', '-o'])
        self.assertEqual(commands[1], ['/usr/bin/node', trial.ROOT + '/runtime/waydroid-display/pid-compat.js'])
        self.assertEqual(commands[-1], ['/usr/bin/systemctl', 'start', trial.RUNTIME])

    def test_decoder_overlay_only_removes_two_adaptive_declarations(self):
        original = b'''<MediaCodecs><Decoders><!-- preserve -->
        <MediaCodec name="c2.ffmpeg.h264.decoder" type="video/avc"><Alias name="OMX.ffmpeg.h264.decoder"/><Limit name="size" max="4096x4096"/><Feature name="adaptive-playback"/><Feature name="secure-playback"/></MediaCodec>
        <MediaCodec name="c2.ffmpeg.hevc.decoder" type="video/hevc"><Feature name="adaptive-playback"/><Feature name="low-latency"/></MediaCodec>
        <MediaCodec name="c2.ffmpeg.vp9.decoder" type="video/x-vnd.on2.vp9"><Feature name="adaptive-playback"/></MediaCodec>
        </Decoders></MediaCodecs>'''
        changed = trial.decoder_candidate(original)
        expected = ET.fromstring(original)
        for name in trial.DECODER_NAMES:
            codec = expected.find("./Decoders/MediaCodec[@name='" + name + "']")
            codec.remove(codec.find("Feature[@name='adaptive-playback']"))
        self.assertEqual(ET.tostring(ET.fromstring(changed)), ET.tostring(expected))
        self.assertIn(b'<!-- preserve -->', changed)

    def test_decoder_overlay_rejects_missing_duplicate_or_unexpected_features(self):
        for data in [b'<!DOCTYPE MediaCodecs><MediaCodecs/>', b'<!ENTITY x "y"><MediaCodecs/>',
                     b'<Included/>', b'<MediaCodecs/>', b'x' * 65537, b'<broken>',
                     b'<MediaCodecs><Decoders><MediaCodec name="c2.ffmpeg.h264.decoder"><Feature name="adaptive-playback" required="true"/></MediaCodec></Decoders></MediaCodecs>']:
            with self.assertRaises(ValueError):
                trial.decoder_candidate(data)

    def test_decoder_rollback_only_removes_its_exact_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'waydroid.cfg'
            config.write_bytes(b'unchanged config')
            overlay = root / 'vendor/etc/media_codecs_ffmpeg_c2.xml'
            overlay.parent.mkdir(parents=True)
            overlay.write_bytes(b'candidate')
            other = overlay.parent / 'unrelated.xml'
            other.write_bytes(b'preserve')
            backup = root / 'backups/decoder-reuse-20261010'
            backup.mkdir(parents=True)
            manifest = {'profile': 'decoder-reuse', 'config': trial.digest(config.read_bytes()),
                        'candidate': trial.digest(overlay.read_bytes())}
            (backup / 'manifest.json').write_text(json.dumps(manifest))
            calls = []
            with patch.object(trial, 'CFG', config), patch.object(trial, 'DECODER_XML', overlay), \
                 patch.object(trial, 'DECODER_TEMP', overlay.with_name('.candidate.tmp')), \
                 patch.object(trial, 'BACKUPS', backup.parent), patch.object(trial, 'protected'), \
                 patch.object(trial, 'stop', side_effect=lambda: calls.append('stop')), \
                 patch.object(trial, 'start', side_effect=lambda: calls.append('start')), \
                 patch.object(trial, 'ready', side_effect=lambda: calls.append('ready')):
                trial.decoder_reuse_trial(True)
                self.assertFalse(overlay.exists())
                self.assertEqual(other.read_bytes(), b'preserve')
                self.assertEqual(config.read_bytes(), b'unchanged config')
                self.assertEqual(calls, ['stop', 'start', 'ready'])
                overlay.write_bytes(b'changed by another writer')
                with self.assertRaisesRegex(RuntimeError, 'overlay changed'):
                    trial.decoder_reuse_trial(True)
                config.write_bytes(b'changed config')
                with self.assertRaisesRegex(RuntimeError, 'Config changed'):
                    trial.decoder_reuse_trial(True)
                self.assertEqual(calls, ['stop', 'start', 'ready'])


if __name__ == '__main__':
    unittest.main()
