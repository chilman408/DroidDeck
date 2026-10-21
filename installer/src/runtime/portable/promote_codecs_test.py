# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda b: hashlib.sha256(b).hexdigest()
MAIN = b'<MediaCodecs><Include href="media_codecs_ffmpeg_c2.xml"/><Include href="media_codecs_c2.xml"/></MediaCodecs>'
INIT = b'on post-fs-data\n    exec -- /vendor/bin/waydroid-init\n'
DECODERS = {'aac': 'audio/mp4a-latm', 'h264': 'video/avc', 'hevc': 'video/hevc', 'av1': 'video/av01', 'vp9': 'video/x-vnd.on2.vp9'}
FFMPEG = ('<MediaCodecs><Decoders>' + ''.join('<MediaCodec name="c2.ffmpeg.' + n + '.decoder" type="' + mime + '"/>' for n, mime in DECODERS.items()) + '</Decoders></MediaCodecs>').encode()


class CodecPolicyTests(unittest.TestCase):
    def test_cli_fixed_paths_no_source_or_policy_override(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_install_codecs') as install:
            P.main(['--codecs'])
            self.assertEqual(install.call_args.args[1:], (Path('/var/lib/waydroid/overlay/vendor'), Path('/var/lib/waydroid/waydroid.cfg')))
            with self.assertRaises(ValueError):
                P.main(['--codecs', '/override'])

    def test_generated_overlay_preserves_accepted_bytes_and_aac_only_ranking(self):
        self.assertEqual(sha(P.CODEC_FILES['media_codecs_c2.xml']), '495bd954d845b80f615ed57f61654d2ba4be8c253d9a38a55b537a3c99824924')
        codecs = ET.fromstring(P.CODEC_FILES['media_codecs_c2.xml']).findall('./Decoders/MediaCodec')
        self.assertEqual([c.attrib for c in codecs], [dict(name='c2.android.aac.decoder', type='audio/mp4a-latm', update='true', rank='1'), dict(name='c2.ffmpeg.aac.decoder', type='audio/mp4a-latm', update='true', rank='512')])
        self.assertEqual(P.CODEC_FILES['init/waydroid-init.rc'], INIT + b'    setprop media.sf.hwaccel 0\n    setprop debug.ffmpeg-codec2.hwaccel.drm 0\n    setprop debug.ffmpeg-codec2.hwaccel.force 0\n    setprop debug.ffmpeg-codec2.pixel_format RGBX_8888\n')

    def test_publisher_metadata_validates_include_order_decoder_identity_and_init(self):
        P.validate_codec_vendor(MAIN, FFMPEG, INIT)
        for args in ((MAIN.replace(b'media_codecs_c2.xml', b'other.xml'), FFMPEG, INIT),
                     (MAIN, FFMPEG.replace(b'video/avc', b'video/other'), INIT),
                     (MAIN, FFMPEG, INIT + b'changed'), (b'x' * 65537, FFMPEG, INIT),
                     (MAIN, b'<invalid', INIT),
                     (MAIN, FFMPEG.replace(b'</Decoders>', b'<MediaCodec type="audio/eac3"/></Decoders>'), INIT)):
            with self.assertRaises(ValueError):
                P.validate_codec_vendor(*args)

    def test_guard_only_observes_four_inactive_units(self):
        with mock.patch.object(P.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'inactive\n')) as run:
            P._assert_codec_stopped()
            self.assertEqual(run.call_count, 4)
            self.assertTrue(all(c.args[0][:2] == ['/usr/bin/systemctl', 'show'] for c in run.call_args_list))
        with mock.patch.object(P.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'active\n')):
            with self.assertRaises(ValueError):
                P._assert_codec_stopped()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class CodecInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-codecs-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vendor = self.root / 'vendor'
        self.vendor.mkdir(mode=0o755)
        self.cfg = self.root / 'waydroid.cfg'
        self.cfg.write_bytes(b'generated metadata only')
        self.cfg.chmod(0o600)
        self.pin = sha(self.cfg.read_bytes())

    def install(self, **kw):
        return P._install_codecs(self.copier, self.vendor, self.cfg, config_pin=self.pin,
                                 stopped=kw.get('stopped', lambda: None), verify_vendor=kw.get('verify_vendor', lambda _: None))

    def test_new_atomic_tree_exact_bytes_modes_receipt_no_activation_and_no_replay(self):
        r = self.install()
        self.assertFalse(r['configurationChanged'])
        self.assertFalse(r['androidOperationsExecuted'])
        self.assertFalse(r['readyForActivation'])
        final = self.vendor / 'etc'
        self.assertEqual({p.relative_to(final).as_posix() for p in final.rglob('*')}, {'init', 'init/waydroid-init.rc', 'media_codecs_c2.xml', 'droiddeck-codec-receipt.json'})
        for p in [final] + list(final.rglob('*')):
            s = p.lstat()
            self.assertEqual((s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode)), (0, 0, 0o755 if p.is_dir() else 0o644))
            if p.is_file():
                self.assertEqual(s.st_nlink, 1)
        for n, b in P.CODEC_FILES.items():
            self.assertEqual((final / n).read_bytes(), b)
        self.assertEqual(json.loads((final / 'droiddeck-codec-receipt.json').read_bytes()), r)
        self.assertEqual(sha(self.cfg.read_bytes()), self.pin)
        with self.assertRaises(ValueError):
            self.install()

    def test_existing_vendor_content_and_config_changes_refuse_before_output(self):
        (self.vendor / 'sentinel').write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((self.vendor / 'sentinel').read_bytes(), b'preserve')
        (self.vendor / 'sentinel').unlink()
        self.cfg.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(self.vendor.iterdir()), [])

    def test_vendor_validation_and_active_guard_refuse_before_output(self):
        def fail(*_):
            raise ValueError('generated refusal')
        for kwargs in ({'verify_vendor': fail}, {'stopped': fail}):
            with self.assertRaises(ValueError):
                self.install(**kwargs)
            self.assertEqual(list(self.vendor.iterdir()), [])

    def test_linked_config_refuses_before_output(self):
        real = self.root / 'real.cfg'
        self.cfg.rename(real)
        self.cfg.symlink_to(real)
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(list(self.vendor.iterdir()), [])

    def test_raced_final_directory_never_replaced(self):
        rename = P.rename_exclusive
        def race(directory, old, new):
            (self.vendor / 'etc').mkdir()
            (self.vendor / 'etc/sentinel').write_bytes(b'preserve')
            return rename(directory, old, new)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.install()
        self.assertEqual((self.vendor / 'etc/sentinel').read_bytes(), b'preserve')

    def test_midstage_mutation_and_extra_output_refuse_publication(self):
        calls = 0
        def mutate():
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.vendor / 'pending-droiddeck-codecs/media_codecs_c2.xml').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install(stopped=mutate)
        self.assertFalse((self.vendor / 'etc').exists())

    def test_changed_cfg_at_last_guard_refuses_publication(self):
        calls = 0
        def mutate():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.cfg.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install(stopped=mutate)
        self.assertFalse((self.vendor / 'etc').exists())

    def test_postpublication_sync_failure_reports_uncertain_and_retains_tree(self):
        sync = os.fsync
        def failure(fd):
            if (self.vendor / 'etc').exists():
                raise OSError('generated disk failure')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=failure), self.assertRaises(P.PromotionUncertain):
            self.install()
        self.assertEqual((self.vendor / 'etc/media_codecs_c2.xml').read_bytes(), P.CODEC_FILES['media_codecs_c2.xml'])


if __name__ == '__main__':
    unittest.main()
