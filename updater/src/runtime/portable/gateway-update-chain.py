# SPDX-License-Identifier: GPL-3.0-only
"""Cumulative signed gateway updates with an original RC2 rollback baseline.

Each newer release is admitted against the retained original installation after
the preceding attempt has fully rolled back. Android data and GPU supervision
stay intact. History is append-only; preparation is unpublished and cannot affect
boot. No download, package hook or arbitrary destination is supported here.
"""
from contextlib import ExitStack, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

spec = importlib.util.spec_from_file_location('chain_gateway_base', Path(__file__).with_name('gateway-update.py'))
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
P, T, H, E, G = B.P, B.T, B.H, B.E, B.G
B.CONTROLLER = Path('/var/lib/droiddeck/gateway-update-controller-v3')
B.ROOT = Path('/var/lib/droiddeck/gateway-updates-v3')
B.SOURCE = B.CONTROLLER / 'sources'
B.TEMPLATE = 'droiddeck-gateway-update-v3@.service'
B.BOOT = 'droiddeck-gateway-update-boot-v3.service'
B.SELF = 'runtime/portable/gateway-update-chain.py'
B.OVERLAYS = (*B.OVERLAYS, 'gateway-update-chain.py')
PREPARING = B.CONTROLLER / 'preparing'
HISTORY = B.CONTROLLER / 'history'
ORIGINAL_INSPECT = B.inspect_controller
BASE_TRANSACTION = B.Transaction


def inspect_controller():
    context = ORIGINAL_INSPECT()
    raw = G.protected_read(B.CONTROLLER / 'chain-roots.json')
    expected = {'schema': 1, 'contextSha256': P.sha(P.encode(context)),
                'preparingIdentity': T.identity(PREPARING), 'historyIdentity': T.identity(HISTORY)}
    if raw != P.encode(expected) or G.protected_read(B.CONTROLLER / 'chain-complete.json') != raw:
        raise ValueError('Original completed preparation/history enrollment required')
    return context


B.inspect_controller = inspect_controller


def enroll(root, pin):
    result = B.enroll(root, pin)
    context = ORIGINAL_INSPECT()
    T.mkdir_new(PREPARING); T.mkdir_new(HISTORY)
    raw = P.encode({'schema': 1, 'contextSha256': P.sha(P.encode(context)),
        'preparingIdentity': T.identity(PREPARING), 'historyIdentity': T.identity(HISTORY)})
    T.write_new(B.CONTROLLER / 'chain-roots.json', raw)
    T.write_new(B.CONTROLLER / 'chain-complete.json', raw)
    inspect_controller()
    return result


