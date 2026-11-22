# SPDX-License-Identifier: GPL-3.0-only
"""Child-only runtime view primitive. No CLI, package authority or service writer."""
from contextlib import ExitStack
import ctypes
import math
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import subprocess
import sys
import threading
import time

ENV_KEYS=frozenset(('PATH','HOME','LANG','LC_ALL','USER','LOGNAME','XDG_RUNTIME_DIR',
    'DBUS_SESSION_BUS_ADDRESS','NODE_PATH','LD_LIBRARY_PATH',
    'TESLA_WAYDROID_RUNTIME_OWNER','TESLA_WAYDROID_RUNTIME_ROLE','TESLA_WAYDROID_VIDEO_SETTINGS',
    'DROIDMATE_GPU_RESIZE','DROIDMATE_EXPERIMENTAL_H264_ENCODER',
    'DROIDDECK_EXPERIMENTAL_GPU_CAPTURE','DROIDDECK_EXPERIMENTAL_FRAME_PACING'))

class DrainError(RuntimeError):
    """Original CPU fallback is unsafe until the owned candidate group is gone."""

def _path(value):
    if (type(value) is not str or not 2<=len(value)<=4096 or not value.startswith('/') or value.startswith('//')
        or str(PurePosixPath(value))!=value or '..' in value.split('/')
        or any(ord(c)<33 or ord(c)>126 for c in value)):
        raise ValueError('Canonical absolute protected path required')
    return value

def validate_inputs(source,target,argv,environment,admit,fallback,drain_seconds):
    _path(source);_path(target)
    if source==target or source.startswith(target+'/') or target.startswith(source+'/'):
        raise ValueError('Disjoint source and original runtime required')
    if (type(argv) is not list or not 1<=len(argv)<=64 or any(type(a) is not str or '\0' in a for a in argv)
        or sum(len(a) for a in argv)>65536):
        raise ValueError('Bounded explicit executable arguments required')
    _path(argv[0])
    if (type(environment) is not dict or not set(environment)<=ENV_KEYS
        or any(type(k) is not str or type(v) is not str or '\0' in v for k,v in environment.items())
        or environment.get('PATH')!='/usr/bin:/bin'
        or sum(len(k)+len(v) for k,v in environment.items())>65536):
        raise ValueError('Explicit closed non-injected child environment required')
    if not callable(admit) or not callable(fallback):
        raise ValueError('Independent admission and original fallback callbacks required')
    if type(drain_seconds) not in (int,float) or not math.isfinite(drain_seconds) or not 0<drain_seconds<=30:
        raise ValueError('Bounded teardown interval required')

def _pin(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid)

def hold_directory(stack,name):
    """Keep every original ancestor and reject links, writable/foreign directories."""
    checks=[]
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);stack.callback(os.close,fd)
    for part in ('',*PurePosixPath(name).parts[1:]):
        if part:
            try:fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            except OSError as e:raise ValueError('Protected real runtime directory required') from e
            stack.callback(os.close,fd)
        s=os.fstat(fd)
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_mode&0o022:
            raise ValueError('Root-owned protected runtime ancestry required')
        checks.append((fd,_pin(s)))
    final=fd
    def check():
        if any(_pin(os.fstat(handle))!=original for handle,original in checks):
            raise ValueError('Held runtime directory changed')
        if _pin(os.stat(name,follow_symlinks=False))!=checks[-1][1]:
            raise ValueError('Original runtime pathname changed')
    return final,check

def mount_runtime(source_fd,target):
    """Runs ONLY in Popen's new-session child, before executable dispatch."""
    source_name=_path(os.readlink('/proc/self/fd/'+str(source_fd)))
    original=_pin(os.fstat(source_fd))
    libc=ctypes.CDLL(None,use_errno=True)
    libc.unshare.argtypes=[ctypes.c_int];libc.unshare.restype=ctypes.c_int
    libc.mount.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_ulong,ctypes.c_void_p]
    libc.mount.restype=ctypes.c_int
    def require(result):
        if result!=0:
            code=ctypes.get_errno();raise OSError(code,os.strerror(code))
    require(libc.unshare(0x00020000)) # CLONE_NEWNS
    require(libc.mount(None,b'/',None,16384|262144,None)) # MS_REC|MS_PRIVATE
    dest=os.fsencode(target)
    # An inherited directory FD still names the old namespace's mount. Linux
    # rejects binding that mount into its clone. Reopen protected ancestry in
    # this child namespace and require the exact retained original inode.
    with ExitStack() as stack:
        local_fd,check=hold_directory(stack,source_name)
        check()
        if _pin(os.fstat(local_fd))!=original:raise ValueError('Candidate source changed during unshare')
        src=('/proc/self/fd/'+str(local_fd)).encode('ascii')
        require(libc.mount(src,dest,None,4096,None)) # MS_BIND
        require(libc.mount(None,dest,None,4096|32|1|2|4,None)) # BIND|REMOUNT|RDONLY|NOSUID|NODEV
    if ((os.stat(target).st_dev,os.stat(target).st_ino)!=(os.fstat(source_fd).st_dev,os.fstat(source_fd).st_ino)
        or not os.statvfs(target).f_flag&os.ST_RDONLY):
        raise ValueError('Exact read-only candidate runtime mount required')

