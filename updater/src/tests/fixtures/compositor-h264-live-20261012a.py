#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
"""Encode only the verified private compositor node into the existing H264 protocol."""
import json
import os
import re
import signal
import select
import socket
import struct
import sys
import time

MAX_PACKET = 2 * 1024 * 1024
SOCKET = "\0scrcpy_54410001"


def validate_plan(plan):
    keys = {"serial", "width", "height", "sourceWidth", "sourceHeight", "left", "top", "right", "bottom", "bitRate", "maxFps"}
    if not isinstance(plan, dict) or set(plan) != keys or any(type(v) is not int for v in plan.values()):
        raise ValueError("Invalid compositor plan")
    if not 1 <= plan["serial"] <= 9007199254740991:
        raise ValueError("Invalid source identity")
    for width, height in [(plan["width"], plan["height"]), (plan["sourceWidth"], plan["sourceHeight"])]:
        if any(v < 96 or v > 1920 or v % 2 for v in (width, height)) or width * height > 1600000:
            raise ValueError("Invalid capture dimensions")
    if any(plan[key] < 0 for key in ("left", "top", "right", "bottom")) or \
            plan["left"] + plan["right"] >= plan["sourceWidth"] or plan["top"] + plan["bottom"] >= plan["sourceHeight"]:
        raise ValueError("Invalid compositor crop")
    if plan["bitRate"] not in (1000000, 2000000, 3000000, 4000000, 8000000, 12000000) or plan["maxFps"] not in (30, 45, 60):
        raise ValueError("Invalid encoder targets")
    return plan


def packet(payload, pts=0, config=False, key=False):
    if not payload or len(payload) > MAX_PACKET or not 0 <= pts < (1 << 61):
        raise ValueError("Invalid H264 packet")
    flags = pts | ((1 << 62) if config else 0) | ((1 << 61) if key else 0)
    return struct.pack(">QI", flags, len(payload)) + payload


def parameter_sets(payload):
    # x264 supplies complete Annex B access units; this only extracts protocol configuration.
    if not payload or len(payload) > MAX_PACKET:
        raise ValueError("Invalid access unit")
    starts = list(re.finditer(b"\x00\x00(?:\x00)?\x01", payload))
    result, types = [], set()
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(payload)
        if start.end() >= end:
            raise ValueError("Empty Annex B NAL")
        kind = payload[start.end()] & 31
        if kind in (7, 8):
            result.append(b"\0\0\0\1" + payload[start.end():end])
            types.add(kind)
    if types != {7, 8}:
        raise ValueError("Key frame lacks SPS/PPS")
    config = b"".join(result)
    if len(config) > 256 * 1024:
        raise ValueError("Oversized configuration")
    return config


def pipeline_description(plan):
    p = validate_plan(plan)
    return (f"pipewiresrc target-object={p['serial']} do-timestamp=true ! "
            f"video/x-raw,width={p['sourceWidth']},height={p['sourceHeight']} ! "
            f"videocrop left={p['left']} top={p['top']} right={p['right']} bottom={p['bottom']} ! "
            f"videoscale ! videoconvert ! videorate drop-only=true max-rate={p['maxFps']} ! "
            f"video/x-raw,format=I420,width={p['width']},height={p['height']},framerate=[0/1,{p['maxFps']}/1] ! "
            # Shed overload before encoding; encoded deltas must retain their references.
            "queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
            f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={p['bitRate'] // 1000} "
            f"key-int-max={p['maxFps']} byte-stream=true ! "
            "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au ! "
            "appsink name=frames sync=false max-buffers=2 drop=false")


def main():
    if os.getuid() != 1000 or len(sys.argv) != 2 or not re.fullmatch(r"[a-f0-9]{32}", os.environ.get("TESLA_WAYDROID_RUNTIME_OWNER", "")):
        raise RuntimeError("Owned unprivileged compositor capture required")
    plan = validate_plan(json.loads(sys.argv[1]))
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    stopping = [False]
    def stop(_signum, _frame):
        stopping[0] = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET)
        listener.listen(1)
        listener.settimeout(1)
        while not stopping[0]:
            try:
                client, _ = listener.accept()
            except socket.timeout:
                continue
            pipeline = None
            try:
                with client:
                    client.settimeout(1)
                    client.sendall(b"h264" + struct.pack(">III", 0x80000000, plan["width"], plan["height"]))
                    pipeline = Gst.parse_launch(pipeline_description(plan))
                    sink = pipeline.get_by_name("frames")
                    pipeline.set_state(Gst.State.PLAYING)
                    base_pts, configured = None, False
                    first_deadline = time.monotonic() + 10
                    while not stopping[0]:
                        if select.select([client], [], [], 0)[0]:
                            if not client.recv(1, socket.MSG_PEEK):
                                break
                            raise RuntimeError('Capture socket is read-only')
                        sample = sink.emit("try-pull-sample", Gst.SECOND)
                        if sample is None:
                            message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                            if message:
                                detail = message.parse_error()[0] if message.type == Gst.MessageType.ERROR else 'EOS'
                                raise RuntimeError("Compositor encoder stopped: " + str(detail))
                            if not configured and time.monotonic() >= first_deadline:
                                state = pipeline.get_state(0)
                                raise RuntimeError('Compositor encoder has no frames: state=' + state[1].value_nick + ', pending=' + state[2].value_nick)
                            continue
                        buffer = sample.get_buffer()
                        payload = buffer.extract_dup(0, buffer.get_size())
                        key = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
                        if not configured:
                            if not key:
                                continue
                            client.sendall(packet(parameter_sets(payload), config=True))
                            configured = True
                            print('Compositor H264 configuration and first key frame ready', flush=True)
                        if buffer.pts == Gst.CLOCK_TIME_NONE:
                            raise RuntimeError("Encoder omitted presentation timestamp")
                        if base_pts is None:
                            base_pts = buffer.pts
                        client.sendall(packet(payload, (buffer.pts - base_pts) // 1000, key=key))
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            finally:
                if pipeline:
                    pipeline.set_state(Gst.State.NULL)
                    pipeline.get_state(3 * Gst.SECOND)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
