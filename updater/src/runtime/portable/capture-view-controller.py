# SPDX-License-Identifier: GPL-3.0-only
"""Signed video-only private-view trial. Durable rollback; no Android/settings writes."""
from contextlib import ExitStack
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import struct
import subprocess
import time

ROOT=Path('/var/lib/droiddeck-capture-switch')
BOOT=Path('/var/lib/droiddeck-capture-loader/bootstrap.py')
RECOVER='droiddeck-capture-recover@.service'
SOCKET=ROOT/'control.sock'
PHASES={'prepared','published','recycle-requested','accepted','restore-requested','restored'}
FILES=('portable/worker-entry.js','preview-gateway/compositor-h264.py','preview-gateway/compositor-video-launcher.js')
KEYS={'schema','kind','token','packageSha256','viewToken','view','phase','boot','peers','baseline','originalFiles',
      'mappingSha256','dropDirectoryIdentity','dropFileIdentity','videoBefore','after','socketIdentity'}


def encode(v):return (json.dumps(v,sort_keys=True,separators=(',',':'))+'\n').encode()
def sha(v):return hashlib.sha256(v).hexdigest()
def token(v):
    if type(v) is not str or not re.fullmatch('[a-f0-9]{32}',v):raise ValueError('Closed operation/view token required')
    return v
def pin(s):return [s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns]
def command(args,timeout=15):
    r=subprocess.run(args,capture_output=True,stdin=subprocess.DEVNULL,timeout=timeout,env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C','HOME':'/root'})
    if r.returncode or len(r.stdout)>1048576:raise ValueError('Fixed capture controller command failed')
    return r.stdout
def record(v):
    if (type(v) is not dict or set(v)!=KEYS or type(v['schema']) is not int or v['schema']!=1
        or v['kind']!='capture-private-view-operation' or v['phase'] not in PHASES):raise ValueError('Closed durable view operation required')
    token(v['token']);token(v['viewToken'])
    for k in ('packageSha256','mappingSha256'):
        if type(v[k]) is not str or not re.fullmatch('[a-f0-9]{64}',v[k]):raise ValueError('Exact operation digest required')
    if (not isinstance(v['baseline'],dict) or not isinstance(v['peers'],dict) or not isinstance(v['view'],dict)
        or not re.fullmatch('[a-f0-9-]{36}',v['boot']) or set(v['originalFiles'])!=set(FILES)):
        raise ValueError('Closed source/graph operation binding required')
    for value in v['originalFiles'].values():
        if not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Original worker code digest required')
    return v


class Store:
    def __init__(self,stack,S,*,create):
        import fcntl
        self.S=S;self.dirs=S.Dirs(stack);parent=self.dirs.open(ROOT.parent)
        if create:
            try:os.mkdir(ROOT.name,0o700,dir_fd=parent);os.fsync(parent)
            except FileExistsError:pass
        self.fd=S.private_dir(self.dirs,ROOT)
        try:fd=os.open('lock',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd);os.fsync(self.fd)
        except FileExistsError:fd=os.open('lock',os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self.fd)
        stack.callback(os.close,fd);s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink,s.st_size,stat.S_IMODE(s.st_mode))!=(0,0,1,0,0o600):raise ValueError('Owned zero-length operation lock required')
        self.lock,self.lock_pin=fd,pin(s);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.current=self.read();self.owner=os.getpid()
    def check(self):
        self.dirs.check()
        if os.getpid()!=self.owner or pin(os.fstat(self.lock))!=self.lock_pin or pin(os.stat('lock',dir_fd=self.fd,follow_symlinks=False))!=self.lock_pin:
            raise ValueError('Held controller lock changed')
        if self.read()!=self.current:raise ValueError('Durable operation generation changed')
        allowed={'lock','state.json','control.sock'}
        if any(n not in allowed and not re.fullmatch(r'history-[a-f0-9]{32}\.json',n) and not re.fullmatch(r'\.state-[a-f0-9]{32}',n) and not re.fullmatch(r'\.socket-[a-f0-9]{32}',n) for n in os.listdir(self.fd)):
            raise ValueError('Foreign controller namespace member')
        return True
    def read(self):
        try:raw,identity=self.S.read_at(self.fd,'state.json',131072,0o600)
        except FileNotFoundError:return None
        value=self.S.P.document(raw);record(value);return raw,identity
    def value(self):return None if self.current is None else record(self.S.P.document(self.current[0]))
    def save(self,v):
        record(v);self.check();name='.state-'+secrets.token_hex(16);self.S.write_new(self.fd,name,encode(v))
        self.check()
        if self.current is None:
            rename_new(self.fd,name,'state.json')
        else:os.replace(name,'state.json',src_dir_fd=self.fd,dst_dir_fd=self.fd);os.fsync(self.fd)
        self.current=self.read();self.check()
    def archive(self):
        self.check();v=self.value()
        if v is None or v['phase']!='restored':raise ValueError('Only verified restored operations may be archived')
        name='history-'+token(v['token'])+'.json'
        try:self.S.write_new(self.fd,name,self.current[0])
        except FileExistsError:
            if self.S.read_at(self.fd,name,131072,0o600)[0]!=self.current[0]:raise ValueError('Foreign operation history')
        os.unlink('state.json',dir_fd=self.fd);os.fsync(self.fd);self.current=None;self.check()


