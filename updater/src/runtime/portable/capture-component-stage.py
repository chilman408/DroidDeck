# SPDX-License-Identifier: GPL-3.0-only
"""Durable root-owned complete capture staging, never service activation.

Original updater lease retained throughout. Only a fresh independently owned
/var/lib/droiddeck-capture slot is written; old /opt and /etc seals stay intact.
Partial slots are retained and REFUSED, never silently adopted or overwritten.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import io
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location('capture_stage_' + name.replace('-', '_'), Path(__file__).with_name(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


A=load('capture-component-auth');R=load('capture-release-authority');P=A.P
ROOT=Path('/var/lib/droiddeck-capture')
TRUST=Path('/var/lib/droiddeck/update-trust.json')
META={'intent.json','bundle.tar','statement.json','signature.bin','complete.json','runtime'}


def root_linux():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Root Linux capture staging required')


class Dirs:
    def __init__(self,stack):self.stack,self.opened=stack,{}
    def open(self,path):
        path=Path(path)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Canonical protected directory required')
        if path in self.opened:return self.opened[path][2]
        parent,name=(None,'/') if path==Path('/') else (self.open(path.parent),path.name)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);self.stack.callback(os.close,fd)
        info=os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_gid!=0 or info.st_mode&0o022:raise ValueError('Protected root-owned directory ancestry required')
        identity=(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)
        self.opened[path]=(parent,name,fd,identity)
        return fd
    def check(self):
        for parent,name,fd,before in self.opened.values():
            for s in (os.fstat(fd),os.stat(name,dir_fd=parent,follow_symlinks=False)):
                if (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)!=before:raise ValueError('Capture stage ancestry changed')


def write_new(fd,name,raw,mode=0o600):
    out=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode,dir_fd=fd)
    try:
        os.fchmod(out,mode)
        with os.fdopen(out,'wb',closefd=False) as f:f.write(raw);f.flush();os.fsync(out)
    finally:os.close(out)
    os.fsync(fd)


def generation(s):return list(P.identity(s))


def read_at(fd,name,limit,mode):
    file=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
    try:
        before=os.fstat(file)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid,before.st_gid,stat.S_IMODE(before.st_mode),before.st_nlink)!=(0,0,mode,1)
                or not 0<before.st_size<=limit):raise ValueError('Owned bounded stage file required')
        with os.fdopen(file,'rb',closefd=False) as stream:raw=stream.read(limit+1)
        if (len(raw)!=before.st_size or P.identity(os.fstat(file))!=P.identity(before)
                or P.identity(os.stat(name,dir_fd=fd,follow_symlinks=False))!=P.identity(before)):raise ValueError('Stage file generation changed')
        return raw,generation(before)
    finally:os.close(file)


def payloads(bundle):
    A.C.review(bundle,P.sha(bundle))
    with tarfile.open(fileobj=io.BytesIO(bundle),mode='r:') as archive:
        prefix=A.C.PREFIX+'runtime/'
        return {r.name[len(prefix):]:archive.extractfile(r).read() for r in archive if r.name.startswith(prefix)}


def tree(stack,dirs,slot,expected,*,create=False,checkpoint=lambda event:None):
    """Fixed reviewed payload map only; no tar extraction or selected paths."""
    directory=slot/'runtime'
    if create:os.mkdir('runtime',mode=0o700,dir_fd=dirs.open(slot));os.fsync(dirs.open(slot))
    root=dirs.open(directory)
    required={''}
    for name in expected:
        p=PurePosixPath(name)
        if p.is_absolute() or str(p)!=name or '..' in p.parts:raise ValueError('Closed stage payload path required')
        required.update(str(parent) for parent in p.parents if str(parent)!='.')
    if create:
        for relative in sorted(required-{''},key=lambda v:(len(v.split('/')),v)):
            parent=dirs.open(directory/str(PurePosixPath(relative).parent))
            os.mkdir(PurePosixPath(relative).name,mode=0o700,dir_fd=parent);os.fsync(parent)
            dirs.open(directory/relative)
        for index,(name,raw) in enumerate(sorted(expected.items())):
            p=PurePosixPath(name);write_new(dirs.open(directory/str(p.parent)),p.name,raw,0o444)
            if index==0:checkpoint('first-payload-written')
        # Seal payload directories after all children are durable. Refresh the
        # held generation intentionally; all subsequent reads require that mode.
        for relative in sorted(required,key=lambda v:(len(v.split('/')),v),reverse=True):
            path=directory/relative;fd=dirs.open(path);os.fchmod(fd,0o555);os.fsync(fd)
            parent,name,handle,_=dirs.opened[path];s=os.fstat(handle)
            dirs.opened[path]=(parent,name,handle,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid))
    observed,records=set(),{}
    for relative in sorted(required):
        fd=dirs.open(directory/relative);s=os.fstat(fd)
        if stat.S_IMODE(s.st_mode)!=0o555:raise ValueError('Sealed read-only runtime directory required')
        names=set(os.listdir(fd));wanted={n[len(relative)+1:].split('/')[0] if relative else n.split('/')[0] for n in expected if not relative or n.startswith(relative+'/')}
        if names!=wanted:raise ValueError('Exact complete runtime directory inventory required')
        records['runtime'+('/'+relative if relative else '')]=generation(s)
    for name,raw in sorted(expected.items()):
        p=PurePosixPath(name);value,identity=read_at(dirs.open(directory/str(p.parent)),p.name,A.C.MAX_FILE,0o444)
        if value!=raw:raise ValueError('Owned runtime payload differs')
        records['runtime/'+name]=identity
    dirs.check();return records


def require_base(report):
    if (report.get('kind')!='verified-installed-pilot-release' or report.get('signedReleaseVerified') is not True
            or report.get('installedCodeOwnershipVerified') is not True or report.get('updateLockShared') is not True
            or report.get('signedBundleSha256')!=A.BASE_RELEASE or report.get('servicesChanged') is not False):
        raise ValueError('Original locked compatible signed installation required')
    return report['binding']


def private_dir(dirs,path):
    fd=dirs.open(path)
    if stat.S_IMODE(os.fstat(fd).st_mode)!=0o700:raise ValueError('Exact private stage directory required')
    return fd


def stage(bundle_path,statement_path,signature_path,*,checkpoint=lambda event:None):
    root_linux()
    trust=P.read_file(TRUST,4096,owner=0)
    bundle=P.read_file(Path(bundle_path),A.C.MAX_ARCHIVE,owner=0)
    statement=P.read_file(Path(statement_path),8192,owner=0);signature=P.read_file(Path(signature_path),64,owner=0)
    authenticated=A.authenticate(bundle,statement,signature,trust)
    digest=P.sha(bundle);expected=payloads(bundle)
    with R.hold_release() as lease,ExitStack() as stack:
        binding=require_base(lease['report']);dirs=Dirs(stack);parent=dirs.open(ROOT.parent)
        try:os.mkdir(ROOT.name,mode=0o700,dir_fd=parent);os.fsync(parent)
        except FileExistsError:pass
        root=private_dir(dirs,ROOT);fcntl.flock(root,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Never replay/adopt an existing complete OR partial slot, even same bytes.
        os.mkdir(digest,mode=0o700,dir_fd=root);os.fsync(root)
        slot=ROOT/digest;fd=private_dir(dirs,slot);s=os.fstat(fd)
        intent={'schema':1,'kind':'owned-capture-staging','bundleSha256':digest,
            'statementSha256':P.sha(statement),'signatureSha256':P.sha(signature),'trustSha256':P.sha(trust),
            'keySha256':authenticated['keySha256'],'baseBinding':binding,
            'directoryIdentity':[s.st_dev,s.st_ino],'activationAllowed':False}
        write_new(fd,'intent.json',P.encode(intent));checkpoint('intent-written')
        for name,raw in [('bundle.tar',bundle),('statement.json',statement),('signature.bin',signature)]:write_new(fd,name,raw)
        records=tree(stack,dirs,slot,expected,create=True,checkpoint=checkpoint);checkpoint('files-staged')
        for name,limit in [('intent.json',8192),('bundle.tar',A.C.MAX_ARCHIVE),('statement.json',8192),('signature.bin',64)]:
            _,records[name]=read_at(fd,name,limit,0o600)
        if require_base(lease['check']())!=binding:raise ValueError('Original installation changed during staging')
        dirs.check();checkpoint('before-complete')
        write_new(fd,'complete.json',P.encode({'schema':1,'intentSha256':P.sha(P.encode(intent)),'objects':records}));checkpoint('complete-written')
        if set(os.listdir(fd))!=META:raise ValueError('Unexpected stage metadata')
        return {'kind':'owned-capture-component-staged','bundleSha256':digest,'files':len(expected),
            'originalInstallationOwnershipVerified':True,'releaseStatementAuthenticated':True,'activationAllowed':False,
            'readyForActivation':False,'servicesChanged':False,'payloadExecuted':False}


@contextmanager
def hold_component(digest):
    root_linux();P.digest(digest)
    with R.hold_release() as lease,ExitStack() as stack:
        binding=require_base(lease['report']);dirs=Dirs(stack);root=private_dir(dirs,ROOT)
        fcntl.flock(root,fcntl.LOCK_EX|fcntl.LOCK_NB)
        slot=ROOT/digest;fd=private_dir(dirs,slot)
        if set(os.listdir(fd))!=META:raise ValueError('Partial or foreign capture slot refused')
        intent_raw,_=read_at(fd,'intent.json',8192,0o600);intent=P.document(intent_raw)
        bundle,_=read_at(fd,'bundle.tar',A.C.MAX_ARCHIVE,0o600);statement,_=read_at(fd,'statement.json',8192,0o600);signature,_=read_at(fd,'signature.bin',64,0o600)
        trust=P.read_file(TRUST,4096,owner=0);auth=A.authenticate(bundle,statement,signature,trust);s=os.fstat(fd)
        expected={'schema':1,'kind':'owned-capture-staging','bundleSha256':P.sha(bundle),'statementSha256':P.sha(statement),
            'signatureSha256':P.sha(signature),'trustSha256':P.sha(trust),'keySha256':auth['keySha256'],
            'baseBinding':binding,'directoryIdentity':[s.st_dev,s.st_ino],'activationAllowed':False}
        if digest!=P.sha(bundle) or intent_raw!=P.encode(expected):raise ValueError('Original capture stage binding required')
        records=tree(stack,dirs,slot,payloads(bundle))
        for name,limit in [('intent.json',8192),('bundle.tar',A.C.MAX_ARCHIVE),('statement.json',8192),('signature.bin',64)]:_,records[name]=read_at(fd,name,limit,0o600)
        complete,_=read_at(fd,'complete.json',1024*1024,0o600)
        if complete!=P.encode({'schema':1,'intentSha256':P.sha(intent_raw),'objects':records}):raise ValueError('Original completed stage object generations required')
        dirs.check()
        report={'kind':'owned-capture-component-inspection','bundleSha256':digest,'files':len(payloads(bundle)),
            'originalInstallationOwnershipVerified':True,'releaseStatementAuthenticated':True,
            'activationAllowed':False,'readyForActivation':False,'servicesChanged':False,'payloadExecuted':False}
        # Retain original updater + component flocks and protected descriptors.
        # These generation checks are for inert view preparation boundaries;
        # they are not a runtime native/effective-graph admission adapter.
        _,complete_generation=read_at(fd,'complete.json',1024*1024,0o600)
        retained={**records,'complete.json':complete_generation}
        def check():
            dirs.check()
            if set(os.listdir(fd))!=META:raise ValueError('Component metadata namespace changed')
            for name,before in retained.items():
                parent=dirs.open(slot/str(PurePosixPath(name).parent))
                current=os.stat(PurePosixPath(name).name,dir_fd=parent,follow_symlinks=False)
                if generation(current)!=before:raise ValueError('Held component generation changed')
        check()
        yield {'report':report,'runtimeRoot':str(slot/'runtime'),'receiptSha256':P.sha(complete),
               'baseBinding':binding,'check':check}
        check()


def inspect(digest):
    with hold_component(digest) as component:return dict(component['report'])


if __name__=='__main__':
    try:
        if len(sys.argv)==5 and sys.argv[1]=='stage':result=stage(*sys.argv[2:])
        elif len(sys.argv)==3 and sys.argv[1]=='inspect':result=inspect(sys.argv[2])
        else:raise ValueError('stage ROOT_PRIVATE_BUNDLE STATEMENT SIGNATURE or inspect DIGEST')
        print(P.encode(result).decode('ascii').strip())
    except Exception as error:
        print('Capture staging refused; retain partial state: '+type(error).__name__,file=sys.stderr);sys.exit(1)
