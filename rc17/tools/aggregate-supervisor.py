# SPDX-License-Identifier: GPL-3.0-only
"""Systemd-owned installer lifetime and same-boot recovery.

No VM/app-data operations. Only signed enrolled entry commands are admitted;
foreign unit definitions, overrides and existing operations are preserved.
"""
from contextlib import ExitStack
import json,os,re,stat,subprocess,types
from pathlib import Path

INSTALLER='droidmate-aggregate-install.service'
RECOVERY='droidmate-aggregate-recovery.service'
INSTALLER_PATH='/etc/systemd/system/'+INSTALLER
CGROUP=('0::/system.slice/'+INSTALLER+'\n').encode('ascii')
ENV={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'}
SECURITY=('FragmentPath','DropInPaths','Type','KillMode','OnFailure','OnFailureJobMode','User','Group',
 'ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload','RootDirectory','RootImage','PrivateMounts','ExecStart')


def worker_argv(ctx):
    return [b'/usr/bin/python3',b'-I',b'-B',str(ctx['root']/'source/tools/aggregate-installer-entry.py').encode('ascii'),b'--enrolled',b'install-existing']


def installer_unit(ctx):
    digest=ctx['release']['packageSha256']
    if not re.fullmatch('[a-f0-9]{64}',digest) or ctx['root']!=Path('/usr/local/share/droidmate-rc17-'+digest):raise ValueError('Original enrolled installer root required')
    return ('[Unit]\nDescription=DroidMate owned aggregate installation\nAfter=network.target\nOnFailure='+RECOVERY+'\nOnFailureJobMode=fail\n'
      '[Service]\nType=oneshot\nUser=root\nGroup=root\nUMask=0077\nWorkingDirectory=/\nPrivateMounts=no\n'
      'Environment=PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C\n'
      'UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME DBUS_SYSTEM_BUS_ADDRESS NODE_OPTIONS\n'
      'ExecStart='+b' '.join(worker_argv(ctx)).decode('ascii')+'\nKillMode=control-group\n'
      'TimeoutStartSec=1800\nTimeoutStopSec=180\nRestart=no\n').encode('ascii')


def route_install(phase,is_worker):
    if type(is_worker) is not bool:raise ValueError('Actual worker observation required')
    if phase=='new':return 'worker' if is_worker else 'launch'
    if phase=='accepted':return 'terminal'
    raise ValueError('Incomplete/restored installation requires recovery, never replay')


def validate_worker(ctx,pid,fields,group,argv,same_namespace):
    if (type(pid) is not int or pid<2 or group!=CGROUP or argv!=worker_argv(ctx) or same_namespace is not True
      or fields.get('FragmentPath')!=INSTALLER_PATH or fields.get('DropInPaths')!='' or fields.get('MainPID')!=str(pid)
      or fields.get('Type')!='oneshot' or fields.get('KillMode')!='control-group'
      or fields.get('OnFailure')!=RECOVERY or fields.get('OnFailureJobMode')!='fail'):
        raise ValueError('Actual owned enrolled installer process required')
    return True


def command(args,timeout=30):
    empty_commands={'ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload'}
    objects={'/org/freedesktop/systemd1/unit/'+n.replace('-','_2d').replace('.','_2e') for n in (INSTALLER,RECOVERY)}
    bus=(isinstance(args,list) and len(args)==8 and args[:5]==['/usr/bin/busctl','--system','--json=short','get-property','org.freedesktop.systemd1']
         and args[5] in objects and args[6]=='org.freedesktop.systemd1.Service' and args[7] in empty_commands)
    if not isinstance(args,list) or args[:1]!=['/usr/bin/systemctl'] and not bus:raise ValueError('Fixed installer bus command required')
    result=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,cwd='/',env=ENV,timeout=timeout)
    if result.returncode or len(result.stdout)>65536 or len(result.stderr)>16384:raise ValueError('Owned installer systemd operation refused')
    return result.stdout.decode('utf-8')


