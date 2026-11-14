# SPDX-License-Identifier: GPL-3.0-only
"""Enroll original signed capture controls and boot preference for aggregate installs.

No capture activation, Android/account/DRM changes or host profile operations.
Original frozen enrollment/staging APIs own their receipts and partial-state rules.
"""
from contextlib import ExitStack,contextmanager
import hashlib,io,json,os,re,secrets,stat,subprocess,tarfile,types
from pathlib import Path

PACKAGE='a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da'
COMPONENT='0747a1d63f455cfcf003f326d7f9ed3c5e6de63dfee5ae4498799384d033bc92'
STATEMENT_PIN='e4da601ed7c7fdad932f3f24ac29365299a76dce1d07c1964de9c02a3b6cae69'
SIGNATURE_PIN='dcb2176f849e596d625d81926a50a3d0227c2f72caa017b674f4c5c60a7243cd'
STATEMENT='runtime/portable/capture-component-staging.statement.json'
SIGNATURE='runtime/portable/capture-component-staging.sig'
ENROLLER_PIN='44252a64a419cdfac80f7f8c898a17ef9ee1a9a5b82896a93a3222478a9502c1'
STATE=Path('/var/lib/droidmate-guest-enrollment')


def sha(raw):return hashlib.sha256(raw).hexdigest()

def public_receipts(statement,signature):
    if (type(statement) is not bytes or (len(statement),sha(statement))!=(642,STATEMENT_PIN)
            or type(signature) is not bytes or (len(signature),sha(signature))!=(64,SIGNATURE_PIN)):
        raise ValueError('Original generic signed component staging receipts required')
    return True


