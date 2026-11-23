# SPDX-License-Identifier: GPL-3.0-only
"""New ephemeral retained-FD tests; never replay the protected QY fixture."""
from pathlib import Path
import importlib.util,os,tempfile,unittest
from unittest import mock
spec=importlib.util.spec_from_file_location('entry',Path(__file__).with_name('aggregate-installer-entry.py'))
E=importlib.util.module_from_spec(spec);spec.loader.exec_module(E)

class FailureLocationTests(unittest.TestCase):
 def test_locations_never_include_exception_text_locals_or_private_paths(self):
  secret='test-private-value-never-log'
  scope={'secret':secret}
  try:exec(compile('def diagnostic():\n raise ValueError(secret)\ndiagnostic()','/private/aggregate-runtime.py','exec'),scope)
  except ValueError as error:
   rows=E.failure_locations(error)
  self.assertTrue(any('aggregate-runtime.py:diagnostic:' in row for row in rows))
  self.assertNotIn(secret,str(rows));self.assertNotIn('/private',str(rows))

@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:1)()==0,'Root Linux protected ancestry')
class RetainedEntryTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='droidmate-retained-entry-',dir='/var/lib')
  self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'slot'
  raw=Path(__file__).with_name('update-installer-entry.py').read_bytes().replace(b'\r\n',b'\n')
  self.original=E.stager(raw,Path(__file__).with_name('update-installer-entry.py'))
  self.files={'source/large.py':b'x'*(2*1024**2),'part':b'original public data\n'}
  self.original.materialize(self.root,self.files)
  self.held=E.RetainedInputs(self.original);self.addCleanup(self.held.close)
  for n,b in self.files.items():self.assertEqual(self.held.read(self.root/n,len(b)),b)
  for n in ('intent.json','complete.json'):self.held.read(self.root/n,1024**2)
  self.held.seal(self.root,{*self.files,'intent.json','complete.json'})
 def test_repeated_checks_read_no_package_body(self):
  with mock.patch.object(self.original,'read',side_effect=AssertionError('Body reread')),mock.patch.object(E.os,'pread',side_effect=AssertionError('Body reread')):
   for _ in range(100):self.assertTrue(self.held.check())
 def test_inplace_change_with_restored_mtime_is_rejected(self):
  p=self.root/'part';s=p.stat();p.write_bytes(b'changed public data!\n');os.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))
  with self.assertRaises(ValueError):self.held.check()
 def test_same_bytes_replacement_is_rejected(self):
  p=self.root/'part';raw=p.read_bytes();p.rename(self.root/'retained-part');p.write_bytes(raw);p.chmod(0o600)
  with self.assertRaises(ValueError):self.held.check()
 def test_foreign_namespace_member_is_rejected(self):
  (self.root/'foreign').write_bytes(b'foreign')
  with self.assertRaises(ValueError):self.held.check()
 def test_root_loss_never_recreates_it(self):
  old=self.root.with_name('retained-slot');self.root.rename(old)
  with self.assertRaises((ValueError,OSError)):self.held.check()
  self.assertFalse(self.root.exists())
 def test_closed_guard_and_link_replacement_are_rejected(self):
  p=self.root/'part';p.rename(self.root/'retained-part');p.symlink_to(self.root/'retained-part')
  with self.assertRaises(ValueError):self.held.check()
  self.held.close()
  with self.assertRaises(ValueError):self.held.check()

if __name__=='__main__':unittest.main()
