# SPDX-License-Identifier: GPL-3.0-only
"""Read authenticated aggregate inputs and compile complete public browser views.

No configuration copies, installed-code imports, archive extraction or service
operations. The enclosing signed entry retains every original source generation.
"""
import hashlib,io,json,re,tarfile,types
from pathlib import Path,PurePosixPath

CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
COMPONENTS=(33423360,'597de20e6c3657aca54d5332ffad02b5c45bbd30499c558a52d3de94f5d4a7dd')
PINS={
 'runtime/portable/capture-component-staging.statement.json':'569086c9e15d97a6e7857e192e18b7ba53c3cfd8dcde7fb96f1c5f19b88cffef',
 'runtime/portable/capture-component-staging.sig':'1961c82d4aec095d7354071bfb838b521c2e69655176483038441fab57f40c19',
 'tools/package-rc18-components.py':'e838e0e85f6b03089e5f480c96641de217bde107d868eb1c351479ab393b17fc',
 'tools/aggregate-browser-view.py':'fb681b3248359da685fb657510fbcaa9a874367b0d13ff184d92ff512f723460',
 'tools/update-installer-entry.py':'48ae63a3deb49022ec7065c0a17ea836712eef97080952ba22aed96948bcd7c5',
 'runtime/portable/aggregate-gateway.js':'037856026e0c90b1d80452970c688768eaaadf2908124c3fc9a4fce37e842223',
}
MODULES={'runtime/portable/native-gpu-readmission.py','tools/aggregate-release.py','tools/aggregate-portable-capture.py','tools/aggregate-fresh-install.py',
 'tools/aggregate-legacy-video.py','tools/aggregate-legacy-runtime.py','tools/aggregate-legacy-backend.py','tools/aggregate-runtime.py','tools/aggregate-supervisor.py','tools/aggregate-enroll.py',*PINS}

def sha(b):return hashlib.sha256(b).hexdigest()

def retain(ctx):
 if type(ctx) is not dict or set(ctx)!={'root','release','check'} or not callable(ctx['check']):raise ValueError('Retained aggregate context required')
 root,callback=ctx['root'],ctx['check']
 def encode(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
 raw=encode(ctx['release']);release=json.loads(raw)
 if (type(release) is not dict or release.get('kind')!='droidmate-aggregate-release-authorization'
     or release.get('aggregateRelease')!='RC18' or release.get('coreRuntimeVersion')!='0.1.1-rc.20'
     or release.get('compatibleReleaseSha256')!=CORE or release.get('activationAllowed') is not True
     or type(release.get('packageSha256')) is not str or not re.fullmatch('[a-f0-9]{64}',release['packageSha256'])
     or root!=Path('/usr/local/share/droidmate-rc18-'+release['packageSha256'])):raise ValueError('Original aggregate domain/root required')
 def check():
  def same():
   if set(ctx)!={'root','release','check'} or ctx['root']!=root or ctx['check'] is not callback or encode(ctx['release'])!=raw:raise ValueError('Original aggregate binding changed')
  same()
  if callback() is not True:raise ValueError('Original aggregate admission lost')
  same();return True
 check();return {'root':root,'release':release,'check':check}

def source_bytes(ctx,name):
 if name not in MODULES:raise ValueError('Closed aggregate source name required')
 if ctx['check']() is not True:raise ValueError('Retained original source required')
 raw=(ctx['root']/'source'/name).read_bytes()
 if type(raw) is not bytes or not 0<len(raw)<=1024**2 or name in PINS and sha(raw)!=PINS[name]:raise ValueError('Original bounded integration source required')
 if ctx['check']() is not True:raise ValueError('Source changed before compilation')
 return raw

def source_module(ctx,name):
 ctx=retain(ctx)
 if not name.endswith('.py'):raise ValueError('Python integration module required')
 raw=source_bytes(ctx,name);path=ctx['root']/'source'/name
 m=types.ModuleType('authenticated_'+name.replace('/','_').replace('-','_').replace('.','_'));m.__file__=str(path)
 exec(compile(raw,str(path),'exec'),m.__dict__)
 ctx['check']();return m

def component_assets(raw,recipe):
 if type(raw) is not bytes or (len(raw),sha(raw))!=COMPONENTS:raise ValueError('Original complete QU components required')
 report=recipe.inspect(raw)
 if report.get('assets')!=len(recipe.ASSETS):raise ValueError('Closed original asset count required')
 assets={};prefix='droidmate-rc18-components/assets/'
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
  for role,row in recipe.ASSETS.items():
   name=prefix+row['name'];m=t.getmember(name)
   if m.type!=tarfile.REGTYPE or m.size!=row['size']:raise ValueError('Original regular component required')
   b=t.extractfile(m).read(row['size']+1)
   if len(b)!=row['size'] or sha(b)!=row['sha256']:raise ValueError('Original component bytes required')
   assets[role]=b
 return assets

def materialize(ctx):
 ctx=retain(ctx);root=ctx['root']
 recipe=source_module(ctx,'tools/package-rc18-components.py')
 raw=(root/'components.tar').read_bytes();ctx['check']();assets=component_assets(raw,recipe);ctx['check']()
 policy=source_module(ctx,'tools/aggregate-release.py')
 front_raw=(root/'frontend/bundle-manifest.json').read_bytes();ctx['check']()
 if sha(front_raw)!=policy.FRONTEND_PIN:raise ValueError('Original frontend manifest required')
 meta=policy.document(front_raw,canonical=False);files={}
 if type(meta.get('files')) is not list or len(meta['files'])!=policy.FRONTEND_COUNT:raise ValueError('Original closed frontend inventory required')
 for row in meta['files']:
  name=policy.name(row['path'])
  if name in files:raise ValueError('Unique public frontend path required')
  files[name]=(root/'frontend'/name).read_bytes();ctx['check']()
 files['bundle-manifest.json']=front_raw;policy.frontend_files(files);ctx['check']()
 return types.SimpleNamespace(ctx=ctx,assets=assets,frontend=files,frontend_sha=policy.FRONTEND_PIN,
  compiler=source_module(ctx,'tools/aggregate-browser-view.py'),adapter=source_bytes(ctx,'runtime/portable/aggregate-gateway.js'))

def browser_material(ctx,mode):
 if mode not in ('portable','legacy'):raise ValueError('Explicit recognized layout required')
 material=materialize(ctx);material.ctx['check']()
 runtime=material.compiler.runtime_tree(material.assets['capturePackage'],material.adapter,mode)
 package=material.ctx['release']['packageSha256'];front=material.frontend_sha
 result={'runtimeFiles':{mode+'/runtime/'+n:b for n,b in runtime.items()},
  'frontendFiles':{'frontend/'+n:b for n,b in material.frontend.items()},'runtimeDigest':package,'frontendDigest':front,
  'mapping':material.compiler.mapping(mode,package,front)}
 material.ctx['check']();return result
