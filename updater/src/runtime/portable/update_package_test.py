# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('update_package', Path(__file__).with_name('update-package.py'))
U = importlib.util.module_from_spec(spec)
spec.loader.exec_module(U)


def row(path, raw, mode=0o644):
    return {'path': path, 'size': len(raw), 'sha256': U.sha(raw), 'mode': mode}


def fixture():
    before = [row('index.html', b'old'), row('retired.js', b'retired')]
    after = [row('index.html', b'new'), row('main.js', b'// generated fixture; never executed')]
    manifest = {'schema': 1, 'kind': 'droiddeck-runtime-update', 'base': dict(U.BASE),
                'target': {'version': '0.1.0-rc.3', 'sourceRevision': 'a' * 40, 'sourceSha256': 'b' * 64},
                'platform': dict(U.PLATFORM), 'dataPolicy': 'preserve',
                'components': [{'name': 'frontend', 'before': before, 'after': after}]}
    payloads = {U.PREFIX + 'payload/frontend/index.html': (b'new', 0o644),
                U.PREFIX + 'payload/frontend/main.js': (b'// generated fixture; never executed', 0o644)}
    return manifest, payloads


def bundle(manifest=None, payloads=None):
    original, files = fixture()
    value = original if manifest is None else manifest
    return U.canonical_archive({U.MANIFEST: (U.encode(value), 0o644), **(files if payloads is None else payloads)})