def _libc():
    value=ctypes.CDLL(None,use_errno=True)
    value.prctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]
    value.prctl.restype=ctypes.c_int
    return value

def reaper(value=None):
    libc=_libc();answer=ctypes.c_int()
    result=libc.prctl(37,ctypes.addressof(answer),0,0,0) if value is None else libc.prctl(36,value,0,0,0)
    if result:raise DrainError('Kernel descendant ownership unavailable')
    return answer.value if value is None else value

def children():
    with open('/proc/self/task/'+str(os.getpid())+'/children','rb') as f:raw=f.read(1048577)
    if len(raw)>1048576 or any(not p.isdigit() for p in raw.split()):
        raise DrainError('Bounded direct-child ownership required')
    return [int(p) for p in raw.split()]

def single_launcher():
    if (sys.platform!='linux' or os.uname().machine not in ('x86_64','amd64')
        or os.geteuid()!=0 or threading.current_thread() is not threading.main_thread()
        or threading.active_count()!=1 or len(os.listdir('/proc/self/task'))!=1 or children()):
        raise ValueError('Child-free single-threaded root Linux x86 launcher required')
    if signal.getsignal(signal.SIGCHLD)!=signal.SIG_DFL:
        raise ValueError('Original waitable default SIGCHLD policy required')
    # glibc's Linux x86_64 sigaction ABI, the explicitly supported guest platform.
    class Action(ctypes.Structure):
        _fields_=[('handler',ctypes.c_void_p),('mask',ctypes.c_ulong*16),
                  ('flags',ctypes.c_int),('restorer',ctypes.c_void_p)]
    libc=_libc();action=Action()
    libc.sigaction.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.POINTER(Action)]
    libc.sigaction.restype=ctypes.c_int
    if libc.sigaction(signal.SIGCHLD,None,ctypes.byref(action)) or action.handler or action.flags&2:
        raise ValueError('Kernel SIGCHLD auto-reaping/caller reaper refused')

def reap_exited():
    """Only ECHILD proves no descendants remain; a /proc census never does."""
    for _ in range(65536):
        try:value=os.waitid(os.P_ALL,0,os.WEXITED|os.WNOHANG)
        except ChildProcessError:return True
        if value is None:return False
    raise DrainError('Child-reap bound exceeded')

def _signal_fd(fd,signum):
    try:signal.pidfd_send_signal(fd,signum)
    except ProcessLookupError:pass

def drain_group(_leader,seconds):
    """Drain all kernel-adopted descendants, including forks missed by /proc."""
    if reaper()!=1:raise DrainError('Original subreaper ownership lost')
    end=time.monotonic()+seconds
    while True:
        if reap_exited():return True
        now=time.monotonic()
        if now>=end:return False
        signum=signal.SIGKILL if now>=end-seconds/2 else signal.SIGTERM
        # Selection may miss a new fork; it can never authorize success. The
        # next kernel wait sees it (or its live parent), until genuine ECHILD.
        for pid in children():
            try:fd=os.pidfd_open(pid)
            except ProcessLookupError:continue
            try:_signal_fd(fd,signum)
            finally:os.close(fd)
        time.sleep(.01)

def wait_child(pid,pidfd,stopping,seconds):
    stopped_at=None;forced=False
    while True:
        try:status=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
        except ChildProcessError as e:raise DrainError('Original child waitability lost') from e
        if status is not None:
            return status.si_status if status.si_code==os.CLD_EXITED else -status.si_status
        now=time.monotonic()
        if stopping[0] is not None:
            if stopped_at is None:
                stopped_at=now;_signal_fd(pidfd,signal.SIGTERM)
            if not forced and now-stopped_at>=seconds:
                _signal_fd(pidfd,signal.SIGKILL);forced=True
            if now-stopped_at>=seconds*2:raise DrainError('Owned candidate did not stop')
        time.sleep(.01)

