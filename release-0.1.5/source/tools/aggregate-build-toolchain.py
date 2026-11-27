# SPDX-License-Identifier: GPL-3.0-only
"""Pinned private build JDK/static libc; installed libraries and foundation stay intact."""
from pathlib import Path
from contextlib import contextmanager
import errno, hashlib, importlib.util, ipaddress, json, os, platform, re, signal, ssl, stat, subprocess, sys, time
import urllib.error, urllib.request

PACKAGE = Path('/usr/local/share/droidmate-foundation-rc2/droiddeck-source-candidate')
STATE = Path('/var/lib/droidmate-fresh-install/toolchain')
JDK = Path('/usr/lib/jvm/java-17-openjdk-amd64')
SNAPSHOT = 'https://snapshot.ubuntu.com/ubuntu/20261015T000000Z/'
VERSION = '17.0.20.1+1-1~26.04'
# Ubuntu-signed 20261015 snapshot: reproduce the original static native payloads.
# Only libc.a is exposed read-only inside the private build namespace.
STATIC_LIBC_PACKAGE = ('libc6-dev_2.43-2ubuntu2.4_amd64.deb',2294116,
    '17bb816a65a25c3416854eef7e9cc6d9a3761f45bad0d44e49bc676b7bab1769')
STATIC_LIBC = Path('/usr/lib/x86_64-linux-gnu/libc.a')
STATIC_LIBC_BYTES = 6238972
STATIC_LIBC_SHA256 = '2a277b999268f100c5f838689704b7432e9b1fdac76bf0cbda83cdbbaecc89e2'
WAYDROID_VERSION = '1.6.2'
WAYDROID_PIN = Path('/etc/apt/preferences.d/90-droidmate-waydroid')
WAYDROID_PIN_BYTES = b'Package: waydroid\nPin: version 1.6.2\nPin-Priority: 1001\n'
PACKAGES = (
    ('jdk', 71779420, 'e440a910fd940d2f7eb49d91aad25b2200703b3ebc76e6eadabe9c0b3eed382a'),
    ('jre', 48133612, 'bed7ea25ac704932b8658c77a0642040e6d46713f4e2f43830ea82d063814716'))
FOUNDATION_MANIFEST = '6996c754fe58aa1ce9e62426bd9a60aefbc78089b66e2d65eeec6ca5241c9b20'
ENV = {'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C','PYTHONDONTWRITEBYTECODE':'1'}


def system():
    if sys.platform!='linux' or os.geteuid()!=0 or platform.machine()!='x86_64':
        raise ValueError('Root Linux x86_64 toolchain context required')


def regular(path, limit, *, allow_empty=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise ValueError('Canonical protected input required')
    for parent in path.parents:
        s=parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:raise ValueError('Protected root ancestry required')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_nlink!=1 or s.st_mode&0o022 or not (0 if allow_empty else 1)<=s.st_size<=limit:
            raise ValueError('Bounded root-owned regular input required')
        with os.fdopen(os.dup(fd),'rb')as stream:raw=stream.read(limit+1)
        same=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if len(raw)!=s.st_size or any(same(v)!=same(s)for v in (os.fstat(fd),path.lstat())):raise ValueError('Original protected input changed')
        return raw
    finally:os.close(fd)


def run(args, timeout=30):
    p=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,env=ENV,cwd='/',timeout=timeout)
    if p.returncode:raise ValueError('Pinned toolchain operation refused')
    return p.stdout


def deb_name(role):return 'openjdk-17-'+role+'-headless_'+VERSION+'_amd64.deb'


def package_check():
    for role,size,pin in PACKAGES:
        raw=regular(STATE/deb_name(role),size)
        if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('Original signed-Ubuntu package bytes required')
    name,size,pin=STATIC_LIBC_PACKAGE;raw=regular(STATE/name,size)
    if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=pin:raise ValueError('Original signed-Ubuntu static libc package required')


def toolchain_receipt():
    name,size,pin=STATIC_LIBC_PACKAGE
    return {'schema':2,'version':VERSION,'packages':[{'role':r,'bytes':n,'sha256':h}for r,n,h in PACKAGES],
            'staticLibc':{'package':name,'bytes':size,'sha256':pin,'archive':str(STATIC_LIBC),
                          'archiveBytes':STATIC_LIBC_BYTES,'archiveSha256':STATIC_LIBC_SHA256}}


