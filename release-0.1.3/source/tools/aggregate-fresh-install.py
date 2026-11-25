# SPDX-License-Identifier: GPL-3.0-only
"""Fresh Ubuntu foundation + original signed core20; internal aggregate API only.

No standalone CLI or recovery replay. Original component journals own recovery.
All effects are behind Native; tests replace that OS boundary, never installers.
"""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import signal
import time
import stat
import subprocess
import sys
import tarfile
import types

sys.dont_write_bytecode = True
CORE = '1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
STAGER = '48ae63a3deb49022ec7065c0a17ea836712eef97080952ba22aed96948bcd7c5'
ASSETS = {
    'foundation': {'name':'droiddeck-0.1.0-rc.2-source.tar','size':7577600,'sha256':'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28'},
    'coreUpdater': {'name':'droiddeck-0.1.1-rc.20-update.pyz','size':10564125,'sha256':'4a942115bc7c2c3d89ad710bda523b5a316f235e90daa8ac72a7332d1eb02bd9'},
    'coreSource': {'name':'droiddeck-0.1.1-rc.20-source.tar','size':8632320,'sha256':'162fd7f50072da4700bc4eabbf95127dfd6724dd5d51959e5c805c9df80f0b3e'},
    'capturePackage': {'name':'capture-package.tar','size':3348480,'sha256':'462cd2e75eefa61a0ce7a0bc5bdbf24231d235b4700b019e9ebc749d713c593e'},
    'captureStatement': {'name':'capture-statement.json','size':805,'sha256':'e23ec35d00324eda4d7c875bd872a44de8f95170e3b89f212a737c68db951c2d'},
    'captureSignature': {'name':'capture-signature.bin','size':64,'sha256':'e83f89536177eba7a80a8d0bce2fa395f98accecc13711689efee6735487dc4d'},
    'gpuHost': {'name':'droidmate-gpu-host-v2.tar','size':1290240,'sha256':'0e8ad94c23fb704439eeac7a72ea0970a896b8fa5222c9bbf7b647092199f06f'},
    'gpuSource': {'name':'droidmate-gpu-native-source-v2.tar','size':1822720,'sha256':'3cdc8e9837f069eaa3e10218d1ef0bdc3d4f4b0e205be8378d7550c132bcba57'},
    'gpuGuest': {'name':'droidmate-gpu-guest-v1.tar','size':122880,'sha256':'7b1f2cd6976a924673c3a95d190b8ee47a1e17872e1b571a4d406df42e51c8a3'},
}
PREFIX = 'droiddeck-source-candidate/'
FOUNDATION_MANIFEST = '6996c754fe58aa1ce9e62426bd9a60aefbc78089b66e2d65eeec6ca5241c9b20'
STATE = Path('/var/lib/droidmate-fresh-install')
PUBLIC = Path('/usr/local/share/droidmate-foundation-rc2')
POLICY = Path('/etc/needrestart/conf.d/90-droiddeck.conf')
POLICY_BYTES = (b'# SPDX-License-Identifier: GPL-3.0-only\n'
    b'# Library updates may be installed normally. Restart this coupled Android graph\n'
    b'# together during coordinated maintenance or reboot, never as independent jobs.\n'
    b'$nrconf{override_rc}->{qr(^waydroid-container(?:\\.service)?$)} = 0;\n'
    b'$nrconf{override_rc}->{qr(^tesla-waydroid-[A-Za-z0-9_.@-]+$)} = 0;\n'
    b'$nrconf{override_rc}->{qr(^droiddeck-[A-Za-z0-9_.@-]+$)} = 0;\n1;\n')
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C',
       'JAVA_HOME':'/usr/lib/jvm/java-17-openjdk-amd64','PYTHONDONTWRITEBYTECODE':'1'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('ascii')


