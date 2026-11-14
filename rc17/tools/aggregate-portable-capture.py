# SPDX-License-Identifier: GPL-3.0-only
"""Reuse the original signed portable capture authority during aggregate updates.

No new capture authorization, enrollment, core update, boot-preference replay or
service-registration path. This internal bridge requires the signed entry's
retained ctx and exposes the original lease only after CPU owner drain.
"""
from contextlib import ExitStack,contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import types

CORE='1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
PACKAGE='a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da'
BOOTSTRAP_PIN='22c52ff53faeae67716171d6d15e5dfc79ff11b9268fe59037406e651961f707'
LOADER=Path('/var/lib/droiddeck-capture-loader/bootstrap.py')
SOURCE='runtime/portable/capture-control-bootstrap.py'

def retain(ctx):
    if type(ctx) is not dict or set(ctx)!={'root','release','check'} or not callable(ctx['check']):raise ValueError('Retained aggregate context required')
    root,callback=ctx['root'],ctx['check']
    def encode(v):return json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
    raw=encode(ctx['release']);release=json.loads(raw)
    if (type(release) is not dict or release.get('kind')!='droidmate-aggregate-release-authorization'
            or release.get('aggregateRelease')!='RC17' or release.get('coreRuntimeVersion')!='0.1.1-rc.20'
            or release.get('compatibleReleaseSha256')!=CORE or release.get('activationAllowed') is not True
            or type(release.get('packageSha256')) is not str or not re.fullmatch('[a-f0-9]{64}',release['packageSha256'])
            or root!=Path('/usr/local/share/droidmate-rc17-'+release['packageSha256'])):
        raise ValueError('Original aggregate domain/root required')
    def check():
        def same():
            if (set(ctx)!={'root','release','check'} or ctx['root']!=root or ctx['check'] is not callback
                    or encode(ctx['release'])!=raw):raise ValueError('Original aggregate binding changed')
        same()
        if callback() is not True:raise ValueError('Original aggregate admission lost')
        same();return True
    check();return {'root':root,'release':release,'check':check}

def read_loader_source(ctx):
    ctx['check']();path=ctx['root']/'source'/SOURCE;raw=path.read_bytes()
    if len(raw)!=20459 or hashlib.sha256(raw).hexdigest()!=BOOTSTRAP_PIN:raise ValueError('Original signed loader integration bytes required')
    ctx['check']();return raw

def loader(ctx):
    raw=read_loader_source(ctx)
    # Compile ONLY signed aggregate source, never unverified installed code.
    # The same bytes must be the actual protected independently enrolled loader.
    module=types.ModuleType('original_portable_capture_loader');module.__file__=str(LOADER)
    exec(compile(raw,str(LOADER),'exec'),module.__dict__)
    with ExitStack() as stack:
        tree=module.Tree(stack)
        if tree.file(LOADER,1048576)!=raw:raise ValueError('Original installed loader bytes required')
        tree.check();ctx['check']()
    return module

def cpu_status(value):
    return (type(value) is dict and value.get('phase') in ('restored','no-operation')
            and value.get('updaterLeaseHeld') is False)

def gpu_status(value):
    return (type(value) is dict and value.get('phase')=='accepted' and value.get('mode')=='gpu'
            and value.get('updaterLeaseHeld') is True)

def require_release(report):
    if (type(report) is not dict or report.get('kind')!='verified-installed-pilot-release'
            or report.get('version')!='0.1.1-rc.20' or report.get('signedBundleSha256')!=CORE
            or any(report.get(k) is not True for k in ('signedReleaseVerified','installedCodeOwnershipVerified','updateLockShared'))
            or report.get('servicesChanged') is not False):raise ValueError('Original immutable core20 authority required')
    return True

class CaptureRecoveryPending(ValueError):
    """Owned original controller has not finished restoring; never CPU-ready."""