def rename_new(fd,a,b):
    libc=ctypes.CDLL(None,use_errno=True);fn=libc.renameat2
    fn.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint);fn.restype=ctypes.c_int
    if fn(fd,a.encode(),fd,b.encode(),1):
        e=ctypes.get_errno();raise OSError(e,os.strerror(e))
    os.fsync(fd)


MEDIA_JS=r"""
const W=require('/opt/droiddeck/runtime/preview-gateway/node_modules/ws');
const w=new W('ws://127.0.0.1:9092'),end=setTimeout(()=>process.exit(2),2200);
let configured=false,key=false,frames=0,settled=false;
w.on('message',(data,binary)=>{
 if(settled||!binary)return;
 if(data.length<13||data.length>2*1024*1024+12)process.exit(3);
 const flags=data.readBigUInt64BE(0),size=data.readUInt32BE(8);
 if(size!==data.length-12)process.exit(4);
 if(flags&(1n<<62n))configured=true;
 else {frames++;if(flags&(1n<<61n))key=true;}
 if(configured&&key&&frames>=1){settled=true;clearTimeout(end);process.stdout.write(JSON.stringify({configuration:true,keyFrame:true,frames}));w.close();w.terminate();}
});
w.on('error',()=>{if(!settled)process.exit(5);});
"""


