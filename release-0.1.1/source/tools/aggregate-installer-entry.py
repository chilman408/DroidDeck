# SPDX-License-Identifier: GPL-3.0-only
"""Fixed-key aggregate entry. Authenticate every byte before importing payload code."""
from contextlib import ExitStack
import hashlib,io,json,os,re,stat,subprocess,sys,tarfile,tempfile,types,zipfile
from pathlib import Path,PurePosixPath

PUBLIC_KEY='302a300506032b65700321009d63ba428047b862b5cd1fb5ac994814d985b9a015ecf5805de1261d7825e69c'
KEY_PIN='4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f'
CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
CONTRACT_PIN='40baefbcecf7d9b95b4457d283bd6951bbe90fc8da4926eb680778ce4721d394'
STAGER_PIN='48ae63a3deb49022ec7065c0a17ea836712eef97080952ba22aed96948bcd7c5'
COMPONENTS=(33423360,'30e86c1efebbf1ed4203190f8c5e6f4f9a88b7edcf40ccf2a3e785cfb280fade')
FRONTEND_PIN='754e5d2da9a03b38d8c5943c556109e50a5f00441c777bf62ce2306f6a2e5826'
PREFIX='droidmate-rc18-release/'
MANIFEST=PREFIX+'manifest.json'
ENTRY='tools/aggregate-installer-entry.py'
ENVELOPE={'__main__.py','release.tar','statement.json','signature.bin'}
MAX_ARCHIVE=48*1024**2
MAX_PART=16*1024**2
ENV={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'}

def sha(b):return hashlib.sha256(b).hexdigest()
def encode(v):return (json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode('ascii')
def unique(rows):
    out={}
    for k,v in rows:
        if k in out:raise ValueError('Duplicate aggregate metadata')
        out[k]=v
    return out
def nonfinite(v):raise ValueError('Nonfinite aggregate metadata')
def document(raw):
    value=json.loads(raw,object_pairs_hook=unique,parse_constant=nonfinite)
    if encode(value)!=raw:raise ValueError('Canonical aggregate metadata required')
    return value
def records(files):return [{'path':n,'size':len(b),'sha256':sha(b)} for n,b in sorted(files.items())]
def path_name(n):
    p=PurePosixPath(n)
    if (type(n) is not str or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}',n) or p.is_absolute() or str(p)!=n
            or any(s in ('.','..','.git','.ssh','.config','.env') for s in p.parts)):
        raise ValueError('Canonical public aggregate path required')
def canonical_tar(files):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w:',format=tarfile.USTAR_FORMAT) as t:
        for n,b in sorted(files.items()):
            m=tarfile.TarInfo(n);m.mode=0o644;m.size=len(b);t.addfile(m,io.BytesIO(b))
    return out.getvalue()
def unpack(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ARCHIVE:raise ValueError('Bounded aggregate required')
    files={};total=0
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as t:
        for m in t:
            path_name(m.name)
            allowed=m.name in (MANIFEST,PREFIX+'components.tar') or m.name.startswith((PREFIX+'source/',PREFIX+'frontend/'))
            if (not allowed or m.name in files or m.type!=tarfile.REGTYPE or m.pax_headers or m.sparse is not None
                    or m.linkname or m.mode!=0o644 or m.uid or m.gid or m.uname or m.gname or m.mtime
                    or not 0<m.size<=40*1024**2 or len(files)>=512):raise ValueError('Closed regular aggregate required')
            total+=m.size
            if total>MAX_ARCHIVE:raise ValueError('Expanded aggregate bound exceeded')
            b=t.extractfile(m).read(40*1024**2+1)
            if len(b)!=m.size:raise ValueError('Complete aggregate member required')
            files[m.name]=b
    if canonical_tar(files)!=raw:raise ValueError('Canonical complete aggregate bytes required')
    return files
def verify_signature(statement,signature):
    if not 0<len(statement)<=8192 or len(signature)!=64:raise ValueError('Bounded Ed25519 authorization required')
    key=bytes.fromhex(PUBLIC_KEY)
    if len(key)!=44 or sha(key)!=KEY_PIN:raise ValueError('Original pilot public key required')
    with ExitStack() as stack:
        handles=[]
        for raw in (key,statement,signature):
            f=stack.enter_context(tempfile.TemporaryFile());f.write(raw);f.flush();f.seek(0);handles.append(f.fileno())
        names=['/proc/self/fd/'+str(fd) for fd in handles]
        p=subprocess.run(['/usr/bin/openssl','pkeyutl','-verify','-pubin','-keyform','DER','-inkey',names[0],
            '-rawin','-in',names[1],'-sigfile',names[2]],pass_fds=tuple(handles),stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd='/',env=ENV,timeout=15)
        if p.returncode:raise ValueError('Aggregate signature refused')
def authenticate(entries,*,verify=verify_signature):
    if type(entries) is not dict or set(entries)!=ENVELOPE or any(type(b) is not bytes for b in entries.values()):raise ValueError('Closed aggregate envelope required')
    statement,signature=entries['statement.json'],entries['signature.bin']
    if not 0<len(statement)<=8192 or len(signature)!=64:raise ValueError('Bounded authorization required')
    verify(statement,signature)  # No JSON/tar/code is parsed before this succeeds.
    value=document(statement)
    keys={'schema','kind','channel','aggregateRelease','coreRuntimeVersion','compatibleReleaseSha256','activationAllowed',
          'keySha256','packageSha256','manifestSha256','sourceRevision','sourceInventorySha256','contractSha256'}
    if (type(value) is not dict or set(value)!=keys or type(value['schema']) is not int or value['schema']!=1
            or value['kind']!='droidmate-aggregate-release-authorization' or value['channel']!='pilot'
            or value['aggregateRelease']!='RC18' or value['coreRuntimeVersion']!='0.1.1-rc.20'
            or value['compatibleReleaseSha256']!=CORE or value['activationAllowed'] is not True
            or value['keySha256']!=KEY_PIN or value['contractSha256']!=CONTRACT_PIN
            or not re.fullmatch('[a-f0-9]{40}',value['sourceRevision'])
            or any(type(value[k]) is not str or not re.fullmatch('[a-f0-9]{64}',value[k]) for k in
                   ('packageSha256','manifestSha256','sourceInventorySha256'))):raise ValueError('Exact separate aggregate domain required')
    raw=entries['release.tar']
    if sha(raw)!=value['packageSha256']:raise ValueError('Authenticated complete aggregate required')
    files=unpack(raw)
    if MANIFEST not in files or sha(files[MANIFEST])!=value['manifestSha256']:raise ValueError('Authenticated manifest required')
    meta=document(files[MANIFEST]);others={n:b for n,b in files.items() if n!=MANIFEST}
    meta_keys={'schema','kind','aggregateRelease','coreRuntimeVersion','compatibleReleaseSha256','sourceRevision',
               'componentsSha256','frontendManifestSha256','sourceInventorySha256','contract','files'}
    if (type(meta) is not dict or set(meta)!=meta_keys or type(meta['schema']) is not int or meta['schema']!=1
            or meta['kind']!='droidmate-rc18-aggregate-release' or encode(meta['files'])!=encode(records(others))
            or any(meta[k]!=value[k] for k in ('aggregateRelease','coreRuntimeVersion','compatibleReleaseSha256','sourceRevision','sourceInventorySha256'))
            or sha(encode(meta['contract']))!=CONTRACT_PIN):raise ValueError('Exact aggregate manifest and every member required')
    sources={n[len(PREFIX+'source/'):]:b for n,b in files.items() if n.startswith(PREFIX+'source/')}
    component=files.get(PREFIX+'components.tar',b'')
    frontend=files.get(PREFIX+'frontend/bundle-manifest.json',b'')
    if ((len(component),sha(component))!=COMPONENTS or meta['componentsSha256']!=sha(component)
            or sha(frontend)!=FRONTEND_PIN or meta['frontendManifestSha256']!=FRONTEND_PIN
            or sha(encode(records(sources)))!=value['sourceInventorySha256']
            or sources.get(ENTRY)!=entries['__main__.py']):raise ValueError('Original assets and exact signed entry required')
    return value,files
def canonical_zip(entries):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED,allowZip64=False) as z:
        for n,b in sorted(entries.items()):
            row=zipfile.ZipInfo(n,(1980,1,1,0,0,0));row.create_system=3;row.external_attr=0o100644<<16;row.compress_type=zipfile.ZIP_STORED
            z.writestr(row,b)
    return output.getvalue()
def zip_entries(raw):
    if type(raw) is not bytes or not 0<len(raw)<=MAX_ARCHIVE+2*1024**2:raise ValueError('Bounded installer capsule required')
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        rows=z.infolist()
        if len(rows)!=4 or {r.filename for r in rows}!=ENVELOPE:raise ValueError('Closed installer capsule required')
        limits={'__main__.py':1048576,'release.tar':MAX_ARCHIVE,'statement.json':8192,'signature.bin':64}
        if any(r.compress_type!=zipfile.ZIP_STORED or r.flag_bits&1 or r.external_attr>>16!=0o100644 or not 0<r.file_size<=limits[r.filename] for r in rows):
            raise ValueError('Regular bounded uncompressed capsule required')
        entries={r.filename:z.read(r) for r in rows}
    if canonical_zip(entries)!=raw:raise ValueError('Exact canonical capsule required')
    return entries
def read_bounded(path,maximum):
    path=Path(path);before=path.lstat()
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
    def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,getattr(s,'st_birthtime_ns',None) if os.name=='nt' else s.st_ctime_ns)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<=info.st_size<=maximum or identity(info)!=identity(before):raise ValueError('Bounded original regular input required')
        with os.fdopen(os.dup(fd),'rb') as f:raw=f.read(maximum+1)
        if len(raw)!=info.st_size or identity(info)!=identity(os.fstat(fd)) or identity(info)!=identity(path.lstat()):raise ValueError('Original input generation required')
        return raw
    finally:os.close(fd)
