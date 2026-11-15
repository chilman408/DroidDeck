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
import tempfile
import threading
import time
from collections import deque

MAX_PACKET = 2 * 1024 * 1024
SOCKET = "\0scrcpy_54410001"
VAAPI_MAX_PACKET = 512 * 1024


class VaapiEncoderFailure(RuntimeError):
    pass


def encoder_status(message):
    """Fixed nonblocking parent channel; never enter blocked Gst after losing it."""
    value = os.environ.get('DROIDMATE_ENCODER_STATUS_FD')
    if value is None:
        return
    if value != '3' or message not in ('vaapi-failed','teardown-begin','teardown-done'):
        raise ValueError('Fixed encoder supervision channel required')
    raw = (message+'\n').encode('ascii')
    try:
        os.set_blocking(3, False)
        if os.write(3, raw) != len(raw):
            os._exit(75)
    except OSError:
        os._exit(75)


def shutdown_capture_pipeline(Gst, pipeline, supervised=False):
    if supervised:
        encoder_status('teardown-begin')
    pipeline.set_state(Gst.State.NULL)
    pipeline.get_state(3 * Gst.SECOND)
    if supervised:
        encoder_status('teardown-done')


class EncoderProgress:
    """Bound outstanding codec work, never elapsed lifetime or idle UI time."""
    def __init__(self, maximum_seconds=1.0, clock=time.monotonic):
        self.maximum_seconds, self.clock = maximum_seconds, clock
        self.pending, self.previous = deque(), None
        self.seen_output, self.failure = False, None
        self.first_output_at = None
        self.lock = threading.Lock()

    def invalid(self, reason):
        with self.lock:
            self.failure = reason

    def submitted(self, pts):
        with self.lock:
            if (type(pts) is not int or pts < 0 or pts >= (1 << 64)-1 or
                    self.previous is not None and pts <= self.previous or len(self.pending) >= 64):
                self.failure = 'Hardware pending input is invalid or unbounded'
                return
            self.previous = pts
            self.pending.append((pts, self.clock()))

    def completed(self, pts):
        # This runs at the parsed AU's source pad, before appsink/network pressure.
        with self.lock:
            if not self.pending or self.pending[0][0] != pts:
                self.failure = 'Hardware output lost its submitted frame identity'
                return
            unused, start = self.pending.popleft()
            now = self.clock()
            if self.first_output_at is None:
                self.first_output_at = now
            if self.seen_output and now-max(start, self.first_output_at) > self.maximum_seconds:
                self.failure = 'Hardware encoding exceeded pending-frame deadline'
            self.seen_output = True

    def check(self):
        with self.lock:
            # First-frame admission owns startup. Inputs queued during startup
            # get one fixed drain window; later completions never renew it.
            if self.seen_output and self.pending and self.clock()-max(self.pending[0][1], self.first_output_at) > self.maximum_seconds:
                self.failure = 'Hardware encoding stalled with outstanding input'
            if self.failure:
                raise VaapiEncoderFailure(self.failure)


def encoder_mode(environment, plan):
    requested = environment.get('DROIDMATE_EXPERIMENTAL_H264_ENCODER', 'cpu')
    if requested not in ('cpu', 'vaapi'):
        raise ValueError('Unsupported experimental H264 encoder')
    validate_plan(plan)
    # Keep every valid selected target. Actual encoder/caps/IDR/byte-budget
    # admission decides whether hardware works, followed by same-target CPU.
    return requested


class EncoderByteBudget:
    """One-second VBV allowance on actual PTS; never buffers or drops coded deltas."""
    def __init__(self, bitrate):
        if type(bitrate) is not int or bitrate <= 0:
            raise ValueError('Positive encoder bitrate required')
        self.bitrate = bitrate
        self.capacity = bitrate * 1000000000
        self.credit = self.capacity
        self.previous = None
        self.failed = False

    def accept(self, pts, size):
        if self.failed:
            return False
        if type(pts) is not int or pts < 0 or type(size) is not int or size <= 0:
            self.failed = True
            return False
        if self.previous is not None:
            if pts <= self.previous:
                self.failed = True
                return False
            self.credit = min(self.capacity, self.credit + (pts-self.previous)*self.bitrate)
        self.previous = pts
        cost = size * 8 * 1000000000
        if cost > self.credit:
            self.failed = True
            return False
        self.credit -= cost
        return True


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
    if plan["bitRate"] not in (320000, 450000, 720000, 1000000, 2000000, 3000000, 4000000, 6000000, 8000000, 10000000, 12000000) or plan["maxFps"] not in (10, 15, 30, 45, 60):
        raise ValueError("Invalid encoder targets")
    if plan["maxFps"] == 10 and plan["bitRate"] > 450000:
        raise ValueError("10 fps is reserved for automatic emergency targets")
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


