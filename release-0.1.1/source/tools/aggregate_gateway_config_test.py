# SPDX-License-Identifier: GPL-3.0-only
import hashlib,importlib.util,json,subprocess,shutil,types,unittest,os
from pathlib import Path
s=importlib.util.spec_from_file_location('r',Path(__file__).with_name('aggregate-runtime.py'))
R=importlib.util.module_from_spec(s);s.loader.exec_module(R)

class GatewayConfigTests(unittest.TestCase):
 def test_portable_mapping_explicitly_binds_optin_and_canonical_generation(self):
  raw=R.portable_gateway_mapping(b'[Service]\nEnvironment=DROIDMATE_BROWSER_FRONTEND=/public/frontend\n','a'*64)
  self.assertIn(b'Environment=DROIDDECK_PORTABLE_RUNTIME=1\n',raw)
  self.assertIn(b'Environment=DROIDDECK_CONFIGURATION_SHA256='+b'a'*64+b'\n',raw)
  for digest in [None,'','a'*63,'a'*64+'\n']:
   with self.assertRaises(ValueError):R.portable_gateway_mapping(b'[Service]\n',digest)
 def test_digest_runs_original_normalizer_and_ignores_input_key_order(self):
  node=shutil.which('node')
  if not node:self.skipTest('Node required')
  files={n:(Path(__file__).resolve().parents[1]/'runtime'/n).read_bytes().replace(b'\r\n',b'\n') for n in R.CONFIG_DIGEST_PINS}
  material=types.SimpleNamespace(compiler=types.SimpleNamespace(capture_runtime=lambda raw:files),assets={'capturePackage':b'fixture'})
  real_run=subprocess.run
  def run(args,**kwargs):
   if os.name=='nt':kwargs['env']={**{k:os.environ[k] for k in ('SystemRoot','SystemDrive','COMSPEC') if k in os.environ},**kwargs['env']}
   return real_run([node,*args[1:]],**kwargs)
  expected={'version':1,'user':'deck','uid':1200,'gid':1201,'lanAddress':'10.20.30.40','home':'/home/deck','port':8443,'hostname':'droiddeck.invalid'}
  config={k:expected[k] for k in reversed(list(expected)) if k not in ('version','home','port')}
  digest=R.portable_configuration_digest(material,config,_run=run)
  self.assertEqual(digest,hashlib.sha256(json.dumps(expected,separators=(',',':')).encode()).hexdigest())
  with self.assertRaises(ValueError):R.portable_configuration_digest(material,{**config,'uid':0},_run=run)
  files['portable/runtime-config.js']+=b'\n'
  with self.assertRaises(ValueError):R.portable_configuration_digest(material,config,_run=run)

if __name__=='__main__':unittest.main()
