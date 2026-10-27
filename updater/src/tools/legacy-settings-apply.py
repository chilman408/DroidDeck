# SPDX-License-Identifier: GPL-3.0-only
"""Explicit code-only legacy preview exchange. Never migrates installer authority."""
from pathlib import Path
import hashlib,json,os,stat,subprocess,sys,time,ssl,urllib.request
ROOT=Path('/opt/teslaandroid-waydroid/runtime/preview-gateway')
JOURNAL=Path('/root/droiddeck-settings-20261026ja')
PAYLOAD=Path('/home/hilman/droiddeck-settings-20261026ja-target')
SERVICE='tesla-waydroid-runtime.service'
def digest(raw):return hashlib.sha256(raw).hexdigest()
def regular(path):
 s=path.lstat()
 if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>1024*1024:raise ValueError('Bounded single-link regular code required')
 return s

def rows(root,payload):
 manifest=json.loads((payload/'manifest.json').read_text());before=manifest['originalSha256'];after=manifest['afterSha256']
 if set(after)!={'server.js','audio-relay.js','h264-relay.js','video-settings.js','adaptive-video.js','browser-adaptive-policy.js','browser-audio-policy.js','browser-audio-playback.js','browser-audio-worker.js'}:raise ValueError('Closed legacy target file set required')
 result={}
 for name,pin in after.items():
  regular(payload/name);raw=(payload/name).read_bytes()
  if digest(raw)!=pin:raise ValueError('Exact target source required')
  f=root/name
  if name in before:
   s=regular(f)
   if digest(f.read_bytes())!=before[name]:raise ValueError('Audited original legacy source required')
   result[name]={'before':before[name],'after':pin,'mode':stat.S_IMODE(s.st_mode),'identity':[s.st_dev,s.st_ino,s.st_mtime_ns,s.st_size]}
  else:
   if os.path.lexists(f):raise ValueError('New policy path must be absent')
   result[name]={'before':None,'after':pin,'mode':0o644,'identity':None}
 return result

def write_new(path,raw,mode=0o600):
 fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,mode)
 with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())

def replace_code(path,raw,mode):
 temporary=path.with_name(path.name+'.droiddeck-settings-new')
 write_new(temporary,raw,mode);os.replace(temporary,path)

def prepare(root,payload,journal):
 if not root.is_dir() or root.is_symlink() or not payload.is_dir() or payload.is_symlink():raise ValueError('Real fixed code directories required')
 result=rows(root,payload);journal.mkdir(mode=0o700)
 for name,row in result.items():
  if row['before'] is not None:write_new(journal/(name+'.before'),(root/name).read_bytes(),row['mode'])
  raw=(payload/name).read_bytes()
  if digest(raw)!=row['after']:raise ValueError('Target changed before protected snapshot')
  write_new(journal/(name+'.after'),raw,row['mode'])
 write_new(journal/'intent.json',(json.dumps(result,sort_keys=True)+'\n').encode());return result

def exchange(root,payload,journal,result):
 if rows(root,payload)!=result:raise ValueError('Original code generation changed before activation')
 applied=[]
 try:
  for name,row in result.items():
   raw=(journal/(name+'.after')).read_bytes()
   if digest(raw)!=row['after']:raise ValueError('Exact protected target snapshot required')
   replace_code(root/name,raw,row['mode']);applied.append(name)
 except BaseException:
  restore(root,journal,result,names=applied);raise

def restore(root,journal,result,*,names=None):
 selected=list(names if names is not None else result)
 for name in selected:
  if digest((root/name).read_bytes())!=result[name]['after']:raise ValueError('Keep unrelated changed code; rollback refused')
 for name in selected:
  row=result[name]
  if digest((root/name).read_bytes())!=row['after']:raise ValueError('Keep unrelated changed code; rollback refused')
  if row['before'] is None:(root/name).unlink()
  else:
   raw=(journal/(name+'.before')).read_bytes()
   if digest(raw)!=row['before']:raise ValueError('Exact code-only backup required')
   replace_code(root/name,raw,row['mode'])

def health():
 context=ssl.create_default_context(cafile='/home/hilman/.config/teslaandroid-waydroid/cert.pem')
 for _ in range(50):
  try:
   with urllib.request.urlopen('https://192.168.2.90:8443/api/health',context=context,timeout=2) as r:
    if r.status==200 and json.loads(r.read(1024)).get('status')=='ok':return
  except Exception:pass
  time.sleep(1)
 raise ValueError('Original TLS/runtime health did not return')
def main(args):
 if os.geteuid()!=0 or sys.platform!='linux' or args not in (['--apply'],['--rollback']):raise ValueError('Explicit root legacy operation required')
 os.umask(0o077)
 if args==['--apply']:
  result=prepare(ROOT,PAYLOAD,JOURNAL)
  subprocess.run(['systemctl','stop',SERVICE],check=True)
  activated=False
  try:
   exchange(ROOT,PAYLOAD,JOURNAL,result);activated=True;subprocess.run(['systemctl','start',SERVICE],check=True);health()
  except BaseException:
   # exchange restores a partial swap itself; only fully activated targets use full restore.
   if activated:restore(ROOT,JOURNAL,result)
   subprocess.run(['systemctl','start',SERVICE],check=True);raise
  write_new(JOURNAL/'complete.json',b'{"kind":"legacy-code-backport","healthVerified":true,"installerMigration":false}\n')
 else:
  if not (JOURNAL/'complete.json').exists() or (JOURNAL/'rolled-back.json').exists():raise ValueError('Completed unreplayed original code backup required')
  result=json.loads((JOURNAL/'intent.json').read_text());subprocess.run(['systemctl','stop',SERVICE],check=True)
  restore(ROOT,JOURNAL,result);subprocess.run(['systemctl','start',SERVICE],check=True);health();write_new(JOURNAL/'rolled-back.json',b'{}\n')
 print('Legacy code-only operation complete; Android data/TLS/lockscreen/native authority unchanged')
if __name__=='__main__':main(sys.argv[1:])
