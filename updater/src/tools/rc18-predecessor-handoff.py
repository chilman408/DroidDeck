# SPDX-License-Identifier: GPL-3.0-only
"""Successor handoff template, appended only to the reconstructed signed backend.

No standalone entry, import-time effects or arbitrary paths. Only portable RC17
is recognized. Recovery never retries retirement or installs a new transaction.
"""
HANDOFF_PREDECESSOR='7c9a48e879a71cd69d03ce587e2feeef32f231a375c3be3deb685c5b963950d1'
HANDOFF_OLD_CAPTURE='a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da'
HANDOFF_NEW_CAPTURE='@@NEW_CAPTURE@@'
HANDOFF_ENTRY_PIN='@@OLD_ENTRY_PIN@@'
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
HANDOFF_PHASES=('new','intent','armed','cpu','stopped','mapped','loaded','started','verified','accepted')
HANDOFF_METHODS=('admit','stage','restore_predecessor','retire_preference','switch_recovery','install_successor','check_successor','verify','finish')


def handoff_transact(journal,backend,action):
    if action not in ('upgrade-existing','upgrade-recover'):raise ValueError('Closed successor handoff action required')
    backend.check();phase=journal.state()
    if action=='upgrade-recover':
        if phase=='new':return {'phase':'new','servicesChanged':False}
        if phase=='accepted':backend.verify();backend.finish();return {'phase':phase,'servicesChanged':False}
        if phase=='restored':backend.verify_cpu();backend.finish();return {'phase':phase,'servicesChanged':False}
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


class SuccessorHandoff:
    def __init__(self,ctx,engine,journal,material,compiler):
        self.ctx,self.e,self.j,self.material,self.m=ctx,engine,journal,material,compiler
        self.stack=ExitStack();self.p=compiler.source_module(ctx,'tools/aggregate-portable-capture.py')
        self.b=self.p.loader(ctx);self.tree=self.b.Tree(self.stack)
        self.i,self.program,self.program_raw=guest_helpers(material)
        baseline=journal.root/'baseline.json'
        if os.path.lexists(baseline):preference=self.program.preference(self.b.encode(journal.read('baseline.json')['preference']))
        else:
            with ExitStack() as stack:
                preference=self.program.preference(self.b.Tree(stack).file(self.program.ROOT/'preference.json',4096))
        self.old_capture=preference['packageSha256']
        if self.old_capture not in HANDOFF_PREDECESSORS:raise ValueError('Only an explicit publisher-pinned predecessor is supported')
        self.predecessor=HANDOFF_PREDECESSORS[self.old_capture]
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
        with self.b.hold_self():self.b.stage(*(str(self.inputs/n) for n in names),HANDOFF_NEW_CAPTURE)
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
            raw=self.b.Tree(stack).file(path,8192,0o644)
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
    def verify_cpu(self,predecessor=False):
        if os.path.lexists(Path('/var/lib/droiddeck-capture-switch/control.sock')):raise ValueError('Capture owner has not drained')
        for unit in PEERS:
            fields=self.run(['/usr/bin/systemctl','is-active',unit]).strip()
            if fields!=b'active':raise ValueError('Healthy CPU services required')
        with ExitStack() as stack:
            state=json.loads(self.b.Tree(stack).file(Path('/var/lib/droiddeck-capture-switch/state.json'),131072))
        if state.get('phase')!='restored':raise ValueError('Restored CPU capture required')
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
    if args not in (['upgrade-existing'],['upgrade-recover']):raise ValueError('Closed successor handoff route')
    m=load_material(ctx);ctx=m.retain(ctx);material=m.materialize(ctx)
    tag=ctx['release']['packageSha256'][:16]
    entry=ctx['root']/'source/tools/aggregate-installer-entry.py'
    unit='droidmate-successor-install-'+tag+'.service'
    group=('0::/system.slice/'+unit+'\n').encode('ascii')
    if args==['upgrade-existing'] and Path('/proc/self/cgroup').read_bytes()!=group:
        for base in ('/etc/systemd/system','/run/systemd/system'):
            if any(os.path.lexists(Path(base)/n) for n in (unit,unit+'.d')):raise ValueError('Foreign handoff worker definition preserved')
        argv=['/usr/bin/systemd-run','--unit='+unit,'--wait','--collect',
          '--property=Type=oneshot','--property=KillMode=control-group',
          '--property=TimeoutStartSec=4500','--property=TimeoutStopSec=180',
          '--property=OnFailure=droidmate-successor-recovery-'+tag+'.service',
          '--property=OnFailureJobMode=fail','--property=UMask=0077',
          '--property=Environment=PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C',
          '--property=UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH PYTHONPATH PYTHONHOME DBUS_SYSTEM_BUS_ADDRESS NODE_OPTIONS',
          '/usr/bin/python3','-I','-B',str(entry),'--enrolled','upgrade-existing']
        result=subprocess.run(argv,cwd='/',env={'PATH':'/usr/bin:/bin','HOME':'/root','LANG':'C','LC_ALL':'C'},stdin=subprocess.DEVNULL,capture_output=True,timeout=4600)
        ctx['check']()
        if result.returncode:raise ValueError('Supervised handoff refused; retain independent recovery')
        return {'phase':'accepted','servicesChanged':True,'installerSupervised':True}
    if args==['upgrade-existing']:
        expected=[b'/usr/bin/python3',b'-I',b'-B',str(entry).encode('ascii'),b'--enrolled',b'upgrade-existing']
        if Path('/proc/self/cmdline').read_bytes().rstrip(b'\0').split(b'\0')!=expected:raise ValueError('Exact enrolled handoff worker required')
    root=Path('/var/lib/droidmate-successor');parent=engine.Journal.__new__(engine.Journal)
    parent.dirs={};parent.files={};parent.closed=False
    try:
        fd=parent.directory(root.parent)
        if not os.path.lexists(root):os.mkdir(root.name,0o700,dir_fd=fd);os.fsync(fd)
    finally:parent.close()
    path=root/ctx['release']['packageSha256'];create=not os.path.lexists(path)
    if create and args==['upgrade-recover']:raise ValueError('Original handoff journal required')
    with engine.Journal(path,{'packageSha256':ctx['release']['packageSha256'],'layout':'portable'},create=create) as journal:
        b=SuccessorHandoff(ctx,engine,journal,material,m)
        try:return handoff_transact(journal,b,args[0])
        finally:b.close()
