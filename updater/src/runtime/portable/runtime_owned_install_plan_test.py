# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
spec = importlib.util.spec_from_file_location('owned_install_tests', HERE / 'runtime-owned-install-plan.py')
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)
PACKAGE = Path('/root/droiddeck-owned-install-plan-20261017ee/recipe-sources')


def blob(name):
    return (ROOT / name).read_bytes().replace(b'\r\n', b'\n')


def example():
    config = {'version': 1, 'user': 'fixture', 'uid': 1000, 'gid': 1000, 'lanAddress': '10.50.2.3',
              'home': '/home/fixture', 'port': 8443, 'hostname': 'device.teslaandroid.com'}
    raw = json.dumps(config, indent=1).encode()
    generation = G._sha(json.dumps(config, separators=(',', ':')).encode())
    owned = G._encode({'version': 2, 'configurationDigest': generation, 'originalConfigurationSha256': 'a' * 64,
                      'profiles': {'software': 'b' * 64, 'gpu': 'c' * 64}})
    base = G._encode({'configurationDigest': generation, 'sourceMetadataSha256': G._sha(raw), 'files': [], 'links': []}, compact=True)
    child = subprocess.run(['node', '-e', G.SCRIPT, str(HERE / 'owned-install-recipe.js')],
        input=json.dumps({'metadata': raw.decode(), 'ownedPolicy': json.loads(owned)}).encode(),
        capture_output=True, timeout=10)
    if child.returncode:
        raise AssertionError('Generated recipe fixture failed: ' + child.stderr.decode())
    return raw, base, G._encode(config), owned, json.loads(child.stdout)


def fake_held(base, canonical, owned):
    return {'check': mock.Mock(), 'read_plan': lambda: base, 'read_configuration': lambda: canonical,
        'read_owned_policy': lambda: owned, 'read_source': lambda name: b'payload', 'policy': {
            **dict.fromkeys(G.P.C.L.FALSE_FLAGS, False), 'profilePolicyVerified': True,
            'configurationValidatorExecuted': True, 'sourceValidationCodeExecuted': True}}