class Native:
    def __init__(self,ctx,material,compiler):
        self.m=compiler;self.ctx=compiler.retain(ctx);self.material=material;self.stack=ExitStack();self.view_token=None;self.inputs=None
        try:
            self.p=compiler.source_module(self.ctx,'tools/aggregate-portable-capture.py')
            raw=self.p.read_loader_source(self.ctx);self.b=types.ModuleType('aggregate_fresh_original_loader');self.b.__file__=str(self.p.LOADER)
            exec(compile(raw,str(self.p.LOADER),'exec'),self.b.__dict__);self.bootstrap=raw
            self.tree=self.b.Tree(self.stack)
            helper=compiler.source_module(self.ctx,'tools/aggregate-runtime.py')
            self.i,self.program,self.program_raw=helper.guest_helpers(material)
            self.f=compiler.source_module(self.ctx,'tools/aggregate-fresh-install.py')
            self.stager=self.f.load_stager(self.f.Binding(self.ctx));self.root=STATE/self.ctx['release']['packageSha256']
            self.statement=compiler.source_bytes(self.ctx,STATEMENT);self.signature=compiler.source_bytes(self.ctx,SIGNATURE)
            public_receipts(self.statement,self.signature)
            self.context()
        except BaseException:self.stack.close();raise

    def context(self):
        if self.ctx['check']() is not True:raise ValueError('Original aggregate enrollment source required')
        self.tree.check();return True

    def inspect(self):
        self.context()
        if os.path.lexists(self.program.ROOT):return {'complete':True}
        if os.path.lexists(self.root):raise ValueError('Original partial enrollment preserved; no replay')
        return {'complete':False}

    def directory(self,path,mode=0o700):
        self.context();parent=self.tree.directory(path.parent)
        if not os.path.lexists(path):os.mkdir(path.name,mode=mode,dir_fd=parent);os.fsync(parent)
        fd=self.tree.directory(path)
        if stat.S_IMODE(os.fstat(fd).st_mode)!=mode:raise ValueError('Original private enrollment directory required')
        self.context();return fd

    def close(self):self.stack.close()

    def record(self,name,value):
        if name not in {'intent.json','loader.json','component.json','view.json','preference.json','complete.json'}:raise ValueError('Closed enrollment record name required')
        self.context();raw=self.b.encode(value);self.b.write_new(self.tree.directory(self.root),name,raw)
        if self.tree.file(self.root/name,16384)!=raw:raise ValueError('Original enrollment record required')
        self.context()

    @contextmanager
    def claim(self):
        if os.name!='posix' or os.geteuid()!=0:raise ValueError('Root Linux original enrollment required')
        self.context();self.directory(STATE);parent=self.tree.directory(STATE)
        os.mkdir(self.root.name,mode=0o700,dir_fd=parent);os.fsync(parent);fd=self.tree.directory(self.root)
        import fcntl
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            # The outer activation signature is checked independently before
            # original loader/control source is used for any enrollment effects.
            trust=self.tree.file(self.b.TRUST,4096)
            value,files=self.b.authenticate(self.material.assets['capturePackage'],self.material.assets['captureStatement'],self.material.assets['captureSignature'],trust)
            if value['packageSha256']!=PACKAGE or value['componentSha256']!=COMPONENT:raise ValueError('Original signed runtime component required')
            self.control_files=files
            with tarfile.open(fileobj=io.BytesIO(self.material.assets['capturePackage']),mode='r:') as archive:
                component=archive.extractfile(self.b.PREFIX+'component.tar').read()
            if sha(component)!=COMPONENT:raise ValueError('Original component bytes required')
            # Use the immutable guest program's exact matching recovery template.
            recovery=self.program.recovery_unit()
            operator={'bootstrap.py':self.bootstrap,'package.tar':self.material.assets['capturePackage'],
                'statement.json':self.material.assets['captureStatement'],'signature.bin':self.material.assets['captureSignature'],
                'recovery.service':recovery}
            payload={**{'operator/'+n:b for n,b in operator.items()},
              'operator/operator-inputs.json':self.b.encode({n:{'size':len(b),'sha256':sha(b)} for n,b in operator.items()}),
              'component.tar':component,'component-statement.json':self.statement,'component-signature.bin':self.signature,'guest-program.py':self.program_raw}
            self.stager.materialize(self.root/'inputs',payload)
            for n,b in payload.items():
                if self.tree.file(self.root/'inputs'/n,44*1024**2)!=b:raise ValueError('Original staged enrollment inputs required')
            self.inputs=self.root/'inputs';self.context();yield self;self.context()
        finally:self.stack.close()

    def loader(self):
        self.context()
        if os.path.lexists(self.b.SELF.parent) or os.path.lexists(self.b.SERVICE):
            with self.p.Capture(self.ctx) as capture:capture.check()
            return True
        path=self.ctx['root']/'source/tools/enroll-capture-control.py';raw=path.read_bytes()
        if sha(raw)!=ENROLLER_PIN:raise ValueError('Original frozen loader enroller required')
        self.context()
        result=self.f.run_owned(['/usr/bin/python3','-I','-B',str(path),str(self.inputs/'operator'),self.ctx['release']['sourceRevision'],PACKAGE],subprocess.DEVNULL,subprocess.DEVNULL,180)
        if result.returncode:raise ValueError('Original loader enrollment incomplete; preserve original intent')
        with self.p.Capture(self.ctx) as capture:capture.check()
        self.context();return True

    def component(self):
        self.context()
        with self.p.Capture(self.ctx) as capture:
            stage=capture.c['module']('capture-component-stage.py')
            if os.path.lexists(stage.ROOT/COMPONENT):
                with stage.hold_component(COMPONENT) as component:component['check']()
            else:stage.stage(str(self.inputs/'component.tar'),str(self.inputs/'component-statement.json'),str(self.inputs/'component-signature.bin'))
        self.context();return True

    def view(self):
        self.context()
        with self.p.Capture(self.ctx) as capture:
            view=capture.c['module']('capture-private-view.py');state=capture.v.ROOT/'state.json'
            if os.path.lexists(state):
                value=capture.v.record(json.loads(self.tree.file(state,131072)))
                if value['packageSha256']!=PACKAGE or value['phase']!='restored':raise ValueError('Existing original CPU operation required before fresh enrollment')
                token=value['viewToken']
                with view.hold_view(token) as original:
                    if original['report']['mode']!='gpu' or original['report']['componentSha256']!=COMPONENT:raise ValueError('Original complete GPU view required')
                    original['check']()
            else:
                token=secrets.token_hex(16);view.prepare(COMPONENT,token,'gpu')
            self.view_token=token
        self.context();return True

    def machine_id(self):
        path=Path('/etc/machine-id');parent=self.tree.directory(path.parent)
        mode=stat.S_IMODE(os.stat(path.name,dir_fd=parent,follow_symlinks=False).st_mode)
        if mode not in (0o444,0o644):raise ValueError('Protected original machine identity required')
        return self.tree.file(path,4096,mode)

    def preference(self):
        self.context()
        if not re.fullmatch('[a-f0-9]{32}',self.view_token or ''):raise ValueError('Original authenticated GPU view required')
        b=self.program
        preference=b.preference(b.encode({'schema':1,'kind':'droidmate-gpu-boot-preference','packageSha256':PACKAGE,
          'viewToken':self.view_token,'loaderSha256':self.p.BOOTSTRAP_PIN,'machineSha256':sha(self.machine_id())}))
        history=b.ROOT.parent/('droidmate-gpu-install-rc17-'+self.ctx['release']['packageSha256'][:16])
        for action in ('enroll','arm'):
            self.context()
            with ExitStack() as stack:
                tree=object.__new__(b.Native);tree.stack,tree.held,tree.dirs,tree.records=stack,{},{},{};tree.p=preference
                if tree.read_file(self.inputs/'guest-program.py',65536)!=self.program_raw:raise ValueError('Original guest program source required')
                self.i.transaction(b,tree,self.program_raw,preference,history,action)
            self.context()
        return True

    def verify(self):
        self.context();b=self.program
        raw=self.tree.file(b.ROOT/'preference.json',4096);preference=b.preference(raw)
        if preference['packageSha256']!=PACKAGE or preference['loaderSha256']!=self.p.BOOTSTRAP_PIN:raise ValueError('Original signed capture preference required')
        with ExitStack() as stack:
            original=b.Native(stack,sha(raw));original.armed();self.i.original_loader(b,original,original.p)
            files=self.i.payload(b,self.program_raw,original.p)
            self.i.installation_provenance(b,original,files,original.p);original.verify()
        with self.p.Capture(self.ctx) as capture:capture.check()
        self.context();return True


def enroll(ctx,material,compiler):
    native=Native(ctx,material,compiler)
    report={'kind':'aggregate-original-capture-enrolled','captureEnrolled':True,'bootPreferenceEnrolled':True,
            'servicesStarted':False,'hostProfileInstalled':False,'releaseComplete':False}
    try:
        native.context()
        if native.inspect()['complete']:
            if native.verify() is not True:raise ValueError('Original existing enrollment refused')
            return report
        with native.claim():
            native.record('intent.json',{'kind':'aggregate-original-capture-enrollment'})
            for phase in ('loader','component','view','preference'):
                native.context()
                if getattr(native,phase)() is not True:raise ValueError('Original enrollment step refused: '+phase)
                native.context();native.record(phase+'.json',{'completed':True})
            if native.verify() is not True:raise ValueError('Original full enrollment verification required')
            native.record('complete.json',report)
        return report
    finally:native.close()
