# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

FILE = Path(__file__).with_name('runtime-final-layout.py')
spec = importlib.util.spec_from_file_location('final_layout_tests', FILE)
K = importlib.util.module_from_spec(spec)
spec.loader.exec_module(K)


def fixture():
    runtime = {'assembly/runtime/file-' + str(i) + '.js': ('generated' + str(i)).encode() for i in range(221)}
    rows = [{'source': source, 'path': source.removeprefix('assembly/'), 'size': len(raw),
             'sha256': K.E._sha(raw), 'mode': 0o644} for source, raw in runtime.items()]
    for name, (path, mode) in K.L.ARTIFACT_LAYOUT.items():
        raw = ('generated artifact ' + name).encode()
        source = 'artifacts/' + name
        runtime[source] = raw
        rows.append({'source': source, 'path': path, 'size': len(raw), 'sha256': K.E._sha(raw), 'mode': mode})
    recipe = {name: ('generated recipe ' + name).encode() for name in K.E.RECIPE_FILES}
    value = {'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-runtime-artifact-placement-plan',
        'targetRoot': '/opt/droiddeck', 'ownerUid': 0, 'ownerGid': 0, 'installable': False, 'readyForActivation': False,
        'files': rows, 'ownedRecipeFiles': K.E._records(recipe, recipe),
        'links': [{'path': name, 'target': target} for name, target in sorted(K.L.LINKS.items())]}
    plan = K.E._encode(value, compact=True)
    held = {'check': mock.Mock(), 'read_plan': mock.Mock(return_value=plan), 'read_source': runtime.__getitem__,
        'read_recipe_file': recipe.__getitem__, 'policy': {**dict.fromkeys(K.L.FALSE_FLAGS, False),
            'configurationValidatorExecuted': True, 'sourceRecipeCodeExecuted': True}}
    return held, value, runtime, recipe