class Binding:
    """Retain the caller's admitted identity, including callback identity."""
    def __init__(self, ctx):
        if type(ctx) is not dict or set(ctx) != {'root','release','check'} or not callable(ctx['check']):
            raise ValueError('Retained authenticated aggregate context required')
        self.ctx, self.root, self.callback = ctx, ctx['root'], ctx['check']
        self.raw = encode(ctx['release'])
        r = json.loads(self.raw)
        if (r.get('kind') != 'droidmate-aggregate-release-authorization'
                or r.get('aggregateRelease') != 'RC18' or r.get('coreRuntimeVersion') != '0.1.1-rc.20'
                or r.get('compatibleReleaseSha256') != CORE or r.get('activationAllowed') is not True
                or type(r.get('packageSha256')) is not str or not re.fullmatch('[a-f0-9]{64}',r['packageSha256'])
                or self.root != Path('/usr/local/share/droidmate-rc18-'+r['packageSha256'])):
            raise ValueError('Original RC18/core20 activation domain required')
        self.release = r
        self.check()

    def check(self):
        def same():
            if (set(self.ctx) != {'root','release','check'} or self.ctx['root'] != self.root
                    or self.ctx['check'] is not self.callback or encode(self.ctx['release']) != self.raw):
                raise ValueError('Original aggregate context changed')
        same()
        if self.callback() is not True:
            raise ValueError('Original retained aggregate admission lost')
        same()
        return True


def parameters(user, lan):
    if type(user) is not str or user == 'root' or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}',user):
        raise ValueError('Explicit existing nonroot user required')
    if type(lan) is not str:
        raise ValueError('Explicit private IPv4 required')
    ip = ipaddress.IPv4Address(lan)
    if (str(ip) != lan or ip.packed[-1] in (0,255) or not any(ip in ipaddress.IPv4Network(n)
            for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))):
        raise ValueError('Explicit private IPv4 required')


def foundation_files(raw):
    """Parse only the pinned publisher tar; never tar.extract/extractall."""
    if type(raw) is not bytes or len(raw) > 8*1024**2:
        raise ValueError('Bounded foundation archive required')
    files, canonical, total = {}, io.BytesIO(), 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source, \
                tarfile.open(fileobj=canonical,mode='w:',format=tarfile.USTAR_FORMAT) as target:
            for m in source:
                name = m.name
                p = PurePosixPath(name)
                if (not name.startswith(PREFIX) or p.is_absolute() or str(p) != name
                        or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}',name)
                        or any(n in ('.','..','.git','.ssh','.env') for n in name.split('/'))
                        or name in files or m.type != tarfile.REGTYPE or m.pax_headers or m.sparse is not None
                        or m.linkname or m.uid or m.gid or m.uname or m.gname or m.mtime
                        or m.mode != (0o755 if name == PREFIX+'droiddeck-install' else 0o644)
                        or not 0 <= m.size <= 1024**2 or len(files) >= 845):
                    raise ValueError('Canonical closed regular foundation sources required')
                total += m.size
                if total > 8*1024**2:
                    raise ValueError('Expanded foundation bound exceeded')
                data = source.extractfile(m).read(m.size+1)
                if len(data) != m.size:
                    raise ValueError('Truncated foundation source')
                files[name] = (data,m.mode)
                row=tarfile.TarInfo(name);row.mode=m.mode;row.size=len(data)
                target.addfile(row,io.BytesIO(data))
    except (tarfile.TarError,EOFError,OSError) as error:
        raise ValueError('Invalid foundation source archive') from error
    if len(files) != 845 or canonical.getvalue() != raw:
        raise ValueError('Exact canonical 845-member foundation required')
    manifest_raw = files[PREFIX+'package-manifest.json'][0]
    if sha(manifest_raw) != FOUNDATION_MANIFEST:
        raise ValueError('Original foundation manifest required')
    manifest = json.loads(manifest_raw)
    expected = [{'path':n[len(PREFIX):],'size':len(b),'sha256':sha(b)}
                for n,(b,_mode) in sorted(files.items()) if n != PREFIX+'package-manifest.json']
    if manifest.get('files') != expected:
        raise ValueError('Complete original foundation source closure required')
    return files


