# SPDX-License-Identifier: GPL-3.0-only
"""Supervised gateway-only code exchange; Android and GPU watchdog stay armed.

This controller has fixed destinations and independently retained sources. A
restarted unfinished transaction always rolls back; it never continues apply.
It never modifies Android data, installation receipts, TLS identity or defaults.
"""
from contextlib import ExitStack
import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tarfile
import time
import urllib.error
import urllib.request


def load(name, root=None):
    file = (root / 'runtime/portable' if root else Path(__file__).parent) / (name + '.py')
    spec = importlib.util.spec_from_file_location('gateway_update_' + name.replace('-', '_'), file)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


T = load('gpu-fallback-trial')
G, H = T.G, T.H
A, E = load('update-admission'), load('update-exchange')
P = A.P
PREDECESSOR = Path('/var/lib/droiddeck/gateway-update-controller')
CONTROLLER = Path('/var/lib/droiddeck/gateway-update-controller-v2')
SOURCE = CONTROLLER / 'sources'
ROOT = Path('/var/lib/droiddeck/gateway-updates-v2')
SYSTEMD = Path('/etc/systemd/system')
TEMPLATE = 'droiddeck-gateway-update-v2@.service'
BOOT = 'droiddeck-gateway-update-boot.service'
RUNTIME = 'tesla-waydroid-runtime.service'
LIVE = Path('/opt/droiddeck/runtime/preview-gateway')
SELF = 'runtime/portable/gateway-update.py'
POLICY = Path('/etc/needrestart/conf.d/90-droiddeck.conf')
OVERLAYS = ('gateway-update.py', 'gpu-fallback-trial.py', 'update-admission.py', 'update-base.py',
            'installation-tail-recovery.py', 'update-package.py', 'update-exchange.py', 'fresh-gpu.py',
            'gpu-activation.py', 'gpu-native.js', 'recovery-probe-io.js', 'cpu-fallback.js', 'cpu-recovery.py',
            'needrestart-droiddeck.conf')


def unit_bytes():
    return ('[Unit]\nDescription=DroidDeck authenticated gateway update and rollback\nAfter=local-fs.target ' + BOOT + '\n'
            '[Service]\nType=exec\nUser=root\nGroup=root\nWorkingDirectory=/\n'
            'ExecStart=/usr/bin/python3 -I -B ' + str(SOURCE / SELF) + ' run %i\n'
            'Restart=on-failure\nRestartSec=3s\nRemainAfterExit=yes\nKillMode=control-group\n'
            'TimeoutStopSec=10s\nUMask=0077\n[Install]\nWantedBy=multi-user.target\n').encode('ascii')


def instance(digest):
    return TEMPLATE.replace('@.', '@' + P.digest(digest) + '.')


def boot_bytes(current):
    if not re.fullmatch('[a-f0-9]{12}', current): raise ValueError('Exact GPU identifier required')
    watch = 'droiddeck-fresh-gpu-watch@' + current + '.service'
    return ('[Unit]\nDescription=DroidDeck interrupted gateway recovery before streaming\n'
            'After=local-fs.target\nBefore=' + RUNTIME + ' ' + watch + '\n'
            '[Service]\nType=oneshot\nUser=root\nGroup=root\nWorkingDirectory=/\n'
            'ExecStart=/usr/bin/python3 -I -B ' + str(SOURCE / SELF) + ' recover-code-at-boot\n'
            'RemainAfterExit=yes\nRestart=on-failure\nRestartSec=3s\nTimeoutStartSec=120s\nUMask=0077\n'
            '[Install]\nWantedBy=multi-user.target\nRequiredBy=' + RUNTIME + ' ' + watch + '\n').encode('ascii')


def current_gpu():
    raw = G.command(['/usr/bin/systemctl', 'list-units', '--all', '--output=json', '--no-pager',
        '--state=active', 'droiddeck-fresh-gpu-watch@*.service']).stdout
    if len(raw) > 16384: raise ValueError('Bounded original watcher required')
    value = json.loads(raw)
    if type(value) is not list or len(value) != 1 or value[0].get('active') != 'active':
        raise ValueError('Exactly one active original GPU watcher required')
    match = re.fullmatch(r'droiddeck-fresh-gpu-watch@([a-f0-9]{12})\.service', value[0].get('unit', ''))
    if not match: raise ValueError('Original watcher identifier required')
    return match[1]


def policy_authority():
    if not os.path.lexists(POLICY): return None
    # Development successor preserves and verifies the first enrollment. It does
    # not adopt an administrator's unrelated equal-byte maintenance policy.
    old = load('gateway-update', PREDECESSOR / 'sources')
    value = old.inspect_controller()
    return {'contextSha256': P.sha(P.encode(value)),
            'contextGeneration': E.generation((PREDECESSOR / 'context.json').lstat())}


def rows(files):
    return [{'path': name, 'size': len(raw), 'sha256': P.sha(raw)} for name, raw in sorted(files.items())]


