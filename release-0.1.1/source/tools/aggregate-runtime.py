# SPDX-License-Identifier: GPL-3.0-only
"""Admitted aggregate runtime operations; no import-time activation.

Original signed core/capture authority owns video operations. This coordinator
owns only its journal, public browser view, recovery unit and temporary guard.
"""
from contextlib import ExitStack,contextmanager
import base64,secrets,errno,signal
import urllib.error
import hashlib,io,ipaddress,json,os,re,ssl,socket,stat,subprocess,sys,tarfile,time,types,urllib.request
from pathlib import Path

RUNTIME='tesla-waydroid-runtime.service'
GATEWAY='tesla-waydroid-gateway-preview.service'
PREFERENCE='droidmate-gpu-default.service'
PEERS=tuple('tesla-waydroid-'+n+'.service' for n in ('runtime','compositor','video-preview','audio-preview','gateway-preview','video-relay-preview','audio-relay-preview'))
ROOT=Path('/var/lib/droidmate-aggregate')
PUBLIC=Path('/var/lib/droidmate-browser')
CONFIG=Path('/etc/droiddeck/runtime.json')
GRAPH=('FragmentPath','DropInPaths','BindPaths','BindReadOnlyPaths','PrivateMounts','RootDirectory','RootImage','TemporaryFileSystem')
ENV={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'}


def sha(raw):return hashlib.sha256(raw).hexdigest()

def command(args,timeout=30):
    if not isinstance(args,list) or args[:1]!=['/usr/bin/systemctl']:raise ValueError('Fixed system bus command required')
    p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,env=ENV,cwd='/',timeout=timeout)
    if p.returncode or len(p.stdout)>262144 or len(p.stderr)>16384:raise ValueError('Original systemd operation refused')
    return p.stdout.decode('utf-8')


def fields(unit,names=GRAPH):
    if unit not in {*PEERS,PREFERENCE,'droidmate-aggregate-recovery.service','tesla-waydroid-display-helper-preview.service'}:raise ValueError('Closed aggregate service name required')
    raw=command(['/usr/bin/systemctl','show',unit,'--property='+','.join(names)])
    values={}
    for line in raw.splitlines():
        name,sep,value=line.partition('=')
        if not sep or name not in names or name in values:raise ValueError('Closed effective unit observation required')
        values[name]=value
    if set(values)!=set(names):raise ValueError('Complete effective graph required')
    return values


class NoBrowserRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

@contextmanager
def browser_deadline(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('Original browser connection budget exhausted')
    if signal.getitimer(signal.ITIMER_REAL)!=(0.,0.):raise ValueError('Existing process timer preserved')
    previous=signal.getsignal(signal.SIGALRM)
    def expired(_number,_frame):raise TimeoutError('Original browser connection budget exhausted')
    signal.signal(signal.SIGALRM,expired)
    try:
        signal.setitimer(signal.ITIMER_REAL,remaining);yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)

def browser_route(opener,url):
    # Retry only listener startup within the original 12-second HTTP budget.
    deadline=time.monotonic()+12
    with browser_deadline(deadline):
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Original browser connection budget exhausted')
            try:
                with opener.open(url,timeout=remaining)as response:
                    raw=response.read(1048577)
                    if response.status!=200 or len(raw)>1048576:raise ValueError('Original TLS browser route did not become healthy')
                    if time.monotonic()>=deadline:raise TimeoutError('Original browser connection budget exhausted')
                    return raw
            except urllib.error.URLError as error:
                if isinstance(error,urllib.error.HTTPError):error.close();raise
                if not isinstance(error.reason,OSError)or isinstance(error.reason,ssl.SSLError)or error.reason.errno not in (errno.ECONNREFUSED,errno.ECONNRESET):raise
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Original browser connection budget exhausted')
            time.sleep(min(.1,remaining))


def recovery_unit(ctx):
    root=ctx['root'];digest=ctx['release']['packageSha256']
    if root!=Path('/usr/local/share/droidmate-rc18-'+digest) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Original enrolled source required')
    return ('[Unit]\nDescription=DroidMate owned aggregate installation recovery\n'
        'After=tesla-waydroid-runtime.service droiddeck-capture-recover@e140f8c4c989aa434c967b17a82a79a52efb1c6a0f421a0f7c35115827788fe6.service\n'
        'ConditionPathExists='+str(ROOT/digest/'intent.json')+'\n'
        '[Service]\nType=oneshot\nUMask=0077\nExecStart=/usr/bin/python3 -I -B '+str(root/'source/tools/aggregate-installer-entry.py')+' --enrolled recover\n'
        'TimeoutStartSec=900\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n').encode('ascii')


def gateway_graph(baseline,actual,mapping,source,target):
    if set(baseline)!=set(GRAPH) or set(actual)!=set(GRAPH):raise ValueError('Closed original gateway graph required')
    expected={**baseline,'DropInPaths':mapping,'PrivateMounts':'yes','BindReadOnlyPaths':source+':'+target+':rbind'}
    if actual!=expected:raise ValueError('Original gateway graph plus exact owned mapping required')
    return True



class MediaStarting(ValueError):
    """A real HTTP 503, never a successful media proof."""


def stream_upgrade(host,port,path,context):
    """Check the real media upgrade; stop before reading any frame payload."""
    if (not re.fullmatch(r'/sockets/(display|audio)\?session=[a-f0-9]{64}(?:&format=h264)?',path)
            or type(port) is not int or not 1024<=port<=65535 or str(ipaddress.IPv4Address(host))!=host):
        raise ValueError('Closed LAN media upgrade required')
    key=base64.b64encode(secrets.token_bytes(16)).decode('ascii');authority=host+':'+str(port)
    request=('GET '+path+' HTTP/1.1\r\nHost: '+authority+'\r\nOrigin: https://'+authority+
             '\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: '+key+
             '\r\nSec-WebSocket-Version: 13\r\n\r\n').encode('ascii')
    with socket.create_connection((host,port),timeout=10) as raw:
        with context.wrap_socket(raw,server_hostname=host) as stream:
            stream.sendall(request);reply=bytearray()
            while not reply.endswith(b'\r\n\r\n'):
                if len(reply)>=8192:raise ValueError('Bounded media upgrade headers required')
                part=stream.recv(1)
                if not part:raise ValueError('Media upgrade ended before headers')
                reply.extend(part)
            rows=bytes(reply).decode('ascii').split('\r\n')
            headers={}
            for line in rows[1:]:
                if not line:continue
                name,sep,value=line.partition(':')
                if not sep or name.lower() in headers:raise ValueError('Unambiguous media upgrade required')
                headers[name.lower()]=value.strip()
            if re.fullmatch(r'HTTP/1\.1 503 [ -~]+',rows[0]):raise MediaStarting('Live media temporarily unavailable')
            if rows[0]!='HTTP/1.1 101 Switching Protocols':raise ValueError('Live media WebSocket upgrade refused')
            accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
            if (headers.get('upgrade','').lower()!='websocket' or headers.get('connection','').lower()!='upgrade'
                    or headers.get('sec-websocket-accept')!=accept):raise ValueError('Original media protocol upgrade proof required')
            stream.sendall(b'\x88\x80'+secrets.token_bytes(4))
    return True



def audio_enabled(opener,url,token):
    # browserAudioState is a POST-only setting mutation, never a health probe.
    request=urllib.request.Request(url+'/api/configuration',headers={'X-Display-Session':token})
    with opener.open(request,timeout=12) as response:raw=response.read(16385)
    if len(raw)>16384:raise ValueError('Bounded original browser configuration required')
    value=json.loads(raw)
    if (type(value) is not dict or type(value.get('browserAudioSupported')) is not int or value['browserAudioSupported']!=1
            or type(value.get('persist.tesla-android.browser_audio.is_enabled')) is not int
            or value['persist.tesla-android.browser_audio.is_enabled'] not in (0,1)):
        raise ValueError('Original browser audio availability required')
    return value['persist.tesla-android.browser_audio.is_enabled']==1


def browser_media(opener,url,host,port,context,deadline,check):
    # Retain the cumulative NETWORK budget. Original source/lease verification
    # can be expensive on an updated VM; it remains mandatory outside IO alarms.
    remaining=deadline-time.monotonic()
    while True:
        if remaining<=0:raise TimeoutError('Original media readiness budget exhausted')
        check()
        deadline=time.monotonic()+remaining
        with browser_deadline(deadline):
            request=urllib.request.Request(url+'/api/displaySession',data=b'{}',headers={'Origin':url,'Content-Type':'application/json'},method='POST')
            with opener.open(request,timeout=min(12,remaining)) as response:
                raw=response.read(4097)
                if response.status!=200 or len(raw)>4096:raise ValueError('Bounded original browser session required')
                session=json.loads(raw)
            if type(session) is not dict or not re.fullmatch('[a-f0-9]{64}',session.get('token','')):
                raise ValueError('Original temporary browser session required for media check')
            token=session['token']
            request=urllib.request.Request(url+'/api/displayState',headers={'X-Display-Session':token})
            with opener.open(request,timeout=min(12,max(.001,deadline-time.monotonic()))) as response:
                raw=response.read(16385)
                if response.status!=200 or len(raw)>16384:raise ValueError('Bounded original display state required')
                state=json.loads(raw)
            if (type(state) is not dict or state.get('recoveryRequired') is not False
                    or state.get('transactionStatus') not in ('idle','transferring','resizing')):
                raise ValueError('Original display authority is not healthy')
            if state['transactionStatus']=='idle':
                try:
                    stream_upgrade(host,port,'/sockets/display?session='+token+'&format=h264',context)
                    if audio_enabled(opener,url,token):stream_upgrade(host,port,'/sockets/audio?session='+token,context)
                    if time.monotonic()>=deadline:raise TimeoutError('Original media readiness budget exhausted')
                    break
                except MediaStarting:
                    pass
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('Original media readiness budget exhausted')
            time.sleep(min(1,remaining))
        remaining=deadline-time.monotonic()
    check();return True