class Engine:
    def __init__(self,c,V,N,R,view,native,store,*,checkpoint=lambda event:None):
        self.c,self.V,self.N,self.R,self.view,self.n,self.store=c,V,N,R,view,native,store
        self.b=R.Backend();self.checkpoint=checkpoint;self.op=store.value();self.graph_target=None
        self.mapping=view['mapping'];self.drop=Path(N.DROP);self.drop_fd=None
        if view['report']['componentSha256']!=c['statement']['componentSha256']:raise ValueError('View is not the signed runtime component')
    def guard(self):
        self.c['check']();self.view['check']();self.n.check();self.store.check()
        if self.op is not None:
            if self.op['packageSha256']!=self.c['statement']['packageSha256'] or self.op['viewToken']!=self.view['report']['token'] or self.op['view']!=self.view['report']:
                raise ValueError('Operation does not belong to held signed view')
            if self.op['boot']!=self.n.boot.strip() or self.op['peers']!=self.n.peers:raise ValueError('Fresh independent recovery admission required')
        return True
    def graph(self,*,pending=False):
        self.guard();_d,current,_env=self.n.bus.video()
        if pending:current={**current,'NeedDaemonReload':False}
        if self.graph_target=='mapped':
            self.N.require_graph(self.op['baseline'],current,runtime=self.view['runtimeRoot'],config=self.view['configRoot'])
        else:self.N.require_graph(self.op['baseline'],current)
        return True
    def snapshot(self,current,*,mapped):
        proc=self.n.video_process();pid=proc['pid'];root=Path('/proc')/str(pid)/'root'
        mounts={}
        for line in (Path('/proc')/str(pid)/'mountinfo').read_text().splitlines():
            f=line.split()
            if f[4] in ('/opt/droiddeck/runtime','/etc/droiddeck'):mounts[f[4]]=f[5].split(',')
        if mapped:
            for dest,src in (('/opt/droiddeck/runtime',self.view['runtimeRoot']),('/etc/droiddeck',self.view['configRoot'])):
                a,b=(root/dest.lstrip('/')).stat(),Path(src).stat()
                if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino) or 'ro' not in mounts.get(dest,[]):return False
        elif mounts:return False
        files={n:sha((root/'opt/droiddeck/runtime'/n).read_bytes()) for n in FILES}
        expected={n:sha((Path(self.view['runtimeRoot'])/n).read_bytes()) for n in FILES} if mapped else self.op['originalFiles']
        if files!=expected:return False
        if proc!=self.n.video_process():raise ValueError('Ready worker generation changed')
        return True
    def manual_cpu_ready(self):
        # Execute only the independently authenticated, held view's status reader.
        # That reader checks root ownership/no-follow, freshness, boot and PID birth;
        # additionally bind the report to THIS exact video service generation.
        proc=self.n.video_process()
        reader=str(Path(self.view['runtimeRoot'])/'preview-gateway/video-encoding.js')
        script="process.stdout.write(JSON.stringify(require(process.argv[1]).readStatus()))"
        try:
            value=json.loads(command(['/usr/bin/node','-e',script,reader],3))
        except (ValueError,subprocess.TimeoutExpired):return False
        return (value.get('preference')=='cpu' and value.get('effective')=='cpu'
            and value.get('gpuAvailable') is True and value.get('pid')==proc['pid']
            and value.get('birth')==str(proc['birth']) and value.get('boot')==self.n.boot.strip()
            and proc==self.n.video_process())
    def ready(self,current,*,mapped):
        self.guard()
        try:exact=self.snapshot(current,mapped=mapped)
        except (FileNotFoundError,ProcessLookupError,ValueError):return False
        if not exact:return False
        inv=current['InvocationID']
        if not re.fullmatch('[a-f0-9]{32}',inv):raise ValueError('Exact ready invocation required')
        logs=command(['/usr/bin/journalctl','--no-pager','-o','cat','-n','48','_SYSTEMD_INVOCATION_ID='+inv],5)
        if b'Compositor H264 configuration and first key frame ready' not in logs:return False
        manual_cpu=mapped and self.view['report']['mode']=='gpu' and self.manual_cpu_ready()
        if mapped and not manual_cpu and self.view['report']['mode']=='gpu' and b'Experimental GPU capture unavailable; using CPU capture' in logs:
            raise ValueError('GPU preprocessing failed; prior CPU worker will be restored')
        if mapped and not manual_cpu and self.view['report']['mode']=='gpu':
            if any(marker in logs for marker in (b'Experimental VAAPI encoder unavailable',b'Experimental VAAPI stream unavailable',b'Experimental GPU resize active')):
                raise ValueError('Initial hardware encoder admission failed; prior CPU worker will be restored')
            if b'Experimental VAAPI H264 encoder active' not in logs:return False
        try:
            result=json.loads(command(['/usr/bin/nsenter','-t',str(self.n.container),'-n','/usr/bin/node','-e',MEDIA_JS],3))
        except (ValueError,subprocess.TimeoutExpired):return False
        if result.get('configuration') is not True or result.get('keyFrame') is not True:return False
        self.guard()
        if manual_cpu and not self.manual_cpu_ready():return False
        return True
    def ensure(self,mapped,*,force=False):
        self.graph_target='mapped' if mapped else 'original'
        checked=self.n.video_process()  # never signal an exec-pending or unknown launcher
        with self.R.hold_video_recycle(admit=self.guard,owned_graph=self.graph,_backend=self.b) as cycle:
            if (int(cycle.before['MainPID']),cycle.birth)!=(checked['pid'],checked['birth']):
                raise ValueError('Exact checked Node generation changed before cycle admission')
            if not force and self.ready(cycle.before,mapped=mapped):
                cycle._original();return {'recycled':False,'after':cycle.before}
            result=cycle.run(lambda current:self.ready(current,mapped=mapped))
            return {'recycled':True,'after':result['after']}
    def staged_directory(self):
        return self.drop.parent.parent/('.droiddeck-capture-view-'+self.op['token'])
    def directory(self,*,create=False,staged=False):
        parent=self.store.dirs.open(self.drop.parent.parent)
        path=self.staged_directory() if staged or create else self.drop.parent
        if create:
            if os.path.lexists(self.drop.parent) or os.path.lexists(path):raise ValueError('Foreign mapping directory')
            os.mkdir(path.name,0o755,dir_fd=parent);os.fsync(parent)
            self.checkpoint('directory-created-before-receipt')
        fd=self.store.dirs.open(path);info=os.fstat(fd)
        identity=[info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid]
        if self.op['dropDirectoryIdentity'] is None:
            if not create:raise ValueError('Uncommitted staged directory preserved')
            self.op['dropDirectoryIdentity']=identity
        elif self.op['dropDirectoryIdentity']!=identity:raise ValueError('Owned mapping directory changed')
        if set(os.listdir(fd))-{self.drop.name}:raise ValueError('Foreign view mapping member')
        return fd
    def retire_directory(self,path):
        fd=self.directory(staged=path!=self.drop.parent)
        try:self.mapping_pin(fd,self.drop.name)
        except FileNotFoundError:pass
        else:os.unlink(self.drop.name,dir_fd=fd);os.fsync(fd)
        if os.listdir(fd):raise ValueError('Unexpected mapping directory content')
        parent=self.store.dirs.open(path.parent);os.rmdir(path.name,dir_fd=parent);os.fsync(parent)
        self.store.dirs.opened.pop(path)
    def mapping_pin(self,fd,name,*,created=False):
        raw,identity=self.V.S.read_at(fd,name,4096,0o600);s=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if raw!=self.mapping or sha(raw)!=self.op['mappingSha256']:raise ValueError('Owned mapping bytes differ')
        actual=pin(s)
        if self.op['dropFileIdentity'] is None and not created:raise ValueError('Uncommitted mapping file must be preserved')
        if self.op['dropFileIdentity'] is not None and actual!=self.op['dropFileIdentity']:raise ValueError('Mapping inode/generation differs')
        return actual
    def save(self,phase):
        self.op['phase']=phase;self.store.save(self.op);self.checkpoint('durable-'+phase)
    def original_files(self,proc):
        root=Path('/proc')/str(proc['pid'])/'root/opt/droiddeck/runtime'
        return {n:sha((root/n).read_bytes()) for n in FILES}
    def change(self):
        if self.op is not None:
            if self.op['phase']!='restored':raise ValueError('Previous view operation requires recovery')
            cleanup_socket(self)
            self.store.archive();self.op=None
        self.guard();_d,base,_env=self.n.bus.video();self.N.require_baseline(base)
        proc=self.n.video_process();original=self.original_files(proc);self.guard()
        if self.n.video_process()!=proc:raise ValueError('Original worker changed')
        self.op={'schema':1,'kind':'capture-private-view-operation','token':secrets.token_hex(16),
            'packageSha256':self.c['statement']['packageSha256'],'viewToken':self.view['report']['token'],'view':self.view['report'],
            'phase':'prepared','boot':self.n.boot.strip(),'peers':self.n.peers,'baseline':base,'originalFiles':original,
            'mappingSha256':sha(self.mapping),'dropDirectoryIdentity':None,'dropFileIdentity':None,
            'videoBefore':self.b.observe(self.b.video_unit),'after':None,'socketIdentity':None}
        self.store.save(self.op);self.checkpoint('intent-durable')
        try:
            fd=self.directory(create=True);self.V.S.write_new(fd,self.drop.name,self.mapping)
            self.op['dropFileIdentity']=self.mapping_pin(fd,self.drop.name,created=True);self.save('prepared')
            self.graph_target='original';self.graph()
            parent=self.store.dirs.open(self.drop.parent.parent);staged=self.staged_directory()
            rename_new(parent,staged.name,self.drop.parent.name)
            row=self.store.dirs.opened.pop(staged)
            self.store.dirs.opened[self.drop.parent]=(parent,self.drop.parent.name,row[2],row[3])
            self.checkpoint('mapping-published')
            self.save('published');self.graph(pending=True)
            command(['/usr/bin/systemctl','daemon-reload']);self.graph_target='mapped';self.graph()
            self.save('recycle-requested');result=self.ensure(True,force=True)
            self.op['after']=result['after'];self.save('accepted')
            return {'phase':'accepted','mode':self.view['report']['mode'],'videoOnly':True,'reversible':True,'updaterLeaseHeld':True}
        except Exception:
            self.restore();raise
    def rebind(self):
        # Independent signed release/view/native checks already completed afresh.
        # Only a native-owner nonce may differ in the effective environment.
        self.c['check']();self.view['check']();self.n.check();self.store.check()
        _d,current,_env=self.n.bus.video();base=dict(self.op['baseline'])
        if current['StableEnvironmentSha256']!=base['StableEnvironmentSha256']:raise ValueError('Non-native worker environment changed')
        base['EnvironmentSha256']=current['EnvironmentSha256']
        normalized={**current,'NeedDaemonReload':False}
        try:self.N.require_graph(base,normalized)
        except ValueError:self.N.require_graph(base,normalized,runtime=self.view['runtimeRoot'],config=self.view['configRoot'])
        self.op['baseline']=base;self.op['boot']=self.n.boot.strip();self.op['peers']=self.n.peers
        self.store.save(self.op)
    def restore(self):
        if self.op is None:return {'phase':'no-operation'}
        self.guard();self.save('restore-requested')
        if os.path.lexists(self.drop.parent):
            fd=self.directory()
            try:self.mapping_pin(fd,self.drop.name)
            except FileNotFoundError:pass
            else:os.unlink(self.drop.name,dir_fd=fd);os.fsync(fd)
            self.checkpoint('mapping-removed-worker-pending')
        staged=self.staged_directory()
        if os.path.lexists(staged) and self.op['dropDirectoryIdentity'] is not None:
            self.retire_directory(staged)
        self.graph_target='original'
        command(['/usr/bin/systemctl','daemon-reload']);self.graph()
        deadline=time.monotonic()+15
        while True:
            try:result=self.ensure(False);break
            except ValueError:
                if time.monotonic()>=deadline:raise
                time.sleep(.2);self.guard()
        self.op['after']=result['after']
        if os.path.lexists(self.drop.parent):self.retire_directory(self.drop.parent)
        self.checkpoint('directory-removed-before-restored')
        self.save('restored')
        return {'phase':'restored','mode':'cpu','videoOnly':True,'reversible':True,'recycled':result['recycled']}