class Review(unittest.TestCase):
    def test_real_runtime_font_scripts_and_dependency_metadata_are_role_scoped(self):
        self.assertEqual(U.path_name('fonts/MaterialIcons-Regular.otf', component='frontend'), 'fonts/MaterialIcons-Regular.otf')
        for name, role in U.RUNTIME_SCRIPTS.items():
            self.assertEqual(U.path_name(name, component=role), name)
            for other in U.COMPONENTS.keys() - {role}:
                with self.assertRaises(ValueError): U.path_name(name, component=other)
        for name in ('node_modules/a/index.d.ts', 'node_modules/a/umd/a.min.js.map',
                     'node_modules/a/README.markdown', 'node_modules/a/.github/FUNDING.yml',
                     *(('node_modules/a/' + name) for name in U.DEPENDENCY_METADATA)):
            self.assertEqual(U.path_name(name, component='gateway'), name)
            with self.assertRaises(ValueError): U.path_name(name, component='frontend')
        for name in ('arbitrary.py', 'setup.sh', 'hooks/install.sh', 'other/native-stop.py',
                     '.travis.yml', 'node_modules/a/.npmrc', 'node_modules/a/source.ts',
                     'node_modules/a/.github/workflows/publish.yml', 'node_modules/a/.git/hooks/start.sh'):
            with self.assertRaises(ValueError): U.path_name(name, component='gateway')

    def test_complete_font_inventory_roundtrip_and_script_cannot_cross_component(self):
        manifest, payloads = fixture()
        name, font = 'fonts/MaterialIcons-Regular.otf', b'OTTO generated font fixture, never parsed'
        for side in ('before', 'after'):
            manifest['components'][0][side] = sorted([*manifest['components'][0][side], row(name, font)], key=lambda row: row['path'])
        payloads[U.PREFIX + 'payload/frontend/' + name] = font, 0o644
        raw = bundle(manifest, payloads)
        self.assertEqual(U.review(raw, U.sha(raw))['components'][0]['targetFiles'], 3)
        manifest, payloads = fixture()
        script, code = 'native-stop.py', b'# generated fixture, never executed\n'
        manifest['components'][0]['after'] = sorted([*manifest['components'][0]['after'], row(script, code)], key=lambda row: row['path'])
        payloads[U.PREFIX + 'payload/frontend/' + script] = code, 0o644
        raw = bundle(manifest, payloads)
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_closed_review_reports_only_preparation_not_install_or_signature(self):
        raw = bundle()
        report = U.review(raw, U.sha(raw))
        self.assertTrue(report['archiveDigestMatched'])
        for name in ('signatureVerified', 'originalInstallationOwnershipVerified', 'installable',
                     'androidDataChangesAllowed', 'tlsIdentityChangesAllowed', 'servicesChanged'):
            self.assertIs(report[name], False)
        self.assertEqual(report['components'], [{'component': 'frontend', 'targetRoot': '/opt/droiddeck/frontend',
                                               'added': 1, 'removed': 1, 'modified': 1, 'targetFiles': 2}])
        self.assertEqual(len(report['pendingGates']), 5)
        self.assertEqual(raw, bundle())

    def test_independent_digest_is_mandatory_and_tampering_refuses(self):
        raw = bundle()
        for pin in ('', 'A' * 64, 'not-a-signature', 'c' * 64, None):
            with self.subTest(pin=pin), self.assertRaises(ValueError): U.review(raw, pin)
        with self.assertRaises(ValueError): U.review(raw[:-1] + b'x', U.sha(raw))

    def test_strict_version_order_and_release_channels(self):
        for value in ('0.1.0-rc.2', '0.1.0-rc.1', '0.0.9', 'latest', '0.1.0-beta.3', '0.1.0-rc.03', '01.1.0'):
            manifest, _ = fixture(); manifest['target']['version'] = value
            raw = bundle(manifest)
            with self.subTest(version=value), self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        for value in ('0.1.0-rc.3', '0.1.0', '0.1.1-rc.1', '1.0.0'):
            manifest, _ = fixture(); manifest['target']['version'] = value
            raw = bundle(manifest); self.assertEqual(U.review(raw, U.sha(raw))['target']['version'], value)

    def test_wrong_base_platform_and_data_migrations_refuse(self):
        for key, value in (('base', {'version': '0.1.0-rc.1', 'sourceSha256': 'a' * 64}),
                           ('platform', {**U.PLATFORM, 'architecture': 'aarch64'}),
                           ('dataPolicy', 'migrate'), ('schema', True), ('kind', 'clean-installer')):
            manifest, _ = fixture(); manifest[key] = value
            raw = bundle(manifest)
            with self.subTest(key=key), self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        manifest, _ = fixture(); manifest['target']['sourceSha256'] = U.BASE['sourceSha256']
        raw = bundle(manifest)
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_hooks_foreign_fields_and_duplicate_json_refuse(self):
        for key in ('hooks', 'commands', 'android', 'tlsPrivateKey', 'rollbackCommand'):
            manifest, _ = fixture(); manifest[key] = 'not permitted'
            raw = bundle(manifest)
            with self.subTest(key=key), self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        with self.assertRaises(ValueError): U.document(b'{"schema":1,"schema":1}\n')
        with self.assertRaises(ValueError): U.document(b'{ "schema": 1 }\n')

    def test_components_paths_and_inventories_are_closed(self):
        for name in ('android', '/etc', 'tls', 'accounts', ['frontend']):
            manifest, _ = fixture(); manifest['components'][0]['name'] = name
            raw = bundle(manifest)
            with self.subTest(component=name), self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        for path in ('../index.html', '/etc/passwd', 'a/../index.html', 'a//index.html',
                     './index.html', 'a\\index.js', '.env', '.env.production', '.ssh/id_rsa',
                     'credentials.json', 'cert.pem', 'key.pem', 'id_ed25519', 'app.service',
                     'https://host/main.js', 'a\x00.js', 'a\u2215b.js'):
            with self.subTest(path=path), self.assertRaises(ValueError): U.path_name(path)

    def test_payload_mismatch_missing_extra_and_noop_refuse(self):
        manifest, payloads = fixture()
        modified = dict(payloads); modified[U.PREFIX + 'payload/frontend/main.js'] = (b'foreign', 0o644)
        missing = dict(payloads); del missing[U.PREFIX + 'payload/frontend/main.js']
        extra = {**payloads, U.PREFIX + 'payload/frontend/extra.js': (b'extra', 0o644)}
        for selected in (modified, missing, extra):
            raw = bundle(payloads=selected)
            with self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        manifest['components'][0]['before'] = copy.deepcopy(manifest['components'][0]['after'])
        raw = bundle(manifest)
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_duplicate_unsorted_boolean_and_permission_records_refuse(self):
        for transform in (
                lambda rows: rows.append(copy.deepcopy(rows[0])),
                lambda rows: rows.reverse(),
                lambda rows: rows[0].update(size=True),
                lambda rows: rows[0].update(mode=True),
                lambda rows: rows[0].update(mode=0o4755),
                lambda rows: rows[0].update(sha256='bad')):
            manifest, _ = fixture(); transform(manifest['components'][0]['after']); raw = bundle(manifest)
            with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_empty_code_file_is_valid_but_false_size_is_not(self):
        manifest, payloads = fixture()
        manifest['components'][0]['after'][1] = row('main.js', b'')
        payloads[U.PREFIX + 'payload/frontend/main.js'] = b'', 0o644
        raw = bundle(manifest, payloads); self.assertTrue(U.review(raw, U.sha(raw))['archiveDigestMatched'])

    def test_links_directories_sparse_and_duplicate_tar_members_refuse(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:', format=tarfile.USTAR_FORMAT) as archive:
                member = tarfile.TarInfo(U.PREFIX + 'payload/frontend/main.js'); member.type = kind
                member.linkname = '/etc/shadow'; archive.addfile(member)
            raw = output.getvalue()
            with self.subTest(kind=kind), self.assertRaises((ValueError, tarfile.TarError)): U.review(raw, U.sha(raw))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:') as archive:
            for _ in range(2): archive.addfile(tarfile.TarInfo(U.MANIFEST))
        raw = output.getvalue()
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_noncanonical_trailing_bytes_and_untrusted_bounds_refuse(self):
        raw = bundle() + b'unlisted content'
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        with mock.patch.object(U, 'MAX_ARCHIVE', 10), self.assertRaises(ValueError): U.review(bundle(), U.sha(bundle()))
        with mock.patch.object(U, 'MAX_TOTAL', 10), self.assertRaises(ValueError): U.review(bundle(), U.sha(bundle()))
        with mock.patch.object(U, 'MAX_FILES', 1), self.assertRaises(ValueError): U.review(bundle(), U.sha(bundle()))

    def test_all_components_have_fixed_roots_and_sorted_unique_names(self):
        manifest, files = fixture(); component = manifest['components'][0]
        manifest['components'] = [{**copy.deepcopy(component), 'name': name} for name in sorted(U.COMPONENTS)]
        payloads = {name.replace('/frontend/', '/' + component + '/'): value
                    for component in U.COMPONENTS for name, value in files.items()}
        raw = bundle(manifest, payloads); report = U.review(raw, U.sha(raw))
        self.assertEqual({row['component']: row['targetRoot'] for row in report['components']}, U.COMPONENTS)
        manifest['components'].reverse(); raw = bundle(manifest, payloads)
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))
        manifest, files = fixture(); manifest['components'].append(copy.deepcopy(manifest['components'][0]))
        raw = bundle(manifest, files)
        with self.assertRaises(ValueError): U.review(raw, U.sha(raw))

    def test_root_staging_rejected_before_any_input_or_directory_access(self):
        with mock.patch.object(U.sys, 'platform', 'linux'), mock.patch.object(U.os, 'geteuid', return_value=0, create=True):
            with mock.patch.object(U, 'read_file') as reader:
                with self.assertRaises(ValueError): U.stage(Path('/absent'), 'a' * 64, Path('/absent-stage'))
                reader.assert_not_called()

    def test_non_linux_staging_is_not_a_weaker_security_fallback(self):
        with mock.patch.object(U.sys, 'platform', 'win32'), self.assertRaises(ValueError): U.unprivileged_linux()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() != 0, 'Unprivileged Linux filesystem tests')
