# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import struct
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('producer', Path(__file__).with_name('compositor-h264.py'))
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)

PLAN = dict(serial=99, width=980, height=720, sourceWidth=1280, sourceHeight=720,
            left=150, right=150, top=0, bottom=0, bitRate=4000000, maxFps=30)


class BoundedPacingTests(unittest.TestCase):
    def sequence(self,source,seconds=6,gap=False):
        return [n*1000000000//source+(400000 if n%2 else 0)+(5000000000 if gap and n>=source*2 else 0) for n in range(source*seconds)]
    def test_jittered_rate_targets_and_idle_do_not_accumulate_credit(self):
        for source,rate in [(30,15),(30,30),(60,15),(60,30),(60,45),(60,60)]:
            for gap in (False,True):
                gate=producer.BoundedFramePacer(rate)
                values=self.sequence(source,gap=gap);out=[p for p in values if gate.accept(p)]
                self.assertLessEqual(abs(len(out)-rate*6),2,(source,rate,gap,len(out)))
                self.assertEqual(len(out),len(set(out)))
                self.assertTrue(all(p in values for p in out))
                for start in out:self.assertLessEqual(sum(start<=v<start+1000000000 for v in out),rate+1)
    def test_sparse_input_then_fast_source_does_not_store_credit(self):
        gate=producer.BoundedFramePacer(15)
        sparse=[i*1000000000 for i in range(5)]
        dense=[5000000000+i*1000000000//60 for i in range(120)]
        self.assertTrue(all(gate.accept(p) for p in sparse))
        out=[p for p in dense if gate.accept(p)]
        self.assertLessEqual(abs(len(out)-30),1)
    def test_duplicate_backward_and_invalid_timestamps_do_not_mutate_clock(self):
        gate=producer.BoundedFramePacer(30)
        self.assertTrue(gate.accept(1000000000))
        for p in (1000000000,999999999,-1,None,True,1.0,2**64-1):self.assertFalse(gate.accept(p))
        self.assertTrue(gate.accept(1033333333))
        for r in (0,14,16,True,30.0):
            with self.assertRaises(ValueError):producer.BoundedFramePacer(r)
    def test_pacing_is_opt_in_and_default_pipelines_are_unchanged(self):
        self.assertEqual(producer.capture_pacing({}),'legacy')
        self.assertEqual(producer.capture_pacing({'DROIDDECK_EXPERIMENTAL_FRAME_PACING':'bounded'}),'bounded')
        for bad in ('auto','1','',None):
            with self.assertRaises(ValueError):producer.capture_pacing({'DROIDDECK_EXPERIMENTAL_FRAME_PACING':bad})
        for mode in ('cpu','hybrid'):
            original=producer.pipeline_description(PLAN,capture_mode=mode)
            self.assertEqual(original,producer.pipeline_description(PLAN,capture_mode=mode,frame_pacing='legacy'))
            candidate=producer.pipeline_description(PLAN,capture_mode=mode,frame_pacing='bounded')
            self.assertIn('identity name=frame_pacer',candidate)
            self.assertNotIn('videorate',candidate)
            self.assertIn('format=I420,width=980,height=720,framerate=0/1',candidate)


    def test_bounded_gpu_uses_scale_only_for_zero_crop_real_resize(self):
        resized=dict(PLAN,left=0,right=0,top=0,bottom=0,width=960,height=540)
        fast=producer.pipeline_description(resized,'hybrid','bounded')
        self.assertIn('glcolorscale name=gpu_scale',fast)
        self.assertNotIn('glshader',fast)
        for unchanged in (PLAN,dict(resized,width=1280,height=720)):
            slow=producer.pipeline_description(unchanged,'hybrid','bounded')
            self.assertIn('glshader name=gpu_crop',slow)
        self.assertIn('glshader name=gpu_crop',producer.pipeline_description(resized,'hybrid'))


class ProtocolTests(unittest.TestCase):
    def test_portable_worker_identity_requires_exact_unprivileged_uid_and_lease(self):
        owner = 'a' * 32
        producer.validate_worker_identity(1000, ['{}'], owner)
        producer.validate_worker_identity(1200, ['{}', '--uid', '1200'], owner)
        for uid, args, lease in [(0, ['{}'], owner), (1200, ['{}'], owner),
                                 (1000, ['{}', '--uid', '1200'], owner),
                                 (1200, ['{}', '--uid', '01200'], owner),
                                 (1200, ['{}', '--uid', '1200;sh'], owner),
                                 (1200, ['{}', '--uid', '1200', 'extra'], owner),
                                 (999, ['{}', '--uid', '999'], owner),
                                 (1200, ['{}', '--uid', '1200'], 'forged')]:
            with self.assertRaises(RuntimeError):
                producer.validate_worker_identity(uid, args, lease)

    def test_gpu_mode_is_explicit_default_cpu_and_rejects_unknown_modes(self):
        self.assertEqual(producer.capture_mode({}), 'cpu')
        self.assertEqual(producer.capture_mode({'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE':'hybrid'}), 'hybrid')
        for value in ('auto','1','HYBRID','hybrid;sh',''):
            with self.assertRaises(ValueError):producer.capture_mode({'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE':value})
        self.assertEqual(producer.pipeline_description(PLAN),producer.pipeline_description(PLAN,capture_mode='cpu'))
        with self.assertRaises(ValueError):producer.pipeline_description(PLAN,capture_mode='auto')

    def test_hybrid_crops_on_gpu_then_converts_rgba_on_cpu(self):
        description=producer.pipeline_description(PLAN,capture_mode='hybrid')
        self.assertIn('video/x-raw(memory:DMABuf),format=DMA_DRM,drm-format=XR24,width=1280,height=720',description)
        self.assertIn('glshader name=gpu_crop',description)
        self.assertIn('video/x-raw(memory:GLMemory),format=RGBA,width=980,height=720',description)
        self.assertLess(description.index('gldownload'),description.index('videoconvert'))
        self.assertIn('videorate drop-only=true max-rate=30',description)
        self.assertIn('format=I420,width=980,height=720,framerate=30/1',description)
        self.assertIn('x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000',description)
        self.assertNotIn('videocrop',description)
        shader=producer.gpu_crop_shader(PLAN)
        self.assertIn('0.117187500000',shader)
        self.assertIn('0.765625000000',shader)

    def test_hybrid_declares_variable_source_rate_without_changing_output_targets(self):
        for rate in (15,30,45,60):
            description=producer.pipeline_description(dict(PLAN,maxFps=rate),capture_mode='hybrid')
            source=description.split(' ! ',2)[1]
            self.assertIn('framerate=0/1',source)
            self.assertNotIn('framerate=[',source)
            self.assertIn(f'format=I420,width=980,height=720,framerate={rate}/1',description)
            self.assertIn(f'videorate drop-only=true max-rate={rate}',description)

    def test_gpu_admission_requires_exact_renderer_and_actual_dma(self):
        producer.validate_gpu_evidence('virgl (AMD Radeon Graphics)',30,0)
        for renderer,dma,other in [('llvmpipe',30,0),('virgl (llvmpipe)',30,0),('virgl (AMD)',0,30),('virgl (AMD)',30,1)]:
            with self.assertRaises(RuntimeError):producer.validate_gpu_evidence(renderer,dma,other)

    def test_failed_gpu_is_stopped_before_cpu_fallback_and_bad_cpu_is_stopped(self):
        events=[]
        class Pipe:
            def __init__(self,name,fail=False):self.name,self.fail=name,fail
            def get_by_name(self,name):return self
            def set_state(self,state):
                events.append((self.name,state))
                if self.fail and state=='playing':raise RuntimeError('injected start failure')
            def get_state(self,timeout):pass
            def emit(self,*args):return object()
        gst=SimpleNamespace(SECOND=1,State=SimpleNamespace(PLAYING='playing',NULL='null'))
        gpu,cpu=Pipe('gpu'),Pipe('cpu')
        def factory(gst,plan,mode):
            events.append(('create',mode))
            return (gpu if mode=='hybrid' else cpu),{'dma':1,'other':0}
        with patch.object(producer,'create_capture_pipeline',side_effect=factory),patch.object(producer,'verify_capture_gpu',side_effect=RuntimeError('injected admission failure')):
            result=producer.prepare_capture(gst,PLAN,'hybrid')
        self.assertEqual(result[3],'cpu')
        self.assertIsNone(result[2])
        self.assertLess(events.index(('gpu','null')),events.index(('create','cpu')))
        bad=Pipe('bad',True)
        with patch.object(producer,'create_capture_pipeline',return_value=(bad,{})),self.assertRaises(RuntimeError):
            producer.prepare_capture(gst,PLAN,'cpu')
        self.assertIn(('bad','null'),events)

    def test_failed_gpu_releases_pending_sample_before_pool_teardown(self):
        held=[]
        class Sample:pass
        class Pipe:
            def __init__(self,gpu):self.gpu=gpu
            def get_by_name(self,name):return self
            def emit(self,*args):
                sample=Sample();held.append(weakref.ref(sample));return sample
            def set_state(self,state):
                if self.gpu and state=='null':self.released=bool(held) and held[0]() is None
            def get_state(self,timeout):pass
        gpu,cpu=Pipe(True),Pipe(False)
        gst=SimpleNamespace(SECOND=1,State=SimpleNamespace(PLAYING='playing',NULL='null'))
        def factory(gst,plan,mode):return (gpu if mode=='hybrid' else cpu),{'dma':1,'other':0}
        with patch.object(producer,'create_capture_pipeline',side_effect=factory),patch.object(producer,'verify_capture_gpu',side_effect=RuntimeError('reject GPU')):
            producer.prepare_capture(gst,PLAN,'hybrid')
        self.assertTrue(gpu.released)

    def test_default_cpu_does_not_wait_for_a_gpu_or_consume_first_sample(self):
        class Pipe:
            def get_by_name(self,name):return self
            def set_state(self,state):pass
            def emit(self,*args):raise AssertionError('CPU default must not pre-pull')
        pipeline=Pipe();gst=SimpleNamespace(State=SimpleNamespace(PLAYING='playing'))
        with patch.object(producer,'create_capture_pipeline',return_value=(pipeline,{})),patch.object(producer,'verify_capture_gpu',side_effect=AssertionError('No GPU admission on CPU default')):
            prepared=producer.prepare_capture(gst,PLAN,'cpu')
        self.assertIs(prepared[0],pipeline);self.assertIsNone(prepared[2]);self.assertEqual(prepared[3],'cpu')

    def test_config_and_key_headers_match_existing_relay(self):
        flags, size = struct.unpack('>QI', producer.packet(b'abc', 456, key=True)[:12])
        self.assertEqual(flags, (1 << 61) | 456)
        self.assertEqual(size, 3)
        self.assertEqual(producer.packet(b'x', config=True)[0], 0x40)
        for payload, pts in [(b'', 0), (b'x' * (producer.MAX_PACKET + 1), 0), (b'x', -1), (b'x', 1 << 61)]:
            with self.assertRaises(ValueError):
                producer.packet(payload, pts)

    def test_only_sps_and_pps_enter_configuration(self):
        payload = b'\0\0\1\x09\xf0\0\0\0\1\x67abc\0\0\1\x68def\0\0\0\1\x65picture'
        self.assertEqual(producer.parameter_sets(payload), b'\0\0\0\1\x67abc\0\0\0\1\x68def')
        for invalid in [b'', b'\0\0\1\x65picture', b'\0\0\1\x67abc', b'\0\0\1\0\0\1\x68abc']:
            with self.assertRaises(ValueError):
                producer.parameter_sets(invalid)

    def test_validated_crop_and_profile_build_fixed_pipeline(self):
        pipeline = producer.pipeline_description(PLAN)
        self.assertIn('target-object=99', pipeline)
        self.assertIn('left=150 top=0 right=150 bottom=0', pipeline)
        self.assertIn('width=980,height=720,framerate=[0/1,30/1]', pipeline)
        self.assertIn('profile=baseline,stream-format=byte-stream,alignment=au', pipeline)
        for key, value in [('serial', 0), ('width', 981), ('height', 2000), ('left', 1280),
                           ('maxFps', 0), ('bitRate', 123), ('serial', '99 ! fakesink'), ('top', True)]:
            with self.assertRaises(ValueError):
                producer.pipeline_description(dict(PLAN, **{key: value}))

    def test_overload_discards_raw_frames_not_encoded_references(self):
        pipeline = producer.pipeline_description(PLAN)
        self.assertIn('appsink name=frames sync=false max-buffers=2 drop=false', pipeline)
        raw_queue = 'queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream'
        self.assertIn(raw_queue, pipeline)
        self.assertLess(pipeline.index(raw_queue), pipeline.index('x264enc'))
        self.assertGreater(pipeline.index('appsink'), pipeline.index('x264enc'))

    def test_three_mbps_uses_kilobits_in_x264_without_changing_geometry_or_frame_rate(self):
        pipeline = producer.pipeline_description(dict(PLAN, bitRate=3000000))
        self.assertIn('bitrate=3000 ', pipeline)
        self.assertIn('width=980,height=720,framerate=[0/1,30/1]', pipeline)
        for invalid in (3000001, '3000000', 3000000.5):
            with self.assertRaises(ValueError):
                producer.pipeline_description(dict(PLAN, bitRate=invalid))

    def test_idle_source_does_not_generate_catchup_duplicates(self):
        self.assertIn('videorate drop-only=true max-rate=30 !', producer.pipeline_description(PLAN))
        for rate in (30, 45, 60):
            pipeline = producer.pipeline_description(dict(PLAN, maxFps=rate))
            self.assertIn(f'max-rate={rate}', pipeline)
            self.assertIn(f'framerate=[0/1,{rate}/1]', pipeline)


class ResponsiveTargetTests(unittest.TestCase):
    def test_new_targets_reach_cpu_and_vaapi_without_substituting_selected_settings(self):
        for bitrate,rate in ((320000,10),(450000,10),(6000000,30),(10000000,30)):
            plan=dict(PLAN,bitRate=bitrate,maxFps=rate)
            for encoder in ('cpu','vaapi'):
                pipeline=producer.pipeline_description(plan,encoder=encoder)
                self.assertIn(f'bitrate={bitrate//1000}',pipeline)
                self.assertIn(f'max-rate={rate}',pipeline)
                self.assertIn('vah264enc' if encoder=='vaapi' else 'x264enc',pipeline)
    def test_auto_only10fps_rejected_above_the_emergency_bitrates(self):
        for bitrate in (720000,1000000,6000000,10000000):
            with self.assertRaises(ValueError):producer.validate_plan(dict(PLAN,bitRate=bitrate,maxFps=10))
    def test_bounded_pacer_accepts10fps_without_catchup_credit(self):
        gate=producer.BoundedFramePacer(10)
        selected=[i for i in range(120) if gate.accept(i*1000000000//60)]
        self.assertLessEqual(abs(len(selected)-20),1)


if __name__ == '__main__':
    unittest.main()
