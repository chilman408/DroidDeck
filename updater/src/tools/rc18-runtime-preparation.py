#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare an unsigned RC18 successor from frozen RC17 and signed new capture inputs.

Pure offline compiler. No installed-tree inputs, unit operations, key access or
transaction replay. The original RC17 recipes and published bytes stay immutable.
Includes a new preserving portable predecessor handoff; VM qualification is required.
"""
import ast,base64,hashlib,importlib.util,io,json,re,subprocess,sys,tarfile,types
from pathlib import Path

PREDECESSOR='7c9a48e879a71cd69d03ce587e2feeef32f231a375c3be3deb685c5b963950d1'
PUBLIC='302a300506032b65700321009d63ba428047b862b5cd1fb5ac994814d985b9a015ecf5805de1261d7825e69c'
KEY='4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f'
ROOT=Path(__file__).resolve().parents[1]

def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode('ascii')
def load(name):
 spec=importlib.util.spec_from_file_location('rc18_'+name.replace('-','_'),ROOT/'runtime/portable'/(name+'.py'))
 value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def read(path,maximum):
 path=Path(path);info=path.lstat()
 if not path.is_file() or path.is_symlink() or info.st_nlink!=1 or not 0<info.st_size<=maximum:raise ValueError('Bounded regular public input required')
 raw=path.read_bytes()
 if len(raw)!=info.st_size:raise ValueError('Input changed')
 return raw

def members(raw):
 result={}
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as archive:
  for item in archive:
   if len(result)>=512:raise ValueError('Bounded public archive inventory required')
   parts=item.name.split('/')
   if (item.type!=tarfile.REGTYPE or item.pax_headers or item.sparse is not None or item.linkname or item.name in result
       or item.name.startswith('/') or any(p in ('','..','.') for p in parts) or not 0<item.size<=40*1024**2):raise ValueError('Closed public archive members required')
   result[item.name]=archive.extractfile(item).read()
   if len(result[item.name])!=item.size:raise ValueError('Complete archive member required')
 return result

def compile_policy(raw,name):
 ast.parse(raw,filename=name);module=types.ModuleType('offline_'+name.replace('-','_'));module.__file__='<verified-offline-'+name+'>'
 exec(compile(raw,module.__file__,'exec'),module.__dict__);return module

def verify_public_signature(statement,signature):
 if len(signature)!=64:raise ValueError('Exact Ed25519 signature required')
 script="const c=require('crypto');let x='';process.stdin.on('data',b=>x+=b);process.stdin.on('end',()=>{const v=JSON.parse(x);const k=c.createPublicKey({key:Buffer.from(v.key,'hex'),format:'der',type:'spki'});if(!c.verify(null,Buffer.from(v.statement,'base64'),k,Buffer.from(v.signature,'base64')))process.exit(1);});"
 request={'key':PUBLIC,'statement':base64.b64encode(statement).decode(),'signature':base64.b64encode(signature).decode()}
 p=subprocess.run(['node','-e',script],input=encode(request),capture_output=True,timeout=15)
 if p.returncode:raise ValueError('Public signature verification failed')

def replace_exact(raw,before,after):
 if before not in raw:raise ValueError('Frozen recipe anchor absent')
 return raw.replace(before,after)

def prepare(predecessor,component,activation,activation_statement,activation_signature,component_statement,component_signature,revision):
 if not re.fullmatch('[a-f0-9]{40}',revision):raise ValueError('Exact producer source revision required')
 if sha(predecessor)!=PREDECESSOR:raise ValueError('Original published RC17 required')
 A=load('capture-activation-auth');C=A.C
 A.review_signing_inputs(activation,activation_statement,KEY);C.review_signing_inputs(component,component_statement,KEY)
 verify_public_signature(activation_statement,activation_signature);verify_public_signature(component_statement,component_signature)
 new_package=members(activation)
 if new_package[A.COMPONENT]!=component:raise ValueError('One matching complete component required')
 observed=C.C.review(component,sha(component));new_count=observed['files']
 original=members(predecessor);prefix='droidmate-rc17-release/'
 sources={n[len(prefix+'source/'):]:v for n,v in original.items() if n.startswith(prefix+'source/')}
 original_policy=compile_policy(sources['tools/aggregate-release.py'],'predecessor_release')
 original_policy.review(predecessor)
 components=members(original[prefix+'components.tar']);cp='droidmate-rc17-components/'
 recipe_source=components[cp+'source/tools/package-rc17-components.py']
 old_recipe=compile_policy(recipe_source,'predecessor_components');old_recipe.inspect(original[prefix+'components.tar'])
 assets={role:components[cp+'assets/'+row['name']] for role,row in old_recipe.ASSETS.items()}
 assets.update(capturePackage=activation,captureStatement=activation_statement,captureSignature=activation_signature)
 replacements={
  old_recipe.ASSETS['capturePackage']['sha256']:sha(activation),
  old_recipe.ASSETS['captureStatement']['sha256']:sha(activation_statement),
  old_recipe.ASSETS['captureSignature']['sha256']:sha(activation_signature),
  '0747a1d63f455cfcf003f326d7f9ed3c5e6de63dfee5ae4498799384d033bc92':sha(component),
  sha(sources['runtime/portable/capture-component-staging.statement.json']):sha(component_statement),
  sha(sources['runtime/portable/capture-component-staging.sig']):sha(component_signature),
 }
 def text(raw):
  value=raw.decode('utf-8').replace('RC17','RC18').replace('rc17','rc18')
  for before,after in replacements.items():value=value.replace(before,after)
  return value.encode('utf-8')
 new_recipe=text(recipe_source)
 for role,raw in [('capturePackage',activation),('captureStatement',activation_statement),('captureSignature',activation_signature)]:
  old=old_recipe.ASSETS[role];new_recipe=replace_exact(new_recipe, ("'size':"+str(old['size'])+",'sha256':'"+sha(raw)+"'").encode(),("'size':"+str(len(raw))+",'sha256':'"+sha(raw)+"'").encode())
 recipe=compile_policy(new_recipe,'successor_components')
 # Use explicit three-file names; never adopt an archive-derived destination.
 recipe_sources={'COPYING':sources['COPYING'],'docs/RC18-COMPONENTS.md':text(components[cp+'source/docs/RC17-COMPONENTS.md']),'tools/package-rc18-components.py':new_recipe}
 new_components=recipe.build(assets,recipe_sources,revision);recipe.inspect(new_components)
 replacements[sha(recipe_source)]=sha(new_recipe)
 view=text(sources['tools/aggregate-browser-view.py'])
 view=replace_exact(view,b'len(package)!=3246080',('len(package)!='+str(len(activation))).encode())
 view=replace_exact(view,b'len(files)!=241',('len(files)!='+str(new_count)).encode())
 replacements[sha(sources['tools/aggregate-browser-view.py'])]=sha(view)
 replacements[sha(original[prefix+'components.tar'])]=sha(new_components)
 output={}
 for name,raw in sources.items():
  new_name=name.replace('RC17','RC18').replace('rc17','rc18')
  if name=='tools/package-rc17-components.py':value=new_recipe
  elif name=='tools/aggregate-browser-view.py':value=view
  elif name=='runtime/portable/capture-component-staging.statement.json':value=component_statement
  elif name=='runtime/portable/capture-component-staging.sig':value=component_signature
  elif name.startswith('tools/aggregate') or name.startswith('docs/RC17-'):value=text(raw)
  else:value=raw # Independently frozen loader/stager/native helpers stay byte-exact.
  if name=='tools/aggregate-enroll.py':
   old=b"""                if value['packageSha256']!=PACKAGE or value['phase']!='restored':raise ValueError('Existing original CPU operation required before fresh enrollment')
                token=value['viewToken']
                with view.hold_view(token) as original:
                    if original['report']['mode']!='gpu' or original['report']['componentSha256']!=COMPONENT:raise ValueError('Original complete GPU view required')
                    original['check']()