@contextmanager
def locked():
    import fcntl
    context = inspect_controller()
    fd = H.protected(B.ROOT, directory=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield context
    finally: os.close(fd)


def records():
    fd = H.protected(HISTORY, directory=True)
    try: names = sorted(os.listdir(fd))
    finally: os.close(fd)
    if len(names) > 32 or names != ['%04d.json' % n for n in range(1, len(names) + 1)]:
        raise ValueError('Closed bounded consecutive release history required')
    result, seen = [], set()
    for ordinal, name in enumerate(names, 1):
        path = HISTORY / name
        raw = G.protected_read(path, maximum=65536); value = P.document(raw)
        if (set(value) != {'schema', 'ordinal', 'digest', 'preparation', 'transaction', 'parent'}
                or type(value['schema']) is not int or value['schema'] != 1
                or type(value['ordinal']) is not int or value['ordinal'] != ordinal
                or not re.fullmatch('[a-f0-9]{12}', value['preparation'])
                or P.digest(value['digest']) in seen):
            raise ValueError('Original unique ordered release attempt required')
        parent = value['parent']
        if not result:
            if parent is not None: raise ValueError('First release has no predecessor')
        else:
            previous = result[-1]
            if (type(parent) is not dict or set(parent) != {'recordSha256', 'recordGeneration', 'rollbackGeneration'}
                    or parent['recordSha256'] != previous['sha256'] or parent['recordGeneration'] != previous['generation']):
                raise ValueError('Original predecessor history inode and bytes required')
            marker = B.ROOT / previous['value']['digest'] / 'rolled-back.json'
            if E.generation(marker.lstat()) != parent['rollbackGeneration']:
                raise ValueError('Original completed predecessor rollback generation required')
        seen.add(value['digest'])
        result.append({'value': value, 'sha256': P.sha(raw), 'generation': E.generation(path.lstat())})
    return result


def transaction_receipt(directory):
    return {'rootIdentity': T.identity(directory),
        'intentGeneration': E.generation((directory / 'intent.json').lstat()),
        'readyGeneration': E.generation((directory / 'ready.json').lstat())}


@contextmanager
def preparation_namespace(directory, context):
    # Reuse the checked, service-free original preparer in an unpublished fixed
    # namespace. No live path is redirected. Recheck actual enrollment afterwards.
    original_root, original_inspect = B.ROOT, B.inspect_controller
    B.ROOT, B.inspect_controller = directory, lambda: context
    try: yield
    finally: B.ROOT, B.inspect_controller = original_root, original_inspect
    if inspect_controller() != context: raise ValueError('Controller changed during preparation')


def rename_new(source_parent, name, destination_parent, destination):
    left = H.protected(source_parent, directory=True)
    right = H.protected(destination_parent, directory=True)
    try:
        rename = B.ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = (B.ctypes.c_int, B.ctypes.c_char_p, B.ctypes.c_int, B.ctypes.c_char_p, B.ctypes.c_uint)
        rename.restype = B.ctypes.c_int
        if rename(left, name.encode('ascii'), right, destination.encode('ascii'), 1) != 0:
            raise OSError(B.ctypes.get_errno(), 'Exclusive original publication failed')
        os.fsync(left); os.fsync(right)
    finally: os.close(left); os.close(right)


def finish_publication(row, context):
    value = row['value']; digest = value['digest']; destination = B.ROOT / digest
    if os.path.lexists(destination):
        if transaction_receipt(destination) != value['transaction']:
            raise ValueError('Original published transaction inode generations required')
        return
    work = PREPARING / value['preparation']; staged = work / digest
    if transaction_receipt(staged) != value['transaction']:
        raise ValueError('Exact completely prepared original transaction required')
    with preparation_namespace(work, context):
        tx = BASE_TRANSACTION(digest)
        side, phases = tx.observe()
        if side != 'before' or phases != {'intent.json', 'ready.json', 'candidate'}:
            raise ValueError('Only unpublished complete preparation may be published')
    rename_new(work, digest, B.ROOT, digest)
    if transaction_receipt(destination) != value['transaction']:
        raise ValueError('Original transaction changed during publication')


class Transaction(BASE_TRANSACTION):
    def __init__(self, digest, *, historical_base=None):
        self.historical_base = historical_base
        super().__init__(digest)

    def observe(self):
        if self.historical_base is None: return super().observe()
        # A closed predecessor's original before directory now belongs to the
        # current pair. Verify that SAME full before inventory and original proof,
        # its retained target directory and all immutable metadata; never accept a
        # previous root because it merely happens to have the same bytes.
        current = self.historical_base
        names = set(os.listdir(self.directory))
        required = {'intent.json', 'ready.json', 'candidate', 'apply-intent.json', 'rollback-intent.json', 'rolled-back.json'}
        allowed = required | {'applied.json', 'healthy.json', 'rollback-request.json', 'boot-code-restored.json'}
        if not required <= names <= allowed:
            raise ValueError('Historical attempt must be completely rolled back')
        for name in names - {'intent.json', 'ready.json', 'candidate'}:
            if G.protected_read(self.directory / name) != P.encode({'schema': 1,
                    'intentSha256': P.sha(self.intent_raw), 'phase': name[:-5]}):
                raise ValueError('Original historical phase bytes required')
        if ('healthy.json' in names and 'applied.json' not in names
                or 'rollback-request.json' in names and 'healthy.json' not in names):
            raise ValueError('Ordered original historical phases required')
        for name in ('before', 'baseProof', 'originalNamespace', 'configuration', 'certSha256', 'certGeneration', 'currentGpu', 'runtimeUnit'):
            if self.value[name] != current.value[name]:
                raise ValueError('Same original installed baseline lineage required')
        if (B.tree(self.directory, 'candidate', self.changed['after']) != self.after
                or B.namespace(self.value['baseProof']) != self.value['originalNamespace']
                or G.protected_read(self.directory / 'intent.json', maximum=2 * 1024 * 1024) != self.intent_raw):
            raise ValueError('Original historical retained code and metadata required')
        for path, generation in self.value['baseProof']['receiptGenerations'].items():
            if E.generation(os.lstat(path)) != generation: raise ValueError('Original historical receipt required')
        cert, generation = B.read_public_cert(self.value['configuration'])
        if P.sha(cert) != self.value['certSha256'] or generation != self.value['certGeneration']:
            raise ValueError('Original historical TLS identity required')
        return 'historical-original-baseline', names

    def actor(self):
        if self.historical_base is not None: raise ValueError('Historical code may never mutate')
        super().actor()
        rows = records()
        if not rows or rows[-1]['value']['digest'] != self.digest:
            raise ValueError('Only the original current release attempt may run')
        current_view(rows)


def current_view(rows):
    expected = {row['value']['digest'] for row in rows}
    fd = H.protected(B.ROOT, directory=True)
    try:
        if set(os.listdir(fd)) != expected: raise ValueError('Closed published release namespace required')
    finally: os.close(fd)
    if not rows: return None
    for row in rows:
        value = row['value']
        if transaction_receipt(B.ROOT / value['digest']) != value['transaction']:
            raise ValueError('Original release transaction inode generations required')
    current = Transaction(rows[-1]['value']['digest'])
    versions = []
    for row in rows:
        digest = row['value']['digest']
        tx = current if digest == current.digest else Transaction(digest, historical_base=current)
        versions.append(P.version(tx.manifest['target']['version']))
    if any(right <= left for left, right in zip(versions, versions[1:])):
        raise ValueError('Strictly newer signed cumulative releases required')
    return current


def prepare(digest):
    with locked() as context:
        rows = records()
        for row in rows: finish_publication(row, context)
        current = current_view(rows)
        if len(rows) >= 32: raise ValueError('Release history bound reached')
        if current:
            side, phases = current.observe()
            if side != 'before' or 'rolled-back.json' not in phases:
                raise ValueError('Complete original-baseline rollback before preparing the next release')
        _intent, manifest, _bundle, _admitted = B.signed_input(digest)
        if current and P.version(manifest['target']['version']) <= P.version(current.manifest['target']['version']):
            raise ValueError('New attempt must be a strictly newer signed release')
        preparation = secrets.token_hex(6); work = PREPARING / preparation; T.mkdir_new(work)
        with preparation_namespace(work, context): B.prepare(digest)
        directory = work / digest
        value = {'schema': 1, 'ordinal': len(rows) + 1, 'digest': digest, 'preparation': preparation,
            'transaction': transaction_receipt(directory), 'parent': None if not current else {
                'recordSha256': rows[-1]['sha256'], 'recordGeneration': rows[-1]['generation'],
                'rollbackGeneration': E.generation((current.directory / 'rolled-back.json').lstat())}}
        # The history file is complete/fsynced in unpublished storage before an
        # exclusive atomic publication. If the subsequent directory rename is
        # interrupted, boot can publish ONLY this exact ready original inode.
        T.write_new(work / 'history.json', P.encode(value))
        rename_new(work, 'history.json', HISTORY, '%04d.json' % value['ordinal'])
        row = records()[-1]
        finish_publication(row, context); current_view(records())
        return {'kind': 'gateway-chain-prepared', 'digest': digest, 'attempt': len(rows) + 1,
                'codeApplied': False, 'unpublishedPartialPreparationsCannotAffectBoot': True}


def recover_code_at_boot():
    B.boot_actor()
    with locked() as context:
        rows = records()
        for row in rows: finish_publication(row, context)
        current = current_view(rows)
        pending = bool(current and B.restore_code_at_boot(current))
        B.boot_actor()
        if pending: G.command(['/usr/bin/systemctl', 'start', '--no-block', current.unit])
        return {'kind': 'gateway-chain-boot-barrier', 'attemptsVerified': len(rows),
                'originalCodeRestored': pending, 'rollbackCompletionDeferredUntilNativeReady': pending}


def request_rollback(digest):
    with locked():
        current = current_view(records())
        if current is None or current.digest != P.digest(digest): raise ValueError('Original current release required')
        return B.request_rollback(digest)


def review_inputs(bundle_path, statement_path, signature_path):
    bundle = G.protected_read(Path(bundle_path), maximum=P.MAX_ARCHIVE)
    statement = G.protected_read(Path(statement_path), maximum=8192)
    signature = G.protected_read(Path(signature_path), maximum=64)
    trust = G.protected_read(B.A.TRUST, maximum=4096)
    manifest, _review, _key = B.A.authenticate(bundle, statement, signature, trust)
    return P.sha(bundle), manifest


def wait_phase(digest, phase):
    deadline = time.monotonic() + 360
    while time.monotonic() < deadline:
        with locked():
            current = current_view(records())
            if current is None or current.digest != digest:
                raise ValueError('Current release changed during supervised update')
            _, phases = current.observe()
            if phase + '.json' in phases: return current
            if phase == 'healthy' and 'rollback-intent.json' in phases:
                raise ValueError('Apply was interrupted and is rolling back; never resume forward')
        time.sleep(3)
    raise TimeoutError('Supervised update incomplete; preserve original records and inspect status')


def install(mode, package, bundle_path, statement_path, signature_path):
    if mode not in ('completed', 'recovered'): raise ValueError('Explicit original base authority required')
    # Authenticate the new release BEFORE disturbing the current healthy code.
    digest, manifest = review_inputs(bundle_path, statement_path, signature_path)
    with locked() as context:
        rows = records()
        for row in rows: finish_publication(row, context)
        current = current_view(rows)
        if current and current.digest == digest:
            _, phases = current.observe()
            if 'rolled-back.json' in phases: raise ValueError('A rolled-back attempt cannot be replayed')
            if 'healthy.json' in phases and 'rollback-request.json' not in phases:
                return {'kind': 'gateway-chain-already-installed', 'digest': digest, 'readOnly': True}
        elif current and P.version(manifest['target']['version']) <= P.version(current.manifest['target']['version']):
            raise ValueError('Only a strictly newer signed cumulative release may follow the current attempt')
    if current and current.digest != digest:
        _, phases = current.observe()
        if 'rolled-back.json' not in phases:
            if 'healthy.json' in phases and 'rollback-request.json' not in phases:
                request_rollback(current.digest)
                G.command(['/usr/bin/systemctl', 'restart', current.unit], timeout=30)
            elif 'rollback-intent.json' in phases or 'rollback-request.json' in phases:
                G.command(['/usr/bin/systemctl', 'start', '--no-block', current.unit])
            else: raise ValueError('A different pending release must be explicitly resolved first')
            wait_phase(current.digest, 'rolled-back')
    if not current or current.digest != digest:
        if review_inputs(bundle_path, statement_path, signature_path) != (digest, manifest):
            raise ValueError('Reviewed release inputs changed; no new code can run')
        if os.path.lexists(B.A.ROOT / digest):
            admitted, owned_manifest, _bundle, _generations = B.signed_input(digest)
            if owned_manifest != manifest or admitted['mode'] != mode or admitted['package'] != str(Path(package)):
                raise ValueError('Original admitted release and package required')
            B.A.inspect(digest, package)
        else:
            result = B.A.admit(mode, package, bundle_path, statement_path, signature_path)
            if result['admission'] != digest: raise ValueError('Original reviewed release admission required')
        prepare(digest)
    with locked():
        selected = current_view(records())
        if selected is None or selected.digest != digest: raise ValueError('Original current prepared release required')
        _, phases = selected.observe()
        if 'apply-intent.json' in phases:
            # An interrupted/recovering attempt belongs to its existing supervisor.
            # Restart may recover rollback, but this invocation cannot call it a
            # new forward update or replay an apply intent.
            raise ValueError('Existing attempt is recovering; inspect-current before further work')
        G.command(['/usr/bin/systemctl', 'start', '--no-block', selected.unit])
    wait_phase(digest, 'healthy')
    return {'kind': 'gateway-chain-installed', 'digest': digest, 'target': manifest['target'],
            'androidDataPreserved': True, 'rollbackBaseline': P.BASE, 'browserMediaAcceptanceRequired': True}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Explicit root Linux operation required')
    os.umask(0o077)
    if len(args) == 3 and args[0] == 'enroll': result = enroll(args[1], args[2])
    elif args == ['inspect-controller']: result = inspect_controller()
    elif args == ['arm-boot']: result = B.arm_boot()
    elif len(args) == 2 and args[0] == 'prepare': result = prepare(P.digest(args[1]))
    elif args == ['inspect-current']:
        with locked():
            rows = records(); current = current_view(rows)
            result = {'kind': 'gateway-chain-inspection', 'attempts': len(rows), 'readOnly': True,
                'current': None if current is None else current.digest,
                'phases': [] if current is None else sorted(current.observe()[1])}
    elif len(args) == 2 and args[0] == 'rollback-request': result = request_rollback(args[1])
    elif len(args) == 6 and args[0] == 'install':
        if Path(__file__).absolute() != B.SOURCE / B.SELF: raise ValueError('Independent enrolled installer required')
        result = install(*args[1:])
    elif args == ['recover-code-at-boot'] or len(args) == 2 and args[0] == 'run':
        if Path(__file__).absolute() != B.SOURCE / B.SELF: raise ValueError('Independent enrolled systemd entrypoint required')
        result = recover_code_at_boot() if len(args) == 1 else Transaction(P.digest(args[1])).run()
    else: raise ValueError('Explicit enroll/inspect-controller/arm-boot/prepare/inspect-current/rollback-request/run required')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__': main(sys.argv[1:])