def static_libc_check():
    raw=regular(STATE/'extracted'/STATIC_LIBC.relative_to('/'),STATIC_LIBC_BYTES)
    if len(raw)!=STATIC_LIBC_BYTES or hashlib.sha256(raw).hexdigest()!=STATIC_LIBC_SHA256:
        raise ValueError('Original extracted static libc archive required')
    # The installed target may receive normal Ubuntu updates; admit its protected
    # regular path, without replacing or pinning its bytes in the global namespace.
    regular(STATIC_LIBC,64*1024**2)


def installed_waydroid():
    p=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${db:Status-Status} ${Version}','waydroid'],
        stdin=subprocess.DEVNULL,capture_output=True,env=ENV,cwd='/',timeout=15)
    if p.returncode==1 and not p.stdout:return None
    text=p.stdout.decode('ascii').strip()
    if p.returncode or not re.fullmatch(r'installed [0-9][A-Za-z0-9.+:~_-]*',text):
        raise ValueError('Clean or fully installed Waydroid package state required')
    return text.split(' ',1)[1]


def compatible_waydroid(version):
    if version not in (None,WAYDROID_VERSION):
        raise ValueError('Existing different Waydroid preserved; no automatic downgrade')


def protected_parent(path):
    for parent in (Path(path),*Path(path).parents):
        v=parent.lstat()
        if not stat.S_ISDIR(v.st_mode) or v.st_uid!=0 or v.st_mode&0o022:
            raise ValueError('Protected package preference ancestry required')


def pin_generation(v):
    return [v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns]