def load_material(ctx):
    # The signed entry retains and checks the complete source set around loading.
    if ctx['check']() is not True:raise ValueError('Original signed aggregate source required')
    path=ctx['root']/'source/tools/aggregate-material.py';raw=path.read_bytes()
    if ctx['check']() is not True:raise ValueError('Original source changed before compilation')
    m=types.ModuleType('admitted_aggregate_material');m.__file__=str(path)
    exec(compile(raw,str(path),'exec'),m.__dict__)
    if ctx['check']() is not True:raise ValueError('Original source changed during compilation')
    return m


CONFIG_DIGEST_PINS={
 'portable/configuration-generation.js':'971154e50a16d556c1488695b9a15467600ffdba55fdcb3e2bb29e53a59ff78b',
 'portable/runtime-config.js':'dc87ee44c58eecfb8fa73db775f99223be7180e24ba3d039fc81e1bba5ae79ce',
 'portable/read-bounded.js':'91eb96125edddb2f5f49b87732bf9c36f5342ff882b5f67a7c0a855e41071a56',
 'browser-connection/policy.js':'e71f0bb2b3193716d40a9d52bd64334265d79c499e6c9531d9f06a79f9306cbb',
}


def portable_configuration_digest(material,configuration,*,_run=subprocess.run):
    # Use the ORIGINAL pure normalizer; its canonical JSON differs from file bytes.
    runtime=material.compiler.capture_runtime(material.assets['capturePackage']);sources={}
    for name,pin in CONFIG_DIGEST_PINS.items():
        raw=runtime[name]
        if type(raw) is not bytes or sha(raw)!=pin:raise ValueError('Original configuration normalizer required')
        sources[name]=raw.decode('utf-8')
    program=r"""
const fs=require('node:fs'),path=require('node:path').posix;
try {
 const input=JSON.parse(fs.readFileSync(0,'utf8')),cache=Object.create(null);
 const allowed=new Set(['node:crypto','node:fs','node:url','node:net']);
 function load(name) {
  if(cache[name])return cache[name].exports;
  if(!Object.hasOwn(input.sources,name))throw Error('Unknown original module');
  const m={exports:{}};cache[name]=m;
  function local(id) {
   if(allowed.has(id))return require(id);
   if(!id.startsWith('.'))throw Error('Unknown original import');
   return load(path.normalize(path.join(path.dirname(name),id))+'.js');
  }
  new Function('require','module','exports',input.sources[name])(local,m,m.exports);
  return m.exports;
 }
 process.stdout.write(load('portable/configuration-generation.js').configurationDigest(input.configuration)+'\n');
} catch(_) {process.exitCode=1;}
"""
    payload=json.dumps({'sources':sources,'configuration':configuration},separators=(',',':'),allow_nan=False).encode()
    if len(payload)>65536:raise ValueError('Bounded original configuration inputs required')
    result=_run(['/usr/bin/node','-e',program],input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=ENV,cwd='/',timeout=15)
    if result.returncode or len(result.stdout)!=65 or len(result.stderr)>8192:raise ValueError('Original configuration digest refused')
    digest=result.stdout.decode('ascii').removesuffix('\n')
    if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Canonical original configuration digest required')
    return digest


def portable_gateway_mapping(base,digest):
    if (type(base) is not bytes or not base.startswith(b'[Service]\n') or not base.endswith(b'\n')
            or b'Environment=DROIDDECK_' in base or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}',digest)):
        raise ValueError('Original portable mapping and canonical generation required')
    return base+b'Environment=DROIDDECK_PORTABLE_RUNTIME=1\nEnvironment=DROIDDECK_CONFIGURATION_SHA256='+digest.encode('ascii')+b'\n'


def guest_helpers(material):
    """Compile only independently pinned files from the immutable guest asset."""
    pins={'runtime/portable/capture-boot-preference.py':'074c80eec62a79f6f80efd810d4413a7730cf25a6bc750218f008bd36a124df8',
          'tools/install-gpu-boot-preference.py':'12a4a107f0b5fce0f69a63a387109a07a0a0edfa9de01076940fe187e35b5252'}
    code={}
    with tarfile.open(fileobj=io.BytesIO(material.assets['gpuGuest']),mode='r:') as archive:
        for name,pin in pins.items():
            row=archive.getmember('source/'+name)
            if row.type!=tarfile.REGTYPE or not 0<row.size<=65536:raise ValueError('Original closed guest enrollment source required')
            raw=archive.extractfile(row).read()
            if sha(raw)!=pin:raise ValueError('Original guest enrollment source pin required')
            code[name]=raw
    installer=types.ModuleType('aggregate_original_guest_installer');installer.__file__='<authenticated-guest-installer>'
    exec(compile(code['tools/install-gpu-boot-preference.py'],installer.__file__,'exec'),installer.__dict__)
    program=code['runtime/portable/capture-boot-preference.py'];b=installer.program_from_bytes(program,installer.PROGRAM_SHA)
    return installer,b,program


def protected_read(capture,path,limit=16384,mode=0o600):
    with ExitStack() as stack:
        tree=capture.b.Tree(stack);raw=tree.file(path,limit,mode);tree.check();capture.check();return raw


