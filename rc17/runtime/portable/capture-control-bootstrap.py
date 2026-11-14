# SPDX-License-Identifier: GPL-3.0-only
"""Operator-pinned capture control loader. Authenticate before importing package code."""
from contextlib import ExitStack,contextmanager
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import threading

ROOT=Path('/var/lib/droiddeck-capture-controls')
TRUST=Path('/var/lib/droiddeck/update-trust.json')
SELF=Path('/var/lib/droiddeck-capture-loader/bootstrap.py')
SERVICE=Path('/etc/systemd/system/droiddeck-capture-recover@.service')
PREFIX='droiddeck-capture-activation/'
BASE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
NAMES=tuple(sorted(('capture-activation-auth.py','capture-component-auth.py','capture-component-stage.py',
 'capture-private-view.py','capture-release-authority.py','capture-release-verifier.json','capture-runtime-package.py',
 'capture-policy-controller.py','capture-policy-transaction.py','capture-video-admission.py','capture-video-recycle.py',
 'capture-view-controller.py','gpu-native.js','recovery-probe-io.js','effective-units.py','runtime-candidate-admission.py','runtime-candidate-dependencies.py',
 'update-admission.py','update-package.py','update-target.py','preview-gateway/release-version.json')))
CONTRACT={'schema':1,'scope':'capture-worker-only','modes':['cpu','gpu'],
 'originalUpdaterLease':'required','componentAndViewOwnership':'required','exactEffectiveGraph':'required',
 'heldNativeSource':'required','durablePreviousWorkerRestore':'required','crossBootAdmission':'independent',
 'originalRuntimeAndConfig':'preserve-sealed-namespace','androidData':'preserve','unraidChanges':False,'healthyExpiry':False}
KEYS={'schema','kind','channel','purpose','activationAllowed','keySha256','packageSha256','manifestSha256',
 'componentSha256','controlSourcesSha256','sourceRevision','compatibleReleaseSha256','contractSha256'}
