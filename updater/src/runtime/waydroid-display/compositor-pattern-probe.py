#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Decode a generated isolated compositor pattern; never attach to Android."""
import json
import hashlib
import os
import sys
import time

PALETTE = [(220, 30, 30), (30, 220, 220), (30, 220, 30), (220, 30, 220)]

def cpu_phase(data):
    if len(data) != 320 * 240 * 4:
        raise RuntimeError("Unexpected CPU pattern geometry")
    # Both halves must match the fixture; compression noise cannot satisfy this gate.
    colors = [tuple(data[(40 * 320 + x) * 4:(40 * 320 + x) * 4 + 3]) for x in (80, 240)]
    matches = [phase for phase in range(4) if all(
        max(abs(a - b) for a, b in zip(colors[half], PALETTE[(phase + half) % 4])) <= 30
        for half in range(2))]
    if len(matches) != 1:
        raise RuntimeError("CPU pattern colors or channel order are incorrect")
    return matches[0]

def meaningful_motion(previous, current):
    if previous is None:
        return False
    if len(previous) != len(current) or len(current) != 320 * 240 * 4:
        raise RuntimeError("Invalid CPU motion buffers")
    differences = [max(abs(previous[i + channel] - current[i + channel]) for channel in range(3))
                   for y in range(60, 180, 8) for x in range(48, 272, 8)
                   for i in [(y * 320 + x) * 4]]
    return sum(differences) / len(differences) >= 3 and \
        sum(delta >= 24 for delta in differences) / len(differences) >= .03

def frame_summary(data, width, height, dmabuf=False):
    if (width, height) != (320, 240) or len(data) != width * height * 4:
        raise RuntimeError("Unexpected decoded pattern geometry or stride")
    pixels = range(0, len(data), 64)
    fraction = round(sum(max(data[i:i + 3]) > 8 for i in pixels) / len(pixels), 5)
    if fraction < .1:
        return None
    if not dmabuf:
        if fraction < .55:
            raise RuntimeError("Generated pattern blanked after initialization")
        for x, channel in [(160, 1), (265, 2)]:
            offset = (30 * width + x) * 4
            rgb = data[offset:offset + 3]
            if rgb[channel] < 150 or any(rgb[channel] < rgb[index] + 60 for index in range(3) if index != channel):
                raise RuntimeError("Generated pattern color channels are incorrect")
    return {"nonblackFraction": fraction, "pixelDigest": hashlib.sha256(data).hexdigest()}


def main():
    if os.getuid() != 1000 or len(sys.argv) not in [2, 3] or not sys.argv[1].isdigit() \
            or (len(sys.argv) == 3 and sys.argv[2] not in ['--dmabuf', '--cpu-pattern']):
        raise RuntimeError("Fixed user and verified numeric pattern node required")
    node = int(sys.argv[1])
    if not 1 <= node <= 9007199254740991:
        raise RuntimeError("Invalid pattern node")
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    cpu = len(sys.argv) == 3 and sys.argv[2] == '--cpu-pattern'
    dmabuf = len(sys.argv) == 3 and not cpu
    Gst.init(None)
    pipeline = Gst.parse_launch(
        f"pipewiresrc target-object={node} do-timestamp=true ! "
        "videoconvert ! video/x-raw,format=I420 ! "
        "x264enc tune=zerolatency speed-preset=ultrafast bitrate=1000 key-int-max=15 byte-stream=true ! "
        "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au ! avdec_h264 ! "
        "videoconvert ! video/x-raw,format=RGBA ! "
        "appsink name=frames sync=false max-buffers=2 drop=true"
    )
    sink = pipeline.get_by_name("frames")
    samples = []
    warmup_black = 0
    phases, motion_events, previous = set(), 0, None
    required_frames = 120 if cpu else 30
    deadline = time.monotonic() + 12
    try:
        pipeline.set_state(Gst.State.PLAYING)
        while len(samples) < required_frames and time.monotonic() < deadline:
            sample = sink.emit("try-pull-sample", Gst.SECOND)
            if sample is None:
                message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                if message:
                    error, _ = message.parse_error()
                    raise RuntimeError(str(error))
                continue
            structure = sample.get_caps().get_structure(0)
            width, height = structure.get_value("width"), structure.get_value("height")
            if (width, height) != (320, 240):
                raise RuntimeError("Unexpected isolated compositor dimensions")
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if not ok:
                raise RuntimeError("Cannot inspect decoded pattern")
            try:
                summary = frame_summary(mapped.data, width, height, dmabuf or cpu)
                if not samples and summary is None:
                    warmup_black += 1
                    continue
                if summary is None:
                    raise RuntimeError("Generated pattern blanked after initialization")
                if cpu:
                    phases.add(cpu_phase(mapped.data))
                    motion_events += meaningful_motion(previous, mapped.data)
                    previous = bytes(mapped.data)
                samples.append({"pts": int(buffer.pts), **summary})
            finally:
                buffer.unmap(mapped)
        if len(samples) < required_frames or max(item["nonblackFraction"] for item in samples) < .1:
            state = pipeline.get_state(0)
            detail = {"decodedFrames": len(samples), "pipelineState": state[1].value_nick,
                      "pendingState": state[2].value_nick,
                      "maxNonblack": max((item["nonblackFraction"] for item in samples), default=None)}
            raise RuntimeError("Generated pattern did not survive H264 round trip: " + json.dumps(detail))
        if len({item["pts"] for item in samples}) < 20:
            raise RuntimeError("Pattern stream did not advance")
        if cpu:
            if len(phases) < 3 or motion_events < 5:
                raise RuntimeError("CPU pattern lacks meaningful motion: " + json.dumps({
                    "colorPhases": len(phases), "meaningfulMotionEvents": motion_events}))
        elif dmabuf:
            if len({item["pixelDigest"] for item in samples}) < 20:
                raise RuntimeError("Decoded DMA-buffer pattern pixels did not animate")
        elif max(item["nonblackFraction"] for item in samples) - min(item["nonblackFraction"] for item in samples) < .1:
            raise RuntimeError("Decoded pattern animation did not change")
        print(json.dumps({"decodedFrames": len(samples), "width": 320, "height": 240,
                          "firstPts": samples[0]["pts"], "lastPts": samples[-1]["pts"],
                          "minNonblack": min(item["nonblackFraction"] for item in samples),
                          "maxNonblack": max(item["nonblackFraction"] for item in samples),
                          "warmupBlackFrames": warmup_black, "steadyColorsVerified": not dmabuf,
                          "cpuWrittenPattern": cpu, "colorPhases": len(phases) if cpu else None,
                          "meaningfulMotionEvents": motion_events if cpu else None,
                          "distinctPixelFrames": len({item["pixelDigest"] for item in samples}),
                          "source": "generated test pattern only"}))
    finally:
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(3 * Gst.SECOND)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
