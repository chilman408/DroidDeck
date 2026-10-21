# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import unittest
from unittest import mock

HERE = Path(__file__).parent
FILE = HERE / 'runtime-configured-plan.py'
spec = importlib.util.spec_from_file_location('configured_plan_tests', FILE)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def normalized(user='fixture', uid=1000, gid=1000, home='/home/fixture'):
    return {'version': 1, 'user': user, 'uid': uid, 'gid': gid,
            'lanAddress': '10.50.2.3', 'home': home, 'port': 8443, 'hostname': 'device.teslaandroid.com'}


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class Boundary(unittest.TestCase):
    def test_bounded_input_and_privilege_before_any_source_or_child_execution(self):
        with ExitStack() as stack, mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=AssertionError('no source reads')), \
                mock.patch.object(P.subprocess, 'run', side_effect=AssertionError('no child')):
            with mock.patch.object(P.A, '_root_linux'):
                for raw in (None, '', b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        P.hold_configured_runtime_plan(stack, 'fixture', '/root/source', raw)
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(P.A.sys, 'platform', platform), \
                        mock.patch.object(P.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        P.hold_configured_runtime_plan(stack, 'fixture', '/root/source', b'{}')

    def test_fixed_data_only_child_command_and_sanitized_environment(self):
        raw = b'generated metadata'
        result = subprocess.CompletedProcess([], 0, compact(normalized()), b'')
        with mock.patch.object(P.subprocess, 'run', return_value=result) as child:
            value, encoded = P._normalize(PurePosixPath('/root/reviewed'), raw)
            self.assertEqual(value, normalized())
            self.assertEqual(encoded, result.stdout)
            args, options = child.call_args
            self.assertEqual(args[0], ['/usr/bin/node', '-e', P.SCRIPT,
                                      '/root/reviewed/assembly/runtime/portable/runtime-config.js'])
            self.assertEqual(options, {'input': raw, 'capture_output': True, 'timeout': 10, 'cwd': '/',
                                      'close_fds': True, 'env': {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}})

    def test_child_failures_timeout_or_noncanonical_output_are_redacted(self):
        for result in (subprocess.CompletedProcess([], 1, b'', b'private-value'),
                       subprocess.CompletedProcess([], 0, b'', b''),
                       subprocess.CompletedProcess([], 0, b'{}', b''),
                       subprocess.CompletedProcess([], 0, compact(normalized()) + b'\n', b''),
                       subprocess.CompletedProcess([], 0, b'x' * 16385, b'')):
            with mock.patch.object(P.subprocess, 'run', return_value=result), self.assertRaisesRegex(ValueError, '^Configuration validation refused$'):
                P._normalize(PurePosixPath('/root/reviewed'), b'{}')
        with mock.patch.object(P.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed validator', 10)), \
                self.assertRaisesRegex(ValueError, '^Configuration validation refused$'):
            P._normalize(PurePosixPath('/root/reviewed'), b'{}')

    def _held(self):
        account = {key: normalized()[key] for key in ('user', 'uid', 'gid', 'home')}
        base = compact({'targetAccount': account, 'files': [], **dict.fromkeys(P.L.FALSE_FLAGS, False)})
        return {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=base),
                'read_source': mock.Mock(return_value=b'payload'),
                'policy': {'planSha256': hashlib.sha256(base).hexdigest(), **dict.fromkeys(P.L.FALSE_FLAGS, False)}}

    def test_binding_keeps_distinct_input_canonical_and_placement_generations(self):
        held = self._held()
        raw = b' {"fixture":"noncanonical source bytes"} '
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.L, 'hold_runtime_install_plan', return_value=held), \
                mock.patch.object(P, '_normalize', return_value=(normalized(), compact(normalized()))):
            result = P.hold_configured_runtime_plan(stack, 'fixture', '/root/reviewed', raw)
            metadata = result['read_configuration']()
            self.assertEqual(metadata, (json.dumps(normalized(), indent=2) + '\n').encode())
            value = json.loads(result['read_plan']())
            self.assertEqual(value['configuration'], normalized())
            self.assertEqual(value['configurationPath'], '/etc/droiddeck/runtime.json')
            self.assertEqual(value['configurationDigest'], hashlib.sha256(compact(normalized())).hexdigest())
            self.assertEqual(value['sourceMetadataSha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(value['canonicalMetadataSha256'], hashlib.sha256(metadata).hexdigest())
            self.assertNotEqual(value['sourceMetadataSha256'], value['canonicalMetadataSha256'])
            self.assertEqual(value['placementPlanSha256'], held['policy']['planSha256'])
            for flag in ('configurationIncluded', 'configurationMatched', 'configurationValidatorExecuted', 'sourceValidationCodeExecuted'):
                self.assertIs(result['policy'][flag], True)
            for flag in P.L.FALSE_FLAGS:
                if flag != 'configurationIncluded':
                    self.assertIs(result['policy'][flag], False)
            self.assertIs(result['policy']['profilePolicyVerified'], False)
            held['check'].side_effect = ValueError('source or account changed')
            for read in (result['read_plan'], result['read_configuration'], lambda: result['read_source']('artifacts/location-bridge.jar')):
                with self.assertRaises(ValueError):
                    read()

    def test_mismatched_identity_or_generation_drift_during_validation_refuses(self):
        held = self._held()
        with ExitStack() as stack, mock.patch.object(P.A, '_root_linux'), \
                mock.patch.object(P.L, 'hold_runtime_install_plan', return_value=held):
            for field, value in (('user', 'other'), ('uid', 1001), ('gid', 1001), ('home', '/home/other')):
                changed = {**normalized(), field: value}
                with mock.patch.object(P, '_normalize', return_value=(changed, compact(changed))), self.assertRaises(ValueError):
                    P.hold_configured_runtime_plan(stack, 'fixture', '/root/reviewed', b'{}')
            def changed(*args):
                held['check'].side_effect = ValueError('changed during child validation')
                return normalized(), compact(normalized())
            with mock.patch.object(P, '_normalize', side_effect=changed), self.assertRaises(ValueError):
                P.hold_configured_runtime_plan(stack, 'fixture', '/root/reviewed', b'{}')

    def test_cli_requires_bounded_stdin_not_file_or_validator_overrides(self):
        for args in ([], ['--install'], ['--inspect-configured-plan', 'fixture', '/root/source', '/private-value'],
                     ['--inspect-configured-plan', 'fixture', '/root/source', '-', '--validator', '/private-value']):
            with self.assertRaises(ValueError):
                P.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--install', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Configured runtime planning refused; no changes performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated-data-only configured-plan fixtures required')
class Configured(unittest.TestCase):
    def setUp(self):
        self.source = '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly'
        self.account = P.A._account('hilman')
        self.config = normalized(*self.account)

    def hold(self, stack, raw=None):
        raw = json.dumps(self.config, indent=1).encode() if raw is None else raw
        return P.hold_configured_runtime_plan(stack, 'hilman', self.source, raw)

    def test_real_validator_and_nss_bind_235_inputs_without_reexecuting_on_reads(self):
        before = set(os.listdir('/proc/self/fd'))
        original = P._normalize
        with mock.patch.object(P, '_normalize', wraps=original) as validator, ExitStack() as stack:
            held = self.hold(stack)
            value = json.loads(held['read_plan']())
            self.assertEqual(value['configuration'], self.config)
            self.assertEqual(len(value['files']), 229)
            self.assertEqual(len(value['links']), 4)
            self.assertEqual(held['policy']['assemblyFilesVerified'], 235)
            self.assertEqual(held['read_configuration'](), (json.dumps(self.config, indent=2) + '\n').encode())
            self.assertTrue(held['read_source']('artifacts/location-bridge.jar'))
            held['read_plan']()
            self.assertEqual(validator.call_count, 1)
            for flag in P.L.FALSE_FLAGS:
                if flag != 'configurationIncluded':
                    self.assertIs(held['policy'][flag], False)
            self.assertIs(held['policy']['rootPrivateCopyVerified'], False)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_primary_validator_defaults_and_idna_are_reused(self):
        value = {key: self.config[key] for key in ('user', 'uid', 'gid', 'home', 'lanAddress')}
        value['hostname'] = 'B\u00dcCHER.example'
        raw = json.dumps(value, ensure_ascii=False).encode('utf-8')
        with ExitStack() as stack:
            held = self.hold(stack, raw)
            canonical = json.loads(held['read_configuration']())
            self.assertEqual(canonical['hostname'], 'xn--bcher-kva.example')
            self.assertEqual(canonical['version'], 1)
            self.assertEqual(canonical['port'], 8443)
            self.assertEqual(held['policy']['configurationDigest'], hashlib.sha256(compact(canonical)).hexdigest())

    def test_generated_invalid_secrets_schema_network_and_identity_refuse(self):
        for value in ({**self.config, 'password': 'generated-private-value'}, {**self.config, 'certificatePem': 'generated-private-value'},
                      {**self.config, 'uid': self.config['uid'] + 1}, {**self.config, 'hostname': 'https://bad.example'},
                      {**self.config, 'lanAddress': '8.8.8.8'}, {**self.config, 'port': 443}, {**self.config, 'uid': True}):
            with self.subTest(keys=tuple(value)), ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack, json.dumps(value).encode())
        for raw in (b'{"password":generated-private-value}', b'\xff'):
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack, raw)

    def test_account_drift_during_validator_prevents_binding(self):
        original = P._normalize
        with mock.patch.object(P.A, '_account', return_value=self.account) as account:
            def changed(*args):
                result = original(*args)
                account.return_value = ('hilman', self.account[1] + 1, self.account[2], self.account[3])
                return result
            with mock.patch.object(P, '_normalize', side_effect=changed), ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)

    def test_cached_configuration_and_payload_reads_reject_later_account_drift(self):
        with mock.patch.object(P.A, '_account', return_value=self.account) as account, ExitStack() as stack:
            held = self.hold(stack)
            held['read_configuration']()
            account.return_value = ('hilman', self.account[1], self.account[2], '/home/foreign')
            for read in (held['read_plan'], held['read_configuration'], lambda: held['read_source']('artifacts/logical-capture.jar')):
                with self.assertRaises(ValueError):
                    read()

    def test_source_generation_drift_during_validator_is_not_a_success(self):
        original_hold, original_validator = P.L.hold_runtime_install_plan, P._normalize
        changed = False
        def wrapped(*args):
            held = original_hold(*args)
            def check():
                if changed:
                    raise ValueError('generated source-generation drift')
                held['check']()
            return {**held, 'check': check}
        def validator(*args):
            nonlocal changed
            result = original_validator(*args)
            changed = True
            return result
        with mock.patch.object(P.L, 'hold_runtime_install_plan', side_effect=wrapped), \
                mock.patch.object(P, '_normalize', side_effect=validator), ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)


if __name__ == '__main__':
    unittest.main()