def capture_mode(environment):
    mode = environment.get('DROIDDECK_EXPERIMENTAL_GPU_CAPTURE', 'cpu')
    if mode not in ('cpu', 'hybrid'):
        raise ValueError('Unsupported experimental capture mode')
    return mode


def gpu_crop_shader(plan):
    p = validate_plan(plan)
    sw, sh = p['sourceWidth'], p['sourceHeight']
    return ('#version 100\nprecision highp float;\nvarying vec2 v_texcoord; uniform sampler2D tex;\n'
            'void main() { vec2 pos = vec2(%.12f,%.12f)+v_texcoord*vec2(%.12f,%.12f); '
            'gl_FragColor=texture2D(tex,pos); }' %
            (p['left']/sw, p['top']/sh,
             (sw-p['left']-p['right'])/sw, (sh-p['top']-p['bottom'])/sh))


def validate_gpu_evidence(renderer, dma_buffers, other_buffers):
    name = renderer.lower()
    if ('virgl' not in name or any(x in name for x in ('llvmpipe', 'softpipe', 'swrast', 'swiftshader', 'software'))
            or type(dma_buffers) is not int or dma_buffers < 1 or other_buffers != 0):
        raise RuntimeError('GPU capture needs actual DMA input and native VirGL')


def _legacy_pipeline_description(plan, capture_mode='cpu'):
    p = validate_plan(plan)
    if capture_mode not in ('cpu', 'hybrid'):
        raise ValueError('Unsupported experimental capture mode')
    if capture_mode == 'hybrid':
        # Weston is variable-rate. Constrain the source's negotiation to a scalar
        # fraction so PipeWire does not receive nested rate choices from videorate.
        # The selected fixed output cap below still limits encoded frame rate.
        return (f"pipewiresrc name=gpu_capture target-object={p['serial']} do-timestamp=true ! "
                f"video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=XR24,width={p['sourceWidth']},height={p['sourceHeight']},framerate=0/1 ! "
                "glupload name=gpu_upload ! glcolorconvert ! "
                "video/x-raw(memory:GLMemory),format=RGBA,texture-target=2D ! glshader name=gpu_crop ! "
                f"video/x-raw(memory:GLMemory),format=RGBA,width={p['width']},height={p['height']},pixel-aspect-ratio=1/1,texture-target=2D ! "
                "gldownload ! videoconvert ! "
                f"videorate drop-only=true max-rate={p['maxFps']} ! "
                f"video/x-raw,format=I420,width={p['width']},height={p['height']},framerate={p['maxFps']}/1 ! "
                "queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
                f"x264enc tune=zerolatency speed-preset=ultrafast bitrate={p['bitRate'] // 1000} "
                f"key-int-max={p['maxFps']} byte-stream=true ! "
                "video/x-h264,profile=baseline,stream-format=byte-stream,alignment=au ! "
                "appsink name=frames sync=false max-buffers=2 drop=false")
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



def capture_pacing(environment):
    mode = environment.get('DROIDDECK_EXPERIMENTAL_FRAME_PACING', 'legacy')
    if mode not in ('legacy', 'bounded'):
        raise ValueError('Unsupported experimental frame pacing')
    return mode


