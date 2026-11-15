# SPDX-License-Identifier: GPL-3.0-only
"""Read-only exact video graph and native-source admission. Never activate/signal."""
from contextlib import ExitStack, contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys

VIDEO='tesla-waydroid-video-preview.service'
PEERS=tuple('tesla-waydroid-'+n+'.service' for n in ('runtime','compositor','audio-preview','gateway-preview','audio-relay-preview','video-relay-preview'))
COMPOSITOR='tesla-waydroid-compositor.service'
LEASE=Path('/run/droiddeck/runtime/native-lease.json')
CONFIG=Path('/etc/droiddeck/runtime.json')
DROP='/run/systemd/system/'+VIDEO+'.d/90-droiddeck-capture-view.conf'
_spec=importlib.util.spec_from_file_location('capture_graph_effective',Path(__file__).with_name('effective-units.py'))
E=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(E)
NS_FIELDS=('RootDirectory','RootImage','ReadOnlyPaths','ReadWritePaths','InaccessiblePaths','TemporaryFileSystem','MountImages','ExtensionImages',
           'PrivateNetwork','NetworkNamespacePath','PrivateUsers','PrivateTmp','PrivateDevices','ProtectSystem','ProtectHome','MountAPIVFS','ProtectProc','ProcSubset')

ARTIFACTS={
    '/opt/droiddeck/libgbinder/libglibutil.so.1.0.80':(85664,'adf21a840c7754cb61f82e444ae73c1f410abe4f1427e35f74fe45fa99170d90'),
    '/opt/droiddeck/libgbinder/libgbinder.so.1.1.45':(164192,'d473613f9c57921113d3cefe1cbc99676476270db756b52b5aa407c1ece06cae')}
LINKS={'libgbinder.so':'libgbinder.so.1.1.45','libgbinder.so.1':'libgbinder.so.1.1.45',
    'libglibutil.so':'libglibutil.so.1.0.80','libglibutil.so.1':'libglibutil.so.1.0.80'}


def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate metadata key')
        result[key]=value
    return result


def plain(value,depth=0):
    if depth>8:raise ValueError('Bounded graph structure required')
    if isinstance(value,str):return E.text(value)
    if type(value) is bool or type(value).__name__=='Boolean':return E.boolean(value)
    if isinstance(value,int):return E.integer(value)
    if isinstance(value,(tuple,list)):
        if len(value)>256:raise ValueError('Bounded graph vector required')
        return [plain(v,depth+1) for v in value]
    raise ValueError('Closed systemd metadata type required')


def mount_rows(values):
    rows=plain(values)
    if any(type(r) is not list or len(r)!=4 or type(r[0]) is not str or type(r[1]) is not str
           or type(r[2]) is not bool or type(r[3]) is not int for r in rows):raise ValueError('Exact structured bind graph required')
    if len({tuple(row) for row in rows})!=len(rows):raise ValueError('Duplicate bind graph row')
    return sorted(rows)


def stable_environment(values):
    values=E.strings(values);result=[]
    for item in values:
        key,sep,value=item.partition('=')
        if not sep:raise ValueError('Canonical environment entry required')
        if key=='TESLA_WAYDROID_RUNTIME_OWNER':
            if not re.fullmatch('[a-f0-9]{32}',value):raise ValueError('Exact independently checked native owner required')
            item=key+'=<independently-verified-native-owner>'
        result.append(item)
    return E.digest(result)


def project(service,unit):
    result=E.project_record(service,unit)
    result["StableEnvironmentSha256"]=stable_environment(service["Environment"])
    result.update(RestartUSec=E.integer(service['RestartUSec']),PrivateMounts=E.boolean(service['PrivateMounts']),
        BindPaths=mount_rows(service['BindPaths']),BindReadOnlyPaths=mount_rows(service['BindReadOnlyPaths']),
        NamespaceSha256=sha(encode({k:plain(service[k]) for k in NS_FIELDS})))
    result.update({key:E.strings(unit[key]) for key in ('BindsTo','Requisite','Conflicts','Before','OnFailure','JoinsNamespaceOf')})
    return result