class Capture:
    def __init__(self,ctx,*,_loader=loader,_clock=time.monotonic,_pause=time.sleep):
        self.ctx=retain(ctx);self.load=_loader;self.clock=_clock;self.pause=_pause
        self.stack=ExitStack();self.closed=False;self.lease=False

    def __enter__(self):
        try:
            self.ctx['check']();self.b=self.load(self.ctx);self.ctx['check']()
            self.owner=self.stack.enter_context(self.b.hold_self())
            self.c=self.stack.enter_context(self.b.hold_controls(PACKAGE))
            if self.c['statement']['compatibleReleaseSha256']!=CORE or self.c['statement']['packageSha256']!=PACKAGE:
                raise ValueError('Original compatible signed capture package required')
            self.v=self.c['module']('capture-view-controller.py')
            self.r=self.c['module']('capture-release-authority.py')
            self.check();return self
        except BaseException:self.close();raise

    def check(self):
        if self.closed:raise ValueError('Closed original portable capture admission')
        if self.ctx['check']() is not True:raise ValueError('Original aggregate source admission lost')
        if self.owner() is not None or self.c['check']() is not True:raise ValueError('Original capture enrollment/controls changed')
        if self.ctx['check']() is not True:raise ValueError('Original aggregate source admission changed')
        return True

    def owned_socket(self):
        """Bind a live socket to this signed package's protected operation."""
        self.check()
        with ExitStack() as stack:
            tree=self.b.Tree(stack);root=tree.directory(self.v.ROOT)
            if stat.S_IMODE(os.fstat(root).st_mode)!=0o700:raise ValueError('Original private controller directory required')
            op=self.v.record(self.b.document(tree.file(self.v.ROOT/'state.json',131072)))
            socket=self.v.SOCKET.lstat()
            if (op['packageSha256']!=PACKAGE or op['phase']!='accepted'
                    or op['boot']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                    or not stat.S_ISSOCK(socket.st_mode) or (socket.st_uid,socket.st_gid,socket.st_nlink,stat.S_IMODE(socket.st_mode))!=(0,0,1,0o600)
                    or op.get('socketIdentity')!=self.v.pin(socket)):
                raise ValueError('Live original package/operation/socket identity required')
            tree.check();self.check();return {'token':op['token'],'viewToken':op['viewToken'],'socketIdentity':self.v.pin(socket)}

    def status(self):
        """Read-only status: never invoke orphaned controller rebind/recovery."""
        self.check()
        if os.path.lexists(self.v.SOCKET):
            owner=self.owned_socket();result=self.v.client('status')
            if self.owned_socket()!=owner:raise ValueError('Original controller changed during status')
            if not gpu_status(result):raise ValueError('Original live GPU controller required')
        else:
            state=self.v.ROOT/'state.json'
            if os.path.lexists(state):
                with ExitStack() as stack:
                    tree=self.b.Tree(stack);raw=tree.file(state,1048576)
                    op=self.v.record(self.b.document(raw));tree.check()
                    if op['packageSha256']!=PACKAGE:raise ValueError('Foreign original capture package preserved')
                    if op['phase']!='restored':raise CaptureRecoveryPending('Original capture requires its own recovery')
                result={'phase':'restored','mode':'cpu','updaterLeaseHeld':False}
            elif os.path.lexists(self.v.ROOT):raise ValueError('Incomplete original controller namespace preserved')
            else:result={'phase':'no-operation','mode':'cpu','updaterLeaseHeld':False}
        self.check();return result

    @contextmanager
    def release(self):
        """Original exclusive updater lease; caller must release before change."""
        self.check()
        if self.lease or not cpu_status(self.status()) or os.path.lexists(self.v.SOCKET):
            raise ValueError('CPU restoration/owner drain required before original lease')
        with self.r.hold_release() as original:
            require_release(original['report']);self.lease=True
            try:
                def check():
                    self.check();report=original['check']();require_release(report)
                    if os.path.lexists(self.v.SOCKET) or not cpu_status(self.status()):raise ValueError('CPU/socket state changed under original lease')
                    self.check();return True
                check();yield {'report':dict(original['report']),'check':check};check()
            finally:self.lease=False

    def restore(self,*,deadline_seconds=180):
        if type(deadline_seconds) not in (int,float) or not math.isfinite(deadline_seconds) or not 0<deadline_seconds<=300:
            raise ValueError('Bounded original restoration deadline required')
        self.check()
        if self.lease:raise ValueError('Release original updater lease before capture restoration')
        previous=None
        def now():
            nonlocal previous
            value=self.clock()
            if type(value) not in (int,float) or not math.isfinite(value) or value<0 or previous is not None and value<previous:
                raise ValueError('Finite monotonic restoration clock required')
            previous=value;return value
        start=now()
        def budget():
            if now()-start>=deadline_seconds:raise TimeoutError('Original capture owner did not finish; preserve recovery')
        if os.path.lexists(self.v.SOCKET):
            if not gpu_status(self.status()):raise ValueError('Original accepted GPU owner required')
            owner=self.owned_socket();self.check();budget()
            if self.owned_socket()!=owner:raise ValueError('Original capture operation changed before restoration')
            reply=self.v.client('restore');self.check();budget()
            if type(reply) is not dict or reply.get('phase') not in ('restored','no-operation'):
                raise ValueError('Original owner did not acknowledge restoration')
        while True:
            self.check();budget()
            try:
                if not os.path.lexists(self.v.SOCKET) and cpu_status(self.status()):
                    with self.release() as original:original['check']()
                    self.check();budget();return {'phase':'restored','mode':'cpu','updaterLeaseHeld':False,'originalLeaseAvailable':True}
            except (BlockingIOError,CaptureRecoveryPending):
                # The original recovery can move state between the read-only CPU
                # probe and exclusive lease acquisition. Wait, never dispatch or
                # rebind an orphan. Source/package/ownership errors still refuse.
                pass
            self.pause(.1)

    def change(self,view):
        if type(view) is not str or not re.fullmatch('[a-f0-9]{32}',view):raise ValueError('Existing authenticated GPU view token required')
        self.check()
        if self.lease:raise ValueError('Original updater lease cannot overlap a new capture operation')
        if not cpu_status(self.status()) or os.path.lexists(self.v.SOCKET):raise ValueError('Original CPU worker required before fresh GPU admission')
        result=self.v.dispatch(self.c,'change',[view]);self.check()
        if not gpu_status(self.status()):raise ValueError('Fresh signed GPU operation did not remain accepted')
        self.check();return result

    def close(self):
        if not self.closed:self.closed=True;self.stack.close()

    def __exit__(self,*args):self.close()