def overlays(root):
    return {'runtime/portable/' + name: G.protected_read(Path(root) / 'runtime/portable' / name) for name in OVERLAYS}


def enroll(root, expected):
    changes = overlays(root)
    if P.sha(P.encode(rows(changes))) != P.digest(expected):
        raise ValueError('Exact independently reviewed controller source required')
    original, files = T.base_sources()
    if any(os.path.lexists(SYSTEMD / name) for name in (TEMPLATE, BOOT)):
        raise ValueError('Existing controller units cannot be adopted')
    inherited, current = policy_authority(), current_gpu()
    for path in (POLICY.parent.parent, POLICY.parent):
        if not os.path.lexists(path):
            parent = H.protected(path.parent, directory=True)
            try:
                os.mkdir(path.name, mode=0o755, dir_fd=parent)
                fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try: os.fchmod(fd, 0o755); os.fsync(fd)
                finally: os.close(fd)
                os.fsync(parent)
            finally: os.close(parent)
        fd = H.protected(path, directory=True); os.close(fd)
    T.mkdir_new(CONTROLLER); T.mkdir_new(SOURCE); T.mkdir_new(ROOT)
    files.update(changes)
    intent = {'schema': 1, 'kind': 'gateway-update-controller-sources', 'root': str(SOURCE),
              'rootIdentity': T.identity(SOURCE), 'baseIntentSha256': P.sha(original), 'files': rows(files)}
    T.write_new(SOURCE / 'intent.json', P.encode(intent))
    fd = H.protected(SOURCE, directory=True)
    try: T.S.finish(fd, intent, files, '--resume-gpu-sources', root=SOURCE)
    finally: os.close(fd)
    parent = H.protected(SYSTEMD, directory=True)
    try:
        for name, raw in ((TEMPLATE, unit_bytes()), (BOOT, boot_bytes(current))):
            H.write_new(parent, name, raw)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try: os.fchmod(fd, 0o644); os.fsync(fd)
            finally: os.close(fd)
        os.fsync(parent)
    finally: os.close(parent)
    policy_parent = H.protected(POLICY.parent, directory=True)
    try:
        if inherited is None:
            H.write_new(policy_parent, POLICY.name, changes['runtime/portable/needrestart-droiddeck.conf'])
            fd = os.open(POLICY.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=policy_parent)
            try: os.fchmod(fd, 0o644); os.fsync(fd)
            finally: os.close(fd)
        elif G.protected_read(POLICY, 0o644, 4096) != changes['runtime/portable/needrestart-droiddeck.conf']:
            raise ValueError('Original accepted policy bytes required')
        os.fsync(policy_parent)
    finally: os.close(policy_parent)
    info = os.stat(SYSTEMD / TEMPLATE)
    context = {'schema': 1, 'kind': 'gateway-update-controller', 'machineSha256': T.machine_pin(),
        'rootIdentity': T.identity(CONTROLLER), 'updatesIdentity': T.identity(ROOT),
        'sourceSha256': P.sha(P.encode(intent)), 'unitSha256': P.sha(unit_bytes()),
        'unitIdentity': [info.st_dev, info.st_ino], 'originalSourceSha256': P.sha(original),
        'maintenancePolicyGeneration': E.generation(POLICY.lstat()),
        'maintenancePolicySha256': P.sha(changes['runtime/portable/needrestart-droiddeck.conf']),
        'policyAuthority': inherited, 'currentGpu': current,
        'bootUnitSha256': P.sha(boot_bytes(current)), 'bootUnitGeneration': E.generation((SYSTEMD / BOOT).lstat())}
    T.write_new(CONTROLLER / 'context.json', P.encode(context))
    G.command(['/usr/bin/systemd-analyze', 'verify', str(SYSTEMD / TEMPLATE), str(SYSTEMD / BOOT)])
    G.command(['/usr/bin/systemctl', 'daemon-reload'])
    T.write_new(CONTROLLER / 'complete.json', P.encode(context))
    return {'kind': 'gateway-update-controller-enrolled', 'servicesStarted': False, 'codeApplied': False}