class Controls:
    def __init__(self,check):self.check_source=check;self.stack=ExitStack();self.dirs={};self.files={};self.closed=False
    @staticmethod
    def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    def directory(self,path):
        if path in self.dirs:return self.dirs[path][0]
        if not path.is_absolute() or '..' in path.parts:raise ValueError('Protected control ancestry required')
        parent=None if path==Path('/') else self.directory(path.parent)
        fd=os.open('/' if parent is None else path.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);self.stack.callback(os.close,fd)
        s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or s.st_mode&0o022:raise ValueError('Original root-owned control directory required')
        self.dirs[path]=(fd,self.identity(s)[:5]);return fd
    def hold(self,path,expected,link=False):
        self.check_source();parent=self.directory(path.parent)
        fd=os.open(path.name,os.O_PATH|os.O_NOFOLLOW if link else os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=parent);self.stack.callback(os.close,fd)
        s=os.fstat(fd)
        if (s.st_uid,s.st_gid,s.st_nlink,stat.S_IMODE(s.st_mode))!=(0,0,2,0o777 if link else 0o644):raise ValueError('Original owned control inode required')
        if link:
            if not stat.S_ISLNK(s.st_mode) or os.readlink(path)!=expected:raise ValueError('Original owned recovery enable link required')
        elif not stat.S_ISREG(s.st_mode) or not 0<s.st_size<=8192 or os.pread(fd,8193,0)!=expected:raise ValueError('Original owned control bytes required')
        self.files[path]=(fd,self.identity(s),expected,link);self.check()
    def check(self):
        if self.closed or self.check_source() is not True:raise ValueError('Original installer source admission lost')
        for p,(fd,identity) in self.dirs.items():
            if self.identity(os.fstat(fd))[:5]!=identity or self.identity(p.lstat())[:5]!=identity:raise ValueError('Original control directory changed')
        for p,(fd,identity,expected,link) in self.files.items():
            if self.identity(os.fstat(fd))!=identity or self.identity(p.lstat())!=identity:raise ValueError('Original owned control generation changed')
            if (os.readlink(p) if link else os.pread(fd,8193,0))!=expected:raise ValueError('Original owned control payload changed')
        return True
    def close(self):self.closed=True;self.stack.close()