def fork_child(start,stopping):
    """Parent obtains a pidfd without waiting for namespace setup to complete."""
    error_read,error_write=os.pipe2(os.O_CLOEXEC|os.O_NONBLOCK)
    permit_read,permit_write=os.pipe2(os.O_CLOEXEC)
    try:pid=os.fork()
    except BaseException:
        for fd in (error_read,error_write,permit_read,permit_write):os.close(fd)
        raise
    if pid==0:
        try:
            os.close(error_read);os.close(permit_write)
            for signum in (signal.SIGTERM,signal.SIGINT):signal.signal(signum,signal.SIG_DFL)
            os.setsid()
            if os.read(permit_read,1)!=b'G':os._exit(0)
            os.close(permit_read)
            os.chdir('/')
            fd=os.open('/dev/null',os.O_RDONLY|os.O_CLOEXEC)
            if fd!=0:os.dup2(fd,0);os.close(fd)
            result=start()
            if type(result) is not int or not 0<=result<=255:raise ValueError('Fixed child exit required')
            os._exit(result)
        except BaseException:
            try:os.write(error_write,b'E')
            except OSError:pass
            os._exit(111)
    os.close(error_write);os.close(permit_read)
    try:
        fd=os.pidfd_open(pid)
        if stopping[0] is None:os.write(permit_write,b'G')
    except BaseException:
        os.close(error_read)
        # Never signal an unheld/reused PID. This original child stays our
        # unreaped child; the subreaper owns final cleanup on the error path.
        raise
    finally:os.close(permit_write)
    return pid,fd,error_read

def launch(start,stopping,seconds):
    pid,pidfd,error_fd=fork_child(start,stopping)
    try:
        result=wait_child(pid,pidfd,stopping,seconds)
        if not drain_group(pid,seconds):raise DrainError('Owned descendants remain; CPU fallback refused')
        try:failed=os.read(error_fd,2)
        except BlockingIOError:failed=b''
        if failed not in (b'',b'E'):raise DrainError('Invalid child setup result')
        return result,bool(failed)
    finally:os.close(pidfd);os.close(error_fd)

def run_view(source,target,argv,environment,*,admit,fallback,drain_seconds=5):
    """Trusted code only; CPU callback runs in an owned child in the original view."""
    validate_inputs(source,target,argv,environment,admit,fallback,drain_seconds)
    single_launcher()
    stopping=[None];original={};previous=None;owned=False
    def stop(signum,_frame):stopping[0]=signum
    try:
        for signum in (signal.SIGTERM,signal.SIGINT):
            original[signum]=signal.getsignal(signum);signal.signal(signum,stop)
        with ExitStack() as stack:
            source_fd,check_source=hold_directory(stack,source)
            _target_fd,check_target=hold_directory(stack,target)
            admit();single_launcher();check_source();check_target()
            if any(signal.getsignal(s) is not stop for s in original):
                raise ValueError('Admission changed launcher signal ownership')
            previous=reaper();reaper(1);owned=True
            def candidate():
                check_source();check_target();mount_runtime(source_fd,target)
                os.execve(argv[0],argv,dict(environment))
            if stopping[0] is not None:return {'mode':'stopped','candidateExit':None,'signal':stopping[0]}
            exitcode,failed=launch(candidate,stopping,drain_seconds)
        # Handlers stay owned through FD cleanup, the decision, and the CPU
        # child lifetime. A returning caller handler cannot consume a stop.
        if stopping[0] is not None:
            return {'mode':'stopped','candidateExit':exitcode,'signal':stopping[0]}
        cpu_exit,_failed=launch(fallback,stopping,drain_seconds)
        if stopping[0] is not None:
            return {'mode':'stopped','candidateExit':exitcode,'signal':stopping[0]}
        return {'mode':'cpu','reason':'setup-failed' if failed else 'candidate-exited',
                'candidateExit':exitcode,'returncode':cpu_exit}
    finally:
        if owned:
            # On an uncertain/live cleanup error keep subreaper ownership; no
            # CPU fallback is permitted. Restore only after kernel ECHILD.
            if reap_exited():reaper(previous)
        for signum,handler in original.items():signal.signal(signum,handler)