class PortableBackend:
    def __init__(self,ctx,engine,journal,material,compiler,action):
        self.ctx,self.c,self.j,self.material,self.m,self.action=ctx,engine,journal,material,compiler,action
        self.stack=ExitStack();self.lease=None;self.lease_context=None;self.rollback_mode=action!='install'
        self.capture=self.stack.enter_context(compiler.source_module(ctx,'tools/aggregate-portable-capture.py').Capture(ctx))
        self.view=compiler.browser_material(ctx,'portable');self.i,self.b,self.program=guest_helpers(material)
        self.baseline=self.read_baseline() if os.path.lexists(journal.root/'baseline.json') else None
        self.maps={}
        self.closed=False

    def check(self):
        if self.closed:raise ValueError('Closed aggregate backend')
        if self.ctx['check']() is not True or self.capture.check() is not True:raise ValueError('Original aggregate source/control admission lost')
        self.j.guard()
        if self.baseline is not None:
            if self.j.read('baseline.json')!=self.baseline:raise ValueError('Original immutable aggregate baseline changed')
            self.physical_check()
        if self.lease is not None and self.lease['check']() is not True:raise ValueError('Original core20 lease admission lost')
        return True

    def read_baseline(self):
        value=self.j.read('baseline.json')
        if (type(value) is not dict or set(value)!={'schema','kind','layout','packageSha256','config','configSha256','configurationDigest','gateway','runtime','preferenceSha256','viewToken','physical'}
                or value['schema']!=1 or value['kind']!='droidmate-original-aggregate-baseline' or value['layout']!='portable'
                or value['packageSha256']!=self.ctx['release']['packageSha256'] or not re.fullmatch('[a-f0-9]{32}',value['viewToken'])
                or type(value['configurationDigest']) is not str or not re.fullmatch('[a-f0-9]{64}',value['configurationDigest'])):
            raise ValueError('Original closed preserving baseline required')
        return value

    def physical_check(self):
        for name,expected in self.baseline['physical'].items():
            path=Path(name);s=path.lstat()
            if (not stat.S_ISREG(s.st_mode) or (s.st_uid,s.st_gid,s.st_nlink)!=(0,0,1)
                    or list(self.c.generation(s))!=expected['generation'] or sha(path.read_bytes())!=expected['sha256']):
                raise ValueError('Original physical unit/configuration generation changed')
        if fields(RUNTIME)!=self.baseline['runtime']:raise ValueError('Original physical runtime service graph changed')
        return True

    def establish(self):
        if self.baseline is not None:return self.check()
        self.ctx['check']();self.capture.check()
        config_raw=protected_read(self.capture,CONFIG,16384,0o644);config=json.loads(config_raw)
        if type(config) is not dict or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}',config.get('user','')):raise ValueError('Original portable configuration required')
        ip=ipaddress.IPv4Address(config['lanAddress'])
        if not any(ip in ipaddress.IPv4Network(v) for v in ('192.168.0.0/16','10.0.0.0/8','172.16.0.0/12')):raise ValueError('Original private LAN endpoint required')
        gateway=fields(GATEWAY);runtime=fields(RUNTIME)
        if gateway!={**gateway,'DropInPaths':'','BindPaths':'','BindReadOnlyPaths':'','RootDirectory':'','RootImage':'','TemporaryFileSystem':''}:
            raise ValueError('Original unmapped portable gateway required')
        if gateway['FragmentPath']!='/run/systemd/transient/'+GATEWAY or runtime['FragmentPath']!='/etc/systemd/system/'+RUNTIME:
            raise ValueError('Original runtime/transient gateway roots required')
        pref=protected_read(self.capture,self.b.ROOT/'preference.json',4096)
        preference=self.b.preference(pref)
        if preference['packageSha256']!=self.capture.c['statement']['packageSha256']:raise ValueError('Original matching GPU preference required')
        physical={}
        paths={CONFIG,Path(runtime['FragmentPath']),*(Path(p) for p in runtime['DropInPaths'].split()),self.b.SERVICE,self.b.ROOT/'preference.json',self.b.ROOT/'complete.json',self.b.ROOT/'program.py',self.b.ROOT/'installation.json',self.b.ROOT/'armed.json',self.b.handoff_path(preference['packageSha256'])}
        for path in paths:
            mode=stat.S_IMODE(path.lstat().st_mode)
            raw=protected_read(self.capture,path,65536,mode)
            physical[str(path)]={'sha256':sha(raw),'generation':list(self.c.generation(path.lstat()))}
        value={'schema':1,'kind':'droidmate-original-aggregate-baseline','layout':'portable','packageSha256':self.ctx['release']['packageSha256'],
            'config':config,'configSha256':sha(config_raw),'configurationDigest':portable_configuration_digest(self.material,config),'gateway':gateway,'runtime':runtime,'preferenceSha256':sha(pref),'viewToken':preference['viewToken'],'physical':physical}
        self.j.write('baseline.json',value);self.baseline=self.read_baseline();self.check();return True

    def admit(self):
        self.establish();self.check()
        if self.action=='install':self.health(original=True)
        return True

    def map(self,key):
        if key not in self.maps:
            if key=='gateway':raw=portable_gateway_mapping(self.view['mapping'],self.baseline['configurationDigest'])
            elif key=='installer':raw=self.m.source_module(self.ctx,'tools/aggregate-supervisor.py').installer_unit(self.ctx)
            else:raw={'gateway':self.view['mapping'],'recovery':recovery_unit(self.ctx),'preference':self.c.preference_inhibit(self.j.root),'preference-restore':self.c.preference_inhibit(self.j.root)}[key]
            self.maps[key]=self.c.OwnedMapping(self.j,key,raw)
        return self.maps[key]

    def directory(self,path):
        if path==PUBLIC:return self.c.public_parent(self.j,path)
        if not os.path.lexists(path):
            parent=self.j.directory(path.parent);os.mkdir(path.name,0o755,dir_fd=parent);os.fsync(parent)
        self.j.directory(path)

    def reload(self):
        self.check();command(['/usr/bin/systemctl','daemon-reload']);self.check();return True

    def observe_preference(self):
        self.physical_check()
        return fields(PREFERENCE,('Job','MainPID','ControlPID','ActiveState','SubState'))

    monotonic=staticmethod(time.monotonic)
    def pause(self):time.sleep(.1)

    def verify_preference(self):
        self.check()
        with ExitStack() as stack:
            n=self.b.Native(stack,self.baseline['preferenceSha256']);n.armed();n.verify()
            self.i.original_loader(self.b,n,n.p)
            files=self.i.payload(self.b,self.program,n.p)
            self.i.installation_provenance(self.b,n,files,n.p)
            if n.p['viewToken']!=self.baseline['viewToken']:raise ValueError('Original armed GPU view changed')
            n.verify()
        self.check();return True

    def arm_recovery(self):
        self.check()
        preference='preference' if self.action=='install' else 'preference-restore'
        for path in (self.c.TARGETS['gateway'].parent,self.c.TARGETS[preference].parent):self.directory(path)
        recovery=self.map('recovery');recovery.prepare();recovery.publish();self.check()
        link=self.c.OwnedBootLink(self.j);link.prepare();link.publish();self.check()
        self.c.inhibit_preference(self.map(preference),self,self.check)
        self.verify_preference();self.check();return True

    def restore_cpu(self):
        self.check();self.capture.restore();self.check()
        self.lease_context=self.capture.release();self.lease=self.lease_context.__enter__();self.cpu_check();return True

    def cpu_check(self):
        return self.check()

    def stop(self):
        self.cpu_check();command(['/usr/bin/systemctl','stop',RUNTIME],120);self.cpu_check();return True

    def public_check(self):
        for digest,files in ((self.view['runtimeDigest'],self.view['runtimeFiles']),(self.view['frontendDigest'],self.view['frontendFiles'])):
            self.c.stage_public_tree(PUBLIC,digest,files,self.check,create=False)
        return True

    def publish(self):
        self.cpu_check();self.directory(PUBLIC)
        for digest,files in ((self.view['runtimeDigest'],self.view['runtimeFiles']),(self.view['frontendDigest'],self.view['frontendFiles'])):
            self.c.stage_public_tree(PUBLIC,digest,files,self.check,create=not os.path.lexists(PUBLIC/digest))
        mapping=self.map('gateway');mapping.prepare();mapping.publish();self.cpu_check();return True

    def rollback(self):
        self.rollback_mode=True;self.cpu_check()
        if os.path.lexists(self.j.root/'map-gateway.json'):self.map('gateway').remove()
        elif os.path.lexists(self.c.TARGETS['gateway']):raise ValueError('Foreign gateway mapping preserved')
        self.cpu_check();return True

    def start(self):
        self.cpu_check();command(['/usr/bin/systemctl','start',RUNTIME],180);self.cpu_check();return True

    def health(self,original=False):
        self.check();end=time.monotonic()+90
        @contextmanager
        def source_proof():
            nonlocal end
            started=time.monotonic()
            try:yield
            finally:end+=time.monotonic()-started
        while True:
            observations=[fields(unit,('ActiveState',))['ActiveState'] for unit in PEERS]
            if observations==['active']*7:break
            if time.monotonic()>=end:raise TimeoutError('Original coupled runtime did not become healthy')
            with source_proof():self.check()
            time.sleep(.3)
        # Mandatory local source/lease verification must not consume the shared
        # readiness allowance. Peer waits and all HTTP/TLS/media IO still do.
        with source_proof():
            current=fields(GATEWAY)
            if original:
                if current!=self.baseline['gateway']:raise ValueError('Original CPU/browser graph was not restored')
            else:
                self.public_check()
                if self.map('gateway').verify() is not True:raise ValueError('Owned live gateway mapping required')
                source=str(PUBLIC/self.view['runtimeDigest']/getattr(self,'runtime_view','portable/runtime'))
                gateway_graph(self.baseline['gateway'],current,str(self.c.TARGETS['gateway']),source,getattr(self,'runtime_target','/opt/droiddeck/runtime'))
        config=self.baseline['config'];host=config['lanAddress'];port=config.get('port',8443)
        if type(port) is not int or not 1024<=port<=65535:raise ValueError('Original bounded gateway port required')
        context=ssl._create_unverified_context()
        url='https://'+host+':'+str(port)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),NoBrowserRedirect())
        raw=browser_route(opener,url+'/')
        if not original and sha(raw)!=sha(self.material.frontend['index.html']):raise ValueError('Exact aggregate frontend not served')
        # Check a fresh TLS negotiation and a stream WebSocket upgrade without
        # saving Android frames, bypassing authentication or exposing TLS keys.
        with socket.create_connection((host,port),timeout=10) as stream:
            with context.wrap_socket(stream,server_hostname=host) as secured:
                if not secured.getpeercert(binary_form=True):raise ValueError('Original public TLS certificate missing')
        browser_media(opener,url,host,port,context,end,self.check)
        self.check();return True

    def verify(self):return self.health(original=self.rollback_mode)

    def release_lease(self):
        if self.lease_context is not None:
            context=self.lease_context;self.lease_context=None;self.lease=None;context.__exit__(None,None,None)

    def preference_cleanup(self):
        self.check()
        for key in ('preference','preference-restore'):
            if os.path.lexists(self.j.root/('map-'+key+'.json')):self.map(key).remove()
        self.reload();self.check();return True

    def prefer_gpu(self):
        self.cpu_check();self.release_lease();self.check()
        try:self.capture.change(self.baseline['viewToken'])
        except Exception:
            # No uncertain GPU state is accepted. Only the original controller
            # may restore/drain its worker; preserve the guard if that refuses.
            self.check();self.capture.restore();self.check()
            status=self.capture.status()
            if (status.get('phase') not in ('restored','no-operation') or status.get('mode')!='cpu'
                    or status.get('updaterLeaseHeld') is not False):raise ValueError('Verified original CPU fail-safe required')
            self.health(original=False)
        self.check();return True

    def disarm_recovery(self):
        # Terminal history must reach stable storage BEFORE removing the guard
        # or boot recovery registration. A killed cleanup is resumed by finish.
        self.cpu_check();self.release_lease();self.check();return True

    def finish(self):
        self.check();phase=self.j.state()
        if phase not in ('accepted','restored'):raise ValueError('Durable verified terminal state required before cleanup')
        self.health(original=phase=='restored')
        changed=False
        for key in ('preference','preference-restore'):
            if os.path.lexists(self.j.root/('map-'+key+'.json')):
                mapping=self.map(key)
                if mapping.verify():mapping.remove();changed=True
        if phase=='restored':
            for name,mapping in (('map-installer.json',self.map('installer')),('map-recovery-link.json',self.c.OwnedBootLink(self.j)),('map-recovery.json',self.map('recovery'))):
                if os.path.lexists(self.j.root/name) and mapping.verify():mapping.remove();changed=True
        if changed:self.reload()
        self.check();return True

    def close(self):
        if self.closed:return
        try:self.release_lease()
        finally:self.closed=True;self.stack.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()


