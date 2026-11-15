# SPDX-License-Identifier: GPL-3.0-only
"""Offline hostile-package tests. Optional real publisher inputs, never services."""
import copy
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('capture_package_tested', Path(__file__).with_name('capture-runtime-package.py'))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)
REVISION = 'e0b3bf7f787dc6aa9997bd50659627d90fd3a9a6'
REPO = Path(__file__).resolve().parents[2]
DEPS = os.environ.get('DROIDDECK_CAPTURE_PACKAGE_TEST_DEPENDENCIES')


class Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = C.selected_source(REPO, REVISION)

    def test_complete_fixed_worker_tree_and_local_imports(self):
        self.assertEqual(len(self.source), 96)
        self.assertEqual(C.validate_source(self.source), '0.1.1-rc.20')
        self.assertIn('portable/capture-policy-controller.py', self.source)
        self.assertIn('portable/read-bounded.js', self.source)
        self.assertFalse(any('test' in name or 'tls.key' in name for name in self.source))

    def test_missing_source_and_new_unfulfilled_local_import_refused(self):
        value = dict(self.source); del value['portable/capture-policy.js']
        with self.assertRaises(ValueError): C.validate_source(value)
        value = dict(self.source)
        value['portable/capture-policy.js'] += b"\nrequire('./not-in-package');\n"
        with self.assertRaisesRegex(ValueError, 'Missing local runtime dependency'): C.validate_source(value)

    def test_package_lock_and_original_catalog_pins_cannot_be_redefined(self):
        for name in ('preview-gateway/package-lock.json', 'waydroid-display/package.json', 'portable/capture-release-verifier.json', 'preview-gateway/droiddeck-logo.png'):
            value = dict(self.source); value[name] += b' '
            with self.subTest(name=name), self.assertRaises(ValueError): C.validate_source(value)

    def test_git_commit_only_ignores_dirty_sources_and_private_unselected_files(self):
        with tempfile.TemporaryDirectory(prefix='capture-source-fixture-') as directory:
            root = Path(directory)
            for name, raw in self.source.items():
                path = root / C.SOURCE_PATHS[name]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
            git = lambda *args: C.T.git(root, list(args))
            git('init', '-q'); git('config', 'user.name', 'Generated Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'core.autocrlf', 'false'); git('add', 'runtime'); git('commit', '-qm', 'Generated source')
            revision = git('rev-parse', 'HEAD').decode().strip()
            (root/'runtime/portable/capture-policy.js').write_bytes(b'dirty uncommitted sentinel')
            (root/'runtime/credentials.json').write_bytes(b'generated unselected sentinel')
            self.assertEqual(C.selected_source(root, revision), self.source)
            git('rm', '--cached', 'runtime/portable/capture-policy.js'); git('commit', '-qm', 'Generated incomplete source')
            with self.assertRaises(ValueError): C.selected_source(root, git('rev-parse', 'HEAD').decode().strip())
            with self.assertRaises(ValueError): C.selected_source(root, '--all')


@unittest.skipUnless(DEPS, 'Set explicit pinned publisher dependency test root')
class Package(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = C.selected_source(REPO, REVISION)
        cls.deps = {role: C.dependency_files(role, Path(DEPS)/role) for role in ('gateway', 'display')}
        cls.raw, cls.report = C.make_archive(REVISION, cls.source, cls.deps)
        with tarfile.open(fileobj=io.BytesIO(cls.raw), mode='r:') as archive:
            cls.members = {r.name: (archive.extractfile(r).read(), r.mode) for r in archive}

    def reject(self, members):
        raw = C.P.canonical_archive(members)
        with self.assertRaises((ValueError, KeyError, TypeError)): C.review(raw, C.P.sha(raw))

    def test_full_real_publisher_package_is_reproducible_and_inactive(self):
        other, report = C.make_archive(REVISION, self.source, self.deps)
        self.assertEqual(self.raw, other); self.assertEqual(self.report, report)
        self.assertEqual((report['sourceFiles'], report['dependencyFiles'], report['files']), (96, 145, 241))
        for flag in ('signed', 'installable', 'readyForActivation', 'payloadExecuted', 'servicesChanged'):
            self.assertIs(report[flag], False)
        with self.assertRaises(ValueError): C.P.review(self.raw, C.P.sha(self.raw))

    def test_wrong_digest_modified_payload_missing_role_or_extra_file_refused(self):
        with self.assertRaises(ValueError): C.review(self.raw, '0'*64)
        for name in (C.PREFIX+'runtime/portable/capture-policy.js', C.PREFIX+'dependencies/display.json'):
            value = dict(self.members); del value[name]; self.reject(value)
        value = dict(self.members); key = C.PREFIX+'runtime/portable/capture-policy.js'; value[key] = (value[key][0]+b' ', 0o644); self.reject(value)
        value = dict(self.members); value[C.PREFIX+'runtime/credentials.json'] = (b'generated excluded sentinel', 0o644); self.reject(value)

    def test_dependency_tamper_even_with_recomputed_unsigned_manifest_refused(self):
        value = dict(self.members)
        key = next(n for n in value if '/node_modules/' in n)
        raw = value[key][0] + b' '; value[key] = (raw, 0o644)
        manifest = C.P.document(value[C.MANIFEST][0])
        row = next(r for r in manifest['files'] if C.PREFIX+'runtime/'+r['path'] == key)
        row.update(size=len(raw), sha256=C.P.sha(raw)); value[C.MANIFEST] = (C.P.encode(manifest), 0o644)
        self.reject(value)
        deps = copy.deepcopy(self.deps); del deps['display']
        with self.assertRaises(ValueError): C.make_archive(REVISION, self.source, deps)

    def test_links_duplicates_noncanonical_archive_and_json_refused(self):
        for type_ in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w:', format=tarfile.USTAR_FORMAT) as archive:
                item = tarfile.TarInfo(C.MANIFEST); item.type = type_; item.linkname = '/etc/shadow'; archive.addfile(item)
            raw = output.getvalue()
            with self.subTest(type=type_), self.assertRaises(ValueError): C.review(raw, C.P.sha(raw))
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:', format=tarfile.USTAR_FORMAT) as archive:
            for _ in range(2):
                item = tarfile.TarInfo(C.MANIFEST); item.mode = 0o644; item.size = 2; archive.addfile(item, io.BytesIO(b'{}'))
        raw = output.getvalue()
        with self.assertRaises(ValueError): C.review(raw, C.P.sha(raw))
        raw = self.raw + b'\0'*512
        with self.assertRaises(ValueError): C.review(raw, C.P.sha(raw))
        value = dict(self.members); value[C.MANIFEST] = (value[C.MANIFEST][0]+b' ', 0o644); self.reject(value)

    def test_contract_cannot_claim_activation_or_change_preservation(self):
        for key, changed in [('installable', True), ('schema', True), ('kind', 'droiddeck-runtime-update'), ('runtimeVersion', '0.1.1-rc.21')]:
            value = dict(self.members); manifest = C.P.document(value[C.MANIFEST][0]); manifest[key] = changed
            value[C.MANIFEST] = (C.P.encode(manifest), 0o644)
            with self.subTest(key=key): self.reject(value)
        value = dict(self.members); manifest = C.P.document(value[C.MANIFEST][0]); manifest['contract']['androidData'] = 'erase'
        value[C.MANIFEST] = (C.P.encode(manifest), 0o644); self.reject(value)

    def test_real_dependency_reader_refuses_extras_and_hardlinks(self):
        with tempfile.TemporaryDirectory(prefix='capture-dependency-fixture-') as directory:
            target = Path(directory)/'gateway'; shutil.copytree(Path(DEPS)/'gateway', target)
            extra = target/'unexpected'; extra.mkdir()
            with self.assertRaises(ValueError): C.dependency_files('gateway', target)
            extra.rmdir(); extra = target/'unexpected.json'; extra.write_bytes(b'{}')
            with self.assertRaises(ValueError): C.dependency_files('gateway', target)
            extra.unlink()
            entry = next(n for n in target.rglob('*') if n.is_file() and n.name != 'bundle-manifest.json')
            duplicate = Path(directory)/'held-hardlink'; os.link(entry, duplicate)
            with self.assertRaises(ValueError): C.dependency_files('gateway', target)


if __name__ == '__main__': unittest.main(verbosity=2)
