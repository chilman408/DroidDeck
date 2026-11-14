# SPDX-License-Identifier: GPL-3.0-only
"""Closed aggregate material decoding: no installation or payload execution."""
import importlib.util
from pathlib import Path
import types,unittest
from unittest import mock
spec=importlib.util.spec_from_file_location('aggregate_material',Path(__file__).with_name('aggregate-material.py'))
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def context():return {'root':Path('/usr/local/share/droidmate-rc17-'+'a'*64),'release':{
 'kind':'droidmate-aggregate-release-authorization','aggregateRelease':'RC17','coreRuntimeVersion':'0.1.1-rc.20',
 'compatibleReleaseSha256':M.CORE,'activationAllowed':True,'packageSha256':'a'*64},'check':lambda:True}

class MaterialTests(unittest.TestCase):
 def test_context_bound_before_callback_and_closed_modules(self):
  c=context();r=M.retain(c);self.assertTrue(r['check']())
  c['release']['packageSha256']='b'*64
  with self.assertRaises(ValueError):r['check']()
  with self.assertRaises(ValueError):M.source_module(context(),'../../foreign.py')
  c=context()
  def mutate():c['root']=Path('/foreign');return True
  c['check']=mutate
  with self.assertRaises(ValueError):M.retain(c)
 def test_module_read_requires_original_source_boundary_after_read_before_execution(self):
  c=context();ok=[True];c['check']=lambda:ok[0]
  def raw(path):ok[0]=False;return b"raise RuntimeError('executed')\n"
  with mock.patch.object(Path,'read_bytes',raw):
   with self.assertRaises(ValueError):M.source_module(c,'tools/aggregate-browser-view.py')
 def test_closed_component_members_and_exact_roles(self):
  import io,tarfile
  assets={'foundation':{'name':'base.tar','size':4,'sha256':M.sha(b'base')}}
  def archive(files):
   out=io.BytesIO()
   with tarfile.open(fileobj=out,mode='w:',format=tarfile.USTAR_FORMAT) as t:
    for n,b in files.items():
     m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;t.addfile(m,io.BytesIO(b))
   return out.getvalue()
  prefix='droidmate-rc17-components/assets/'
  raw=archive({prefix+'base.tar':b'base'})
  recipe=types.SimpleNamespace(ASSETS=assets,inspect=lambda raw:{'assets':1})
  with mock.patch.object(M,'COMPONENTS',(len(raw),M.sha(raw))):
   self.assertEqual(M.component_assets(raw,recipe),{'foundation':b'base'})
   with self.assertRaises(ValueError):M.component_assets(raw+b'changed',recipe)
   duplicate=archive({prefix+'base.tar':b'evil'})
   with mock.patch.object(M,'COMPONENTS',(len(duplicate),M.sha(duplicate))):
    with self.assertRaises(ValueError):M.component_assets(duplicate,recipe)
 def test_browser_tree_paths_match_qualified_gateway_mapping(self):
  runtime={'portable/worker-entry.js':b'entry','preview-gateway/node_modules/ws/x.js':b'lib'}
  compiler=types.SimpleNamespace(runtime_tree=mock.Mock(return_value=runtime),mapping=mock.Mock(return_value=b'map'))
  material=types.SimpleNamespace(ctx=context(),assets={'capturePackage':b'package'},frontend={'index.html':b'index','bundle-manifest.json':b'meta'},compiler=compiler,adapter=b'adapter',frontend_sha='b'*64)
  with mock.patch.object(M,'materialize',return_value=material):
   v=M.browser_material(context(),'portable')
  self.assertEqual(v['runtimeFiles'],{'portable/runtime/'+n:b for n,b in runtime.items()})
  self.assertEqual(v['frontendFiles'],{'frontend/index.html':b'index','frontend/bundle-manifest.json':b'meta'})
  self.assertEqual(v['runtimeDigest'],'a'*64);self.assertEqual(v['frontendDigest'],'b'*64)
  self.assertEqual(v['mapping'],b'map')
  compiler.mapping.assert_called_once_with('portable','a'*64,'b'*64)
  compiler.runtime_tree.assert_called_once_with(b'package',b'adapter','portable')
  with self.assertRaises(ValueError):M.browser_material(context(),'unknown')

if __name__=='__main__':unittest.main()
