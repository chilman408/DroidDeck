# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('browser_view',Path(__file__).with_name('aggregate-browser-view.py'))
V=importlib.util.module_from_spec(spec);spec.loader.exec_module(V)

class BrowserViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adapter=(ROOT/'runtime/portable/aggregate-gateway.js').read_bytes().replace(b'\r\n',b'\n')

    def test_mapping_preserves_original_command_and_has_only_owned_directives(self):
        for mode,target in [('portable','/opt/droiddeck/runtime'),('legacy','/opt/teslaandroid-waydroid/runtime')]:
            data=V.mapping(mode,'a'*64,'b'*64)
            self.assertEqual(data,('[Service]\nPrivateMounts=yes\nBindReadOnlyPaths=/var/lib/droidmate-browser/'+'a'*64+'/'+mode+'/runtime:'+target+'\nEnvironment=DROIDMATE_BROWSER_FRONTEND=/var/lib/droidmate-browser/'+'b'*64+'/frontend\n').encode())
            self.assertNotIn(b'ExecStart',data)
            self.assertEqual(data.count(b'BindReadOnlyPaths='),1)

    def test_mapping_refuses_path_directive_and_mode_injection(self):
        for mode,release,frontend in [('other','a'*64,'b'*64),('portable','a'*63,'b'*64),
            ('portable','A'*64,'b'*64),('legacy','a'*64,'../frontend'),('legacy','a'*64,'b'*64+'\nExecStart=/bin/true')]:
            with self.assertRaises(ValueError):V.mapping(mode,release,frontend)

    def test_modified_candidate_or_adapter_refused_before_tar_parse(self):
        for package,adapter,mode in [(b'not tar',self.adapter,'portable'),(b'',self.adapter,'legacy'),
            (b'not tar',self.adapter+b'\n','legacy'),(b'not tar',self.adapter,'other')]:
            with self.assertRaises(ValueError):V.runtime_tree(package,adapter,mode)

    def test_wrappers_execute_original_entry_contract_without_dispatching_other_roles(self):
        script="""const fs=require('node:fs'),vm=require('node:vm');
const v=JSON.parse(fs.readFileSync(0,'utf8')), original={startWorker:()=>{}}, module={exports:{}};
let calls=[], errors=[], p={argv:['node','entry',...v.args],env:{DROIDMATE_BROWSER_FRONTEND:v.frontend}};
const originalId=v.mode==='portable'?'./aggregate-original-worker-entry':'./aggregate-original-server';
const adapterId=v.mode==='portable'?'./aggregate-gateway':'../portable/aggregate-gateway';
const requireEntry=name=>{if(name===originalId)return original;if(name!==adapterId)throw Error('Unexpected module ID');return {startGateway:async options=>{
  calls.push({mode:options.mode,frontend:options.frontendDir,args:options.args,
    portableOriginal:options.portable===original.startWorker,legacyOriginal:options.server===original});
}}};
requireEntry.main=v.importOnly?{}:module;
vm.runInNewContext(v.source,{require:requireEntry,module,process:p,console:{error:x=>errors.push(x)}});
setImmediate(()=>console.log(JSON.stringify({calls,errors,exitCode:p.exitCode??0,exportSame:module.exports===original})));
"""
        frontend='/var/lib/droidmate-browser/'+'a'*64+'/frontend'
        for mode,args in [('portable',['gateway']),('legacy',['/home/deck/frontend','8443'])]:
            for import_only in [False,True]:
                value={'source':V.wrapper(mode).decode(),'mode':mode,'args':args,'frontend':frontend,'importOnly':import_only}
                p=subprocess.run(['node','-e',script],input=json.dumps(value),text=True,capture_output=True,check=True,timeout=10)
                result=json.loads(p.stdout);self.assertEqual(result['errors'],[]);self.assertEqual(result['exitCode'],0)
                self.assertTrue(result['exportSame'])
                self.assertEqual(len(result['calls']),0 if import_only else 1)
                if not import_only:
                    call=result['calls'][0];self.assertEqual(call['mode'],mode);self.assertEqual(call['frontend'],frontend)
                    self.assertEqual(call['args'],[] if mode=='portable' else args)
                    self.assertEqual(call['portableOriginal'],mode=='portable');self.assertEqual(call['legacyOriginal'],mode=='legacy')
        for args in [[],['video'],['gateway','extra']]:
            p=subprocess.run(['node','-e',script],input=json.dumps({'mode':'portable','source':V.wrapper('portable').decode(),
                'args':args,'frontend':frontend}),text=True,capture_output=True,check=True,timeout=10)
            result=json.loads(p.stdout);self.assertEqual(result['calls'],[]);self.assertEqual(result['exitCode'],1)
            self.assertEqual(result['errors'],['Aggregate browser startup rejected'])

    def test_actual_fixed_package_complete_view_retains_every_original_file(self):
        source=ROOT/'build/private/droidmate-combined-20261107ov/package.tar'
        if not source.exists():self.skipTest('Actual qualified artifact supplied separately on Linux')
        package=source.read_bytes();original=V.capture_runtime(package)
        self.assertEqual(len(original),241)
        for mode,entry,archived in [('portable','portable/worker-entry.js','portable/aggregate-original-worker-entry.js'),
            ('legacy','preview-gateway/server.js','preview-gateway/aggregate-original-server.js')]:
            tree=V.runtime_tree(package,self.adapter,mode)
            self.assertEqual(len(tree),243)
            self.assertEqual(set(tree)-set(original),{archived,'portable/aggregate-gateway.js'})
            self.assertEqual(tree[archived],original[entry]);self.assertEqual(tree[entry],V.wrapper(mode))
            self.assertEqual(tree['portable/aggregate-gateway.js'],self.adapter)
            for name,data in original.items():
                if name!=entry:self.assertEqual(tree[name],data,name)
            self.assertTrue(any('/node_modules/' in n for n in tree))

if __name__=='__main__':unittest.main()
