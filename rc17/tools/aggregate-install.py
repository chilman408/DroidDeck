# SPDX-License-Identifier: GPL-3.0-only
"""Owned aggregate transaction primitives. No import-time or standalone activation.

The admitted host/layout backend is intentionally separate from this engine.
Every backend method must be idempotent for recovery and return exactly True.
`verify` checks the intended browser/TLS/media layout during install, and the
original CPU setup during recovery. No backend is selected from user input.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

INSTALL=('admit','arm_recovery','restore_cpu','stop','publish','reload','start','verify','prefer_gpu')
RESTORE=('admit','arm_recovery','restore_cpu','stop','rollback','reload','start','verify','disarm_recovery')
METHODS=set(INSTALL+RESTORE)
PHASES=('new','intent','armed','cpu','stopped','mapped','loaded','started','verified','accepted')
TARGETS={
    'video':Path('/etc/systemd/system/tesla-waydroid-video-preview.service.d/70-droidmate-aggregate.conf'),
    'gateway':Path('/etc/systemd/system/tesla-waydroid-gateway-preview.service.d/70-droidmate-aggregate.conf'),
    'recovery':Path('/etc/systemd/system/droidmate-aggregate-recovery.service'),
    'installer':Path('/etc/systemd/system/droidmate-aggregate-install.service'),
    'preference':Path('/etc/systemd/system/droidmate-gpu-default.service.d/70-droidmate-aggregate.conf'),
    'preference-restore':Path('/etc/systemd/system/droidmate-gpu-default.service.d/75-droidmate-aggregate-restore.conf'),
}
BOOT_LINK=Path('/etc/systemd/system/multi-user.target.wants/droidmate-aggregate-recovery.service')
RECOVERY_UNIT='/etc/systemd/system/droidmate-aggregate-recovery.service'
LIMIT=32768

def sha(raw):return hashlib.sha256(raw).hexdigest()

def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('ascii')

def pairs(rows):
    out={}
    for name,value in rows:
        if name in out:raise ValueError('Duplicate journal key')
        out[name]=value
    return out

def document(raw):
    value=json.loads(raw,object_pairs_hook=pairs)
    if encode(value)!=raw:raise ValueError('Canonical finite journal required')
    return value

def transition(before,after):
    allowed={**{a:{b,'recovering'} for a,b in zip(PHASES,PHASES[1:])},
             'accepted':{'recovering'},'recovering':{'restored'},'restored':set()}
    if before not in allowed or after not in allowed[before]:raise ValueError('Closed durable phase transition required')

def transact(journal,backend,check,action):
    if action not in ('install','recover','undo'):raise ValueError('Closed transaction action required')
    def boundary():
        if check() is not True:raise ValueError('Original admitted source boundary lost')
    boundary();phase=journal.state();boundary()
    if phase=='restored' or phase=='accepted' and action!='undo' or phase=='new' and action!='install':
        if action=='install' and phase=='restored':raise ValueError('Restored operation cannot be replayed')
        return {'phase':phase,'servicesChanged':False}
    if action=='install' and phase!='new':raise ValueError('Incomplete install requires recovery, not replay')
    def call(name):
        boundary()
        if getattr(backend,name)() is not True:raise ValueError('Admitted backend refused '+name)
        boundary()
    # The first boundary above is the before-admit boundary.
    if backend.admit() is not True:raise ValueError('Original runtime admission refused')
    boundary()
    if action=='install':
        journal.append('intent');boundary()
        for method,next_phase in zip(INSTALL[1:],PHASES[2:]):
            call(method);journal.append(next_phase);boundary()
    else:
        if phase!='recovering':journal.append('recovering');boundary()
        for method in RESTORE[1:]:call(method)
        journal.append('restored');boundary()
    result={'phase':journal.state(),'servicesChanged':True};boundary();return result

def generation(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def directory_id(s):return generation(s)[:5]

def inode(s):return generation(s)[:5]

def publish_new(directory,pending,name):
    # Atomic no-replace rename has no hard-link/unlink crash window. An
    # interrupted private pending file is retained, never adopted as an event.
    import ctypes
    libc=ctypes.CDLL(None,use_errno=True)
    rename=libc.renameat2
    rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    rename.restype=ctypes.c_int
    if rename(directory,os.fsencode(pending),directory,os.fsencode(name),1):
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))

class Journal:
    """One root-private immutable history, held under a nonblocking kernel lock."""
    def __init__(self,root,identity,*,create=False):
        if os.name!='posix' or os.geteuid()!=0:raise ValueError('Root Linux journal required')
        if (type(identity) is not dict or set(identity)!={'packageSha256','layout'}
                or type(identity['packageSha256']) is not str or not re.fullmatch('[a-f0-9]{64}',identity['packageSha256'])
                or identity['layout'] not in ('portable','legacy')):raise ValueError('Closed aggregate identity required')
        self.root=Path(root);self.dirs={};self.files={};self.closed=False
        self.fd=None
        try:
            parent=self.directory(self.root.parent)
            if create:
                os.mkdir(self.root.name,mode=0o700,dir_fd=parent);os.fsync(parent)
            self.fd=self.directory(self.root)
            if stat.S_IMODE(os.fstat(self.fd).st_mode)!=0o700:raise ValueError('Private original journal required')
            import fcntl
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            if create:
                self.intent={'schema':1,'identity':identity,'rootIdentity':list(directory_id(os.fstat(self.fd))),
                             'transactionToken':secrets.token_hex(16)}
                self.write('intent.json',self.intent)
            else:
                self.intent=self.read('intent.json')
                if (type(self.intent) is not dict or set(self.intent)!={'schema','identity','rootIdentity','transactionToken'}
                        or type(self.intent['schema']) is not int or self.intent['schema']!=1 or self.intent['identity']!=identity
                        or self.intent['rootIdentity']!=list(directory_id(os.fstat(self.fd)))
                        or type(self.intent['transactionToken']) is not str
                        or not re.fullmatch('[a-f0-9]{32}',self.intent['transactionToken'])):
                    raise ValueError('Original immutable transaction identity required')
            self.state()
        except BaseException:self.close();raise

    def directory(self,path):
        p=Path(path)
        if not p.is_absolute() or '..' in p.parts or str(p).startswith('//'):raise ValueError('Canonical protected path required')
        if p in self.dirs:return self.dirs[p][0]
        parent=None if p==Path('/') else self.directory(p.parent)
        fd=os.open('/' if parent is None else p.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode&0o022:
            os.close(fd);raise ValueError('Root-owned protected ancestry required')
        self.dirs[p]=(fd,directory_id(s));return fd

    def guard(self):
        if self.closed:raise ValueError('Closed original journal')
        try:
            for p,(fd,identity) in self.dirs.items():
                if directory_id(os.fstat(fd))!=identity or directory_id(p.lstat())!=identity:raise ValueError('Original directory replaced')
            for name,(fd,identity,raw) in self.files.items():
                if (generation(os.fstat(fd))!=identity or generation((self.root/name).lstat())!=identity
                        or os.pread(fd,LIMIT+1,0)!=raw):raise ValueError('Original journal record replaced')
        except OSError as error:raise ValueError('Original journal generation unavailable') from error
        return True

    def read(self,name):
        if not re.fullmatch(r'(intent|baseline|services|map-(gateway|video|recovery|preference|preference-restore|recovery-link|installer)|[0-9]{8})\.json',name):raise ValueError('Closed journal record name')
        self.guard()
        if name not in self.files:
            fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=self.fd)
            try:
                s=os.fstat(fd)
                if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode))!=(0,0,1,0o600)
                        or not 0<s.st_size<=LIMIT):raise ValueError('Private bounded single-link journal record required')
                raw=os.pread(fd,LIMIT+1,0)
                if len(raw)!=s.st_size or generation(os.fstat(fd))!=generation(s):raise ValueError('Stable original record required')
                self.files[name]=(fd,generation(s),raw);fd=None
            finally:
                if fd is not None:os.close(fd)
        self.guard();return document(self.files[name][2])

    def write(self,name,value):
        if not re.fullmatch(r'(intent|baseline|services|map-(gateway|video|recovery|preference|preference-restore|recovery-link|installer)|[0-9]{8})\.json',name):raise ValueError('Closed journal record name')
        raw=encode(value)
        if not 0<len(raw)<=LIMIT:raise ValueError('Bounded durable record required')
        self.guard();pending='.pending-'+self.intent['transactionToken']+'-'+secrets.token_hex(16)
        fd=os.open(pending,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        try:
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(raw);stream.flush();os.fsync(fd)
            expected=generation(os.fstat(fd));self.guard()
            if generation(os.stat(pending,dir_fd=self.fd,follow_symlinks=False))!=expected:
                raise ValueError('Original pending record inode required')
            publish_new(self.fd,pending,name);os.fsync(self.fd)
            published=generation(os.fstat(fd))
            if (published[:-1]!=expected[:-1] or generation(os.stat(name,dir_fd=self.fd,follow_symlinks=False))!=published
                    or os.pread(fd,LIMIT+1,0)!=raw):raise ValueError('Original created record publication required')
            if self.read(name)!=value or self.files[name][1]!=published:
                raise ValueError('Original durable publication generation required')
            self.guard()
        finally:os.close(fd)

    def history(self):
        self.guard();names=set(os.listdir(self.fd))
        pending={n for n in names if re.fullmatch(r'\.pending-'+self.intent['transactionToken']+'-[a-f0-9]{32}',n)}
        if len(pending)>128:raise ValueError('Bounded interrupted journal namespace required')
        for n in pending:
            fd=os.open(n,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=self.fd)
            try:
                s=os.fstat(fd)
                if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode))!=(0,0,1,0o600)
                        or not 0<=s.st_size<=LIMIT):raise ValueError('Private interrupted write preserved')
            finally:os.close(fd)
        names-=pending
        if any(not re.fullmatch(r'(intent|baseline|services|map-(gateway|video|recovery|preference|preference-restore|recovery-link|installer)|[0-9]{8})\.json',n) for n in names):
            raise ValueError('Foreign or interrupted journal files preserved')
        if 'intent.json' not in names:raise ValueError('Original intent required')
        for n in sorted(names):self.read(n)
        events=sorted(n for n in names if re.fullmatch(r'[0-9]{8}\.json',n));phase='new';previous=sha(encode(self.intent))
        for i,n in enumerate(events,1):
            if n!=f'{i:08}.json':raise ValueError('Contiguous immutable history required')
            row=self.read(n)
            if (type(row) is not dict or set(row)!={'schema','sequence','previousSha256','phase'}
                    or type(row['schema']) is not int or row['schema']!=1 or type(row['sequence']) is not int or row['sequence']!=i
                    or row['previousSha256']!=previous):raise ValueError('Original hash-linked history required')
            transition(phase,row['phase']);phase=row['phase'];previous=sha(encode(row))
        # A prior process may have died after rename but before directory sync.
        # Do not use even a valid terminal tail until durability is established.
        os.fsync(self.fd);self.guard()
        return phase,len(events),previous

    def state(self):return self.history()[0]

    def append(self,phase):
        before,count,previous=self.history();transition(before,phase)
        self.write(f'{count+1:08}.json',{'schema':1,'sequence':count+1,'previousSha256':previous,'phase':phase})
        if self.state()!=phase:raise ValueError('Durable phase not committed')

    def close(self):
        if self.closed:return
        self.closed=True
        for fd,_identity,_raw in self.files.values():os.close(fd)
        for fd,_identity in reversed(list(self.dirs.values())):os.close(fd)
        self.files={};self.dirs={}

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class OwnedMapping:
    """Publish/undo a public drop-in only through a durable retained inode anchor."""
    def __init__(self,journal,key,raw):
        if key not in TARGETS or type(raw) is not bytes or not 0<len(raw)<=LIMIT:raise ValueError('Fixed public mapping required')
        self.j=journal;self.key=key;self.raw=raw;self.target=TARGETS[key]
        self.parent=journal.directory(self.target.parent)
        self.anchor=self.target.parent/('.droidmate-rc17-'+journal.intent['transactionToken']+'-'+key)
        self.retired=self.anchor.with_name(self.anchor.name+'.retired')
        self.name='map-'+key+'.json'

    def prepare(self):
        self.j.state()
        if os.path.lexists(self.j.root/self.name):self.verify();return
        if os.path.lexists(self.target):raise ValueError('Foreign target preserved')
        self.j.guard()
        try:fd=os.open(self.anchor.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=self.parent)
        except OSError as error:raise ValueError('Unknown anchor preserved') from error
        try:
            os.fchmod(fd,0o644)
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(self.raw);stream.flush();os.fsync(fd)
            identity=list(inode(os.fstat(fd)));os.fsync(self.parent)
        finally:os.close(fd)
        self.j.write(self.name,{'schema':1,'target':str(self.target),'anchor':str(self.anchor),'retired':str(self.retired),
                                'inode':identity,'bytes':len(self.raw),'sha256':sha(self.raw)})
        self.verify()

    def verify(self):
        self.j.guard();row=self.j.read(self.name)
        required={'schema':1,'target':str(self.target),'anchor':str(self.anchor),'retired':str(self.retired),
                  'inode':row.get('inode') if type(row) is dict else None,'bytes':len(self.raw),'sha256':sha(self.raw)}
        if row!=required or type(row['inode']) is not list or len(row['inode'])!=5:raise ValueError('Exact owned mapping record required')
        present=os.path.lexists(self.target);retired=os.path.lexists(self.retired)
        if present and retired:raise ValueError('Conflicting live and retired mappings preserved')
        for p in (self.anchor,*([self.target] if present else []),*([self.retired] if retired else [])):
            try:fd=os.open(p.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=self.parent)
            except OSError as error:raise ValueError('Original mapping anchor unavailable') from error
            try:
                s=os.fstat(fd);data=os.pread(fd,LIMIT+1,0)
                if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,stat.S_IMODE(s.st_mode))!=(0,0,0o644)
                        or list(inode(s))!=row['inode'] or s.st_nlink!=(2 if present or retired else 1)
                        or data!=self.raw or generation(os.fstat(fd))!=generation(s)
                        or generation(p.lstat())!=generation(s)):
                    raise ValueError('Original retained mapping inode and bytes required')
            finally:os.close(fd)
        self.j.guard();return present

    def publish(self):
        if self.verify():return
        if os.path.lexists(self.retired):raise ValueError('Retired owned mapping cannot be replayed')
        os.link(self.anchor.name,self.target.name,src_dir_fd=self.parent,dst_dir_fd=self.parent,follow_symlinks=False)
        os.fsync(self.parent)
        if self.verify() is not True:raise ValueError('Original mapping publication required')

    def remove(self):
        if not self.verify():return
        # Rename into a fresh retained quarantine, then verify the moved inode.
        # Never unlink the target after an earlier ownership check: another
        # privileged installer could replace it in that interval. Retirements
        # remain as evidence and are safe to resume after process death.
        publish_new(self.parent,self.target.name,self.retired.name);os.fsync(self.parent)
        try:
            if self.verify():raise ValueError('Owned mapping retirement incomplete')
        except (OSError,ValueError) as error:
            # Preserve any unexpected object. Restore its old name only if that
            # name is still absent, without overwriting a concurrent replacement.
            try:publish_new(self.parent,self.retired.name,self.target.name)
            except OSError:pass
            os.fsync(self.parent)
            raise ValueError('Unexpected retired mapping preserved') from error



class OwnedBootLink(OwnedMapping):
    """Enable exactly the owned recovery service without modifying RUNTIME.

    Unit registration cannot use a RUNTIME drop-in: that would change its sealed
    original dependency graph. The separately owned enable link survives reboot
    and is retired with the same no-replace/quarantine protocol as public maps.
    """
    def __init__(self,journal):
        self.j=journal;self.key='recovery-link';self.target=BOOT_LINK
        self.parent=journal.directory(self.target.parent)
        self.anchor=self.target.parent/('.droidmate-rc17-'+journal.intent['transactionToken']+'-'+self.key)
        self.retired=self.anchor.with_name(self.anchor.name+'.retired');self.name='map-recovery-link.json'

    def prepare(self):
        self.j.state()
        if os.path.lexists(self.j.root/self.name):self.verify();return
        if os.path.lexists(self.target):raise ValueError('Foreign boot registration preserved')
        self.j.guard();os.symlink(RECOVERY_UNIT,self.anchor.name,dir_fd=self.parent);os.fsync(self.parent)
        handle=os.open(self.anchor.name,os.O_PATH|os.O_NOFOLLOW,dir_fd=self.parent)
        try:
            info=os.fstat(handle)
            if not stat.S_ISLNK(info.st_mode) or generation(info)!=generation(self.anchor.lstat()) or os.readlink(self.anchor)!=RECOVERY_UNIT:
                raise ValueError('Original newly created enable link required')
            row={'schema':1,'target':str(self.target),'anchor':str(self.anchor),'retired':str(self.retired),
                 'inode':list(inode(info)),'linkTarget':RECOVERY_UNIT}
            self.j.write(self.name,row)
            if list(inode(os.fstat(handle)))!=row['inode']:raise ValueError('Original enable-link inode required')
        finally:os.close(handle)
        self.verify()

    def verify(self):
        self.j.guard();row=self.j.read(self.name)
        required={'schema':1,'target':str(self.target),'anchor':str(self.anchor),'retired':str(self.retired),
                  'inode':row.get('inode') if type(row) is dict else None,'linkTarget':RECOVERY_UNIT}
        if row!=required or type(row['inode']) is not list or len(row['inode'])!=5:raise ValueError('Exact owned boot registration required')
        present=os.path.lexists(self.target);retired=os.path.lexists(self.retired)
        if present and retired:raise ValueError('Conflicting boot registration preserved')
        for path in (self.anchor,*([self.target] if present else []),*([self.retired] if retired else [])):
            handle=os.open(path.name,os.O_PATH|os.O_NOFOLLOW,dir_fd=self.parent)
            try:
                info=os.fstat(handle)
                if (not stat.S_ISLNK(info.st_mode) or (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(0,0,0o777)
                        or list(inode(info))!=row['inode'] or info.st_nlink!=(2 if present or retired else 1)
                        or os.readlink(path)!=RECOVERY_UNIT or generation(path.lstat())!=generation(info)):
                    raise ValueError('Original retained boot-link inode and target required')
            finally:os.close(handle)
        self.j.guard();return present


def public_inventory(files):
    """Public runtime/frontend bytes only; never a configured machine directory."""
    from pathlib import PurePosixPath
    if type(files) is not dict or not 1<=len(files)<=512:raise ValueError('Bounded closed public inventory required')
    total=0
    for name,raw in files.items():
        if (type(name) is not str or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}',name)
                or str(PurePosixPath(name))!=name or PurePosixPath(name).is_absolute()
                or name.split('/')[0] not in ('frontend','portable','legacy')
                or any(p in ('.','..') or p.startswith('.') and p not in
                       ('.package-lock.json','.github','.travis.yml','.auto-changelog','.gitattributes','.npmignore') for p in name.split('/'))
                or type(raw) is not bytes or not 0<len(raw)<=8*1024**2):
            raise ValueError('Canonical bounded public file required')
        total+=len(raw)
    if total>24*1024**2:raise ValueError('Bounded complete public tree required')
    names=set(files)
    if any(str(parent) in names for name in names for parent in PurePosixPath(name).parents):
        raise ValueError('Public file cannot also be a parent directory')
    return [{'path':n,'size':len(b),'sha256':sha(b)} for n,b in sorted(files.items())]


PUBLIC_PARENT=Path('/var/lib/droidmate-browser')

def public_parent(journal,path):
    path=Path(path)
    if path!=PUBLIC_PARENT:raise ValueError('Fixed public browser parent required')
    parent=journal.directory(path.parent)
    if os.path.lexists(path):
        fd=journal.directory(path)
        if not os.fstat(fd).st_mode&0o001:raise ValueError('Existing private public parent preserved')
        return True
    # Initialize an unpredictable private sibling before atomic no-replace
    # publication. Never chmod PUBLIC, even if another creator races us there.
    pending='.droidmate-public-parent-'+secrets.token_hex(16)
    os.mkdir(pending,mode=0o700,dir_fd=parent)
    original=os.stat(pending,dir_fd=parent,follow_symlinks=False)
    fd=os.open(pending,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try:
        info=os.fstat(fd)
        if directory_id(info)!=directory_id(original)or not stat.S_ISDIR(info.st_mode)or(info.st_uid,info.st_gid)!=(0,0)or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('Original private public-parent claim required')
        os.fchmod(fd,0o755);os.fsync(fd)
        if directory_id(os.stat(pending,dir_fd=parent,follow_symlinks=False))!=directory_id(os.fstat(fd)):
            raise ValueError('Private public-parent claim replaced')
        publish_new(parent,pending,path.name);os.fsync(parent)
        expected=directory_id(os.fstat(fd))
        if directory_id(path.lstat())!=expected or directory_id(os.fstat(journal.directory(path)))!=expected:
            raise ValueError('Published public parent changed')
    finally:os.close(fd)
    return True


class PublicTree:
    """Protected read-only public files with private immutable generation records."""
    def __init__(self,root,files,digest,*,create=False):
        from contextlib import ExitStack
        self.root=Path(root);self.files=files;self.digest=digest
        self.stack=ExitStack();self.dirs={};self.held={};self.closed=False
        try:
            parent=self.directory(self.root.parent)
            if create:
                os.mkdir(self.root.name,mode=0o755,dir_fd=parent)
                self.fd=self.directory(self.root,created=True)
                os.fsync(parent);self.guard()
            else:self.fd=self.directory(self.root)
            import fcntl
            # World-readable directory FDs can be opened and flocked by the
            # gateway user. Coordination belongs to an independently protected
            # private file, not that public directory inode.
            try:
                lock=os.open('coordination.lock',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW if create
                             else os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
            except OSError as error:raise ValueError('Original private coordination file unavailable') from error
            self.stack.callback(os.close,lock)
            if create:os.fchmod(lock,0o600)
            s=os.fstat(lock)
            if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode),s.st_size)!=(0,0,1,0o600,0)):
                raise ValueError('Original private coordination file required')
            self.held['coordination.lock']=(lock,generation(s),b'');self.guard()
            if create:os.fsync(lock);os.fsync(self.fd);self.guard()
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.intent={'schema':1,'kind':'droidmate-public-browser-tree','packageSha256':digest,
                         'rootIdentity':list(directory_id(os.fstat(self.fd))[:2]),'files':public_inventory(files)}
            if create:self.create()
            self.verify()
        except BaseException:self.close();raise

    def directory(self,path,*,created=False):
        p=Path(path)
        if not p.is_absolute() or str(p).startswith('//') or '..' in p.parts:raise ValueError('Protected public path required')
        if p in self.dirs:return self.dirs[p][0]
        parent=None if p==Path('/') else self.directory(p.parent)
        fd=os.open('/' if parent is None else p.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        self.stack.callback(os.close,fd);s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode&0o022:
            raise ValueError('Root-owned protected public ancestry required')
        if created:
            # Only the descriptor just acquired immediately after our exclusive
            # mkdir may be made public. Existing private ancestors are refused.
            os.fchmod(fd,0o755);s=os.fstat(fd)
        if not s.st_mode&0o001:raise ValueError('Publicly traversable protected ancestry required')
        self.dirs[p]=(fd,directory_id(s));return fd

    def guard(self):
        if self.closed:raise ValueError('Closed public tree')
        try:
            for p,(fd,identity) in self.dirs.items():
                if directory_id(os.fstat(fd))!=identity or directory_id(p.lstat())!=identity:
                    raise ValueError('Original public directory generation changed')
            for name,(fd,identity,raw) in self.held.items():
                if (generation(os.fstat(fd))!=identity or generation((self.root/name).lstat())!=identity
                        or os.pread(fd,8*1024**2+1,0)!=raw):raise ValueError('Original public file generation changed')
        except OSError as error:raise ValueError('Original public tree unavailable') from error

    def retain_read(self,name,mode):
        # Internal batch read: completion guards the WHOLE batch on both sides.
        if name not in self.held:
            p=self.root/name;parent=self.directory(p.parent)
            fd=os.open(p.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=parent)
            self.stack.callback(os.close,fd);s=os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode))!=(0,0,1,mode)
                    or not 0<s.st_size<=8*1024**2):raise ValueError('Exact public or private metadata file required')
            raw=os.pread(fd,8*1024**2+1,0)
            if len(raw)!=s.st_size:raise ValueError('Complete original public file required')
            self.held[name]=(fd,generation(s),raw)
        return self.held[name][2]

    def read(self,name,mode):
        self.guard();raw=self.retain_read(name,mode);self.guard();return raw

    def record(self,name,value):
        raw=encode(value);self.guard()
        if name not in ('intent.json','complete.json') or len(raw)>262144:raise ValueError('Bounded public generation receipt required')
        pending='.'+name+'.pending'
        fd=os.open(pending,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        try:
            with os.fdopen(fd,'wb',closefd=False) as f:f.write(raw);f.flush();os.fsync(fd)
            expected=generation(os.fstat(fd));self.guard()
            if generation(os.stat(pending,dir_fd=self.fd,follow_symlinks=False))!=expected:
                raise ValueError('Original public receipt pending inode required')
            publish_new(self.fd,pending,name);os.fsync(self.fd)
            current=generation(os.fstat(fd))
            if (current[:-1]!=expected[:-1] or generation(os.stat(name,dir_fd=self.fd,follow_symlinks=False))!=current
                    or os.pread(fd,262145,0)!=raw or self.read(name,0o600)!=raw or self.held[name][1]!=current):
                raise ValueError('Original public receipt publication required')
        finally:os.close(fd)

    def create(self):
        from pathlib import PurePosixPath
        self.record('intent.json',self.intent)
        for name,raw in sorted(self.files.items()):
            p=self.root/name;current=self.root
            for part in PurePosixPath(name).parent.parts:
                parent=self.directory(current);current/=part
                if current not in self.dirs:
                    self.guard();os.mkdir(part,mode=0o755,dir_fd=parent)
                    self.directory(current,created=True);os.fsync(parent);self.guard()
            parent=self.directory(p.parent);self.guard()
            fd=os.open(p.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444,dir_fd=parent)
            try:
                os.fchmod(fd,0o444)
                with os.fdopen(fd,'wb',closefd=False) as f:f.write(raw);f.flush();os.fsync(fd)
                expected=generation(os.fstat(fd));os.fsync(parent)
                if self.read(name,0o444)!=raw or self.held[name][1]!=expected:raise ValueError('Original created public file required')
            finally:os.close(fd)
        for p in sorted((p for p in self.dirs if p==self.root or self.root in p.parents),key=lambda p:len(p.parts),reverse=True):
            fd,original=self.dirs[p];self.guard();os.fchmod(fd,0o555);os.fsync(fd)
            s=os.fstat(fd)
            if directory_id(s)[:2]!=original[:2] or stat.S_IMODE(s.st_mode)!=0o555:raise ValueError('Original frozen public directory required')
            self.dirs[p]=(fd,directory_id(s))
        self.record('complete.json',self.completion())

    def completion(self):
        self.guard();actual_files=set();actual_dirs=set()
        for current,children,names in os.walk(self.root,followlinks=False):
            p=Path(current);self.directory(p)
            if stat.S_IMODE(os.fstat(self.dirs[p][0]).st_mode)!=0o555:raise ValueError('Read-only public directory required')
            actual_dirs.add(p.relative_to(self.root).as_posix())
            for child in children:self.directory(p/child)
            actual_files.update((p/n).relative_to(self.root).as_posix() for n in names)
        from pathlib import PurePosixPath
        expected_dirs={'.',*(parent.as_posix() for n in self.files for parent in PurePosixPath(n).parents)}
        expected_files={*self.files,'intent.json','coordination.lock'}
        if os.path.lexists(self.root/'complete.json'):expected_files.add('complete.json')
        if actual_dirs!=expected_dirs or actual_files!=expected_files:raise ValueError('Closed original public namespace required')
        for n,b in self.files.items():
            if self.retain_read(n,0o444)!=b:raise ValueError('Original authenticated public bytes required')
        if self.retain_read('intent.json',0o600)!=encode(self.intent):raise ValueError('Original public tree intent required')
        # Check the closed namespace again after batch retention, then every
        # retained file's CURRENT bytes/generation. No cached success result.
        final_dirs=set();final_files=set()
        for current,children,names in os.walk(self.root,followlinks=False):
            p=Path(current);final_dirs.add(p.relative_to(self.root).as_posix())
            final_files.update((p/n).relative_to(self.root).as_posix()for n in names)
        if final_dirs!=expected_dirs or final_files!=expected_files:raise ValueError('Closed public namespace changed during verification')
        self.guard()
        return {'schema':1,'intentSha256':sha(encode(self.intent)),
                'generations':{n:list(self.held[n][1]) for n in sorted({*self.files,'intent.json','coordination.lock'})},
                'directories':{n:list(self.dirs[self.root/n][1]) for n in sorted(expected_dirs)}}

    def verify(self):
        try:
            receipt=document(self.read('complete.json',0o600))
            if receipt!=self.completion():raise ValueError('Original completed public generations required')
            os.fsync(self.fd);self.guard()
        except OSError as error:raise ValueError('Incomplete public tree preserved') from error

    def close(self):
        if not self.closed:self.closed=True;self.stack.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def stage_public_tree(parent,digest,files,check,*,create=False):
    """Backend-only public staging. Existing and partial trees are never repaired."""
    if not callable(check) or check() is not True:raise ValueError('Retained source admission required')
    if type(digest) is not str or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Content-addressed public tree required')
    files=dict(files);public_inventory(files)
    if os.name!='posix' or os.geteuid()!=0:raise ValueError('Root Linux public staging required')
    if check() is not True:raise ValueError('Original source admission lost before staging')
    root=Path(parent)/digest
    if type(create) is not bool or not create and not os.path.lexists(root):raise ValueError('Explicit fresh creation or original existing tree required')
    with PublicTree(root,files,digest,create=create) as tree:
        if check() is not True:raise ValueError('Original source admission lost after staging')
        tree.verify()
        if check() is not True:raise ValueError('Original source admission lost during final verification')
        return root


def preference_inhibit(journal_root):
    path=Path(journal_root).as_posix()
    if not re.fullmatch('/var/lib/droidmate-aggregate/[a-f0-9]{64}',path):raise ValueError('Fixed aggregate intent condition required')
    return ('[Unit]\nConditionPathExists=!'+path+'/intent.json\n').encode('ascii')


def preference_settled(fields):
    if (type(fields) is not dict or set(fields)!={'Job','MainPID','ControlPID','ActiveState','SubState'}
            or any(type(v) is not str for v in fields.values())
            or not re.fullmatch('[0-9]{1,10}',fields['MainPID'])
            or not re.fullmatch('[0-9]{1,10}',fields['ControlPID'])
            or not re.fullmatch('[0-9]{0,10}',fields['Job'])):
        raise ValueError('Exact bounded preference job observation required')
    states={('inactive','dead'),('failed','failed'),('active','exited'),
            ('activating','start'),('activating','start-pre'),('activating','start-post'),
            ('deactivating','stop'),('deactivating','stop-post'),('deactivating','stop-sigterm'),
            ('deactivating','stop-sigkill'),('deactivating','final-sigterm'),('deactivating','final-sigkill')}
    state=(fields['ActiveState'],fields['SubState'])
    if state not in states:raise ValueError('Original oneshot preference state required')
    return not fields['Job'] and fields['MainPID']=='0' and fields['ControlPID']=='0' and state in {
        ('inactive','dead'),('failed','failed'),('active','exited')}


def inhibit_preference(mapping,backend,check,*,deadline_seconds=300):
    """Install before effects, settle old jobs, never kill/replay the preference.

    `mapping` is an exact owned install/recovery inhibition, and backend
    rechecks the original enrolled unit/receipt while observing its dynamic job.
    A condition alone cannot stop a job which already passed its condition check.
    """
    import math
    if (type(deadline_seconds) not in (int,float) or not math.isfinite(deadline_seconds)
            or not 0<deadline_seconds<=360 or not callable(check)):
        raise ValueError('Bounded update-only settling deadline required')
    def source():
        if check() is not True:raise ValueError('Original aggregate source admission lost')
    def boundary():
        source()
        if mapping.verify() is not True:raise ValueError('Original preference inhibition mapping required')
        source()
    source();mapping.prepare();source();mapping.publish();boundary()
    if backend.reload() is not True:raise ValueError('Preference inhibition reload refused')
    boundary();last=None
    def clock():
        nonlocal last
        now=backend.monotonic()
        if type(now) not in (int,float) or not math.isfinite(now) or now<0 or last is not None and now<last:
            raise ValueError('Finite nonregressing monotonic preference observation required')
        last=now;return now
    start=clock();previous=None
    def deadline():
        if clock()-start>=deadline_seconds:raise TimeoutError('Existing preference job did not settle; inhibition preserved')
    while True:
        boundary();deadline()
        fields=backend.observe_preference();boundary()
        deadline()
        if preference_settled(fields) and fields==previous:
            boundary();deadline();return {'preferenceInhibited':True,'existingJobSettled':True}
        previous=dict(fields) if preference_settled(fields) else None
        backend.pause()


def main(args,ctx):
    """Called only by the authenticated capsule or retained enrolled entry."""
    import types
    if type(ctx) is not dict or not callable(ctx.get('check')) or ctx['check']() is not True:
        raise ValueError('Original signed aggregate admission required')
    path=ctx['root']/'source/tools/aggregate-runtime.py';raw=path.read_bytes()
    if ctx['check']() is not True:raise ValueError('Original backend source admission lost')
    backend=types.ModuleType('authenticated_aggregate_runtime');backend.__file__=str(path)
    exec(compile(raw,str(path),'exec'),backend.__dict__)
    if ctx['check']() is not True:raise ValueError('Original backend source admission changed')
    engine=types.SimpleNamespace(**globals())
    return backend.main(args,ctx,engine)