def read_protected(path, limit, mode):
    """Immutable root-owned regular bytes; no dependency executes during bootstrap."""
    path = Path(path)
    for parent in path.parents:
        s=parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode & 0o022:
            raise ValueError('Protected nonlinked root ancestry required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or (before.st_uid,before.st_gid,before.st_nlink,stat.S_IMODE(before.st_mode)) != (0,0,1,mode)
                or not 0 <= before.st_size <= limit):
            raise ValueError('Bounded original root-owned regular input required')
        with os.fdopen(os.dup(fd),'rb') as stream:
            raw=stream.read(limit+1)
        generation=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(raw)!=before.st_size or any(generation(s)!=generation(before) for s in (os.fstat(fd),path.lstat())):
            raise ValueError('Original input generation changed')
        return raw, generation(before)
    finally:
        os.close(fd)


def load_stager(binding):
    binding.check()
    path=binding.root/'source/tools/update-installer-entry.py'
    raw,_generation=read_protected(path,1024**2,0o600)
    if sha(raw)!=STAGER:
        raise ValueError('Original frozen source stager required')
    binding.check()
    module=types.ModuleType('authenticated_fresh_stager');module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    binding.check()
    return module


def policy_decision(raw):
    if raw is None:
        return []
    if raw != POLICY_BYTES:
        raise ValueError('Foreign existing restart policy preserved')
    return ['--accept-existing-policy']


def group_members(group):
    if type(group) is not int or group<2:raise ValueError('Owned child session required')
    active=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdecimal():continue
        try:
            raw=(p/'stat').read_text();fields=raw[raw.rindex(')')+2:].split()
            if int(fields[2])==group and int(fields[3])==group and fields[0] not in ('Z','X'):active.append(int(p.name))
        except (FileNotFoundError,ProcessLookupError):continue
    return active


def run_owned(args,output,error,timeout):
    """Own one new installer session; drain descendants before returning."""
    process=subprocess.Popen(args,cwd='/',env=ENV,stdin=subprocess.DEVNULL,stdout=output,stderr=error,start_new_session=True)
    try:
        code=process.wait(timeout=timeout)
        return subprocess.CompletedProcess(args,code)
    finally:
        # Kill only the newly created child's process group. Systemd services
        # remain under their ORIGINAL independent recovery authority.
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:pass
        try:os.killpg(process.pid,getattr(signal,'SIGKILL',9))
        except ProcessLookupError:pass
        process.wait(timeout=5)
        end=time.monotonic()+5
        while group_members(process.pid):
            if time.monotonic()>=end:raise TimeoutError('Owned installer descendants did not drain; retain original journals')
            time.sleep(.02)


@contextmanager
def verified_imports(native,root,files):
    """Close dynamic dependency loading over retained signed bytes."""
    factory=importlib.util.spec_from_file_location
    class Loader:
        def __init__(self,path,raw):self.path,self.raw=path,raw
        def create_module(self,spec):return None
        def exec_module(self,module):
            native.verify()
            if native.source(self.path)!=self.raw:raise ValueError('Original retained policy source required')
            exec(compile(self.raw,str(self.path),'exec'),module.__dict__)
            native.verify()
    def spec(name,location=None,*args,**kwargs):
        path=Path(location)
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Closed absolute policy source required')
        try:relative=path.relative_to(root).as_posix()
        except ValueError:raise ValueError('Closed policy source root required')
        if relative not in files or not relative.endswith('.py'):raise ValueError('Closed signed policy dependency required')
        native.verify();raw=native.source(path)
        if raw!=files[relative]:raise ValueError('Original signed dependency bytes required')
        return factory(name,path,loader=Loader(path,raw))
    importlib.util.spec_from_file_location=spec
    try:yield
    finally:importlib.util.spec_from_file_location=factory