def inspect_controller():
    raw = G.protected_read(CONTROLLER / 'context.json')
    value = P.document(raw)
    if (set(value) != {'schema', 'kind', 'machineSha256', 'rootIdentity', 'updatesIdentity', 'sourceSha256',
            'unitSha256', 'unitIdentity', 'originalSourceSha256', 'maintenancePolicyGeneration', 'maintenancePolicySha256',
            'policyAuthority', 'currentGpu', 'bootUnitSha256', 'bootUnitGeneration'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'gateway-update-controller' or value['machineSha256'] != T.machine_pin()
            or value['rootIdentity'] != T.identity(CONTROLLER) or value['updatesIdentity'] != T.identity(ROOT)
            or G.protected_read(CONTROLLER / 'complete.json') != raw
            or G.protected_read(SYSTEMD / TEMPLATE, 0o644) != unit_bytes()):
        raise ValueError('Original completed controller enrollment required')
    info = os.stat(SYSTEMD / TEMPLATE, follow_symlinks=False)
    if [info.st_dev, info.st_ino] != value['unitIdentity'] or P.sha(unit_bytes()) != value['unitSha256']:
        raise ValueError('Original controller template inode required')
    if (E.generation(POLICY.lstat()) != value['maintenancePolicyGeneration']
            or P.sha(G.protected_read(POLICY, 0o644, 4096)) != value['maintenancePolicySha256']):
        raise ValueError('Original accepted coupled-service maintenance policy required')
    if (G.protected_read(SYSTEMD / BOOT, 0o644) != boot_bytes(value['currentGpu'])
            or P.sha(boot_bytes(value['currentGpu'])) != value['bootUnitSha256']
            or E.generation((SYSTEMD / BOOT).lstat()) != value['bootUnitGeneration']
            or value['policyAuthority'] is not None and policy_authority() != value['policyAuthority']):
        raise ValueError('Original boot recovery and policy authority required')
    original, originals = T.base_sources()
    source_raw = G.protected_read(SOURCE / 'intent.json')
    files = {**originals, **overlays(SOURCE)}
    expected = {'schema': 1, 'kind': 'gateway-update-controller-sources', 'root': str(SOURCE),
        'rootIdentity': T.identity(SOURCE), 'baseIntentSha256': P.sha(original), 'files': rows(files)}
    if (P.sha(original) != value['originalSourceSha256'] or P.sha(source_raw) != value['sourceSha256']
            or source_raw != P.encode(expected)):
        raise ValueError('Original complete independent controller sources required')
    fd = H.protected(SOURCE, directory=True)
    try: T.S.finish(fd, expected, files, '--inspect-gpu-sources', root=SOURCE)
    finally: os.close(fd)
    return value


def boot_links(context):
    watch = 'droiddeck-fresh-gpu-watch@' + context['currentGpu'] + '.service'
    return [SYSTEMD / (name + suffix) / BOOT for name, suffix in (
        ('multi-user.target', '.wants'), (RUNTIME, '.requires'), (watch, '.requires'))]


def inspect_bootstrap(context):
    raw = G.protected_read(CONTROLLER / 'boot-armed.json')
    value = P.document(raw)
    expected = {'schema': 1, 'contextSha256': P.sha(P.encode(context)), 'links': {}}
    for path in boot_links(context):
        fd = H.protected(path.parent, directory=True)
        try:
            info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISLNK(info.st_mode) or (info.st_uid, info.st_gid) != (0, 0)
                    or os.readlink(path.name, dir_fd=fd) != str(SYSTEMD / BOOT)):
                raise ValueError('Original fixed boot recovery dependency required')
            expected['links'][str(path)] = E.generation(info)
        finally: os.close(fd)
    if value != expected: raise ValueError('Original boot dependency generations required')


def arm_boot():
    context = inspect_controller()
    if os.path.lexists(CONTROLLER / 'boot-armed.json'):
        inspect_bootstrap(context)
        return {'kind': 'gateway-boot-armed', 'readOnly': True}
    if os.listdir(ROOT): raise ValueError('Arm boot recovery before preparing the first update')
    links = boot_links(context)
    if any(os.path.lexists(path) for path in links):
        raise ValueError('Existing dependency cannot be silently adopted')
    # Publish intent before the first enable link. Partial publication remains
    # fail-closed; it cannot be replayed as a fresh enrollment.
    T.write_new(CONTROLLER / 'boot-arm-intent.json', P.encode({'schema': 1,
        'contextSha256': P.sha(P.encode(context)), 'links': [str(path) for path in links]}))
    result = {'schema': 1, 'contextSha256': P.sha(P.encode(context)), 'links': {}}
    for path in links:
        parent = H.protected(SYSTEMD, directory=True)
        try:
            try: os.mkdir(path.parent.name, mode=0o755, dir_fd=parent); os.fsync(parent)
            except FileExistsError: pass
        finally: os.close(parent)
        fd = H.protected(path.parent, directory=True)
        try:
            os.symlink(str(SYSTEMD / BOOT), path.name, dir_fd=fd); os.fsync(fd)
            result['links'][str(path)] = E.generation(os.stat(path.name, dir_fd=fd, follow_symlinks=False))
        finally: os.close(fd)
    T.write_new(CONTROLLER / 'boot-armed.json', P.encode(result))
    inspect_bootstrap(context)
    G.command(['/usr/bin/systemctl', 'daemon-reload'])
    G.command(['/usr/bin/systemctl', 'start', BOOT], timeout=120)
    return {'kind': 'gateway-boot-armed', 'codeApplied': False, 'originalServicesRestarted': False}


