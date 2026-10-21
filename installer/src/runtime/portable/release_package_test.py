# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('release_package_tested', Path(__file__).with_name('release-package.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Package(unittest.TestCase):
    def setUp(self):
        self.files = {name: ('fixture ' + name + '\n').encode() for name in sorted(D.REQUIRED)}
        self.revision = '1' * 40
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_archive_is_deterministic_and_normalized(self):
        first, receipt = D.render(self.files, self.revision)
        second, same = D.render(dict(reversed(list(self.files.items()))), self.revision)
        self.assertEqual((first, receipt), (second, same))
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['wholeInstallCommandAvailable'])
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:') as archive:
            entries = archive.getmembers()
            self.assertEqual([e.name for e in entries], sorted(e.name for e in entries))
            for entry in entries:
                self.assertTrue(entry.isfile())
                self.assertEqual((entry.uid, entry.gid, entry.mtime, entry.uname, entry.gname), (0, 0, 0, '', ''))
                self.assertEqual(entry.mode, 0o755 if entry.name.endswith('/droiddeck-install') else 0o644)
            manifest = json.load(archive.extractfile(D.ROOT + 'package-manifest.json'))
            self.assertFalse(manifest['servicesActivated'])
            for row in manifest['files']:
                raw = archive.extractfile(D.ROOT + row['path']).read()
                self.assertEqual((len(raw), D.sha(raw)), (row['size'], row['sha256']))
        self.assertEqual(first[257:263], b'ustar\0')

    def test_private_binary_paths_missing_required_and_collisions_refuse(self):
        for name in ('runtime/data/system/locksettings.db', 'runtime/private.pem',
                     'runtime/portable/../../secret.py', 'runtime/images/system.img',
                     'runtime/other.png', 'runtime/app.apk', 'runtime/link.so', 'C:/private.py'):
            with self.assertRaises(ValueError):
                D.render({**self.files, name: b'private fixture'}, self.revision)
        files = self.files.copy()
        files.pop(next(iter(D.REQUIRED)))
        with self.assertRaises(ValueError):
            D.render(files, self.revision)
        with self.assertRaises(ValueError):
            D.render({**self.files, 'runtime/X.py': b'x', 'runtime/x.py': b'y'}, self.revision)

    def test_launcher_help_is_valid_python_and_does_not_run_phases(self):
        compile(D.LAUNCHER, 'droiddeck-install', 'exec')
        launcher = self.root / 'droiddeck-install'
        launcher.write_bytes(D.LAUNCHER)
        for args, code in ((['--help'], 0), (['--force'], 1), ([], 1)):
            result = subprocess.run([sys.executable, '-I', '-B', str(launcher), *args],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, code)
        self.assertIn(b'not a complete installer', subprocess.run(
            [sys.executable, '-I', '-B', str(launcher), '--help'], capture_output=True, timeout=10).stdout)

    def test_launcher_routes_only_supported_exact_phases_without_activation(self):
        launcher = self.root / 'droiddeck-install'
        routes = [
            *[([mode], 'host-prerequisites.py', None) for mode in
              ('--install-host', '--resume-host', '--inspect-host')],
            *[([mode, 'u'], 'prepare-user-session.py', None) for mode in
              ('--prepare-user-session', '--resume-user-session', '--inspect-user-session')],
            *[([mode], 'stage-installer-sources.py', None) for mode in
              ('--stage-installer-sources', '--resume-installer-sources', '--inspect-installer-sources',
               '--stage-gpu-sources', '--resume-gpu-sources', '--inspect-gpu-sources')],
            (['--install-machine', 'u', '192.168.2.215', 'droiddeck-test.invalid'], 'install-machine.py', None),
            *[([mode, 'u', 'assembly', 'recipes', '-'], 'install-registration.py', None)
              for mode in ('--install-registration', '--resume-registration')],
            *[([mode, 'u', 'assembly', 'recipes', 'frontend', 'compat', '-'], 'install-runtime.py', None)
              for mode in ('--install-runtime', '--resume-runtime', '--verify-runtime')],
            *[([mode], 'setup-local-tls.py', None) for mode in
              ('--initialize-local-tls', '--resume-local-tls', '--inspect-local-tls')],
            *[([mode, 'new-output'], 'acquire-inputs.py', None) for mode in
              ('--acquire-build-inputs', '--acquire-image-inputs')],
            *[([mode, 'u', 'prepared', 'root-output'], 'publish-install-inputs.py', None) for mode in
              ('--publish-install-inputs', '--verify-install-inputs')],
            (['--inspect-defaults'], 'installation-defaults.py', ['--inspect']),
            (['--initialize-defaults', 'publisher.apk'], 'installation-defaults.py', ['--initialize', 'publisher.apk'])]
        for args, script, translated in routes:
            with self.subTest(mode=args[0]), mock.patch.object(sys, 'argv', [str(launcher), *args]), \
                    mock.patch.object(os, 'execve') as execute:
                exec(compile(D.LAUNCHER, str(launcher), 'exec'), {'__file__': str(launcher)})
                execute.assert_called_once()
                binary, argv, environment = execute.call_args.args
                self.assertEqual(binary, '/usr/bin/python3')
                self.assertEqual(argv, ['/usr/bin/python3', '-I', '-B',
                    str(self.root / 'src/runtime/portable' / script), *(args if translated is None else translated)])
                self.assertEqual(set(environment), {'PATH', 'LANG', 'LC_ALL', 'JAVA_HOME'})
        with mock.patch.object(sys, 'argv', [str(launcher), '--reproduce-artifacts', 'inputs.json', 'new-output']), \
                mock.patch.object(os, 'execve') as execute:
            exec(compile(D.LAUNCHER, str(launcher), 'exec'), {'__file__': str(launcher)})
        self.assertEqual(execute.call_args.args[:2], ('/usr/bin/node', ['/usr/bin/node',
            str(self.root / 'src/runtime/portable/artifact-build.js'), 'reproduce', 'inputs.json', 'new-output']))
        with mock.patch.object(sys, 'argv', [str(launcher), '--prepare-install', 'acquired', 'new-output']), \
                mock.patch.object(os, 'execve') as execute:
            exec(compile(D.LAUNCHER, str(launcher), 'exec'), {'__file__': str(launcher)})
        self.assertEqual(execute.call_args.args[:2], ('/usr/bin/node', ['/usr/bin/node',
            str(self.root / 'src/runtime/portable/prepare-install.js'), 'acquired', 'new-output']))
        for args in (['--install-runtime'], ['--initialize-local-tls', '--force'],
                     ['--verify-runtime', 'u', 'a', 'r', 'f', 'c', 'private-key']):
            with mock.patch.object(sys, 'argv', [str(launcher), *args]), mock.patch.object(os, 'execve') as execute, \
                    self.assertRaises(SystemExit) as stopped:
                exec(compile(D.LAUNCHER, str(launcher), 'exec'), {'__file__': str(launcher)})
            self.assertEqual(stopped.exception.code, 1)
            execute.assert_not_called()

    def test_required_native_and_java_build_tests_are_packaged(self):
        required = {'tests/java/GpsBridgeReflectionTest.java', 'tests/java/LogicalCaptureValidationTest.java',
                    'tests/native/test_api.cpp', 'tests/native/test_binder_library.c',
                    'tests/native/test_display_capture.c', 'tests/native/display_capture_fixture.c',
                    'tests/native/test_touch_protocol.c', 'tests/native/test_touch_mapping.c',
                    'tests/native/test_touch_keepalive.c'}
        self.assertTrue(required.issubset(D.REQUIRED))
        for name in required:
            files = self.files.copy()
            files.pop(name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                D.render(files, self.revision)

    def test_new_output_only_and_archive_generation(self):
        raw, _ = D.render(self.files, self.revision)
        path = self.root / 'candidate.tar.gz'
        D.publish(str(path), raw)
        self.assertEqual(D.read_archive(str(path)), raw)
        with self.assertRaises(FileExistsError):
            D.publish(str(path), b'never overwrite')
        self.assertEqual(path.read_bytes(), raw)
        if sys.platform != 'win32':
            alias = self.root / 'alias.tar.gz'
            os.link(path, alias)
            with self.assertRaises(ValueError):
                D.read_archive(str(path))

    def test_verify_reconstructs_instead_of_trusting_a_modified_manifest(self):
        raw, _ = D.render(self.files, self.revision)
        path = self.root / 'candidate.tar.gz'
        D.publish(str(path), raw + b'extra compressed payload')
        with mock.patch.object(D, 'SOURCE_REVISION', self.revision), \
                mock.patch.object(D, 'read_sources', return_value=self.files), self.assertRaises(ValueError):
            D.main(['verify', '/fixture', str(path)])

    def test_binary_archive_bytes_do_not_use_windows_text_translation(self):
        path = self.root / 'binary.tar.gz'
        raw = b'fixture\r\n\x1a\x00\xff\n'
        D.publish(str(path), raw)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(D.read_archive(str(path)), raw)

    def test_empty_python_package_initializers_are_valid_source(self):
        raw, _ = D.render({**self.files, 'tools/__init__.py': b''}, self.revision)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
            self.assertEqual(archive.extractfile(D.ROOT + 'src/tools/__init__.py').read(), b'')

    def test_descriptor_and_path_ctime_views_are_checked_independently(self):
        path = self.root / 'ctime.tar'
        D.publish(str(path), b'fixture')
        original = os.fstat
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        def other_view(fd):
            info = original(fd)
            values = {key: getattr(info, key) for key in fields}
            values['st_ctime_ns'] += 1234
            return SimpleNamespace(**values)
        with mock.patch.object(D.os, 'fstat', side_effect=other_view):
            self.assertEqual(D.read_archive(str(path)), b'fixture')

    def test_git_reader_uses_pinned_blobs_not_worktree_and_checks_blob_hashes(self):
        commit = b'tree ' + b'a' * 40 + b'\n\nfixture\n'
        revision = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
        raw, name = b'original committed bytes', 'runtime/portable/a.py'
        object_id = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest().encode()
        listing = b'100644 blob ' + object_id + b' ' + str(len(raw)).encode() + b'\t' + name.encode() + b'\0'
        batch = object_id + b' blob ' + str(len(raw)).encode() + b'\n' + raw + b'\n'
        with mock.patch.object(D, 'SOURCE_REVISION', revision), mock.patch.object(D, 'git', side_effect=[commit, listing, batch]):
            self.assertEqual(D.read_sources('/fixture'), {name: raw})
        for changed in (batch[:-2] + b'x\n', batch + b'extra'):
            with mock.patch.object(D, 'SOURCE_REVISION', revision), \
                    mock.patch.object(D, 'git', side_effect=[commit, listing, changed]), self.assertRaises(ValueError):
                D.read_sources('/fixture')
        unsafe = listing.replace(b'100644 blob', b'120000 blob')
        with mock.patch.object(D, 'SOURCE_REVISION', revision), \
                mock.patch.object(D, 'git', side_effect=[commit, unsafe]), self.assertRaises(ValueError):
            D.read_sources('/fixture')

    def test_exact_cli_has_no_revision_or_tool_override(self):
        for args in ([], ['extract', '/repo', '/archive'], ['build', '/repo', '/archive', '--revision=other']):
            with self.assertRaises(ValueError):
                D.main(args)

    def test_historical_recipe_sources_require_complete_pinned_generators(self):
        with self.assertRaises(ValueError):
            D.read_install_sources('/unused', self.files)

    def test_historical_recipes_are_hash_bound_without_executing_python(self):
        raw = b'pinned original recipe\n'
        pins = {'runtime/portable/f' + str(i) + '.js': (len(raw), D.sha(raw)) for i in range(15)}
        files = {name: b'// fixed generator fixture\n' for name in (
            'runtime/portable/runtime-bundle.js', 'runtime/portable/runtime-candidate.js',
            'runtime/portable/runtime_candidate_inputs.js', 'runtime/portable/read-bounded.js')}
        files['runtime/portable/runtime-owned-install-plan.py'] = (
            "REVISION = '" + 'a' * 40 + "'\nPINS = MappingProxyType(" + repr(pins) + ")\nraise RuntimeError('must not execute')\n").encode()
        def generate(args, **options):
            output = Path(args[-1])
            output.mkdir()
            for i in range(80):
                (output / ('f' + str(i) + '.js')).write_bytes(b'generated source fixture')
            return subprocess.CompletedProcess(args, 0, stdout=b'fixed', stderr=b'')
        with mock.patch.object(D.subprocess, 'run', side_effect=generate), mock.patch.object(D, 'git', return_value=raw):
            result = D.read_install_sources('/source', files)
        self.assertEqual(len(result), 96)
        manifest = json.loads(result[D.INSTALL_INPUTS + 'recipes/bundle-manifest.json'])
        self.assertEqual(manifest['sourceRevision'], 'a' * 40)
        self.assertEqual(len(manifest['files']), 15)
        with mock.patch.object(D.subprocess, 'run', side_effect=generate), \
                mock.patch.object(D, 'git', return_value=b'foreign same-size bytes'), self.assertRaises(ValueError):
            D.read_install_sources('/source', files)


if __name__ == '__main__':
    unittest.main()