def native_gpu_boundary():
    # Signed builtins-only reader. No guest setting writes, media/data export or
    # dependence on mutable installed display code.
    raw=command(['/usr/bin/node',str(Path(__file__).with_name('gpu-native.js'))],35)
    value=json.loads(raw)
    if value.get('nativeGpuVerified') is not True:raise ValueError('Accepted native VirGL/software codec4/RGBX setup required')
    return {k:value[k] for k in ('containerIdentity','androidIdentity')}


def notify_ready():
    address=os.environ.get('NOTIFY_SOCKET')
    if type(address) is not str or not address or not (address.startswith('/') or address.startswith('@')):raise ValueError('Owned notify service required')
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as s:s.connect('\0'+address[1:] if address.startswith('@') else address);s.sendall(b'READY=1')


def serve(engine):
    path=SOCKET
    staged=ROOT/('.socket-'+engine.op['token'])
    identity=None
    if os.path.lexists(path):raise ValueError('Foreign control socket')
    stopping=[False]
    def stop(_sig,_frame):stopping[0]=True
    prior={sig:signal.signal(sig,stop) for sig in (signal.SIGTERM,signal.SIGINT)}
    listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try:
        listener.bind(str(staged));os.chmod(staged,0o600);identity=staged.lstat().st_ino
        engine.checkpoint('socket-created-before-receipt')
        engine.op['socketIdentity']=pin(staged.lstat());engine.store.save(engine.op)
        rename_new(engine.store.fd,staged.name,path.name)
        engine.checkpoint('socket-published')
        listener.listen(4);listener.settimeout(1)
        notify_ready()
        while not stopping[0]:
            try:conn,_=listener.accept()
            except socket.timeout:continue
            with conn:
                conn.settimeout(2);pid,uid,gid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid!=0:continue
                raw=conn.recv(513)
                if raw not in (b'status\n',b'restore\n'):continue
                if raw==b'status\n':
                    engine.guard();conn.sendall(encode({'phase':'accepted','mode':engine.view['report']['mode'],'updaterLeaseHeld':True}))
                else:
                    result=engine.restore();conn.sendall(encode(result));return result
        return engine.restore()
    finally:
        listener.close()
        if os.path.lexists(path) and identity is not None:
            s=path.lstat()
            if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=0 or s.st_ino!=identity:raise ValueError('Owned socket changed')
            path.unlink()
        for sig,handler in prior.items():signal.signal(sig,handler)