def package_entries(path):return zip_entries(read_bounded(path,MAX_ARCHIVE+2*1024**2))
def archive_parts(raw):
    if type(raw) is not bytes or not 0<len(raw)<=3*MAX_PART:raise ValueError('Three bounded archive parts required')
    return {'release.part-'+str(i):raw[i*MAX_PART:(i+1)*MAX_PART] for i in range(3)}
def compiled(name,raw,filename):
    module=types.ModuleType(name);module.__file__=str(filename);exec(compile(raw,str(filename),'exec'),module.__dict__);return module
def stager(raw,filename):
    if sha(raw)!=STAGER_PIN:raise ValueError('Independently frozen immutable materializer required')
    return compiled('trusted_aggregate_materializer',raw,filename)
def directory_id(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)
class RetainedInputs:
    """Validate bytes once, then retain their exact kernel generations.

    Source membership, ownership, descriptors and ctime remain checked at every
    boundary. This never repairs/reopens a missing slot or caches a True result.
    """
    def __init__(self,original):
        self.original=original;self.stack=ExitStack();self.dirs={};self.files={};self.namespace={};self.closed=False
    def directory(self,path):
        if self.closed:raise ValueError('Closed retained enrollment')
        path=Path(path)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Protected canonical source ancestry required')
        if path in self.dirs:return self.dirs[path][2]
        parent,name=(None,'/') if path==Path('/') else (self.directory(path.parent),path.name)
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent);self.stack.callback(os.close,fd)
        state=os.fstat(fd)
        if not stat.S_ISDIR(state.st_mode) or (state.st_uid,state.st_gid)!=(0,0) or state.st_mode&0o022:
            raise ValueError('Original protected source ancestry required')
        self.dirs[path]=(parent,name,fd,directory_id(state));return fd
    def read(self,path,maximum=32*1024**2):
        if self.closed:raise ValueError('Closed retained enrollment')
        path=Path(path)
        if path in self.files:
            self.check();raw=self.files[path][4]
            if len(raw)>maximum:raise ValueError('Original bounded retained input required')
            return raw
        parent=self.directory(path.parent);raw,generation=self.original.read(path,maximum)
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent);self.stack.callback(os.close,fd)
        if self.original.generation(os.fstat(fd))!=generation:
            raise ValueError('Source generation changed before retention')
        self.files[path]=(parent,path.name,fd,generation,raw);self.check();return raw
    def seal(self,root,names):
        root=Path(root);expected={root:set()}
        for name in names:
            path_name(name);parts=PurePosixPath(name).parts;parent=root
            for i,part in enumerate(parts):
                expected.setdefault(parent,set()).add(part)
                if i<len(parts)-1:parent/=part;expected.setdefault(parent,set())
        for path in expected:self.directory(path)
        self.namespace={path:frozenset(children) for path,children in expected.items()};return self.check()
    def check(self):
        if self.closed:raise ValueError('Closed retained enrollment')
        try:
            for parent,name,fd,identity in self.dirs.values():
                if directory_id(os.fstat(fd))!=identity or directory_id(os.stat(name,dir_fd=parent,follow_symlinks=False))!=identity:
                    raise ValueError('Original retained source ancestry changed')
            for parent,name,fd,generation,_raw in self.files.values():
                if self.original.generation(os.fstat(fd))!=generation or self.original.generation(os.stat(name,dir_fd=parent,follow_symlinks=False))!=generation:
                    raise ValueError('Original retained input generation changed')
            for path,names in self.namespace.items():
                if frozenset(os.listdir(self.dirs[path][2]))!=names:raise ValueError('Original closed retained source namespace changed')
        except OSError as error:raise ValueError('Original retained input unavailable') from error
        return True
    def close(self):
        if not self.closed:self.closed=True;self.stack.close()