def signed_input(digest):
    P.digest(digest)
    directory = A.ROOT / digest
    with ExitStack() as stack:
        base = A.load('update-base'); records = base.Records(stack)
        fd = A.private_directory(records, directory)
        if set(os.listdir(fd)) != A.FILES: raise ValueError('Original complete signed admission required')
        raw = records.read(directory / 'intent.json', maximum=32768)
        intent = P.document(raw); info = os.fstat(fd)
        if (intent.get('directoryIdentity') != [info.st_dev, info.st_ino]
                or intent.get('bundleSha256') != digest or intent.get('machineSha256') != A.machine(records)):
            raise ValueError('Original machine-bound admission required')
        trust = records.read(A.TRUST, maximum=4096)
        bundle = records.read(directory / 'bundle.tar', maximum=P.MAX_ARCHIVE)
        statement = records.read(directory / 'statement.json', maximum=8192)
        signature = records.read(directory / 'signature.bin', maximum=64)
        if (P.sha(trust) != intent['trustSha256'] or P.sha(bundle) != digest
                or P.sha(statement) != intent['statementSha256'] or P.sha(signature) != intent['signatureSha256']
                or records.read(directory / 'complete.json') != P.encode({'schema': 1, 'intentSha256': P.sha(raw),
                    'objects': A.retained_objects(records, directory)})):
            raise ValueError('Original admitted bytes and immutable receipt generations required')
        manifest, _, key = A.authenticate(bundle, statement, signature, trust)
        if key != intent['keySha256'] or manifest['target'] != intent['target'] or manifest['base'] != intent['base']:
            raise ValueError('Original authenticated release identity required')
        if len(manifest['components']) != 1 or manifest['components'][0]['name'] != 'gateway':
            raise ValueError('Only gateway code may be updated by this controller')
        records.check()
        return intent, manifest, bundle, {name: list(row[3]) for name, row in sorted(records.files.items())}


def namespace(proof):
    result = {}
    for name, row in sorted(proof['objects'].items()):
        if Path(name).is_relative_to(LIVE): continue
        info = os.lstat(name)
        if [info.st_dev, info.st_ino] != [row['device'], row['inode']]:
            raise ValueError('Original non-gateway installation object required')
        if stat.S_ISDIR(info.st_mode):
            result[name] = {'identity': E.directory(info, stat.S_IMODE(info.st_mode)), 'children': sorted(os.listdir(name))}
        else:
            result[name] = {'generation': E.generation(info)}
    return result


def read_public_cert(config):
    # Read only the fixed PUBLIC certificate, never key.pem or its directory listing.
    path = Path(config['home']) / '.config/droiddeck/cert.pem'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != config['uid']
                or stat.S_IMODE(info.st_mode) not in (0o600, 0o644) or not 1 <= info.st_size <= 65536):
            raise ValueError('Original bounded public TLS certificate required')
        raw = os.read(fd, 65537)
        if len(raw) != info.st_size or any(E.generation(current) != E.generation(info) for current in (os.fstat(fd), path.lstat())):
            raise ValueError('Original public certificate generation changed')
        return raw, E.generation(info)
    finally: os.close(fd)


def tree(parent, name, inventory):
    fd = H.protected(parent, directory=True)
    try: return E.tree(fd, name, inventory)
    finally: os.close(fd)


def prepare(digest):
    context = inspect_controller()
    inspect_bootstrap(context)
    intent, manifest, bundle, admitted = signed_input(digest)
    A.inspect(digest, intent['package'])
    proof = A.base_proof(intent['mode'], intent['package'])
    if A.bind(manifest, proof, intent['mode']) != intent['baseProofSha256']:
        raise ValueError('Exact admitted original base required')
    changed = manifest['components'][0]
    before = tree(LIVE.parent, LIVE.name, changed['before'])
    original_namespace = namespace(proof)
    config = json.loads(G.protected_read(Path('/etc/droiddeck/runtime.json'), 0o644, 16384), object_pairs_hook=P.unique)
    raw_cert, cert_generation = read_public_cert(config)
    current = context['currentGpu']
    directory = ROOT / digest
    T.mkdir_new(directory)
    transaction = {'schema': 1, 'kind': 'signed-gateway-code-transaction', 'digest': digest,
        'rootIdentity': T.identity(directory), 'contextSha256': P.sha(P.encode(context)),
        'admissionGenerations': admitted, 'baseProof': proof, 'originalNamespace': original_namespace,
        'before': before, 'configuration': config, 'certSha256': P.sha(raw_cert), 'certGeneration': cert_generation,
        'currentGpu': current, 'originalAdmission': intent,
        'runtimeUnit': {'generation': E.generation((SYSTEMD / RUNTIME).lstat()),
                        'sha256': P.sha(G.protected_read(SYSTEMD / RUNTIME, 0o644, 16384))}}
    T.write_new(directory / 'intent.json', P.encode(transaction))
    candidate = directory / 'candidate'; candidate.mkdir(mode=0o755); candidate.chmod(0o755)
    with tarfile.open(fileobj=io.BytesIO(bundle), mode='r:') as archive:
        for row in changed['after']:
            path = candidate / row['path']
            relative = Path(row['path']).parent
            current_parent = candidate
            for part in relative.parts:
                current_parent /= part
                if not current_parent.exists(): current_parent.mkdir(mode=0o755); current_parent.chmod(0o755)
            data = archive.extractfile(P.PREFIX + 'payload/gateway/' + row['path']).read()
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, row['mode'])
            try:
                os.fchmod(fd, row['mode']); view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0: raise OSError('Incomplete target code copy')
                    view = view[written:]
                os.fsync(fd)
            finally: os.close(fd)
    for path, _, _ in os.walk(candidate, topdown=False):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    after = tree(directory, 'candidate', changed['after'])
    if before['directories'][''][0] != after['directories'][''][0]:
        raise ValueError('Same-filesystem atomic code exchange required')
    if A.base_proof(intent['mode'], intent['package']) != proof or namespace(proof) != original_namespace:
        raise ValueError('Original installation changed during target preparation')
    T.write_new(directory / 'ready.json', P.encode({'schema': 1, 'intentSha256': P.sha(P.encode(transaction)), 'after': after}))
    return {'kind': 'gateway-update-prepared', 'digest': digest, 'codeApplied': False, 'servicesChanged': False}


