#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded generated-video diagnostics before encoding; no saved pixels or stream."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

spec = importlib.util.spec_from_file_location('capture_encoder', Path(__file__).with_name('compositor-h264.py'))
encoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(encoder)
WIDTH, HEIGHT = 120, 90


def pipeline_description(plan):
    p = encoder.validate_plan(plan)
    return (f"pipewiresrc target-object={p['serial']} do-timestamp=true ! "
            f"video/x-raw,width={p['sourceWidth']},height={p['sourceHeight']} ! "
            f"videocrop left={p['left']} top={p['top']} right={p['right']} bottom={p['bottom']} ! "
            "videoscale ! videoconvert ! "
            f"video/x-raw,format=RGBA,width={WIDTH},height={HEIGHT} ! "
            "appsink name=frames sync=false max-buffers=2 drop=true")


def video_region(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != WIDTH * HEIGHT * 4:
        raise ValueError('Unexpected generated thumbnail stride')
    return bytes(channel for y in range(27, 54, 3) for x in range(36, 84, 3)
                 for channel in data[(y * WIDTH + x) * 4:(y * WIDTH + x) * 4 + 3])


def pixel_facts(previous, current):
    if len(current) != 432 or (previous is not None and len(previous) != len(current)):
        raise ValueError('Invalid generated region')
    samples = len(current) // 3
    colorful = sum(max(current[i:i + 3]) > 100 and max(current[i:i + 3]) - min(current[i:i + 3]) > 60
                   for i in range(0, len(current), 3)) / samples
    changed = 0
    mean = 0
    if previous is not None:
        delta = [abs(a - b) for a, b in zip(previous, current)]
        changed = sum(max(delta[i:i + 3]) >= 24 for i in range(0, len(delta), 3)) / samples
        mean = sum(delta) / len(delta)
    return {'colorfulRatio': colorful, 'changedRatio': changed, 'meanDifference': mean,
            'moving': changed >= .03 and mean >= 3}


def main():
    if os.getuid() != 1000 or len(sys.argv) != 2 or len(sys.argv[1]) > 2048:
        raise RuntimeError('Fixed unprivileged generated source probe required')
    plan = encoder.validate_plan(json.loads(sys.argv[1]))
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    pipeline = Gst.parse_launch(pipeline_description(plan))
    sink = pipeline.get_by_name('frames')
    frames = colors = motion = 0
    max_changed = max_difference = 0
    previous = None
    deadline = time.monotonic() + 12
    next_sample = 0
    try:
        pipeline.set_state(Gst.State.PLAYING)
        while time.monotonic() < deadline:
            sample = sink.emit('try-pull-sample', Gst.SECOND)
            if sample is None:
                if pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR):
                    raise RuntimeError('Generated source pipeline failed')
                continue
            if time.monotonic() < next_sample:
                continue
            caps = sample.get_caps().get_structure(0)
            if (caps.get_value('width'), caps.get_value('height')) != (WIDTH, HEIGHT):
                raise RuntimeError('Unexpected generated source geometry')
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                raise RuntimeError('Cannot inspect generated source')
            try:
                region = video_region(mapped.data)
            finally:
                buffer.unmap(mapped)
            facts = pixel_facts(previous, region)
            frames += 1
            if facts['colorfulRatio'] > .2:
                colors += 1
                motion += int(facts['moving'])
                max_changed = max(max_changed, facts['changedRatio'])
                max_difference = max(max_difference, facts['meanDifference'])
                previous = region
            next_sample = time.monotonic() + .2
        if frames < 8:
            raise RuntimeError('Insufficient generated source frames')
        print(json.dumps({'sampledFrames': frames, 'colorSamples': colors,
                          'meaningfulMotionSamples': motion, 'maximumChangedRatio': max_changed,
                          'maximumMeanDifference': max_difference, 'beforeH264Encoding': True,
                          'savedPixels': False, 'globalSettingsWrites': False,
                          'scope': 'generated testsrc2 only'}))
    finally:
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(3 * Gst.SECOND)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Generated compositor motion inspection failed; no settings changed', file=sys.stderr)
        sys.exit(1)
