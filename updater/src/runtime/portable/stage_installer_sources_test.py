# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('stage_sources_tested', Path(__file__).with_name('stage-installer-sources.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_fixed_source_roles_never_share_pre_registration_namespace(self):
        self.assertTrue(D.ROOT.is_relative_to('/root'))
        self.assertEqual(D.GPU_ROOT.as_posix(), '/var/lib/droiddeck/gpu-recovery')
        self.assertTrue(set(D.MODES).isdisjoint(D.GPU_MODES))

    def test_only_literal_independent_pins_are_read(self):
        self.assertEqual(D.literal(b"PINS = MappingProxyType({'a': (2, 'pin')})", 'PINS', wrapped=True), {'a': (2, 'pin')})
        for raw in (b'PINS = dangerous()', b'PINS = MappingProxyType({}, foreign=True)',
                    b'PINS = {}; PINS = {}', b'OTHER = {}'):
            with self.assertRaises(ValueError):
                D.literal(raw, 'PINS', wrapped=True)

    def package(self):
        recipes = {'runtime/fixture' + str(i) + '.js': b'fixed\n' for i in range(15)}
        pins = {name: (len(raw), D.digest(raw)) for name, raw in recipes.items()}
        revision = 'a' * 40
        sources = {D.RECIPES + name: raw for name, raw in recipes.items()}
        sources[D.RECIPES + 'bundle-manifest.json'] = D.H.encode({
            'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-recipe-source',
            'sourceRevision': revision, 'installable': False,
            'files': [{'path': name, 'size': size, 'sha256': pin} for name, (size, pin) in sorted(pins.items())]})
        sources['runtime/portable/runtime-owned-install-plan.py'] = ("REVISION = '" + revision + "'\nPINS = MappingProxyType(" + repr(pins) + ')\n').encode()
        sources['runtime/waydroid-display/location-adapter.js'] = b'compat\n'
        sources['runtime/portable/runtime-compat-install.py'] = ("PIN = '" + D.digest(b'compat\n') + "'\n").encode()
        for name in D.TOOLS:
            sources[name] = b'tool\n'
        sources['runtime/portable/gpu-activation.py'] = ('PINS = ' + repr({name: D.digest(sources[name]) for name in D.TOOLS})).encode()
        files = {'src/' + name: raw for name, raw in sources.items()}
        files['src/tools/__init__.py'] = b''
        manifest = {'kind': 'source-release-candidate', 'product': 'DroidDeck', 'sourceRevision': 'b' * 40,
                    'installable': False, 'files': [{'path': name, 'size': len(raw), 'sha256': D.digest(raw)} for name, raw in files.items()]}
        return files, manifest

    def read(self, files, manifest):
        package = Path('/fixture')
        values = {str(package / name): raw for name, raw in files.items()}
        values[str(package / 'package-manifest.json')] = D.H.encode(manifest)
        with mock.patch.object(D.H, 'protected', side_effect=lambda path, **kwargs: values[str(path)]):
            return D.read_package(package)

    def test_complete_manifest_and_independent_recipe_gpu_compat_pins(self):
        files, manifest = self.package()
        output, pin = self.read(files, manifest)
        self.assertEqual(pin, D.digest(D.H.encode(manifest)))
        self.assertEqual(output['compat-source.js'], b'compat\n')
        self.assertEqual(len([name for name in output if name.startswith('recipes/')]), 16)
        for name in ('src/tools/__init__.py', 'src/tools/waydroid-layer-property.py'):
            changed = {**files, name: b'foreign\n'}
            with self.assertRaises(ValueError):
                self.read(changed, manifest)
        # Even a rewritten outer manifest cannot override independent GPU pins.
        files['src/tools/waydroid-layer-property.py'] = b'foreign\n'
        for row in manifest['files']:
            raw = files[row['path']]
            row.update(size=len(raw), sha256=D.digest(raw))
        with self.assertRaises(ValueError):
            self.read(files, manifest)

    def test_invalid_manifest_path_or_duplicate_refused_before_access(self):
        for name in ('../private', '/private', 'src/../private', 'src//private'):
            files, manifest = self.package()
            manifest['files'][0]['path'] = name
            with self.assertRaises(ValueError):
                self.read(files, manifest)
        files, manifest = self.package()
        manifest['files'].append(manifest['files'][0])
        with self.assertRaises(ValueError):
            self.read(files, manifest)


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux protected source fixtures')
class Native(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='droiddeck-source-stage-', dir='/root')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patch = mock.patch.object(D, 'ROOT', self.root)
        patch.start()
        self.addCleanup(patch.stop)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.files = {'a/a.py': b'first\n', 'b/b.js': b'second\n'}
        info = os.fstat(self.fd)
        self.intent = {'schema': 1, 'rootIdentity': [info.st_dev, info.st_ino], 'fixture': 'original'}
        D.H.write_new(self.fd, 'intent.json', D.H.encode(self.intent))

    def test_completed_resume_inspect_are_readonly_and_keep_inodes(self):
        D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')
        original = {str(path): path.stat().st_ino for path in self.root.rglob('*')}
        with mock.patch.object(D.H, 'write_new', side_effect=AssertionError('write replay')):
            for mode in ('--resume-installer-sources', '--inspect-installer-sources'):
                self.assertTrue(D.finish(self.fd, self.intent, self.files, mode)['readOnly'])
        self.assertEqual(original, {str(path): path.stat().st_ino for path in self.root.rglob('*')})

    def test_process_death_resume_keeps_first_file(self):
        code = '''import importlib.util,json,os,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('s',sys.argv[1]);d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
d.ROOT=Path(sys.argv[2]);fd=os.open(d.ROOT,os.O_RDONLY|os.O_DIRECTORY)
intent=json.loads((d.ROOT/'intent.json').read_bytes())
d.finish(fd,intent,{'a/a.py':b'first\\n','b/b.js':b'second\\n'},'--resume-installer-sources',after_file=lambda name:os._exit(73))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, D.__file__, str(self.root)], timeout=20)
        self.assertEqual(result.returncode, 73)
        original = (self.root / 'a/a.py').stat().st_ino
        self.assertFalse((self.root / 'complete.json').exists())
        D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')
        self.assertEqual(original, (self.root / 'a/a.py').stat().st_ino)

    def test_foreign_partial_link_unknown_and_incomplete_inspection_refuse(self):
        with self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, self.files, '--inspect-installer-sources')
        D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')
        target = self.root / 'a/a.py'
        target.write_bytes(b'partial')
        with self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')
        self.assertEqual(target.read_bytes(), b'partial')
        target.unlink()
        target.symlink_to(self.root / 'b/b.js')
        with self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')
        target.unlink()
        target.write_bytes(self.files['a/a.py'])
        target.chmod(0o600)
        (self.root / 'foreign').mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, self.files, '--resume-installer-sources')

    def test_empty_source_read_explicit_and_default_guard_unchanged(self):
        D.H.write_new(self.fd, 'empty.py', b'')
        with self.assertRaises(ValueError):
            D.H.protected(self.root / 'empty.py')
        self.assertEqual(D.H.protected(self.root / 'empty.py', allow_empty=True), b'')


if __name__ == '__main__':
    unittest.main()