def view_mounts(runtime,config):
    if (type(runtime) is not str or not re.fullmatch('/var/lib/droiddeck-capture/[a-f0-9]{64}/runtime',runtime)
            or type(config) is not str or not re.fullmatch('/var/lib/droiddeck-capture-views/[a-f0-9]{32}/config',config)):
        raise ValueError('Exact owned complete view paths required')
    # a(ssbt): source, destination, ignore missing, Linux mount flags. Default
    # recursive bind requires MS_REC=16384. No ignore/norec/different destinations.
    return sorted([[runtime,'/opt/droiddeck/runtime',False,16384],[config,'/etc/droiddeck',False,16384]])


def require_baseline(model):
    if (model.get('LoadState')!='loaded' or model.get('FragmentPath')!='/run/systemd/transient/'+VIDEO
            or model.get('Transient') is not True or model.get('NeedDaemonReload') is not False
            or model.get('DropInPaths') or model.get('PrivateMounts') is not False
            or model.get('BindPaths') or model.get('BindReadOnlyPaths')
            or model.get('Restart')!='always' or model.get('RestartUSec')!=2000000
            or model.get('KillMode')!='control-group' or model.get('User') not in ('','root')
            or model.get('Group') not in ('','root')):
        raise ValueError('Original unoverridden supervised video graph required')
    return model


def require_graph(baseline,current,*,runtime=None,config=None):
    require_baseline(baseline)
    if set(current)!=set(baseline):raise ValueError('Same complete graph projection required')
    expected=dict(baseline)
    if (runtime is None)!=(config is None):raise ValueError('Both complete directory binds required')
    if runtime is not None:
        expected.update(PrivateMounts=True,BindReadOnlyPaths=view_mounts(runtime,config),DropInPaths=[DROP])
    # Binding these paths on the supported single-root filesystem may add only
    # root-mount dependencies. No removed dependencies or arbitrary new edges.
    adjusted=dict(current)
    for name in ('Requires','After'):
        if not set(baseline[name])<=set(current[name]) or not (set(current[name])-set(baseline[name]))<={'-.mount'}:
            raise ValueError('Unexpected effective dependency change')
        adjusted[name]=baseline[name]
    if adjusted!=expected:raise ValueError('Command/environment/identity/namespace or mount graph changed')
    return True


def state_record(service,unit):
    # ControlGroup belongs to Service on the actual system bus, while systemctl
    # flattens both interfaces. Keep separate GetAll snapshots and verify epochs.
    invocation=bytes(unit['InvocationID']).hex()
    if len(invocation)!=32:raise ValueError('Exact unit invocation required')
    return {'ActiveState':E.text(unit['ActiveState']),'SubState':E.text(unit['SubState']),
        'InvocationID':invocation,'ControlGroup':E.text(service['ControlGroup']),
        'MainPID':E.integer(service['MainPID']),'NRestarts':E.integer(service['NRestarts'])}


def pam_session(service,session,user,uid):
    identity=E.text(session['Id']);scope=E.text(session['Scope'])
    account=plain(session['User'])
    if (E.text(service['PAMName'])!='droiddeck' or E.text(session['Service'])!='droiddeck'
            or E.text(session['Name'])!=user or not account or type(account[0]) is not int or account[0]!=uid
            or not re.fullmatch('[A-Za-z0-9_-]{1,64}',identity) or scope!='session-'+identity+'.scope'
            or E.boolean(session['Remote']) or E.text(session['State']) not in ('active','online')
            or E.integer(session['Leader'])<2):raise ValueError('Original local DroidDeck PAM session required')
    stable={k:plain(session[k]) for k in ('Id','User','Name','Service','Scope','Leader','Class','Type','Remote')}
    return stable,'/user.slice/user-'+str(uid)+'.slice/'+scope


