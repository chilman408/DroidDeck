# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
REPO = HERE.parents[1]
FILE = HERE / 'machine-gpu-bootstrap.py'
B = load('bootstrap_under_test', FILE) if FILE.exists() else None


class BootstrapBoundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(B, 'Independent source bootstrap implementation required')

    def test_fixed_source_recipe_matches_canonical_repository_bytes(self):
        self.assertEqual(len(B.PINS), 9)
        for relative, (size, digest) in B.PINS.items():
            raw = (REPO / relative).read_bytes().replace(b'\r\n', b'\n')
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest), relative)
        self.assertEqual(set(B.ROLES), {'copy', 'recovery', 'supervision', 'units', 'health', 'peers', 'lifecycle', 'state'})

    def test_root_platform_and_canonical_path_precede_reads(self):
        with ExitStack() as stack, mock.patch.object(B.os, 'open', side_effect=AssertionError('no source reads')), \
                mock.patch.object(B.os, 'O_DIRECTORY', getattr(B.os, 'O_DIRECTORY', 0), create=True), \
                mock.patch.object(B.os, 'O_NOFOLLOW', getattr(B.os, 'O_NOFOLLOW', 0), create=True):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(B.sys, 'platform', platform), mock.patch.object(B.os, 'geteuid', return_value=uid, create=True), self.assertRaises(ValueError):
                    B.hold_bootstrap_sources(stack)
            with mock.patch.object(B.sys, 'platform', 'linux'), mock.patch.object(B.os, 'geteuid', return_value=0, create=True):
                for path in ('relative', '/root/../source', '/root//source', '//root/source', '/root/source/', '/', '/root/\x00source'):
                    with self.assertRaises(ValueError):
                        B.hold_bootstrap_sources(stack, path)

    def test_cli_does_not_read_or_activate(self):
        with mock.patch('builtins.open', side_effect=AssertionError('no CLI reads')):
            for args in ([], ['--watch'], ['--work'], ['--install'], ['--force']):
                with self.assertRaises(ValueError):
                    B.main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux bootstrap fixtures required')
