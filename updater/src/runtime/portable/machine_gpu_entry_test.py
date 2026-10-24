# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).parent
REPO = HERE.parents[1]
FILE = HERE / 'machine-gpu-entry.py'
E = load('machine_entry', FILE) if FILE.exists() else None
P = load('entry_peer_policy', HERE / 'machine-gpu-peer-policy.py')


class EntryRecipe(unittest.TestCase):
    def test_peer_commands_use_trusted_launcher_before_runtime_modules(self):
        for unit, mode in ((P.WATCHER, '--watch'), (P.WORKER, '--work')):
            service, _unit = P.properties(unit)
            self.assertEqual(service['ExecStartEx'][0][1],
                ['/usr/bin/python3', '-I', '-B', '/opt/droiddeck/machine-gpu-entry.py', mode])


class EntryBoundary(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(E, 'Pinned bootstrap launcher required')

    def test_bootstrap_pin_matches_canonical_accepted_source(self):
        raw = (HERE / 'machine-gpu-bootstrap.py').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), E.BOOTSTRAP_PIN)

    def test_start_modes_refuse_before_reading_any_source(self):
        with mock.patch.object(E, 'hold_runtime_sources', side_effect=AssertionError('no startup reads')):
            for args in ([], ['--watch'], ['--work'], ['--recover'], ['--force'], ['--verify-sources', '--root', '/root/foreign']):
                with self.assertRaises(ValueError):
                    E.main(args)

    def test_root_platform_paths_precede_reads(self):
        with ExitStack() as stack, mock.patch.object(E.os, 'open', side_effect=AssertionError('no reads')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(E.sys, 'platform', platform), mock.patch.object(E.os, 'geteuid', return_value=uid, create=True), self.assertRaises(ValueError):
                    E.hold_runtime_sources(stack)
            with mock.patch.object(E.sys, 'platform', 'linux'), mock.patch.object(E.os, 'geteuid', return_value=0, create=True):
                for bad in ('relative', '/', '//root/source', '/root/../source', '/root//source', '/root/source/', '/root/\x00source'):
                    for keyword in ('bootstrap', 'root'):
                        with self.assertRaises(ValueError):
                            E.hold_runtime_sources(stack, **{keyword: bad})


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux entry fixtures required')
class EntrySources(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(E, 'Pinned bootstrap launcher required')
        self.T = load('entry_bootstrap_fixtures', HERE / 'machine_gpu_bootstrap_test.py')
        self.f = self.T.BootstrapSources()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.bootstrap = self.root.parent / 'bootstrap.py'
        self.bootstrap.write_bytes((HERE / 'machine-gpu-bootstrap.py').read_bytes().replace(b'\r\n', b'\n'))
        self.bootstrap.chmod(0o600)

    def hold(self, stack):
        return E.hold_runtime_sources(stack, bootstrap=self.bootstrap, root=self.root)

    def snapshot(self):
        return self.f.f.snapshot(), (self.bootstrap.stat().st_ino, self.bootstrap.stat().st_mode, self.bootstrap.read_bytes())

    def test_readonly_ten_source_closure_and_sanitized_verification(self):
        before = self.snapshot()
        with ExitStack() as stack:
            ctx = self.hold(stack)
            ctx['check']()
            for role in self.T.B.ROLES:
                self.assertEqual(ctx['load'](role).__file__, str(self.root / self.T.B.ROLES[role]))
            policy = ctx['policy']
            self.assertTrue(policy['bootstrapSourceVerified'])
            self.assertEqual(policy['verifiedSourceCount'], 10)
            for flag in ('bootstrapPathMatched', 'installedRootMatched', 'installedEntryVerified',
                         'installedRecipeVerified', 'effectiveGraphVerified', 'independentRecoveryArmed',
                         'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'):
                self.assertFalse(policy[flag])
            self.assertNotIn(str(self.root), json.dumps(policy))
        output = io.StringIO()
        original_hold = E.hold_runtime_sources
        def fixture_sources(stack):
            return original_hold(stack, bootstrap=self.bootstrap, root=self.root)
        with mock.patch.object(E, 'hold_runtime_sources', side_effect=fixture_sources), redirect_stdout(output):
            E.main(['--verify-sources'])
        self.assertTrue(json.loads(output.getvalue())['bootstrapSourceVerified'])
        self.assertEqual(self.snapshot(), before)

    def test_wrong_size_and_same_size_bootstrap_never_execute(self):
        raw = self.bootstrap.read_bytes()
        for changed in (b'raise AssertionError("foreign bootstrap executed")\n', bytes([raw[0] ^ 1]) + raw[1:]):
            self.bootstrap.write_bytes(changed)
            with ExitStack() as stack, mock.patch.object(E, 'exec', side_effect=AssertionError('unverified execution'), create=True), self.assertRaises(ValueError):
                self.hold(stack)

    def test_mode_owner_hardlink_and_symlink_refuse_without_repair(self):
        self.bootstrap.chmod(0o644)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        self.assertEqual(self.bootstrap.stat().st_mode & 0o777, 0o644)
        self.bootstrap.chmod(0o600)
        os.chown(self.bootstrap, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        os.chown(self.bootstrap, 0, 0)
        target = self.bootstrap.with_name('target')
        os.link(self.bootstrap, target)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.hold(stack)
        target.unlink()
        self.bootstrap.rename(target)
        self.bootstrap.symlink_to(target)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            self.hold(stack)

    def test_same_bytes_bootstrap_replacement_invalidates_all_contexts(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            replacement = self.bootstrap.with_name('replacement')
            replacement.write_bytes(self.bootstrap.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, self.bootstrap)
            with self.assertRaises(ValueError):
                ctx['check']()
            with self.assertRaises(ValueError):
                ctx['load']('state')

    def test_bootstrap_mutation_during_compile_never_executes_foreign_bytes(self):
        original_compile = compile
        def raced(raw, filename, mode, *args, **kwargs):
            self.bootstrap.write_bytes(b'raise AssertionError("foreign bootstrap executed")\n')
            return original_compile(raw, filename, mode, *args, **kwargs)
        with ExitStack() as stack, mock.patch('builtins.compile', side_effect=raced), self.assertRaises(ValueError):
            self.hold(stack)

    def test_component_change_invalidates_outer_context(self):
        with ExitStack() as stack:
            ctx = self.hold(stack)
            file = self.root / 'runtime/portable/machine-gpu-lifecycle.py'
            file.write_bytes(file.read_bytes() + b' ')
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_leaf_alias_and_closed_stack_refuse(self):
        alias = self.root.parent / 'alias'
        alias.symlink_to(self.bootstrap)
        with ExitStack() as stack, self.assertRaises((OSError, ValueError)):
            E.hold_runtime_sources(stack, bootstrap=alias, root=self.root)
        with ExitStack() as stack:
            ctx = self.hold(stack)
        with self.assertRaises((OSError, ValueError)):
            ctx['check']()

    def test_bootstrap_only_ancestor_replacement_invalidates_check_and_load(self):
        directory = self.root.parent / 'bootstrap-source'
        directory.mkdir(mode=0o700)
        file = directory / 'bootstrap.py'
        file.write_bytes(self.bootstrap.read_bytes())
        file.chmod(0o600)
        before = self.f.f.snapshot()
        with ExitStack() as stack:
            ctx = E.hold_runtime_sources(stack, bootstrap=file, root=self.root)
            directory.rename(directory.with_name('displaced-bootstrap-source'))
            directory.mkdir(mode=0o700)
            file.write_bytes(self.bootstrap.read_bytes())
            file.chmod(0o600)
            with self.assertRaises(ValueError):
                ctx['check']()
            with self.assertRaises(ValueError):
                ctx['load']('state')
        self.assertEqual(self.f.f.snapshot(), before)

    def test_unsafe_parent_refuses_before_bootstrap_execution(self):
        parent = self.bootstrap.parent
        parent.chmod(0o777)
        try:
            with ExitStack() as stack, mock.patch.object(E, 'exec', side_effect=AssertionError('untrusted parent execution'), create=True), self.assertRaises(ValueError):
                self.hold(stack)
        finally:
            parent.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
