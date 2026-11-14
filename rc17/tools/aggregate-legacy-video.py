# SPDX-License-Identifier: GPL-3.0-only
"""Internal legacy video adapter. No CLI, signature bypass or enrollment authority.

Only the authenticated aggregate coordinator may supply ctx and an independent
owned mapping/public-view check. The parent keeps the original physical CPU
runtime. Frozen namespace code owns candidate teardown and CPU child supervision.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import types

CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
HELPERS={
    'legacy-gpu-preflight.py':'e0536c2809e13a5506e9f65401f8a449ec3b9eb95c5a963bd2cf000c950887c6',
    'legacy-video-namespace.py':'c711c34401a22db7f26b230f6f2a446c6ff7d569ef2639d7d56ef4842515d012',
}
POLICY=Path('/etc/droiddeck/capture-policy.json')
UUID=r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
CGROUP='0::/system.slice/{unit}\n'

def retain_context(ctx):
    # Capture identities before invoking a caller callback. A callback cannot
    # retarget the path/domain while continuing to validate an older package.
    if type(ctx) is not dict or set(ctx)!={'root','release','check'} or not callable(ctx['check']):
        raise ValueError('Original authenticated aggregate context required')
    root=ctx['root'];callback=ctx['check']
    def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    raw=encoded(ctx['release']);release=json.loads(raw)
    def unchanged():
        if (set(ctx)!={'root','release','check'} or ctx['root']!=root or ctx['check'] is not callback
                or encoded(ctx['release'])!=raw):raise ValueError('Original aggregate context binding changed')
    def check():
        unchanged()
        if callback() is not True:raise ValueError('Retained aggregate admission lost')
        unchanged();return True
    result={'root':root,'release':release,'check':check}
    check_context(result);return result

def check_context(ctx):
    if type(ctx) is not dict or set(ctx)!={'root','release','check'} or not callable(ctx['check']):
        raise ValueError('Original authenticated aggregate context required')
    value=ctx['release']
    if (type(value) is not dict or value.get('kind')!='droidmate-aggregate-release-authorization'
            or value.get('aggregateRelease')!='RC17' or value.get('coreRuntimeVersion')!='0.1.1-rc.20'
            or value.get('compatibleReleaseSha256')!=CORE or value.get('activationAllowed') is not True
            or type(value.get('packageSha256')) is not str or not re.fullmatch('[a-f0-9]{64}',value['packageSha256'])
            or ctx['root']!=Path('/usr/local/share/droidmate-rc17-'+value['packageSha256'])):
        raise ValueError('Fixed aggregate layout/domain required')
    if ctx['check']() is not True:raise ValueError('Retained aggregate admission lost')
    return True

def parent_command(ctx):
    check_context(ctx)
    return ['/usr/bin/python3','-I','-B',(ctx['root']/'source/tools/aggregate-installer-entry.py').as_posix(),'--enrolled','legacy-video']

def video_dropin(unit):
    if type(unit) is not str or not re.fullmatch(r'[a-z0-9-]{1,120}\.service',unit):raise ValueError('Fixed video unit basename required')
    return Path('/etc/systemd/system')/(unit+'.d')/'70-droidmate-aggregate.conf'

def helper(ctx,name):
    check_context(ctx)
    if name not in HELPERS:raise ValueError('Independently frozen legacy helper required')
    path=ctx['root']/'source/tools'/name;raw=path.read_bytes()
    if not 0<len(raw)<=1024**2 or hashlib.sha256(raw).hexdigest()!=HELPERS[name]:raise ValueError('Original qualified helper bytes required')
    check_context(ctx)
    module=types.ModuleType('verified_'+name.replace('-','_').replace('.','_'));module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    check_context(ctx);return module

def worker_environments(p,env):
    owner=env.get(b'TESLA_WAYDROID_RUNTIME_OWNER') if type(env) is dict else None
    if (not isinstance(owner,bytes) or not re.fullmatch(rb'[a-f0-9]{32}',owner)
            or env.get(b'TESLA_WAYDROID_RUNTIME_ROLE')!=b'video'
            or env.get(b'NODE_PATH')!=p.NODE_PATH.encode('ascii')
            or env.get(b'LD_LIBRARY_PATH')!=b'/opt/teslaandroid-waydroid/libgbinder'
            or b'DBUS_SYSTEM_BUS_ADDRESS' in env or any(key in env for key in p.FORBIDDEN)
            or any(key.startswith(b'LD_') and key!=b'LD_LIBRARY_PATH' or key.startswith(b'GST_PLUGIN_') for key in env)):
        raise ValueError('Original non-injected legacy owner/role/environment required')
    cpu={'PATH':'/usr/bin:/bin','HOME':'/root','USER':'root','LOGNAME':'root','LANG':'C','LC_ALL':'C',
         'NODE_PATH':p.NODE_PATH,'LD_LIBRARY_PATH':'/opt/teslaandroid-waydroid/libgbinder',
         'TESLA_WAYDROID_RUNTIME_OWNER':owner.decode('ascii'),'TESLA_WAYDROID_RUNTIME_ROLE':'video'}
    key=b'TESLA_WAYDROID_VIDEO_SETTINGS'
    if key in env:
        if env[key]!=b'1':raise ValueError('Original video settings enable flag required')
        cpu[key.decode('ascii')]=env[key].decode('ascii')
    gpu={**cpu,'DROIDMATE_EXPERIMENTAL_H264_ENCODER':'vaapi','DROIDMATE_GPU_RESIZE':'selective',
         'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE':'cpu','DROIDDECK_EXPERIMENTAL_FRAME_PACING':'bounded'}
    return cpu,gpu

def validate_parent(p,ctx,pid,fields,env,lease,sources,command):
    check_context(ctx)
    if (type(pid) is not int or pid<2 or type(fields) is not dict
            or fields.get('MainPID')!=str(pid) or fields.get('DropInPaths')!=video_dropin(p.UNIT).as_posix()
            or command!=b'\0'.join(x.encode('ascii') for x in parent_command(ctx))+b'\0'):
        raise ValueError('Actual owned enrolled video parent required')
    # This is an explicit projection of the two OWNED differences, not a claim
    # that the new Python parent is the original Node observation. Actual argv,
    # sole override and kernel process identity are independently checked.
    p.validate(sources,{**fields,'DropInPaths':''},p.COMMAND,env,lease)
    worker_environments(p,env);check_context(ctx);return True

class Parent:
    def __init__(self,ctx,mapping_check):
        ctx=retain_context(ctx)
        if not callable(mapping_check):raise ValueError('Independent owned mapping admission required')
        self.ctx=ctx;self.mapping_check=mapping_check;self.p=helper(ctx,'legacy-gpu-preflight.py')
        self.stack=ExitStack();self.closed=False

    def extras(self):
        text=self.p.collect(['show',self.p.UNIT,'--property=PrivateMounts,KillMode,User,Group'])
        rows={}
        for line in text.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key in rows:raise ValueError('Unambiguous selected video security properties required')
            rows[key]=value
        if (set(rows)!={'PrivateMounts','KillMode','User','Group'} or rows['PrivateMounts']!='no'
                or rows['KillMode']!='control-group' or rows['User'] not in ('','root') or rows['Group'] not in ('','root')):
            raise ValueError('Original host namespace and root control-group supervision required')
        return rows

    def cgroup(self):
        p=self.p;fd=os.open('cgroup',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=self.process.fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Original kernel cgroup record required')
            raw=os.read(fd,4097)
            if raw!=CGROUP.format(unit=p.UNIT).encode('ascii'):raise ValueError('Selected video service cgroup required')
            return raw
        finally:os.close(fd)

    def original_namespace(self):
        for name,handle,expected in self.pid_one:
            current=os.stat('/proc/1/'+name)
            if self.p.identity(os.fstat(handle))[:5]!=expected or self.p.identity(current)[:5]!=expected:
                raise ValueError('Original PID1 root/mount namespace changed')
            if self.p.identity(os.fstat(self.process.targets[name]))[:5]!=expected:
                raise ValueError('Video parent is not in the original host namespace')

    def __enter__(self):
        try:
            if os.name!='posix' or os.geteuid()!=0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
                raise ValueError('Root Linux fixed system bus parent required')
            p=self.p;p.require_no_portable()
            if os.path.lexists(POLICY):raise ValueError('Legacy policy conflict preserved')
            self.pid=os.getpid();self.process=p.Process(self.stack,self.pid,executable=Path('/usr/bin/python3'))
            self.pid_one=[]
            for name in ('root','ns/mnt'):
                fd=os.open('/proc/1/'+name,os.O_PATH);self.stack.callback(os.close,fd)
                self.pid_one.append((name,fd,p.identity(os.fstat(fd))[:5]))
            self.original_namespace();self.cgroup()
            self.held=p.Held(self.stack);self.sources={}
            for name,pin in p.PINS.items():
                path=p.ROOT/name;mode=stat.S_IMODE(path.lstat().st_mode)
                if mode not in (0o444,0o644):raise ValueError('Original physical legacy source mode required')
                raw=self.held.read(path,262144,mode);self.sources[name]=hashlib.sha256(raw).hexdigest()
                if self.sources[name]!=pin or self.process.source_identity(path)!=self.held.files[path][1]:
                    raise ValueError('Original physical legacy source/kernel view required')
            self.lease_raw=self.held.read(p.LEASE,512,0o600);self.lease=p.lease_document(self.lease_raw)
            self.environment_raw=self.process.read('environ',131072);self.env=p.environment(self.environment_raw)
            self.command=self.process.read('cmdline',4096);self.fields=p.unit_fields();self.security=self.extras()
            self.boot=p.proc_read(Path('/proc/sys/kernel/random/boot_id'),128)
            if not re.fullmatch((UUID+'\n').encode('ascii'),self.boot):raise ValueError('Current kernel boot required')
            validate_parent(p,self.ctx,self.pid,self.fields,self.env,self.lease,self.sources,self.command)
            self.cpu_env,self.gpu_env=worker_environments(p,self.env)
            self.admit();return self
        except BaseException:self.close();raise

    def verify(self):
        if self.closed:raise ValueError('Closed original legacy parent')
        p=self.p;p.require_no_portable()
        if os.path.lexists(POLICY):raise ValueError('Legacy policy conflict preserved')
        self.process.verify();self.held.verify();self.original_namespace();self.cgroup()
        if (p.unit_fields()!=self.fields or self.extras()!=self.security
                or self.process.read('cmdline',4096)!=self.command or self.process.read('environ',131072)!=self.environment_raw
                or p.proc_read(Path('/proc/sys/kernel/random/boot_id'),128)!=self.boot):
            raise ValueError('Original video parent/unit/boot generation changed')
        self.process.verify();self.held.verify();return True

    def admit(self):
        check_context(self.ctx)
        if self.mapping_check() is not True:raise ValueError('Owned video mapping/public view admission lost')
        self.verify();check_context(self.ctx)
        if self.mapping_check() is not True:raise ValueError('Owned video mapping admission changed')
        self.verify();check_context(self.ctx)
        if self.mapping_check() is not True:raise ValueError('Owned mapping admission lost after native verification')
        check_context(self.ctx);return True

    def fallback(self):
        self.admit()
        argv=self.p.COMMAND[:-1].decode('ascii').split('\0')
        os.execve(argv[0],argv,dict(self.cpu_env))

    def close(self):
        if not self.closed:self.closed=True;self.stack.close()

    def __exit__(self,*args):self.close()

def run(ctx,mapping_check):
    ctx=retain_context(ctx)
    if not callable(mapping_check):raise ValueError('Independent owned mapping admission required')
    namespace=helper(ctx,'legacy-video-namespace.py')
    with Parent(ctx,mapping_check) as parent:
        check_context(ctx)
        source=Path('/var/lib/droidmate-browser')/ctx['release']['packageSha256']/'legacy/runtime'
        argv=parent.p.COMMAND[:-1].decode('ascii').split('\0')
        return namespace.run_view(source.as_posix(),parent.p.ROOT.as_posix(),argv,dict(parent.gpu_env),admit=parent.admit,fallback=parent.fallback)
