# SPDX-License-Identifier: GPL-3.0-only
"""Separately enrolled no-code handoff proof; runtime updates remain prohibited."""
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time


def load(name, root=None):
    file = (root / 'runtime/portable' if root else Path(__file__).parent) / (name + '.py')
    spec = importlib.util.spec_from_file_location('guard_' + name.replace('-', '_'), file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


T = load('gpu-fallback-trial')
G, H = T.G, T.H
ROOT = Path('/var/lib/droiddeck/update-supervision-v2')
SOURCE = ROOT / 'sources'
SYSTEMD = Path('/etc/systemd/system')
SELF = 'runtime/portable/update-guard.py'
GUARD = 'droiddeck-update-recovery.service'
WORKER = 'droiddeck-update-worker@.service'
OVERLAYS = ('gpu-fallback-trial.py', 'update-base.py', 'installation-tail-recovery.py',
            'fresh-gpu.py', 'gpu-activation.py', 'gpu-native.js', 'recovery-probe-io.js',
            'cpu-fallback.js', 'cpu-recovery.py', 'update-quiescence.py', 'update-exchange.py',
            'update-package.py', 'update-guard.py')


def token(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{12}', value):
        raise ValueError('Exact original GPU token required')
    return value


def unit_bytes(value):
    owner = 'droiddeck-fresh-gpu-watch@' + token(value) + '.service'
    return ('[Unit]\nDescription=DroidDeck independent no-code update handoff guard\n'
            'After=local-fs.target\nBefore=' + owner + ' tesla-waydroid-runtime.service\n'
            '[Service]\nType=notify\nNotifyAccess=main\nUser=root\nGroup=root\nWorkingDirectory=/\n'
            'ExecStart=/usr/bin/python3 -I -B ' + str(SOURCE / SELF) + ' --watch\n'
            'Restart=on-failure\nRestartSec=2s\nKillMode=control-group\n'
            'TimeoutStartSec=30s\nTimeoutStopSec=10s\nUMask=0077\n'
            '[Install]\nWantedBy=multi-user.target\n').encode('ascii')


def worker_bytes():
    # This enrolled prototype can never start a code mutator, even accidentally.
    return (b'[Unit]\nDescription=DroidDeck unavailable update worker (proof only)\n'
            b'[Service]\nType=oneshot\nUser=root\nGroup=root\nExecStart=/usr/bin/false\n'
            b'KillMode=control-group\nUMask=0077\n')


def overlay_files(root):
    return {'runtime/portable/' + name: G.protected_read(Path(root) / 'runtime/portable' / name) for name in OVERLAYS}


def file_rows(files):
    return [{'path': name, 'size': len(raw), 'sha256': G.sha(raw)} for name, raw in sorted(files.items())]


def prove(mode, package):
    if mode == 'completed':
        return load('update-base').inspect(package)
    if mode == 'recovered':
        return load('installation-tail-recovery').verify(package)
    raise ValueError('Explicit original completed/recovered authority required')


def prepare(mode, package, current, overlay, expected):
    current = token(current)
    if type(expected) is not str or not re.fullmatch('[a-f0-9]{64}', expected):
        raise ValueError('Independent reviewed source digest required')
    changes = overlay_files(overlay)
    if G.sha(H.encode(file_rows(changes))) != expected:
        raise ValueError('Source overlays differ from independently reviewed digest')
    before = prove(mode, package)
    if before.get('originalInstallationOwnershipVerified') is not True:
        raise ValueError('Original installed ownership required')
    old_raw, originals = T.base_sources()
    admission = G.protected_read(T.ADMISSIONS / (current + '.json'))
    admitted = json.loads(admission)
    if admitted.get('transaction') != current or admitted.get('kind') != 'original-owned-fresh-gpu':
        raise ValueError('Original GPU admission required')
    _, active, _ = T.activation(current, T.BASE)
    if active.store.status()['stage'] != 'ready' or not active.native():
        raise ValueError('Current accepted native GPU required')
    for name in (GUARD, WORKER):
        if os.path.lexists(SYSTEMD / name):
            raise ValueError('Existing unit must never be adopted or overwritten')
    T.mkdir_new(ROOT)
    for name in ('sources', 'leases', 'requests', 'results'):
        T.mkdir_new(ROOT / name)
    files = {**originals, **changes}
    source_intent = {'schema': 1, 'kind': 'no-code-update-guard-sources', 'root': str(SOURCE),
        'rootIdentity': T.identity(SOURCE), 'baseIntentSha256': G.sha(old_raw), 'files': file_rows(files)}
    T.write_new(SOURCE / 'intent.json', H.encode(source_intent))
    fd = H.protected(SOURCE, directory=True)
    try:
        T.S.finish(fd, source_intent, files, '--resume-gpu-sources', root=SOURCE)
    finally:
        os.close(fd)
    context = {'schema': 1, 'kind': 'no-code-update-guard', 'purpose': 'handoff-proof-only',
        'updatesAllowed': False, 'mode': mode, 'package': str(Path(package)), 'currentGpu': current,
        'machineSha256': T.machine_pin(), 'rootIdentity': T.identity(ROOT),
        'sourceIntentSha256': G.sha(H.encode(source_intent)), 'gpuAdmissionSha256': G.sha(admission),
        'originalSourceSha256': G.sha(old_raw), 'guardUnitSha256': G.sha(unit_bytes(current)),
        'workerUnitSha256': G.sha(worker_bytes())}
    T.write_new(ROOT / 'context.json', H.encode(context))
    if prove(mode, package) != before:
        raise ValueError('Base changed; preserve incomplete enrollment, no replay')
    T.write_new(ROOT / 'complete.json', H.encode(context))
    parent = H.protected(SYSTEMD, directory=True)
    try:
        for name, raw in ((GUARD, unit_bytes(current)), (WORKER, worker_bytes())):
            H.write_new(parent, name, raw)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.fchmod(fd, 0o644); os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)
    identities = {name: [os.stat(SYSTEMD / name).st_dev, os.stat(SYSTEMD / name).st_ino] for name in (GUARD, WORKER)}
    T.write_new(ROOT / 'units.json', H.encode({'schema': 1, 'contextSha256': G.sha(H.encode(context)), 'identities': identities}))
    G.command(['/usr/bin/systemd-analyze', 'verify', str(SYSTEMD / GUARD), str(SYSTEMD / WORKER)])
    G.command(['/usr/bin/systemctl', 'daemon-reload'])
    return {'kind': 'no-code-guard-enrolled', 'codeApplicationAllowed': False,
            'guardStarted': False, 'originalWatcherChanged': False, 'installable': False}


def inspect():
    raw = G.protected_read(ROOT / 'context.json')
    context = json.loads(raw)
    keys = {'schema', 'kind', 'purpose', 'updatesAllowed', 'mode', 'package', 'currentGpu', 'machineSha256',
            'rootIdentity', 'sourceIntentSha256', 'gpuAdmissionSha256', 'originalSourceSha256',
            'guardUnitSha256', 'workerUnitSha256'}
    if (type(context) is not dict or set(context) != keys or type(context['schema']) is not int or context['schema'] != 1
            or context['kind'] != 'no-code-update-guard' or context['purpose'] != 'handoff-proof-only'
            or context['updatesAllowed'] is not False or context['rootIdentity'] != T.identity(ROOT)
            or context['machineSha256'] != T.machine_pin() or H.encode(context) != raw
            or G.protected_read(ROOT / 'complete.json') != raw):
        raise ValueError('Original completed no-code guard enrollment required')
    token(context['currentGpu'])
    original_raw, originals = T.base_sources()
    if G.sha(original_raw) != context['originalSourceSha256']:
        raise ValueError('Original source publication changed')
    source_raw = G.protected_read(SOURCE / 'intent.json')
    value = json.loads(source_raw)
    expected = {**originals, **overlay_files(SOURCE)}
    if (G.sha(source_raw) != context['sourceIntentSha256'] or value != {'schema': 1,
            'kind': 'no-code-update-guard-sources', 'root': str(SOURCE), 'rootIdentity': T.identity(SOURCE),
            'baseIntentSha256': G.sha(original_raw), 'files': file_rows(expected)}):
        raise ValueError('Exact original protected guard sources required')
    fd = H.protected(SOURCE, directory=True)
    try:
        T.S.finish(fd, value, expected, '--inspect-gpu-sources', root=SOURCE)
    finally:
        os.close(fd)
    if G.sha(G.protected_read(T.ADMISSIONS / (context['currentGpu'] + '.json'))) != context['gpuAdmissionSha256']:
        raise ValueError('Original GPU admission changed')
    units = json.loads(G.protected_read(ROOT / 'units.json'))
    if (units != {'schema': 1, 'contextSha256': G.sha(raw), 'identities': {
            name: [os.stat(SYSTEMD / name, follow_symlinks=False).st_dev, os.stat(SYSTEMD / name, follow_symlinks=False).st_ino]
            for name in (GUARD, WORKER)}} or
            G.protected_read(SYSTEMD / GUARD, 0o644) != unit_bytes(context['currentGpu']) or
            G.protected_read(SYSTEMD / WORKER, 0o644) != worker_bytes()):
        raise ValueError('Original enrolled unit inodes and bytes required')
    return context


def activation(context):
    module = load('fresh-gpu', SOURCE)
    module.ROOT = module.G.ROOT = SOURCE
    module.configure(context['currentGpu'])
    admitted = json.loads(module.G.protected_read(module.ADMISSIONS / (context['currentGpu'] + '.json')))
    parser, _policy = module.G.sources()
    active = module.Fresh(parser, admitted)
    active.metadata()
    return module, active


def code_snapshot(components):
    exchange = load('update-exchange', SOURCE)
    result = {}
    for row in components:
        if row['name'] not in exchange.P.COMPONENTS or row['root'] != exchange.P.COMPONENTS[row['name']]:
            raise ValueError('Only fixed installed public code roots allowed')
        path = Path(row['root'])
        fd = H.protected(path.parent, directory=True)
        try:
            result[row['name']] = exchange.tree(fd, path.name, row['files'])
        finally:
            os.close(fd)
    if set(result) != set(exchange.P.COMPONENTS):
        raise ValueError('All three original code components required')
    return result


class Journal:
    def __init__(self, directory, components, before, context, base):
        self.directory, self.components, self.before = directory, components, before
        self.context, self.base = context, base

    def intent(self, value):
        T.write_new(self.directory / 'intent.json', H.encode({'schema': 1, 'purpose': 'handoff-proof-only',
            'codeApplicationAllowed': False, 'contextSha256': G.sha(H.encode(self.context)),
            'leaseIdentity': T.identity(self.directory), 'baseSha256': G.sha(H.encode(self.base)),
            'components': self.components, 'originalCode': self.before, 'fence': value}))

    def marker(self, name):
        raw = G.protected_read(self.directory / 'intent.json')
        T.write_new(self.directory / (name + '.json'), H.encode({'schema': 1, 'intentSha256': G.sha(raw), 'phase': name}))

    def granted(self): self.marker('granted')
    def return_intent(self): self.marker('return-intent')
    def returned(self): self.marker('returned')


def verify_code(journal):
    if code_snapshot(journal.components) != journal.before:
        raise ValueError('Original live code changed; never rearm over unknown code')


def proof_result(context, name, kind):
    return {'schema': 1, 'kind': kind, 'request': name,
            'contextSha256': G.sha(H.encode(context)),
            'leaseIdentity': T.identity(ROOT / 'leases' / name),
            'originalCodeAndBaseUnchanged': True, 'originalSupervisionRearmed': True,
            'sourceIntentSha256': context['sourceIntentSha256'],
            'liveCodeApplied': False, 'updatesAllowed': False, 'installable': False}


def wait_base(context, expected):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        try:
            if G.sha(H.encode(prove(context['mode'], context['package']))) == expected:
                return
        except (OSError, ValueError, G.subprocess.SubprocessError):
            pass
        time.sleep(1)
    raise ValueError('Original source/base supervision did not reverify')


def recover_proof(context, name):
    """Return only provably unchanged original code; never replay a proof/apply.

    Recovery has its own exclusive records. It cannot manufacture a missing
    grant/return/completion in the interrupted original proof. The enrolled
    worker is /usr/bin/false and this version has no code application path.
    """
    if type(name) is not str or not re.fullmatch('[a-f0-9]{32}', name):
        raise ValueError('Exact interrupted original request required')
    directory = ROOT / 'leases' / name
    T.identity(directory)
    names = set(os.listdir(directory))
    allowed = {'intent.json', 'granted.json', 'return-intent.json', 'returned.json', 'recovery-intent.json'}
    if names - allowed:
        raise ValueError('Unknown interrupted proof objects preserved')
    if 'intent.json' not in names:
        if names:
            raise ValueError('Partial proof has no original intent')
        # Death before the durable intent cannot have stopped the old watcher.
        # Only a complete fresh baseline proof can close this empty request.
        prove(context['mode'], context['package'])
        T.write_new(ROOT / 'results' / (name + '.json'),
                    H.encode(proof_result(context, name, 'no-code-proof-aborted-before-intent')))
        return
    raw = G.protected_read(directory / 'intent.json')
    value = load('update-package', SOURCE).document(raw)
    if (type(value) is not dict or set(value) != {'schema', 'purpose', 'codeApplicationAllowed',
            'contextSha256', 'leaseIdentity', 'baseSha256', 'components', 'originalCode', 'fence'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['purpose'] != 'handoff-proof-only' or value['codeApplicationAllowed'] is not False
            or value['contextSha256'] != G.sha(H.encode(context))
            or value['leaseIdentity'] != T.identity(directory)
            or not isinstance(value['baseSha256'], str)
            or not re.fullmatch('[a-f0-9]{64}', value['baseSha256'])):
        raise ValueError('Original unchanged no-code lease required')
    facts = value['fence']
    owner = 'droiddeck-fresh-gpu-watch@' + token(context['currentGpu']) + '.service'
    quiet = load('update-quiescence', SOURCE)
    if (type(facts) is not dict or set(facts) != {'watcher', 'originalPid', 'guard', 'guardPid',
            'worker', 'boot', 'nativeGeneration'} or facts['watcher'] != owner or facts['guard'] != GUARD
            or facts['worker'] != quiet.worker(value['baseSha256'])):
        raise ValueError('Original proof supervision scope required')
    for phase in ('granted', 'return-intent', 'returned'):
        if phase + '.json' in names:
            if G.protected_read(directory / (phase + '.json')) != H.encode({
                    'schema': 1, 'intentSha256': G.sha(raw), 'phase': phase}):
                raise ValueError('Original proof phase changed')
    if ('returned.json' in names and 'return-intent.json' not in names
            or 'return-intent.json' in names and 'granted.json' not in names):
        raise ValueError('Interrupted original phases out of order')
    journal = Journal(directory, value['components'], value['originalCode'], context, None)
    module, active = activation(context)
    fence = quiet.Fence(module.G, active, journal, value['baseSha256'], context['guardUnitSha256'])
    fence.guard(); fence.scope()
    # Take the SAME original recovery lock even after our previous process died.
    # A recovery already in progress wins; no pending recovery runs behind us.
    with active.store.lock():
        fence.guard(); fence.scope()
        module.G.require_quiet(module.G.unit(facts['worker']))
        verify_code(journal)
        state = module.G.unit(owner)
        if state['ActiveState'] not in ('active', 'activating'):
            module.G.require_quiet(state)
        recovery = H.encode({'schema': 1, 'intentSha256': G.sha(raw),
                             'leaseIdentity': T.identity(directory), 'action': 'return-unchanged-original-owner'})
        if 'recovery-intent.json' in names:
            if G.protected_read(directory / 'recovery-intent.json') != recovery:
                raise ValueError('Original pending recovery intent changed')
        else:
            T.write_new(directory / 'recovery-intent.json', recovery)
    # Release before starting the original watcher: its boot path takes this lock.
    if state['ActiveState'] not in ('active', 'activating'):
        module.G.command(['/usr/bin/systemctl', 'start', '--no-block', owner], timeout=20)
    wait_base(context, value['baseSha256'])
    verify_code(journal)
    T.write_new(ROOT / 'results' / (name + '.json'),
                H.encode(proof_result(context, name, 'no-code-handoff-recovered')))


def run_proof(context, name):
    if not re.fullmatch('[a-f0-9]{32}', name):
        raise ValueError('Original proof request identity required')
    directory = ROOT / 'leases' / name
    if os.path.lexists(directory):
        raise ValueError('Existing proof must use explicit read-only recovery, never replay')
    before = prove(context['mode'], context['package'])
    components = before['components']
    snapshot = code_snapshot(components)
    T.mkdir_new(directory)
    journal = Journal(directory, components, snapshot, context, before)
    module, active = activation(context)
    fence = load('update-quiescence', SOURCE).Fence(module.G, active, journal, G.sha(H.encode(before)), context['guardUnitSha256'])
    try:
        fence.acquire()
        fence.return_owner(lambda: verify_code(journal))
    except Exception:
        # No worker or code destination is available in this enrolled prototype.
        # If a grant exists, original-code proof is mandatory before rearming.
        if fence.granted and not fence.closed:
            fence.return_owner(lambda: verify_code(journal))
        raise
    wait_base(context, G.sha(H.encode(before)))
    T.write_new(ROOT / 'results' / (name + '.json'),
                H.encode(proof_result(context, name, 'no-code-handoff-proof')))


def request():
    inspect()
    name = secrets.token_hex(16)
    T.write_new(ROOT / 'requests' / (name + '.json'), H.encode({'schema': 1, 'purpose': 'handoff-proof-only', 'request': name}))
    return {'request': name, 'updatesAllowed': False}


def watch():
    if Path(__file__).absolute() != SOURCE / SELF:
        raise ValueError('Only original enrolled guard entrypoint may run')
    context = inspect()
    module, _active = activation(context)
    owner = module.G.unit(GUARD)
    if owner['ActiveState'] not in ('active', 'activating') or owner['MainPID'] != str(os.getpid()):
        raise ValueError('Separately supervised enrolled guard main process required')
    module.notify()
    while True:
        inspect()
        names = os.listdir(ROOT / 'requests')
        if len(names) > 128 or any(not re.fullmatch(r'[a-f0-9]{32}\.json', name) for name in names):
            raise ValueError('Bounded original no-code requests required')
        for filename in sorted(names):
            name = filename[:-5]
            raw = G.protected_read(ROOT / 'requests' / filename)
            if raw != H.encode({'schema': 1, 'purpose': 'handoff-proof-only', 'request': name}):
                raise ValueError('Only original no-code request permitted')
            if os.path.lexists(ROOT / 'results' / filename):
                result_raw = G.protected_read(ROOT / 'results' / filename)
                result = load('update-package', SOURCE).document(result_raw)
                if result != proof_result(context, name, result.get('kind')) or result['kind'] not in (
                        'no-code-handoff-proof', 'no-code-handoff-recovered', 'no-code-proof-aborted-before-intent'):
                    raise ValueError('Original closed proof result required')
                continue
            if os.path.lexists(ROOT / 'leases' / name):
                recover_proof(context, name)
            else:
                run_proof(context, name)
        time.sleep(2)


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Explicit root Linux no-code guard operation required')
    os.umask(0o077)
    if len(args) == 6 and args[0] == '--prepare':
        result = prepare(*args[1:])
    elif args == ['--inspect']:
        inspect(); result = {'kind': 'no-code-guard-inspection', 'readOnly': True, 'updatesAllowed': False}
    elif args == ['--request-proof']:
        result = request()
    elif args == ['--watch']:
        return watch()
    else:
        raise ValueError('Only prepare/inspect/request-proof/watch; NO payload apply operation')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('No-code guard refused or incomplete: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
