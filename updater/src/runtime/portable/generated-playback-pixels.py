# SPDX-License-Identifier: GPL-3.0-only
"""Measure only a live generated-media lease, in memory, after H264 decode."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

WIDTH, HEIGHT = 120, 90
PHASE = 'admission'


def pixels(data):
    if len(data) != WIDTH * HEIGHT * 4:
        raise ValueError('Unexpected thumbnail layout')
    return bytes(channel for y in range(27, 54, 3) for x in range(36, 84, 3)
                 for channel in data[(y * WIDTH + x) * 4:(y * WIDTH + x) * 4 + 3])


def facts(previous, current):
    if len(current) != 432 or (previous is not None and len(previous) != 432):
        raise ValueError('Unexpected region')
    colors = sum(max(current[i:i+3]) > 100 and max(current[i:i+3])-min(current[i:i+3]) > 60
                 for i in range(0, 432, 3)) / 144
    delta = [abs(a-b) for a, b in zip(previous, current)] if previous is not None else [0]*432
    changed = sum(max(delta[i:i+3]) >= 24 for i in range(0, 432, 3)) / 144
    return colors, changed >= .03 and sum(delta)/432 >= 3


def lease(pin):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://192.168.240.1:18186/status', timeout=2) as response:
        raw = response.read(2049)
    if len(raw) > 2048:
        raise ValueError('Oversized lease')
    value = json.loads(raw)
    if value.get('kind') != 'generated-playback' or value.get('sha256') != pin or value.get('active') is not True:
        raise ValueError('Generated playback lease expired')


def main():
    global PHASE
    if os.getuid() != 1000 or len(sys.argv) != 3 or len(sys.argv[1]) > 2048 or len(sys.argv[2]) != 64:
        raise ValueError('Fixed user, capture plan and generated pin required')
    spec = importlib.util.spec_from_file_location('installed_encoder', '/opt/droiddeck/runtime/preview-gateway/compositor-h264.py')
    encoder = importlib.util.module_from_spec(spec); spec.loader.exec_module(encoder)
    plan, pin = encoder.validate_plan(json.loads(sys.argv[1])), sys.argv[2]
    PHASE = 'lease-before'; lease(pin)
    PHASE = 'gstreamer-import'
    import gi
    gi.require_version('Gst', '1.0')
    from gi.repository import Gst
    Gst.init(None)
    p = plan
    description = (f"pipewiresrc target-object={p['serial']} do-timestamp=true "
        "stream-properties=props,media.type=Video,media.category=Capture,media.role=Screen ! "
        f"video/x-raw,width={p['sourceWidth']},height={p['sourceHeight']} ! "
        f"videocrop left={p['left']} top={p['top']} right={p['right']} bottom={p['bottom']} ! "
        "videoconvert ! video/x-raw,format=I420 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast bitrate=3000 key-int-max=30 byte-stream=true ! "
        "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au ! avdec_h264 ! "
        f"videoscale ! videoconvert ! video/x-raw,format=RGBA,width={WIDTH},height={HEIGHT} ! "
        "appsink name=frames sync=false max-buffers=2 drop=true")
    PHASE = 'pipeline-parse'; pipeline = Gst.parse_launch(description)
    sink, previous = pipeline.get_by_name('frames'), None
    frames = colors = motion = 0
    deadline, next_sample = time.monotonic()+8, 0
    try:
        PHASE = 'pipeline-start'; pipeline.set_state(Gst.State.PLAYING)
        while time.monotonic() < deadline:
            PHASE = 'lease-live'; lease(pin)
            PHASE = 'sample'
            sample = sink.emit('try-pull-sample', Gst.SECOND)
            if sample is None:
                if pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR):
                    raise ValueError('Generated decode pipeline failed')
                continue
            if time.monotonic() < next_sample:
                continue
            caps = sample.get_caps().get_structure(0)
            if (caps.get_value('width'), caps.get_value('height')) != (WIDTH, HEIGHT):
                raise ValueError('Thumbnail dimensions changed')
            buffer = sample.get_buffer(); ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                raise ValueError('Cannot map generated thumbnail')
            try:
                region = pixels(mapped.data)
            finally:
                buffer.unmap(mapped)
            PHASE = 'lease-after-sample'; lease(pin)
            color, moving = facts(previous, region)
            frames += 1
            if color > .2:
                colors += 1; motion += int(moving); previous = region
            next_sample = time.monotonic()+.2
        result = {'kind': 'generated-playback-pixels', 'frames': frames, 'colorFrames': colors,
                  'motionSamples': motion, 'afterH264Decode': True, 'savedPixels': False,
                  'verified': frames >= 8 and colors >= 8 and motion >= 3}
        print(json.dumps(result))
    finally:
        pipeline.set_state(Gst.State.NULL); pipeline.get_state(2*Gst.SECOND)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Generated-only pixel inspection unavailable; phase=' + PHASE + '; no pixels saved', file=sys.stderr)
        sys.exit(1)