class Native:
    def __init__(self,binding,user,lan):
        self.binding,self.user,self.lan=binding,user,lan
        self.root=STATE
        self.package=PUBLIC/'droiddeck-source-candidate'
        self.updater=self.root/'inputs/assets'/ASSETS['coreUpdater']['name']
        self.directories,self.files,self.staged={},{},{}
        self.stack=None

    def preflight(self):
        if sys.platform!='linux' or os.geteuid()!=0 or platform.machine()!='x86_64' or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
            raise ValueError('Root unredirected Ubuntu x86_64 installation required')
        release=platform.freedesktop_os_release()
        if (release.get('ID'),release.get('VERSION_ID'))!=('ubuntu','26.04'):
            raise ValueError('Clean Ubuntu26.04 required')
        import pwd
        account=pwd.getpwnam(self.user)
        if (account.pw_uid<1000 or account.pw_gid<1000 or account.pw_dir!='/home/'+self.user
                or account.pw_shell in ('/bin/false','/usr/sbin/nologin')):
            raise ValueError('Original regular nonroot home account required')
        home=Path(account.pw_dir);s=home.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=account.pw_uid or s.st_mode & 0o022:
            raise ValueError('Original selected home required')
        markers=[self.root,PUBLIC,Path('/opt/droiddeck'),Path('/etc/droiddeck'),Path('/var/lib/droiddeck'),
                 Path('/var/lib/droiddeck-install-intent'),Path('/var/lib/waydroid'),Path('/etc/waydroid-extra'),
                 Path('/opt/teslaandroid-waydroid'),Path('/etc/teslaandroid-waydroid'),
                 Path('/root/droiddeck-installation'),Path('/root/droiddeck-installer-sources')]
        for relative in ('droiddeck-installation','.config/droiddeck','.local/share/waydroid',
                         '.config/teslaandroid-waydroid','.local/share/teslaandroid-waydroid'):
            markers.append(home/relative)
        if any(os.path.lexists(p) for p in markers):
            raise ValueError('Fresh target required; preserve all Android/portable/legacy/partial state')
        self.binding.check()

    def directory(self,path):
        if path in self.directories:return self.directories[path][0]
        fd=self.stager.protected_directory(path)
        self.stack.callback(os.close,fd)
        s=os.fstat(fd)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid)
        if identity(path.lstat())!=identity(s):
            raise ValueError('Original directory path binding changed')
        self.directories[path]=(fd,identity(s))
        return fd

    def mkdir(self,path,mode):
        parent=self.stager.protected_directory(path.parent)
        try:
            os.mkdir(path.name,mode=mode,dir_fd=parent)
            fd=self.directory(path)
            os.fchmod(fd,mode)
            s=os.fstat(fd)
            self.directories[path]=(fd,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid))
            os.fsync(fd);os.fsync(parent)
        finally:
            os.close(parent)

    @contextmanager
    def claim(self,stager,assets,files):
        import fcntl
        self.stager=stager
        with ExitStack() as stack:
            self.stack=stack
            self.preflight()
            self.mkdir(self.root,0o700)  # Exclusive global claim; no resume branch.
            fcntl.flock(self.directories[self.root][0],fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.record('intent.json',{'schema':1,'kind':'aggregate-fresh-core-install',
                'aggregatePackageSha256':self.binding.release['packageSha256'],
                'user':self.user,'lan':self.lan,'sourcePackage':str(self.package),
                'assets':{n:sha(b) for n,b in sorted(assets.items())}})
            stored={'assets/'+ASSETS[n]['name']:b for n,b in assets.items()}
            self.stager.materialize(self.root/'inputs',stored)
            self.hold_materialized(self.root/'inputs',stored)
            self.input_files=stored
            self.mkdir(PUBLIC,0o755)
            for name in sorted({p.as_posix() for n in files for p in PurePosixPath(n).parents if str(p)!='.'},
                               key=lambda n:(n.count('/'),n)):
                self.mkdir(PUBLIC/name,0o755)
            for name,(raw,mode) in sorted(files.items()):
                path=PUBLIC/name
                stager.write(path,raw)
                os.chmod(path,mode,follow_symlinks=False)
                value,generation=read_protected(path,1024**2,mode)
                if value!=raw:
                    raise ValueError('Original staged foundation bytes required')
                self.files[path]=(value,generation,mode)
            self.verify()
            yield self
            self.verify()

    def hold_materialized(self,root,files):
        self.binding.check();self.directory(root)
        expected={*files,'intent.json','complete.json'}
        directories={'.',*(p.as_posix() for n in files for p in PurePosixPath(n).parents)}
        for n in sorted(directories,key=lambda n:(n.count('/'),n)):self.directory(root/n)
        for n in sorted(expected):
            path=root/n;raw,generation=read_protected(path,32*1024**2,0o600)
            if n in files and raw!=files[n]:raise ValueError('Original authenticated staged input required')
            self.files[path]=(raw,generation,0o600)
        self.staged[root]=(expected,directories)
        self.binding.check()

    def source(self,path):
        self.verify()
        if path not in self.files:raise ValueError('Original retained source required')
        return self.files[path][0]

    def verify(self):
        self.binding.check()
        for path,(fd,before) in self.directories.items():
            for s in (os.fstat(fd),path.lstat()):
                if (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)!=before:
                    raise ValueError('Fresh source/state directory generation changed')
        for path,(raw,generation,mode) in self.files.items():
            if read_protected(path,max(len(raw),1),mode)!=(raw,generation):
                raise ValueError('Original fresh-install source generation changed')
        if hasattr(self,'input_files') and self.root/'inputs' not in self.staged:
            raise ValueError('Original staged input generations required')
        for root,(expected,directories) in self.staged.items():
            actual=set();actual_dirs=set()
            for current,children,names in os.walk(root,followlinks=False):
                current=Path(current);actual_dirs.add(current.relative_to(root).as_posix())
                actual.update((current/n).relative_to(root).as_posix() for n in names)
            if actual!=expected or actual_dirs!=directories:raise ValueError('Original closed staged namespace required')
        self.binding.check()
        return True

    def record(self,name,value):
        self.binding.check()
        self.stager.write(self.root/name,encode(value))
        raw,generation=read_protected(self.root/name,1024**2,0o600)
        if raw!=encode(value):
            raise ValueError('Original phase record required')
        self.files[self.root/name]=(raw,generation,0o600)
        self.binding.check()

    def run(self,label,args,timeout):
        self.verify()
        path=self.root/(label+'.stdout')
        err=self.root/(label+'.stderr')
        with path.open('xb') as output,err.open('xb') as error:
            os.chmod(path,0o600);os.chmod(err,0o600)
            result=run_owned(args,output,error,timeout)
            output.flush();error.flush();os.fsync(output.fileno());os.fsync(error.fileno())
        self.verify()
        if result.returncode:
            raise ValueError('Original '+label+' incomplete; retain logs and component journals, never replay')
        raw,_generation=read_protected(path,1024**2,0o600)
        return json.loads(raw)

    def foundation_completed(self,result):
        raw,_generation=read_protected(Path('/root/droiddeck-installation/complete.json'),8192,0o600)
        if json.loads(raw)!={'kind':'clean-machine-installation','transaction':result['transaction'],
                           'browserRebootRecoveryAcceptanceRequired':True,'installable':False}:
            raise ValueError('Original foundation completion required')
        intent=json.loads(read_protected(Path('/root/droiddeck-installation/intent.json'),32768,0o600)[0])
        config=intent.get('configuration',{})
        if (config.get('user')!=self.user or config.get('lanAddress')!=self.lan
                or intent.get('packageManifestSha256')!=FOUNDATION_MANIFEST):
            raise ValueError('Original foundation package/user/address binding required')
        self.verify()

    def policy_flags(self):
        self.verify()
        try:
            raw,generation=read_protected(POLICY,4096,0o644)
        except FileNotFoundError:
            return []
        flags=policy_decision(raw)
        # Only authenticated source INSIDE the original updater is consulted.
        entries=self.stager.package_entries(self.updater)
        release,_manifest,files=self.stager.authenticate(entries)
        if release['bundleSha256']!=CORE or release['target']['version']!='0.1.1-rc.20':
            raise ValueError('Original core20 updater authorization required')
        if entries['source.tar']!=self.input_files['assets/'+ASSETS['coreSource']['name']]:
            raise ValueError('Original retained core source differs from signed updater source')
        directory=self.root/'policy-verifier'
        if os.path.lexists(directory):raise ValueError('Existing policy verifier claim preserved')
        staged={'src/'+n:b for n,b in files.items()}
        self.stager.materialize(directory,staged);self.hold_materialized(directory,staged);self.verify()
        path=directory/'src/runtime/portable/gateway-update-release.py'
        with verified_imports(self,directory/'src',files):
            spec=importlib.util.spec_from_file_location('original_fresh_policy_admission',path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            if module.reviewed_policy()!=raw:raise ValueError('Original updater restart policy differs')
            proof=module.B.A.base_proof('completed',str(self.package))
        if proof.get('originalInstallationOwnershipVerified') is not True:
            raise ValueError('Original readonly completed-base policy admission required')
        if read_protected(POLICY,4096,0o644)!=(raw,generation):
            raise ValueError('Original accepted policy generation changed')
        self.verify()
        return flags

    def core_completed(self,result):
        # Original updater install waits for healthy.json; no new release receipt
        # is manufactured or substituted for its original ownership journal.
        self.verify()


def fresh_install(ctx, assets, user, lan):
    """Explicit install-new continuation; success means capture enrollment NEXT."""
    parameters(user,lan)
    binding=Binding(ctx)
    if type(assets) is not dict or set(assets)!=set(ASSETS):
        raise ValueError('All nine original QU assets required')
    held=dict(assets)
    for name,row in ASSETS.items():
        raw=held[name]
        if type(raw) is not bytes or (len(raw),sha(raw))!=(row['size'],row['sha256']):
            raise ValueError('Exact original asset required: '+name)
    files=foundation_files(held['foundation'])
    stager=load_stager(binding)
    native=Native(binding,user,lan)
    native.preflight()
    def boundary():
        binding.check();native.verify();binding.check()
    with native.claim(stager,held,files):
        boundary()
        native.record('foundation.started.json',{'kind':'original-clean-foundation','user':user,'lan':lan})
        result=native.run('foundation',['/usr/bin/python3','-I','-B',str(binding.root/'source/tools/aggregate-build-toolchain.py'),
                          'foundation',user,lan],21600)
        if (type(result) is not dict or result.get('kind')!='clean-machine-installation'
                or type(result.get('transaction')) is not str or not re.fullmatch('[a-f0-9]{12}',result['transaction'])
                or result.get('phasesCompleted')!=35 or result.get('releaseAccepted') is not False):
            raise ValueError('Original foundation completion result required')
        boundary();native.foundation_completed(result);boundary()
        native.record('foundation.complete.json',result)
        flags=native.policy_flags()
        if flags not in ([],['--accept-existing-policy']):
            raise ValueError('Only original admitted existing-policy flag permitted')
        boundary()
        native.record('core.started.json',{'coreBundleSha256':CORE,'rc2Package':str(native.package),'policyFlags':flags})
        core=native.run('core',['/usr/bin/python3','-I','-B',str(native.updater),
                        'install','completed',str(native.package),*flags],1800)
        if (type(core) is not dict or core.get('kind')!='gateway-chain-installed'
                or core.get('digest')!=CORE or type(core.get('target')) is not dict
                or core['target'].get('version')!='0.1.1-rc.20' or core.get('androidDataPreserved') is not True):
            raise ValueError('Original signed core20 healthy completion required')
        boundary();native.core_completed(core);boundary()
        report={'kind':'aggregate-fresh-core-ready','coreRuntimeVersion':'0.1.1-rc.20',
                'compatibleReleaseSha256':CORE,'aggregatePackageSha256':binding.release['packageSha256'],
                'rc2Package':str(native.package),'stateRoot':str(native.root),
                'readyForCaptureEnrollment':True,'releaseComplete':False,
                'captureEnrolled':False,'hostProfileInstalled':False,'bootPreferenceEnrolled':False}
        native.record('complete.json',report);boundary()
        return report
