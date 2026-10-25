# SPDX-License-Identifier: GPL-3.0-only
"""Non-destructive rc.1 pilot repair with original-owned, single-use GPU admission."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

spec = importlib.util.spec_from_file_location('boot_update', Path(__file__).with_name('reboot-hotfix.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)
ROOT = Path('/var/lib/droiddeck/pilot-reboot-repair-1')
GPU = H.TARGET


def run(args, *, data=None, timeout=45):
    result = subprocess.run(list(map(str, args)), input=data, stdout=subprocess.PIPE,
                            stderr=None, check=True, timeout=timeout)
    if len(result.stdout) > 65536:
        raise ValueError('Bounded repair command output required')
    return result.stdout


def once(fd, name, operation):
    complete, started = name + '.complete.json', name + '.started.json'
    if complete in os.listdir(fd):
        return json.loads(H.read(fd, complete))['result']
    H.write_new(fd, started, b'{"started":true}\n')
    result = operation()
    H.write_new(fd, complete, json.dumps({'result': result}, sort_keys=True).encode())
    return result


def watcher_token(name):
    match = re.fullmatch(r'droiddeck-fresh-gpu-watch@([a-f0-9]{12})\.service', name)
    if not match:
        raise ValueError('Exactly one original enabled GPU watcher required')
    return match[1]


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or args not in (['--apply'], ['--resume'], ['--verify']):
        raise ValueError('Root Linux explicit --apply, --resume or --verify required')
    with ExitStack() as stack:
        source = Path(__file__).absolute().parent
        directory = H.directory(stack, source)
        payload = H.read(directory, 'fresh-gpu.py')
        if H.digest(payload) != H.NEW:
            raise ValueError('Exact repair source payload required')
        if args == ['--apply']:
            enabled = Path('/etc/systemd/system/multi-user.target.wants')
            H.directory(stack, enabled)
            rows = list(enabled.glob('droiddeck-fresh-gpu-watch@*.service'))
            if len(rows) != 1:
                raise ValueError('One enabled original watcher required')
            if not rows[0].is_symlink() or rows[0].resolve() != Path('/etc/systemd/system/droiddeck-fresh-gpu-watch@.service'):
                raise ValueError('Original boot watcher template required')
            old_token = watcher_token(rows[0].name)
            status = json.loads(H.read(H.directory(stack, GPU.parents[3] / 'gpu-transactions' / old_token), 'status.json'))
            if status['stage'] not in ('ready', 'restored'):
                raise ValueError('Wait for original guard to finish; do not adopt uncertain GPU state')
            config = json.loads(H.read(H.directory(stack, Path('/root/droiddeck-installation')), 'configuration.json'))
            os.mkdir(ROOT, 0o700)
            H.write_new(H.directory(stack, ROOT), 'intent.json', json.dumps({
                'originalToken': old_token, 'newAdmission': status['stage'] == 'restored',
                'configuration': config}, sort_keys=True).encode())
        fd = H.directory(stack, ROOT)
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = json.loads(H.read(fd, 'intent.json'))
        old_token = intent['originalToken']
        watcher_token('droiddeck-fresh-gpu-watch@' + old_token + '.service')
        if args == ['--verify']:
            token = json.loads(H.read(fd, 'admission.complete.json'))['result']
            return print(run(['/usr/bin/python3', '-I', '-B', GPU, '--inspect', token]).decode())
        once(fd, 'stop-watcher', lambda: run(['/usr/bin/systemctl', 'stop',
             'droiddeck-fresh-gpu-watch@' + old_token + '.service']).decode())
        def admit():
            if not intent['newAdmission']:
                return old_token
            config = intent['configuration']
            command = ['/usr/bin/python3', '-I', '-B', GPU, '--admit', config['user'],
                       '/root/droiddeck-installation/protected/artifact-stage/assembly',
                       '/root/droiddeck-installer-sources/recipes',
                       str(Path(config['home']) / 'droiddeck-installation/prepared/frontend'),
                       '/root/droiddeck-installer-sources/compat-source.js', '-']
            original = H.read(H.directory(stack, Path('/root/droiddeck-installation')), 'configuration.json')
            if json.loads(original) != config:
                raise ValueError('Original installation configuration changed')
            result = json.loads(run(command, data=original, timeout=120))
            token = result['transaction']
            watcher_token('droiddeck-fresh-gpu-watch@' + token + '.service')
            return token
        token = once(fd, 'admission', admit)
        once(fd, 'source-update', lambda: run(['/usr/bin/python3', '-I', '-B', source / 'reboot-hotfix.py',
             '--resume' if H.JOURNAL.with_name('reboot-hotfix-2').exists() else '--apply', source / 'fresh-gpu.py']).decode())
        if intent['newAdmission']:
            once(fd, 'disable-restored', lambda: run(['/usr/bin/systemctl', 'disable',
                 'droiddeck-fresh-gpu-watch@' + old_token + '.service']).decode())
        once(fd, 'start-watcher', lambda: run(['/usr/bin/systemctl', 'start',
             'droiddeck-fresh-gpu-watch@' + token + '.service']).decode())
        if intent['newAdmission']:
            once(fd, 'activate-once', lambda: run(['/usr/bin/systemctl', 'start',
                 'droiddeck-fresh-gpu-worker@' + token + '.service'], timeout=460).decode())
        health = json.loads(run(['/usr/bin/python3', '-I', '-B', GPU, '--inspect', token]))
        if health.get('nativeGpuVerified') is not True or health['status']['stage'] != 'ready':
            raise ValueError('Native GPU readiness required before enabling boot startup')
        once(fd, 'enable-watcher', lambda: run(['/usr/bin/systemctl', 'enable',
             'droiddeck-fresh-gpu-watch@' + token + '.service']).decode())
        once(fd, 'start-runtime', lambda: run(['/usr/bin/systemctl', 'start', 'tesla-waydroid-runtime.service']).decode())
        print(json.dumps({'kind': 'pilot-reboot-repair', 'nativeGpuVerified': True,
                          'originalAndroidDataRetained': True, 'activationReplayed': False}))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Pilot repair incomplete; preserve original journals and inspect before retry: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