def main(args,ctx,engine):
    counts={'plan':0,'install-existing':0,'install-new':2,'recover':0,'undo':0,'legacy-video':0,'upgrade-existing':0,'upgrade-recover':0,'upgrade-undo':0}
    if type(args) is not list or not args or args[0] not in counts or len(args)!=counts[args[0]]+1:raise ValueError('Closed aggregate coordinator action required')
    if sys.platform!='linux' or os.geteuid()!=0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:raise ValueError('Root unredirected Linux coordinator required')
    if args in (['upgrade-existing'],['upgrade-recover'],['upgrade-undo']):
        result=successor_handoff(args,ctx,engine);ctx['check']();print(json.dumps(result,sort_keys=True));return result
    m=load_material(ctx);ctx=m.retain(ctx);material=m.materialize(ctx)
    portable=os.path.lexists(CONFIG);legacy=os.path.lexists('/opt/teslaandroid-waydroid/runtime')
    if portable and legacy:raise ValueError('Ambiguous installation preserved')
    if args[0]=='legacy-video':
        if portable or not legacy:raise ValueError('Enrolled legacy video requires the original legacy layout')
        return m.source_module(ctx,'tools/aggregate-legacy-runtime.py').run(ctx,engine,material,m)
    if args[0]=='install-new':
        if portable or legacy:raise ValueError('Existing installation preserved; explicit fresh Ubuntu required')
        fresh=m.source_module(ctx,'tools/aggregate-fresh-install.py').fresh_install(ctx,material.assets,args[1],args[2])
        if fresh.get('readyForCaptureEnrollment') is not True:raise ValueError('Original healthy fresh core required')
        # Continue into the same preserving installer; never claim foundation-only success.
        args=['install-existing'];portable=True
    if not portable and not legacy:
        if args==['plan']:
            result={'kind':'droidmate-aggregate-plan','layout':'recognized-legacy' if legacy else 'clean','readyForInstallation':False,'servicesChanged':False}
            print(json.dumps(result));return result
        raise ValueError('Original portable capture and GPU boot enrollment required')
    legacy_module=m.source_module(ctx,'tools/aggregate-legacy-backend.py') if legacy else None
    layout='legacy' if legacy else 'portable'
    recovery=legacy_module.recovery_unit(ctx) if legacy else recovery_unit(ctx)
    def backend_for(context,journal,action):
        if legacy:return attach_coordinated_workers(legacy_module.LegacyBackend(context,engine,journal,material,m,action,types.SimpleNamespace(**globals())),layout='legacy')
        return attach_coordinated_workers(PortableBackend(context,engine,journal,material,m,action))
    if args==['plan'] and legacy:
        result={'kind':'droidmate-aggregate-plan','layout':'recognized-legacy','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
            'required':['reviewed-original-legacy-dependencies','owned-legacy-journal','original-native-runtime-drain','selected-vm-qualified-gpu-host'],
            'servicesChanged':False,'readyForPublication':False}
        print(json.dumps(result));return result
    if args==['plan']:
        result={'kind':'droidmate-aggregate-plan','layout':'portable','aggregateRelease':'RC18','coreRuntimeVersion':'0.1.1-rc.20',
            'required':['original-signed-core20','original-capture-loader','armed-gpu-boot-preference','selected-vm-qualified-gpu-host'],
            'servicesChanged':False,'readyForPublication':False}
        print(json.dumps(result));return result
    digest=ctx['release']['packageSha256'];root=ROOT/digest
    if not os.path.lexists(ROOT):
        if args[0]!='install-existing':raise ValueError('Original aggregate journal required')
        parent=engine.Journal.__new__(engine.Journal);parent.dirs={};parent.files={};parent.closed=False
        try:fd=parent.directory(ROOT.parent);os.mkdir(ROOT.name,0o700,dir_fd=fd);os.fsync(fd)
        finally:parent.close()
    create=not os.path.lexists(root)
    if create and args[0]!='install-existing':raise ValueError('Original transaction journal required for recovery')
    supervisor=None;launch=False
    try:
        with engine.Journal(root,{'packageSha256':digest,'layout':layout},create=create) as journal:
            state=journal.state();worker_ctx=ctx
            if legacy and state=='new' and args[0]=='install-existing':
                # Both private baselines precede even the first owned service
                # registration, so an admitted recovery always has its originals.
                with backend_for(ctx,journal,'install') as baseline_backend:baseline_backend.establish()
            if args[0]=='install-existing':
                module=m.source_module(ctx,'tools/aggregate-supervisor.py')
                route=module.route_install(state,module.Supervisor.is_worker())
                if route in ('launch','worker'):
                    supervisor=module.Supervisor(ctx,engine,journal);supervisor.prepare(recovery)
                    if route=='launch':launch=True
                    else:
                        supervisor.require_worker()
                        original=ctx
                        def check_worker():
                            if original['check']() is not True:raise ValueError('Original signed installer source lost')
                            supervisor.require_worker();return True
                        worker_ctx={'root':ctx['root'],'release':ctx['release'],'check':check_worker}
            if not launch:
                if not legacy and state=='new' and args==['install-existing']:
                    m.source_module(worker_ctx,'tools/aggregate-enroll.py').enroll(worker_ctx,material,m)
                if legacy and state=='new' and args[0] in ('recover','undo'):
                    with backend_for(worker_ctx,journal,args[0]) as backend:result=backend.restore_unstarted()
                elif state=='new' and args[0] in ('recover','undo'):result={'phase':state,'servicesChanged':False}
                else:
                    with backend_for(worker_ctx,journal,'install' if args[0]=='install-existing' else args[0]) as backend:
                        result=engine.transact(journal,backend,backend.check,'install' if args[0]=='install-existing' else args[0])
                        backend.finish()
                        if legacy:result={**result,'preferredVideoMode':'gpu','effectiveVideoMode':backend.video_mode}
        if launch:
            # The journal lock must be released before the independent worker
            # admits the same journal. The caller owns no installer lifetime.
            supervisor.controls.check();command(['/usr/bin/systemctl','daemon-reload']);supervisor.graph();supervisor.start()
            with engine.Journal(root,{'packageSha256':digest,'layout':layout},create=False) as journal:
                if journal.state()!='accepted':raise ValueError('Owned installer did not reach durable acceptance; retain recovery')
                result={'phase':'accepted','servicesChanged':True,'installerSupervised':True}
                if legacy:
                    with backend_for(ctx,journal,'recover') as backend:
                        backend.video_check(original=False)
                        result.update(preferredVideoMode='gpu',effectiveVideoMode=backend.video_mode)
        ctx['check']();print(json.dumps(result,sort_keys=True));return result
    finally:
        if supervisor:supervisor.close()


# SPDX-License-Identifier: GPL-3.0-only
"""Successor handoff template, appended only to the reconstructed signed backend.

No standalone entry, import-time effects or arbitrary paths. Only portable RC17
is recognized. Recovery never retries retirement or installs a new transaction.
"""
HANDOFF_PREDECESSOR='7c9a48e879a71cd69d03ce587e2feeef32f231a375c3be3deb685c5b963950d1'
HANDOFF_OLD_CAPTURE='a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da'
HANDOFF_NEW_CAPTURE='e140f8c4c989aa434c967b17a82a79a52efb1c6a0f421a0f7c35115827788fe6'
HANDOFF_ENTRY_PIN='8e876a7c8b18dbc90091e6bcd0a4904f69ba59c1e02006d34321cfc861a7a87f'
# Explicit publisher-signed qualification predecessor; pins come from the local
# verified artifact, never learned from installed code. RC17 remains supported.
HANDOFF_PREDECESSORS={
 HANDOFF_OLD_CAPTURE:{'aggregate':HANDOFF_PREDECESSOR,'release':'rc17','entry':HANDOFF_ENTRY_PIN},
 'baf49445cc9e331d6ee9a3ba71eb48ad3e9723301ff60874f99723e3f73d0526':{
  'aggregate':'eeff71b5f9ea8df916088cef656b13686089629dee6c0fe721fa28ab8cab135b',
  'release':'rc18','entry':'8af86bf50d4fa3b9e1220360e20725d6b3ea830dcd859a23efba34d2d736f410',
  'handoffRecoverySha256':'ed1f732e60c34603ca396cd6cedf8519129e657102514c4c66eed0032fc3f756'},
}
# Same capture on .7 (direct install) and .215 (preserving handoff).
HANDOFF_PREDECESSORS['14703568a3cf09351bbfa8e1f435b5cec0cb936ea006eb6e8316a5c9d2ec141b']={
 'aggregate':'d51ea91fb9e4f28e112f57a41129fcd0bbbd7c8500e45f5f04e79fc44831fd7f',
 'release':'rc18','entry':'892b437bb6a3047fc001fe2b2272124e27d8ea0e70ad97cec4d5db93fc438a92',
 'handoffRecoverySha256':'125fadcfa9d7d843b818c1fb0f794a2f6f44ac4c10eb055a1e7fde496c8b707b',
 'handoffRecoveryOptional':True}
# EC1A is the same signed capture on a clean VM and after the pinned C904 handoff.
HANDOFF_PREDECESSORS['a970bedc0666213e1e0fe9aac6cd3d4b9ee9e71690986ffa3b11e0a77e317017']={
 'aggregate':'6488fffef271ff2aca876113c3f6864f0075cd865ed520a9f67b2709ac387e98',
 'release':'rc18','entry':'a41a0946d9d7fb2dcdcde116a597e7bc912bdcd24a269f92b7ca9fd7221588c4',
 'handoffRecoverySha256':'30fd574e52c0dc56879caba982f792485fdba61da3cbc0c73dfce2e20be24033',
 'handoffRecoveryOptional':True}
HANDOFF_PREDECESSORS['b4cab518de53b8c7fb7e204ee9e47285ba72e05cc0fe5570ed174e87ae92b851']={
 'aggregate':'e111da74492c561a87c4135df9fa3439d687ac0325c4e92683e835d3f4e84446','release':'rc18',
 'entry':'7ae29ac218ee06a3d8936f0680a86dfecdca0e95341d9ca0b7fa5dfef8e6b464',
 'handoffRecoverySha256':'125ac4dd89192448853b01e07a3a71ac02a8d02e285857293b38bdd521a37714'}
HANDOFF_PREDECESSORS['fa91d250e3b5dfdfacc1e8286bc860aedbc3ce1a55e885220113f1e8618daa6c']={
 'aggregate':'ea368d26446a2f2bfc03075a11bbbd4cf253c51e9ab416001322b64abdd2001c','release':'rc18',
 'entry':'19e1541f463e2e023c6d82d4f37e57e2204da91761ff9f0da3842bcfd60f0bb9',
 'handoffRecoverySha256':'6f8a60d0248ee5d8ad1e186e965c7e1c8010f7942cf0f9599892359bafd46361'}
HANDOFF_PREDECESSORS['9d44e10c924ff769c140ca32edf71d6791cb94cbfc25ad79ddcffed34cc03413']={
 'aggregate':'2d73933c86e6bf88ccce84825592d0d9a1bcddd4fe417cb4bd60f03f48fab60d','release':'rc18',
 'entry':'2969d2c1d7077a8a0fd5e9584b45441cda1039ea74ee5c0c8611ece2eb80e1fb',
 'handoffRecoverySha256':'c07457f35ffa2e6b5474d356fca53961e1b01357c6f0084c2de448f5cddcbb30'}
