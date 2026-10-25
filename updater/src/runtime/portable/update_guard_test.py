# SPDX-License-Identifier: GPL-3.0-only
"""Root-private durable guard fixtures; no installed services are operated."""
import copy
import hashlib
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


def load(name):
    spec = importlib.util.spec_from_file_location('tested_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


if sys.platform == 'linux':
    D, F = load('update-guard'), load('update_quiescence_test')
else:
    # Import-only policy checks; native ownership/flock tests remain explicit skips.
    with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}):
        D, F = load('update-guard'), load('update_quiescence_test')
NAME, TOKEN = 'a' * 32, F.TOKEN


class Policy(unittest.TestCase):
    def test_enrolled_worker_cannot_apply_code_and_units_order_original_owner(self):
        self.assertIn(b'ExecStart=/usr/bin/false\n', D.worker_bytes())
        self.assertIn(('Before=droiddeck-fresh-gpu-watch@' + TOKEN + '.service').encode(), D.unit_bytes(TOKEN))
        self.assertIn(b'Restart=on-failure\n', D.unit_bytes(TOKEN))
        for value in ('../escape', '', True, 'A' * 12):
            with self.assertRaises(ValueError): D.token(value)

    def test_complete_guard_dependency_overlay_is_explicit(self):
        self.assertEqual(len(D.OVERLAYS), len(set(D.OVERLAYS)))
        self.assertTrue({'gpu-fallback-trial.py', 'update-base.py', 'installation-tail-recovery.py',
                         'recovery-probe-io.js', 'update-quiescence.py', 'update-exchange.py'} <= set(D.OVERLAYS))


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private durable guard fixtures')
class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-update-guard-fixture-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('leases', 'requests', 'results'): (self.root / name).mkdir(mode=0o700)
        self.fx = F.Fixture()
        self.context = {'mode': 'recovered', 'package': '/root/fixture-public-package', 'currentGpu': TOKEN,
                        'sourceIntentSha256': 'c' * 64, 'guardUnitSha256': hashlib.sha256(F.GUARD_TEMPLATE).hexdigest()}
        self.components = [{'name': name, 'root': path, 'files': []} for name, path in sorted(D.load('update-package').COMPONENTS.items())]
        self.snapshot = {row['name']: {'original': row['root']} for row in self.components}
        self.base = {'components': self.components, 'originalInstallationOwnershipVerified': True}
        self.fx.units[F.D.worker(D.G.sha(D.H.encode(self.base)))] = dict(F.QUIET)
        original_load = D.load
        patches = (patch.object(D, 'ROOT', self.root), patch.object(D, 'SOURCE', Path(__file__).parents[2]),
                   patch.object(D, 'activation', lambda _context: (SimpleNamespace(G=self.fx.g), self.fx.active)),
                   patch.object(D, 'code_snapshot', lambda components: copy.deepcopy(self.snapshot)),
                   patch.object(D, 'prove', self.prove),
                   patch.object(D, 'load', lambda name, root=None: F.D if name == 'update-quiescence' else original_load(name, root)),
                   patch.object(F.D, 'WATCH_PIN', hashlib.sha256(F.TEMPLATE).hexdigest()))
        for item in patches: item.start(); self.addCleanup(item.stop)

    def prove(self, *_args):
        if self.fx.units[self.fx.g.WATCHER]['ActiveState'] != 'active':
            raise ValueError('Original independent owner not yet rearmed')
        return copy.deepcopy(self.base)

    def journal(self, phase='intent'):
        directory = self.root / 'leases' / NAME
        directory.mkdir(mode=0o700)
        journal = D.Journal(directory, copy.deepcopy(self.components), copy.deepcopy(self.snapshot), self.context, self.base)
        journal.intent({'watcher': self.fx.g.WATCHER, 'originalPid': '100', 'guard': D.GUARD,
                        'guardPid': '999', 'worker': F.D.worker(D.G.sha(D.H.encode(self.base))),
                        'boot': F.BOOT, 'nativeGeneration': F.EVIDENCE})
        for name in ('granted', 'return_intent', 'returned'):
            if phase == 'intent': break
            getattr(journal, name)()
            if phase == name: break
        return journal

    def result(self):
        value = json.loads((self.root / 'results' / (NAME + '.json')).read_bytes())
        self.assertFalse(value['updatesAllowed'])
        self.assertFalse(value['liveCodeApplied'])
        self.assertTrue(value['originalSupervisionRearmed'])
        return value

    def test_normal_proof_keeps_durable_original_phases_and_rearms_owner(self):
        D.run_proof(self.context, NAME)
        self.assertEqual(self.result()['kind'], 'no-code-handoff-proof')
        self.assertEqual(set(os.listdir(self.root / 'leases' / NAME)),
                         {'intent.json', 'granted.json', 'return-intent.json', 'returned.json'})
        self.assertFalse(self.fx.locked)

    def test_restarted_return_at_each_original_phase_never_forges_missing_phases(self):
        for phase in ('intent', 'granted', 'return_intent', 'returned'):
            with self.subTest(phase=phase):
                journal = self.journal(phase)
                original = {p.name: p.read_bytes() for p in journal.directory.iterdir()}
                self.fx.units[self.fx.g.WATCHER] = dict(F.QUIET)
                D.recover_proof(self.context, NAME)
                self.assertEqual(self.result()['kind'], 'no-code-handoff-recovered')
                self.assertEqual({p.name: p.read_bytes() for p in journal.directory.iterdir()
                                  if p.name != 'recovery-intent.json'}, original)
                self.assertFalse(self.fx.locked)
                shutil.rmtree(journal.directory)
                (self.root / 'results' / (NAME + '.json')).unlink()

    def test_pending_return_resumes_after_second_death_before_service_start(self):
        journal = self.journal('granted')
        self.fx.units[self.fx.g.WATCHER] = dict(F.QUIET)
        command = self.fx.g.command
        def died(args, **kwargs):
            if args[1] == 'start': raise SystemExit(73)
            return command(args, **kwargs)
        with patch.object(self.fx.g, 'command', died), self.assertRaises(SystemExit):
            D.recover_proof(self.context, NAME)
        self.assertTrue((journal.directory / 'recovery-intent.json').exists())
        self.assertFalse(self.fx.locked)
        self.assertFalse((self.root / 'results' / (NAME + '.json')).exists())
        retained = (journal.directory / 'recovery-intent.json').stat().st_ino
        D.recover_proof(self.context, NAME)
        self.result()
        self.assertEqual((journal.directory / 'recovery-intent.json').stat().st_ino, retained)

    def test_empty_preintent_lease_closes_only_with_complete_fresh_original_base(self):
        directory = self.root / 'leases' / NAME
        directory.mkdir(mode=0o700)
        D.recover_proof(self.context, NAME)
        self.assertEqual(self.result()['kind'], 'no-code-proof-aborted-before-intent')
        self.assertEqual(os.listdir(directory), [])
        self.assertEqual(self.fx.events, [])

    def test_unknown_code_cannot_rearm_or_create_recovery_authority(self):
        journal = self.journal('granted')
        self.fx.units[self.fx.g.WATCHER] = dict(F.QUIET)
        self.snapshot['display'] = {'foreign': True}
        with self.assertRaises(ValueError): D.recover_proof(self.context, NAME)
        self.assertFalse(any(e.startswith('start:') for e in self.fx.events))
        self.assertFalse((journal.directory / 'recovery-intent.json').exists())

    def test_copied_lease_cannot_adopt_original_inode_authority(self):
        journal = self.journal('granted')
        original = journal.directory.with_name('original-retained')
        journal.directory.rename(original)
        shutil.copytree(original, journal.directory)
        with self.assertRaises(ValueError): D.recover_proof(self.context, NAME)
        self.assertEqual(self.fx.events, [])

    def test_foreign_phase_or_reordered_completion_or_unknown_object_refuses(self):
        for kind in ('phase', 'order', 'object'):
            journal = self.journal()
            if kind == 'phase': D.T.write_new(journal.directory / 'granted.json', b'{}\n')
            elif kind == 'order': journal.returned()
            else: D.T.write_new(journal.directory / 'unknown.json', b'{}\n')
            with self.assertRaises(ValueError): D.recover_proof(self.context, NAME)
            self.assertEqual(self.fx.events, [])
            shutil.rmtree(journal.directory)

    def test_pending_original_gpu_recovery_lock_wins_without_rearming_or_receipt(self):
        journal = self.journal('granted')
        self.fx.locked = True
        with self.assertRaises(BlockingIOError): D.recover_proof(self.context, NAME)
        self.assertFalse((journal.directory / 'recovery-intent.json').exists())
        self.assertEqual(self.fx.events, [])

    def test_changed_context_does_not_adopt_old_lease(self):
        self.journal('granted')
        self.context['sourceIntentSha256'] = 'e' * 64
        with self.assertRaises(ValueError): D.recover_proof(self.context, NAME)
        self.assertEqual(self.fx.events, [])


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private enrollment fixtures')
class Enrollment(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-guard-enrollment-fixture-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name)
        self.root = parent / 'enrollment'
        self.source = self.root / 'sources'
        self.systemd, self.overlay, self.admissions = (parent / n for n in ('units', 'overlay', 'admissions'))
        for path in (self.systemd, self.overlay, self.admissions): path.mkdir(mode=0o700)
        (self.overlay / 'runtime').mkdir(mode=0o700)
        (self.overlay / 'runtime/portable').mkdir(mode=0o700)
        for name in D.OVERLAYS: D.T.write_new(self.overlay / 'runtime/portable' / name, ('# fixture ' + name + '\n').encode())
        D.T.write_new(self.admissions / (TOKEN + '.json'), D.H.encode({'transaction': TOKEN, 'kind': 'original-owned-fresh-gpu'}))
        self.calls = []
        actor = SimpleNamespace(store=SimpleNamespace(status=lambda: {'stage': 'ready'}), native=lambda: True)
        patches = (patch.object(D, 'ROOT', self.root), patch.object(D, 'SOURCE', self.source),
                   patch.object(D, 'SYSTEMD', self.systemd), patch.object(D.T, 'ADMISSIONS', self.admissions),
                   patch.object(D.T, 'machine_pin', lambda: 'd' * 64),
                   patch.object(D.T, 'base_sources', lambda: (b'fixture-original-source\n', {'original.py': b'# original\n'})),
                   patch.object(D.T, 'activation', lambda *_: (None, actor, None)),
                   patch.object(D, 'prove', lambda *_: {'originalInstallationOwnershipVerified': True}),
                   patch.object(D.G, 'command', lambda args, **_: self.calls.append(args)))
        for item in patches: item.start(); self.addCleanup(item.stop)
        self.digest = D.G.sha(D.H.encode(D.file_rows(D.overlay_files(self.overlay))))

    def prepare(self):
        return D.prepare('recovered', '/root/fixture-original-package', TOKEN, self.overlay, self.digest)

    def test_exclusive_enrollment_inspects_complete_source_and_never_starts_a_service(self):
        result = self.prepare()
        self.assertFalse(result['guardStarted'])
        self.assertFalse(result['codeApplicationAllowed'])
        self.assertFalse(D.inspect()['updatesAllowed'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1], ['/usr/bin/systemctl', 'daemon-reload'])
        with self.assertRaises(ValueError): self.prepare()

    def test_wrong_reviewed_digest_refuses_before_creating_any_enrollment(self):
        with self.assertRaises(ValueError):
            D.prepare('recovered', '/root/fixture-original-package', TOKEN, self.overlay, 'f' * 64)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.calls, [])

    def test_existing_foreign_service_is_preserved_and_no_enrollment_created(self):
        D.T.write_new(self.systemd / D.GUARD, b'# foreign\n')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.root.exists())
        self.assertEqual((self.systemd / D.GUARD).read_bytes(), b'# foreign\n')

    def test_same_byte_replacement_unit_does_not_adopt_enrolled_inode(self):
        self.prepare()
        path = self.systemd / D.GUARD
        path.rename(self.systemd / 'original-retained')
        path.write_bytes((self.systemd / 'original-retained').read_bytes())
        path.chmod(0o644)
        with self.assertRaises(ValueError): D.inspect()

    def test_changed_or_unknown_source_prevents_guard_inspection(self):
        self.prepare()
        D.T.write_new(self.source / 'unknown.py', b'# foreign\n')
        with self.assertRaises(ValueError): D.inspect()
        (self.source / 'unknown.py').unlink()
        path = self.source / 'runtime/portable/update-guard.py'
        path.write_bytes(b'# changed\n')
        with self.assertRaises(ValueError): D.inspect()


if __name__ == '__main__':
    unittest.main()
