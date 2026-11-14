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
    if unit not in {*PEERS,PREFERENCE,'droidmate-aggregate-recovery.service'}:raise ValueError('Closed aggregate service name required')
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
    if root!=Path('/usr/local/share/droidmate-rc17-'+digest) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Original enrolled source required')
    return ('[Unit]\nDescription=DroidMate owned aggregate installation recovery\n'
        'After=tesla-waydroid-runtime.service droiddeck-capture-recover@a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da.service\n'
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
        while True:
            observations=[fields(unit,('ActiveState',))['ActiveState'] for unit in PEERS]
            if observations==['active']*7:break
            if time.monotonic()>=end:raise TimeoutError('Original coupled runtime did not become healthy')
            self.check();time.sleep(.3)
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
    counts={'plan':0,'install-existing':0,'install-new':2,'recover':0,'undo':0,'legacy-video':0}
    if type(args) is not list or not args or args[0] not in counts or len(args)!=counts[args[0]]+1:raise ValueError('Closed aggregate coordinator action required')
    if sys.platform!='linux' or os.geteuid()!=0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:raise ValueError('Root unredirected Linux coordinator required')
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
        if legacy:return legacy_module.LegacyBackend(context,engine,journal,material,m,action,types.SimpleNamespace(**globals()))
        return PortableBackend(context,engine,journal,material,m,action)
    if args==['plan'] and legacy:
        result={'kind':'droidmate-aggregate-plan','layout':'recognized-legacy','aggregateRelease':'RC17','coreRuntimeVersion':'0.1.1-rc.20',
            'required':['reviewed-original-legacy-dependencies','owned-legacy-journal','original-native-runtime-drain','selected-vm-qualified-gpu-host'],
            'servicesChanged':False,'readyForPublication':False}
        print(json.dumps(result));return result
    if args==['plan']:
        result={'kind':'droidmate-aggregate-plan','layout':'portable','aggregateRelease':'RC17','coreRuntimeVersion':'0.1.1-rc.20',
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
