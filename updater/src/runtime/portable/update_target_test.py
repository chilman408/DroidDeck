# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('target_tested', Path(__file__).with_name('update-target.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class Target(unittest.TestCase):
    def test_manifest_target_and_gateway_health_use_one_release_record(self):
        record=Path(__file__).parents[1]/'preview-gateway/release-version.json'
        self.assertTrue(record.is_file(),'Shared gateway/package version record required')
        self.assertEqual(D.VERSION,D.P.document(record.read_bytes())['version'])
        self.assertEqual(D.VERSION,'0.1.1-rc.16')
        self.assertIn('release-version.json',D.GATEWAY)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-target-build-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        self.gateway = self.repo / 'runtime/preview-gateway'; self.gateway.mkdir(parents=True)
        for name in D.GATEWAY: (self.gateway / name).write_bytes(('// fixture source ' + name + '\n').encode())
        (self.gateway/'release-version.json').write_bytes(D.P.encode({'version':D.VERSION}))
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture'); self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'core.autocrlf', 'false'); self.git('add', 'runtime')
        self.git('commit', '-qm', 'generated target fixture')
        self.revision = self.git('rev-parse', 'HEAD').decode().strip()
        self.dep = self.root / 'dependencies'; self.dep.mkdir()
        (self.dep / 'demo').mkdir()
        raw = b'// publisher fixture dependency\n'
        D.write_new(self.dep / 'demo/index.js', raw)
        D.write_new(self.dep / 'bundle-manifest.json', b'fixture pinned independently by test\n')
        item = patch.object(D.D, 'dependency_records', lambda role, manifest: {
            'demo/index.js': (len(raw), D.P.sha(raw))} if role == 'gateway' and manifest == b'fixture pinned independently by test\n' else {})
        item.start(); self.addCleanup(item.stop)
        self.inventory = D.P.encode({'schema': 1, 'kind': 'original-runtime-code-inventory', 'base': D.P.BASE,
            'components': [{'name': name, 'root': root, 'files': [
                {'path': 'index.js', 'size': 3, 'sha256': D.P.sha(b'old'), 'mode': 0o644}]} for name, root in sorted(D.P.COMPONENTS.items())]})

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], stderr=subprocess.PIPE)

    def source(self):
        return D.source_archive(self.repo, self.revision)

    def test_two_builds_equal_and_only_gateway_target_contains_committed_source_and_dependencies(self):
        source = self.source()
        self.assertEqual(source, self.source())
        (self.gateway / 'server.js').write_bytes(b'foreign uncommitted source')
        first, receipt = D.build(source, self.inventory, self.dep)
        second, repeated = D.build(self.source(), self.inventory, self.dep)
        self.assertEqual(first, second); self.assertEqual(receipt, repeated)
        self.assertFalse(receipt['payloadExecuted']); self.assertFalse(receipt['readyForApply'])
        self.assertFalse(receipt['nativeArtifactsChanged']); self.assertEqual(receipt['publisherDependencyFiles'], 1)
        self.assertEqual([row['component'] for row in receipt['components']], ['gateway'])
        with tarfile.open(fileobj=io.BytesIO(first), mode='r:') as archive:
            self.assertEqual(archive.extractfile(D.P.PREFIX + 'payload/gateway/server.js').read(), b'// fixture source server.js\n')

    def test_source_tar_tamper_link_or_extra_bytes_refuses(self):
        raw = self.source()
        for changed in (raw + b'x', raw[:-10240], raw.replace(b'fixture source server.js', b'foreign source server.js', 1)):
            with self.assertRaises((ValueError, KeyError, tarfile.TarError)): D.sources(changed)
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:') as archive:
            item = tarfile.TarInfo('link'); item.type = tarfile.SYMTYPE; item.linkname = '/etc/passwd'; archive.addfile(item)
        with self.assertRaises(ValueError): D.sources(output.getvalue())

    def test_committed_gateway_version_drift_refuses_payload_before_signing(self):
        (self.gateway/'release-version.json').write_bytes(D.P.encode({'version':'0.1.1-rc.5'}))
        self.git('add','runtime');self.git('commit','-qm','generated stale gateway version fixture')
        revision=self.git('rev-parse','HEAD').decode().strip()
        with self.assertRaisesRegex(ValueError,'Gateway health identity'):
            D.build(D.source_archive(self.repo,revision),self.inventory,self.dep)

    def test_unknown_source_or_noncommit_revision_refuses(self):
        for name in ('runtime/../escape.js', 'runtime/.ssh/key.js', '/runtime/server.js', 'runtime/.env', 'tools/credential.pem'):
            with self.assertRaises(ValueError): D.source_name(name)
        for revision in ('HEAD', 'main', self.revision.upper(), '../escape'):
            with self.assertRaises(ValueError): D.source_archive(self.repo, revision)

    def test_foreign_dependency_bytes_or_extra_file_refuses(self):
        (self.dep / 'demo/index.js').write_bytes(b'foreign')
        with self.assertRaises(ValueError): D.build(self.source(), self.inventory, self.dep)
        (self.dep / 'demo/extra.js').write_bytes(b'foreign')
        with self.assertRaises(ValueError): D.build(self.source(), self.inventory, self.dep)

    @unittest.skipUnless(os.name != 'nt', 'Native filesystem no-follow fixture')
    def test_dependency_symlink_refuses(self):
        path = self.dep / 'demo/index.js'; path.unlink(); path.symlink_to(self.gateway / 'server.js')
        with self.assertRaises(ValueError): D.build(self.source(), self.inventory, self.dep)

    def test_original_inventory_is_complete_fixed_and_does_not_authorize_installation(self):
        original = D.P.document(self.inventory)
        for value in ({**original, 'base': {}}, {**original, 'extra': True},
                      {**original, 'components': original['components'][::-1]}):
            with self.assertRaises(ValueError): D.build(self.source(), D.P.encode(value), self.dep)
        self.assertNotIn(b'installable":true', D.build(self.source(), self.inventory, self.dep)[0])

    def test_new_output_never_overwrites_existing_artifact(self):
        path = self.root / 'artifact.tar'; D.write_new(path, b'original')
        with self.assertRaises(FileExistsError): D.write_new(path, b'foreign')
        self.assertEqual(path.read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