class BoundedFramePacer:
    """Drop raw frames on a rational PTS grid; no buffers, clock writes or idle credit."""
    def __init__(self, rate):
        if type(rate) is not int or rate not in (10, 15, 30, 45, 60):
            raise ValueError('Supported integer pacing rate required')
        self.rate = rate
        self.tolerance = min(1000000 * rate, 1000000000 // 20)
        self.due = None
        self.last = None

    def accept(self, timestamp):
        if type(timestamp) is not int or not 0 <= timestamp < (1 << 63):
            return False
        if self.last is not None and timestamp <= self.last:
            return False
        scaled = timestamp * self.rate
        if self.due is not None and scaled + self.tolerance < self.due:
            return False
        if self.due is None or scaled >= self.due + 1000000000:
            self.due = scaled + 1000000000
        else:
            self.due += 1000000000
        self.last = timestamp
        return True


def pipeline_description(plan, capture_mode='cpu', frame_pacing='legacy', encoder='cpu'):
    text = _legacy_pipeline_description(plan, capture_mode)
    if encoder not in ('cpu', 'vaapi'):
        raise ValueError('Unsupported encoder')
    if encoder == 'vaapi':
        p = validate_plan(plan)
        if encoder_mode({'DROIDMATE_EXPERIMENTAL_H264_ENCODER':'vaapi'},p) != 'vaapi':
            raise ValueError('VAAPI target is outside trial qualification')
        text = (f"pipewiresrc target-object={p['serial']} do-timestamp=true ! "
                f"video/x-raw,width={p['sourceWidth']},height={p['sourceHeight']} ! "
                f"videocrop left={p['left']} top={p['top']} right={p['right']} bottom={p['bottom']} ! "
                f"videoscale ! videoconvert ! videorate drop-only=true max-rate={p['maxFps']} ! "
                f"video/x-raw,format=NV12,width={p['width']},height={p['height']},framerate=[0/1,{p['maxFps']}/1] ! "
                "queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
                f'capssetter name=encoder_nominal_rate join=true replace=false caps="video/x-raw,framerate=(fraction){p["maxFps"]}/1" ! '
                f"vah264enc name=hardware_encoder rate-control=cbr bitrate={p['bitRate']//1000} "
                f"key-int-max={p['maxFps']} cabac=false dct8x8=false ref-frames=1 ! "
                "video/x-h264,profile=main ! h264parse name=hardware_packetizer config-interval=-1 ! "
                "video/x-h264,stream-format=byte-stream,alignment=au ! "
                "appsink name=frames sync=false max-buffers=2 drop=false")
        if capture_mode == 'hybrid':
            # Keep the existing DMA/GL transform and bounded pacing, then feed
            # NV12 to the separately supervised encoder. No target substitution.
            prefix = _legacy_pipeline_description(plan, 'hybrid').split('x264enc tune=', 1)[0]
            text = prefix.replace('format=I420', 'format=NV12') + text[text.index('capssetter name=encoder_nominal_rate'):]
    if frame_pacing == 'legacy':
        return text
    if frame_pacing != 'bounded':
        raise ValueError('Unsupported experimental frame pacing')
    p = validate_plan(plan)
    if capture_mode == 'hybrid' and not any(p[k] for k in ('left', 'right', 'top', 'bottom')) and (p['width'], p['height']) != (p['sourceWidth'], p['sourceHeight']):
        text = text.replace('glshader name=gpu_crop ! ', 'glcolorscale name=gpu_scale ! ', 1)
    text = text.replace(f"videorate drop-only=true max-rate={p['maxFps']} ! ",
                        'identity name=frame_pacer ! ', 1)
    for value in (f"[0/1,{p['maxFps']}/1]", f"{p['maxFps']}/1"):
        text = text.replace(f"height={p['height']},framerate={value} ! ",
                            f"height={p['height']},framerate=0/1 ! ", 1)
    return text


def install_frame_pacer(Gst, pipeline, plan):
    pacer = BoundedFramePacer(validate_plan(plan)['maxFps'])
    def observe(pad, event):
        buffer = event.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        return Gst.PadProbeReturn.OK if pacer.accept(int(buffer.pts)) else Gst.PadProbeReturn.DROP
    pipeline.get_by_name('frame_pacer').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, observe)
    return pacer


