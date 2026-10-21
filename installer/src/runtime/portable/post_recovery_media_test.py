# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


def load():
    filename = Path(__file__).with_name('post-recovery-media.py')
    if not filename.exists():
        raise AssertionError('Post-recovery media component must exist')
    spec = importlib.util.spec_from_file_location('media_proof', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def video():
    return {'kind': 'generated-playback-pixels', 'frames': 40, 'colorFrames': 40,
            'motionSamples': 30, 'afterH264Decode': True, 'savedPixels': False,
            'verified': True, 'browserDecodedFrames': 350, 'audioVerified': False,
            'browserStreamVerified': False}


def audio():
    return {'kind': 'generated-android-audio', 'bytes': 368640, 'frames': 92160,
            'seconds': 1.92, 'nonzeroSamples': 150000, 'peak': 3,
            'tone': {'frequencyHz': 1000, 'sampleRate': 48000, 'windows': 192, 'matchingWindows': 190},
            'generatedPcmVerified': True, 'savedAudio': False, 'browserAudioVerified': False,
            'installable': False}


class MediaProof(unittest.TestCase):
    def test_report_requires_both_actual_proofs_and_never_claims_browser_or_sync(self):
        module = load()
        result = module.report(video(), audio())
        self.assertTrue(result['generatedMediaVerified'])
        self.assertEqual(result['video'], {'frames': 40, 'colorFrames': 40, 'motionSamples': 30})
        self.assertFalse(result['browserStreamVerified'])
        self.assertFalse(result['syncVerified'])
        self.assertFalse(result['installable'])
        self.assertNotIn('nonzeroSamples', result['audio'])

    def test_boolean_labels_cannot_substitute_for_frames_motion_or_tone(self):
        module = load()
        for change in ({'frames': 0}, {'motionSamples': 0}, {'colorFrames': 0}, {'verified': False},
                       {'savedPixels': True}, {'browserStreamVerified': True}, {'frames': True}):
            with self.assertRaises(ValueError):
                module.report({**video(), **change}, audio())
        for change in ({'peak': 0}, {'seconds': 3}, {'frames': 0}, {'bytes': 123},
                       {'savedAudio': True}, {'generatedPcmVerified': False},
                       {'tone': {**audio()['tone'], 'matchingWindows': 0}}):
            with self.assertRaises(ValueError):
                module.report(video(), {**audio(), **change})

    def test_foreign_private_fields_and_oversized_results_rejected(self):
        module = load()
        for changed in ({**video(), 'private': 'NO'}, {**video(), 'frames': 65},
                        {**video(), 'browserDecodedFrames': float('nan')}):
            with self.assertRaises(ValueError):
                module.report(changed, audio())
        with self.assertRaises(ValueError):
            module.report(video(), {**audio(), 'private': 'NO'})

    def test_fixture_readiness_requires_exact_generated_digest_before_app_launch(self):
        module = load()
        for kind in ('generated-playback', 'generated-audio-fixture'):
            sample = {'kind': kind, 'sha256': module.MEDIA_DIGEST, 'active': False}
            self.assertTrue(module.fixture_status(sample, kind))
            self.assertFalse(module.fixture_status({**sample, 'sha256': '0' * 64}, kind))
            self.assertFalse(module.fixture_status({**sample, 'kind': 'foreign'}, kind))

    def fixture(self):
        self.module = load()
        self.calls = []
        self.generation = 1
        self.fail = None
        self.changed = None
        def action(name, result=None):
            def invoke():
                self.calls.append(name)
                if self.changed == name:
                    self.generation += 1
                if self.fail == name:
                    raise ValueError('Generated boundary failure')
                return result
            return invoke
        return self.module.Proof(admit=action('admit'), identity=lambda: self.generation,
                                 vacant=action('vacant'), video=action('video', video()),
                                 audio=action('audio', audio()), cleanup=action('cleanup'),
                                 native=action('native', True))

    def test_acceptance_requires_same_generation_after_cleanup_and_native_check(self):
        proof = self.fixture()
        self.assertTrue(proof.run()['generatedMediaVerified'])
        self.assertEqual(self.calls, ['admit', 'vacant', 'video', 'audio', 'cleanup', 'vacant', 'native'])

    def test_graph_change_between_proofs_stops_before_audio_but_cleans(self):
        proof = self.fixture()
        self.changed = 'video'
        with self.assertRaises(ValueError):
            proof.run()
        self.assertNotIn('audio', self.calls)
        self.assertIn('cleanup', self.calls)

    def test_failure_or_cleanup_generation_change_cannot_report_acceptance(self):
        for failure in ('video', 'audio', 'cleanup'):
            proof = self.fixture()
            self.fail = failure
            with self.assertRaises(ValueError):
                proof.run()
            self.assertIn('cleanup', self.calls)
        proof = self.fixture()
        self.changed = 'cleanup'
        with self.assertRaises(ValueError):
            proof.run()

    def test_live_native_failure_cannot_pass_after_valid_media(self):
        proof = self.fixture()
        proof.native = lambda: False
        with self.assertRaises(ValueError):
            proof.run()

    def test_cleanup_only_stops_owned_live_units(self):
        module = load()
        quiet = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0', 'Description': 'foreign'}
        self.assertFalse(module.owned_unit(quiet, 'owned'))
        active = {**quiet, 'ActiveState': 'active', 'MainPID': '123', 'Description': 'owned'}
        self.assertTrue(module.owned_unit(active, 'owned'))
        with self.assertRaises(ValueError):
            module.owned_unit({**active, 'Description': 'foreign'}, 'owned')

    def test_cli_rejects_arbitrary_paths_commands_and_owner_values(self):
        module = load()
        for args in ([], ['--activate'], ['--inspect', '../foreign'], ['--cleanup', 'a' * 32, '--force']):
            with self.assertRaises(ValueError):
                module.main(args)

    def test_cleanup_uses_one_shared_deadline_and_still_attempts_independent_actions(self):
        module = load()
        now = [0]
        calls = []
        def first(budget):
            calls.append(budget.remaining(20))
            now[0] = 40
            raise ValueError('One cleanup failed')
        def second(budget):
            calls.append(budget.remaining(20))
        with self.assertRaises(ValueError):
            module.clean_all((first, second), clock=lambda: now[0])
        self.assertEqual(calls, [20, 5])

    def test_generated_task_teardown_rejects_foreign_or_ambiguous_jelly_pages(self):
        module = load()
        url = 'http://192.168.240.1:18186/'
        dump = '  * Hist #0: ActivityRecord{abc u0 org.lineageos.jelly/.MainActivity t42}\n      Intent { act=android.intent.action.VIEW dat=' + url + ' cmp=org.lineageos.jelly/.MainActivity }\n'
        self.assertTrue(module.owned_pages(dump, {url}))
        self.assertTrue(module.owned_pages(dump.replace('Hist #', 'Hist  #'), {url}))
        self.assertTrue(module.owned_pages(dump + '      app=ProcessRecord{123 99:org.lineageos.jelly/u0a123}\n', {url}))
        self.assertFalse(module.owned_pages('  * Hist #0: ActivityRecord{def u0 other/.Main t1}\n', {url}))
        for foreign in (dump.replace(url, 'https://private.invalid/'), dump.replace('      Intent {', '      Other {')):
            with self.assertRaises(ValueError):
                module.owned_pages(foreign, {url})
        with self.assertRaises(ValueError):
            module.owned_pages(dump + '  * Hist #1: ActivityRecord{def u0 org.lineageos.jelly/.SettingsActivity t43}\n      Intent { dat=https://private.invalid/ }\n', {url})

    def test_receipt_is_published_only_after_complete_write(self):
        module = load()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'audio.json'
            video = Path(directory) / 'video.json'
            video.write_text('{"owned":"video"}')
            def fail(fd, data):
                os.write(fd, data[:2])
                raise InterruptedError('Generated interruption before publication')
            with self.assertRaises(InterruptedError):
                module.publish_record(target, b'{"owned":"audio"}', write=fail)
            self.assertFalse(target.exists())
            self.assertEqual(json.loads(video.read_text()), {'owned': 'video'})
            module.publish_record(target, b'{"owned":"audio"}')
            self.assertEqual(json.loads(target.read_bytes()), {'owned': 'audio'})
            with self.assertRaises(FileExistsError):
                module.publish_record(target, b'{"foreign":true}')
            self.assertEqual(json.loads(target.read_bytes()), {'owned': 'audio'})

    def test_child_diagnostics_project_only_known_phase_fields(self):
        module = load()
        value = module.child_detail(b'Generated Android playback inspection incomplete: phase=pixels, pixelPhase=sample; PRIVATE_URL')
        self.assertEqual(value, 'video=pixels; pixel=sample')
        self.assertEqual(module.child_detail(b'PRIVATE_URL arbitrary stderr'), 'unavailable')

    def test_video_checker_receives_guest_shell_environment_without_source_changes(self):
        module = load()
        self.assertEqual(module.video_command(), ['/usr/bin/env',
            'PATH=/system/bin:/apex/com.android.runtime/bin:/usr/bin:/usr/sbin:/bin',
            'ANDROID_ROOT=/system', 'ANDROID_DATA=/data', '/usr/bin/node',
            str(module.VIDEO / 'generated-playback-check.js')])

    def test_page_teardown_waits_for_android_retirement_without_repeating_stop(self):
        module = load()
        now = [0.0]
        pages = iter(['Resumed: ActivityRecord{abc u0 org.lineageos.jelly/.MainActivity t42}', ''])
        module.wait_pages_closed(lambda: next(pages), {'http://192.168.240.1:18186/'},
                                 clock=lambda: now[0], sleep=lambda step: now.__setitem__(0, now[0] + step))
        self.assertGreater(now[0], 0)
        with self.assertRaises(TimeoutError):
            module.wait_pages_closed(lambda: 'ActivityRecord{abc u0 org.lineageos.jelly/.SettingsActivity t42}',
                                     {'http://192.168.240.1:18186/'}, clock=lambda: now[0],
                                     sleep=lambda step: now.__setitem__(0, now[0] + step))

    def test_inspection_source_damage_does_not_gate_independent_cleanup(self):
        module = load()
        calls = []
        ctx = object.__new__(module.Context)
        ctx.token = 'a' * 32
        ctx.read = lambda path: (_ for _ in ()).throw(ValueError('Missing video source'))
        ctx.stop = lambda name, role, budget: calls.append(role)
        ctx.close_pages = lambda budget: calls.append('page')
        with self.assertRaises(ValueError):
            ctx.cleanup()
        self.assertEqual(calls, ['page', 'audio', 'video'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
