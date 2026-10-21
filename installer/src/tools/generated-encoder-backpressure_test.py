# SPDX-License-Identifier: GPL-3.0-only
"""Generated-only overload regression; never connects to Waydroid or a browser."""
import importlib.util
import json
from pathlib import Path
import threading
import time
import unittest

spec = importlib.util.spec_from_file_location(
    'producer', Path(__file__).parents[1] / 'runtime/preview-gateway/compositor-h264.py')
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)

try:
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
except ImportError:
    Gst = None

PLAN = dict(serial=99, width=320, height=240, sourceWidth=320, sourceHeight=240,
            left=0, right=0, top=0, bottom=0, bitRate=1000000, maxFps=60)


def generated_pipeline(description, source_fps=60):
    prefix = ('pipewiresrc target-object=99 do-timestamp=true ! '
              'video/x-raw,width=320,height=240 ! ')
    if not description.startswith(prefix):
        raise ValueError('Unexpected fixed producer prefix')
    if source_fps not in (30, 60):
        raise ValueError('Fixed generated source rate required')
    return ('videotestsrc is-live=true num-buffers=90 pattern=ball ! '
            f'video/x-raw,width=320,height=240,framerate={source_fps}/1 ! ' + description[len(prefix):])


def exercise(description, source_fps=60):
    pipeline = Gst.parse_launch(generated_pipeline(description, source_fps))
    sink = pipeline.get_by_name('frames')
    produced, consumed = [], []
    lock = threading.Lock()

    def encoded(_pad, info):
        buffer = info.get_buffer()
        if buffer is not None:
            with lock:
                produced.append(buffer.pts)
        return Gst.PadProbeReturn.OK

    sink.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, encoded)
    try:
        pipeline.set_state(Gst.State.PLAYING)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            sample = sink.emit('try-pull-sample', 100 * Gst.MSECOND)
            if sample is not None:
                consumed.append(sample.get_buffer().pts)
                if len(consumed) == 10:
                    time.sleep(0.35)
            elif sink.get_property('eos'):
                break
            error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if error:
                raise RuntimeError('Generated pipeline failed')
        else:
            raise RuntimeError('Generated pipeline did not finish within bound')
    finally:
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(3 * Gst.SECOND)
    with lock:
        missing = sum(pts not in set(consumed) for pts in produced)
        facts = dict(encoded=len(produced), consumed=len(consumed), missingEncoded=missing,
                     ordered=consumed == sorted(consumed), generatedOnly=True,
                     androidWrites=False, savedPixels=False)
    return facts


@unittest.skipIf(Gst is None, 'Native GStreamer required')
class BackpressureTests(unittest.TestCase):
    def test_every_supported_frame_target_handles_a_lower_rate_source(self):
        for target in (30, 45, 60):
            with self.subTest(target=target):
                facts = exercise(producer.pipeline_description(dict(PLAN, maxFps=target)), source_fps=30)
                print(json.dumps(dict(sourceFps=30, targetFps=target, **facts)), flush=True)
                self.assertEqual(facts['missingEncoded'], 0)
                self.assertTrue(facts['ordered'])
                self.assertGreater(facts['consumed'], 30)

    def test_generated_slow_consumer_retains_every_encoded_reference(self):
        candidate = producer.pipeline_description(PLAN)
        # Reconstruct the exact previous queue policy for the paired control.
        previous = candidate.replace(
            'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! ', '')
        previous = previous.replace('max-buffers=2 drop=false', 'max-buffers=2 drop=true')
        previous = previous.replace('videorate drop-only=true max-rate=60 !', 'videorate !')
        previous = previous.replace('framerate=[0/1,60/1]', 'framerate=60/1')
        old, new = exercise(previous), exercise(candidate)
        print(json.dumps(dict(previous=old, candidate=new)), flush=True)
        self.assertGreater(old['missingEncoded'], 5)
        self.assertEqual(new['missingEncoded'], 0)
        self.assertTrue(new['ordered'])
        self.assertGreater(new['consumed'], 30)

    def test_sparse_source_never_expands_idle_time_into_encoded_burst(self):
        def count_frames(drop_only):
            pipeline = Gst.parse_launch(
                'appsrc name=source format=time is-live=true '
                'caps=video/x-raw,format=I420,width=320,height=240,framerate=60/1 ! '
                'videorate ' + ('drop-only=true ' if drop_only else '') + '! '
                'video/x-raw,framerate=30/1 ! appsink name=frames sync=false')
            source, sink = pipeline.get_by_name('source'), pipeline.get_by_name('frames')
            count = 0
            try:
                pipeline.set_state(Gst.State.PLAYING)
                for pts in [0, 60 * Gst.SECOND, 60 * Gst.SECOND + Gst.SECOND // 30]:
                    buffer = Gst.Buffer.new_allocate(None, 320 * 240 * 3 // 2, None)
                    buffer.pts = pts
                    buffer.duration = Gst.SECOND // 60
                    if source.emit('push-buffer', buffer) != Gst.FlowReturn.OK:
                        raise RuntimeError('Generated sparse source refused buffer')
                source.emit('end-of-stream')
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    sample = sink.emit('try-pull-sample', 100 * Gst.MSECOND)
                    if sample is not None:
                        count += 1
                    elif sink.get_property('eos'):
                        return count
                    if pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR):
                        raise RuntimeError('Generated sparse pipeline failed')
                raise RuntimeError('Generated sparse source timeout')
            finally:
                pipeline.set_state(Gst.State.NULL)
                pipeline.get_state(3 * Gst.SECOND)
        previous = count_frames(False)
        candidate = count_frames('videorate drop-only=true' in producer.pipeline_description(PLAN))
        print(json.dumps(dict(sparseSourceBuffers=3, previousRateOutput=previous,
                              candidateRateOutput=candidate, generatedOnly=True)), flush=True)
        self.assertGreater(previous, 1000)
        self.assertLessEqual(candidate, 3)
        self.assertGreaterEqual(candidate, 2)


if __name__ == '__main__':
    unittest.main()