def create_capture_pipeline(Gst, plan, mode, encoder='cpu'):
    pacing = capture_pacing(os.environ)
    pipeline = Gst.parse_launch(pipeline_description(plan, capture_mode=mode, frame_pacing=pacing, encoder=encoder))
    if pacing == 'bounded':
        install_frame_pacer(Gst, pipeline, plan)
    if encoder == 'vaapi':
        install_encoder_progress(Gst, pipeline)
    counters = {'dma': 0, 'other': 0}
    if mode == 'hybrid':
        import gi
        gi.require_version('GstAllocators', '1.0')
        gi.require_version('GstGL', '1.0')
        from gi.repository import GstAllocators, GstGL
        crop = pipeline.get_by_name('gpu_crop')
        if crop is not None:
            crop.set_property('fragment', gpu_crop_shader(plan))
        elif pipeline.get_by_name('gpu_scale') is None:
            raise RuntimeError('GPU transform missing')
        def observe(pad, event):
            buffer = event.get_buffer()
            if buffer:
                dma = buffer.n_memory() > 0 and all(GstAllocators.is_dmabuf_memory(buffer.peek_memory(i)) for i in range(buffer.n_memory()))
                counters['dma' if dma else 'other'] += 1
            return Gst.PadProbeReturn.OK
        pipeline.get_by_name('gpu_capture').get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, observe)
    return pipeline, counters


def install_encoder_progress(Gst, pipeline):
    progress = EncoderProgress()
    pipeline.droidmate_encoder_progress = progress
    def observer(record):
        segment = [None]
        def observe(pad, info):
            try:
                if info.type & Gst.PadProbeType.EVENT_DOWNSTREAM:
                    event = info.get_event()
                    if event.type == Gst.EventType.SEGMENT:
                        value = event.parse_segment()
                        if (segment[0] is not None or value.format != Gst.Format.TIME or
                                value.rate != 1.0 or value.applied_rate != 1.0):
                            raise ValueError('Unexpected encoder segment transition')
                        segment[0] = value.copy()
                elif info.type & Gst.PadProbeType.BUFFER:
                    buffer = info.get_buffer()
                    if buffer is not None:
                        if segment[0] is None or buffer.pts == Gst.CLOCK_TIME_NONE:
                            raise ValueError('Encoder timestamp lacks a time segment')
                        # Encoders may rebase their output PTS and SEGMENT together.
                        # Compare exact running times, never infer a first-frame offset.
                        pts = int(segment[0].to_running_time(Gst.Format.TIME, int(buffer.pts)))
                        if not 0 <= pts < Gst.CLOCK_TIME_NONE:
                            raise ValueError('Encoder timestamp outside segment')
                        record(pts)
            except Exception:
                progress.invalid('Hardware frame/segment identity is invalid')
            return Gst.PadProbeReturn.OK
        return observe
    probes = Gst.PadProbeType.BUFFER | Gst.PadProbeType.EVENT_DOWNSTREAM
    pipeline.get_by_name('hardware_encoder').get_static_pad('sink').add_probe(probes, observer(progress.submitted))
    pipeline.get_by_name('hardware_packetizer').get_static_pad('src').add_probe(probes, observer(progress.completed))
    return progress


def verify_capture_gpu(pipeline, counters):
    import ctypes
    context = pipeline.get_by_name('gpu_upload').get_property('context')
    if context is None:
        raise RuntimeError('GPU context not ready')
    values = []
    def read_renderer(ctx, unused):
        address = ctx.get_proc_address('glGetString')
        if address:
            value = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_uint)(address)(0x1F01)
            if value: values.append(value.decode('ascii', 'replace'))
    context.thread_add(read_renderer, None)
    if len(values) != 1:
        raise RuntimeError('GPU renderer evidence missing')
    validate_gpu_evidence(values[0], counters['dma'], counters['other'])