META={'intent.json','package.tar','statement.json','signature.bin','complete.json','runtime'}
MAX_ARCHIVE=44*1024**2
ENV={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'}


def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode('ascii')
def unique(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise ValueError('Duplicate authorization metadata')
        out[key]=value
    return out
def document(raw):
    out=json.loads(raw,object_pairs_hook=unique)
    if encode(out)!=raw:raise ValueError('Canonical public metadata required')
    return out
def digest(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Exact authorization digest required')
    return value
def source(name):
    if name not in NAMES:raise ValueError('Closed signed control file required')
    return 'runtime/'+name if name=='preview-gateway/release-version.json' else 'runtime/portable/'+name
def generation(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns]
def root_linux():
    if sys.platform!='linux' or os.geteuid()!=0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:raise ValueError('Root Linux fixed system bus required')


class Tree:
    def __init__(self,stack):self.stack,self.dirs,self.files=stack,{},{}
    def directory(self,path):
        path=Path(path)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Canonical protected path required')
        if path in self.dirs:return self.dirs[path][2]
        parent,name=(None,'/') if path==Path('/') else (self.directory(path.parent),path.name)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);self.stack.callback(os.close,fd);s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode&0o022:raise ValueError('Protected control ancestry required')
        self.dirs[path]=(parent,name,fd,[s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid]);return fd
    def file(self,path,limit,mode=0o600):
        path=Path(path)
        if path in self.files:return self.files[path][4]
        parent=self.directory(path.parent);fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        self.stack.callback(os.close,fd);s=os.fstat(fd)
        if (not stat.S_ISREG(s.st_mode) or [s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode)]!=[0,0,1,mode]
                or not 0<s.st_size<=limit):raise ValueError('Owned bounded control input required')
        raw=os.pread(fd,limit+1,0)
        if len(raw)!=s.st_size:raise ValueError('Complete control input required')
        self.files[path]=(parent,path.name,fd,generation(s),raw);self.check();return raw
    def check(self):
        for parent,name,fd,before in self.dirs.values():
            for s in (os.fstat(fd),os.stat(name,dir_fd=parent,follow_symlinks=False)):
                if [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid]!=before:raise ValueError('Control ancestry changed')
        for parent,name,fd,before,_raw in self.files.values():
            if generation(os.fstat(fd))!=before or generation(os.stat(name,dir_fd=parent,follow_symlinks=False))!=before:
                raise ValueError('Held control input changed')


def public_key(raw):
    value=document(raw)
    if (type(value) is not dict or set(value)!={'schema','kind','channel','publicKeySpkiHex'}
        or type(value['schema']) is not int or value['schema']!=1 or value['kind']!='droiddeck-update-trust'
        or value['channel']!='pilot' or type(value['publicKeySpkiHex']) is not str
        or not re.fullmatch('302a300506032b6570032100[a-f0-9]{64}',value['publicKeySpkiHex'])):
        raise ValueError('Existing enrolled public Ed25519 key required')
    return bytes.fromhex(value['publicKeySpkiHex'])


def verify_signature(key,statement,signature):
    if len(signature)!=64 or not 0<len(statement)<=8192:raise ValueError('Bounded signature required')
    with ExitStack() as stack:
        handles=[]
        for raw in (key,statement,signature):
            f=stack.enter_context(tempfile.TemporaryFile());f.write(raw);f.flush();f.seek(0);handles.append(f.fileno())
        paths=['/proc/self/fd/'+str(fd) for fd in handles]
        r=subprocess.run(['/usr/bin/openssl','pkeyutl','-verify','-pubin','-keyform','DER','-inkey',paths[0],
            '-rawin','-in',paths[1],'-sigfile',paths[2]],pass_fds=tuple(handles),env=ENV,cwd='/',
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
        if r.returncode:raise ValueError('Activation signature does not match enrolled key')


def authenticate(package,statement,signature,trust):
    if type(package) is not bytes or not 0<len(package)<=MAX_ARCHIVE:raise ValueError('Bounded control package required')
    key=public_key(trust);value=document(statement)
    if (type(value) is not dict or set(value)!=KEYS or type(value['schema']) is not int or value['schema']!=1
        or value['kind']!='droiddeck-capture-activation-authorization' or value['channel']!='pilot'
        or value['purpose']!='guarded-capture-activation' or value['activationAllowed'] is not True
        or value['keySha256']!=sha(key) or value['packageSha256']!=sha(package)
        or value['compatibleReleaseSha256']!=BASE or value['contractSha256']!=sha(encode(CONTRACT))
        or type(value['sourceRevision']) is not str or not re.fullmatch('[a-f0-9]{40}',value['sourceRevision'])):
        raise ValueError('Separate signed guarded activation domain required')
    for k in ('manifestSha256','componentSha256','controlSourcesSha256'):digest(value[k])
    # NO package module/import/AST/executable source is consulted before this.
    verify_signature(key,statement,signature)
    import tarfile
    allowed={PREFIX+'manifest.json',PREFIX+'component.tar',*(PREFIX+'control/'+n for n in NAMES)}
    members={};total=0
    with tarfile.open(fileobj=io.BytesIO(package),mode='r:') as t:
        for row in t:
            limit=40*1024**2 if row.name==PREFIX+'component.tar' else 1048576
            if (row.name not in allowed or row.name in members or row.type!=tarfile.REGTYPE or row.mode!=0o644
                or row.uid or row.gid or row.uname or row.gname or row.mtime or row.pax_headers or row.sparse is not None
                or not 0<row.size<=limit):raise ValueError('Closed signed control namespace required')
            total+=row.size
            if total>MAX_ARCHIVE:raise ValueError('Control payload bound exceeded')
            raw=t.extractfile(row).read(limit+1)
            if len(raw)!=row.size:raise ValueError('Complete signed control bytes required')
            members[row.name]=raw
    if set(members)!=allowed:raise ValueError('Complete signed control payload required')
    canonical=io.BytesIO()
    with tarfile.open(fileobj=canonical,mode='w',format=tarfile.USTAR_FORMAT) as t:
        for name,raw in sorted(members.items()):
            row=tarfile.TarInfo(name);row.size=len(raw);row.mode=0o644;t.addfile(row,io.BytesIO(raw))
    if canonical.getvalue()!=package:raise ValueError('Canonical signed archive required')
    raw=members[PREFIX+'manifest.json'];manifest=document(raw)
    if (sha(raw)!=value['manifestSha256'] or type(manifest) is not dict
        or set(manifest)!={'schema','kind','sourceRevision','compatibleReleaseSha256','componentSha256',
            'componentManifestSha256','controlSourcesSha256','contract','controls'}
        or type(manifest['schema']) is not int or manifest['schema']!=1 or manifest['kind']!='droiddeck-capture-activation-package'
        or manifest['sourceRevision']!=value['sourceRevision'] or manifest['compatibleReleaseSha256']!=BASE
        or manifest['componentSha256']!=value['componentSha256'] or manifest['controlSourcesSha256']!=value['controlSourcesSha256']
        or encode(manifest['contract'])!=encode(CONTRACT) or sha(members[PREFIX+'component.tar'])!=value['componentSha256']):
        raise ValueError('Signed package contract differs')
    files={source(n):members[PREFIX+'control/'+n] for n in NAMES}
    rows=[{'path':n,'size':len(members[PREFIX+'control/'+n]),'sha256':sha(members[PREFIX+'control/'+n])} for n in NAMES]
    if encode(manifest['controls'])!=encode(rows) or sha(encode(rows))!=value['controlSourcesSha256']:
        raise ValueError('Signed source catalog differs')
    if files['runtime/preview-gateway/release-version.json']!=encode({'version':'0.1.1-rc.20'}):raise ValueError('Fixed version asset required')
    return value,files


def write_new(fd,name,raw):
    out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        os.fchmod(out,0o600)
        with os.fdopen(out,'wb',closefd=False) as f:f.write(raw);f.flush();os.fsync(out)
    finally:os.close(out)
    os.fsync(fd)


class Loader(importlib.abc.Loader):
    def __init__(self,tree,path):self.tree,self.path=tree,path
    def create_module(self,spec):return None
    def exec_module(self,module):
        self.tree.check()
        if self.path not in self.tree.files:raise ValueError('Unheld control module refused')
        exec(compile(self.tree.files[self.path][4],str(self.path),'exec'),module.__dict__)
        self.tree.check()


@contextmanager
def imports(tree,slot):
    if threading.active_count()!=1:raise ValueError('Isolated single-thread control loader required')
    original=importlib.util.spec_from_file_location
    allowed={slot/source(n) for n in NAMES if n.endswith('.py')}
    def spec(name,location=None,*args,**kwargs):
        p=Path(location) if location is not None else None
        if p not in allowed:raise ValueError('Unsigned control import refused')
        s=importlib.machinery.ModuleSpec(name,Loader(tree,p),origin=str(p));s.has_location=True;return s
    importlib.util.spec_from_file_location=spec
    try:yield spec
    finally:importlib.util.spec_from_file_location=original


def code_files(tree,slot,files):
    for name,raw in files.items():
        if tree.file(slot/name,1048576)!=raw:raise ValueError('Exact signed control source/asset bytes required')
    # Closed recursive code namespace, no import sidecars or planted assets.
    needed={'runtime'}
    for name in files:
        p=Path(name)
        needed.update(str(v) for v in p.parents if str(v)!='.')
    for name in needed:
        directory=slot/name;fd=tree.directory(directory)
        if set(os.listdir(fd))!={str(Path(p).relative_to(name)).split('/')[0] for p in files if p.startswith(name+'/')}:
            raise ValueError('Foreign control namespace member')
    return {name:tree.files[slot/name][3] for name in sorted(files)}


def intent(value,trust,statement,signature,slot):
    s=slot.lstat()
    return {'schema':1,'kind':'owned-signed-capture-controls','packageSha256':value['packageSha256'],
        'statementSha256':sha(statement),'signatureSha256':sha(signature),'trustSha256':sha(trust),
        'directoryIdentity':[s.st_dev,s.st_ino]}


def complete(tree,slot,files,raw_intent):
    objects=code_files(tree,slot,files)
    objects.update({n:tree.files[slot/n][3] for n in ('intent.json','package.tar','statement.json','signature.bin')})
    return {'schema':1,'intentSha256':sha(raw_intent),
        'directories':{str(path.relative_to(slot)):row[3] for path,row in tree.dirs.items() if path!=slot and slot in path.parents},
        'objects':objects}


def load_auth(tree,slot):
    with imports(tree,slot) as spec:
        d=spec('admitted_capture_authorization',slot/'runtime/portable/capture-activation-auth.py')
        a=importlib.util.module_from_spec(d);d.loader.exec_module(a)
    return a


def stage(package_path,statement_path,signature_path,expected):
    import fcntl
    root_linux();digest(expected)
    with ExitStack() as stack:
        tree=Tree(stack)
        package=tree.file(Path(package_path),MAX_ARCHIVE);statement=tree.file(Path(statement_path),8192)
        signature=tree.file(Path(signature_path),64);trust=tree.file(TRUST,4096)
        if sha(package)!=expected:raise ValueError('Independently selected package digest required')
        value,files=authenticate(package,statement,signature,trust);tree.check()
        parent=tree.directory(ROOT.parent)
        try:os.mkdir(ROOT.name,0o700,dir_fd=parent);os.fsync(parent)
        except FileExistsError:pass
        root=tree.directory(ROOT)
        if stat.S_IMODE(os.fstat(root).st_mode)!=0o700:raise ValueError('Private control root required')
        fcntl.flock(root,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.mkdir(expected,0o700,dir_fd=root);os.fsync(root);slot=ROOT/expected;fd=tree.directory(slot)
        raw_intent=encode(intent(value,trust,statement,signature,slot))
        for n,raw in [('intent.json',raw_intent),('package.tar',package),('statement.json',statement),('signature.bin',signature)]:
            write_new(fd,n,raw);tree.file(slot/n,MAX_ARCHIVE if n=='package.tar' else 8192)
        for name,raw in files.items():
            p=Path(name);current=slot
            for part in p.parent.parts:
                parent=tree.directory(current)
                try:os.mkdir(part,0o700,dir_fd=parent);os.fsync(parent)
                except FileExistsError:pass
                current=current/part
            write_new(tree.directory(current),p.name,raw)
        code_files(tree,slot,files)
        auth=load_auth(tree,slot)
        with imports(tree,slot):auth.authenticate(package,statement,signature,trust)
        tree.check();write_new(fd,'complete.json',encode(complete(tree,slot,files,raw_intent)))
        return {'kind':'owned-signed-capture-controls','packageSha256':expected,'controlFiles':len(files),'servicesChanged':False,'readyForActivation':False}


@contextmanager
def hold_controls(expected):
    root_linux();digest(expected)
    with ExitStack() as stack:
        tree=Tree(stack);slot=ROOT/expected;fd=tree.directory(slot)
        if stat.S_IMODE(os.fstat(fd).st_mode)!=0o700 or set(os.listdir(fd))!=META:raise ValueError('Completed private control slot required')
        package=tree.file(slot/'package.tar',MAX_ARCHIVE);statement=tree.file(slot/'statement.json',8192)
        signature=tree.file(slot/'signature.bin',64);trust=tree.file(TRUST,4096)
        if sha(package)!=expected:raise ValueError('Original control package required')
        value,files=authenticate(package,statement,signature,trust)
        raw_intent=tree.file(slot/'intent.json',8192)
        if raw_intent!=encode(intent(value,trust,statement,signature,slot)):raise ValueError('Original control slot intent required')
        expected_complete=encode(complete(tree,slot,files,raw_intent))
        if tree.file(slot/'complete.json',1048576)!=expected_complete:raise ValueError('Original complete code generations required')
        owner=os.getpid()
        def check():
            if os.getpid()!=owner or set(os.listdir(fd))!=META:raise ValueError('Current-process complete control slot required')
            tree.check();code_files(tree,slot,files);return True
        with imports(tree,slot) as spec:
            d=spec('admitted_capture_auth',slot/'runtime/portable/capture-activation-auth.py')
            auth=importlib.util.module_from_spec(d);d.loader.exec_module(auth)
            report=auth.authenticate(package,statement,signature,trust)
            def module(name):
                if name not in NAMES or not name.endswith('.py'):raise ValueError('Fixed admitted control module required')
                check();d=spec('admitted_'+name.replace('-','_'),slot/source(name));m=importlib.util.module_from_spec(d);d.loader.exec_module(m);check();return m
            yield {'report':report,'statement':value,'module':module,'check':check}
            check()


@contextmanager
def hold_self():
    root_linux()
    if Path(__file__)!=SELF:raise ValueError('Fixed independently enrolled loader path required')
    with ExitStack() as stack:
        tree=Tree(stack);fd=tree.directory(SELF.parent)
        if stat.S_IMODE(os.fstat(fd).st_mode)!=0o700 or set(os.listdir(fd))!={'bootstrap.py','complete.json'}:
            raise ValueError('Closed independently enrolled loader namespace required')
        raw=tree.file(SELF,1048576);unit=tree.file(SERVICE,8192,0o644)
        receipt=document(tree.file(SELF.parent/'complete.json',8192))
        if (type(receipt) is not dict or set(receipt)!={'schema','kind','sourceRevision','files'}
            or type(receipt['schema']) is not int or receipt['schema']!=1 or receipt['kind']!='operator-pinned-capture-loader'
            or type(receipt['sourceRevision']) is not str or not re.fullmatch('[a-f0-9]{40}',receipt['sourceRevision'])
            or receipt['files']!={'bootstrap.py':{'sha256':sha(raw),'generation':tree.files[SELF][3]},
                'recoveryTemplate':{'sha256':sha(unit),'generation':tree.files[SERVICE][3]}}):
            raise ValueError('Original operator loader/service enrollment required')
        tree.check();yield tree.check;tree.check()


def dispatch(args):
    if len(args)==5 and args[0]=='stage':return stage(*args[1:])
    if len(args)==2 and args[0]=='inspect':
        with hold_controls(args[1]) as c:return {**c['report'],'ownedControlSourcesVerified':True}
    if len(args)>=2 and args[0] in ('change','run','restore','recover','status'):
        with hold_controls(args[1]) as c:return c['module']('capture-view-controller.py').dispatch(c,args[0],args[2:])
    raise ValueError('stage ROOT_PACKAGE ROOT_STATEMENT ROOT_SIGNATURE SHA; inspect SHA; change/restore/recover/status SHA [ARGS]')


def main(args):
    with hold_self() as owned:
        if len(args)>=2 and args[0] in ('change','run','restore','recover','status','inspect'):
            with hold_controls(args[1]) as c:
                previous=c['check']
                def check():
                    owned();return previous()
                c['check']=check
                if args[0]=='inspect':return {**c['report'],'ownedControlSourcesVerified':True,'operatorLoaderVerified':True}
                return c['module']('capture-view-controller.py').dispatch(c,args[0],args[2:])
        result=dispatch(args);owned();return result


if __name__=='__main__':
    try:print(encode(main(sys.argv[1:])).decode().strip())
    except Exception as e:
        print('Capture control refused; retain owned recovery state: '+type(e).__name__,file=sys.stderr);sys.exit(1)
