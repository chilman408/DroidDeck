# SPDX-License-Identifier: GPL-3.0-only
"""Focused legacy admission regressions. No service, VM or real enrollment."""
from contextlib import ExitStack
import copy
import importlib.util
import os
import posixpath
import re
import subprocess
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock


def load(name):
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


K = load('aggregate-legacy-runtime.py')
L = load('aggregate-legacy-video.py')
P = load('legacy-gpu-preflight.py')
C = load('aggregate-install.py')
DIGEST = 'a' * 64


def context():
    return {'root': Path('/usr/local/share/droidmate-rc17-' + DIGEST), 'release': {
        'kind': 'droidmate-aggregate-release-authorization', 'aggregateRelease': 'RC17',
        'coreRuntimeVersion': '0.1.1-rc.20', 'compatibleReleaseSha256': L.CORE,
        'packageSha256': DIGEST, 'activationAllowed': True}, 'check': lambda: True}


class JournalModel:
    def __init__(self):
        self.root = K.ROOT / DIGEST
        self.fd = 71
        self.intent = {'schema': 1, 'identity': {'layout': 'legacy', 'packageSha256': DIGEST},
                       'rootIdentity': [1, 2, 3, 0, 0], 'transactionToken': 'b' * 32}
        self.records = {'intent.json': self.intent}
        self.phase = 'loaded'
        self.closed = False
        self.writes = []

    def guard(self):
        if self.closed:
            raise ValueError('Closed journal')
        return True

    def read(self, name):
        return copy.deepcopy(self.records[name])

    def write(self, name, value):
        self.writes.append(name)
        self.records[name] = copy.deepcopy(value)

    def state(self):
        return self.phase

    def close(self):
        self.closed = True

    def directory(self, path):
        if path != self.root:
            raise AssertionError('Unexpected path')
        return self.fd

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def engine():
    return types.SimpleNamespace(TARGETS={'video': K.VIDEO},
        directory_id=lambda s: (1, 2, 3, 0, 0), stage_public_tree=mock.Mock(return_value=True))


