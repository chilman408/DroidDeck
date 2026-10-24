# SPDX-License-Identifier: GPL-3.0-only
import ast
import configparser
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-owned-profile-plan.py')
spec = importlib.util.spec_from_file_location('owned_profile_tests', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
ORIGINAL = b'[waydroid]\narch = x86_64\nimages_path = /etc/waydroid-extra/images\n\n[properties]\n'


def base():
    plan = b'{"files":[],"links":[]}'
    return {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=plan),
        'read_configuration': mock.Mock(return_value=b'configuration'),
        'read_source': mock.Mock(return_value=b'payload'), 'policy': {
            **dict.fromkeys(P.C.L.FALSE_FLAGS, False), 'configurationDigest': 'a' * 64,
            'configurationIncluded': True, 'configurationValidatorExecuted': True,
            'sourceValidationCodeExecuted': True, 'planSha256': hashlib.sha256(plan).hexdigest()}}


class Boundary(unittest.TestCase):
    def test_pure_recipes_are_ast_identical_to_reviewed_primary_not_imported(self):
        pins = {'profile_candidate': 'c4ef236d08ba97693b4417714418f4b99bebec576bfec7111c73c3954f179a11',
                'profile_with_detected_protocols': '564465ba05feacadf48ca561762ba6bfe59e7ea01bc21158ac5c750c752ebdf0'}
        functions = {n.name: n for n in ast.parse(FILE.read_text()).body if isinstance(n, ast.FunctionDef)}
        for name, pin in pins.items():
            self.assertEqual(hashlib.sha256(ast.dump(functions[name], include_attributes=False).encode()).hexdigest(), pin)

    def test_gpu_rendering_keeps_software_codec4_rgbx_and_fixed_protocols(self):
        for gpu in (False, True):
            raw = P.profile_with_detected_protocols(P.profile_candidate(ORIGINAL, gpu=gpu))
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(raw.decode())
            self.assertEqual(dict(parser.items('properties')), {
                'debug.stagefright.ccodec': '4', 'ro.waydroid.software_rendering': '0' if gpu else '1',
                'persist.waydroid.use_subsurface': 'true' if gpu else 'false', 'media.sf.hwaccel': '0',
                'debug.ffmpeg-codec2.hwaccel.drm': '0', 'debug.ffmpeg-codec2.hwaccel.force': '0',
                'debug.ffmpeg-codec2.pixel_format': 'RGBX_8888'})
            self.assertEqual(parser['waydroid']['binder_protocol'], 'aidl3')
            self.assertEqual(parser['waydroid']['service_manager_protocol'], 'aidl3')

    def test_invalid_original_properties_schema_bounds_and_protocols_refuse(self):
        for raw in (b'', b'x' * 65537, b'\xff', b'[waydroid]\narch=x86_64',
                    ORIGINAL.replace(b'x86_64', b'arm64'), ORIGINAL + b'password=generated-secret\n',
                    ORIGINAL.replace(b'[waydroid]', b'[DEFAULT]\nunknown=1\n[waydroid]')):
            with self.assertRaises(ValueError):
                P.profile_candidate(raw)
        for name in ('binder_protocol', 'service_manager_protocol'):
            raw = ORIGINAL.replace(b'arch = x86_64', ('arch = x86_64\n' + name + ' = aidl3').encode())
            with self.assertRaises(ValueError):
                P.profile_with_detected_protocols(P.profile_candidate(raw))

    def test_privilege_bounds_and_fixed_cli_before_source_access(self):
        with ExitStack() as stack, mock.patch.object(P.C, 'hold_configured_runtime_plan', side_effect=AssertionError('no source')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.A.sys, 'platform', platform), mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_owned_profile_runtime_plan(stack, 'fixture', '/root/source', b'{}')
            with mock.patch.object(P.A, '_root_linux'):
                for raw in (None, '', b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        P.hold_owned_profile_runtime_plan(stack, 'fixture', '/root/source', raw)
        for args in ([], ['--install'], ['--inspect-owned-profile-plan', 'fixture', '/root/source', '/metadata'],
                     ['--inspect-owned-profile-plan', 'fixture', '/root/source', '-', '--profiles', '/foreign']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'generated-secret'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Owned profile runtime planning refused; no changes performed')

    def test_owned_policy_independent_of_receipts_and_all_reads_check_both_sources(self):
        held = base()
        data = {'original.cfg': ORIGINAL, 'baseline.cfg': P.profile_candidate(ORIGINAL),
                'gpu.cfg': P.profile_candidate(ORIGINAL, gpu=True)}
        profiles = {'check': mock.Mock(), 'read': data.__getitem__}
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.C, 'hold_configured_runtime_plan', return_value=held), \
                mock.patch.object(P, '_hold_profile_sources', return_value=profiles) as loader:
            result = P.hold_owned_profile_runtime_plan(stack, 'fixture', '/root/source', b'{}')
            loader.assert_called_once_with(stack, '/var/lib/droiddeck/waydroid-profile')
            plan = json.loads(result['read_plan']())
            policy = json.loads(result['read_owned_policy']())
            self.assertEqual(plan['ownedHeadlessPolicy'], policy)
            self.assertEqual(plan['configuredPlanSha256'], held['policy']['planSha256'])
            self.assertEqual(policy['originalConfigurationSha256'], hashlib.sha256(ORIGINAL).hexdigest())
            self.assertEqual(result['policy']['ownedProfilePolicySha256'], hashlib.sha256(result['read_owned_policy']()).hexdigest())
            for flag in ('profilePolicyVerified', 'gpuProfilePolicyRegenerated', 'profileSourceNamespaceVerified',
                         'configurationValidatorExecuted', 'sourceValidationCodeExecuted'):
                self.assertIs(result['policy'][flag], True)
            for flag in ('profileSourcesCopied', 'gpuAccelerationVerified', 'codecOverlayVerified', 'independentRecoveryArmed',
                         *[f for f in P.C.L.FALSE_FLAGS if f != 'configurationIncluded']):
                self.assertIs(result['policy'][flag], False)
            self.assertNotIn(ORIGINAL, result['read_plan']())
            readers = (result['read_plan'], result['read_owned_policy'], result['read_configuration'], lambda: result['read_source']('payload'))
            for check in (held['check'], profiles['check']):
                check.side_effect = ValueError('drift')
                for read in readers:
                    with self.assertRaises(ValueError):
                        read()
                check.side_effect = None


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated profile fixtures required')
class Protected(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-profile-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'profiles'
        self.root.mkdir(mode=0o700)
        self.write('original.cfg', ORIGINAL)
        self.write('baseline.cfg', P.profile_candidate(ORIGINAL))
        self.write('gpu.cfg', P.profile_candidate(ORIGINAL, gpu=True))

    def write(self, name, raw):
        file = self.root / name
        file.write_bytes(raw)
        file.chmod(0o600)

    def bind(self, stack, held=None):
        loader = P._hold_profile_sources
        def redirected(s, root):
            self.assertEqual(root, '/var/lib/droiddeck/waydroid-profile')
            return loader(s, str(self.root))
        with mock.patch.object(P.C, 'hold_configured_runtime_plan', return_value=held or base()), \
                mock.patch.object(P, '_hold_profile_sources', side_effect=redirected):
            return P.hold_owned_profile_runtime_plan(stack, 'fixture', '/root/source', b'{}')

    def test_actual_held_original_files_and_fd_cleanup(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = self.bind(stack)
            held['read_plan']()
            self.assertTrue(held['read_owned_policy']())
            self.assertEqual(held['read_configuration'](), b'configuration')
            self.assertEqual(held['read_source']('payload'), b'payload')
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_each_profile_must_be_root_private_regular_single_link(self):
        for name in P.NAMES:
            file = self.root / name
            for mode in (0o644, 0o400, 0o660):
                file.chmod(mode)
                with ExitStack() as stack, self.assertRaises(ValueError):
                    self.bind(stack)
            file.chmod(0o600)
            os.chown(file, 1000, 1000)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.bind(stack)
            os.chown(file, 0, 0)
            link = Path(self.temp.name) / 'extra-link'
            os.link(file, link)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.bind(stack)
            link.unlink()

    def test_symlink_fifo_missing_extra_and_nonprivate_directory_refuse(self):
        file = self.root / 'gpu.cfg'
        raw = file.read_bytes()
        file.unlink()
        for kind in ('symlink', 'fifo', 'missing'):
            if kind == 'symlink':
                file.symlink_to(self.root / 'baseline.cfg')
            elif kind == 'fifo':
                os.mkfifo(file, 0o600)
            with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
                self.bind(stack)
            if kind != 'missing':
                file.unlink()
        self.write('gpu.cfg', raw)
        self.write('extra', b'generated')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.bind(stack)
        (self.root / 'extra').unlink()
        self.root.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.bind(stack)

    def test_same_bytes_file_replacement_and_rewrite_are_generation_changes(self):
        for name in P.NAMES:
            file = self.root / name
            with ExitStack() as stack:
                held = self.bind(stack)
                raw = file.read_bytes()
                replacement = Path(self.temp.name) / 'replacement'
                replacement.write_bytes(raw)
                replacement.chmod(0o600)
                os.replace(replacement, file)
                with self.assertRaises(ValueError):
                    held['read_owned_policy']()
            with ExitStack() as stack:
                held = self.bind(stack)
                file.write_bytes(raw)
                with self.assertRaises(ValueError):
                    held['read_plan']()

    def test_original_directory_rename_and_same_bytes_replacement_refuse(self):
        with ExitStack() as stack:
            held = self.bind(stack)
            old = self.root.with_name('displaced')
            self.root.rename(old)
            self.root.mkdir(mode=0o700)
            for name in P.NAMES:
                self.write(name, (old / name).read_bytes())
            with self.assertRaises(ValueError):
                held['read_configuration']()

    def test_source_or_profile_drift_during_recipe_prevents_return(self):
        original = P.profile_candidate
        for target in ('source', 'profile'):
            held = base()
            def recipe(*args, **kwargs):
                result = original(*args, **kwargs)
                if target == 'source':
                    held['check'].side_effect = ValueError('source changed')
                else:
                    self.write('gpu.cfg', original(ORIGINAL, gpu=True))
                return result
            with mock.patch.object(P, 'profile_candidate', side_effect=recipe), ExitStack() as stack, self.assertRaises(ValueError):
                self.bind(stack, held)

    def test_mismatched_profiles_bounds_and_preboot_protocol_drift_refuse(self):
        for raw in (b'foreign bytes', b'', b'x' * 65537, P.profile_candidate(ORIGINAL)):
            self.write('gpu.cfg', raw)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.bind(stack)
        protocol_original = ORIGINAL.replace(b'arch = x86_64', b'arch = x86_64\nbinder_protocol = aidl3')
        self.write('original.cfg', protocol_original)
        self.write('gpu.cfg', P.profile_candidate(protocol_original, gpu=True))
        self.write('baseline.cfg', P.profile_candidate(protocol_original))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.bind(stack)

    def test_failure_closes_all_descriptors(self):
        before = set(os.listdir('/proc/self/fd'))
        self.write('gpu.cfg', b'foreign bytes')
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.bind(stack)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_composes_actual_dy_235_source_and_nss_with_only_generated_profiles(self):
        loader = P._hold_profile_sources
        account = P.A._account('hilman')
        config = dict(zip(('user', 'uid', 'gid', 'home'), account))
        config['lanAddress'] = '10.50.2.3'
        raw = json.dumps(config).encode()
        def redirected(stack, root):
            self.assertEqual(root, '/var/lib/droiddeck/waydroid-profile')
            return loader(stack, str(self.root))
        with mock.patch.object(P, '_hold_profile_sources', side_effect=redirected), \
                mock.patch.object(P.A, '_account', return_value=account) as nss, ExitStack() as stack:
            held = P.hold_owned_profile_runtime_plan(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly', raw)
            plan = json.loads(held['read_plan']())
            self.assertEqual((len(plan['files']), len(plan['links'])), (229, 4))
            self.assertEqual(plan['ownedHeadlessPolicy']['configurationDigest'], held['policy']['configurationDigest'])
            self.assertEqual(held['policy']['assemblyFilesVerified'], 235)
            nss.return_value = ('hilman', account[1] + 1, account[2], account[3])
            for read in (held['read_plan'], held['read_owned_policy'], held['read_configuration'], lambda: held['read_source']('artifacts/location-bridge.jar')):
                with self.assertRaises(ValueError):
                    read()


if __name__ == '__main__':
    unittest.main()
