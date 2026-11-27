# SPDX-License-Identifier: GPL-3.0-only
import importlib.util,types,unittest
from pathlib import Path
from unittest import mock
s=importlib.util.spec_from_file_location('aggregate_enroll',Path(__file__).with_name('aggregate-enroll.py'))
E=importlib.util.module_from_spec(s);s.loader.exec_module(E)

class Model:
 def __init__(self,ctx,material,compiler):self.events=[];self.existing=False;self.fail=None
 def context(self):return True
 def close(self):self.events.append("released")
 def inspect(self):self.events.append('inspect');return {'complete':self.existing}
 def verify(self):self.events.append('verify');return True
 def claim(self):
  outer=self
  class Claim:
   def __enter__(self):outer.events.append('claim');return outer
   def __exit__(self,*args):outer.events.append('close')
  return Claim()
 def record(self,name,value):self.events.append(name)
 def __getattr__(self,name):
  if name not in ('loader','component','view','preference'):raise AttributeError(name)
  def call():
   self.events.append(name)
   if self.fail==name:raise ValueError('Original step refused')
   return True
  return call

class EnrollmentTests(unittest.TestCase):
 def setUp(self):
  self.model=Model(None,None,None);self.p=mock.patch.object(E,'Native',return_value=self.model);self.p.start()
 def tearDown(self):self.p.stop()
 def test_complete_existing_setup_is_verified_without_enrollment_replay(self):
  self.model.existing=True;r=E.enroll({},None,None)
  self.assertTrue(r['captureEnrolled']);self.assertTrue(r['bootPreferenceEnrolled']);self.assertFalse(r['servicesStarted'])
  self.assertEqual(self.model.events,['inspect','verify','released'])
 def test_fresh_steps_record_each_completion_and_verify_before_final_acceptance(self):
  r=E.enroll({},None,None)
  self.assertEqual(self.model.events,['inspect','claim','intent.json','loader','loader.json','component','component.json','view','view.json','preference','preference.json','verify','complete.json','close','released'])
  self.assertTrue(r['captureEnrolled']);self.assertFalse(r['releaseComplete']);self.assertFalse(r['hostProfileInstalled'])
 def test_refused_step_preserves_claim_and_never_runs_later_steps(self):
  for fail in ('loader','component','view','preference'):
   self.model.events=[];self.model.fail=fail
   with self.assertRaises(ValueError):E.enroll({},None,None)
   self.assertNotIn('complete.json',self.model.events);self.assertEqual(self.model.events[-2:],['close','released'])
   for later in ('loader','component','view','preference')[('loader','component','view','preference').index(fail)+1:]:self.assertNotIn(later,self.model.events)
 def test_only_original_exact_public_component_receipts_are_accepted(self):
  with self.assertRaises(ValueError):E.public_receipts(b'changed',b'x'*64)

if __name__=='__main__':unittest.main()