def cleanup_socket(engine):
    for path in (SOCKET,ROOT/('.socket-'+engine.op['token'])):
        if not os.path.lexists(path):continue
        info=path.lstat()
        if engine.op['socketIdentity'] is None and path!=SOCKET:
            # An uncommitted hidden socket was never the control endpoint.
            continue
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0 or info.st_gid!=0
            or engine.op['socketIdentity'] is None or pin(info)!=engine.op['socketIdentity']):
            raise ValueError('Unknown socket inode preserved after safe worker restoration')
        path.unlink();os.fsync(engine.store.fd)


def client(action):
    if action not in ('status','restore') or os.geteuid()!=0:raise ValueError('Root-only fixed control action')
    s=SOCKET.lstat()
    if not stat.S_ISSOCK(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or stat.S_IMODE(s.st_mode)!=0o600:raise ValueError('Owned private control socket required')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
        c.settimeout(60);c.connect(str(SOCKET))
        _pid,uid,_gid=struct.unpack('3i',c.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if uid!=0:raise ValueError('Owned root server required')
        c.sendall((action+'\n').encode());out=c.recv(4097)
        if not 0<len(out)<=4096:raise ValueError('Bounded controller reply required')
        return json.loads(out)


def dispatch(c,action,args):
    # All modules loaded before original release verifier installs its own closed
    # import scope. No package code outside the signed loader map is imported.
    V=c['module']('capture-private-view.py');N=c['module']('capture-video-admission.py');R=c['module']('capture-video-recycle.py')
    if action in ('status','restore') and os.path.lexists(SOCKET):
        try:return client(action)
        except ConnectionRefusedError:pass
    if action=='change':
        if len(args)!=1:raise ValueError('One owned view token required')
        view=token(args[0]);digest=c['statement']['packageSha256']
        # A root-installed, hash-pinned loader and exact recovery template must
        # be enrolled by the deployment adapter before any real worker change.
        if Path(__file__).parts[:1]!=('/',) or not BOOT.is_file():raise ValueError('Production loader enrollment required')
        expected=RECOVER_TEXT
        if Path('/etc/systemd/system',RECOVER).read_bytes()!=expected:raise ValueError('Exact recovery service required')
        unit='droiddeck-capture-trial-'+secrets.token_hex(16)+'.service'
        command(['/usr/bin/systemd-run','--unit='+unit,'--property=Type=notify','--property=NotifyAccess=main',
            '--property=TimeoutStartSec=120','--property=TimeoutStopSec=60','--property=OnFailure=droiddeck-capture-recover@'+digest+'.service',
            '/usr/bin/python3','-I','-B',str(BOOT),'run',digest,view],130)
        return client('status')
    if action=='status' and not ROOT.exists():return {'phase':'no-operation','mode':'cpu'}
    if action not in ('run','recover','restore','status'):raise ValueError('Closed capture action required')
    if action=='run':
        if len(args)!=1:raise ValueError('One explicit owned view token required')
        view_token=token(args[0])
        group=Path('/proc/self/cgroup').read_text()
        if not re.fullmatch(r'0::/system.slice/droiddeck-capture-trial-[a-f0-9]{32}\.service\n',group):raise ValueError('Owned supervised trial service required')
    else:
        if args:raise ValueError('Fixed recovery/status requires no arguments')
        if not ROOT.exists():return {'phase':'no-operation','mode':'cpu'}
        # Read identifier only; full root-owned state validation follows under lock.
        raw=ROOT.joinpath('state.json').read_bytes() if ROOT.joinpath('state.json').exists() else None
        if raw is None:return {'phase':'no-operation','mode':'cpu'}
        v=record(json.loads(raw));view_token=v['viewToken']
        if v['packageSha256']!=c['statement']['packageSha256']:raise ValueError('Original signed package required for recovery')
    with V.hold_view(view_token) as view,N.hold_native_source() as native,ExitStack() as stack:
        store=Store(stack,V.S,create=action=='run');engine=Engine(c,V,N,R,view,native,store)
        if action=='run':
            native_gpu_boundary()
            engine.change()
            try:return serve(engine)
            except Exception:engine.restore();raise
        if engine.op is None:return {'phase':'no-operation'}
        if engine.op['boot']!=native.boot.strip() or engine.op['peers']!=native.peers:engine.rebind()
        if action=='status':return {'phase':engine.op['phase'],'mode':view['report']['mode'],'updaterLeaseHeld':False}
        if action=='recover' and engine.op['phase']=='accepted' and engine.op['boot']==native.boot.strip():
            # No healthy expiry. Without a surviving owner process, recover to
            # original CPU so the updater lock can be released consistently.
            pass
        result=engine.restore()
        cleanup_socket(engine)
        return result


RECOVER_TEXT=('[Unit]\nDescription=DroidDeck owned capture recovery\nAfter=network.target droiddeck-update-boot.service tesla-waydroid-runtime.service\nStartLimitIntervalSec=0\n'
    '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 -I -B '+BOOT.as_posix()+' recover %i\n'
    'Restart=on-failure\nRestartSec=3\nTimeoutStartSec=120\n'
    '[Install]\nWantedBy=multi-user.target\n').encode()