"""
   new=b"""                if value['phase']!='restored':raise ValueError('Restored CPU operation required before successor enrollment')
                if value['packageSha256']==PACKAGE:
                    token=value['viewToken']
                    with view.hold_view(token) as original:
                        if original['report']['mode']!='gpu' or original['report']['componentSha256']!=COMPONENT:raise ValueError('Original complete GPU view required')
                        original['check']()
                elif value['packageSha256'] in ('a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da','baf49445cc9e331d6ee9a3ba71eb48ad3e9723301ff60874f99723e3f73d0526','14703568a3cf09351bbfa8e1f435b5cec0cb936ea006eb6e8316a5c9d2ec141b'):
                    # Keep the predecessor's completed record byte-exact. The
                    # signed controller archives it when starting a FRESH operation.
                    token=secrets.token_hex(16);view.prepare(COMPONENT,token,'gpu')
                else:raise ValueError('Unrecognized predecessor operation preserved')
"""
   # `text` rebinds the forward package pin, so the predecessor anchor remains explicit.
   forward=old.replace(b'a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da',sha(activation).encode())
   value=replace_exact(value,forward,new)
  if name=='tools/aggregate-legacy-runtime.py':
   value=replace_exact(value,b'len(raw) != 3246080',('len(raw) != '+str(len(activation))).encode())
  if name=='tools/aggregate-portable-capture.py':
   bridge=b"""    def predecessor_restored(self,op):
        predecessors={'a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da':'0747a1d63f455cfcf003f326d7f9ed3c5e6de63dfee5ae4498799384d033bc92',
          'baf49445cc9e331d6ee9a3ba71eb48ad3e9723301ff60874f99723e3f73d0526':'cffefc08f288c94cb612c70df9220991002f335dfe4c8d0b09dbb5e824048034',
          '14703568a3cf09351bbfa8e1f435b5cec0cb936ea006eb6e8316a5c9d2ec141b':'91c390865c09f09b5c830286d996e1f7e8640f2e24f020bbe1b8207c9d911718'}
        if op['packageSha256'] not in predecessors or op['phase']!='restored':return False
        raw=self.b.encode(op);cached=getattr(self,'_predecessor_terminal',None)
        if cached is not None:
            if cached[0]!=raw:raise ValueError('Completed predecessor generation changed')
            cached[1]();self.check();return True
        if self.lease:raise ValueError('Predecessor must be authenticated before taking the updater lease')
        # Authenticate ONCE before release(). Retain independent descriptors and
        # lock-free inventory checks, never reacquire hold_view under updater lock.
        with self.b.hold_controls(op['packageSha256']) as previous:
            if previous['statement']['componentSha256']!=predecessors[op['packageSha256']]:raise ValueError('Exact signed predecessor component required')
            view=previous['module']('capture-private-view.py')
            with view.hold_view(op['viewToken']) as original:
                if original['report']!=op['view'] or original['report']['mode']!='gpu' or original['report']['componentSha256']!=previous['statement']['componentSha256']:raise ValueError('Original completed predecessor view required')
                verify=self.retain_predecessor_view(view,original,op)
                verify();original['check']();previous['check']()
        self._predecessor_terminal=(raw,verify);verify();self.check();return True

    def retain_predecessor_view(self,view,original,op):
        # Only the already authenticated view contributes paths and payloads.
        # Retain exact metadata plus complete runtime/config inventory generations.
        tree=self.b.Tree(self.stack);dirs=view.S.Dirs(self.stack)
        root=view.ROOT/view.token_name(op['viewToken']);slot=Path(original['runtimeRoot']).parent
        intent_raw=tree.file(root/'intent.json',8192);intent=view.P.document(intent_raw)
        tree.file(root/'complete.json',65536)
        bundle=tree.file(slot/'bundle.tar',view.S.A.C.MAX_ARCHIVE)
        for name,limit in [('intent.json',8192),('statement.json',8192),('signature.bin',64),('complete.json',1048576)]:tree.file(slot/name,limit)
        payloads=view.S.payloads(bundle);objects=view.S.tree(self.stack,dirs,slot,payloads)
        config,generation=view.configuration(dirs);mapping=tree.file(root/'mounts.conf',4096)
        initial=view.policy(intent['mode']);config_objects=view.inventory(dirs,root,intent,mapping,config,initial)
        def check():
            tree.check();dirs.check()
            if set(os.listdir(dirs.open(root)))!=view.FILES or set(os.listdir(dirs.open(slot)))!=view.S.META:raise ValueError('Predecessor view/component namespace changed')
            if view.configuration(dirs)!=(config,generation):raise ValueError('Predecessor configuration generation changed')
            if view.S.tree(self.stack,dirs,slot,payloads)!=objects or view.inventory(dirs,root,intent,mapping,config,initial)!=config_objects:raise ValueError('Authenticated predecessor inventory changed')
            tree.check();dirs.check()
        check();return check