class Bus:
    def __init__(self):
        if sys.platform!='linux' or os.geteuid()!=0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
            raise ValueError('Root Linux fixed system bus required')
        import dbus
        self.dbus=dbus;self.bus=dbus.SystemBus(private=True)
        self.manager=dbus.Interface(self.bus.get_object('org.freedesktop.systemd1','/org/freedesktop/systemd1'),'org.freedesktop.systemd1.Manager')
    def close(self):self.bus.close()
    def sample(self,name):
        if name not in (VIDEO,*PEERS):raise ValueError('Closed video/peer unit required')
        obj=self.bus.get_object('org.freedesktop.systemd1',self.manager.GetUnit(name,timeout=2))
        p=self.dbus.Interface(obj,'org.freedesktop.DBus.Properties')
        unit=p.GetAll('org.freedesktop.systemd1.Unit',timeout=2);service=p.GetAll('org.freedesktop.systemd1.Service',timeout=2)
        again=p.GetAll('org.freedesktop.systemd1.Unit',timeout=2);after=p.GetAll('org.freedesktop.systemd1.Service',timeout=2)
        dynamic=state_record(service,unit)
        if name!=VIDEO and dynamic!=state_record(after,again):raise ValueError('Guarded peer generation changed during sampling')
        if name==VIDEO and project(service,unit)!=project(after,again):raise ValueError('Video static graph changed during sampling')
        return dynamic,service,unit

    def source_session(self,pid,user,uid):
        _dynamic,service,_unit=self.sample(COMPOSITOR)
        manager=self.dbus.Interface(self.bus.get_object('org.freedesktop.login1','/org/freedesktop/login1'),'org.freedesktop.login1.Manager')
        path=manager.GetSessionByPID(pid,timeout=2)
        p=self.dbus.Interface(self.bus.get_object('org.freedesktop.login1',path),'org.freedesktop.DBus.Properties')
        session=p.GetAll('org.freedesktop.login1.Session',timeout=2)
        result=pam_session(service,session,user,uid)
        if manager.GetSessionByPID(pid,timeout=2)!=path or pam_session(service,p.GetAll('org.freedesktop.login1.Session',timeout=2),user,uid)!=result:
            raise ValueError('Compositor login session changed')
        return result
    def states(self):
        result={}
        for name in PEERS:
            dynamic,_service,_unit=self.sample(name)
            if dynamic['ActiveState']!='active' or dynamic['SubState']!='running' or dynamic['MainPID']<2:
                raise ValueError('Six active guarded peers required')
            result[name]=dynamic
        return result
    def video(self):
        dynamic,service,unit=self.sample(VIDEO)
        environment=[str(v) for v in service['Environment']]
        values={}
        for item in environment:
            key,sep,value=item.partition('=')
            if not sep or key in values:raise ValueError('Unique bounded environment names required')
            values[key]=value
        return dynamic,project(service,unit),values


