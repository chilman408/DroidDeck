# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack, contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('tested_gateway_update', Path(__file__).with_name('gateway-update.py'))
D = importlib.util.module_from_spec(spec)
if sys.platform == 'linux': spec.loader.exec_module(D)
else:
    with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}): spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_fixed_controller_and_no_general_destination_or_unsigned_apply(self):
        self.assertEqual(D.LIVE.as_posix(), '/opt/droiddeck/runtime/preview-gateway')
        self.assertEqual(D.A.TRUST.as_posix(), '/var/lib/droiddeck/update-trust.json')
        raw = D.unit_bytes()
        self.assertIn(b'Restart=on-failure\n', raw)
        self.assertIn(b' run %i\n', raw)
        self.assertNotIn(b'Conflicts=', raw)
        self.assertNotIn(b'droiddeck-fresh-gpu-watch', raw)

    def test_boot_precedes_both_original_services_and_does_not_run_payload(self):
        raw = D.boot_bytes('012345abcdef')
        self.assertIn(b'Before=tesla-waydroid-runtime.service droiddeck-fresh-gpu-watch@012345abcdef.service\n', raw)
        self.assertIn(b'RequiredBy=tesla-waydroid-runtime.service droiddeck-fresh-gpu-watch@012345abcdef.service\n', raw)
        self.assertIn(b' recover-code-at-boot\n', raw)
        self.assertIn(b'RemainAfterExit=yes\n', raw)
        self.assertNotIn(b'/opt/droiddeck', raw)
        self.assertNotEqual(D.CONTROLLER, D.PREDECESSOR)
        with self.assertRaises(ValueError): D.boot_bytes('../foreign')


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private gateway transaction fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='droiddeck-gateway-update-fixture-', dir='/root')
        self.addCleanup(tmp.cleanup); self.root = Path(tmp.name)
        self.live_parent = self.root / 'installed'; self.live_parent.mkdir(mode=0o755); self.live_parent.chmod(0o755)
        self.live = self.live_parent / 'preview-gateway'; self.live.mkdir(mode=0o755); self.live.chmod(0o755)
        self.transactions = self.root / 'transactions'; self.transactions.mkdir(mode=0o700)
        self.home = self.root / 'fixture-home'; (self.home / '.config/droiddeck').mkdir(parents=True, mode=0o700)
        cert = self.home / '.config/droiddeck/cert.pem'; cert.write_bytes(b'PUBLIC generated certificate fixture\n'); cert.chmod(0o600)
        self.configuration = {'home': str(self.home), 'uid': 0, 'gid': 0, 'user': 'fixture', 'lanAddress': '192.168.2.7', 'port': 8443}
        self.digest = 'c' * 64; self.context = {'original': 'generated fixture enrollment', 'currentGpu': '012345abcdef'}
        old, new = b'// original gateway fixture\n', b'// target gateway fixture\n'
        self.before = [{'path': 'server.js', 'size': len(old), 'sha256': D.P.sha(old), 'mode': 0o644}]
        self.after = [{'path': 'server.js', 'size': len(new), 'sha256': D.P.sha(new), 'mode': 0o644}]
        path = self.live / 'server.js'; path.write_bytes(old); path.chmod(0o644)
        self.original_inode = self.live.stat().st_ino
        self.manifest = {'components': [{'name': 'gateway', 'before': self.before, 'after': self.after}], 'target': {'version': '0.1.1-rc.1'}}
        self.bundle = D.P.canonical_archive({D.P.PREFIX + 'payload/gateway/server.js': (new, 0o644)})
        receipt = self.root / 'original-receipt.json'; receipt.write_bytes(b'{"original":true}\n'); receipt.chmod(0o600)
        self.receipt = receipt
        self.systemd = self.root / 'units'; self.systemd.mkdir(mode=0o700)
        (self.systemd / D.RUNTIME).write_bytes(b'# generated fixture runtime unit\n')
        (self.systemd / D.RUNTIME).chmod(0o644)
        self.proof = {'objects': {str(self.live_parent): {'kind': 'directories', 'device': self.live_parent.stat().st_dev, 'inode': self.live_parent.stat().st_ino},
                        str(receipt): {'kind': 'files', 'device': receipt.stat().st_dev, 'inode': receipt.stat().st_ino}},
                      'receiptGenerations': {str(receipt): D.E.generation(receipt.stat())}}
        self.intent = {'mode': 'recovered', 'package': '/root/generated-original-package', 'baseProofSha256': D.P.sha(D.P.encode(self.proof))}
        self.events, self.locked = [], False
        self.g = SimpleNamespace(boot_id=lambda: 'fixture-boot', unit=lambda _: {'ActiveState': 'active', 'MainPID': '10', 'ControlPID': '0'},
            require_quiet=lambda _: None, command=lambda *a, **k: None,
            WATCHER='droiddeck-fresh-gpu-watch@012345abcdef.service', WORKER='droiddeck-fresh-gpu-worker@012345abcdef.service')
        self.active = SimpleNamespace(metadata=lambda: None,
            store=SimpleNamespace(lock=self.lock, status=lambda: {'stage': 'ready', 'boot': 'fixture-boot'}))
        original_read, original_load = D.G.protected_read, D.load
        self._original_bootstrap = D.inspect_bootstrap
        def read(path, *args, **kwargs):
            if str(path) == '/etc/droiddeck/runtime.json': return (json.dumps(self.configuration, indent=2) + '\n').encode()
            if str(path) == '/var/lib/droiddeck/installation-tail-recovery/complete.json': return D.P.encode({'currentGpu': '012345abcdef'})
            return original_read(path, *args, **kwargs)
        module = SimpleNamespace(G=self.g, configure=lambda _: None, ADMISSIONS=self.root,
                                 Fresh=lambda *_: self.active)
        self.g.protected_read = lambda *_: D.P.encode({'generated': True})
        self.g.sources = lambda: (None, None)
        patches = (patch.object(D, 'LIVE', self.live), patch.object(D, 'ROOT', self.transactions), patch.object(D, 'SYSTEMD', self.systemd),
            patch.object(D, 'inspect_controller', lambda: copy.deepcopy(self.context)),
            patch.object(D, 'inspect_bootstrap', lambda _: None),
            patch.object(D, 'boot_actor', lambda: None),
            patch.object(D, 'signed_input', lambda _: (copy.deepcopy(self.intent), copy.deepcopy(self.manifest), self.bundle, {'generated': [1, 2]})),
            patch.object(D.A, 'inspect', lambda *_: None),
            patch.object(D.A, 'base_proof', self.base_proof),
            patch.object(D.A, 'bind', lambda *_: self.intent['baseProofSha256']),
            patch.object(D.G, 'protected_read', read),
            patch.object(D, 'load', lambda name, root=None: module if name == 'fresh-gpu' else original_load(name, root)),
            patch.object(D.Transaction, 'actor', lambda _: None),
            patch.object(D.Transaction, 'native', lambda _: {'containerIdentity': '1:2:3', 'androidIdentity': '4:5/6:7'}),
            patch.object(D.Transaction, 'stop_runtime', lambda x: self.events.append('stop') or x.observe()),
            patch.object(D.Transaction, 'start_and_verify', lambda x, target: self.events.append('health:' + str(target)) or x.observe()))
        for item in patches: item.start(); self.addCleanup(item.stop)

    def base_proof(self, *_args):
        if self.locked: raise BlockingIOError('Base proof independently takes the SAME original GPU lock')
        return copy.deepcopy(self.proof)

    @contextmanager
    def lock(self):
        if self.locked: raise BlockingIOError('Original recovery lock held')
        self.locked = True
        try: yield
        finally: self.locked = False

    def prepare(self):
        return D.prepare(self.digest)

    def transaction(self):
        return D.Transaction(self.digest)

    def test_real_atomic_gateway_apply_and_explicit_rollback_restore_original_inode(self):
        self.prepare(); transaction = self.transaction()
        result = transaction.run(); self.assertTrue(result['codeApplied'])
        self.assertNotEqual(self.live.stat().st_ino, self.original_inode)
        self.assertEqual(transaction.observe()[0], 'after')
        self.assertEqual(transaction.run(), {'kind': 'gateway-update-healthy', 'readOnly': True})
        D.request_rollback(self.digest)
        result = self.transaction().run()
        self.assertTrue(result['originalInstallationReverified'])
        self.assertEqual(self.live.stat().st_ino, self.original_inode)
        self.assertEqual(self.transaction().observe()[0], 'before')
        self.assertFalse(self.locked)
        self.assertEqual(self.events, ['stop', 'health:True', 'stop', 'health:False'])

    def test_process_death_before_exchange_after_exchange_and_after_apply_marker_rolls_back(self):
        for phase in ('intent', 'exchange', 'applied'):
            with self.subTest(phase=phase):
                self.prepare()
                pid = os.fork()
                if pid == 0:
                    try:
                        transaction = self.transaction(); transaction.marker('apply-intent')
                        if phase != 'intent': transaction.exchange()
                        if phase == 'applied': transaction.marker('applied')
                        os._exit(73)
                    except BaseException: os._exit(74)
                _, code = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(code), 73)
                self.assertTrue(self.transaction().run()['originalInstallationReverified'])
                self.assertEqual(self.live.stat().st_ino, self.original_inode)
                shutil.rmtree(self.transactions / self.digest)

    def test_death_during_rollback_after_restoring_original_is_recoverable(self):
        self.prepare(); transaction = self.transaction(); transaction.run(); D.request_rollback(self.digest)
        pid = os.fork()
        if pid == 0:
            try:
                tx = self.transaction(); tx.marker('rollback-intent'); tx.exchange(); os._exit(73)
            except BaseException: os._exit(74)
        _, code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(code), 73)
        self.assertTrue(self.transaction().run()['originalInstallationReverified'])
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_original_namespace_or_receipt_mutation_blocks_before_code_exchange(self):
        self.prepare()
        self.receipt.write_bytes(b'{"foreign":true}\n')
        with self.assertRaises(ValueError): self.transaction()
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_same_byte_foreign_candidate_or_copied_transaction_is_not_adopted(self):
        self.prepare()
        candidate = self.transactions / self.digest / 'candidate/server.js'
        raw = candidate.read_bytes(); candidate.rename(candidate.with_name('original-retained'))
        candidate.write_bytes(raw); candidate.chmod(0o644)
        with self.assertRaises(ValueError): self.transaction()
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_unknown_or_reordered_phase_refuses(self):
        self.prepare(); transaction = self.transaction()
        transaction.marker('healthy')
        with self.assertRaises(ValueError): self.transaction()
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_shared_original_gpu_lock_wins_before_any_stop_or_mutation(self):
        self.prepare(); self.locked = True
        with self.assertRaises(BlockingIOError): self.transaction().run()
        self.assertEqual(self.events, [])
        self.assertFalse((self.transactions / self.digest / 'apply-intent.json').exists())

    def test_prepare_cannot_replay_or_overwrite_original_transaction(self):
        self.prepare(); before = (self.transactions / self.digest / 'intent.json').stat()
        with self.assertRaises(FileExistsError): self.prepare()
        self.assertEqual((self.transactions / self.digest / 'intent.json').stat(), before)

    def test_maintenance_finalizer_never_exchanges_target_or_invents_rollback(self):
        self.prepare(); tx = self.transaction(); tx.run()
        with self.assertRaises(ValueError): D.finalize_restored(self.digest)
        self.assertEqual(self.transaction().observe()[0], 'after')
        self.assertFalse((self.transactions / self.digest / 'rolled-back.json').exists())
        D.request_rollback(self.digest); tx.marker('rollback-intent'); tx.exchange()
        self.assertTrue(D.finalize_restored(self.digest)['originalInstallationReverified'])
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_boot_restores_pending_code_then_normal_controller_finishes_proof(self):
        self.prepare(); tx = self.transaction(); tx.marker('apply-intent'); tx.exchange(); tx.marker('applied')
        self.assertTrue(D.restore_code_at_boot(self.transaction()))
        side, names = self.transaction().observe()
        self.assertEqual(side, 'before')
        self.assertIn('boot-code-restored.json', names)
        self.assertNotIn('rolled-back.json', names)
        self.assertEqual(self.events, [])
        self.assertTrue(self.transaction().run()['originalInstallationReverified'])
        self.assertFalse(D.restore_code_at_boot(self.transaction()))
        self.assertEqual(self.live.stat().st_ino, self.original_inode)

    def test_death_after_boot_exchange_before_marker_reopens_original_pair(self):
        self.prepare(); tx = self.transaction(); tx.marker('apply-intent'); tx.exchange()
        pid = os.fork()
        if pid == 0:
            try:
                original = D.Transaction.marker
                def death_marker(obj, name):
                    if name == 'boot-code-restored': os._exit(73)
                    original(obj, name)
                D.Transaction.marker = death_marker
                D.restore_code_at_boot(self.transaction()); os._exit(74)
            except BaseException: os._exit(75)
        _, code = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(code), 73)
        self.assertEqual(self.live.stat().st_ino, self.original_inode)
        self.assertTrue(D.restore_code_at_boot(self.transaction()))
        self.assertTrue(self.transaction().run()['originalInstallationReverified'])

    def test_healthy_update_has_no_boot_expiry_and_prepared_update_is_not_applied(self):
        self.prepare()
        self.assertFalse(D.restore_code_at_boot(self.transaction()))
        self.assertEqual(self.live.stat().st_ino, self.original_inode)
        self.transaction().run()
        self.assertFalse(D.restore_code_at_boot(self.transaction()))
        self.assertEqual(self.transaction().observe()[0], 'after')
        self.assertFalse((self.transactions / self.digest / 'rollback-intent.json').exists())

    def test_running_original_watcher_blocks_boot_mutation(self):
        self.prepare(); tx = self.transaction(); tx.marker('apply-intent'); tx.exchange()
        def quiet(_): raise ValueError('Original watcher is running')
        with patch.object(self.g, 'require_quiet', quiet):
            with self.assertRaises(ValueError): D.restore_code_at_boot(self.transaction())
        self.assertEqual(self.transaction().observe()[0], 'after')
        self.assertFalse((self.transactions / self.digest / 'rollback-intent.json').exists())

    def test_updater_waits_for_current_boot_gpu_before_restarting_runtime(self):
        self.prepare(); tx = self.transaction(); tx.marker('apply-intent')
        with patch.object(self.active.store, 'status', lambda: {'stage': 'ready', 'boot': 'previous-boot'}):
            with self.assertRaises(ValueError): self.transaction().run()
        self.assertEqual(self.events, [])
        self.assertFalse((self.transactions / self.digest / 'rollback-intent.json').exists())

    def test_boot_refuses_partial_or_foreign_namespace(self):
        self.prepare()
        foreign = self.transactions / 'foreign'; foreign.mkdir(mode=0o700)
        with self.assertRaises(ValueError): D.recover_code_at_boot()
        foreign.rmdir()
        (self.transactions / self.digest / 'ready.json').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)): D.recover_code_at_boot()
        self.assertEqual(self.events, [])

    def test_arm_publishes_exact_owned_dependencies_and_rejects_equal_target_replacement(self):
        controller = self.root / 'controller'; controller.mkdir(mode=0o700)
        with patch.object(D, 'CONTROLLER', controller), patch.object(D.G, 'command', lambda *a, **k: None):
            self.assertFalse(D.arm_boot()['codeApplied'])
            # Use the real verifier: identical link targets on replacement inodes
            # cannot inherit the original enrollment's boot authority.
            verifier = self._original_bootstrap
            verifier(self.context)
            self.assertTrue(D.arm_boot()['readOnly'])
            link = D.boot_links(self.context)[0]
            link.rename(link.with_name('retained-original'))
            link.symlink_to(D.SYSTEMD / D.BOOT)
            with self.assertRaises(ValueError): verifier(self.context)

    def test_arm_refuses_foreign_enable_link_before_any_publication(self):
        controller = self.root / 'controller'; controller.mkdir(mode=0o700)
        with patch.object(D, 'CONTROLLER', controller), patch.object(D.G, 'command', lambda *a, **k: None):
            link = D.boot_links(self.context)[0]
            link.parent.mkdir(mode=0o755); link.symlink_to(D.SYSTEMD / D.BOOT)
            with self.assertRaises(ValueError): D.arm_boot()
            self.assertEqual(os.listdir(controller), [])


if __name__ == '__main__':
    unittest.main()
