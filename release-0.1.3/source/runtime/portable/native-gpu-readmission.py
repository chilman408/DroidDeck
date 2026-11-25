# SPDX-License-Identifier: GPL-3.0-only
"""Verify the preserved native GPU baseline after the signed portable core exchange."""
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
import types

TOKEN = re.compile('[a-f0-9]{12}')
NATIVE = Path('/var/lib/droiddeck/gpu-recovery/runtime/portable')
FRESH_PIN = '6ba151d8a8ef6f79ab8ecb2b081040b045461adc68915bc889dffca53ee29c82'
ACTIVATION_PIN = 'cf17ed639ad8e87dad465f3b2a8190a0987882b3e3fd1b03d183b3276c721dbd'
RUNTIME = 'tesla-waydroid-runtime.service'
CPU = '40e24715fcd8c3e9bc1003c934fc93466f6cfdac18b8496da35ed13bd6def0ba'
GPU = '2162aad79e10a32d3480b8b05629816f7975882102302cb219fb503a87283ea8'
CORE = '1b81d77c394ffab2875511237e8346893519db28274496039811fac7b8aca84a'
CAPTURE = '37c40eba45fba6f323264cea93df4c794f430886acecc365e221bbf0beefc166'
AUTHORITY = (Path('/var/lib/droiddeck-capture-controls') / CAPTURE
             / 'runtime/portable/capture-release-authority.py')