HANDOFF_PREDECESSORS['dec213421e9be2801e2e565e9bd971ffd43823f718d30574f2f6f468d58331b2']={
 'aggregate':'b8d27293a156091a836fb9342ee497408df1fe2d9fe3173d495408f67eccb6c5','release':'rc18',
 'entry':'b5a56fb17ab92528678229b40295ba52550d1a345268ec9f2eb608f1cb7e3030',
 'handoffRecoverySha256':'1c6842028c68c51ed2c05f8d85a60163c8a7cf81a41aac7b9b16726384926fd1'}
HANDOFF_PREDECESSORS['13b49ed386cd67a5a19a1e4d95a25c7ead78e46775cd9785a4951c3a00e955ad']={
 'aggregate':'e34eeb1358c479ba0f348ac067343b8fceaf858d8ab02cdf2902c06a86b14f85','release':'rc18',
 'entry':'6dc3ad2f6ef619a9fc8ffcc3d1322e5da71b10ae27169ff1261bf53d72d53c74',
 'handoffRecoverySha256':'537fdcca6a18827b901e81ee30c07ec0fa7060c1bb16337990ae38f6f7ec2507'}
# Two accepted aggregate layouts share one signed runtime. The immutable
# installation origin selects ONLY among these publisher-pinned candidates.
HANDOFF_SHARED_PREDECESSORS={
 '2c7d495a3ed6ed096ec1285a08389c04be8c9070f83a3dfb9b8a5c889aacb394':[
  {'aggregate':'c80a06892581d6f0e856cf35f595b1503cb1f3c031be82b3049c40e256c10d10','release':'rc18',
   'entry':'4e97d9ceef4baa1a1940b9395ceff82bf53145daa137de855d5ccd820d0e65c2',
   'handoffRecoverySha256':'72172e82d337f62b2892a9c242e7209576be19d3730a637c0dbc7d92723ba95d'},
  {'aggregate':'842a4f5b771da4ea17d696b575d8e2c85b8791112a53e68e007f157841a6422d','release':'rc18',
   'entry':'4a958dc03eaf6604a74b36559443ad5903396c34f590420b6ea627b3c82982e6',
   'handoffRecoverySha256':'46bd3b5ccbf1cd3ad5e6b3a7000a90a9f3f19067047ac988b773bb62be8f8a98'},
 ]}
# Current retry capture has a fresh UE origin and a preserving UG origin.
# Entries and guard bytes are reconstructed from those immutable signed bundles.
HANDOFF_SHARED_PREDECESSORS['c9044522aedc1b1dec6bf7a15b135cc43af19577b1c4eb9f80ba96530ddfbc80']=[
 {'aggregate':'5d8f52db6b55a62acbd45042e8f2045091be6aaeb32c8620631fdbf747ce70a4','release':'rc18',
  'entry':'d5cdf51379708f9eb438b642add697390b7694c2fbff94ed2c928047c566e39e',
  'handoffRecoverySha256':'31bd2992d198ed8d9f4aff5fb44acfacfa213e4695b59ba9ab3fa11113fff785','handoffRecoveryOptional':True},
 {'aggregate':'92782f6d9be2add0bef861a06da8af885bdbcf625834fe7789b8fd7e3e4d26c2','release':'rc18',
  'entry':'c4865f26cb89339200a41408445578e6b3c875cf73669c15c8f0e716ced2bc7f',
  'handoffRecoverySha256':'217d9ce1b5e01170e6cbd686b7b260cd0faefa992c5b6f2243c4b8b99ac1260b'},
]
# The same signed TV release completed two different preserving update paths.
# Its guard's After= dependencies retain the earlier capture identity. Pins are
# independently reconstructed from the signed TV template, not installed bytes.
HANDOFF_GUARD_ORIGINS={
 '2d73933c86e6bf88ccce84825592d0d9a1bcddd4fe417cb4bd60f03f48fab60d':{
  ('2c7d495a3ed6ed096ec1285a08389c04be8c9070f83a3dfb9b8a5c889aacb394','4a958dc03eaf6604a74b36559443ad5903396c34f590420b6ea627b3c82982e6'):'18a302afda8a7f6ecae0804eceb239d306fd3dd2ab252d98067d5cf0f0edd8a2',
  ('fa91d250e3b5dfdfacc1e8286bc860aedbc3ce1a55e885220113f1e8618daa6c','19e1541f463e2e023c6d82d4f37e57e2204da91761ff9f0da3842bcfd60f0bb9'):'c07457f35ffa2e6b5474d356fca53961e1b01357c6f0084c2de448f5cddcbb30',
 }}

def predecessor_guard_pin(predecessor,capture,entry):
    origins=HANDOFF_GUARD_ORIGINS.get(predecessor['aggregate'])
    if origins is None:return predecessor.get('handoffRecoverySha256')
    if type(capture)is not str or type(entry)is not str or (capture,entry)not in origins:
        raise ValueError('Publisher-pinned completed predecessor origin required')
    return origins[(capture,entry)]

def select_predecessor(capture,*,history=None,entry=None):
    if capture in HANDOFF_PREDECESSORS:return HANDOFF_PREDECESSORS[capture]
    candidates=HANDOFF_SHARED_PREDECESSORS.get(capture,[])
    matches=[value for value in candidates if (entry==value['entry'] if entry is not None else history=='droidmate-gpu-install-rc18-'+value['aggregate'][:16])]
    if len(matches)!=1:raise ValueError('Only an explicit publisher-pinned installation origin is supported')
    return matches[0]
HANDOFF_PHASES=('new','intent','armed','cpu','stopped','mapped','loaded','started','verified','accepted')
HANDOFF_METHODS=('admit','stage','restore_predecessor','retire_preference','switch_recovery','install_successor','check_successor','verify','finish')


def handoff_transact(journal,backend,action):
    if action not in ('upgrade-existing','upgrade-recover','upgrade-undo'):raise ValueError('Closed successor handoff action required')
    backend.check();phase=journal.state()
    if action in ('upgrade-recover','upgrade-undo'):
        if phase=='new':
            if action=='upgrade-undo':raise ValueError('Original admitted upgrade required before Undo')
            return {'phase':'new','servicesChanged':False}
        if phase=='accepted' and action=='upgrade-recover':backend.verify();backend.finish();return {'phase':phase,'servicesChanged':False}
        if phase=='restored':
            changed=backend.start_restored_cpu() if action=='upgrade-recover' else False
            backend.verify_cpu();backend.finish();return {'phase':phase,'servicesChanged':changed}
        if phase!='recovering':journal.append('recovering')
        backend.recover_cpu();backend.verify_cpu();journal.append('restored');backend.finish()
        return {'phase':'restored','servicesChanged':True,'androidDataPreserved':True}
    if phase=='accepted':backend.verify();backend.finish();return {'phase':phase,'servicesChanged':False}
    if phase!='new':raise ValueError('Interrupted or restored handoff requires recovery, never replay')
    # Record intent BEFORE even the first external side effect. Each boundary
    # has its own durable phase; interruption goes to CPU, not forward replay.
    journal.append('intent');backend.admit()
    for method,after in zip(HANDOFF_METHODS[1:],HANDOFF_PHASES[2:]):
        backend.check();getattr(backend,method)();backend.check();journal.append(after)
    return {'phase':'accepted','servicesChanged':True,'androidDataPreserved':True}


def handoff_child_locations(raw):
    """Keep only the signed child's bounded code-location diagnostic, never text."""
    import json,re
    if type(raw)is not bytes or len(raw)>8192:return None
    for line in reversed(raw.splitlines()):
        try:value=json.loads(line)
        except (ValueError,UnicodeError):continue
        if type(value)is not dict or set(value)!={'failureLocations'}:continue
        rows=value['failureLocations']
        pattern=r'(?:(?:aggregate-[a-z-]+|capture-[a-z-]+|gateway-update[a-z-]*)\.py|contextlib\.py):[A-Za-z_<>][A-Za-z0-9_<>]*:[1-9][0-9]{0,5}'
        if type(rows)is list and 0<len(rows)<=16 and all(type(row)is str and len(row)<=160 and re.fullmatch(pattern,row)for row in rows):return rows
    return None


