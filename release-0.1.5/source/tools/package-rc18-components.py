#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Canonical unsigned RC18 component distribution. Never installs or imports payloads."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile

PREFIX='droidmate-rc18-components/'
MANIFEST=PREFIX+'manifest.json'
CHECKPOINT=PREFIX+'source/checkpoint-manifest.json'
CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
LICENSE_PIN='3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'
SOURCES=tuple(sorted(('COPYING','docs/RC18-COMPONENTS.md','tools/package-rc18-components.py')))
ASSETS={
 'foundation':{'name':'droiddeck-0.1.0-rc.2-source.tar','size':7577600,'sha256':'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28'},
 'coreUpdater':{'name':'droiddeck-0.1.1-rc.20-update.pyz','size':10564125,'sha256':'4a942115bc7c2c3d89ad710bda523b5a316f235e90daa8ac72a7332d1eb02bd9'},
 'coreSource':{'name':'droiddeck-0.1.1-rc.20-source.tar','size':8632320,'sha256':'162fd7f50072da4700bc4eabbf95127dfd6724dd5d51959e5c805c9df80f0b3e'},
 'capturePackage':{'name':'capture-package.tar','size':3348480,'sha256':'01690d5b4efaef3e26a7b30bd58e2c3aaa321ac2f8ff8dd661f9e76d3388ba11'},
 'captureStatement':{'name':'capture-statement.json','size':805,'sha256':'a43fa0a8518d26c4f52cdb7afc3cd8e2b63b3d8c7af31419d2c86bf4b9e8b966'},
 'captureSignature':{'name':'capture-signature.bin','size':64,'sha256':'36ad487f0f96fc0f1dcbbee6af9d7789881db30ebc5a1a5db8372a99cd6fa46d'},
 'gpuHost':{'name':'droidmate-gpu-host-v2.tar','size':1290240,'sha256':'0e8ad94c23fb704439eeac7a72ea0970a896b8fa5222c9bbf7b647092199f06f'},
 'gpuSource':{'name':'droidmate-gpu-native-source-v2.tar','size':1822720,'sha256':'3cdc8e9837f069eaa3e10218d1ef0bdc3d4f4b0e205be8378d7550c132bcba57'},
 'gpuGuest':{'name':'droidmate-gpu-guest-v1.tar','size':122880,'sha256':'7b1f2cd6976a924673c3a95d190b8ee47a1e17872e1b571a4d406df42e51c8a3'},
}
MAX_FILE=12*1024**2
MAX_ARCHIVE=40*1024**2
MAX_SOURCE=1024**2

def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')
def document(raw):
    def unique(rows):
        value={}
        for k,v in rows:
            if k in value:raise ValueError('Duplicate inventory key refused')
            value[k]=v
        return value
    value=json.loads(raw,object_pairs_hook=unique)
    if encode(value)!=raw:raise ValueError('Canonical inventory required')
    return value

