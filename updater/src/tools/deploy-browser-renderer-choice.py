# SPDX-License-Identifier: GPL-3.0-only
"""New exact-content two-file .90 Settings backport; no runtime/service operations."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

# Import filesystem primitives only; the other helper's __main__ never runs.
spec=importlib.util.spec_from_file_location('aud_file_primitives',Path(__file__).with_name('deploy-webcodecs-aud.py'))
F=importlib.util.module_from_spec(spec);spec.loader.exec_module(F)
FRONT=Path('/home/hilman/teslaandroid-waydroid/frontend')
BACKUP=Path('/var/lib/teslaandroid-waydroid/backups/renderer-choice-20261024if')
FILES=(
 ('manager','js/display/display-renderer-manager.js','fe5cb641ca2b25c024229ecf1757d339fb8697642572b012dc684c135f78716b','130733e7a48084cc7a03fedd8c275dbed4c2658cc76c9f566ed5c7b8f85eb8be'),
 ('settings','js/settings/settings-controller.js','d450ec729362bd17a16d4f3dbb37048f97d548cfe841972388a4d1985707395e','37e8f597a9d1689e91dd496c799e1855b0710cebdf42d504da18dce09aaa5ce3'))

def replace(path,info,raw):
 pending=path.with_name('pending-renderer-choice-20261024if-'+path.name)
 F.save_new(pending,raw,uid=info.st_uid,gid=info.st_gid,mode=stat.S_IMODE(info.st_mode))
 current=path.lstat()
 if any(getattr(current,k)!=getattr(info,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns')):raise ValueError('Concurrent change retained')
 os.replace(pending,path);F.sync_directory(path.parent)

def main(mode):
 if os.geteuid()!=0:raise ValueError('Root needed only for protected browser assets and backup')
 if F.sha(F.read(FRONT/'js/display/renderers/h264-webcodecs/h264WebCodecsWorker.js')[1])!='2a5dd7f06f8cf3676af467590484e2e3df32ae1d2fb1a082e09ced91e01e9c41':raise ValueError('Verified AUD worker required')
 rows=[]
 for name,path,before,after in FILES:
  target=FRONT/path;info,raw=F.read(target)
  if mode=='--apply':
   candidate=F.read(Path(__file__).with_name('droiddeck-'+name+'-choice-20261024if.js'))[1]
   if F.sha(raw)!=before or F.sha(candidate)!=after:raise ValueError('Exact original browser baseline/candidate required')
  elif mode=='--restore':
   b=BACKUP.lstat()
   if not stat.S_ISDIR(b.st_mode) or b.st_uid!=0 or b.st_mode&0o077:raise ValueError('Original private backup required')
   candidate=F.read(BACKUP/(name+'.before.js'),root=True)[1]
   if F.sha(raw) not in (before,after) or F.sha(candidate)!=before:raise ValueError('Foreign state retained')
  else:raise ValueError('--apply or --restore required')
  rows.append((name,target,info,raw,candidate,before,after))
 if mode=='--apply':
  BACKUP.mkdir(mode=0o700)
  for name,_,_,raw,_,_,_ in rows:F.save_new(BACKUP/(name+'.before.js'),raw)
  F.save_new(BACKUP/'prepared.json',json.dumps({'files':[{'name':r[0],'before':r[5],'after':r[6]} for r in rows]}).encode())
  F.sync_directory(BACKUP);F.sync_directory(BACKUP.parent)
 for name,path,info,raw,candidate,before,after in rows:
  expected=after if mode=='--apply' else before
  if F.sha(raw)!=expected:replace(path,info,candidate)
  if F.sha(F.read(path)[1])!=expected:raise ValueError('Inspect retained original backup; do not replay')
 F.save_new(BACKUP/('applied.json' if mode=='--apply' else 'restored.json'),json.dumps({'verified':True,'browserOnly':True}).encode())
 F.sync_directory(BACKUP)
 print(json.dumps({'browserOnly':True,'action':mode,'filesVerified':2,'workerUnchanged':True}))

if __name__=='__main__':
 if len(sys.argv)!=2:raise ValueError('One explicit mode required')
 main(sys.argv[1])