class PrivateStage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-update-test-', dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = bundle(); self.pin = U.sha(self.raw)
        self.archive = self.root / 'candidate.tar'; self.archive.write_bytes(self.raw)
        self.output = self.root / 'staged'

    def test_private_durable_stage_and_readonly_inspection(self):
        report = U.stage(self.archive, self.pin, self.output)
        self.assertEqual(report, U.inspect_stage(self.output, self.pin))
        self.assertFalse(report['payloadExecuted']); self.assertFalse(report['installable'])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        self.assertEqual({path.name for path in self.output.iterdir()}, {'intent.json', 'bundle.tar', 'review.json', 'complete.json'})
        for path in self.output.iterdir(): self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual((self.output / 'bundle.tar').read_bytes(), self.raw)
        self.assertFalse((self.output / 'payload').exists())
        before = {p.name: U.identity(p.stat()) for p in self.output.iterdir()}
        U.inspect_stage(self.output, self.pin)
        self.assertEqual(before, {p.name: U.identity(p.stat()) for p in self.output.iterdir()})

    def test_existing_completed_or_foreign_directory_never_adopted(self):
        U.stage(self.archive, self.pin, self.output)
        with self.assertRaises(FileExistsError): U.stage(self.archive, self.pin, self.output)
        foreign = self.root / 'foreign'; foreign.mkdir(mode=0o700)
        sentinel = foreign / 'keep.txt'; sentinel.write_bytes(b'keep')
        with self.assertRaises(FileExistsError): U.stage(self.archive, self.pin, foreign)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_interruptions_preserve_original_partial_stage_without_replay(self):
        for phase in ('intent.json', 'bundle.tar', 'review.json'):
            output = self.root / phase
            def fail(name):
                if name == phase: raise InterruptedError('synthetic interruption')
            with self.subTest(phase=phase), self.assertRaises(InterruptedError):
                U.stage(self.archive, self.pin, output, after_write=fail)
            self.assertFalse((output / 'complete.json').exists())
            with self.assertRaises(ValueError): U.inspect_stage(output, self.pin)
            with self.assertRaises(FileExistsError): U.stage(self.archive, self.pin, output)

    def test_payload_receipt_and_extra_file_tampering_refuse(self):
        U.stage(self.archive, self.pin, self.output)
        for name in ('bundle.tar', 'review.json', 'complete.json', 'intent.json'):
            path = self.output / name; original = path.read_bytes(); path.write_bytes(original + b'x')
            with self.subTest(name=name), self.assertRaises(ValueError): U.inspect_stage(self.output, self.pin)
            path.write_bytes(original)
        extra = self.output / 'foreign.txt'; extra.write_bytes(b'foreign')
        with self.assertRaises(ValueError): U.inspect_stage(self.output, self.pin)

    def test_symlink_and_hardlinked_input_refuse(self):
        link = self.root / 'input-link'; link.symlink_to(self.archive)
        with self.assertRaises(OSError): U.stage(link, self.pin, self.output)
        hard = self.root / 'input-hard'; os.link(self.archive, hard)
        with self.assertRaises(ValueError): U.stage(hard, self.pin, self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_and_writable_parent_refuse_before_stage_creation(self):
        link = self.root / 'linked-parent'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): U.stage(self.archive, self.pin, link / 'child')
        writable = self.root / 'writable'; writable.mkdir(mode=0o700); writable.chmod(0o777)
        with self.assertRaises(ValueError): U.stage(self.archive, self.pin, writable / 'child')
        self.assertFalse((writable / 'child').exists())

    def test_changed_stage_mode_or_identity_preserves_partial_and_foreign_files(self):
        def change_mode(name):
            if name == 'bundle.tar': self.output.chmod(0o777)
        with self.assertRaises(ValueError): U.stage(self.archive, self.pin, self.output, after_write=change_mode)
        self.output.chmod(0o700)
        self.assertFalse((self.output / 'complete.json').exists())
        output = self.root / 'replacement'
        def replace(name):
            if name == 'bundle.tar':
                output.rename(self.root / 'retained'); output.mkdir(mode=0o700)
                (output / 'foreign.txt').write_bytes(b'keep')
        with self.assertRaises(ValueError): U.stage(self.archive, self.pin, output, after_write=replace)
        self.assertEqual((output / 'foreign.txt').read_bytes(), b'keep')
        self.assertTrue((self.root / 'retained' / 'intent.json').is_file())

    def test_private_file_permissions_and_links_are_rechecked(self):
        U.stage(self.archive, self.pin, self.output)
        staged = self.output / 'bundle.tar'; staged.chmod(0o644)
        with self.assertRaises(ValueError): U.inspect_stage(self.output, self.pin)
        staged.chmod(0o600); os.link(staged, self.root / 'foreign-link')
        with self.assertRaises(ValueError): U.inspect_stage(self.output, self.pin)

    def test_copied_stage_is_not_adopted_as_original(self):
        U.stage(self.archive, self.pin, self.output)
        copied = self.root / 'copied'; shutil.copytree(self.output, copied)
        with self.assertRaises(ValueError): U.inspect_stage(copied, self.pin)
        self.assertTrue(U.inspect_stage(self.output, self.pin)['staged'])

    def test_change_after_payload_read_is_detected_without_overwriting(self):
        U.stage(self.archive, self.pin, self.output)
        read = U.read_file
        replacement = self.root / 'replacement.tar'; replacement.write_bytes(self.raw); replacement.chmod(0o600)
        def change(path, *args, **kwargs):
            raw = read(path, *args, **kwargs)
            if path.name == 'review.json': os.replace(replacement, self.output / 'bundle.tar')
            return raw
        with mock.patch.object(U, 'read_file', side_effect=change), self.assertRaises(ValueError):
            U.inspect_stage(self.output, self.pin)
        self.assertEqual((self.output / 'bundle.tar').read_bytes(), self.raw)

    def test_invalid_review_creates_no_stage(self):
        with self.assertRaises(ValueError): U.stage(self.archive, 'c' * 64, self.output)
        self.assertFalse(self.output.exists())

    def test_loss_after_durable_completion_is_inspectable_but_not_replayed(self):
        def fail(name):
            if name == 'complete.json': raise InterruptedError('synthetic return-path loss')
        with self.assertRaises(InterruptedError): U.stage(self.archive, self.pin, self.output, after_write=fail)
        self.assertTrue(U.inspect_stage(self.output, self.pin)['staged'])
        with self.assertRaises(FileExistsError): U.stage(self.archive, self.pin, self.output)

    def test_fifo_input_is_rejected_without_blocking(self):
        fifo = self.root / 'input-fifo'; os.mkfifo(fifo, 0o600)
        with self.assertRaises(ValueError): U.stage(fifo, self.pin, self.output)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