def read_enrollment(root,file=None):
    file=root/'source'/ENTRY if file is None else file
    if file!=root/'source'/ENTRY or not re.fullmatch('/usr/local/share/droidmate-rc18-[a-f0-9]{64}',str(root)):
        raise ValueError('Fixed enrolled aggregate path required')
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    initial=directory_id(os.fstat(fd));closed=False;retained=None
    def close():
        nonlocal closed
        if not closed:
            closed=True
            if retained:retained.close()
            os.close(fd)
    helper=root/'source/tools/update-installer-entry.py'
    try:
        if initial[3:]!=(0,0) or stat.S_IMODE(initial[2])!=0o700:raise ValueError('Original private enrollment root required')
        directories={}
        for p in (helper.parent,*helper.parent.parents):
            s=p.lstat()
            if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode&0o022:raise ValueError('Protected enrolled ancestry required')
            directories[p]=directory_id(s)
        raw=read_bounded(helper,1048576);info=helper.lstat()
        if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(0,0,0o600):raise ValueError('Original protected stager required')
        original=stager(raw,helper);retained=RetainedInputs(original)
        def read(p,limit=1048576):
            return retained.read(p,limit)
        if read(helper)!=raw:raise ValueError('Original stager generation required')
        entries={'__main__.py':read(file),'release.tar':b''.join(read(root/('release.part-'+str(i)),MAX_PART) for i in range(3)),
                 'statement.json':read(root/'statement.json',8192),'signature.bin':read(root/'signature.bin',64)}
        read(root/'intent.json');read(root/'complete.json')
        def guard():
            if closed:raise ValueError('Closed enrollment admission')
            try:
                if directory_id(os.fstat(fd))!=initial or directory_id(root.lstat())!=initial:raise ValueError('Original enrolled root lost')
                if any(directory_id(p.lstat())!=identity for p,identity in directories.items()):raise ValueError('Original enrolled ancestry changed')
                retained.check()
                if directory_id(root.lstat())!=initial:raise ValueError('Original enrolled root changed')
            except OSError as error:raise ValueError('Original enrollment generation unavailable') from error
            return True
        guard.close=close;guard()
        return root,entries,guard
    except BaseException:close();raise
