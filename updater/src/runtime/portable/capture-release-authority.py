# SPDX-License-Identifier: GPL-3.0-only
"""Read-only bridge to the original enrolled pilot release/ownership authority.

No updater dispatch/bootstrap/repair, policy write, Android operation or activation.
RC20 can be inspected, but lacks a signed complete capture-control component.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading

SOURCE = Path('/var/lib/droiddeck/gateway-update-pilot/sources')
ENTRY = 'runtime/portable/gateway-update-release.py'
CATALOG_PIN = '858ccbef278c91fc8a139161f2572518168c7907871adb78f2e783b0a8050fdf'
CATALOG = Path(__file__).with_name('capture-release-verifier.json')
_FALSE = {'readyForActivation': False, 'effectiveGraphVerified': False, 'nativeLeaseVerified': False,
          'realMediaAcceptanceVerified': False, 'servicesChanged': False, 'androidOperationsExecuted': False}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def encode(value): return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def catalog(raw):
    try:
        if type(raw) is not bytes or len(raw) != 14186 or sha(raw) != CATALOG_PIN: raise ValueError()
        value = json.loads(raw)
        if (set(value) != {'version','kind','sourceRevision','baseRevision','files'} or value['version'] != 1
                or value['kind'] != 'capture-release-verifier-catalog' or encode(value) != raw
                or value['sourceRevision'] != '99cee2ab221cc182aeb43ff2a99431a755fe000f'
                or value['baseRevision'] != 'c0e3a5a4a6b1e41647159034227ea08b8ff6c139'
                or len(value['files']) != 99): raise ValueError()
        result = {}
        for row in value['files']:
            if (set(row) != {'path','size','sha256'} or type(row['path']) is not str
                    or not re.fullmatch(r'(?:runtime/portable|recipes/runtime/portable|tools)/[a-z0-9][a-z0-9.-]*\.py', row['path'])
                    or row['path'] in result or type(row['size']) is not int or not 0 < row['size'] <= 1048576
                    or type(row['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', row['sha256'])): raise ValueError()
            result[row['path']] = (row['size'],row['sha256'])
        if list(result) != sorted(result) or ENTRY not in result: raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError('Independent release verifier catalog rejected') from None


def identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


class HeldCode:
    def __init__(self, stack, root, pins):
        self.stack,self.root,self.pins = stack,Path(root),pins
        self.directories,self.files = {},{}
        if not self.root.is_absolute() or str(PurePosixPath(str(root))) != str(root) or '..' in self.root.parts: raise ValueError('Fixed canonical verifier root required')
        for name,pin in pins.items():
            file=self.root/name; parent=self.directory(file.parent)
            fd=os.open(file.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);stack.callback(os.close,fd)
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or (info.st_uid,info.st_gid,info.st_nlink,stat.S_IMODE(info.st_mode))!=(0,0,1,0o600)
                    or info.st_size!=pin[0]): raise ValueError('Protected verifier source required')
            raw=os.pread(fd,pin[0]+1,0)
            if len(raw)!=pin[0] or sha(raw)!=pin[1]: raise ValueError('Verifier source differs from independent catalog')
            self.files[str(file)]=(parent,file.name,fd,identity(info),raw)
        self.check()

    def directory(self,path):
        path=Path(path)
        if str(path) in self.directories:return self.directories[str(path)][2]
        if path==Path('/'):parent,name=None,'/'
        else:parent,name=self.directory(path.parent),path.name
        fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);self.stack.callback(os.close,fd)
        info=os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Protected verifier ancestry required')
        self.directories[str(path)]=(parent,name,fd,(info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid))
        return fd

    def check(self):
        for parent,name,fd,before in self.directories.values():
            for info in (os.fstat(fd),os.stat(name,dir_fd=parent,follow_symlinks=False)):
                if (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid)!=before:raise ValueError('Verifier ancestry changed')
        for parent,name,fd,before,raw in self.files.values():
            if identity(os.fstat(fd))!=before or identity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=before:raise ValueError('Held verifier source changed')

    def bytes(self,path):
        self.check()
        key=str(path)
        if key not in self.files:raise ValueError('Uncatalogued executable verifier source refused')
        return self.files[key][4]


class BytesLoader(importlib.abc.Loader):
    def __init__(self,held,path):self.held,self.path=held,path
    def create_module(self,spec):return None
    def exec_module(self,module):
        # Compile the held verified bytes, never reopen a mutable module pathname.
        raw=self.held.bytes(self.path)
        exec(compile(raw,str(self.path),'exec'),module.__dict__)
        self.held.check()


@contextmanager
def verified_imports(held):
    if threading.active_count()!=1:raise ValueError('Isolated single-thread verifier process required')
    original=importlib.util.spec_from_file_location
    def spec(name,location=None,*args,**kwargs):
        path=Path(location) if location is not None else None
        if path is None or str(path) not in held.files:raise ValueError('Uncatalogued verifier import refused')
        result=importlib.machinery.ModuleSpec(name,BytesLoader(held,path),origin=str(path));result.has_location=True
        return result
    importlib.util.spec_from_file_location=spec
    try:yield spec
    finally:importlib.util.spec_from_file_location=original


def observed_locked_release(wrapper, context):
    wrapper.B.inspect_bootstrap(context)
    rows=wrapper.C.records();current=wrapper.C.current_view(rows)
    if current is None:raise ValueError('Current authenticated release required')
    side,phases=current.observe()
    if side!='after' or not {'applied.json','healthy.json'}<=phases or phases&{'rollback-request.json','rollback-intent.json','rolled-back.json','boot-code-restored.json'}:
        raise ValueError('Healthy accepted signed release required, not an unfinished update')
    current.active.metadata()
    admission,manifest,bundle,generations=wrapper.B.signed_input(current.digest)
    config=wrapper.G.protected_read(Path('/etc/droiddeck/runtime.json'),0o644,16384)
    release_digest=sha(wrapper.P.encode({'context':context,'history':rows,'admission':admission,'generations':generations}))
    source_digest=sha(wrapper.P.encode({'installedGateway':current.after,'originalNamespace':current.value['originalNamespace'],'target':manifest['target']}))
    # Current signed update protocol covers gateway code only. It does not
    # admit the complete new portable controller/policy dependencies. Never
    # infer capture support from an unowned file appearing beside the runtime.
    return {'kind':'verified-installed-pilot-release','version':manifest['target']['version'],
            'signedBundleSha256':current.digest,'binding':{'sourceSha256':source_digest,'configSha256':sha(config),'releaseSha256':release_digest},
            'signedReleaseVerified':True,'installedCodeOwnershipVerified':True,'updateLockShared':True,
            'capturePolicyRuntimeSupported':False,'verifierSources':99,'readOnly':True,**_FALSE}


def observed_release(wrapper):
    wrapper.inspect_enrollment()
    with wrapper.C.locked() as context:
        return observed_locked_release(wrapper, context)


def read_catalog():
    fd=os.open(CATALOG,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_nlink!=1
                or before.st_mode&0o022 or before.st_size!=14186):raise ValueError('Protected verifier catalog required')
        raw=os.read(fd,14187)
        if identity(os.fstat(fd))!=identity(before) or identity(CATALOG.lstat())!=identity(before):raise ValueError('Verifier catalog changed')
        return raw
    finally:os.close(fd)


@contextmanager
def verified_wrapper():
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('Root Linux release inspection required')
    pins=catalog(read_catalog())
    with ExitStack() as stack:
        held=HeldCode(stack,SOURCE,pins)
        with verified_imports(held) as spec:
            definition=spec('capture_verified_pilot',SOURCE/ENTRY)
            wrapper=importlib.util.module_from_spec(definition);definition.loader.exec_module(wrapper)
            original=wrapper.G.command
            def readonly_command(args,*pos,**kwargs):
                if not isinstance(args,list) or len(args)<3 or args[:2]!=['/usr/bin/systemctl','show']:raise ValueError('Read-only release inspection command refused')
                return original(args,*pos,**kwargs)
            wrapper.G.command=readonly_command
            yield wrapper, held
            held.check()


def inspect_release():
    with verified_wrapper() as (wrapper, held):
        result=observed_release(wrapper)
        held.check()
        return result


@contextmanager
def hold_release():
    """Retain the ORIGINAL updater lock for an inert component staging operation.

    Full checks at operation boundaries, not a per-frame/recycle polling adapter.
    This never enables capture_binding or admits a changed effective service graph.
    """
    with verified_wrapper() as (wrapper, held):
        wrapper.inspect_enrollment()
        with wrapper.C.locked() as context:
            report=observed_locked_release(wrapper, context)
            expected=encode(report)
            def check():
                held.check()
                current=observed_locked_release(wrapper, context)
                if encode(current)!=expected:raise ValueError('Installed release changed during capture staging')
                return current
            yield {'report':report, 'check':check}
            check()


def capture_binding(report):
    # No side effects. Until a reviewed complete signed capture component is
    # defined, even a healthy RC20 report must never authorize a controller lock.
    if (type(report) is not dict or report.get('kind')!='verified-installed-pilot-release'
            or report.get('capturePolicyRuntimeSupported') is not True):
        raise ValueError('Installed release does not admit the complete capture-policy runtime')
    raise ValueError('Capture component admission recipe not yet defined')


def main(args):
    if args!=['inspect']:raise ValueError('Read-only installed-release inspect only')
    print(json.dumps(inspect_release(),sort_keys=True),flush=True)


if __name__=='__main__':
    try:main(sys.argv[1:])
    except Exception as error:
        print('Installed release verification rejected: '+type(error).__name__,file=sys.stderr);sys.exit(1)