class Layout(unittest.TestCase):
    def test_exact_250_owned_files_nine_explicit_registrations_four_aliases(self):
        held, _, _, _ = fixture()
        plan, readers = K._derive(held['read_plan']())
        value = json.loads(plan)
        self.assertEqual((len(value['files']), len(readers), len(value['links'])), (259, 259, 4))
        self.assertEqual(sum(row['registration'] for row in value['files']), 9)
        self.assertEqual(value['ownedInstallPlanSha256'], K.E._sha(held['read_plan']()))
        self.assertEqual(value['registrationTargets'], sorted(K.REGISTRATIONS.values()))
        self.assertTrue(all(not p.startswith('/etc/droiddeck/') for p in value['registrationTargets']))
        for flag in ('registrationNamespaceVerified', 'servicesRegistered', 'finalPermissionsApplied',
                     'runtimeLinksMaterialized', 'finalTargetsPublished', 'installable', 'readyForActivation',
                     'installationAuthorized', 'recoveryAuthorized'):
            self.assertIs(value[flag], False)
        with self.assertRaises(TypeError):
            readers['foreign'] = ()

    def test_recipe_wrapper_and_new_role_manifests_are_not_final_paths(self):
        held, _, _, _ = fixture()
        raw, _ = K._derive(held['read_plan']())
        rows = {row['path']: row for row in json.loads(raw)['files']}
        self.assertFalse(any('/recipe/' in name for name in rows))
        self.assertEqual(rows['/etc/droiddeck/runtime.json']['stagedPath'], 'recipe/runtime.json')
        self.assertEqual(rows['/opt/droiddeck/headless-owned-runtime/policy.json']['sourceRole'], 'profile')
        self.assertEqual(rows['/opt/droiddeck/pid-compat/portable/pid-startup.js']['stagedPath'], 'recipe/pid-compat/portable/pid-startup.js')
        self.assertNotIn('/opt/droiddeck/bundle-manifest.json', rows)
        self.assertEqual(rows['/etc/droiddeck/bundle-manifest.json']['stagedPath'], 'recipe/bundle-manifest.json')

    def test_registration_duplicates_exact_admitted_source_pairs_and_modes(self):
        held, _, _, recipe = fixture()
        raw, readers = K._derive(held['read_plan']())
        rows = {row['path']: row for row in json.loads(raw)['files']}
        for original, destination in K.REGISTRATIONS.items():
            row = rows[destination]
            self.assertEqual((row['size'], row['sha256'], row['mode'], row['uid'], row['gid']),
                             (len(recipe[original]), K.E._sha(recipe[original]), 0o644, 0, 0))
            self.assertEqual(readers[destination][:2], ('recipe', original))
        self.assertIn('/etc/pam.d/droiddeck', rows)
        self.assertIn('/etc/tmpfiles.d/droiddeck.conf', rows)

    def test_artifact_modes_directory_modes_and_direct_library_aliases(self):
        held, _, _, _ = fixture()
        raw, _ = K._derive(held['read_plan']())
        value = json.loads(raw)
        rows = {row['path']: row for row in value['files']}
        for path, mode in K.L.ARTIFACT_LAYOUT.values():
            self.assertEqual(rows['/opt/droiddeck/' + path]['mode'], mode)
        directories = {row['path']: row['mode'] for row in value['ownedDirectories']}
        self.assertEqual(directories['/opt/droiddeck/guest'], 0o700)
        self.assertEqual(directories['/opt/droiddeck/guest/bin'], 0o700)
        self.assertEqual(directories['/etc/droiddeck/services/systemd/system'], 0o755)
        self.assertNotIn('/etc/systemd/system', directories)
        self.assertIs(value['existingRegistrationParentsMustNotBeChmodded'], True)
        for link in value['links']:
            from pathlib import PurePosixPath
            self.assertIn(str(PurePosixPath(link['path']).parent / link['target']), rows)

    def test_mutated_mode_owner_product_kind_or_aliases_refuse(self):
        _, original, _, _ = fixture()
        for field, value in (('ownerUid', True), ('ownerGid', 1000), ('product', 'foreign'), ('version', 2),
                             ('kind', 'foreign'), ('targetRoot', '/foreign'), ('links', [])):
            with self.assertRaises(ValueError):
                K._derive(K.E._encode({**original, field: value}, compact=True))
        for mode in (True, 0o777, 0o4755, 0o600):
            changed = copy.deepcopy(original)
            changed['files'][0]['mode'] = mode
            with self.assertRaises(ValueError):
                K._derive(K.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['links'][0]['target'] = '/foreign.so'
        with self.assertRaises(ValueError):
            K._derive(K.E._encode(changed, compact=True))

    def test_unsafe_names_missing_artifact_or_recipe_and_directory_collision_refuse(self):
        _, original, _, _ = fixture()
        for path in ('../outside', '/absolute', 'runtime/./alias', 'foreign/file', 'runtime'):
            changed = copy.deepcopy(original)
            changed['files'][0]['path'] = path
            with self.assertRaises(ValueError):
                K._derive(K.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['files'][-1]['path'] = 'runtime/missing-artifact'
        changed['files'][-1]['mode'] = 0o644
        with self.assertRaises(ValueError):
            K._derive(K.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['files'][0]['path'] = 'runtime/a'
        changed['files'][1]['path'] = 'runtime/a/b'
        with self.assertRaises(ValueError):
            K._derive(K.E._encode(changed, compact=True))
        changed = copy.deepcopy(original)
        changed['ownedRecipeFiles'].pop()
        with self.assertRaises(ValueError):
            K._derive(K.E._encode(changed, compact=True))

    def test_noncanonical_duplicate_json_and_unbounded_plan_refuse(self):
        held, value, _, _ = fixture()
        raw = held['read_plan']()
        for data in (b'', b'x' * (4 * 1024 * 1024 + 1), json.dumps(value).encode(),
                     b'{"ownerUid":0,' + raw[1:], raw + b' '):
            with self.assertRaises(ValueError):
                K._derive(data)

    def test_all_destination_reads_guard_original_authority_and_source_pairs(self):
        held, _, runtime, recipe = fixture()
        with ExitStack() as stack, mock.patch.object(K.E, 'hold_owned_install_plan', return_value=held) as gate:
            result = K.hold_final_install_layout(stack, 'fixture', '/root/source', '/root/recipes', b'{}')
            gate.assert_called_once()
            value = json.loads(result['read_plan']())
            for row in value['files']:
                data = result['read_file'](row['path'])
                self.assertEqual((len(data), K.E._sha(data)), (row['size'], row['sha256']))
            name = '/etc/droiddeck/runtime.json'
            recipe['runtime.json'] = b'changed original'
            with self.assertRaises(ValueError):
                result['read_file'](name)
            held['check'].side_effect = ValueError('generated source/account drift')
            for read in (result['check'], result['read_plan'], lambda: result['read_file'](name)):
                with self.assertRaises(ValueError):
                    read()

    def test_plan_generation_drift_and_mid_read_account_drift_refuse(self):
        held, _, _, recipe = fixture()
        with ExitStack() as stack, mock.patch.object(K.E, 'hold_owned_install_plan', return_value=held):
            result = K.hold_final_install_layout(stack, 'fixture', '/root/source', '/root/recipes', b'{}')
            original = held['read_plan'].return_value
            held['read_plan'].return_value = b'changed plan'
            with self.assertRaises(ValueError):
                result['read_plan']()
            held['read_plan'].return_value = original
            def drift(name):
                held['check'].side_effect = ValueError('generated mid-read drift')
                return recipe[name]
            held['read_recipe_file'] = drift
            with self.assertRaises(ValueError):
                result['read_file']('/etc/droiddeck/runtime.json')

    def test_closed_destination_read_never_reads_links_metadata_or_foreign_paths(self):
        held, _, _, _ = fixture()
        with ExitStack() as stack, mock.patch.object(K.E, 'hold_owned_install_plan', return_value=held):
            result = K.hold_final_install_layout(stack, 'fixture', '/root/source', '/root/recipes', b'{}')
            for path in (None, [], '/etc/shadow', '/opt/droiddeck/libgbinder/libgbinder.so', '/etc/droiddeck/../runtime.json', 'runtime.json'):
                with self.assertRaises(ValueError):
                    result['read_file'](path)

    def test_cli_no_write_owner_receipt_target_or_activation_override(self):
        for args in ([], ['--install'], ['--register'], ['--recover'],
                     ['--inspect-final-layout', 'u', '/s', '/r', '/settings'],
                     ['--inspect-final-layout', 'u', '/s', '/r', '-', '--target', '/foreign']):
            with self.assertRaises(ValueError):
                K.main(args)
        result = subprocess.run([sys.executable, '-B', str(FILE), '--register', 'private-value'], capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, result.stdout), (1, ''))
        self.assertEqual(result.stderr.strip(), 'Final placement planning refused; no files or registrations changed')


if __name__ == '__main__':
    unittest.main()