class Boundary(unittest.TestCase):
    def test_independent_closed_source_ledger_and_fixed_revision(self):
        self.assertEqual(len(G.PINS), 15)
        self.assertEqual(G.REVISION, 'b4e333fafc72f3479328123cfe50fc17012adda8')
        self.assertEqual(G.source_records(G.source_manifest()), G.PINS)
        for raw in (None, b'', G.source_manifest() + b'\n', G.source_manifest().replace(b'"installable":false', b'"installable":true')):
            with self.assertRaises(ValueError):
                G.source_records(raw)
        with self.assertRaises(TypeError):
            G.PINS['extra'] = (1, 'a' * 64)
        for name, (size, pin) in G.PINS.items():
            self.assertEqual((len(blob(name)), G._sha(blob(name))), (size, pin), name)

    def test_source_builder_uses_only_pinned_git_blobs_and_never_overwrites(self):
        with tempfile.TemporaryDirectory(prefix='droiddeck-source-builder-') as temp:
            parent = Path(temp).resolve()
            self.assertEqual(parent.parent, Path(tempfile.gettempdir()).resolve())
            out = parent / 'source'
            def git(args, **options):
                self.assertEqual(args[:4], ['git', '--no-pager', '--no-replace-objects', '--no-lazy-fetch'])
                self.assertEqual(args[-3:-1], ['cat-file', 'blob'])
                self.assertEqual(options['env']['GIT_ALLOW_PROTOCOL'], '')
                self.assertEqual(options['env']['GIT_CONFIG_GLOBAL'], os.devnull)
                self.assertEqual(options['timeout'], 10)
                revision, name = args[-1].split(':', 1)
                self.assertEqual(revision, G.REVISION)
                return subprocess.CompletedProcess(args, 0, blob(name), b'')
            with mock.patch.object(G.sys, 'platform', 'win32'), mock.patch.object(G.subprocess, 'run', side_effect=git) as child:
                report = G.build_recipe_sources(str(ROOT.resolve()), str(out))
                self.assertEqual(child.call_count, 15)
                self.assertEqual(report['manifestSha256'], G._sha(G.source_manifest()))
                before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob('*') if p.is_file()}
                self.assertEqual(len(before), 16)
                self.assertEqual(before['bundle-manifest.json'], G.source_manifest())
                with self.assertRaises(FileExistsError):
                    G.build_recipe_sources(str(ROOT.resolve()), str(out))
                self.assertEqual(before, {str(p.relative_to(out)): p.read_bytes() for p in out.rglob('*') if p.is_file()})

    def test_builder_refuses_unpinned_blobs_before_output_or_root_execution(self):
        with tempfile.TemporaryDirectory(prefix='droiddeck-source-refusal-') as temp:
            parent = Path(temp).resolve()
            self.assertEqual(parent.parent, Path(tempfile.gettempdir()).resolve())
            out = parent / 'source'
            with mock.patch.object(G.sys, 'platform', 'win32'), \
                    mock.patch.object(G.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'foreign', b'')):
                with self.assertRaises(ValueError):
                    G.build_recipe_sources(str(ROOT.resolve()), str(out))
                self.assertFalse(out.exists())
            with mock.patch.object(G.sys, 'platform', 'linux'), mock.patch.object(G.os, 'geteuid', return_value=0, create=True), \
                    mock.patch.object(G.subprocess, 'run', side_effect=AssertionError('no Git')):
                with self.assertRaises(ValueError):
                    G.build_recipe_sources(str(ROOT.resolve()), str(out))

    def test_privilege_bounds_and_disjoint_sources_before_child_or_source_access(self):
        with ExitStack() as stack, mock.patch.object(G, 'hold_recipe_sources', side_effect=AssertionError('no sources')), \
                mock.patch.object(G, '_derive', side_effect=AssertionError('no child')):
            for platform, uid in (('win32', 0), ('linux', 1000)):
                with mock.patch.object(G.A.sys, 'platform', platform), mock.patch.object(G.A.os, 'geteuid', return_value=uid, create=True):
                    with self.assertRaises(ValueError):
                        G.hold_owned_install_plan(stack, 'fixture', '/root/assembly', '/root/recipes', b'{}')
            with mock.patch.object(G.A, '_root_linux'):
                for raw in (None, b'', b'x' * 16385):
                    with self.assertRaises(ValueError):
                        G.hold_owned_install_plan(stack, 'fixture', '/root/assembly', '/root/recipes', raw)
                for root in ('/opt/droiddeck', '/opt/droiddeck/code', '/opt', '/root/assembly', '/root/assembly/code', '/root'):
                    with self.assertRaises(ValueError):
                        G.hold_owned_install_plan(stack, 'fixture', '/root/assembly', root, b'{}')

    def test_fixed_child_command_environment_bounds_timeout_and_redacted_failures(self):
        with mock.patch.object(G.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'{}', b'')) as child:
            self.assertEqual(G._derive(PurePosixPath('/root/recipes'), b'{}', b'{}'), {})
            args, opts = child.call_args
            self.assertEqual(args[0], ['/usr/bin/node', '-e', G.SCRIPT, '/root/recipes/runtime/portable/owned-install-recipe.js'])
            self.assertEqual(opts['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
            self.assertEqual((opts['cwd'], opts['close_fds'], opts['timeout']), ('/', True, 10))
        for result in (subprocess.CompletedProcess([], 1, b'', b'private-value'), subprocess.CompletedProcess([], 0, b'', b''),
                       subprocess.CompletedProcess([], 0, b'{}', b'private-value'), subprocess.CompletedProcess([], 0, b'x' * 262145, b'')):
            with mock.patch.object(G.subprocess, 'run', return_value=result), self.assertRaisesRegex(ValueError, '^Recipe derivation refused$'):
                G._derive(PurePosixPath('/root/recipes'), b'{}', b'{}')
        with mock.patch.object(G.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed', 10)), self.assertRaisesRegex(ValueError, '^Recipe derivation refused$'):
            G._derive(PurePosixPath('/root/recipes'), b'{}', b'{}')

    def test_recipe_admission_exact_metadata_all_child_records_and_immutable_bytes(self):
        raw, base, canonical, owned, result = example()
        files = G._admit_recipe(result, base, canonical, owned, {'read': blob})
        self.assertEqual(len(files), 21)
        self.assertEqual(files['runtime.json'], canonical)
        self.assertEqual(files['headless-owned-runtime/policy.json'], owned)
        result['files']['runtime.json'] = 'mutated'
        self.assertEqual(files['runtime.json'], canonical)
        self.assertNotEqual(G._sha(raw), G._sha(canonical))

    def test_forged_or_rehashed_recipe_results_do_not_authorize_unknown_files_or_source_changes(self):
        _, base, canonical, owned, original = example()
        variants = []
        for field, value in (('filesWritten', True), ('rootProvenanceVerified', True), ('payloadFiles', 21), ('manifestFiles', True), ('private', 'generated')):
            value_result = copy.deepcopy(original)
            value_result['summary'][field] = value
            variants.append(value_result)
        for name, text in (('../outside', 'generated'), ('runtime.json', '{}'), ('services/bundle-manifest.json', '{}'),
                           ('bundle-manifest.json', '{}'), ('headless-owned-runtime/policy.json', '{}'),
                           ('headless-owned-runtime/headless-owned-runtime.py', '# foreign code\n')):
            value_result = copy.deepcopy(original)
            value_result['files'][name] = text
            variants.append(value_result)
        for value_result in variants:
            with self.assertRaises(ValueError):
                G._admit_recipe(value_result, base, canonical, owned, {'read': blob})
        # Repairing every child/parent record cannot adopt changed executable source.
        changed = copy.deepcopy(original)
        name = 'headless-owned-runtime/headless-runtime.py'
        changed['files'][name] += '# generated mutation\n'
        for manifest, relative in (('headless-owned-runtime/bundle-manifest.json', 'headless-runtime.py'), ('bundle-manifest.json', name)):
            value = json.loads(changed['files'][manifest])
            for row in value['files']:
                if row['path'] == relative:
                    row.update(size=len(changed['files'][name].encode()), sha256=G._sha(changed['files'][name].encode()))
            changed['files'][manifest] = G._encode(value).decode()
        with self.assertRaisesRegex(ValueError, 'independently held source'):
            G._admit_recipe(changed, base, canonical, owned, {'read': blob})

    def test_all_gate_reads_check_both_generations_and_derivation_occurs_once(self):
        raw, base, canonical, owned, result = example()
        held = fake_held(base, canonical, owned)
        sources = {'check': mock.Mock(), 'read': blob}
        with ExitStack() as stack, mock.patch.object(G.A, '_root_linux'), \
                mock.patch.object(G, 'hold_recipe_sources', return_value=sources), \
                mock.patch.object(G.P, 'hold_owned_profile_runtime_plan', return_value=held), \
                mock.patch.object(G, '_derive', return_value=result) as derive:
            out = G.hold_owned_install_plan(stack, 'fixture', '/root/assembly', '/root/recipes', raw)
            self.assertEqual(out['read_recipe_file']('runtime.json'), canonical)
            self.assertEqual(out['policy']['recipeSourceFilesVerified'], 16)
            self.assertEqual(out['policy']['ownedRecipeFilesVerified'], 21)
            self.assertIs(out['policy']['recipeSourceProvenanceVerified'], True)
            self.assertIs(out['policy']['ownedRecipeVerified'], True)
            self.assertIs(out['policy']['sourceRecipeCodeExecuted'], True)
            for flag in ('recipeFilesWritten', *[f for f in G.P.C.L.FALSE_FLAGS if f != 'configurationIncluded']):
                self.assertIs(out['policy'][flag], False)
            readers = (out['read_plan'], out['read_configuration'], out['read_owned_policy'],
                       lambda: out['read_recipe_file']('runtime.json'), lambda: out['read_source']('payload'))
            for check in (held['check'], sources['check']):
                check.side_effect = ValueError('changed')
                for read in readers:
                    with self.assertRaises(ValueError):
                        read()
                check.side_effect = None
            self.assertEqual(derive.call_count, 1)
            for name in ('../runtime.json', 'foreign', 1):
                with self.assertRaises(ValueError):
                    out['read_recipe_file'](name)

    def test_gate_refuses_generation_drift_during_child_before_return(self):
        raw, base, canonical, owned, result = example()
        for role in ('profile', 'recipe'):
            held = fake_held(base, canonical, owned)
            sources = {'check': mock.Mock(), 'read': blob}
            def derive(*args):
                (held if role == 'profile' else sources)['check'].side_effect = ValueError('during child')
                return result
            with ExitStack() as stack, mock.patch.object(G.A, '_root_linux'), \
                    mock.patch.object(G, 'hold_recipe_sources', return_value=sources), \
                    mock.patch.object(G.P, 'hold_owned_profile_runtime_plan', return_value=held), \
                    mock.patch.object(G, '_derive', side_effect=derive), self.assertRaises(ValueError):
                G.hold_owned_install_plan(stack, 'fixture', '/root/assembly', '/root/recipes', raw)

    def test_cli_rejects_pin_owner_validator_and_output_overrides_with_fixed_redaction(self):
        for args in ([], ['--install'], ['--inspect-owned-install-plan', 'fixture', '/root/assembly', '/root/recipes', '/private-value'],
                     ['--build-recipe-sources', '/repo', '/out', '--revision', 'private-value']):
            with self.assertRaises(ValueError):
                G.main(args)
        child = subprocess.run([sys.executable, '-B', str(HERE / 'runtime-owned-install-plan.py'), '--install', 'private-value'],
                               capture_output=True, text=True, timeout=5)
        self.assertEqual((child.returncode, child.stdout), (1, ''))
        self.assertEqual(child.stderr.strip(), 'Owned install planning refused; no deployment performed')


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Root Linux generated recipe-source fixtures required')
class Protected(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-candidate-admission-recipe-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'sources'
        shutil.copytree(PACKAGE, self.root)

    def test_complete_original_private_source_ledger_and_fd_cleanup(self):
        before = set(os.listdir('/proc/self/fd'))
        with ExitStack() as stack:
            held = G.hold_recipe_sources(stack, str(self.root))
            self.assertEqual(len(held['files']), 16)
            self.assertEqual(held['read']('bundle-manifest.json'), G.source_manifest())
            held['check']()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_code_or_repaired_source_manifest_cannot_change_independent_pins(self):
        file = self.root / 'runtime/portable/owned-install-recipe.js'
        file.write_bytes(file.read_bytes() + b'\n// generated change\n')
        with ExitStack() as stack, self.assertRaises(ValueError):
            G.hold_recipe_sources(stack, str(self.root))
        manifest = json.loads(G.source_manifest())
        for row in manifest['files']:
            if row['path'] == 'runtime/portable/owned-install-recipe.js':
                row.update(size=file.stat().st_size, sha256=G._sha(file.read_bytes()))
        (self.root / 'bundle-manifest.json').write_bytes(G._encode(manifest, compact=True))
        with ExitStack() as stack, self.assertRaises(ValueError):
            G.hold_recipe_sources(stack, str(self.root))

    def test_nonprivate_root_or_file_extra_inventory_and_wrong_owner_refuse(self):
        file = self.root / 'runtime/portable/owned-install-recipe.js'
        for target, mode, restore in ((file, 0o644, 0o600), (self.root, 0o755, 0o700)):
            target.chmod(mode)
            with ExitStack() as stack, self.assertRaises(ValueError):
                G.hold_recipe_sources(stack, str(self.root))
            target.chmod(restore)
        os.chown(file, 1000, 1000)
        with ExitStack() as stack, self.assertRaises(ValueError):
            G.hold_recipe_sources(stack, str(self.root))
        os.chown(file, 0, 0)
        (self.root / 'node_modules').mkdir()
        with ExitStack() as stack, self.assertRaises(ValueError):
            G.hold_recipe_sources(stack, str(self.root))

    def test_symlink_fifo_and_hardlink_sources_refuse(self):
        file = self.root / 'runtime/portable/owned-install-recipe.js'
        raw = file.read_bytes()
        for kind in ('symlink', 'fifo', 'hardlink'):
            file.unlink()
            other = Path(self.temp.name) / 'original'
            other.write_bytes(raw)
            other.chmod(0o600)
            if kind == 'symlink':
                file.symlink_to(other)
            elif kind == 'fifo':
                os.mkfifo(file, 0o600)
            else:
                os.link(other, file)
            with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
                G.hold_recipe_sources(stack, str(self.root))
            file.unlink()
            other.unlink()
            file.write_bytes(raw)
            file.chmod(0o600)

    def test_same_bytes_source_inode_and_directory_replacement_refuse(self):
        file = self.root / 'runtime/portable/owned-install-recipe.js'
        with ExitStack() as stack:
            held = G.hold_recipe_sources(stack, str(self.root))
            replacement = Path(self.temp.name) / 'replacement'
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, file)
            with self.assertRaises(ValueError):
                held['read']('bundle-manifest.json')
        with ExitStack() as stack:
            held = G.hold_recipe_sources(stack, str(self.root))
            self.root.rename(self.root.with_name('displaced'))
            shutil.copytree(PACKAGE, self.root)
            with self.assertRaises(ValueError):
                held['check']()

    def test_privileged_bound_recipe_uses_real_235_inputs_profiles_nss_once_without_writes(self):
        account = G.A._account('hilman')
        config = dict(zip(('user', 'uid', 'gid', 'home'), account))
        config['lanAddress'] = '10.50.2.3'
        before = set(os.listdir('/proc/self/fd'))
        with mock.patch.object(G.A, '_account', return_value=account) as nss, \
                mock.patch.object(G, '_derive', wraps=G._derive) as derive, ExitStack() as stack:
            held = G.hold_owned_install_plan(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly', str(self.root), json.dumps(config).encode())
            value = json.loads(held['read_plan']())
            self.assertEqual((len(value['files']), len(value['links']), len(value['ownedRecipeFiles'])), (229, 4, 21))
            self.assertEqual(held['read_recipe_file']('runtime.json'), held['read_configuration']())
            self.assertEqual(held['read_recipe_file']('headless-owned-runtime/policy.json'), held['read_owned_policy']())
            self.assertTrue(held['read_source']('artifacts/location-bridge.jar'))
            self.assertEqual(derive.call_count, 1)
            nss.return_value = ('hilman', account[1] + 1, account[2], account[3])
            for read in (held['read_plan'], held['read_configuration'], held['read_owned_policy'],
                         lambda: held['read_recipe_file']('runtime.json'), lambda: held['read_source']('artifacts/location-bridge.jar')):
                with self.assertRaises(ValueError):
                    read()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_failed_source_admission_closes_descriptors_and_never_executes_child(self):
        before = set(os.listdir('/proc/self/fd'))
        (self.root / 'runtime/portable/owned-install-recipe.js').chmod(0o644)
        with mock.patch.object(G, '_derive', side_effect=AssertionError('no child')), ExitStack() as stack, self.assertRaises(ValueError):
            G.hold_owned_install_plan(stack, 'hilman', '/root/droiddeck-runtime-artifact-proof-ngk095sy/assembly', str(self.root), b'{}')
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)


if __name__ == '__main__':
    unittest.main()