def enrolled_entries():
    file=Path(__file__);return read_enrollment(file.parents[2],file)

def verify_existing(original,root,files,identity):
    """Read-only completed-slot verifier. It has no creation/publication branch."""
    fd=original.protected_directory(root)
    try:
        info=os.fstat(fd)
        if (info.st_dev,info.st_ino)!=identity or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('Original completed root required')
        expected={n:{'size':len(b),'sha256':sha(b)} for n,b in sorted(files.items())}
        intent=encode({'schema':1,'rootIdentity':list(identity),'files':expected})
        if original.read(root/'intent.json')[0]!=intent:raise ValueError('Original immutable enrollment intent required')
        complete=original.document(original.read(root/'complete.json')[0])
        directories={};actual=set()
        for current,children,names in os.walk(root,followlinks=False):
            p=Path(current);handle=original.protected_directory(p)
            try:directories[p.relative_to(root).as_posix()]=list(directory_id(os.fstat(handle)))
            finally:os.close(handle)
            for child in children:
                handle=original.protected_directory(p/child);os.close(handle)
            actual.update((p/n).relative_to(root).as_posix() for n in names)
        required_dirs={'.',*(parent.as_posix() for n in files for parent in PurePosixPath(n).parents)}
        if set(directories)!=required_dirs or actual!={*files,'intent.json','complete.json'}:raise ValueError('Original closed enrollment namespace required')
        generations={}
        for n,b in sorted(files.items()):
            raw,generation=original.read(root/n)
            if raw!=b:raise ValueError('Original authenticated enrollment bytes required')
            generations[n]=generation
        expected_complete={'schema':1,'intentSha256':sha(intent),'generations':generations,'directories':directories}
        if encode(complete)!=encode(expected_complete):raise ValueError('Original completed file generations required')
        if (root.lstat().st_dev,root.lstat().st_ino)!=identity:raise ValueError('Original enrollment root changed')
        return True
    finally:os.close(fd)