def prepare_capture(Gst, plan, requested_mode, requested_encoder='cpu'):
    # Startup failure falls back before any H264 configuration/media is sent.
    # The CPU default takes the original path without a GPU wait or GL imports.
    pipeline = None
    try:
        pipeline, counters = (create_capture_pipeline(Gst, plan, requested_mode)
                              if requested_encoder == 'cpu' else
                              create_capture_pipeline(Gst, plan, requested_mode, requested_encoder))
        sink = pipeline.get_by_name('frames')
        pipeline.set_state(Gst.State.PLAYING)
        first = None
        if requested_mode == 'hybrid' or requested_encoder == 'vaapi':
            first = sink.emit('try-pull-sample', 4 * Gst.SECOND)
            if first is None:
                raise RuntimeError('GPU first frame deadline')
            if requested_encoder == 'vaapi':
                encoder = pipeline.get_by_name('hardware_encoder')
                if encoder is None or encoder.get_factory().get_name() != 'vah264enc':
                    raise VaapiEncoderFailure('Hardware encoder admission failed')
                structure = first.get_caps().get_structure(0)
                if (structure.get_string('profile') != 'main' or
                        structure.get_value('width') != plan['width'] or
                        structure.get_value('height') != plan['height'] or
                        structure.get_string('stream-format') != 'byte-stream' or
                        structure.get_string('alignment') != 'au' or
                        tuple(structure.get_fraction('framerate')) != (True, plan['maxFps'], 1)):
                    raise VaapiEncoderFailure('Hardware encoder caps differ from requested plan')
                buffer = first.get_buffer()
                if (buffer.pts == Gst.CLOCK_TIME_NONE or buffer.has_flags(Gst.BufferFlags.DELTA_UNIT) or
                        not 0 < buffer.get_size() <= VAAPI_MAX_PACKET):
                    raise VaapiEncoderFailure('Hardware first picture invalid')
                payload = buffer.extract_dup(0, buffer.get_size())
                parameter_sets(payload)
                starts = list(re.finditer(b"\x00\x00(?:\x00)?\x01",payload))
                if not any(m.end()+1 < (starts[i+1].start() if i+1 < len(starts) else len(payload))
                           and payload[m.end()] & 0x80 == 0
                           and payload[m.end()] & 0x60 != 0
                           and payload[m.end()] & 31 == 5
                           for i,m in enumerate(starts)):
                    raise VaapiEncoderFailure('Hardware first AU lacks an IDR picture')
                if not EncoderByteBudget(plan['bitRate']).accept(int(buffer.pts),len(payload)):
                    raise VaapiEncoderFailure('Hardware first AU exceeds byte budget')
                progress = getattr(pipeline, 'droidmate_encoder_progress', None)
                if progress is not None:
                    progress.check()
                if requested_mode == 'hybrid':
                    verify_capture_gpu(pipeline, counters)
            else:
                verify_capture_gpu(pipeline, counters)
        return pipeline, sink, first, requested_mode
    except Exception:
        first = None
        managed = requested_encoder == 'vaapi' and os.environ.get('DROIDMATE_ENCODER_STATUS_FD') == '3'
        if managed:
            encoder_status('vaapi-failed')
        if pipeline:
            shutdown_capture_pipeline(Gst, pipeline, requested_encoder == 'vaapi')
        if managed:
            raise VaapiEncoderFailure('Hardware admission failed; parent owns CPU replacement')
        if requested_mode != 'hybrid' and requested_encoder != 'vaapi':
            raise
        print('Experimental VAAPI encoder unavailable; using CPU encoder' if requested_encoder == 'vaapi'
              else 'Experimental GPU capture unavailable; using CPU capture', flush=True)
        pipeline, unused = create_capture_pipeline(Gst, plan, 'cpu')
        try:
            sink = pipeline.get_by_name('frames')
            pipeline.set_state(Gst.State.PLAYING)
            return pipeline, sink, None, 'cpu'
        except Exception:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_state(3 * Gst.SECOND)
            raise


def validate_worker_identity(uid, args, owner):
    expected = 1000
    if len(args) == 3 and args[1] == '--uid' and re.fullmatch(r'[1-9][0-9]{3,9}', args[2]):
        expected = int(args[2])
    elif len(args) != 1:
        raise RuntimeError("Invalid compositor worker arguments")
    if type(uid) is not int or not 1000 <= expected <= 2147483647 or uid != expected or not re.fullmatch(r"[a-f0-9]{32}", owner):
        raise RuntimeError("Owned unprivileged compositor capture required")


