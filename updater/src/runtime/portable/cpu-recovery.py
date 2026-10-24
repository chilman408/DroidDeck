# SPDX-License-Identifier: GPL-3.0-only
"""Bounded software-rendering startup AFTER original GPU restoration is proven."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import stat


class Recovery:
    def __init__(self, activation, gpu):
        self.a, self.g = activation, gpu
        self.boot = gpu.boot_id()
        if not re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', self.boot):
            raise ValueError('Exact kernel boot identity required')
        self.record = 'cpu-fallback-' + self.boot + '.json'

    def status(self):
        try:
            value = self.a.store.read(self.record)
        except FileNotFoundError:
            return None
        if (type(value) is not dict or set(value) != {'version', 'boot', 'stage'}
                or type(value['version']) is not int or value['version'] != 1
                or value['boot'] != self.boot or value['stage'] not in ('starting', 'ready', 'failed')):
            raise ValueError('Original per-boot CPU fallback record required')
        return value['stage']

    def stage(self, value, *, exclusive=False):
        self.a.store.write(self.record, {'version': 1, 'boot': self.boot, 'stage': value}, exclusive)

    def owner(self):
        watcher = self.g.unit(self.g.WATCHER)
        if watcher['ActiveState'] not in ('active', 'activating') or watcher['MainPID'] != str(os.getpid()):
            raise ValueError('Original supervised watcher main process required')
        self.g.require_quiet(self.g.unit(self.g.WORKER))
        self.a.metadata()
        if self.a.store.status()['stage'] != 'restored':
            raise ValueError('Completed original GPU restoration required')
        if self.g.sha(self.g.protected_read(self.g.WORK / 'waydroid.cfg', 0o644)) != self.g.CPU:
            raise ValueError('Original restored CPU profile required')

    def prove_restored(self):
        for name in self.g.UNITS:
            self.g.require_quiet(self.g.unit(name))
        self.a.profiles()
        with ExitStack() as stack:
            ctx = self.a.offline(stack)
            self.a.manifest(ctx)
            if self.a.selected(ctx) != 'absent':
                raise ValueError('Restored saved-layer absence required')
            misc = ctx['property'].parent / 'misc'
            receipt = Path('/var/lib/droiddeck') / self.g.ALLOC
            pending = receipt.with_name('pending-' + self.g.ALLOC)
            names = [os.path.lexists(misc / name) for name in ('waydroid_settings', self.g.READY, self.g.RESTORED)]
            if not any(names) and not os.path.lexists(receipt) and not os.path.lexists(pending):
                ctx['check']()
                return  # Original activation never reached allocator staging.
            if names != [False, False, True] or os.path.lexists(pending):
                raise ValueError('Completed retained allocator restoration required')
            chain = self.a.c.open_chain(stack, misc)
            metadata = json.loads(self.g.protected_read(receipt / 'receipt.json'))
            self.a.p._allocator_metadata(metadata, {key: ctx[key] for key in ('dataDevice', 'dataInode')},
                                        os.fstat(chain[-1][1]), self.g.SCOPE)
            _fd, info, raw = self.a.p._read_profile_file(self.a.c, stack, chain[-1][1], self.g.RESTORED)
            if (raw != self.a.p.ALLOCATOR_CODE or not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != (metadata['fileDevice'], metadata['fileInode'])):
                raise ValueError('Original retained allocator inode and bytes required')
            self.a.c.check_image(chain[-1][1], self.g.RESTORED, _fd, info, private=True)
            self.a.c.check_chain(chain)
            ctx['check']()

    def healthy(self):
        result = self.g.command(['/usr/bin/node', str(self.g.ROOT / 'runtime/portable/cpu-fallback.js')],
                                timeout=35, check=False)
        if result.returncode != 0 or len(result.stdout) > 16384:
            return False
        value = json.loads(result.stdout)
        return (type(value) is dict and value.get('kind') == 'android-cpu-fallback-health'
                and value.get('cpuFallbackVerified') is True and value.get('nativeGpuVerified') is False)

    def stop_locked(self):
        self.owner()
        self.g.command(['/usr/bin/systemctl', 'stop', *self.g.UNITS], timeout=100)
        for name in self.g.UNITS:
            self.g.require_quiet(self.g.unit(name))
        self.stage('failed')

    def start(self):
        with self.a.store.lock():
            self.owner()
            previous = self.status()
            if previous == 'ready':
                return True
            if previous in ('starting', 'failed'):
                # An interrupted attempt is not replayed in the same boot.
                self.stop_locked()
                return False
            self.prove_restored()
            self.stage('starting', exclusive=True)
            try:
                self.g.command(['/usr/bin/systemctl', 'start', self.g.UNITS[3]], timeout=45)
                self.g.command(['/usr/bin/systemctl', 'start', self.g.UNITS[2], self.g.UNITS[1]],
                               timeout=self.a.SESSION_START_TIMEOUT)
                deadline = self.g.time.monotonic() + 120
                while self.g.time.monotonic() < deadline:
                    try:
                        healthy = self.healthy()
                    except (OSError, ValueError, self.g.subprocess.SubprocessError):
                        healthy = False
                    if healthy:
                        self.stage('ready')
                        self.g.command(['/usr/bin/systemctl', 'start', '--no-block', self.g.UNITS[0]])
                        return True
                    self.g.time.sleep(5)
                raise ValueError('Software-rendering startup deadline')
            except Exception:
                self.stop_locked()
                raise

    def watch(self):
        try:
            if not self.start():
                return
            failures = 0
            while True:
                self.owner()
                try:
                    healthy = self.healthy()
                except (OSError, ValueError, self.g.subprocess.SubprocessError):
                    healthy = False
                failures = 0 if healthy else failures + 1
                if failures >= 3:
                    with self.a.store.lock():
                        self.stop_locked()
                    return
                self.g.time.sleep(15)
        except (OSError, ValueError, self.g.subprocess.SubprocessError):
            # Never report private state or loop service restarts after an
            # uncertain fallback. Original restoration and per-boot records stay.
            print('CPU fallback stopped or refused; original recovery records retained', flush=True)
