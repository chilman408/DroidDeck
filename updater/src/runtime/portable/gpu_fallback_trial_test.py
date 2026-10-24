# SPDX-License-Identifier: GPL-3.0-only
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('fallback_trial_tested', Path(__file__).with_name('gpu-fallback-trial.py'))
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)
TOKEN = '012345abcdef'
BOOT = '01234567-1234-1234-1234-123456789abc'
QUIET = {'ActiveState': 'inactive', 'MainPID': '0', 'ControlPID': '0'}


class Policy(unittest.TestCase):
    def test_closed_token_and_source_record_paths(self):
        for token in ('', '../escape', 'A' * 12, '0' * 13, ['0' * 12]):
            with self.assertRaises(ValueError): T.layout(token)
        good = {'path': 'runtime/portable/generated.py', 'size': 1, 'sha256': 'a' * 64}
        self.assertEqual(T.records([good]), {good['path']: good})
        for rows in ([], [good, good], [{**good, 'size': True}], [{**good, 'size': 1048577}],
                     [{**good, 'path': '../escape'}], [{**good, 'path': '/absolute'}],
                     [{**good, 'path': 'runtime//generated.py'}], [{**good, 'extra': True}]):
            with self.assertRaises(ValueError): T.records(rows)

    def test_instance_override_is_exact_scoped_and_never_replays_worker(self):
        value = T.dropin(TOKEN).decode()
        self.assertIn('--watch-trial ' + TOKEN, value)
        self.assertIn('ExecStart=\n', value)
        self.assertIn('Restart=no\n', value)
        for forbidden in ('--activate', '--admit', 'RuntimeMaxSec', 'reset-failed', 'Environment='):
            self.assertNotIn(forbidden, value)

    def test_wrong_mode_refuses_before_any_filesystem_mutation(self):
        with patch.object(T, 'prepare') as prepare, patch.object(T, 'watch') as watch:
            for args in ([], ['--force', TOKEN], ['--prepare-trial', TOKEN], ['--watch-trial', TOKEN, 'extra']):
                with self.assertRaises(ValueError): T.main(args)
        prepare.assert_not_called()
        watch.assert_not_called()


