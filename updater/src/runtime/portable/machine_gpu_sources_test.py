# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('machine_gpu_sources', Path(__file__).with_name('machine-gpu-sources.py'))
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)
REPO = Path(__file__).resolve().parents[2]


class SourceBoundary(unittest.TestCase):
    def test_closed_pins_match_canonical_tracked_source(self):
        self.assertEqual(len(S.PINS), 6)
        for relative, (size, digest) in S.PINS.items():
            raw = (REPO / relative).read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest), relative)
        self.assertEqual(set(S.ROLES), {'recovery', 'supervision', 'units', 'health'})

    def test_root_linux_precedes_directory_read(self):
        copier = mock.Mock()
        copier.canonical.side_effect = AssertionError('must not inspect')
        with ExitStack() as stack:
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(S.sys, 'platform', platform), mock.patch.object(S.os, 'geteuid', return_value=uid, create=True), self.assertRaises(ValueError):
                    S.hold_component_sources(copier, stack)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated protected source fixtures required')
class ProtectedSources(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('source_peer_fixtures', Path(__file__).with_name('machine_gpu_supervision_test.py'))
        self.T = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.T)
        self.peers = self.T.PeerLifetimes()
        self.addCleanup(self.peers.doCleanups)
        self.temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-source-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'source'
        for relative in S.INVENTORY:
            (self.root / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.root / relative).chmod(0o700)
        for relative in S.PINS:
            file = self.root / relative
            file.write_bytes((REPO / relative).read_bytes().replace(b'\r\n', b'\n'))
            file.chmod(0o600)
        spec = importlib.util.spec_from_file_location('source_fixture_copier', REPO / 'runtime/portable/stage-images.py')
        self.copier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.copier)

    def hold(self, stack):
        return S.hold_component_sources(self.copier, stack, self.root)

    def snapshot(self):
        return {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.read_bytes() if p.is_file() else None)
                for p in self.root.rglob('*')}

    def test_readonly_exact_source_admission_and_sanitized_policy(self):
        before = self.snapshot()
        with ExitStack() as stack, mock.patch.object(S.os, 'chmod', side_effect=AssertionError('must not repair')):
            ctx = self.hold(stack)
            ctx['check']()
            policy = ctx['policy']
            self.assertTrue(policy['componentSourcesVerified'])
            self.assertEqual(policy['componentCount'], 6)
            for key in ('installedRootMatched', 'installedRecipeVerified', 'effectiveGraphVerified',
                        'independentRecoveryArmed', 'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
                self.assertFalse(policy[key])
            self.assertNotIn(str(self.root), json.dumps(policy))
        self.assertEqual(self.snapshot(), before)

    def test_load_only_closed_already_verified_modules_without_commands(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            with mock.patch.object(subprocess, 'run', side_effect=AssertionError('no commands during load')):
                for role in S.ROLES:
                    module = ctx['load'](role)
                    self.assertEqual(module.__file__, str(self.root / S.ROLES[role]))
                for role in ('../foreign', 'worker', True, 'stage-images.py'):
                    with self.assertRaises(ValueError):
                        ctx['load'](role)

    def test_missing_extra_or_wrong_bytes_never_execute(self):
        file = self.root / S.ROLES['supervision']
        raw = file.read_bytes()
        for encoded in (raw + b' ', raw.replace(b'pidfd_open', b'pidfd_FAIL'), b'raise AssertionError("must not execute")\n'):
            file.write_bytes(encoded)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
        file.write_bytes(raw)
        file.unlink()
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        file.write_bytes(raw)
        file.chmod(0o600)
        for relative in ('foreign.pem', 'runtime/extra', 'runtime/portable/extra', 'tools/extra'):
            extra = self.root / relative
            extra.write_bytes(b'generated foreign')
            before = self.snapshot()
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(self.snapshot(), before)
            extra.unlink()

    def test_wrong_file_mode_owner_or_hardlink_refuses_without_repair(self):
        file = self.root / S.ROLES['supervision']
        for mode in (0o644, 0o666, 0o700):
            file.chmod(mode)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(file.stat().st_mode & 0o777, mode)
        file.chmod(0o600)
        os.chown(file, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(file, 0, 0)
        linked = self.root.parent / 'hardlink'
        os.link(file, linked)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_unsafe_directory_or_symlink_is_not_followed(self):
        for relative in S.INVENTORY:
            directory = self.root / relative
            directory.chmod(0o755)
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            directory.chmod(0o700)
        file = self.root / S.ROLES['health']
        target = self.root.parent / 'displaced'
        file.rename(target)
        file.symlink_to(target)
        with ExitStack() as stack, self.assertRaises(OSError):
            self.hold(stack)
        self.assertTrue(file.is_symlink())

    def test_same_bytes_file_replacement_invalidates_held_descriptor(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            file = self.root / S.ROLES['supervision']
            replacement = self.root.parent / 'replacement'
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, file)
            with self.assertRaises(ValueError):
                ctx['check']()
            with self.assertRaises(ValueError):
                ctx['load']('supervision')

    def test_same_bytes_directory_replacement_invalidates_held_chain(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            original = self.root / 'tools'
            original.rename(self.root.parent / 'displaced-tools')
            original.mkdir(mode=0o700)
            for relative in S.PINS:
                if relative.startswith('tools/'):
                    file = self.root / relative
                    file.write_bytes((REPO / relative).read_bytes().replace(b'\r\n', b'\n'))
                    file.chmod(0o600)
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_loader_uses_held_verified_bytes_not_reopened_replacement(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            file = self.root / S.ROLES['health']
            raw = file.read_bytes()
            compile_original = compile
            compiled = []
            def replaced(data, filename, mode):
                compiled.append(data)
                replacement = self.root.parent / 'replacement'
                replacement.write_bytes(b'raise AssertionError("foreign code executed")\n')
                replacement.chmod(0o600)
                os.replace(replacement, file)
                return compile_original(data, filename, mode)
            with mock.patch('builtins.compile', side_effect=replaced), self.assertRaises(ValueError):
                ctx['load']('health')
            self.assertEqual(compiled, [raw])
            self.assertIn(b'foreign code executed', file.read_bytes())

    def test_held_bytes_inventory_and_mode_changes_refuse(self):
        for mutation in ('bytes', 'inventory', 'mode'):
            with ExitStack() as stack:
                ctx = self.hold(stack)
                file = self.root / S.ROLES['health']
                raw = file.read_bytes()
                if mutation == 'bytes':
                    file.write_bytes(raw.replace(b'recover', b'foreign'))
                elif mutation == 'inventory':
                    file = self.root / 'tools/extra'
                    file.write_bytes(b'generated')
                else:
                    file.chmod(0o644)
                with self.assertRaises(ValueError):
                    ctx['check']()
            if mutation == 'bytes':
                file.write_bytes(raw)
            elif mutation == 'inventory':
                file.unlink()
            else:
                file.chmod(0o600)

    def test_verified_worker_composes_real_pidfds_with_source_guard(self):
        _, _, kwargs = self.peers.peers()
        kwargs.pop('admit')
        with ExitStack() as stack:
            held = S.hold_verified_worker(self.copier, stack, self.root, **kwargs)
            held['check']()
            self.assertTrue(held['componentSourcesVerified'])
            self.assertTrue(held['peerLifetimeGuardHeld'])
            self.assertFalse(held['independentRecoveryArmed'])
            file = self.root / S.ROLES['health']
            file.write_bytes(file.read_bytes() + b' ')
            with self.assertRaises(ValueError):
                held['check']()

    def test_changed_component_cannot_open_peer_handles(self):
        _, _, kwargs = self.peers.peers()
        kwargs.pop('admit')
        file = self.root / S.ROLES['supervision']
        file.write_bytes(file.read_bytes() + b' ')
        with ExitStack() as stack, mock.patch.object(os, 'pidfd_open', side_effect=AssertionError('must not open')), self.assertRaises(ValueError):
            S.hold_verified_worker(self.copier, stack, self.root, **kwargs)

    def test_source_change_after_owned_action_stops_controller_before_next_action(self):
        f, p = self.peers.recovery()
        _, _, kwargs = self.peers.peers()
        kwargs.pop('admit')
        file = self.root / S.ROLES['health']
        rename = p.rename_exclusive
        actions = []
        def changed(*args, **kw):
            rename(*args, **kw)
            actions.append(args[1:])
            file.write_bytes(file.read_bytes() + b' ')
        with ExitStack() as stack:
            held = S.hold_verified_worker(self.copier, stack, self.root, **kwargs)
            with mock.patch.object(p, 'rename_exclusive', side_effect=changed), self.assertRaises(p.PromotionUncertain):
                f.run_recovery(supervised=held['check'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(json.loads(f.journal.record.read_bytes())['sequence'], 0)
        self.assertTrue(f.status_fixture.status()['checkpointRequired'])


if __name__ == '__main__':
    unittest.main()
