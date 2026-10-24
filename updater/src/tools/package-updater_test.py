# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

s = importlib.util.spec_from_file_location('packager_tested', Path(__file__).with_name('package-updater.py'))
D = importlib.util.module_from_spec(s); s.loader.exec_module(D)

class Package(unittest.TestCase):
    def inputs(self):
        p = D.P; version = '0.1.1-rc.5'; revision = 'e' * 40
        launcher = Path(__file__).with_name('update-installer-entry.py').read_bytes()
        files = {'runtime/portable/gateway-update-release.py': b'# generated bootstrap fixture\n', 'tools/update-installer-entry.py': launcher}
        source_manifest = {'schema': 1, 'kind': 'droiddeck-runtime-update-source', 'sourceRevision': revision,
            'version': version, 'installerIncluded': False, 'files': [
                {'path': n, 'size': len(r), 'sha256': p.sha(r)} for n, r in sorted(files.items())]}
        source = p.canonical_archive({'droiddeck-update-source/source-manifest.json': (p.encode(source_manifest), 0o644),
            **{'droiddeck-update-source/src/' + n: (r, 0o644) for n, r in files.items()}})
        old, new = b'// before\n', b'// after\n'
        row = lambda raw: [{'path':'server.js','size':len(raw),'sha256':p.sha(raw),'mode':0o644}]
        manifest = {'schema': 1, 'kind': 'droiddeck-runtime-update', 'base': p.BASE,
            'target': {'version':version,'sourceRevision':revision,'sourceSha256':p.sha(source)},
            'platform':p.PLATFORM,'dataPolicy':'preserve','components':[{'name':'gateway','before':row(old),'after':row(new)}]}
        bundle = p.canonical_archive({p.MANIFEST:(p.encode(manifest),0o644),p.PREFIX+'payload/gateway/server.js':(new,0o644)})
        statement = p.encode({'schema':1,'kind':'droiddeck-runtime-release','channel':'pilot','keySha256':D.E.KEY_PIN,
            'bundleSha256':p.sha(bundle),'manifestSha256':p.sha(p.encode(manifest)),
            **{k:manifest[k] for k in ('base','target','platform','dataPolicy')}})
        return source,bundle,statement,b'x'*64,launcher

    def test_reproducible_single_file_has_only_public_closed_members(self):
        with patch.object(D.E,'verify_signature',lambda *_:None):
            first, report = D.build(*self.inputs()); second,_ = D.build(*self.inputs())
        self.assertEqual(first,second)
        with zipfile.ZipFile(io.BytesIO(first)) as archive:
            self.assertEqual(set(archive.namelist()),D.E.ENTRIES)
            self.assertEqual(archive.read('__main__.py'),self.inputs()[-1])
        self.assertTrue(report['sourceAndPayloadAuthenticated'])
        self.assertFalse(report['privateKeyIncluded'])

    def test_launcher_must_equal_exact_signed_committed_source(self):
        inputs = list(self.inputs()); inputs[-1] += b'# uncommitted fixture change\n'
        with patch.object(D.E,'verify_signature',lambda *_:None):
            with self.assertRaises(ValueError): D.build(*inputs)

    def test_signature_failure_prevents_packaging(self):
        with patch.object(D.E,'verify_signature',side_effect=ValueError('generated invalid signature')):
            with self.assertRaises(ValueError): D.build(*self.inputs())

if __name__=='__main__':unittest.main()