@unittest.skipUnless(os.geteuid() == 0, 'Root Linux original-owned trial fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='droiddeck-trial-fixture-', dir='/root')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for key, name in (('BASE', 'base'), ('SYSTEMD', 'systemd'), ('ADMISSIONS', 'admissions')):
            directory = self.root / name
            directory.mkdir(mode=0o700)
            p = patch.object(T, key, directory)
            p.start(); self.addCleanup(p.stop)
        self.supplied = self.root / 'supplied'
        self.supplied.mkdir(mode=0o700)
        p = patch.object(T, 'ROOT', self.root / 'trials')
        p.start(); self.addCleanup(p.stop)
        machine = self.root / 'machine-id'
        machine.write_bytes(b'1' * 32 + b'\n'); machine.chmod(0o644)
        p = patch.object(T, 'MACHINE', machine)
        p.start(); self.addCleanup(p.stop)
        self.put(T.SYSTEMD / 'droiddeck-fresh-gpu-watch@.service', b'original watcher template\n')
        p = patch.object(T, 'WATCH_PIN', T.G.sha(b'original watcher template\n'))
        p.start(); self.addCleanup(p.stop)
        self.original_files = {'runtime/portable/generated.py': b'original source\n', 'runtime/portable/empty.py': b''}
        for name, raw in self.original_files.items(): self.put(T.BASE / name, raw)
        self.base_intent = {'schema': 1, 'packageManifestSha256': T.BASE_MANIFEST, 'root': str(T.BASE),
            'machineSha256': T.machine_pin(), 'rootIdentity': T.identity(T.BASE),
            'files': [{'path': name, 'size': len(raw), 'sha256': T.G.sha(raw)} for name, raw in sorted(self.original_files.items())]}
        self.put(T.BASE / 'intent.json', T.H.encode(self.base_intent))
        self.put(T.BASE / 'complete.json', T.H.encode(self.base_intent))
        self.admitted = {'version': 1, 'kind': 'original-owned-fresh-gpu', 'transaction': TOKEN,
            'configuration': {}, 'metadataSha256': 'a' * 64, 'guestReceiptSha256': 'b' * 64, 'dataDevice': 1, 'dataInode': 2}
        self.put(T.ADMISSIONS / (TOKEN + '.json'), T.H.encode(self.admitted))
        updates = {name: b'reviewed candidate ' + name.encode() for name in T.UPDATES}
        for name, raw in updates.items(): self.put(self.supplied / name, raw)
        p = patch.object(T, 'UPDATES', {name: T.G.sha(raw) for name, raw in updates.items()})
        p.start(); self.addCleanup(p.stop)
        self.active = SimpleNamespace(store=SimpleNamespace(status=lambda: {'stage': 'ready', 'boot': BOOT}, lock=lambda: nullcontext()),
            native=lambda: True, recover=Mock(), resume_boot=Mock(), watch=Mock())
        self.module = SimpleNamespace(G=SimpleNamespace(WORKER='original-worker'), notify=Mock())
        for obj, name, options in ((T, 'activation', {'return_value': (self.module, self.active, 'original-policy')}),
                (T, 'unit_layout', {}), (T.G, 'boot_id', {'return_value': BOOT}),
                (T.G, 'unit', {'side_effect': lambda name: QUIET if name == 'original-worker' else
                    {'ActiveState': 'active', 'MainPID': str(os.getpid()), 'ControlPID': '0'}})):
            p = patch.object(obj, name, **options)
            p.start(); self.addCleanup(p.stop)

    def put(self, path, raw):
        parent = self.root
        for part in path.relative_to(self.root).parts[:-1]:
            parent /= part
            parent.mkdir(mode=0o700, exist_ok=True)
        path.write_bytes(raw); path.chmod(0o600)

    def prepare(self):
        return T.prepare(TOKEN, self.supplied)

    def test_new_publication_is_readonly_to_original_and_inspects_exact_candidate(self):
        result = self.prepare()
        self.assertFalse(result['servicesActivated'])
        source = T.inspect(TOKEN)
        for name, raw in self.original_files.items(): self.assertEqual((T.BASE / name).read_bytes(), raw)
        self.assertEqual((source / 'runtime/portable/generated.py').read_bytes(), b'original source\n')
        self.assertEqual((source / 'runtime/portable/empty.py').stat().st_size, 0)
        self.assertEqual(T.layout(TOKEN)[3].read_bytes(), T.dropin(TOKEN))
        self.active.recover.assert_not_called()

    def test_existing_trial_and_override_are_never_adopted_or_overwritten(self):
        self.prepare()
        override = T.layout(TOKEN)[3]
        before = override.stat().st_ino
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(override.stat().st_ino, before)

    def test_bad_candidate_bytes_refuse_before_new_directories(self):
        next(iter(self.supplied.rglob('*.py'))).write_bytes(b'foreign candidate')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(T.ROOT.exists())

    def test_foreign_or_incomplete_original_source_refuses_without_adoption(self):
        (T.BASE / 'runtime/portable/generated.py').write_bytes(b'changed')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(T.ROOT.exists())

    def test_empty_source_fifo_is_nonblocking_and_refused(self):
        path = T.BASE / 'runtime/portable/empty.py'
        path.unlink(); os.mkfifo(path, mode=0o600)
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(T.ROOT.exists())

    def test_same_byte_replacement_override_inode_is_refused(self):
        self.prepare()
        override = T.layout(TOKEN)[3]
        replacement = override.with_name('replacement')
        self.put(replacement, override.read_bytes())
        replacement.replace(override)
        with self.assertRaises(ValueError): T.inspect(TOKEN)

    def test_candidate_tampering_and_unknown_sources_are_refused(self):
        self.prepare()
        source = T.layout(TOKEN)[2]
        self.put(source / 'runtime/portable/foreign.py', b'unknown source')
        with self.assertRaises(ValueError): T.inspect(TOKEN)

    def test_changed_admission_or_machine_identity_cannot_be_used(self):
        self.prepare()
        (T.ADMISSIONS / (TOKEN + '.json')).write_bytes(T.H.encode({**self.admitted, 'dataInode': 3}))
        with self.assertRaises(ValueError): T.inspect(TOKEN)
        self.put(T.ADMISSIONS / (TOKEN + '.json'), T.H.encode(self.admitted))
        T.MACHINE.write_bytes(b'2' * 32 + b'\n')
        with self.assertRaises(ValueError): T.inspect(TOKEN)

    def test_first_supervised_recovery_is_exclusive_and_next_start_only_watches(self):
        self.prepare()
        source = T.layout(TOKEN)[2]
        with patch.object(T, '__file__', str(source / T.SELF)):
            T.watch(TOKEN)
            T.watch(TOKEN)
        self.assertEqual(self.active.recover.call_count, 1)
        self.active.resume_boot.assert_called_once_with()
        self.active.watch.assert_called_once_with('original-policy')

    def test_uncertain_start_record_cannot_be_replayed_as_first_trial(self):
        self.prepare()
        source = T.layout(TOKEN)[2]
        trial = T.layout(TOKEN)[1]
        self.put(trial / 'started.json', T.H.encode({'version': 1, 'transaction': TOKEN, 'boot': BOOT}))
        with patch.object(T, '__file__', str(source / T.SELF)): T.watch(TOKEN)
        self.active.recover.assert_not_called()
        self.active.watch.assert_called_once()

    def test_foreign_supervisor_cannot_enter_restoration(self):
        self.prepare()
        source = T.layout(TOKEN)[2]
        with patch.object(T, '__file__', str(source / T.SELF)), patch.object(T.G, 'unit',
                return_value={'ActiveState': 'active', 'MainPID': '2', 'ControlPID': '0'}):
            with self.assertRaises(ValueError): T.watch(TOKEN)
        self.module.notify.assert_not_called()
        self.active.recover.assert_not_called()

    def retiring(self):
        self.prepare()
        work = self.root / 'waydroid'
        work.mkdir(mode=0o700)
        self.put(work / 'waydroid.cfg', b'original restored CPU')
        (work / 'waydroid.cfg').chmod(0o644)
        self.active.store.status = lambda: {'stage': 'restored', 'boot': BOOT}
        self.stopped = False
        def command(args, **kwargs):
            if args[:2] == ['/usr/bin/systemctl', 'stop']:
                self.stopped = True
            return SimpleNamespace(returncode=0, stdout=b'')
        for obj, name, options in ((T.G, 'WORK', {'new': work}),
                (T.G, 'CPU', {'new': T.G.sha(b'original restored CPU')}),
                (T.G, 'command', {'side_effect': command}),
                (T.G, 'unit', {'side_effect': lambda name: QUIET if self.stopped else
                    {'ActiveState': 'active', 'MainPID': str(os.getpid()), 'ControlPID': '0'}})):
            p = patch.object(obj, name, **options)
            p.start(); self.addCleanup(p.stop)

    def test_retirement_removes_only_owned_override_and_keeps_all_source_and_journals(self):
        self.retiring()
        result = T.retire(TOKEN)
        self.assertTrue(result['workersStopped'])
        self.assertFalse(result['gpuActivationReplayed'])
        trial = T.layout(TOKEN)[1]
        self.assertTrue((trial / 'intent.json').exists())
        self.assertTrue((trial / 'sources' / T.SELF).exists())
        self.assertTrue((trial / 'retired.json').exists())
        self.assertFalse(T.layout(TOKEN)[3].parent.exists())
        self.assertTrue(any(call.args[0] == ['/usr/bin/systemctl', 'stop', *T.RETIRE_UNITS]
                            for call in T.G.command.call_args_list))
        self.assertIn('droiddeck-browser-connection.service', T.RETIRE_UNITS)
        for name, raw in self.original_files.items(): self.assertEqual((T.BASE / name).read_bytes(), raw)

    def test_retirement_refuses_replaced_override_before_any_stop_command(self):
        self.retiring()
        override = T.layout(TOKEN)[3]
        replacement = override.with_name('replacement')
        self.put(replacement, override.read_bytes()); replacement.replace(override)
        with self.assertRaises(ValueError): T.retire(TOKEN)
        T.G.command.assert_not_called()
        self.assertTrue(override.exists())

    def test_nonrestored_state_cannot_be_retired_to_bypass_gpu_recovery(self):
        self.retiring()
        self.active.store.status = lambda: {'stage': 'ready', 'boot': BOOT}
        with self.assertRaises(ValueError): T.retire(TOKEN)
        T.G.command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