class Transaction:
    def __init__(self, digest):
        self.digest, self.directory = P.digest(digest), ROOT / digest
        self.context = inspect_controller()
        self.intent_raw = G.protected_read(self.directory / 'intent.json', maximum=2 * 1024 * 1024)
        self.value = P.document(self.intent_raw)
        ready = P.document(G.protected_read(self.directory / 'ready.json', maximum=2 * 1024 * 1024))
        self.admission, self.manifest, _, admitted = signed_input(digest)
        if (set(self.value) != {'schema', 'kind', 'digest', 'rootIdentity', 'contextSha256', 'admissionGenerations',
                'baseProof', 'originalNamespace', 'before', 'configuration', 'certSha256', 'certGeneration', 'currentGpu', 'originalAdmission', 'runtimeUnit'}
                or type(self.value['schema']) is not int or self.value['schema'] != 1
                or self.value['kind'] != 'signed-gateway-code-transaction' or self.value['digest'] != digest
                or self.value['rootIdentity'] != T.identity(self.directory)
                or self.value['contextSha256'] != P.sha(P.encode(self.context))
                or self.value['admissionGenerations'] != admitted or self.value['originalAdmission'] != self.admission
                or A.bind(self.manifest, self.value['baseProof'], self.admission['mode']) != self.admission['baseProofSha256']
                or set(ready) != {'schema', 'intentSha256', 'after'} or type(ready['schema']) is not int or ready['schema'] != 1
                or ready['intentSha256'] != P.sha(self.intent_raw)):
            raise ValueError('Original prepared authenticated code transaction required')
        self.after, self.changed = ready['after'], self.manifest['components'][0]
        self.module = load('fresh-gpu', SOURCE); self.module.ROOT = self.module.G.ROOT = SOURCE
        self.module.configure(self.value['currentGpu'])
        admitted_gpu = P.document(self.module.G.protected_read(self.module.ADMISSIONS / (self.value['currentGpu'] + '.json')))
        parser, _ = self.module.G.sources()
        self.active = self.module.Fresh(parser, admitted_gpu)
        self.g = self.module.G
        self.unit = instance(digest)
        self.observe()

    def marker(self, name):
        T.write_new(self.directory / (name + '.json'), P.encode({'schema': 1, 'intentSha256': P.sha(self.intent_raw), 'phase': name}))

    def observe(self):
        allowed = {'intent.json', 'ready.json', 'candidate', 'apply-intent.json', 'applied.json',
                   'healthy.json', 'rollback-request.json', 'rollback-intent.json', 'rolled-back.json', 'boot-code-restored.json'}
        names = set(os.listdir(self.directory))
        if not names <= allowed or not {'intent.json', 'ready.json', 'candidate'} <= names:
            raise ValueError('Closed original transaction required')
        for name in names - {'intent.json', 'ready.json', 'candidate'}:
            if G.protected_read(self.directory / name) != P.encode({'schema': 1,
                    'intentSha256': P.sha(self.intent_raw), 'phase': name[:-5]}):
                raise ValueError('Original exact phase required')
        identity = E.directory(os.lstat(LIVE), 0o755)
        if identity == self.value['before']['directories']['']:
            side = 'before'
            if (tree(LIVE.parent, LIVE.name, self.changed['before']) != self.value['before']
                    or tree(self.directory, 'candidate', self.changed['after']) != self.after):
                raise ValueError('Original retained code pair changed')
        elif identity == self.after['directories']['']:
            side = 'after'
            if ('apply-intent.json' not in names
                    or tree(LIVE.parent, LIVE.name, self.changed['after']) != self.after
                    or tree(self.directory, 'candidate', self.changed['before']) != self.value['before']):
                raise ValueError('Original authenticated exchanged pair required')
        else: raise ValueError('Foreign live code root cannot be adopted')
        if ('applied.json' in names and 'apply-intent.json' not in names
                or 'healthy.json' in names and 'applied.json' not in names
                or 'rolled-back.json' in names and ('rollback-intent.json' not in names or side != 'before')
                or 'rollback-intent.json' in names and 'apply-intent.json' not in names
                or 'rollback-request.json' in names and 'healthy.json' not in names
                or 'boot-code-restored.json' in names and ('rollback-intent.json' not in names or side != 'before')
                or side == 'before' and 'applied.json' in names and 'rollback-intent.json' not in names):
            raise ValueError('Ordered original code phases required')
        if (namespace(self.value['baseProof']) != self.value['originalNamespace']
                or G.protected_read(self.directory / 'intent.json', maximum=2 * 1024 * 1024) != self.intent_raw):
            raise ValueError('Non-gateway original ownership changed')
        for path, generation in self.value['baseProof']['receiptGenerations'].items():
            if E.generation(os.lstat(path)) != generation:
                raise ValueError('Original installation receipt generation changed')
        cert, generation = read_public_cert(self.value['configuration'])
        if P.sha(cert) != self.value['certSha256'] or generation != self.value['certGeneration']:
            raise ValueError('Original TLS identity changed')
        return side, names

    def actor(self):
        inspect_controller()
        inspect_bootstrap(self.context)
        if self.g.unit(BOOT)['ActiveState'] != 'active':
            raise ValueError('Current boot recovery barrier must have completed')
        state = self.g.unit(self.unit)
        if state['ActiveState'] != 'active' or state['MainPID'] != str(os.getpid()) or state['ControlPID'] != '0':
            raise ValueError('Independent enrolled systemd controller process required')
        raw = self.g.command(['/usr/bin/systemctl', 'show', self.unit, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
        if dict(line.split('=', 1) for line in raw.decode('ascii').splitlines()) != {
                'FragmentPath': str(SYSTEMD / TEMPLATE), 'DropInPaths': ''}:
            raise ValueError('Original controller without overrides required')
        self.active.metadata(); self.g.sources()
        if (E.generation((SYSTEMD / RUNTIME).lstat()) != self.value['runtimeUnit']['generation']
                or P.sha(self.g.protected_read(SYSTEMD / RUNTIME, 0o644, 16384)) != self.value['runtimeUnit']['sha256']):
            raise ValueError('Original accepted runtime service generation required')
        runtime_layout = self.g.command(['/usr/bin/systemctl', 'show', RUNTIME, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
        if dict(line.split('=', 1) for line in runtime_layout.decode('ascii').splitlines()) != {
                'FragmentPath': str(SYSTEMD / RUNTIME), 'DropInPaths': ''}:
            raise ValueError('Original runtime service without overrides required')
        self.g.require_quiet(self.g.unit(self.g.WORKER))
        watch = self.g.unit(self.g.WATCHER)
        if watch['ActiveState'] not in ('active', 'activating') or int(watch['MainPID']) < 2:
            raise ValueError('Original independent GPU recovery must remain armed')
        template = SYSTEMD / 'droiddeck-fresh-gpu-watch@.service'
        if P.sha(self.g.protected_read(template)) != T.WATCH_PIN:
            raise ValueError('Original accepted GPU watcher template required')
        raw = self.g.command(['/usr/bin/systemctl', 'show', self.g.WATCHER, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
        if dict(line.split('=', 1) for line in raw.decode('ascii').splitlines()) != {
                'FragmentPath': str(template), 'DropInPaths': ''}:
            raise ValueError('Original GPU supervision without foreign overrides required')

    def native(self):
        result = self.g.command(['/usr/bin/node', str(SOURCE / 'runtime/portable/gpu-native.js')], timeout=35)
        value = json.loads(result.stdout)
        if value.get('nativeGpuVerified') is not True: raise ValueError('Independent native GPU proof required')
        return {name: value[name] for name in ('containerIdentity', 'androidIdentity')}

    def stop_runtime(self):
        self.actor()
        self.g.command(['/usr/bin/systemctl', 'stop', RUNTIME], timeout=90)
        self.g.require_quiet(self.g.unit(RUNTIME))
        self.observe()

    def exchange(self, guard=None):
        (guard or self.actor)(); self.g.require_quiet(self.g.unit(RUNTIME)); self.observe()
        left, right = H.protected(LIVE.parent, directory=True), H.protected(self.directory, directory=True)
        try:
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
            rename.restype = ctypes.c_int
            if rename(left, LIVE.name.encode(), right, b'candidate', 2) != 0:
                raise OSError(ctypes.get_errno(), 'Original atomic gateway exchange failed')
            os.fsync(left); os.fsync(right)
        finally: os.close(left); os.close(right)
        self.observe(); self.g.require_quiet(self.g.unit(RUNTIME))

    def start_and_verify(self, target):
        self.actor(); self.observe()
        self.g.command(['/usr/bin/systemctl', 'start', RUNTIME], timeout=90)
        config = self.value['configuration']; cert, _ = read_public_cert(config)
        ssl_context = ssl.create_default_context(cadata=cert.decode('ascii'))
        # Proxy environment variables never route this local pinned-TLS probe.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl_context))
        origin = 'https://' + config['lanAddress'] + ':' + str(config['port'])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with opener.open(origin + '/api/runtimeRelease', timeout=5) as response:
                    value = json.loads(response.read(1025))
                    if (not target or value != {'kind': 'droiddeck-runtime-release',
                            'version': self.manifest['target']['version'], 'updateProtocol': 1}):
                        raise ValueError('Exact new running release required')
                self.native(); self.actor(); self.observe()
                if self.g.unit(RUNTIME)['ActiveState'] == 'active': return
            except urllib.error.HTTPError as error:
                if not target and error.code in (401, 404, 502):
                    # RC2 has no release endpoint. Original code/full ownership
                    # and separate browser-media verification still must pass.
                    self.native(); self.actor(); self.observe(); return
            except (OSError, ValueError, urllib.error.URLError): pass
            time.sleep(1)
        raise ValueError('Pinned-TLS running gateway/native health did not verify')

    def run(self):
        self.actor()
        with ExitStack() as stack:
            fd = H.protected(self.directory, directory=True); stack.callback(os.close, fd)
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stack.enter_context(self.active.store.lock())
            state = self.active.store.status()
            if state['stage'] != 'ready' or state['boot'] != self.g.boot_id():
                raise ValueError('Original GPU recovery must finish for this boot before updating')
            side, names = self.observe()
            if 'rolled-back.json' in names: return {'kind': 'gateway-update-rolled-back', 'readOnly': True}
            if 'healthy.json' in names and 'rollback-request.json' not in names:
                return {'kind': 'gateway-update-healthy', 'readOnly': True}
            if 'apply-intent.json' in names or 'rollback-request.json' in names:
                self.rollback()
            else:
                before = self.native()
                state = self.active.store.status()
                if state['stage'] != 'ready' or state['boot'] != self.g.boot_id():
                    raise ValueError('Accepted current-boot native-ready GPU required')
                self.marker('apply-intent')
                self.stop_runtime()
                if self.native() != before: raise ValueError('Android generation changed before code exchange')
                self.exchange(); self.marker('applied')
                self.start_and_verify(True)
                if self.native() != before: raise ValueError('Android changed during gateway-only update')
                self.marker('healthy')
                return {'kind': 'gateway-update-healthy', 'digest': self.digest, 'codeApplied': True,
                        'androidGenerationUnchanged': True, 'originalGpuWatcherRemainedArmed': True,
                        'browserMediaAcceptanceRequired': True, 'installable': False}
        # The complete original base proof itself takes the original GPU lock.
        # Release our mutation fence before taking that separate read-only proof.
        self.actor()
        return self.verify_restored()

    def rollback(self):
        side, names = self.observe()
        if 'rollback-intent.json' not in names: self.marker('rollback-intent')
        self.stop_runtime()
        if side == 'after': self.exchange()
        self.start_and_verify(False)

    def verify_restored(self):
        side, names = self.observe()
        if side != 'before' or 'rollback-intent.json' not in names or 'rolled-back.json' in names:
            raise ValueError('Original restored pending rollback required')
        proof = A.base_proof(self.admission['mode'], self.admission['package'])
        if proof != self.value['baseProof']: raise ValueError('Original complete installation did not return')
        self.native(); self.observe()
        self.marker('rolled-back')
        return {'kind': 'gateway-update-rolled-back', 'digest': self.digest,
                'originalInstallationReverified': True, 'originalDirectoryInodeRestored': True}


def boot_actor():
    context = inspect_controller(); inspect_bootstrap(context)
    state = G.unit(BOOT)
    if (state['ActiveState'] != 'activating' or str(os.getpid()) not in (state['MainPID'], state['ControlPID'])):
        raise ValueError('Original supervised boot recovery process required')
    raw = G.command(['/usr/bin/systemctl', 'show', BOOT, '-p', 'FragmentPath', '-p', 'DropInPaths']).stdout
    if dict(line.split('=', 1) for line in raw.decode('ascii').splitlines()) != {
            'FragmentPath': str(SYSTEMD / BOOT), 'DropInPaths': ''}:
        raise ValueError('Original boot recovery unit without overrides required')


def restore_code_at_boot(transaction):
    """Restore only a pending original code pair, before runtime or GPU starts.

    This intentionally does not mark rollback complete. After the GPU watcher
    recovers the current boot, the regular controller proves native/TLS health
    and the complete original installation before writing rolled-back.json.
    """
    import fcntl
    with ExitStack() as stack:
        fd = H.protected(transaction.directory, directory=True); stack.callback(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.enter_context(transaction.active.store.lock())
        boot_actor()
        side, names = transaction.observe()
        if ('rolled-back.json' in names or 'apply-intent.json' not in names
                or 'healthy.json' in names and 'rollback-request.json' not in names):
            return False

        def guard():
            boot_actor()
            transaction.active.metadata(); transaction.g.sources()
            for unit in (transaction.unit, RUNTIME, transaction.g.WATCHER, transaction.g.WORKER):
                transaction.g.require_quiet(transaction.g.unit(unit))
            info = transaction.value['runtimeUnit']
            if (E.generation((SYSTEMD / RUNTIME).lstat()) != info['generation']
                    or P.sha(G.protected_read(SYSTEMD / RUNTIME, 0o644, 16384)) != info['sha256']):
                raise ValueError('Original runtime service required before boot restoration')
        guard()
        if 'rollback-intent.json' not in names: transaction.marker('rollback-intent')
        if side == 'after': transaction.exchange(guard=guard)
        guard()
        side, names = transaction.observe()
        if side != 'before': raise ValueError('Original code must precede boot startup')
        if 'boot-code-restored.json' not in names: transaction.marker('boot-code-restored')
        return True


def recover_code_at_boot():
    boot_actor()
    fd = H.protected(ROOT, directory=True)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        names = sorted(os.listdir(fd))
        if len(names) > 32 or any(not re.fullmatch('[a-f0-9]{64}', name) for name in names):
            raise ValueError('Bounded closed original transaction namespace required')
        # Verify every original pair before starting dependent services. A partial
        # preparation or foreign entry cannot be ignored as a successful boot.
        transactions = [Transaction(name) for name in names]
        pending = [tx for tx in transactions if restore_code_at_boot(tx)]
        boot_actor()
        for tx in pending:
            G.command(['/usr/bin/systemctl', 'start', '--no-block', tx.unit])
        return {'kind': 'gateway-code-boot-barrier', 'transactionsVerified': len(transactions),
                'originalCodeRestored': len(pending), 'rollbackCompletionDeferredUntilNativeReady': bool(pending)}
    finally: os.close(fd)


def request_rollback(digest):
    transaction = Transaction(digest)
    _, names = transaction.observe()
    if 'healthy.json' not in names or 'rollback-request.json' in names or 'rolled-back.json' in names:
        raise ValueError('Only one explicit rollback of original healthy update allowed')
    transaction.marker('rollback-request')
    return {'kind': 'gateway-update-rollback-requested', 'digest': digest, 'servicesChanged': False}


def finalize_restored(digest):
    """Close only an already restored rollback after stopping its old controller.

    This maintenance operation cannot exchange code or claim unperformed phases.
    It supports the preserved first controller's nested-lock verification defect.
    """
    transaction = Transaction(digest)
    transaction.g.command(['/usr/bin/systemctl', 'stop', transaction.unit], timeout=30)
    transaction.g.require_quiet(transaction.g.unit(transaction.unit))
    side, phases = transaction.observe()
    if side != 'before' or 'rollback-intent.json' not in phases:
        raise ValueError('No known restored rollback to finalize; no code operation available')
    result = transaction.verify_restored()
    transaction.g.require_quiet(transaction.g.unit(transaction.unit))
    return result


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Explicit root Linux operation required')
    os.umask(0o077)
    if len(args) == 3 and args[0] == 'enroll': result = enroll(args[1], args[2])
    elif args == ['inspect-controller']: result = inspect_controller()
    elif args == ['arm-boot']: result = arm_boot()
    elif args == ['recover-code-at-boot']:
        if Path(__file__).absolute() != SOURCE / SELF: raise ValueError('Enrolled boot entrypoint required')
        result = recover_code_at_boot()
    elif len(args) == 2 and args[0] == 'prepare': result = prepare(args[1])
    elif len(args) == 2 and args[0] == 'inspect':
        side, names = Transaction(args[1]).observe(); result = {'kind': 'gateway-update-inspection', 'side': side, 'phases': sorted(names), 'readOnly': True}
    elif len(args) == 2 and args[0] == 'rollback-request': result = request_rollback(args[1])
    elif len(args) == 2 and args[0] == 'finalize-restored-rollback': result = finalize_restored(args[1])
    elif len(args) == 2 and args[0] == 'run':
        if Path(__file__).absolute() != SOURCE / SELF: raise ValueError('Enrolled systemd entrypoint required')
        result = Transaction(args[1]).run()
    else: raise ValueError('Explicit enroll/inspect-controller/prepare/inspect/rollback-request/run required')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
