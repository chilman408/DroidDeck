# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import hashlib,importlib.util,json,tempfile,unittest
from unittest.mock import patch
s=importlib.util.spec_from_file_location('legacy_apply',Path(__file__).with_name('legacy-settings-apply.py'));D=importlib.util.module_from_spec(s);s.loader.exec_module(D)
class Exchange(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name);self.root=self.base/'root';self.root.mkdir();self.payload=self.base/'payload';self.payload.mkdir();self.journal=self.base/'journal';self.old={};self.new={}
  for n in ['server.js','audio-relay.js','h264-relay.js','video-settings.js','adaptive-video.js','browser-adaptive-policy.js','browser-audio-policy.js','browser-audio-playback.js','browser-audio-worker.js']:
   b=('new '+n).encode();(self.payload/n).write_bytes(b);self.new[n]=D.digest(b)
   if n in ['server.js','audio-relay.js','h264-relay.js','video-settings.js']:
    b=('old '+n).encode();(self.root/n).write_bytes(b);self.old[n]=D.digest(b)
  (self.payload/'manifest.json').write_text(json.dumps({'originalSha256':self.old,'afterSha256':self.new}))
 def test_exchange_rollback_and_replay_refusal(self):
  r=D.prepare(self.root,self.payload,self.journal);D.exchange(self.root,self.payload,self.journal,r)
  self.assertTrue(all(D.digest((self.root/n).read_bytes())==h for n,h in self.new.items()));D.restore(self.root,self.journal,r)
  for n,h in self.old.items():self.assertEqual(D.digest((self.root/n).read_bytes()),h)
  self.assertFalse((self.root/'adaptive-video.js').exists())
  with self.assertRaises(FileExistsError):D.prepare(self.root,self.payload,self.journal)
 def test_original_or_target_generation_change_refused(self):
  r=D.prepare(self.root,self.payload,self.journal);(self.root/'server.js').write_text('user edit')
  with self.assertRaises(ValueError):D.exchange(self.root,self.payload,self.journal,r)
  self.assertEqual((self.root/'server.js').read_text(),'user edit')
 def test_partial_exchange_failure_restores_original_code(self):
  r=D.prepare(self.root,self.payload,self.journal);original=D.replace_code;count=0
  def failing(path,raw,mode):
   nonlocal count;count+=1
   if count==3:raise OSError('generated interrupted write')
   return original(path,raw,mode)
  with patch.object(D,'replace_code',failing):
   with self.assertRaises(OSError):D.exchange(self.root,self.payload,self.journal,r)
  for n,h in self.old.items():self.assertEqual(D.digest((self.root/n).read_bytes()),h)
 def test_unrelated_post_activation_change_blocks_rollback(self):
  r=D.prepare(self.root,self.payload,self.journal);D.exchange(self.root,self.payload,self.journal,r);(self.root/'server.js').write_text('user edit')
  with self.assertRaises(ValueError):D.restore(self.root,self.journal,r)
  self.assertEqual((self.root/'server.js').read_text(),'user edit')
if __name__=='__main__':unittest.main()