ORIGINAL = Path('/root/droiddeck-installation/configuration.json')
REGISTRATION = Path('/var/lib/droiddeck/registration-transaction/intent.json')
CONFIGURATION = Path('/etc/droiddeck/runtime.json')
AUTHORITY_PIN = '7b1b38c5dde6526dffa64ed82c3e2283dfd44596b88b4e38e21cc105521c9a4f'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def generation(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def protected_read(path, limit, mode):
    """Read one bounded regular file through root-owned ancestry."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent = os.open('/', flags)
    try:
        for name in path.parts[1:-1]:
            child = os.open(name, flags, dir_fd=parent)
            os.close(parent)
            parent = child
            info = os.fstat(parent)
            if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
                raise ValueError('Protected baseline ancestry required')
        with os.fdopen(os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=parent), 'rb') as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode
                    or not 0 < before.st_size <= limit):
                raise ValueError('Protected baseline file required')
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if len(raw) != before.st_size or generation(before) != generation(after) or generation(before) != generation(named):
                raise ValueError('Protected baseline file changed')
            return raw
    finally:
        os.close(parent)


def pinned(path, expected):
    raw = protected_read(path, 1024 * 1024, 0o600)
    if digest(raw) != expected:
        raise ValueError('Unsupported installed controller generation')
    return raw


def load_pinned(path, expected):
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(pinned(path, expected), str(path), 'exec'), module.__dict__)
    return module


def verify_registration(proof, original, registration, configuration, installed):
    """Bind raw install bytes to the recorded plan and the installed configuration."""
    receipts = proof['receiptGenerations']
    if (str(ORIGINAL) not in receipts or str(REGISTRATION) not in receipts
            or digest(original) != proof['configurationSha256']
            or registration['planSha256'] != proof['placementPlanSha256']
            or json.loads(original) != installed or json.loads(configuration) != installed):
        raise ValueError('Original registration and signed core baseline differ')


def verify_transaction(transaction):
    if transaction is None or transaction.digest != CORE:
        raise ValueError('Supported signed core update required')
    # observe verifies the complete live candidate, held original tree, receipt
    # generations, non-gateway inventory and original public TLS identity.
    side, phases = transaction.observe()
    rollback = {'rollback-request.json', 'rollback-intent.json', 'rolled-back.json', 'boot-code-restored.json'}
    if side != 'after' or not {'applied.json', 'healthy.json'} <= phases or phases & rollback:
        raise ValueError('Healthy signed core and retained original baseline required')
    return side, phases


@contextmanager
def verified_baseline():
    """Hold the enrolled update lock across verification and the caller's admission."""
    authority = load_pinned(AUTHORITY, AUTHORITY_PIN)
    with authority.verified_wrapper() as (wrapper, held):
        wrapper.inspect_enrollment()
        with wrapper.C.locked() as context:
            wrapper.B.inspect_bootstrap(context)
            transaction = wrapper.C.current_view(wrapper.C.records())
            state = verify_transaction(transaction)
            held.check()
            original = protected_read(ORIGINAL, 16384, 0o600)
            registration = json.loads(protected_read(REGISTRATION, 1024 * 1024, 0o600))
            configuration = protected_read(CONFIGURATION, 16384, 0o644)
            verify_registration(transaction.value['baseProof'], original, registration,
                                configuration, transaction.value['configuration'])
            token = transaction.value['currentGpu']
            if not isinstance(token, str) or not TOKEN.fullmatch(token):
                raise ValueError('Original native GPU transaction required')
            transaction.active.metadata()
            yield transaction, token, configuration
            held.check()
            if verify_transaction(transaction) != state:
                raise ValueError('Signed core changed during native graphics admission')


def prepare_replacement(token):
    """Prepare an unused native transaction after the supervised handoff stops all workers.

    The caller owns token selection, a durable recovery record, watcher startup and
    activation. This operation never starts a service or replays an existing token.
    """
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ValueError('New native GPU transaction token required')
    pinned(NATIVE / 'gpu-activation.py', ACTIVATION_PIN)
    native = load_pinned(NATIVE / 'fresh-gpu.py', FRESH_PIN)
    with verified_baseline() as (transaction, old_token, configuration):
        if token == old_token:
            raise ValueError('Restored native transaction cannot be reused')
        native.configure(old_token)
        parser, _ = native.G.sources()
        original = transaction.active.admission
        if (original['transaction'] != old_token or original['metadataSha256'] != digest(configuration)
                or native.Fresh(parser, original).store.status()['stage'] != 'restored'):
            raise ValueError('Original native GPU rollback must be complete')
        # The original preparation and offline checks protect the Android data
        # inode, guest payload receipt, profile pair and empty allocator slots.
        with ExitStack() as stack:
            old = native.Fresh(parser, original)
            old.offline(stack)['check']()
            for unit in (*native.G.UNITS, 'droiddeck-browser-connection.service', native.G.WATCHER):
                native.G.require_quiet(native.G.unit(unit))
            native.configure(token)
            admitted = native.ADMISSIONS / (token + '.json')
            if os.path.lexists(admitted) or os.path.lexists(native.G.STATE):
                raise FileExistsError('Existing native GPU admission preserved')
            successor = dict(original, transaction=token)
            directory = private_directory(native.ADMISSIONS)
            stack.callback(os.close, directory)
            receipt = json.dumps(successor, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'
            fd = os.open(admitted.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(receipt)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
            native.Fresh(parser, successor).prepare()
        return {'transaction': token, 'stage': 'prepared'}


def private_directory(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(path, flags)
    info = os.fstat(directory)
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
        os.close(directory)
        raise ValueError('Root-private native admission directory required')
    return directory


def read_profile():
    return digest(protected_read(Path('/var/lib/waydroid/waydroid.cfg'), 1024 * 1024, 0o644))


def boot_watcher(token):
    if not TOKEN.fullmatch(token):
        raise ValueError('Exact native recovery token required')
    unit = f'droiddeck-fresh-gpu-watch@{token}.service'
    script = PurePosixPath(__file__)
    if not script.is_absolute() or '..' in script.parts or not re.fullmatch(r'/[a-zA-Z0-9_./-]+', str(script)):
        raise ValueError('Exact retained recovery helper path required')
    source = (
        '[Unit]\n'
        'Requires=droiddeck-update-boot.service\n'
        'After=droiddeck-update-boot.service\n'
        '[Service]\n'
        f'ExecStopPost=/usr/bin/python3 -I -B {script} restart-restored-cpu {token}\n'
    ).encode('ascii')
    return unit, source


def install_watcher(unit, source):
    parent = Path('/etc/systemd/system')
    addition = parent / (unit + '.d')
    if os.path.lexists(parent / unit) or os.path.lexists(parent / 'multi-user.target.wants' / unit):
        raise ValueError('Existing native watcher preserved')
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.mkdir(addition.name, 0o755, dir_fd=directory)
        child = os.open(addition.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            os.fchmod(child, 0o755)
            fd = os.open('95-droidmate-native-recovery.conf',
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=child)
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(child)
        finally:
            os.close(child)
        os.fsync(directory)
    finally:
        os.close(directory)


def restart_restored_cpu(token):
    if not TOKEN.fullmatch(token):
        raise ValueError('Exact native recovery token required')
    state = subprocess.run(
        ['/usr/bin/systemctl', 'is-system-running'], capture_output=True, timeout=10
    ).stdout.strip()
    if state not in (b'running', b'degraded', b'starting'):
        return
    path = Path('/var/lib/droiddeck/gpu-transactions') / token / 'status.json'
    status = json.loads(protected_read(path, 8192, 0o600))
    if status.get('stage') == 'restored':
        if read_profile() != CPU:
            raise ValueError('Recovered original CPU profile required')
        subprocess.run(
            ['/usr/bin/systemctl', 'start', '--no-block', RUNTIME], check=True, timeout=15)


def repair_for_upgrade(journal, run, check):
    """Rearm native GPU under the signed handoff's already-armed recovery."""
    check()
    profile = read_profile()
    if profile == GPU:
        # A healthy native generation already has a watcher. Leave that
        # generation in place. Normal signed capture admission verifies GPU.
        return False
    if profile != CPU:
        raise ValueError('Unrecognized Waydroid profile preserved')
    with verified_baseline() as (transaction, _token, _configuration):
        if transaction.active.store.status()['stage'] != 'restored':
            raise ValueError('Original native GPU rollback is incomplete')
    token = secrets.token_hex(6)
    watcher, source = boot_watcher(token)
    journal.write('native-gpu-repair.json', {
        'version': 1, 'token': token, 'watcher': watcher, 'dropInSha256': digest(source),
    })
    check()
    systemctl = '/usr/bin/systemctl'
    units = [RUNTIME, 'tesla-waydroid-headless-session.service', 'waydroid-container.service',
             'tesla-waydroid-compositor.service', 'droiddeck-browser-connection.service']
    activation_requested = False
    try:
        run([systemctl, 'stop', *units], 120)
        for unit in units:
            state = run([systemctl, 'show', unit, '-p', 'ActiveState', '--value']).strip()
            if state == b'failed':
                run([systemctl, 'reset-failed', unit])
        prepare_replacement(token)
        check()
        install_watcher(watcher, source)
        run(['/usr/bin/systemd-analyze', 'verify', watcher], 30)
        run([systemctl, 'daemon-reload'])
        run([systemctl, 'enable', '--now', watcher], 40)
        activation_requested = True
        run([systemctl, 'start', f'droiddeck-fresh-gpu-worker@{token}.service'], 460)
        if read_profile() != GPU:
            raise ValueError('Prepared native GPU profile was not activated')
        run([systemctl, 'start', RUNTIME], 180)
        check()
        return True
    except Exception:
        # When the watcher had time to mutate, it alone owns GPU rollback.
        # An earlier failure still leaves the exact CPU profile in place.
        if not activation_requested and read_profile() == CPU:
            run([systemctl, 'start', RUNTIME], 180)
        raise


def inspect():
    with verified_baseline() as (transaction, token, _configuration):
        status = transaction.active.store.status()
        return {'registrationVerified': True, 'signedCore': CORE,
                'originalNativeTransaction': token, 'originalNativeStage': status['stage']}


if __name__ == '__main__':
    if sys.platform != 'linux' or os.geteuid() != 0:
        sys.exit('Linux root required')
    if sys.argv[1:] == ['verify']:
        print(json.dumps(inspect(), sort_keys=True))
    elif len(sys.argv) == 3 and sys.argv[1] == 'restart-restored-cpu':
        restart_restored_cpu(sys.argv[2])
    else:
        sys.exit('Use verify on an existing portable installation')
