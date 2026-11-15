# SPDX-License-Identifier: GPL-3.0-only
import importlib.util,io,os,tarfile,unittest,types,copy
from contextlib import contextmanager
from pathlib import Path
spec=importlib.util.spec_from_file_location('successor_preparation',Path(__file__).with_name('rc18-runtime-preparation.py'));P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)
class SuccessorPreparation(unittest.TestCase):
 def test_closed_archive_refuses_traversal_links_and_duplicate_paths(self):
  for name,kind,duplicate in [('../escape',tarfile.REGTYPE,False),('safe',tarfile.SYMTYPE,False),('safe',tarfile.REGTYPE,True)]:
   stream=io.BytesIO()
   with tarfile.open(fileobj=stream,mode='w') as t:
    for _ in range(2 if duplicate else 1):
     info=tarfile.TarInfo(name);info.type=kind;info.size=1;t.addfile(info,io.BytesIO(b'x'))
   with self.assertRaises(ValueError):P.members(stream.getvalue())
 @unittest.skipUnless(os.environ.get('RC18_PREPARATION_INPUTS'),'Needs exact signed public capture fixture directory')
 def test_reproducible_successor_retains_frozen_helpers_and_both_complete_runtime_views(self):
  root=Path(os.environ['RC18_PREPARATION_INPUTS']);predecessor=P.read(root.parent/'droidmate-rc17-dist-sn/droidmate-rc17-source-and-components.tar',50*1024**2)
  inputs=[predecessor,*[P.read(root/n,50*1024**2) for n in ['capture-runtime.tar','capture-activation.tar','activation-statement.json','activation-signature.bin','component-statement.json','component-signature.bin']]]
  revision='a869bd254d8bb962b9eb55c6c494686eb69e0eeb';raw,statement,receipt=P.prepare(*inputs,revision);again=P.prepare(*inputs,revision)
  self.assertEqual((raw,statement,receipt),again);self.assertFalse(receipt['installable'] or receipt['signed'] or receipt['servicesChanged'] or receipt['predecessorHandoffQualified'])
  self.assertTrue(receipt['predecessorHandoffImplemented']);self.assertEqual(receipt['predecessorHandoffLayouts'],['portable'])
  files=P.members(raw);old=P.members(predecessor);prefix='droidmate-rc18-release/source/'
  for name in ['tools/update-installer-entry.py','tools/enroll-capture-control.py','runtime/portable/capture-control-bootstrap.py','runtime/portable/aggregate-gateway.js','tools/legacy-video-namespace.py','tools/legacy-gpu-preflight.py']:
   self.assertEqual(files[prefix+name],old['droidmate-rc17-release/source/'+name])
  capture=P.compile_policy(files[prefix+'tools/aggregate-portable-capture.py'],'fixture_capture')
  item={'packageSha256':'a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da','phase':'restored','viewToken':'a'*32,'view':{'mode':'gpu','componentSha256':'0747a1d63f455cfcf003f326d7f9ed3c5e6de63dfee5ae4498799384d033bc92'}}
  observed=[]
  @contextmanager
  def held_view(token):
   self.assertEqual(token,item['viewToken']);yield {'report':item['view'],'check':lambda:True}
  @contextmanager
  def held_controls(digest):
   observed.append(digest);yield {'statement':{'componentSha256':item['view']['componentSha256']},'module':lambda name:types.SimpleNamespace(hold_view=held_view),'check':lambda:True}
  c=object.__new__(capture.Capture);c.check=lambda:True;c.lease=False;c.b=types.SimpleNamespace(hold_controls=held_controls,encode=P.encode);c.retain_predecessor_view=lambda *args:lambda:True
  self.assertTrue(c.predecessor_restored(item));self.assertEqual(observed,[item['packageSha256']]);c.lease=True;self.assertTrue(c.predecessor_restored(item));self.assertEqual(len(observed),1,'must not reacquire view/updater locks while release lease is held');c.lease=False
  for key,value in [('phase','accepted'),('phase','restore-requested'),('packageSha256','f'*64)]:
   self.assertFalse(c.predecessor_restored({**item,key:value}));self.assertEqual(len(observed),1)
  before=copy.deepcopy(item);del c._predecessor_terminal;item['view']['componentSha256']='f'*64
  with self.assertRaises(ValueError):c.predecessor_restored(item)
  item=before
  item['packageSha256']='baf49445cc9e331d6ee9a3ba71eb48ad3e9723301ff60874f99723e3f73d0526';item['view']['componentSha256']='cffefc08f288c94cb612c70df9220991002f335dfe4c8d0b09dbb5e824048034'
  self.assertTrue(c.predecessor_restored(item));c.lease=True;self.assertTrue(c.predecessor_restored(item));c.lease=False
  view=P.compile_policy(files[prefix+'tools/aggregate-browser-view.py'],'fixture_view')
  for mode in ['portable','legacy']:
   runtime=view.runtime_tree(inputs[2],files[prefix+'runtime/portable/aggregate-gateway.js'],mode)
   self.assertIn('preview-gateway/browser-settings-overlay.js',runtime);self.assertIn('preview-gateway/video-encoding.js',runtime);self.assertEqual(len(runtime),246)
  for index in [0,2,4,6]:
   changed=list(inputs);changed[index]=changed[index][:-1]+bytes([changed[index][-1]^1])
   with self.assertRaises((ValueError,KeyError)):P.prepare(*changed,revision)

 @unittest.skipUnless(os.environ.get('RC18_PREPARATION_INPUTS'),'Needs exact signed public capture fixture directory')
 def test_public_signing_review_reconstructs_every_byte_and_rejects_unsigned_code_changes(self):
  spec=importlib.util.spec_from_file_location('successor_review',Path(__file__).with_name('rc18-sign-inputs.py'));R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
  root=Path(os.environ['RC18_PREPARATION_INPUTS']);old=P.read(root.parent/'droidmate-rc17-dist-sn/droidmate-rc17-source-and-components.tar',50*1024**2)
  inputs=[old,*[P.read(root/n,50*1024**2) for n in ['capture-runtime.tar','capture-activation.tar','activation-statement.json','activation-signature.bin','component-statement.json','component-signature.bin']]]
  raw,statement,_=P.prepare(*inputs,'a869bd254d8bb962b9eb55c6c494686eb69e0eeb')
  report=R.review(raw,statement,old,P.KEY);self.assertEqual(report['packageSha256'],P.sha(raw));self.assertFalse(report['servicesChanged'])
  for args in [(raw,statement,old,'0'*64),(raw,statement+b' ',old,P.KEY),(raw,statement,old[:-1],P.KEY)]:
   with self.assertRaises((ValueError,KeyError)):R.review(*args)
  changed=raw.replace(b'Only the original controller',b'Only the altered controller',1)
  self.assertNotEqual(changed,raw)
  with self.assertRaises((ValueError,KeyError)):R.review(changed,statement,old,P.KEY)
if __name__=='__main__':unittest.main()