class BootstrapSources(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(B, 'Independent source bootstrap implementation required')
        self.C = load('bootstrap_source_fixtures', HERE / 'machine_gpu_sources_test.py')
        self.f = self.C.ProtectedSources()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        for relative in ('runtime/portable/machine-gpu-peer-policy.py',
                         'runtime/portable/machine-gpu-lifecycle.py',
                         'runtime/portable/machine-gpu-lifecycle-state.py'):
            file = self.root / relative
            file.write_bytes((REPO / relative).read_bytes().replace(b'\r\n', b'\n'))
            file.chmod(0o600)

    def hold(self, stack):
        return B.hold_bootstrap_sources(stack, self.root)

    def test_exact_readonly_admission_and_closed_loads(self):
        before = self.f.snapshot()
        with ExitStack() as stack, mock.patch.object(B.os, 'chmod', side_effect=AssertionError('no repair')):
            ctx = self.hold(stack)
            with mock.patch.object(subprocess, 'run', side_effect=AssertionError('no subprocess')):
                for role in B.ROLES:
                    module = ctx['load'](role)
                    self.assertEqual(module.__file__, str(self.root / B.ROLES[role]))
                for role in ('../entry', True, 'worker', 'bootstrap'):
                    with self.assertRaises(ValueError):
                        ctx['load'](role)
            self.assertTrue(ctx['policy']['bootstrapComponentSourcesVerified'])
            self.assertEqual(ctx['policy']['componentCount'], 9)
            for flag in ('installedRootMatched', 'installedEntryVerified', 'installedRecipeVerified',
                         'effectiveGraphVerified', 'independentRecoveryArmed', 'servicesActivated',
                         'androidOperationsExecuted', 'installable', 'readyForActivation'):
                self.assertFalse(ctx['policy'][flag])
            self.assertNotIn(str(self.root), json.dumps(ctx['policy']))
        self.assertEqual(self.f.snapshot(), before)

    def test_every_component_wrong_bytes_refuses_before_execution(self):
        for relative in B.PINS:
            file = self.root / relative
            raw = file.read_bytes()
            for changed in (b'raise AssertionError("foreign source must not execute")\n', bytes([raw[0] ^ 1]) + raw[1:]):
                file.write_bytes(changed)
                with ExitStack() as stack, \
                        mock.patch.object(B, 'exec', side_effect=AssertionError('executed before full admission'), create=True), \
                        self.assertRaises(ValueError):
                    self.hold(stack)
            file.write_bytes(raw)

    def test_extra_and_missing_names_preserved(self):
        for relative in ('extra', 'runtime/extra', 'runtime/portable/extra', 'tools/extra'):
            extra = self.root / relative
            extra.write_bytes(b'generated sentinel')
            before = self.f.snapshot()
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.hold(stack)
            self.assertEqual(self.f.snapshot(), before)
            extra.unlink()
        file = self.root / B.ROLES['state']
        file.rename(file.with_name('foreign'))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_unsafe_modes_ownership_links_and_directory_are_not_repaired(self):
        file = self.root / B.ROLES['lifecycle']
        file.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.assertEqual(file.stat().st_mode & 0o777, 0o644)
        file.chmod(0o600)
        os.chown(file, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(file, 0, 0)
        link = self.root.parent / 'hardlink'
        os.link(file, link)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        link.unlink()
        directory = self.root / 'runtime'
        directory.chmod(0o755)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)

    def test_file_symlink_and_symlink_ancestor_refuse(self):
        file = self.root / B.ROLES['state']
        target = self.root.parent / 'target'
        file.rename(target)
        file.symlink_to(target)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            self.hold(stack)
        alias = self.root.parent / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            B.hold_bootstrap_sources(stack, alias)

    def test_same_bytes_file_and_directory_replacement_refuse(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            file = self.root / B.ROLES['state']
            replacement = self.root.parent / 'replacement'
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, file)
            with self.assertRaises(ValueError):
                ctx['check']()
        with ExitStack() as stack:
            ctx = self.hold(stack)
            self.root.rename(self.root.with_name('old-source'))
            self.root.mkdir(mode=0o700)
            with self.assertRaises((OSError, ValueError)):
                ctx['check']()

    def test_source_change_during_compile_never_executes_foreign_bytes(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            file = self.root / B.ROLES['lifecycle']
            original_compile = compile
            def raced(raw, filename, mode, *args, **kwargs):
                file.write_bytes(b'raise AssertionError("foreign code executed")\n')
                return original_compile(raw, filename, mode, *args, **kwargs)
            with mock.patch('builtins.compile', side_effect=raced), self.assertRaises(ValueError):
                ctx['load']('lifecycle')

    def test_closed_stack_invalidates_context(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
        with self.assertRaises((OSError, ValueError)):
            ctx['check']()

    def test_bootstrapped_watcher_store_and_owned_controller_complete(self):
        T = load('bootstrap_lifecycle_fixtures', HERE / 'machine_gpu_lifecycle_test.py')
        native = T.NativeLifecycle()
        self.addCleanup(native.doCleanups)
        with ExitStack() as stack:
            ctx = self.hold(stack)
            m, admitted, _peer, controller, _p = native.fixture(stack)
            m.sources = ctx
            lifecycle, state = ctx['load']('lifecycle'), ctx['load']('state')
            parent = self.root.parent / 'state'
            parent.mkdir(mode=0o700)
            args = (ctx['copier'], stack, ctx, lifecycle, m.expected, parent)
            kwargs = {'admit': admitted['check'], 'current_boot': lambda: m.boot, 'clock': lambda: m.now}
            state.prepare(*args, **kwargs)
            store = state.Store(*args, verify_recovery=controller.status_fixture.status, **kwargs)
            store.advance(store.read(), 'recovery-required')
            watcher = lifecycle.Watcher(ctx, m.expected, admit=admitted['check'], read_status=store.read,
                native=lambda: True, stop_worker=m.stopped_worker, worker_stopped=lambda: m.worker_quiet,
                stop_guest=m.stopped_guest, guest_stopped=lambda: m.guest_quiet,
                recover=lambda guard: controller.run_recovery(supervised=guard), mark_restored=store.mark_restored,
                notify=lambda: None, current_boot=lambda: m.boot, clock=lambda: m.now, sleep=lambda delay: None)
            watcher.arm()
            self.assertEqual(watcher.tick()['action'], 'recovered')
            self.assertEqual(store.read()['stage'], 'restored')
            self.assertEqual(json.loads(controller.journal.record.read_bytes())['sequence'], 5)


if __name__ == '__main__':
    unittest.main()