class NodeResolutionTests(unittest.TestCase):
    def test_extensionless_sibling_cannot_replace_reviewed_js_dependency(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'waydroid-display').mkdir();(root/'preview-gateway').mkdir()
            (root/'waydroid-display/host-adapter.js').write_text('module.exports={};')
            (root/'waydroid-display/host-adapter').write_text('throw new Error("shadowed");')
            physical=K.Physical.__new__(K.Physical)
            physical.pins={'waydroid-display/host-adapter.js':'a'*64}
            physical.held=types.SimpleNamespace(directory=lambda p:None)
            with mock.patch.object(K,'PHYSICAL',root):
                with self.assertRaisesRegex(ValueError,'extensionless'):physical.namespace()


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.stat = mock.patch.object(K.os, 'fstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700))
        self.stat.start()
        self.addCleanup(self.stat.stop)

    def guard(self):
        g = K.Admission.__new__(K.Admission)
        g.ctx, g.c, g.j = L.retain_context(context()), engine(), JournalModel()
        g.closed, g.stack = False, ExitStack()
        g.baseline = {'original': True}
        g.j.records['baseline.json'] = copy.deepcopy(g.baseline)
        g.physical = types.SimpleNamespace(check=mock.Mock(return_value=True))
        g.video = types.SimpleNamespace(verify=mock.Mock(return_value=True))
        g.gateway = types.SimpleNamespace(verify=mock.Mock(return_value=True))
        g.public_views = [types.SimpleNamespace(verify=mock.Mock(return_value=True), guard=mock.Mock(return_value=True)) for _ in range(2)]
        g.view = {'runtimeDigest': DIGEST, 'frontendDigest': 'c' * 64,
                  'runtimeFiles': {'legacy/runtime/file.js': b'public'},
                  'frontendFiles': {'frontend/index.html': b'public'}}
        return g

    def test_exact_video_override_only_changes_original_argv(self):
        raw = K.video_unit(context(), L)
        self.assertEqual(raw, ('[Service]\nExecStart=\nExecStart=' + ' '.join(L.parent_command(context())) + '\n').encode())
        self.assertNotIn(b'Bind', raw)
        self.assertNotIn(b'Environment=', raw)
        self.assertNotIn(b'PrivateMounts=', raw)
        e = engine()
        e.TARGETS = {}
        with self.assertRaises(ValueError):
            K.video_mapping(context(), e, JournalModel(), L)

    def test_guard_admits_loaded_startup_without_waiting_for_later_peers(self):
        g = self.guard()
        self.assertTrue(g.check())
        for tree in g.public_views:
            tree.verify.assert_called_once_with()
            tree.guard.assert_called_once_with()
        self.assertEqual(g.j.writes, [])
        for phase in ('new', 'intent', 'armed', 'cpu', 'stopped', 'mapped', 'recovering', 'restored'):
            g.j.phase = phase
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                g.check()

    def test_wrong_layout_root_or_lost_record_refuses(self):
        for kind in ('identity', 'root', 'rootIdentity', 'baseline'):
            g = self.guard()
            if kind == 'identity':
                g.j.intent['identity']['layout'] = 'portable'
            elif kind == 'root':
                g.j.root = K.ROOT / ('d' * 64)
            elif kind == 'rootIdentity':
                g.j.intent['rootIdentity'][1] = 999
            else:
                g.j.records['baseline.json'] = {'different': True}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                g.check()
            g.c.stage_public_tree.assert_not_called()

    def test_loss_during_last_public_check_refuses_before_dispatch(self):
        for kind in ('physical', 'video', 'gateway', 'baseline', 'phase', 'source'):
            g = self.guard()
            alive = [True]
            g.ctx = L.retain_context({**context(), 'check': lambda: alive[0]})
            calls = [0]
            def stage(*args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    if kind == 'physical':
                        g.physical.check.side_effect = ValueError('Physical source replaced')
                    elif kind in ('video', 'gateway'):
                        getattr(g, kind).verify.return_value = False
                    elif kind == 'baseline':
                        g.j.records['baseline.json'] = {'changed': True}
                    elif kind == 'phase':
                        g.j.phase = 'recovering'
                    else:
                        alive[0] = False
                return True
            for tree in g.public_views:
                tree.verify.side_effect = stage
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                g.check()
            self.assertEqual(calls[0], 2)

    def test_first_public_tree_loss_during_last_check_is_not_missed(self):
        g = self.guard()
        def replace_first():
            g.public_views[0].guard.side_effect = ValueError('First public tree replaced')
        g.public_views[1].verify.side_effect = replace_first
        with self.assertRaisesRegex(ValueError, 'First public tree replaced'):
            g.check()

    def test_corrupt_public_generation_refuses_and_closed_guard_refuses(self):
        g = self.guard()
        g.public_views[0].verify.side_effect = ValueError('Same bytes, foreign public generation')
        with self.assertRaises(ValueError):
            g.check()
        g.close()
        with self.assertRaises(ValueError):
            g.check()

    def test_context_mutation_cannot_retarget_dependencies(self):
        g = self.guard()
        original = context()
        g.ctx = L.retain_context(original)
        original['release']['packageSha256'] = 'd' * 64
        with self.assertRaises(ValueError):
            g.check()
        g.c.stage_public_tree.assert_not_called()

    def test_public_reader_releases_only_own_lock_and_retains_generation_handles(self):
        tree = types.SimpleNamespace(held={'coordination.lock': (91, (), b'')},
                                     verify=mock.Mock(), close=mock.Mock())
        factory = mock.Mock(return_value=tree)
        locking = types.SimpleNamespace(flock=mock.Mock(), LOCK_UN=8)
        with mock.patch.dict(sys.modules, {'fcntl': locking}):
            self.assertIs(K.public_reader(types.SimpleNamespace(PublicTree=factory), DIGEST, {'legacy/runtime/x': b'x'}), tree)
            factory.assert_called_once_with(K.PUBLIC / DIGEST, {'legacy/runtime/x': b'x'}, DIGEST, create=False)
            locking.flock.assert_called_once_with(91, 8)
            tree.close.assert_not_called()
            tree.verify.side_effect = ValueError('Generation changed while unlocking')
            with self.assertRaises(ValueError):
                K.public_reader(types.SimpleNamespace(PublicTree=factory), DIGEST, {})
            tree.close.assert_called_once_with()

    def test_reader_does_not_construct_locking_or_writing_journal(self):
        j = JournalModel()
        class Writer:
            def __new__(cls):
                return j
            def __init__(self, *args, **kwargs):
                raise AssertionError('Must never construct a locking writer')
        e = engine()
        e.Journal = Writer
        # Read-only setup initializes engine internals; this model keeps its
        # independently supplied records and deliberately offers no flock API.
        self.assertIs(K.read_journal(context(), e), j)
        self.assertEqual(j.writes, [])
        self.assertFalse(j.closed)
        j.records['intent.json']['transactionToken'] = 'bad'
        with self.assertRaises(ValueError):
            K.read_journal(context(), e)
        self.assertTrue(j.closed)

    def test_establish_requires_new_phase_and_never_recreates_lost_baseline(self):
        j, e = JournalModel(), engine()
        observations = []
        observer = types.SimpleNamespace(inspect=lambda: observations.append('original-process'))
        bridge = types.SimpleNamespace(retain_context=L.retain_context, helper=lambda *a: observer)
        compiler = types.SimpleNamespace(source_module=lambda *a: bridge)
        physical = types.SimpleNamespace(baseline=lambda digest: {'original': digest}, check=lambda: True)
        with mock.patch.object(K, 'Physical', return_value=physical), mock.patch.object(K, 'dependency_pins', return_value={}), \
             mock.patch.object(K.os.path, 'lexists', side_effect=lambda p: p.name in j.records):
            with self.assertRaises(ValueError):
                K.establish(context(), e, j, None, compiler)
            self.assertEqual(j.writes, [])
            j.phase = 'new'
            self.assertTrue(K.establish(context(), e, j, None, compiler))
            self.assertEqual(observations, ['original-process'])
            self.assertEqual(j.writes, ['baseline.json'])
            j.phase = 'accepted'
            self.assertTrue(K.establish(context(), e, j, None, compiler))
            self.assertEqual(observations, ['original-process'])
            j.records['baseline.json'] = {'foreign': True}
            with self.assertRaises(ValueError):
                K.establish(context(), e, j, None, compiler)
            self.assertEqual(j.writes, ['baseline.json'])

    def test_run_passes_concrete_guard_to_unchanged_rb_and_closes_handles(self):
        j = JournalModel()
        calls = []
        bridge = types.SimpleNamespace(retain_context=L.retain_context)
        compiler = types.SimpleNamespace(source_module=lambda *a: bridge)
        class Guard:
            def __init__(self, *args):
                self.ctx, self.bridge = args[0], bridge
            def check(self):
                calls.append('guard')
                return True
            def __enter__(self):
                return self
            def __exit__(self, *args):
                calls.append('closed')
        def rb(ctx, guard):
            self.assertTrue(guard())
            calls.append('RB-namespace-lifecycle')
            return {'mode': 'cpu', 'returncode': 0}
        bridge.run = rb
        with mock.patch.object(K.os, 'name', 'posix'), mock.patch.object(K.os, 'geteuid', return_value=0, create=True), \
             mock.patch.dict(K.os.environ, {}, clear=True), mock.patch.object(K, 'read_journal', return_value=j), \
             mock.patch.object(K, 'Admission', Guard):
            self.assertEqual(K.run(context(), engine(), None, compiler), {'mode': 'cpu', 'returncode': 0})
        self.assertEqual(calls, ['guard', 'guard', 'RB-namespace-lifecycle', 'closed'])
        self.assertTrue(j.closed)


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'native root Linux concurrent-journal test')
class NativeReaderTests(unittest.TestCase):
    def test_real_engine_reader_runs_while_installer_writer_holds_lock(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-legacy-journal-', dir='/var/lib') as temp:
            with mock.patch.object(K, 'ROOT', Path(temp)):
                root = K.ROOT / DIGEST
                with C.Journal(root, {'layout': 'legacy', 'packageSha256': DIGEST}, create=True) as writer:
                    for phase in C.PHASES[1:7]:
                        writer.append(phase)
                    self.assertEqual(writer.state(), 'loaded')
                    with K.read_journal(context(), C) as reader:
                        self.assertEqual(reader.state(), 'loaded')
                        writer.append('started')
                        self.assertEqual(reader.state(), 'started')



class PackageTests(unittest.TestCase):
    def test_historical_profile_cannot_be_used_as_measured_machine_authority(self):
        self.assertEqual(set(K.REVIEWED_CPU_PINS), set(K.HISTORICAL_CPU_PINS))
        self.assertEqual(len(K.CPU_CRLF_FILES), 14)
        self.assertEqual({n for n in K.REVIEWED_CPU_PINS if K.REVIEWED_CPU_PINS[n] != K.HISTORICAL_CPU_PINS[n]}, set(K.CPU_CRLF_FILES))
        with mock.patch.object(K, 'REVIEWED_CPU_PINS', {}), self.assertRaisesRegex(ValueError, 'source-reviewed'):
            K.reviewed_cpu_pins()
        measured = dict(K.HISTORICAL_CPU_PINS)
        # A genuinely reviewed dependency may differ from the historical file;
        # known QM launcher/native anchors still cannot be relabeled.
        measured['waydroid-display/frame-check.js'] = 'd' * 64
        with mock.patch.object(K, 'REVIEWED_CPU_PINS', measured):
            self.assertEqual(K.reviewed_cpu_pins()['waydroid-display/frame-check.js'], 'd' * 64)
            measured['preview-gateway/compositor-h264.py'] = 'e' * 64
            with self.assertRaises(ValueError):
                K.reviewed_cpu_pins()

    def test_reviewed_source_reproduces_from_git_and_closes_imports(self):
        root = Path(__file__).resolve().parents[1]
        if not (root / '.git').exists():
            self.skipTest('Git-free distribution: original source blob audit requires canonical repository')
        for name, blob in K.CPU_SOURCE_BLOBS.items():
            raw = subprocess.check_output(['git', '--no-replace-objects', 'cat-file', 'blob', blob], cwd=root)
            if name == 'preview-gateway/compositor-h264.py':
                self.assertEqual(K.sha(raw), '674ebfe44b9e20707f87d45f0f5cbf2283f445c174042290dda8d2eb19abe40b')
                for before, after in ((b'(1000000, 2000000, 3000000, 4000000, 8000000, 12000000)',
                        b'(450000, 720000, 1000000, 2000000, 3000000, 4000000, 8000000, 12000000)'),
                        (b'(30, 45, 60)', b'(15, 30, 45, 60)')):
                    self.assertEqual(raw.count(before), 1)
                    raw = raw.replace(before, after)
            elif name in K.CPU_CRLF_FILES:
                raw = raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            self.assertEqual(K.sha(raw), K.REVIEWED_CPU_PINS[name], name)
            if name.endswith('.js'):
                for item in re.findall(rb"require\(['\"]([^'\"]+)['\"]\)", raw):
                    dependency = item.decode('ascii')
                    if dependency.startswith('.'):
                        relative = posixpath.normpath(posixpath.join(posixpath.dirname(name), dependency))
                        relative = relative if relative.endswith('.js') else relative + '.js'
                        self.assertIn(relative, K.REVIEWED_CPU_PINS, (name, dependency))
                    else:
                        self.assertTrue(dependency.startswith('node:') or dependency == 'pngjs', (name, dependency))

    def test_immutable_reference_required_before_any_package_parser(self):
        parser = mock.Mock()
        material = types.SimpleNamespace(assets={'capturePackage': b'foreign'}, compiler=types.SimpleNamespace(capture_runtime=parser))
        with self.assertRaises(ValueError):
            K.dependency_pins(material)
        parser.assert_not_called()

    def test_package_supplies_pngjs_only_never_relabels_gpu_files_as_original_cpu(self):
        raw = b'a' * 3246080
        files = {K.PNG + n: b'public' for n in ('package.json', 'lib/png.js', 'lib/a.js', 'lib/b.js', 'LICENSE')}
        files['preview-gateway/compositor-video-launcher.js'] = b'new GPU launcher'
        material = types.SimpleNamespace(assets={'capturePackage': raw}, compiler=types.SimpleNamespace(capture_runtime=lambda _: files))
        # Explicit synthetic reviewed profile, never an assertion about .90.
        with mock.patch.object(K, 'PACKAGE', K.sha(raw)), mock.patch.object(K, 'REVIEWED_CPU_PINS', dict(K.HISTORICAL_CPU_PINS)):
            pins = K.dependency_pins(material)
            self.assertEqual(pins['preview-gateway/compositor-video-launcher.js'], P.PINS['preview-gateway/compositor-video-launcher.js'])
            self.assertEqual(pins[K.PNG + 'lib/a.js'], K.sha(b'public'))
            del files[K.PNG + 'lib/png.js']
            with self.assertRaises(ValueError):
                K.dependency_pins(material)


class ModelHeld:
    """Real local file generations; models Linux protected FD ancestry on Windows.

    This does NOT substitute for the root Linux nofollow/UID/mount proof.
    """
    def __init__(self, stack):
        self.files, self.dirs = {}, {}
    def directory(self, path):
        path = Path(path)
        if path not in self.dirs:
            self.dirs[path] = (None, P.identity(path.lstat())[:5])
        return None
    def read(self, path, limit, mode):
        self.directory(path.parent)
        self.files[path] = (None, P.identity(path.lstat()))
        return path.read_bytes()
    def verify(self):
        for p, (_, identity) in self.dirs.items():
            if P.identity(p.lstat())[:5] != identity:
                raise ValueError('Directory replaced')
        for p, (_, identity) in self.files.items():
            if P.identity(p.lstat()) != identity:
                raise ValueError('File generation changed')


class PhysicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pins = {}
        for n in ('preview-gateway/compositor-h264.py', 'waydroid-display/dependency.js', K.PNG + 'package.json', K.PNG + 'lib/png.js'):
            p = self.root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'public application fixture\n')
            self.pins[n] = K.sha(p.read_bytes())
        self.patch = mock.patch.object(K, 'PHYSICAL', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        if os.name == 'nt':
            # Windows reports writable fixture files as 0666. Model the expected
            # Linux mode here only; production still demands actual 0444/0644.
            original_mode = stat.S_IMODE
            modes = mock.patch.object(K.stat, 'S_IMODE', side_effect=lambda mode: 0o644 if original_mode(mode) == 0o666 else original_mode(mode))
            modes.start()
            self.addCleanup(modes.stop)
        self.observer = types.SimpleNamespace(Held=ModelHeld, require_no_portable=lambda: True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

    def physical(self):
        return K.Physical(self.observer, self.pins, self.stack)

    def test_real_files_detect_dependency_equal_byte_replacement(self):
        p = self.physical()
        dep = self.root / 'waydroid-display/dependency.js'
        dep.rename(dep.with_suffix('.retained'))
        dep.write_bytes(b'public application fixture\n')
        with self.assertRaises(ValueError):
            p.check()

    def test_changed_dependency_is_refused_before_baseline(self):
        (self.root / 'waydroid-display/dependency.js').write_bytes(b'foreign')
        with self.assertRaisesRegex(ValueError, 'reference differs'):
            self.physical()

    def test_pngjs_resolution_shadow_or_extra_file_refuses(self):
        p = self.physical()
        shadow = self.root / (K.PNG[:-1] + '.js')
        shadow.write_bytes(b'module.exports = {}')
        with self.assertRaisesRegex(ValueError, 'resolution shadow'):
            p.check()
        shadow.unlink()
        (self.root / K.PNG / 'foreign.js').write_bytes(b'foreign')
        with self.assertRaisesRegex(ValueError, 'Closed physical pngjs'):
            p.check()

    def test_indirect_python_import_shadow_refuses(self):
        p = self.physical()
        (self.root / 'preview-gateway/enum.py').write_bytes(b'raise RuntimeError("injected")')
        with self.assertRaisesRegex(ValueError, 'Python import shadow'):
            p.check()

    @unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0, 'native root Linux protected-file test')
    def test_native_held_root_files_reject_symlink_and_same_byte_replacement(self):
        # Root-private local fixture only, never an installed source or VM.
        with tempfile.TemporaryDirectory(prefix='droidmate-legacy-dependency-', dir='/var/lib') as temp:
            root = Path(temp)
            for n in self.pins:
                target = root / n
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'public application fixture\n')
                target.chmod(0o644)
            observer = types.SimpleNamespace(Held=P.Held, require_no_portable=lambda: True)
            with mock.patch.object(K, 'PHYSICAL', root), ExitStack() as stack:
                p = K.Physical(observer, self.pins, stack)
                dep = root / 'waydroid-display/dependency.js'
                dep.rename(dep.with_suffix('.retained'))
                dep.symlink_to(dep.with_suffix('.retained'))
                with self.assertRaises((ValueError, OSError)):
                    p.check()
                dep.unlink()
                dep.write_bytes(b'public application fixture\n')
                dep.chmod(0o644)
                with self.assertRaises(ValueError):
                    p.check()


if __name__ == '__main__':
    unittest.main()