def generation(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


class HeldFile:
    def __init__(self,stack,path,mode,maximum):
        self.path=Path(path);self.directories=[]
        parent=None;current=Path('/')
        for part in ('/',*self.path.parent.parts[1:]):
            if part!='/':current=current/part
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            stack.callback(os.close,fd);s=os.fstat(fd)
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('Protected root native ancestry required')
            self.directories.append((current,fd,(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)));parent=fd
        self.parent=parent
        self.fd=os.open(self.path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);stack.callback(os.close,self.fd)
        before=os.fstat(self.fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_gid!=0 or stat.S_IMODE(before.st_mode)!=mode
                or before.st_nlink!=1 or not 0<before.st_size<=maximum):raise ValueError('Exact owned native/config metadata required')
        self.identity=generation(before);self.raw=os.read(self.fd,maximum+1)
        if len(self.raw)!=before.st_size:raise ValueError('Bounded complete native/config bytes required')
        self.check()
    def check(self):
        for path,fd,original in self.directories:
            for s in (os.fstat(fd),path.lstat()):
                if (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)!=original:raise ValueError('Held native ancestry changed')
        if generation(os.fstat(self.fd))!=self.identity or generation(os.stat(self.path.name,dir_fd=self.parent,follow_symlinks=False))!=self.identity:
            raise ValueError('Held native/config file generation changed')


def process(pid,uid,unit,*,video=False,group=None):
    if type(pid) is not int or not 1<pid<2**31:raise ValueError('Exact process required')
    fd=os.open('/proc/'+str(pid),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        def read(name,limit):
            f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
            try:
                data=os.read(f,limit+1)
                if not 0<len(data)<=limit:raise ValueError('Bounded process metadata required')
                return data
            finally:os.close(f)
        def birth():
            value=read('stat',4096);head,sep,tail=value.rpartition(b') ');fields=tail.split()
            if not sep or not head.startswith(str(pid).encode()+b' (') or len(fields)<20 or fields[0] in (b'Z',b'X',b'x'):raise ValueError('Live process birth required')
            return int(fields[19])
        first=birth();status=read('status',65536).splitlines()
        if [v.split()[1:] for v in status if v.startswith(b'Uid:')]!=[[str(uid).encode()]*4]:raise ValueError('Exact process UID required')
        expected_group='/system.slice/'+unit if group is None else group
        if read('cgroup',4096)!=('0::'+expected_group+'\n').encode():raise ValueError('Exact owned process cgroup required')
        argv=read('cmdline',16384).split(b'\0')[:-1];exe=os.readlink('exe',dir_fd=fd)
        if video:
            if argv!=[b'/usr/bin/node',b'/opt/droiddeck/runtime/portable/worker-entry.js',b'compositor-video'] or exe!='/usr/bin/node':
                raise ValueError('Owned compositor-video launcher executable required')
        elif exe!='/usr/bin/weston' or b'--backend=pipewire' not in argv or b'--socket=wayland-teslaandroid' not in argv:
            raise ValueError('Private PipeWire Weston source required')
        if birth()!=first:raise ValueError('Process changed during inspection')
        return {'pid':pid,'birth':first,'argvSha256':sha(b'\0'.join(argv)),'exe':exe,'cgroupSha256':sha(expected_group.encode())}
    finally:os.close(fd)


def native_owner(raw):
    lease=json.loads(raw,object_pairs_hook=unique)
    if (type(lease) is not dict or set(lease)!={'version','lease'} or type(lease['version']) is not int or lease['version']!=1
            or type(lease['lease']) is not str or not re.fullmatch('[a-f0-9]{32}',lease['lease'])):raise ValueError('Exact native capture lease required')
    return lease['lease']


def require_native_role(owner,environment):
    if environment.get('TESLA_WAYDROID_RUNTIME_OWNER')!=owner or environment.get('TESLA_WAYDROID_RUNTIME_ROLE')!='video':
        raise ValueError('Video/native lease ownership mismatch')
    return True


class HeldArtifacts:
    def __init__(self,stack):
        self.files={}
        for name,(size,digest) in ARTIFACTS.items():
            held=HeldFile(stack,Path(name),0o644,size)
            if len(held.raw)!=size or sha(held.raw)!=digest:raise ValueError('Independently pinned native library bytes required')
            self.files[name]=held
        self.links={}
        for name,target in LINKS.items():
            p=Path('/opt/droiddeck/libgbinder')/name;s=p.lstat()
            if not stat.S_ISLNK(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink)!=(0,0,1) or os.readlink(p)!=target:
                raise ValueError('Exact original library soname link required')
            self.links[p]=(generation(s),target)
        self.check()
    def check(self):
        for held in self.files.values():held.check()
        for p,(identity,target) in self.links.items():
            if generation(p.lstat())!=identity or os.readlink(p)!=target:raise ValueError('Held native soname changed')
        return True


class NativeSource:
    def __init__(self,stack,bus):
        if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Root Linux held native source required')
        import pwd
        self.bus=bus;self.owner_pid=os.getpid();self.artifacts=HeldArtifacts(stack)
        self.config=HeldFile(stack,CONFIG,0o644,16384);config=json.loads(self.config.raw,object_pairs_hook=unique)
        if (type(config.get('user')) is not str or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}',config['user'])
                or type(config.get('uid')) is not int or config['uid']<1000 or type(config.get('gid')) is not int or config['gid']<1000):
            raise ValueError('Original nonroot runtime account required')
        account=pwd.getpwnam(config['user'])
        if (account.pw_uid,account.pw_gid,account.pw_dir)!=(config['uid'],config['gid'],config.get('home','/home/'+config['user'])):
            raise ValueError('Runtime account binding changed')
        self.user,self.uid=config['user'],config['uid']
        self.file=HeldFile(stack,LEASE,0o600,512);self.lease=native_owner(self.file.raw)
        self.fragment=HeldFile(stack,Path('/run/systemd/transient')/VIDEO,0o644,16384)
        self.peers=bus.states();self.boot=Path('/proc/sys/kernel/random/boot_id').read_text()
        self.session,self.source_group=bus.source_session(self.peers[COMPOSITOR]['MainPID'],self.user,self.uid)
        self.source=process(self.peers[COMPOSITOR]['MainPID'],self.uid,COMPOSITOR,group=self.source_group)
        self.handle=os.pidfd_open(self.source['pid']);stack.callback(os.close,self.handle)
        self.container=self.lxc_pid();self.check()
    def lxc_pid(self):
        value=subprocess.run(['/usr/bin/lxc-info','-P','/var/lib/waydroid/lxc','-n','waydroid','-pH'],capture_output=True,timeout=2)
        if value.returncode or not re.fullmatch(b'[1-9][0-9]{0,9}\n',value.stdout):raise ValueError('Live original container required')
        return int(value.stdout)
    def check(self):
        if os.getpid()!=self.owner_pid:raise ValueError('Current-process native lease required')
        self.file.check();self.config.check();self.fragment.check();self.artifacts.check()
        if Path('/proc/sys/kernel/random/boot_id').read_text()!=self.boot or self.bus.states()!=self.peers:raise ValueError('Boot or guarded peer changed')
        poll=select.poll();poll.register(self.handle,select.POLLIN|select.POLLERR|select.POLLHUP)
        if (poll.poll(0) or process(self.source['pid'],self.uid,COMPOSITOR,group=self.source_group)!=self.source
                or self.bus.source_session(self.source['pid'],self.user,self.uid)!=(self.session,self.source_group) or self.lxc_pid()!=self.container):
            raise ValueError('Original native/compositor source changed')
        _dynamic,_model,env=self.bus.video()
        require_native_role(self.lease,env)
        self.file.check();self.config.check();self.fragment.check();self.artifacts.check()
        return True
    def video_process(self):
        self.check();dynamic,_model,_env=self.bus.video()
        if dynamic['ActiveState']!='active' or dynamic['SubState']!='running':raise ValueError('Active exact video process required')
        result=process(dynamic['MainPID'],0,VIDEO,video=True);self.check()
        if self.bus.video()[0]!=dynamic:raise ValueError('Video invocation changed during executable inspection')
        return result
    def report(self):
        return {'kind':'held-video-native-source','bootSha256':sha(self.boot.encode()),'nativeLeaseSha256':sha(self.file.raw),
            'configurationSha256':sha(self.config.raw),'compositor':self.source,'containerPid':self.container,
            'nativeLibraryPins':{Path(n).name:d for n,(_size,d) in ARTIFACTS.items()},'peerGenerationsHeld':6,'pamSessionSha256':sha(encode(self.session)),'nativeLeaseOwnerMatched':True,'leaseNonceExported':False,'activationAllowed':False}


@contextmanager
def hold_native_source():
    with ExitStack() as stack:
        bus=Bus();stack.callback(bus.close);native=NativeSource(stack,bus)
        yield native
        native.check()


def main(args):
    if args!=['inspect']:raise ValueError('Read-only fixed video/native inspect only')
    with hold_native_source() as native:
        _state,model,_env=native.bus.video();require_baseline(model);proc=native.video_process()
        return {'kind':'read-only-capture-video-admission','graphSha256':sha(encode(model)),'native':native.report(),'videoProcess':proc,
            'captureGraphVerified':True,'signatureActivationVerified':False,'activationAllowed':False,'servicesChanged':False}


if __name__=='__main__':
    try:print(encode(main(sys.argv[1:])).decode().strip())
    except Exception as error:
        print('Capture video admission refused: '+type(error).__name__,file=sys.stderr);sys.exit(1)