class Supervisor:
    def __init__(self,ctx,engine,journal):
        if type(ctx) is not dict or set(ctx)!={'root','release','check'} or not callable(ctx['check']):raise ValueError('Retained coordinator source required')
        self.ctx,self.root,self.callback=ctx,ctx['root'],ctx['check'];self.release=json.dumps(ctx['release'],sort_keys=True,separators=(',',':'),allow_nan=False)
        self.c,self.j=engine,journal;self.controls=None;self.command=command;self.context();installer_unit(ctx)
    def context(self):
        def same():
            if (set(self.ctx)!={'root','release','check'} or self.ctx['root']!=self.root or self.ctx['check'] is not self.callback
                    or json.dumps(self.ctx['release'],sort_keys=True,separators=(',',':'),allow_nan=False)!=self.release):raise ValueError('Original installer context changed')
        same()
        if self.callback() is not True:raise ValueError('Original installer source admission lost')
        same();return True
    def prepare(self,recovery):
        self.expected_recovery=recovery
        self.context();self.j.guard()
        if type(recovery) is not bytes:raise ValueError('Authenticated recovery unit required')
        for key,raw in [('recovery',recovery),('installer',installer_unit(self.ctx))]:
            mapping=self.c.OwnedMapping(self.j,key,raw);mapping.prepare();mapping.publish()
            if mapping.verify() is not True:raise ValueError('Original owned installer/recovery registration required')
        link=self.c.OwnedBootLink(self.j);link.prepare();link.publish()
        if link.verify() is not True:raise ValueError('Original boot recovery link required')
        self.controls=Controls(self.context)
        self.controls.hold(Path(INSTALLER_PATH),installer_unit(self.ctx));self.controls.hold(Path(self.c.RECOVERY_UNIT),recovery)
        self.controls.hold(self.c.BOOT_LINK,self.c.RECOVERY_UNIT,link=True)
        self.context();return True
    def observe(self,names,unit=None):
        unit=INSTALLER if unit is None else unit
        if unit not in (INSTALLER,RECOVERY):raise ValueError('Closed owned supervision unit required')
        self.context();raw=self.command(['/usr/bin/systemctl','show',unit,'--all','--property='+','.join(names)],30);out={}
        for line in raw.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key not in names or key in out:raise ValueError('Closed installer service fields required')
            out[key]=value
        missing=set(names)-set(out);empty_commands={'ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload'}
        if not missing<=empty_commands:raise ValueError('Complete installer service fields required')
        for name in sorted(missing):
            obj='/org/freedesktop/systemd1/unit/'+unit.replace('-','_2d').replace('.','_2e')
            proof=json.loads(self.command(['/usr/bin/busctl','--system','--json=short','get-property','org.freedesktop.systemd1',obj,'org.freedesktop.systemd1.Service',name],30))
            if proof!={'type':'a(sasbttttuii)','data':[]}:raise ValueError('Structured empty original service command required')
            out[name]=''
        self.context();return out
    def graph(self):
        self.context();self.controls.check();v=self.observe(SECURITY)
        expected={'FragmentPath':INSTALLER_PATH,'DropInPaths':'','Type':'oneshot','KillMode':'control-group','OnFailure':RECOVERY,'OnFailureJobMode':'fail',
          'User':'root','Group':'root','PrivateMounts':'no',**{k:'' for k in ('ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload','RootDirectory','RootImage')}}
        if any(v[k]!=x for k,x in expected.items()):raise ValueError('Original effective installer graph required')
        prefix='{ path=/usr/bin/python3 ; argv[]='+b' '.join(worker_argv(self.ctx)).decode('ascii')+' ; ignore_errors=no ;'
        if not v['ExecStart'].startswith(prefix) or v['ExecStart'].count('argv[]=')!=1:raise ValueError('Original enrolled installer command required')
        self.controls.check();return v
    def recovery_graph(self):
        self.context();self.controls.check();v=self.observe(SECURITY,RECOVERY)
        raw=self.expected_recovery.decode('ascii');lines=raw.splitlines()
        commands=[line[len('ExecStart='):] for line in lines if line.startswith('ExecStart=')]
        if len(commands)!=1 or not commands[0].startswith('/usr/bin/') or '\n' in commands[0]:raise ValueError('One authenticated recovery command required')
        expected={'FragmentPath':self.c.RECOVERY_UNIT,'DropInPaths':'','Type':'oneshot','KillMode':'control-group',
          'OnFailure':'','OnFailureJobMode':'replace','PrivateMounts':'no',
          **{k:'' for k in ('ExecCondition','ExecStartPre','ExecStartPost','ExecStop','ExecStopPost','ExecReload','RootDirectory','RootImage')}}
        if any(v.get(k)!=x for k,x in expected.items()) or v.get('User') not in ('','root') or v.get('Group') not in ('','root'):
            raise ValueError('Original effective recovery graph required')
        prefix='{ path='+commands[0].split(' ',1)[0]+' ; argv[]='+commands[0]+' ; ignore_errors=no ;'
        if not v['ExecStart'].startswith(prefix) or v['ExecStart'].count('argv[]=')!=1:raise ValueError('Original authenticated recovery command required')
        self.controls.check();return True

    @staticmethod
    def is_worker():return Path('/proc/self/cgroup').read_bytes()==CGROUP
    def require_worker(self):
        self.context()
        if self.controls is None:raise ValueError('Owned installer controls must be admitted')
        self.graph();self.recovery_graph();v=self.observe(('FragmentPath','DropInPaths','MainPID','Type','KillMode','OnFailure','OnFailureJobMode'))
        argv=Path('/proc/self/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        same=all((os.stat('/proc/self/'+name).st_dev,os.stat('/proc/self/'+name).st_ino)==
          (os.stat('/proc/1/'+name).st_dev,os.stat('/proc/1/'+name).st_ino) for name in ('root','ns/mnt'))
        validate_worker(self.ctx,os.getpid(),v,Path('/proc/self/cgroup').read_bytes(),argv,same)
        self.controls.check();self.context();return True
    def start(self):
        self.context();self.controls.check();self.recovery_graph();self.graph()
        self.command(['/usr/bin/systemctl','start',INSTALLER],1900)
        self.controls.check();self.context();return True
    def close(self):
        if self.controls:self.controls.close()
