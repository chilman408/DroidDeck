# SPDX-License-Identifier: GPL-3.0-only
import contextlib,importlib.util,io,json,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('aggregate_release',Path(__file__).with_name('aggregate-release.py'))
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)

class AggregateReleaseTests(unittest.TestCase):
    def setUp(self):
        self.components=b'UNIT FIXTURE component bytes, never a real release\x00\x1a\xff'
        self.frontend={'LICENSE':b'Fixture license\n','index.html':b'<title>DroidMate</title>\n'}
        meta={'files':A.records(self.frontend)}
        self.frontend['bundle-manifest.json']=(json.dumps(meta,indent=2)+'\n').encode()
        self.sources={}
        for n in A.SOURCES:
            path=ROOT/('runtime/portable/gpl-3.0.txt' if n=='COPYING' else n)
            if n=='COPYING' and not path.exists():path=ROOT/'COPYING'
            self.sources[n]=path.read_bytes().replace(b'\r\n',b'\n') if path.exists() else (b'pass\n' if n.endswith('.py') else b'Candidate public instructions\n')
        self.stack=[]
        for n,v in [('COMPONENTS',(len(self.components),A.sha(self.components))),('FRONTEND_PIN',A.sha(self.frontend['bundle-manifest.json'])),('FRONTEND_COUNT',2)]:
            p=patch.object(A,n,v);p.start();self.stack.append(p)
        self.rev='a'*40

    def tearDown(self):
        for p in reversed(self.stack):p.stop()

    def build(self,**changes):
        return A.build(changes.get('components',self.components),changes.get('frontend',self.frontend),changes.get('sources',self.sources),changes.get('revision',self.rev))

    def test_complete_deterministic_distribution_preserves_original_core_and_executes_no_payload(self):
        self.sources['tools/aggregate-install.py']=b"raise RuntimeError('Payload executed')\n"
        raw=self.build();self.assertEqual(raw,self.build(frontend=dict(reversed(list(self.frontend.items()))),sources=dict(reversed(list(self.sources.items())))))
        report=A.review(raw);self.assertEqual(report['aggregateRelease'],'RC18');self.assertEqual(report['coreRuntimeVersion'],'0.1.1-rc.20')
        self.assertEqual(report['compatibleReleaseSha256'],A.CORE);self.assertFalse(report['payloadExecuted']);self.assertFalse(report['readyForPublication'])
        self.assertEqual(report['componentsSha256'],A.sha(self.components));self.assertEqual(report['frontendFiles'],2)
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
            self.assertEqual(t.extractfile(A.PREFIX+'components.tar').read(),self.components)
            self.assertTrue(all(m.type==tarfile.REGTYPE and m.mode==0o644 and not m.pax_headers and not m.uid and not m.gid and not m.mtime for m in t))

    def test_fixed_component_and_frontend_pin_tampering_refuses(self):
        with self.assertRaises(ValueError):self.build(components=self.components+b'changed')
        for name in self.frontend:
            changed=dict(self.frontend);changed[name]+=b'changed'
            with self.assertRaises(ValueError):self.build(frontend=changed)
        for changed in [{**self.frontend,'../key.pem':b'private'},dict(list(self.frontend.items())[1:])]:
            with self.assertRaises(ValueError):self.build(frontend=changed)

    def test_closed_source_set_frozen_code_license_revision_and_private_material_refuse(self):
        for n in [next(iter(A.FROZEN)),'COPYING']:
            changed=dict(self.sources);changed[n]+=b'changed'
            with self.assertRaises(ValueError):self.build(sources=changed)
        for changes in [{'../private.py':b'pass\n'},{'tools/aggregate-install.py':b'-----BEGIN PRIVATE KEY-----\n'+b'A'*32},
                        {'tools/aggregate-install.py':b'ghp_'+b'A'*40},{'tools/aggregate-install.py':b'if ('}]:
            with self.assertRaises((ValueError,SyntaxError)):self.build(sources={**self.sources,**changes})
        for rev in ['', 'A'*40,'a'*39,True]:
            with self.assertRaises(ValueError):self.build(revision=rev)

    def test_strict_json_rejects_duplicates_nonfinite_noncanonical_and_unknown_manifest_fields(self):
        for raw in [b'{"a":1,"a":1}\n',b'{"a":NaN}\n',b'{"a":Infinity}\n',b'{ "a": 1 }\n']:
            with self.assertRaises(ValueError):A.document(raw)
        files=A.unpack(self.build());m=A.document(files[A.MANIFEST])
        for changes in [{'schema':True},{'extra':True},{'coreRuntimeVersion':'RC18'}, {'compatibleReleaseSha256':'0'*64},
                        {'contract':{**A.CONTRACT,'healthyExpiry':True}}]:
            changed=dict(files);changed[A.MANIFEST]=A.encode({**m,**changes})
            with self.assertRaises(ValueError):A.review(A.archive(changed))

    def test_tar_duplicates_links_metadata_trailing_data_and_member_changes_refuse(self):
        raw=self.build();files=A.unpack(raw)
        for changed in [{**files,'foreign':b'x'},dict(list(files.items())[1:]),{**files,A.PREFIX+'components.tar':self.components+b'x'}]:
            with self.assertRaises(ValueError):A.review(A.archive(changed))
        with self.assertRaises(ValueError):A.review(raw+b'trailing')
        for kind in ['duplicate','link','mode']:
            out=io.BytesIO()
            with tarfile.open(fileobj=out,mode='w:',format=tarfile.USTAR_FORMAT) as t:
                for i,(name,data) in enumerate(sorted(files.items())):
                    m=tarfile.TarInfo(name);m.size=len(data);m.mode=0o644
                    if i==0 and kind=='link':m.type=tarfile.SYMTYPE;m.linkname='/tmp/foreign';m.size=0
                    if i==0 and kind=='mode':m.mode=0o755
                    t.addfile(m,io.BytesIO(data))
                    if i==0 and kind=='duplicate':t.addfile(m,io.BytesIO(data))
            with self.assertRaises(ValueError):A.review(out.getvalue())

    def test_separate_statement_binds_entire_package_manifest_source_contract_and_expected_key(self):
        raw=self.build();key='b'*64;statement=A.statement(raw,key)
        result=A.review_signing_inputs(raw,statement,key)
        self.assertEqual(result['kind'],'droidmate-aggregate-signing-inputs');self.assertEqual(result['statementSha256'],A.sha(statement))
        value=A.document(statement)
        for changes in [{'kind':'droiddeck-capture-activation-authorization'},{'aggregateRelease':'RC16'},
            {'coreRuntimeVersion':'0.1.1-rc.17'},{'activationAllowed':False},{'keySha256':'c'*64},
            {'packageSha256':'d'*64},{'manifestSha256':'e'*64},{'contractSha256':'f'*64},{'sourceRevision':'0'*40},{'extra':True}]:
            with self.assertRaises(ValueError):A.review_signing_inputs(raw,A.encode({**value,**changes}),key)
        with self.assertRaises(ValueError):A.review_signing_inputs(raw,statement[:-1],key)
        with self.assertRaises(ValueError):A.review_signing_inputs(raw+b'changed',statement,key)

    def test_qualified_git_collector_uses_exact_committed_root_blobs_not_working_or_replacement_bytes(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-aggregate-git-') as tmp:
            root=Path(tmp)/'repo';root.mkdir()
            for n,b in self.sources.items():
                p=root/('runtime/portable/gpl-3.0.txt' if n=='COPYING' else n);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL).decode().strip()
            git('init','-q');git('add','.')
            git('-c','user.name=Aggregate Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','Fixture')
            rev=git('rev-parse','HEAD');target='docs/RC18-INSTALL.md';obj=git('rev-parse',rev+':'+target)
            (root/target).write_bytes(b'WORKING COPY SHOULD NOT ENTER RELEASE\n')
            replacement=git('hash-object','-w',target);git('replace',obj,replacement)
            self.assertEqual(A.git_sources(root,rev),self.sources)
            self.assertEqual(A.git_sources(root/'tools',rev),self.sources)

    def test_cli_and_git_free_source_reader_preserve_binary_inputs_and_refuse_changed_source(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-aggregate-cli-') as tmp:
            root=Path(tmp);source=root/'source';source.mkdir();front=root/'frontend';front.mkdir()
            for n,b in self.sources.items():
                p=source/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
            for n,b in self.frontend.items():(front/n).write_bytes(b)
            components=root/'components.tar';components.write_bytes(self.components);output=root/'new-release.tar'
            pin=A.sha(A.source_checkpoint(self.sources,self.rev))
            self.assertEqual(A.source_tree(source,self.rev,pin),self.sources)
            args=['aggregate-release','build','--checkpoint',str(source),'--source-checkpoint-sha256',pin,
                '--revision',self.rev,'--components',str(components),'--frontend',str(front),'--output',str(output)]
            with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):A.main()
            self.assertEqual(output.read_bytes(),self.build())
            (source/'tools/aggregate-install.py').write_bytes(b'pass # substituted\n')
            with self.assertRaises(ValueError):A.source_tree(source,self.rev,pin)

    def test_external_git_free_checkpoint_cannot_be_relabelled_as_a_different_revision(self):
        with tempfile.TemporaryDirectory(prefix='droidmate-aggregate-revision-') as tmp:
            root=Path(tmp)
            for n,b in self.sources.items():
                p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
            pin=A.sha(A.source_checkpoint(self.sources,self.rev))
            self.assertEqual(A.source_tree(root,self.rev,pin),self.sources)
            with self.assertRaises(ValueError):A.source_tree(root,'b'*40,pin)

if __name__=='__main__':unittest.main()