def ensure_installer_descriptor_budget(limits):
    minimum = 4096
    soft, hard = limits.getrlimit(limits.RLIMIT_NOFILE)
    if hard != limits.RLIM_INFINITY and hard < minimum:
        raise ValueError('Installer descriptor budget unavailable')
    if soft != limits.RLIM_INFINITY and soft < minimum:
        limits.setrlimit(limits.RLIMIT_NOFILE, (minimum, hard))
        if limits.getrlimit(limits.RLIMIT_NOFILE) != (minimum, hard):
            raise ValueError('Installer descriptor budget unavailable')


def installer_failure_report(error):
    report = {'failureLocations': failure_locations(error)}
    for _ in range(8):
        if isinstance(error, OSError) and type(error.errno) is int and 0 < error.errno < 4096:
            report['errno'] = error.errno
            break
        error = error.__cause__ or error.__context__
        if error is None:
            break
    return report


def main(args):
    sys.dont_write_bytecode=True
    if not isinstance(args,list) or not args:raise ValueError('Explicit aggregate action required')
    enrolled=args[0]=='--enrolled';args=args[1:] if enrolled else args
    counts={'verify':0,'plan':0,'install-existing':0,'install-new':2,'recover':0,'undo':0,'legacy-video':0,'upgrade-existing':0,'upgrade-recover':0,'upgrade-undo':0}
    if not args or args[0] not in counts or len(args)!=1+counts[args[0]]:raise ValueError('Closed aggregate action required')
    if args[0]=='legacy-video' and not enrolled:raise ValueError('Enrolled worker entry required')
    if sys.platform!='linux':raise ValueError('Ubuntu Linux aggregate entry required')
    if args[0] in ('install-existing','install-new','recover','undo','upgrade-existing','upgrade-recover','upgrade-undo'):
        if os.geteuid()!=0:raise ValueError('Root aggregate action required')
        import resource
        ensure_installer_descriptor_budget(resource)
    guard=None;retained=None
    try:
        if enrolled:current,entries,guard=enrolled_entries();guard()
        else:current,entries=None,package_entries(Path(sys.argv[0]))
        release,files=authenticate(entries)
        if guard:guard()
        sources={n[len(PREFIX+'source/'):]:b for n,b in files.items() if n.startswith(PREFIX+'source/')}
        policy=compiled('verified_aggregate_policy',sources['tools/aggregate-release.py'],'authenticated-aggregate-policy.py')
        policy.review_signing_inputs(entries['release.tar'],entries['statement.json'],KEY_PIN)
        if guard:guard()
        if args==['verify']:
            print(json.dumps({'kind':'droidmate-aggregate-installer-verified','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
                'packageSha256':release['packageSha256'],'keySha256':KEY_PIN,'servicesChanged':False}));return
        if os.geteuid()!=0:raise ValueError('Root aggregate action required')
        os.umask(0o077)
        root=Path('/usr/local/share')/('droidmate-rc18-'+release['packageSha256'])
        if current is not None and root!=current:raise ValueError('Original enrolled package root required')
        original=stager(sources['tools/update-installer-entry.py'],root/'source/tools/update-installer-entry.py')
        stored={n[len(PREFIX):]:b for n,b in files.items()}
        stored.update(archive_parts(entries['release.tar']));stored.update({n:entries[n] for n in ('statement.json','signature.bin')})
        if not enrolled:
            original.materialize(root,stored)
            _root,observed,guard=read_enrollment(root)
            if observed!=entries:raise ValueError('Original capsule enrollment required')
        guard();identity=(root.lstat().st_dev,root.lstat().st_ino)
        verify_existing(original,root,stored,identity)
        retained=RetainedInputs(original)
        for name,raw in stored.items():
            if retained.read(root/name,max(1,len(raw)))!=raw:raise ValueError('Original authenticated retained bytes required')
        retained.read(root/'intent.json');retained.read(root/'complete.json')
        retained.seal(root,{*stored,'intent.json','complete.json'})
        # Reconcile completed receipts AFTER retaining all descriptors, closing
        # the initial-verification/retention race. No full-body rereads follow.
        verify_existing(original,root,stored,identity);retained.check();guard()
        def check():
            guard();retained.check();guard();return True
        check()
        coordinator=compiled('verified_aggregate_coordinator',sources['tools/aggregate-install.py'],root/'source/tools/aggregate-install.py')
        check()
        return coordinator.main(args,{'root':root,'release':release,'check':check})
    finally:
        if retained:retained.close()
        if guard:guard.close()
def failure_locations(error):
    """Development trace locations only: no exception text, locals or data."""
    rows=[];frame=error.__traceback__
    while frame is not None and len(rows)<16:
        code=frame.tb_frame.f_code;name=Path(code.co_filename).name
        if re.fullmatch(r'(?:aggregate-[a-z-]+|capture-[a-z-]+|gateway-update[a-z-]*)\.py|contextlib\.py',name):
            function=code.co_name
            if re.fullmatch('[A-Za-z_<>][A-Za-z0-9_<>]*',function):rows.append(name+':'+function+':'+str(frame.tb_lineno))
        frame=frame.tb_next
    return rows


if __name__=='__main__':
    try:main(sys.argv[1:])
    except Exception as error:
        print('Aggregate installer refused: '+type(error).__name__+'; preserve original records.',file=sys.stderr)
        print(json.dumps(installer_failure_report(error),sort_keys=True),file=sys.stderr);sys.exit(1)
