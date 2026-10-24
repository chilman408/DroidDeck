# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


if sys.platform == 'linux': C = load('chain_tested', 'gateway-update-chain.py')
else:
    with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}): C = load('chain_tested', 'gateway-update-chain.py')


class Policy(unittest.TestCase):
    def test_separate_fixed_controller_preserves_original_baseline_and_prototypes(self):
        self.assertEqual(C.B.ROOT.as_posix(), '/var/lib/droiddeck/gateway-updates-v3')
        self.assertEqual(C.B.SELF, 'runtime/portable/gateway-update-chain.py')
        self.assertIn(b'gateway-update-chain.py run %i', C.B.unit_bytes())
        self.assertIn('gateway-update.py', C.B.OVERLAYS)
        self.assertIn('gateway-update-chain.py', C.B.OVERLAYS)
        self.assertNotIn(b'/opt/droiddeck', C.B.unit_bytes())


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private cumulative update fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        # Reuse the existing generated code-pair/real-lock fixture, with a fresh
        # module instance. These mocks never touch the installed service graph.
        fixture = load('chain_fixture', 'gateway_update_test.py'); fixture.D = C.B
        self.f = fixture.Native('test_real_atomic_gateway_apply_and_explicit_rollback_restore_original_inode')
        self.addCleanup(self.f.doCleanups); self.f.setUp()
        self.history, self.preparing = self.f.root / 'history', self.f.root / 'preparing'
        self.history.mkdir(mode=0o700); self.preparing.mkdir(mode=0o700)
        for item in (patch.object(C, 'HISTORY', self.history), patch.object(C, 'PREPARING', self.preparing),
                     patch.object(C, 'inspect_controller', lambda: copy.deepcopy(self.f.context)),
                     patch.object(C.G, 'command', lambda *a, **k: None)):
            item.start(); self.addCleanup(item.stop)
        self.inputs = {}
        item = patch.object(C.B, 'signed_input', lambda digest: copy.deepcopy(self.inputs[digest]))
        item.start(); self.addCleanup(item.stop)
        self.first, self.second = 'c' * 64, 'd' * 64
        self.register(self.first, '0.1.1-rc.1', b'// first signed fixture release\n')
        self.register(self.second, '0.1.1-rc.2', b'// second signed fixture release\n')

    def register(self, digest, version, data):
        after = [{'path': 'server.js', 'mode': 0o644, 'size': len(data), 'sha256': C.P.sha(data)}]
        manifest = {'components': [{'name': 'gateway', 'before': self.f.before, 'after': after}], 'target': {'version': version}}
        bundle = C.P.canonical_archive({C.P.PREFIX + 'payload/gateway/server.js': (data, 0o644)})
        self.inputs[digest] = (copy.deepcopy(self.f.intent), manifest, bundle, {'generated': [1, 2]})

    def rollback(self, digest):
        C.request_rollback(digest)
        self.assertTrue(C.Transaction(digest).run()['originalInstallationReverified'])

    def first_closed(self):
        C.prepare(self.first); C.Transaction(self.first).run(); self.rollback(self.first)

    def test_two_real_updates_keep_original_inode_and_verify_historical_target_pairs(self):
        self.first_closed()
        first_record = (self.history / '0001.json').read_bytes()
        C.prepare(self.second); C.Transaction(self.second).run()
        rows = C.records(); self.assertEqual(len(rows), 2)
        self.assertEqual(C.current_view(rows).digest, self.second)
        self.assertEqual((self.history / '0001.json').read_bytes(), first_record)
        self.assertNotEqual(self.f.live.stat().st_ino, self.f.original_inode)
        # Historical attempts cannot be replayed against the current code.
        with self.assertRaises(ValueError): C.Transaction(self.first).run()
        self.rollback(self.second)
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)
        self.assertEqual(C.current_view(C.records()).observe()[0], 'before')
        self.assertEqual(self.f.receipt.read_bytes(), b'{"original":true}\n')

    def test_predecessor_must_finish_original_proof_before_next_preparation(self):
        C.prepare(self.first); C.Transaction(self.first).run()
        before = list(self.preparing.iterdir())
        with self.assertRaises(ValueError): C.prepare(self.second)
        self.assertEqual(list(self.preparing.iterdir()), before)
        self.assertEqual(len(C.records()), 1)

    def test_equal_or_older_release_refused_before_creating_work_or_history(self):
        self.first_closed()
        self.register(self.second, '0.1.1-rc.1', b'// independently signed equal version fixture\n')
        before = list(self.preparing.iterdir())
        with self.assertRaises(ValueError): C.prepare(self.second)
        self.assertEqual(list(self.preparing.iterdir()), before)
        self.assertEqual(len(C.records()), 1)

    def test_unpublished_partial_preparation_cannot_affect_boot(self):
        orphan = self.preparing / 'aaaaaaaaaaaa'; orphan.mkdir(mode=0o700)
        (orphan / 'incomplete-code').write_bytes(b'generated incomplete fixture only\n')
        with patch.object(C.B, 'boot_actor', lambda: None): result = C.recover_code_at_boot()
        self.assertEqual(result['attemptsVerified'], 0)
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)
        self.assertTrue((orphan / 'incomplete-code').exists())

    def test_real_death_after_history_before_directory_publication_finishes_without_apply(self):
        pid = os.fork()
        if pid == 0:
            try:
                C.finish_publication = lambda *_: os._exit(73)
                C.prepare(self.first); os._exit(74)
            except BaseException: os._exit(75)
        _, code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(code), 73)
        self.assertFalse((C.B.ROOT / self.first).exists())
        self.assertEqual(len(C.records()), 1)
        with patch.object(C.B, 'boot_actor', lambda: None): result = C.recover_code_at_boot()
        self.assertEqual(result['attemptsVerified'], 1)
        self.assertFalse(result['originalCodeRestored'])
        self.assertEqual(C.current_view(C.records()).observe()[1], {'intent.json', 'ready.json', 'candidate'})
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)
        self.assertEqual(self.f.events, [])

    def test_foreign_equal_byte_historical_target_is_not_adopted(self):
        self.first_closed(); C.prepare(self.second); C.Transaction(self.second).run()
        candidate = C.B.ROOT / self.first / 'candidate/server.js'
        raw = candidate.read_bytes(); candidate.rename(candidate.with_name('original-retained'))
        candidate.write_bytes(raw); candidate.chmod(0o644)
        with self.assertRaises(ValueError): C.current_view(C.records())

    def test_replaced_predecessor_rollback_marker_breaks_immutable_history(self):
        self.first_closed(); C.prepare(self.second)
        marker = C.B.ROOT / self.first / 'rolled-back.json'
        raw = marker.read_bytes(); marker.rename(marker.with_name('retained-original.json'))
        marker.write_bytes(raw); marker.chmod(0o600)
        with self.assertRaises(ValueError): C.records()

    def test_missing_history_or_foreign_published_transaction_fails_closed(self):
        C.prepare(self.first)
        (C.B.ROOT / self.second).mkdir(mode=0o700)
        with self.assertRaises(ValueError): C.current_view(C.records())
        self.assertEqual(self.f.events, [])

    def test_second_pending_apply_restores_original_with_prior_history_intact(self):
        self.first_closed(); C.prepare(self.second)
        tx = C.Transaction(self.second); tx.marker('apply-intent'); tx.exchange(); tx.marker('applied')
        with patch.object(C.B, 'boot_actor', lambda: None): result = C.recover_code_at_boot()
        self.assertTrue(result['originalCodeRestored'])
        self.assertEqual(len(C.records()), 2)
        self.assertTrue(C.Transaction(self.second).run()['originalInstallationReverified'])
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)

    def test_install_twice_authenticates_then_automatically_restores_baseline_and_applies_newer_release(self):
        def review(bundle, *_): return bundle, copy.deepcopy(self.inputs[bundle][1])
        def command(args, **_kwargs):
            digest = re.fullmatch(r'droiddeck-gateway-update-v3@([a-f0-9]{64})\.service', args[-1])
            if not digest: raise AssertionError('Only a fixed fixture controller service is allowed')
            C.Transaction(digest[1]).run()
        with patch.object(C, 'review_inputs', review), patch.object(C.G, 'command', command), \
                patch.object(C.B.A, 'admit', lambda _mode, _package, bundle, *_: {'admission': bundle}):
            args = ('recovered', self.f.intent['package'])
            self.assertEqual(C.install(*args, self.first, 'generated statement', 'generated signature')['kind'], 'gateway-chain-installed')
            self.assertEqual(C.install(*args, self.second, 'generated statement', 'generated signature')['kind'], 'gateway-chain-installed')
            self.assertEqual(len(C.records()), 2)
            before = list(self.f.events)
            self.assertTrue(C.install(*args, self.second, 'generated statement', 'generated signature')['readOnly'])
            self.assertEqual(self.f.events, before)
        self.assertEqual(C.current_view(C.records()).digest, self.second)
        self.assertEqual(self.f.events, ['stop', 'health:True', 'stop', 'health:False', 'stop', 'health:True'])

    def test_invalid_signature_refuses_before_any_rollback_or_preparation(self):
        C.prepare(self.first); C.Transaction(self.first).run()
        before = list(self.f.events)
        with patch.object(C, 'review_inputs', side_effect=ValueError('Generated invalid signature fixture')):
            with self.assertRaises(ValueError):
                C.install('recovered', self.f.intent['package'], self.second, 'generated statement', 'generated signature')
        self.assertEqual(self.f.events, before)
        self.assertNotIn('rollback-request.json', C.current_view(C.records()).observe()[1])

    def test_real_death_before_history_publication_leaves_only_unpublished_preparation(self):
        pid = os.fork()
        if pid == 0:
            try:
                C.rename_new = lambda *_: os._exit(73)
                C.prepare(self.first); os._exit(74)
            except BaseException: os._exit(75)
        _, code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(code), 73)
        self.assertEqual(C.records(), [])
        self.assertEqual(os.listdir(C.B.ROOT), [])
        with patch.object(C.B, 'boot_actor', lambda: None): result = C.recover_code_at_boot()
        self.assertEqual(result['attemptsVerified'], 0)
        self.assertEqual(self.f.live.stat().st_ino, self.f.original_inode)

    def test_real_death_after_directory_publication_cannot_resume_apply_on_boot(self):
        pid = os.fork()
        if pid == 0:
            try:
                original = C.finish_publication
                def die_after(*args): original(*args); os._exit(73)
                C.finish_publication = die_after
                C.prepare(self.first); os._exit(74)
            except BaseException: os._exit(75)
        _, code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(code), 73)
        with patch.object(C.B, 'boot_actor', lambda: None): result = C.recover_code_at_boot()
        self.assertEqual(result['attemptsVerified'], 1)
        self.assertFalse(result['originalCodeRestored'])
        self.assertNotIn('apply-intent.json', C.current_view(C.records()).observe()[1])
        self.assertEqual(self.f.events, [])


if __name__ == '__main__': unittest.main()