def _main():
    validate_worker_identity(os.getuid(), sys.argv[1:], os.environ.get("TESLA_WAYDROID_RUNTIME_OWNER", ""))
    plan = validate_plan(json.loads(sys.argv[1]))
    mode = capture_mode(os.environ)
    requested_encoder = encoder_mode(os.environ, plan)
    if mode == 'hybrid':
        os.environ.update(GST_GL_WINDOW='surfaceless', GST_GL_PLATFORM='egl', GST_GL_API='gles2', MESA_SHADER_CACHE_DISABLE='true')
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
            hardware = False
            try:
                with client:
                    client.settimeout(1)
                    client.sendall(b"h264" + struct.pack(">III", 0x80000000, plan["width"], plan["height"]))
                    pipeline, sink, pending_sample, effective_mode = prepare_capture(Gst, plan, mode, requested_encoder)
                    if effective_mode == 'cpu': mode = 'cpu'
                    hardware = pipeline.get_by_name('hardware_encoder') is not None
                    progress = getattr(pipeline, 'droidmate_encoder_progress', None) if hardware else None
                    if requested_encoder == 'vaapi' and not hardware:
                        requested_encoder = 'cpu'
                    budget = EncoderByteBudget(plan['bitRate']) if hardware else None
                    base_pts, configured = None, False
                    first_deadline = time.monotonic() + 10
                    while not stopping[0]:
                        if select.select([client], [], [], 0)[0]:
                            if not client.recv(1, socket.MSG_PEEK):
                                break
                            raise RuntimeError('Capture socket is read-only')
                        sample = pending_sample if pending_sample is not None else sink.emit("try-pull-sample", Gst.SECOND)
                        pending_sample = None
                        if progress is not None:
                            progress.check()
                        if sample is None:
                            message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                            if message:
                                detail = message.parse_error()[0] if message.type == Gst.MessageType.ERROR else 'EOS'
                                failure = VaapiEncoderFailure if hardware else RuntimeError
                                raise failure("Compositor encoder stopped: " + str(detail))
                            if not configured and time.monotonic() >= first_deadline:
                                state = pipeline.get_state(0)
                                raise RuntimeError('Compositor encoder has no frames: state=' + state[1].value_nick + ', pending=' + state[2].value_nick)
                            continue
                        buffer = sample.get_buffer()
                        if hardware and not 0 < buffer.get_size() <= VAAPI_MAX_PACKET:
                            raise VaapiEncoderFailure('Hardware AU exceeds qualified decoder size')
                        payload = buffer.extract_dup(0, buffer.get_size())
                        if hardware and (buffer.pts == Gst.CLOCK_TIME_NONE or not budget.accept(int(buffer.pts),len(payload))):
                            raise VaapiEncoderFailure('Hardware stream exceeds PTS/byte budget')
                        key = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
                        if not configured:
                            if not key:
                                continue
                            client.sendall(packet(parameter_sets(payload), config=True))
                            configured = True
                            print('Compositor H264 configuration and first key frame ready', flush=True)
                            if hardware:
                                print('Experimental VAAPI H264 encoder active', flush=True)
                            elif effective_mode == 'hybrid':
                                print('Experimental GPU resize active; software H264 retained', flush=True)
                        if buffer.pts == Gst.CLOCK_TIME_NONE:
                            raise RuntimeError("Encoder omitted presentation timestamp")
                        if base_pts is None:
                            base_pts = buffer.pts
                        client.sendall(packet(payload, (buffer.pts - base_pts) // 1000, key=key))
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            except VaapiEncoderFailure:
                # End this video connection, then accept its normal reconnect on
                # CPU. Keep the latch for this worker so a bad rate cannot flap.
                requested_encoder = 'cpu'
                if os.environ.get('DROIDMATE_ENCODER_STATUS_FD') == '3':
                    encoder_status('vaapi-failed')
                    stopping[0] = True
                print('Experimental VAAPI stream unavailable; CPU encoder latched',flush=True)
            finally:
                if pipeline:
                    shutdown_capture_pipeline(Gst, pipeline, hardware)


def main():
    if os.environ.get('DROIDMATE_EXPERIMENTAL_H264_ENCODER') == 'vaapi':
        validate_worker_identity(os.getuid(), sys.argv[1:], os.environ.get('TESLA_WAYDROID_RUNTIME_OWNER', ''))
        # VA feature discovery depends on render-node access. A fresh per-worker
        # registry avoids reusing a no-device cache from an earlier CPU launch.
        with tempfile.TemporaryDirectory(prefix='droidmate-vaapi-registry-') as registry:
            os.environ['GST_REGISTRY'] = os.path.join(registry,'registry.bin')
            return _main()
    return _main()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