def install_waydroid_pin():
    # Fresh installation only; keep the original package/protocol contract. Never
    # downgrade an existing package or adopt a same-byte foreign preference.
    system();compatible_waydroid(installed_waydroid());protected_parent(WAYDROID_PIN.parent)
    if os.path.lexists(WAYDROID_PIN):raise ValueError('Existing Waydroid preference preserved')
    anchor_dir=WAYDROID_PIN.parent/'.droidmate-waydroid';anchor_dir.mkdir(mode=0o700)
    if anchor_dir.stat().st_dev!=WAYDROID_PIN.parent.stat().st_dev:raise ValueError('Package pin anchor must share target filesystem')
    parent=os.open(WAYDROID_PIN.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent)
    finally:os.close(parent)
    anchor=anchor_dir/'preference';receipt=STATE/'waydroid-pin.json'
    fd=os.open(anchor,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
    try:
        os.fchmod(fd,0o644)
        with os.fdopen(os.dup(fd),'wb')as stream:
            stream.write(WAYDROID_PIN_BYTES);stream.flush();os.fsync(stream.fileno())
        directory=os.open(anchor_dir,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
        parent=os.open(WAYDROID_PIN.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            os.link(anchor,WAYDROID_PIN.name,dst_dir_fd=parent,follow_symlinks=False);os.fsync(fd);os.fsync(parent)
        finally:os.close(parent)
        original=os.fstat(fd)
        if original.st_nlink!=2:raise ValueError('Original owned package pin links required')
        raw=(json.dumps({'schema':1,'sha256':hashlib.sha256(WAYDROID_PIN_BYTES).hexdigest(),
                        'generation':pin_generation(original)},sort_keys=True)+'\n').encode()
        with receipt.open('xb')as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        parent=os.open(STATE,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:os.close(fd)
    check_waydroid_pin()


def check_waydroid_pin():
    protected_parent(WAYDROID_PIN.parent)
    value=json.loads(regular(STATE/'waydroid-pin.json',4096))
    if type(value)is not dict or set(value)!={'schema','sha256','generation'}or type(value['schema'])is not int or value['schema']!=1 or value['sha256']!=hashlib.sha256(WAYDROID_PIN_BYTES).hexdigest():
        raise ValueError('Original private package pin receipt required')
    anchor=WAYDROID_PIN.parent/'.droidmate-waydroid'/'preference'
    protected_parent(anchor.parent)
    for path in (anchor,WAYDROID_PIN):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            v=os.fstat(fd)
            if not stat.S_ISREG(v.st_mode)or (v.st_uid,v.st_gid,v.st_nlink,stat.S_IMODE(v.st_mode))!=(0,0,2,0o644):
                raise ValueError('Original root-owned package pin required')
            if pin_generation(v)!=value['generation']or os.pread(fd,4096,0)!=WAYDROID_PIN_BYTES or pin_generation(path.lstat())!=value['generation']:
                raise ValueError('Owned package pin inode or bytes changed')
        finally:os.close(fd)


def prepare():
    system();os.umask(0o077);STATE.mkdir(mode=0o700)
    inputs=[(deb_name(role),size,pin,'pool/universe/o/openjdk-17/'+deb_name(role)) for role,size,pin in PACKAGES]
    inputs.append((*STATIC_LIBC_PACKAGE,'pool/main/g/glibc/'+STATIC_LIBC_PACKAGE[0]))
    for name,size,pin,relative in inputs:
        path=STATE/name
        # The pinned Ubuntu snapshot can take more than five minutes to
        # transfer the 72 MB JDK. Keep both the connection and transfer bounded.
        transfer_seconds=900
        run(['/usr/bin/curl','--fail','--location','--silent','--show-error','--connect-timeout','15',
             '--max-time',str(transfer_seconds),'--max-filesize',str(size),'--output',str(path),
             SNAPSHOT+relative],timeout=transfer_seconds+10)
        raw=regular(path,size)
        if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=pin:
            raise ValueError('Authenticated compiler download differs')
    package_check();root=STATE/'extracted';root.mkdir(mode=0o700)
    for name,_,_,_ in inputs:run(['/usr/bin/dpkg-deb','--extract',str(STATE/name),str(root)])
    package_check();static_libc_check()
    raw=(json.dumps(toolchain_receipt(),sort_keys=True)+'\n').encode()
    with (STATE/'complete.json').open('xb')as f:f.write(raw);f.flush();os.fsync(f.fileno())


def check():
    package_check()
    if json.loads(regular(STATE/'complete.json',4096))!=toolchain_receipt():raise ValueError('Original private compiler completion required')
    for name in ('java','javac','javap','jar'):regular(STATE/'extracted'/JDK.relative_to('/')/'bin'/name,1024**2)
    static_libc_check();check_waydroid_pin()


def user(value):
    if type(value)is not str or value=='root' or not re.fullmatch('[a-z_][a-z0-9_-]{0,31}',value):raise ValueError('Regular Ubuntu account required')
    return value


def original_build_command(name):
    name=user(name);work=Path('/home')/name/'droiddeck-installation'
    return ['/usr/sbin/runuser','-u',name,'--','/usr/bin/env','-i','PATH=/usr/bin:/bin',
        'HOME=/home/'+name,'LANG=C','LC_ALL=C','JAVA_HOME='+str(JDK),'/usr/bin/node',
        str(PACKAGE/'src/runtime/portable/prepare-install.js'),str(work/'acquired'),str(work/'prepared')]


def build_command(name, original):
    if list(map(str,original))!=original_build_command(name):raise ValueError('Exact original preparation command required')
    return ['/usr/bin/unshare','--mount','--propagation','private','--fork','/usr/bin/python3','-I','-B',str(Path(__file__)),'build',user(name)]


def build(name):
    system();name=user(name);check()
    if installed_waydroid()!=WAYDROID_VERSION:raise ValueError('Qualified Waydroid package required after prerequisites')
    if os.stat('/proc/self/ns/mnt').st_ino==os.stat('/proc/1/ns/mnt').st_ino:
        raise ValueError('Private build mount namespace required before any mount')
    source=STATE/'extracted'/JDK.relative_to('/')
    run(['/usr/bin/mount','--bind',str(source),str(JDK)])
    run(['/usr/bin/mount','-o','remount,bind,ro',str(JDK)])
    run(['/usr/bin/mount','--bind',str(STATE/'extracted'/STATIC_LIBC.relative_to('/')),str(STATIC_LIBC)])
    run(['/usr/bin/mount','-o','remount,bind,ro',str(STATIC_LIBC)])
    if run([str(JDK/'bin/javac'),'-version']).strip()!=b'javac 17.0.20.1':raise ValueError('Original real compiler version required')
    os.execve('/usr/sbin/runuser',original_build_command(name),ENV)


def foundation_sources():
    # Empty package placeholders are allowed only here, still bound to exact hashes.
    raw=regular(PACKAGE/'package-manifest.json',1024**2)
    if hashlib.sha256(raw).hexdigest()!=FOUNDATION_MANIFEST:raise ValueError('Frozen foundation manifest required')
    rows=json.loads(raw)['files']
    if len(rows)!=844:raise ValueError('Complete original foundation inventory required')
    for row in rows:
        rel=row['path']
        if not isinstance(rel,str)or not re.fullmatch('[A-Za-z0-9_./@+-]{1,240}',rel)or '..'in rel.split('/')or rel.startswith('/'):
            raise ValueError('Closed original source path required')
        data=regular(PACKAGE/rel,1024**2,allow_empty=True)
        if len(data)!=row['size']or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Original foundation source required')
    return len(rows)


class NoRuntimeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


@contextmanager
def runtime_deadline(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('Original runtime-start readiness budget exhausted')
    if signal.getitimer(signal.ITIMER_REAL)!=(0.,0.):raise ValueError('Existing process timer preserved')
    previous=signal.getsignal(signal.SIGALRM)
    def expired(_number,_frame):raise TimeoutError('Original runtime-start readiness budget exhausted')
    signal.signal(signal.SIGALRM,expired)
    try:
        signal.setitimer(signal.ITIMER_REAL,remaining)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous)


def wait_runtime_ready(config, deadline):
    with runtime_deadline(deadline):return runtime_ready_request(config,deadline)


def runtime_ready_request(config, deadline):
    # Readiness belongs to the original 150-second runtime-start budget. The
    # untouched defaults phase must still prove actual H264/AAC media afterwards.
    address=config['lanAddress'];port=config['port']
    if str(ipaddress.IPv4Address(address))!=address or type(port)is not int or not 1024<=port<=65535:
        raise ValueError('Original runtime origin required')
    origin='https://'+address+':'+str(port)
    context=ssl.create_default_context(cafile=str(Path(config['home'])/'.config/droiddeck/cert.pem'))
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context),NoRuntimeRedirect())
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Original runtime-start readiness budget exhausted')
        request=urllib.request.Request(origin+'/api/displaySession',data=b'{}',method='POST',
            headers={'Origin':origin,'Content-Type':'application/json'})
        try:
            with opener.open(request,timeout=min(5,remaining))as response:
                raw=response.read(4097)
                if response.status!=200 or len(raw)>4096:raise ValueError('Bounded authenticated runtime session required')
                value=json.loads(raw)
                if type(value)is not dict or type(value.get('token'))is not str or not re.fullmatch('[a-f0-9]{32,128}',value['token']):
                    raise ValueError('Original runtime session shape required')
                if time.monotonic()>=deadline:raise TimeoutError('Original runtime-start readiness budget exhausted')
                return True
        except urllib.error.HTTPError as error:
            retry=error.code==503 and error.headers.get('Retry-After')=='1'
            error.close()
            if not retry:raise
        except urllib.error.URLError as error:
            if not isinstance(error.reason,OSError)or isinstance(error.reason,ssl.SSLError)or error.reason.errno not in (errno.ECONNREFUSED,errno.ECONNRESET):raise
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('Original runtime-start readiness budget exhausted')
        time.sleep(min(.25,remaining))


def foundation_run(original, instance, args, name, lan, *, data=None, timeout=900):
    phase=instance.phase
    if phase=='05-prepare-build':
        if instance.config['user']!=name or data is not None:raise ValueError('Original build account and no input required')
        args=build_command(name,args)
    ready=phase=='30-start-runtime'
    if ready and (instance.config['user']!=name or instance.config['lanAddress']!=lan or data is not None
            or args!=['/usr/bin/systemctl','start','tesla-waydroid-runtime.service']or timeout!=150):
        raise ValueError('Exact original runtime-start command required')
    deadline=time.monotonic()+timeout
    result=original(instance,args,data=data,timeout=timeout)
    if ready:wait_runtime_ready(instance.config,deadline)
    return result


def foundation(name, lan):
    system();user(name);foundation_sources()
    prepare();install_waydroid_pin();check()
    path=PACKAGE/'src/runtime/portable/install-machine.py'
    spec=importlib.util.spec_from_file_location('original_pinned_toolchain_foundation',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=module.Install.run
    def run_with_build_context(self,args,*,data=None,timeout=900):
        return foundation_run(original,self,args,name,lan,data=data,timeout=timeout)
    module.Install.run=run_with_build_context
    try:return module.main(['--install-machine',name,lan,'droiddeck.invalid'])
    finally:module.Install.run=original


def main(args):
    if len(args)==2 and args[0]=='build':return build(args[1])
    if len(args)==3 and args[0]=='foundation':return foundation(args[1],args[2])
    raise ValueError('Closed private compiler action required')


if __name__=='__main__':main(sys.argv[1:])