class SuccessorHandoff:
    def __init__(self,ctx,engine,journal,material,compiler):
        self.ctx,self.e,self.j,self.material,self.m=ctx,engine,journal,material,compiler
        self.stack=ExitStack();self.p=compiler.source_module(ctx,'tools/aggregate-portable-capture.py')
        self.b=self.p.loader(ctx);self.tree=self.b.Tree(self.stack)
        self.i,self.program,self.program_raw=guest_helpers(material)
        baseline=journal.root/'baseline.json'
        saved=journal.read('baseline.json') if os.path.lexists(baseline) else None
        if saved:preference=self.program.preference(self.b.encode(saved['preference']))
        else:
            with ExitStack() as stack:
                preference=self.program.preference(self.b.Tree(stack).file(self.program.ROOT/'preference.json',4096))
        self.old_capture=preference['packageSha256']
        origin=None
        if not saved and self.old_capture in HANDOFF_SHARED_PREDECESSORS:
            with ExitStack() as stack:origin=self.program.document(self.b.Tree(stack).file(self.program.ROOT/'installation.json',8192))
        self.predecessor=select_predecessor(self.old_capture,history=origin.get('history') if type(origin) is dict else None,entry=saved['oldEntrySha256'] if saved else None)
        self.old_root=Path('/usr/local/share/droidmate-'+self.predecessor['release']+'-'+self.predecessor['aggregate'])
        self.old_entry=self.old_root/'source/tools/aggregate-installer-entry.py'
        self.entry=ctx['root']/'source/tools/aggregate-installer-entry.py'
        self.digest=ctx['release']['packageSha256'];self.tag=self.digest[:16]
        self.unit='droidmate-successor-recovery-'+self.tag+'.service'
        self.unit_path=Path('/etc/systemd/system')/self.unit
        self.link=self.unit_path.parent/'multi-user.target.wants'/self.unit
        self.history=Path('/var/lib/droidmate-gpu-install-successor-'+self.tag)
        self.inputs=Path('/var/lib/droidmate-successor-inputs')/self.digest
        self.old_recovery='droiddeck-capture-recover@'+self.old_capture+'.service'
        self.new_recovery='droiddeck-capture-recover@'+HANDOFF_NEW_CAPTURE+'.service'
        self.check()
    def check(self):self.ctx['check']();self.j.guard();self.tree.check();return True
    def run(self,args,timeout=60):
        self.check();r=subprocess.run(args,cwd='/',env={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'},stdin=subprocess.DEVNULL,capture_output=True,timeout=timeout)
        self.check()
        if r.returncode:
            locations=handoff_child_locations(r.stderr)
            if locations:print(json.dumps({'ownedChildFailureLocations':locations},sort_keys=True),file=sys.stderr)
        if r.returncode or len(r.stdout)>1048576:raise ValueError('Owned successor operation refused')
        return r.stdout
    def old(self,action):
        if action not in ('verify','undo','recover'):raise ValueError('No predecessor install replay')
        if sha(self.tree.file(self.old_entry,1048576,0o600))!=self.predecessor['entry']:raise ValueError('Exact original signed entry required')
        return self.run(['/usr/bin/python3','-I','-B',str(self.old_entry),'--enrolled',action],1900)
    def new(self,action):
        if action not in ('install-existing','recover','undo'):raise ValueError('Closed successor install/recovery required')
        return self.run(['/usr/bin/python3','-I','-B',str(self.entry),'--enrolled',action],1900)
    def admit(self):
        if not os.path.lexists(CONFIG) or os.path.lexists('/opt/teslaandroid-waydroid/runtime'):raise ValueError('Recognized portable predecessor only')
        value=json.loads(self.old('verify'))
        if value.get('packageSha256')!=self.predecessor['aggregate']:raise ValueError('Exact signed predecessor required')
        with ExitStack() as stack:
            raw=self.b.Tree(stack).file(self.program.ROOT/'preference.json',4096)
        old=self.program.preference(raw)
        if old['packageSha256']!=self.old_capture:raise ValueError('Original predecessor GPU enrollment required')
        self.j.write('baseline.json',{'preference':old,'oldEntrySha256':self.predecessor['entry']})
        self.guard_install()
        # Drain the exact predecessor verifier before retiring its namespace.
        self.retire_previous_guard()
    def guard_bytes(self):
        return ('[Unit]\nDescription=DroidMate preserving successor recovery\nStartLimitIntervalSec=0\nAfter=network.target '+RUNTIME+' '+self.old_recovery+' '+self.new_recovery+'\n'
          '[Service]\nType=oneshot\nUser=root\nGroup=root\nUMask=0077\nWorkingDirectory=/\nPrivateMounts=no\n'
          'Environment=PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C\n'
          'UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME DBUS_SYSTEM_BUS_ADDRESS NODE_OPTIONS\n'
          'ExecStart=/usr/bin/python3 -I -B '+str(self.entry)+' --enrolled upgrade-recover\n'
          'KillMode=control-group\nTimeoutStartSec=1800\nTimeoutStopSec=180\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=multi-user.target\n').encode('ascii')
    def guard_install(self):
        raw=self.guard_bytes();parent=self.j.directory(self.unit_path.parent);links=self.j.directory(self.link.parent)
        if any(os.path.lexists(p) for p in (self.unit_path,self.link,Path(str(self.unit_path)+'.d'),Path('/run/systemd/system')/self.unit,Path('/run/systemd/system')/(self.unit+'.d'))):raise ValueError('Foreign successor recovery registration preserved')
        self.j.write('services.json',{'unit':str(self.unit_path),'unitSha256':sha(raw),'link':str(self.link),'target':str(self.unit_path)})
        fd=os.open(self.unit_path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=parent)
        try:os.fchmod(fd,0o644);os.write(fd,raw);os.fsync(fd)
        finally:os.close(fd)
        os.fsync(parent);os.symlink(str(self.unit_path),self.link.name,dir_fd=links);os.fsync(links)
        self.run(['/usr/bin/systemctl','daemon-reload']);self.guard_verify()
    def guard_verify(self):
        if self.tree.file(self.unit_path,8192,0o644)!=self.guard_bytes():raise ValueError('Original successor recovery bytes required')
        s=self.link.lstat()
        if not stat.S_ISLNK(s.st_mode) or (s.st_uid,s.st_gid)!=(0,0) or os.readlink(self.link)!=str(self.unit_path):raise ValueError('Original successor recovery link required')
        names=('FragmentPath','DropInPaths','Type','User','Group','KillMode','ExecStart')
        raw=self.run(['/usr/bin/systemctl','show',self.unit,'--property='+','.join(names)]).decode('utf-8')
        values={}
        for line in raw.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key not in names or key in values:raise ValueError('Closed successor recovery graph required')
            values[key]=value
        expected={'FragmentPath':str(self.unit_path),'DropInPaths':'','Type':'oneshot','User':'root','Group':'root','KillMode':'control-group'}
        if set(values)!=set(names) or any(values[k]!=v for k,v in expected.items()):raise ValueError('Exact effective successor recovery graph required')
        prefix='{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -I -B '+str(self.entry)+' --enrolled upgrade-recover ; ignore_errors=no ;'
        if not values['ExecStart'].startswith(prefix) or values['ExecStart'].count('argv[]=')!=1:raise ValueError('Exact successor recovery command required')
    def stage(self):
        self.guard_verify()
        for root in (self.inputs.parent,self.inputs):
            parent=self.j.directory(root.parent)
            if not os.path.lexists(root):os.mkdir(root.name,0o700,dir_fd=parent);os.fsync(parent)
            if stat.S_IMODE(os.fstat(self.j.directory(root)).st_mode)!=0o700:raise ValueError('Private successor inputs required')
        names={'package.tar':self.material.assets['capturePackage'],'statement.json':self.material.assets['captureStatement'],'signature.bin':self.material.assets['captureSignature']}
        parent=self.j.directory(self.inputs)
        for name,raw in names.items():
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            try:os.write(fd,raw);os.fsync(fd)
            finally:os.close(fd)
        os.fsync(parent)
        self.stage_controls(names)
    def stage_controls(self,names):
        # A previous interrupted handoff may have staged this same signed
        # capture package. Reuse only the completed authenticated immutable slot;
        # never overwrite incomplete staging or replay a capture operation.
        with self.b.hold_self():
            slot=self.b.ROOT/HANDOFF_NEW_CAPTURE
            if os.path.lexists(slot):
                with self.b.hold_controls(HANDOFF_NEW_CAPTURE) as c:
                    with ExitStack() as stack:
                        tree=self.b.Tree(stack)
                        for name,raw in names.items():
                            if tree.file(slot/name,len(raw),0o600)!=raw:raise ValueError('Exact existing signed capture inputs required')
                        tree.check();c['check']()
            else:self.b.stage(*(str(self.inputs/n) for n in names),HANDOFF_NEW_CAPTURE)
        with self.b.hold_controls(HANDOFF_NEW_CAPTURE) as c:c['check']()
    def restore_predecessor(self):
        self.guard_verify();self.old('undo');self.verify_cpu(predecessor=True)
    def retire_preference(self):
        self.guard_verify();old=self.j.read('baseline.json')['preference'];b=self.program
        # Undo may leave this exact oneshot helper active/exited. Validate its
        # static bytes/graph, then quiesce it before the frozen retirement API.
        with ExitStack() as stack:
            raw=self.b.Tree(stack).file(b.SERVICE,8192,0o644)
        if raw!=self.i.payload(b,self.program_raw,old)['unit']:raise ValueError('Exact owned preference helper required')
        graph=self.i.system_fields(b,b.SERVICE.name)
        if graph.get('FragmentPath')!=str(b.SERVICE) or graph.get('DropInPaths')!='':raise ValueError('Unmodified preference helper graph required')
        self.run(['/usr/bin/systemctl','stop',b.SERVICE.name])
        self.prepare_retirement_recovery(old)
        # The planning API deliberately retains locks until its stack closes.
        for action in ('plan-retire','retire'):
            with ExitStack() as stack:
                tree=object.__new__(b.Native);tree.stack,tree.held,tree.dirs,tree.records=stack,{},{},{};tree.p=old
                self.i.transaction(b,tree,self.program_raw,old,self.history,action)
        self.check()
    def prepare_retirement_recovery(self,old):
        # A previous terminal CPU recovery intentionally disabled this boot link.
        # The immutable retirement API requires it enabled. Re-enable ONLY the
        # exact idle signed owner, under the newly armed failure recovery guard.
        self.guard_verify();self.verify_cpu(predecessor=True);b=self.program
        unit='droiddeck-capture-recover@'+self.old_capture+'.service'
        fields=self.i.system_fields(b,unit);self.i.quiet(fields)
        expected={'LoadState':'loaded','ActiveState':'inactive','Result':'success',
          'FragmentPath':str(b.RECOVERY_TEMPLATE),'DropInPaths':str(b.handoff_path(self.old_capture)),
          'OnSuccess':b.SERVICE.name,'OnSuccessJobMode':'fail'}
        if fields.get('UnitFileState') not in ('enabled','disabled') or any(fields.get(k,'')!=v for k,v in expected.items()):raise ValueError('Exact idle predecessor recovery required')
        with ExitStack() as stack:
            tree=self.b.Tree(stack)
            if tree.file(b.RECOVERY_TEMPLATE,8192,0o644)!=b.recovery_unit() or tree.file(b.handoff_path(self.old_capture),4096,0o644)!=self.i.payload(b,self.program_raw,old)['handoff']:raise ValueError('Exact signed predecessor recovery bytes required')
            tree.check()
        if fields['UnitFileState']=='disabled':
            link=Path('/etc/systemd/system/multi-user.target.wants')/unit
            if os.path.lexists(link):raise ValueError('Foreign disabled recovery link preserved')
            self.run(['/usr/bin/systemctl','enable',unit])
        self.i.recovery_graph(b,old,True)
        self.check()
    def switch_recovery(self):
        self.guard_verify()
        # No simultaneous enabled old/new capture recovery owners after retirement.
        self.run(['/usr/bin/systemctl','disable',self.old_recovery])
        self.run(['/usr/bin/systemctl','enable',self.new_recovery])
        self.run(['/usr/bin/systemctl','daemon-reload'])
    def retire_previous_guard(self):
        previous_guard=self.predecessor.get('handoffRecoverySha256')
        if not previous_guard:return
        unit='droidmate-successor-recovery-'+self.predecessor['aggregate'][:16]+'.service'
        path=Path('/etc/systemd/system')/unit
        link=path.parent/'multi-user.target.wants'/unit
        if any(os.path.lexists(Path(base)/(unit+'.d')) for base in ('/etc/systemd/system','/run/systemd/system')) or os.path.lexists(Path('/run/systemd/system')/unit):raise ValueError('Foreign previous guard overrides preserved')
        if not os.path.lexists(path):
            if self.predecessor.get('handoffRecoveryOptional') and not os.path.lexists(link):return
            raise ValueError('Original completed predecessor guard required')
        with ExitStack() as stack:
            tree=self.b.Tree(stack)
            if self.predecessor['aggregate'] in HANDOFF_GUARD_ORIGINS:
                baseline=Path('/var/lib/droidmate-successor')/self.predecessor['aggregate']/'baseline.json'
                value=json.loads(tree.file(baseline,8192,0o600))
                if type(value)is not dict or set(value)!={'preference','oldEntrySha256'}:
                    raise ValueError('Original completed predecessor baseline required')
                preference=self.program.preference(self.b.encode(value['preference']))
                previous_guard=predecessor_guard_pin(self.predecessor,preference['packageSha256'],value['oldEntrySha256'])
            raw=tree.file(path,8192,0o644)
        if sha(raw)!=previous_guard:raise ValueError('Original completed predecessor guard required')
        if os.path.lexists(link):
            info=link.lstat()
            if not stat.S_ISLNK(info.st_mode) or (info.st_uid,info.st_gid)!=(0,0) or os.readlink(link)!=str(path):raise ValueError('Foreign previous guard link preserved')
        self.run(['/usr/bin/systemctl','stop',unit],timeout=200)
        self.run(['/usr/bin/systemctl','disable',unit])
    def install_successor(self):self.guard_verify();self.new('install-existing')
    def check_successor(self):
        self.guard_verify()
        with ExitStack() as stack:
            p=self.program.preference(self.b.Tree(stack).file(self.program.ROOT/'preference.json',4096))
        if p['packageSha256']!=HANDOFF_NEW_CAPTURE:raise ValueError('New GPU preference owner required')
    def verify(self):
        # Original aggregate recovery performs admitted health and completed
        # transaction verification without replaying installation.
        self.new('recover');self.check_successor()
    def verify_cpu_state(self):
        if os.path.lexists(Path('/var/lib/droiddeck-capture-switch/control.sock')):raise ValueError('Capture owner has not drained')
        with ExitStack() as stack:
            state=json.loads(self.b.Tree(stack).file(Path('/var/lib/droiddeck-capture-switch/state.json'),131072))
        if state.get('phase')!='restored' or state.get('packageSha256') not in (self.old_capture,HANDOFF_NEW_CAPTURE):
            raise ValueError('Original restored CPU capture required')

    def start_restored_cpu(self):
        self.guard_verify();self.verify_cpu_state()
        for unit in (self.old_recovery,self.new_recovery):
            if os.path.lexists(self.link.parent/unit):
                raise ValueError('Existing capture recovery owner preserved')
        status=self.run(['/usr/bin/systemctl','show',RUNTIME,'--property=ActiveState','--value']).strip()
        if status==b'active':return False
        if status not in (b'inactive',b'failed'):raise ValueError('Settled original CPU runtime required')
        self.run(['/usr/bin/systemctl','start',RUNTIME],180)
        return True

    def verify_cpu(self,predecessor=False):
        self.verify_cpu_state()
        for unit in PEERS:
            fields=self.run(['/usr/bin/systemctl','is-active',unit]).strip()
            if fields!=b'active':raise ValueError('Healthy CPU services required')
    def recover_cpu(self):
        self.guard_verify()
        if os.path.lexists(ROOT/self.digest):self.new('undo')
        elif os.path.lexists(self.history/'complete.json'):
            # Retirement already completed on a verified CPU baseline. The old
            # coordinator no longer owns the relocated preference namespace.
            self.verify_cpu()
        else:self.old('undo')
        self.verify_cpu()
        # A restored handoff must stay CPU after a reboot. Keep the enrolled
        # preference/history intact, but remove both exact boot trigger links.
        for unit in (self.old_recovery,self.new_recovery):
            link=Path('/etc/systemd/system/multi-user.target.wants')/unit
            if os.path.lexists(link):
                info=link.lstat()
                if not stat.S_ISLNK(info.st_mode) or (info.st_uid,info.st_gid)!=(0,0) or os.readlink(link)!=str(self.b.SERVICE):raise ValueError('Foreign capture recovery link preserved')
                self.run(['/usr/bin/systemctl','disable',unit])
            self.run(['/usr/bin/systemctl','stop',unit])
    def finish(self):
        # Keep the exact guard for terminal verification on boot. An accepted
        # handoff never retries enrollment, retirement or installation.
        self.check()
    def close(self):self.stack.close()


def successor_handoff(args,ctx,engine):
    if args not in (['upgrade-existing'],['upgrade-recover'],['upgrade-undo']):raise ValueError('Closed successor handoff route')
    m=load_material(ctx);ctx=m.retain(ctx);material=m.materialize(ctx)
    tag=ctx['release']['packageSha256'][:16]
    entry=ctx['root']/'source/tools/aggregate-installer-entry.py'
    unit=('droidmate-successor-undo-' if args==['upgrade-undo'] else 'droidmate-successor-install-')+tag+'.service'
    group=('0::/system.slice/'+unit+'\n').encode('ascii')
    if args in (['upgrade-existing'],['upgrade-undo']) and Path('/proc/self/cgroup').read_bytes()!=group:
        for base in ('/etc/systemd/system','/run/systemd/system'):
            if any(os.path.lexists(Path(base)/n) for n in (unit,unit+'.d')):raise ValueError('Foreign handoff worker definition preserved')
        argv=['/usr/bin/systemd-run','--unit='+unit,'--wait','--collect',
          '--property=Type=oneshot','--property=KillMode=control-group',
          '--property=TimeoutStartSec=4500','--property=TimeoutStopSec=180',
          '--property=OnFailure=droidmate-successor-recovery-'+tag+'.service',
          '--property=OnFailureJobMode=fail','--property=UMask=0077',
          '--property=Environment=PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C',
          '--property=UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME DBUS_SYSTEM_BUS_ADDRESS NODE_OPTIONS',
          '/usr/bin/python3','-I','-B',str(entry),'--enrolled',args[0]]
        result=subprocess.run(argv,cwd='/',env={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'},stdin=subprocess.DEVNULL,capture_output=True,timeout=4600)
        ctx['check']()
        if result.returncode:raise ValueError('Supervised handoff refused; retain independent recovery')
        expected_phase='restored' if args==['upgrade-undo'] else 'accepted'
        with engine.Journal(Path('/var/lib/droidmate-successor')/ctx['release']['packageSha256'],{'packageSha256':ctx['release']['packageSha256'],'layout':'portable'},create=False) as completed:
            if completed.state()!=expected_phase:raise ValueError('Supervised handoff did not reach durable completion')
        return {'phase':expected_phase,'servicesChanged':True,'installerSupervised':True}
    if args in (['upgrade-existing'],['upgrade-undo']):
        expected=[b'/usr/bin/python3',b'-I',b'-B',str(entry).encode('ascii'),b'--enrolled',args[0].encode('ascii')]
        if Path('/proc/self/cmdline').read_bytes().rstrip(b'\0').split(b'\0')!=expected:raise ValueError('Exact enrolled handoff worker required')
    root=Path('/var/lib/droidmate-successor');parent=engine.Journal.__new__(engine.Journal)
    parent.dirs={};parent.files={};parent.closed=False
    try:
        fd=parent.directory(root.parent)
        if not os.path.lexists(root):os.mkdir(root.name,0o700,dir_fd=fd);os.fsync(fd)
    finally:parent.close()
    path=root/ctx['release']['packageSha256'];create=not os.path.lexists(path)
    if create and args!=['upgrade-existing']:raise ValueError('Original handoff journal required')
    with engine.Journal(path,{'packageSha256':ctx['release']['packageSha256'],'layout':'portable'},create=create) as journal:
        b=SuccessorHandoff(ctx,engine,journal,material,m)
        try:return handoff_transact(journal,b,args[0])
        finally:b.close()


# SPDX-License-Identifier: GPL-3.0-only
"""Private worker-view template appended only to a reconstructed signed runtime.

No import-time effects, standalone activation, physical core writes or host edits.
The aggregate's existing durable mapping engine owns publication and Undo.
"""
import json
import os
from pathlib import Path, PurePosixPath
import re

COORDINATED_WORKERS={
    'video-relay':'tesla-waydroid-video-relay-preview.service',
    'display-helper':'tesla-waydroid-display-helper-preview.service',
}
COORDINATED_TARGETS={key:Path('/etc/systemd/system')/(unit+'.d')/'70-droidmate-aggregate.conf' for key,unit in COORDINATED_WORKERS.items()}
COORDINATED_GRAPH=('FragmentPath','DropInPaths','BindPaths','BindReadOnlyPaths','PrivateMounts','RootDirectory','RootImage','TemporaryFileSystem')
COORDINATED_LAYOUTS={
    'portable':('portable/workers','/opt/droiddeck/runtime','services.json'),
    'legacy':('legacy/runtime','/opt/teslaandroid-waydroid/runtime','coordinated-workers.json'),
}
COORDINATED_CODE={
    'video-relay':('preview-gateway/h264-relay.js','preview-gateway/encoder-stream-state.js','preview-gateway/encoder-control.js'),
    'display-helper':('waydroid-display/helper.js','waydroid-display/transaction.js','preview-gateway/encoder-control.js'),
}


def coordinated_digest(value):
    if type(value)is not str or not re.fullmatch('[a-f0-9]{64}',value):raise ValueError('Exact coordinated aggregate required')
    return value


def coordinated_layout(layout):
    if type(layout) is not str or layout not in COORDINATED_LAYOUTS:
        raise ValueError('Fixed coordinated worker layout required')
    return COORDINATED_LAYOUTS[layout]


def coordinated_binding(digest,layout):
    source,target,_=coordinated_layout(layout)
    return '/var/lib/droidmate-browser/'+coordinated_digest(digest)+'/'+source+':'+target


def coordinated_worker_mapping(key,digest,layout='portable'):
    if key not in COORDINATED_WORKERS:raise ValueError('Fixed coordinated worker required')
    return ('[Service]\nPrivateMounts=yes\nBindReadOnlyPaths='+coordinated_binding(digest,layout)+'\n').encode('ascii')


def add_coordinated_worker_files(view,files):
    # This input is the unmodified, independently authenticated capture runtime,
    # never the gateway-only wrapped worker entry or an installed-tree copy.
    if type(files)is not dict or not files:raise ValueError('Complete signed worker source required')
    additions={}
    for name,raw in files.items():
        if (type(name)is not str or not name or str(PurePosixPath(name))!=name or PurePosixPath(name).is_absolute()
                or any(p in ('.','..') for p in name.split('/')) or type(raw)is not bytes or not 0<len(raw)<=8*1024**2):
            raise ValueError('Closed relative worker source required')
        additions['portable/workers/'+name]=raw
    if set(additions).intersection(view['runtimeFiles']):raise ValueError('Existing coordinated source preserved')
    view['runtimeFiles']={**view['runtimeFiles'],**additions}


def coordinated_baseline(workers,digest,layout='portable'):
    coordinated_digest(digest)
    coordinated_layout(layout)
    if type(workers)is not dict or set(workers)!=set(COORDINATED_WORKERS):raise ValueError('Both original worker graphs required')
    for key,unit in COORDINATED_WORKERS.items():
        row=workers[key]
        if (type(row)is not dict or set(row)!=set(COORDINATED_GRAPH) or any(type(v)is not str for v in row.values())
                or row!={**row,'FragmentPath':'/run/systemd/transient/'+unit,'DropInPaths':'','BindPaths':'','BindReadOnlyPaths':'',
                         'PrivateMounts':'no','RootDirectory':'','RootImage':'','TemporaryFileSystem':''}):
            raise ValueError('Original unmapped transient worker required')
    return {'schema':1,'kind':'droidmate-coordinated-worker-baseline','layout':layout,'packageSha256':digest,'workers':workers}


def coordinated_worker_graph(key,digest,baseline,actual,*,mapped,layout='portable'):
    coordinated_baseline({name:baseline if name==key else {
        'FragmentPath':'/run/systemd/transient/'+unit,'DropInPaths':'','BindPaths':'','BindReadOnlyPaths':'','PrivateMounts':'no',
        'RootDirectory':'','RootImage':'','TemporaryFileSystem':''} for name,unit in COORDINATED_WORKERS.items()},digest,layout)
    if key not in COORDINATED_WORKERS or type(mapped)is not bool:raise ValueError('Fixed coordinated graph required')
    expected=dict(baseline)
    if mapped:expected.update(DropInPaths=str(COORDINATED_TARGETS[key]),PrivateMounts='yes',
        BindReadOnlyPaths=coordinated_binding(digest,layout)+':rbind')
    if actual!=expected:raise ValueError('Exact original or owned worker graph required')
    return True


def verify_coordinated_process(key,sources,*,observe=None,read=None,layout='portable'):
    _,target,_=coordinated_layout(layout)
    if key not in COORDINATED_WORKERS:raise ValueError('Fixed coordinated process required')
    observe=observe or (lambda unit:fields(unit,('MainPID','ActiveState','SubState')))
    read=read or (lambda path:Path(path).read_bytes())
    unit=COORDINATED_WORKERS[key];before=observe(unit)
    if (set(before)!={'MainPID','ActiveState','SubState'} or before['ActiveState']!='active' or before['SubState']!='running'
            or type(before['MainPID'])is not str or not re.fullmatch('[1-9][0-9]*',before['MainPID']) or int(before['MainPID'])<2):
        raise ValueError('Active owned worker process required')
    root='/proc/'+before['MainPID'];stat_path=root+'/stat'
    def birth():
        raw=read(stat_path)
        if type(raw)is not bytes or len(raw)>8192:raise ValueError('Bounded process identity required')
        return raw[raw.rfind(b')')+2:].split()[19]
    original_birth=birth()
    for name in COORDINATED_CODE[key]:
        if name not in sources or read(root+'/root'+target+'/'+name)!=sources[name]:
            raise ValueError('Actual running coordinated worker source differs from signed runtime')
    if observe(unit)!=before or birth()!=original_birth:raise ValueError('Coordinated worker changed during source verification')
    return True


class CoordinatedWorkerViews:
    def __init__(self,backend,observe,layout='portable'):
        _,_,self.record=coordinated_layout(layout)
        self.layout=layout
        self.b,self.observe=backend,observe;self.digest=coordinated_digest(backend.ctx['release']['packageSha256']);self.maps={};self.baseline_raw=None
        if any(backend.c.TARGETS.get(key)!=target for key,target in COORDINATED_TARGETS.items()):raise ValueError('Closed durable worker mapping engine required')

    def read(self):
        row=self.b.j.read(self.record)
        if type(row)is not dict or set(row)!={'schema','kind','layout','packageSha256','workers'}:raise ValueError('Closed owned worker baseline required')
        expected=coordinated_baseline(row['workers'],self.digest,self.layout)
        raw=json.dumps(row,sort_keys=True,separators=(',',':'))
        if row!=expected or self.baseline_raw is not None and self.baseline_raw!=raw:raise ValueError('Original worker baseline changed')
        self.baseline_raw=raw;return row

    def establish(self):
        if not os.path.lexists(self.b.j.root/self.record):
            if self.layout=='legacy' and self.b.j.state()!='new':
                raise ValueError('Lost original coordinated workers cannot be recaptured after intent')
            rows={key:self.observe(unit) for key,unit in COORDINATED_WORKERS.items()}
            self.b.j.write(self.record,coordinated_baseline(rows,self.digest,self.layout))
        self.read();return True

    def mapping(self,key):
        if key not in self.maps:self.maps[key]=self.b.c.OwnedMapping(self.b.j,key,coordinated_worker_mapping(key,self.digest,self.layout))
        return self.maps[key]

    def check(self):
        present=os.path.lexists(self.b.j.root/self.record)
        if present:self.read()
        for key,target in COORDINATED_TARGETS.items():
            if os.path.lexists(self.b.j.root/('map-'+key+'.json')):
                if not present:raise ValueError('Worker mapping has no original baseline')
                self.mapping(key).verify()
            elif os.path.lexists(target):raise ValueError('Foreign worker mapping preserved')
        return True

    def publish(self):
        self.read()
        for key,target in COORDINATED_TARGETS.items():
            self.b.directory(target.parent);mapping=self.mapping(key);mapping.prepare();mapping.publish()
        return self.check()

    def rollback(self):
        for key,target in reversed(list(COORDINATED_TARGETS.items())):
            if os.path.lexists(self.b.j.root/('map-'+key+'.json')):self.mapping(key).remove()
            elif os.path.lexists(target):raise ValueError('Foreign worker mapping preserved')
        return self.check()

    def health(self,original=False):
        baseline=self.read()['workers']
        for key,unit in COORDINATED_WORKERS.items():
            if not original and self.mapping(key).verify() is not True:raise ValueError('Published coordinated worker required')
            coordinated_worker_graph(key,self.digest,baseline[key],self.observe(unit),mapped=not original,layout=self.layout)
        return True


def attach_coordinated_workers(backend,layout='portable'):
    """Attach the two peers to the owned aggregate lifecycle."""
    source,_,_=coordinated_layout(layout)
    sources=backend.material.compiler.capture_runtime(backend.material.assets['capturePackage'])
    if layout=='portable':
        add_coordinated_worker_files(backend.view,sources)
    else:
        # The legacy gateway wraps only server.js. The relay and display
        # helper can read the same authenticated tree as the legacy GPU child.
        for name in set().union(*COORDINATED_CODE.values()):
            if name not in sources or backend.view['runtimeFiles'].get(source+'/'+name)!=sources[name]:
                raise ValueError('Legacy peer source differs from the signed runtime')
    views=CoordinatedWorkerViews(backend,lambda unit:fields(unit,COORDINATED_GRAPH),layout)
    saved={name:getattr(backend,name) for name in ('check','establish','publish','rollback','health')}
    def check():saved['check']();return views.check()
    def establish():saved['establish']();return views.establish()
    def publish():saved['publish']();return views.publish()
    def rollback():
        if layout=='legacy':
            backend.stopped()
        views.rollback()
        return saved['rollback']()
    def health(original=False):
        result=saved['health'](original=original);views.health(original)
        if not original:
            for key in COORDINATED_WORKERS:verify_coordinated_process(key,sources,layout=layout)
        return result
    backend.check,backend.establish,backend.publish,backend.rollback,backend.health=check,establish,publish,rollback,health
    return backend