def revision(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{40}',value):raise ValueError('Original immutable Git revision required')
    return value

def records(files):return [{'path':n,'size':len(b),'sha256':sha(b)} for n,b in sorted(files.items())]

def source_checkpoint(sources,rev):
    return encode({'schema':1,'kind':'droidmate-rc18-recipe-source','sourceRevision':revision(rev),'files':records(sources)})

def validate_sources(sources):
    if type(sources) is not dict or set(sources)!=set(SOURCES):raise ValueError('Three closed public recipe sources required')
    for name,raw in sources.items():
        if type(raw) is not bytes or not 0<len(raw)<=MAX_SOURCE:raise ValueError('Bounded public source required')
        if (any(prefix in raw for prefix in (b'Plbal'+b'al',b'Plbal'+b'bl'))
            or re.search(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s*[A-Za-z0-9+/=]{16,}',raw)
            or re.search(rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b',raw)):
            raise ValueError('Private source material refused')
        if name.endswith('.py'):ast.parse(raw,filename=name)
    if sha(sources['COPYING'])!=LICENSE_PIN:raise ValueError('Original GPL license required')

def entries(assets,sources,rev):
    revision(rev);validate_sources(sources)
    if type(assets) is not dict or set(assets)!=set(ASSETS):raise ValueError('All nine qualified roles required')
    files={}
    for role,row in ASSETS.items():
        raw=assets[role]
        if type(raw) is not bytes or (len(raw),sha(raw))!=(row['size'],row['sha256']):
            raise ValueError('Exact original qualified asset bytes required')
        files[PREFIX+'assets/'+row['name']]=raw
    files.update({PREFIX+'source/'+n:b for n,b in sources.items()})
    files[CHECKPOINT]=source_checkpoint(sources,rev)
    meta={'schema':1,'kind':'droidmate-rc18-component-distribution','aggregateRelease':'RC18',
          'coreRuntimeVersion':'0.1.1-rc.20','compatibleReleaseSha256':CORE,'sourceRevision':rev,
          'sourceCheckpointSha256':sha(files[CHECKPOINT]),
          'assets':[{'role':role,**ASSETS[role]} for role in sorted(ASSETS)],
          'files':records(files),'signed':False,'installable':False,'readyForPublication':False}
    files[MANIFEST]=encode(meta)
    return files

def archive(files):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w:',format=tarfile.USTAR_FORMAT) as a:
        for name,raw in sorted(files.items()):
            item=tarfile.TarInfo(name);item.size=len(raw);item.mode=0o644
            a.addfile(item,io.BytesIO(raw))
    return output.getvalue()

def build(assets,sources,rev):
    raw=archive(entries(assets,sources,rev))
    if len(raw)>MAX_ARCHIVE:raise ValueError('Bounded component distribution required')
    return raw

def inspect(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ARCHIVE:raise ValueError('Bounded distribution required')
    allowed={MANIFEST,CHECKPOINT,*(PREFIX+'assets/'+r['name'] for r in ASSETS.values()),*(PREFIX+'source/'+n for n in SOURCES)}
    files={};total=0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as a:
            for m in a:
                if (m.name not in allowed or m.name in files or m.type!=tarfile.REGTYPE or m.mode!=0o644
                    or m.uid or m.gid or m.uname or m.gname or m.mtime or m.linkname or m.pax_headers
                    or m.sparse is not None or not 0<m.size<=MAX_FILE):
                    raise ValueError('Closed regular canonical members required')
                total+=m.size
                if total>MAX_ARCHIVE:raise ValueError('Expanded distribution bound exceeded')
                b=a.extractfile(m).read(MAX_FILE+1)
                if len(b)!=m.size:raise ValueError('Complete member bytes required')
                files[m.name]=b
    except (tarfile.TarError,EOFError,OSError) as error:raise ValueError('Invalid component archive') from error
    if set(files)!=allowed or archive(files)!=raw:raise ValueError('Exact complete canonical archive required')
    meta=document(files[MANIFEST]);rev=revision(meta.get('sourceRevision'))
    sources={n:files[PREFIX+'source/'+n] for n in SOURCES}
    assets={role:files[PREFIX+'assets/'+r['name']] for role,r in ASSETS.items()}
    if files!=entries(assets,sources,rev):raise ValueError('Exact independent payload/inventory binding required')
    return {'kind':'droidmate-rc18-component-distribution','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
            'sha256':sha(raw),'manifestSha256':sha(files[MANIFEST]),'sourceCheckpointSha256':sha(files[CHECKPOINT]),
            'sourceRevision':rev,'assets':len(assets),'sources':len(sources),'members':len(files),
            'signed':False,'installable':False,'readyForPublication':False,'payloadExecuted':False,'servicesChanged':False}

def identity(s):
    # Windows3.14 pathname stat exposes ChangeTime in ctime while descriptor
    # stat can expose CreationTime. Both agree on explicit birthtime/mtime.
    # Linux keeps ctime as an additional mutation generation check.
    epoch=getattr(s,'st_birthtime_ns',s.st_ctime_ns) if os.name=='nt' else s.st_ctime_ns
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,epoch)

def read(path,limit=MAX_FILE):
    path=Path(os.path.abspath(path))
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Real regular input paths required')
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=limit:
        raise ValueError('Bounded single-link input required')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        opened=os.fstat(fd)
        if identity(opened)!=identity(before):raise ValueError('Original input generation required')
        parts=[];size=0
        while size<=limit:
            b=os.read(fd,min(1024**2,limit+1-size))
            if not b:break
            parts.append(b);size+=len(b)
        raw=b''.join(parts)
        if len(raw)!=opened.st_size or identity(os.fstat(fd))!=identity(opened) or identity(path.lstat())!=identity(opened):
            raise ValueError('Complete stable original input required')
        return raw
    finally:os.close(fd)

def write_new(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        with os.fdopen(os.dup(fd),'wb') as f:f.write(raw);f.flush()
        os.fsync(fd)
    finally:os.close(fd)

def git_sources(root,rev):
    revision(rev)
    def git(*args):return subprocess.check_output(['git','--no-lazy-fetch','--no-replace-objects',*args],cwd=root)
    commit=git('cat-file','commit',rev)
    if hashlib.sha1(b'commit '+str(len(commit)).encode()+b'\0'+commit).hexdigest()!=rev:raise ValueError('Original Git commit object required')
    result={}
    for name in SOURCES:
        path='runtime/portable/gpl-3.0.txt' if name=='COPYING' else name
        row=git('ls-tree','--full-tree','--long',rev,'--',path)
        m=re.fullmatch(rb'(100644|100755) blob ([a-f0-9]{40}) +([0-9]+)\t'+re.escape(path.encode())+rb'\n',row)
        if not m or not 0<int(m[3])<=MAX_SOURCE:raise ValueError('Original regular committed source required')
        raw=git('cat-file','blob',m[2].decode())
        if len(raw)!=int(m[3]) or hashlib.sha1(b'blob '+m[3]+b'\0'+raw).hexdigest().encode()!=m[2]:
            raise ValueError('Original source blob bytes required')
        result[name]=raw
    validate_sources(result);return result

def source_tree(root,rev,pin):
    revision(rev)
    if type(pin) is not str or not re.fullmatch('[a-f0-9]{64}',pin):raise ValueError('External original source checkpoint digest required')
    raw=read(Path(root)/'checkpoint-manifest.json',MAX_SOURCE)
    if sha(raw)!=pin:raise ValueError('Original source checkpoint digest differs')
    meta=document(raw)
    files={n:read(Path(root)/n,MAX_SOURCE) for n in SOURCES}
    validate_sources(files)
    if raw!=source_checkpoint(files,rev):raise ValueError('Exact original source checkpoint/revision required')
    return files

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='operation',required=True)
    review=sub.add_parser('review');review.add_argument('archive',type=Path);review.add_argument('sha256')
    make=sub.add_parser('build');source=make.add_mutually_exclusive_group(required=True)
    source.add_argument('--source-root',type=Path);source.add_argument('--checkpoint',type=Path)
    make.add_argument('--checkpoint-sha256');make.add_argument('--revision',required=True);make.add_argument('--output',type=Path,required=True)
    for role in ASSETS:
        flag=re.sub(r'([A-Z])',lambda m:'-'+m[1].lower(),role)
        make.add_argument('--'+flag,dest=role,type=Path,required=True)
    args=parser.parse_args()
    if args.operation=='review':
        raw=read(args.archive,MAX_ARCHIVE)
        if sha(raw)!=args.sha256:raise ValueError('External distribution digest differs')
    else:
        if args.source_root and args.checkpoint_sha256:raise ValueError('Checkpoint digest belongs only to source-tree mode')
        sources=git_sources(args.source_root,args.revision) if args.source_root else source_tree(args.checkpoint,args.revision,args.checkpoint_sha256)
        raw=build({role:read(getattr(args,role)) for role in ASSETS},sources,args.revision)
        result=inspect(raw);write_new(args.output,raw)
    print(encode(inspect(raw)).decode('ascii').strip())

if __name__=='__main__':main()