"""
   value=replace_exact(value,b'    def status(self):\n',bridge+b'    def status(self):\n')
   value=replace_exact(value,b"if op['packageSha256']!=PACKAGE:raise ValueError('Foreign original capture package preserved')",b"if op['packageSha256']!=PACKAGE and not self.predecessor_restored(op):raise ValueError('Foreign original capture package preserved')")
  if name in ('tools/aggregate-installer-entry.py','tools/aggregate-runtime.py'):
   value=replace_exact(value,b"'legacy-video':0}",b"'legacy-video':0,'upgrade-existing':0,'upgrade-recover':0}")
  if name=='tools/aggregate-runtime.py':
   anchor=b"    m=load_material(ctx);ctx=m.retain(ctx);material=m.materialize(ctx)\n"
   value=replace_exact(value,anchor,b"    if args in (['upgrade-existing'],['upgrade-recover']):return successor_handoff(args,ctx,engine)\n"+anchor)
   helper=(ROOT/'tools/rc18-predecessor-handoff.py').read_bytes()
   helper=helper.replace(b'@@NEW_CAPTURE@@',sha(activation).encode()).replace(b'@@OLD_ENTRY_PIN@@',sha(sources['tools/aggregate-installer-entry.py']).encode())
   ast.parse(helper,filename='authenticated-successor-handoff')
   value+=b'\n\n'+helper
  value=value.replace(b'COMPONENTS=(33320960,',('COMPONENTS=('+str(len(new_components))+',').encode())
  if new_name.endswith('.py'):ast.parse(value,filename=new_name)
  output[new_name]=value
 policy=compile_policy(output['tools/aggregate-release.py'],'successor_release')
 frontend={n[len(prefix+'frontend/'):]:v for n,v in original.items() if n.startswith(prefix+'frontend/')}
 package=policy.build(new_components,frontend,output,revision);report=policy.review(package)
 entry=compile_policy(output['tools/aggregate-installer-entry.py'],'successor_entry')
 statement=policy.statement(package,KEY)
 # Structural agreement only: this aggregate is still unsigned. Nested capture
 # signatures were verified above; the aggregate entry is never activated here.
 if entry.COMPONENTS!=policy.COMPONENTS or entry.PREFIX!=policy.PREFIX or entry.CORE!=policy.CORE or entry.FRONTEND_PIN!=policy.FRONTEND_PIN or entry.CONTRACT_PIN!=sha(policy.encode(policy.CONTRACT)):
  raise ValueError('Successor loader and build policy must have identical fixed bindings')
 files=entry.unpack(package)
 if sha(files[entry.MANIFEST])!=report['manifestSha256']:raise ValueError('Successor entry and pure policy disagree')
 return package,statement,{'compilerSha256':sha(Path(__file__).read_bytes()),'schema':1,'kind':'droidmate-rc18-preparation','predecessorSha256':PREDECESSOR,'packageSha256':sha(package),'sourceRevision':revision,'capturePackageSha256':sha(activation),'componentSha256':sha(component),'files':len(files),'signed':False,'installable':False,'servicesChanged':False,'predecessorHandoffImplemented':True,'predecessorHandoffQualified':False,'predecessorHandoffLayouts':['portable']}

def main(args):
 if len(args)!=9:raise ValueError('PREDECESSOR COMPONENT ACTIVATION ACT_STAT ACT_SIG COMP_STAT COMP_SIG COMMIT NEW_OUTPUT')
 raw=[read(p,50*1024**2 if i<3 else 8192) for i,p in enumerate(args[:7])]
 package,statement,receipt=prepare(*raw,args[7]);output=Path(args[8]);output.mkdir(mode=0o700)
 for name,value in [('release.tar',package),('statement.json',statement),('preparation.json',encode(receipt))]:
  with (output/name).open('xb') as f:f.write(value)
 print(encode(receipt).decode().strip())

if __name__=='__main__':main(sys.argv[1:])
