# SPDX-License-Identifier: GPL-3.0-only
"""Independent legacy CPU dependency and owned video-map admission.

Internal API only. The parent coordinator owns supervision, transaction effects
and health/Undo; this module never starts a service or borrows portable policy.
The reviewed CPU profile reconstructs all 21 measured .90 source hashes from
known Git code/transforms. Physical generations and pngjs still require native
admission; this is not an activation or whole-install qualification receipt.
"""
from contextlib import ExitStack
import hashlib
import json
import os
import re
from pathlib import Path
import stat

ROOT = Path('/var/lib/droidmate-aggregate')
PUBLIC = Path('/var/lib/droidmate-browser')
PHYSICAL = Path('/opt/teslaandroid-waydroid/runtime')
VIDEO = Path('/etc/systemd/system/tesla-waydroid-video-preview.service.d/70-droidmate-aggregate.conf')
PACKAGE = 'a5509f99f4f4e4a26d5ced56ee872fc7bed6f3bdc2af8826cdd181393c7d26da'
PNG = 'waydroid-display/node_modules/pngjs/'
# Transitive literal requires of the QM launcher at 949559d419c749fdbb3c99c8ca8e372d5655420b.
# Only video-settings.js uses the accepted RC20 backport source ee74977c... .
HISTORICAL_CPU_PINS = {
 'preview-gateway/compositor-video-launcher.js':'0b1d6f08c0ac6afbe85781c23238fbd57db4dc0ff70230cf41ff132c211edc86',
 'preview-gateway/compositor-h264.py':'d831a9aa9a34a66820aef35473c2f6e0b357df3719a940a0d2ae16ab966523ad',
 'preview-gateway/package.json':'b81858425ecda17351ae3a16173e9383b3dcd9eff61dd0b8000a729b4507bd3b',
 'preview-gateway/video-settings.js':'254475cc28534cf8e57135e40e26db146b5a7417b9dc11c9c44c40f5bbb2d4d3',
 'preview-gateway/video-capture-controller.js':'9a51072417802186b672fe2843cc562930f784b3ab76a6039e6a6192f5b8c061',
 'preview-gateway/capture-lease.js':'032ad1ee9a4acef2633581bfd798f3757dc2cfc5f1dedd696d6675bd52dadf43',
 'waydroid-display/package.json':'dadda34469dc81a6c38eaba5a633a336e266ca6fca38d6a87cdefdd05dce992a',
 'waydroid-display/runtime-native.js':'54f0a0661ec4b8d2bdb8b033b16782fa1cce0d5728c5b8e76532a8a3e222bd12',
 'waydroid-display/runtime-preflight.js':'8982bf8a2292eb97035abb21eb1c74bbb1b54cfae9dddd0a5692d18b8abf9e9f',
 'waydroid-display/runtime-commands.js':'ee996afa973c013b274324e4859d45e258000a206610a50fa491482433f5b179',
 'waydroid-display/host-adapter.js':'6b9f2518b8549ba9528dcb8d3cbe13a754aafda053742d2e62cd4d088ed2cac8',
 'waydroid-display/frame-check.js':'9347f9e5c6e496ffe566a588bf2913aafc42e0ee00284033fdfc73883faee5f6',
 'waydroid-display/runtime-supervisor.js':'8b8010b779e26d9ce9c04d15535382ff5264e109ddbf8c28df14ca89ea6a2a4e',
 'waydroid-display/gps-store.js':'11fafc097240f178a402d2138a2cfc5308b0465a9623796f01bd9f9330785bbb',
 'waydroid-display/compositor-source.js':'5d86e9fb72c64ef844b8318ac0f39d1900b989d28af41be1483c89b691e1b64b',
 'waydroid-display/touch-adapter.js':'46c4e56bfa0987f9366b82bb8080c11ee12a0d065f93ad2cb3aa0ba12a010bba',
 'waydroid-display/touch-geometry.js':'3f926b7995a424daae1c42fd38290387c86ac253be76c316f8438960a7d0bff1',
 'waydroid-display/session-probe.js':'8b362cb4270d560bc118e1613c439f15e9347e8a426a83f1e5e8ca5d39a970bf',
 'waydroid-display/graphics-restart.js':'5b96c99fcd7076b0fe8610151466a27e8877aa5ff39570956d0ef84a10f7d9cf',
 'waydroid-display/session-lifecycle.js':'06ca7397ff135572f3a3996ecc3bde9c26c67a543f298f43a8999f5461877028',
 'waydroid-display/probe.js':'93143ed63e92c2308063cbc57a3894706c8a576ee0dd91d0f381a72a2120bfe7',
}


# Reviewed against the independently supplied fixed-file .90 report. Six exact
# Git blobs, fourteen deterministic LF->CRLF transports, and the encoder's exact
# legacy-quality-rc18.py transform. These are independent SOURCE references;
# runtime code NEVER reads that machine report or learns hashes from a host.
CPU_SOURCE_BLOBS = {
 'preview-gateway/capture-lease.js':'5568dd30d53b411766c09da266fc2d5a5a941537',
 'preview-gateway/compositor-h264.py':'72cd9fda43dca36377a3763d2f47f18ff3af12fb',
 'preview-gateway/compositor-video-launcher.js':'a677e791faa9ec8258df13cdb23380b9235ccc5c',
 'preview-gateway/package.json':'7bf2a971b5c660859ab087862267ba0fc20ffe0e',
 'preview-gateway/video-capture-controller.js':'db9f40de17d3b5b17b1776890c7079eb48c1c3d5',
 'preview-gateway/video-settings.js':'6ff64a850bac40b841ac1e204050a4a6a6562f32',
 'waydroid-display/compositor-source.js':'3d3a27da9963c01a3ba65d9d4dfa15125275bd6a',
 'waydroid-display/frame-check.js':'c25cef0e8dcfe4351466101dcf1583dfa723dbc3',
 'waydroid-display/gps-store.js':'6baf6ac7d99dcd48efa33ed14081ea82bff44772',
 'waydroid-display/graphics-restart.js':'593c864e44159127749fd35cdd5a9c6ed0d1e1e6',
 'waydroid-display/host-adapter.js':'2673e0244bd2dc0cdbdf8cca4ca794fb54d0417f',
 'waydroid-display/package.json':'3b3b973efb9a660a2154aae4294a81ccd93d65c7',
 'waydroid-display/probe.js':'0c5c6a5ab7c04cb8c65d91118a0141c986801285',
 'waydroid-display/runtime-commands.js':'ca7ee7ae25af133a970ee227e04c58efe8a76700',
 'waydroid-display/runtime-native.js':'043aaae70f9767373f1329524af14fc58607e414',
 'waydroid-display/runtime-preflight.js':'5fdf35bcf1017b3d43195b23ad3c384757fbff36',
 'waydroid-display/runtime-supervisor.js':'0f2cb24293f48fd87211b7436865ed244788c434',
 'waydroid-display/session-lifecycle.js':'5963c8ba2aa34c11380576d495803db9f6a3b74a',
 'waydroid-display/session-probe.js':'2686c16a7a1c868e4855fbe8fcb6bfa74d4a643b',
 'waydroid-display/touch-adapter.js':'f04a596c4f942285ff8f5fc03b1d031971b1e1de',
 'waydroid-display/touch-geometry.js':'0d604aa4e78b09296af34ce6f3d6c79151dbb726',
}
CPU_CRLF_FILES = frozenset((
 'preview-gateway/capture-lease.js',
 'preview-gateway/package.json',
 'preview-gateway/video-capture-controller.js',
 'waydroid-display/frame-check.js',
 'waydroid-display/gps-store.js',
 'waydroid-display/graphics-restart.js',
 'waydroid-display/package.json',
 'waydroid-display/probe.js',
 'waydroid-display/runtime-preflight.js',
 'waydroid-display/runtime-supervisor.js',
 'waydroid-display/session-lifecycle.js',
 'waydroid-display/session-probe.js',
 'waydroid-display/touch-adapter.js',
 'waydroid-display/touch-geometry.js',
))
REVIEWED_CPU_PINS = {
 'preview-gateway/capture-lease.js':'7f4ec463c50a94d917767d88c75d71d3ce5c5a9d6524c6d9e19dfa7f2937429c',
 'preview-gateway/compositor-h264.py':'d831a9aa9a34a66820aef35473c2f6e0b357df3719a940a0d2ae16ab966523ad',
 'preview-gateway/compositor-video-launcher.js':'0b1d6f08c0ac6afbe85781c23238fbd57db4dc0ff70230cf41ff132c211edc86',
 'preview-gateway/package.json':'8fcaef9f5a9e9a0391b04a831aae75269f495a9eac662268d591891081a1af3f',
 'preview-gateway/video-capture-controller.js':'1e53815c938612ade904088683830cc96bc2c609c743e320a7cc8b6ed073de32',
 'preview-gateway/video-settings.js':'254475cc28534cf8e57135e40e26db146b5a7417b9dc11c9c44c40f5bbb2d4d3',
 'waydroid-display/compositor-source.js':'5d86e9fb72c64ef844b8318ac0f39d1900b989d28af41be1483c89b691e1b64b',
 'waydroid-display/frame-check.js':'46505c1e6f24bfdbc5be29be8b00cf61a81c0a652b8ad8c18b548af76a5e6963',
 'waydroid-display/gps-store.js':'a6d8f9cde98f02ffbcb4287c2994a7946c2f2d80235930ecca7f5724232c1840',
 'waydroid-display/graphics-restart.js':'984d488970ff48e6a3ba510fd00d9e7fce476da567dfd30424b67eb91a3f5997',
 'waydroid-display/host-adapter.js':'6b9f2518b8549ba9528dcb8d3cbe13a754aafda053742d2e62cd4d088ed2cac8',
 'waydroid-display/package.json':'411ec4535eb1eda92b0218d735b02aaca8210adb15936775bfdd5b0334fc3337',
 'waydroid-display/probe.js':'2969f41f0b932a23ae16b1cf9323f042a4b4c14488e014b11d8a71a230e950c9',
 'waydroid-display/runtime-commands.js':'ee996afa973c013b274324e4859d45e258000a206610a50fa491482433f5b179',
 'waydroid-display/runtime-native.js':'54f0a0661ec4b8d2bdb8b033b16782fa1cce0d5728c5b8e76532a8a3e222bd12',
 'waydroid-display/runtime-preflight.js':'16440e408a44eae131f1f68d3459437dea055f1acae15bd6b2cf04201ebae0a5',
 'waydroid-display/runtime-supervisor.js':'29b7dda4bb664889a129d1e500321ee5678d462d380aeaf9c1c9f729d02163cb',
 'waydroid-display/session-lifecycle.js':'5fc56208fb83200ecade9935a5ae07f77d2f970c00d886f3885919f4bb9c4a48',
 'waydroid-display/session-probe.js':'c5a039a5a97ab2b875a49fa470264fd88a77ffcbb4a72b92c4d0fcae4dd9a233',
 'waydroid-display/touch-adapter.js':'54022b723aa9a6fb0f9918e84b3e34c4cb7e12ea78439c5d94ba1faa26a645d4',
 'waydroid-display/touch-geometry.js':'fe9bfa482ce8b8fa275fb50a297e340c77c2ca6b2cd6257a5e0c3b49ebade9a0',
}
KNOWN_BASELINE = (
 'preview-gateway/compositor-video-launcher.js', 'preview-gateway/compositor-h264.py',
 'preview-gateway/video-settings.js', 'waydroid-display/runtime-native.js',
 'waydroid-display/runtime-commands.js', 'waydroid-display/host-adapter.js',
 'waydroid-display/compositor-source.js',
)


def reviewed_cpu_pins():
    pins = dict(REVIEWED_CPU_PINS)
    if (not set(HISTORICAL_CPU_PINS).issubset(pins) or not 21 <= len(pins) <= 128
            or any(type(n) is not str or not re.fullmatch(r'(?:preview-gateway|waydroid-display)/[A-Za-z0-9_/-]+\.(?:js|json|py)', n)
                   or '..' in n.split('/') or type(v) is not str or not re.fullmatch('[a-f0-9]{64}', v)
                   for n, v in pins.items())
            or any(pins.get(n) != HISTORICAL_CPU_PINS[n] for n in KNOWN_BASELINE)):
        raise ValueError('Independently measured and source-reviewed legacy CPU profile is not enrolled')
    return pins



def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def video_unit(ctx, bridge):
    """Override argv only. The original CPU parent MUST keep its host namespace."""
    return ('[Service]\nExecStart=\nExecStart=' + ' '.join(bridge.parent_command(ctx)) + '\n').encode('ascii')


def video_mapping(ctx, engine, journal, bridge):
    if engine.TARGETS.get('video') != VIDEO:
        raise ValueError('Coordinator must register the exact owned video target')
    return engine.OwnedMapping(journal, 'video', video_unit(ctx, bridge))


def dependency_pins(material):
    """Use the immutable package solely as pngjs reference, never as CPU authority."""
    raw = material.assets['capturePackage']
    if type(raw) is not bytes or len(raw) != 3246080 or sha(raw) != PACKAGE:
        raise ValueError('Original immutable runtime package required')
    cpu = reviewed_cpu_pins()
    files = material.compiler.capture_runtime(raw)
    package = {n: b for n, b in files.items() if n.startswith(PNG)}
    if not 5 <= len(package) <= 100 or PNG + 'package.json' not in package or PNG + 'lib/png.js' not in package:
        raise ValueError('Complete original pngjs reference required')
    return {**cpu, **{n: sha(b) for n, b in package.items()}}


class Physical:
    """Hold all original application/import generations through launch/fallback."""
    def __init__(self, observer, pins, stack):
        self.p, self.pins = observer, dict(pins)
        self.held = observer.Held(stack)
        self.records = {}
        for name, pin in sorted(self.pins.items()):
            path = PHYSICAL / name
            mode = stat.S_IMODE(path.lstat().st_mode)
            if mode not in (0o444, 0o644):
                raise ValueError('Protected original legacy dependency mode required')
            raw = self.held.read(path, 8 * 1024**2, mode)
            if sha(raw) != pin:
                raise ValueError('Original legacy dependency reference differs: ' + name)
            self.records[name] = {'sha256': pin, 'generation': list(self.held.files[path][1])}
        self.namespace()
        self.directories = {p.relative_to(PHYSICAL).as_posix(): list(identity)
                            for p, (_, identity) in self.held.dirs.items()
                            if p == PHYSICAL or PHYSICAL in p.parents}
        self.check()

    def namespace(self):
        # Node tries an exact extensionless path before adding .js.
        for name in self.pins:
            if name.endswith('.js') and os.path.lexists(PHYSICAL / name[:-3]):
                raise ValueError('Legacy extensionless Node dependency shadow preserved')
        # An extensionless Node require prefers pngjs.js/json/node to its directory.
        # Package scopes above the two pinned application manifests are irrelevant
        # to those files, but a physical runtime package must never change scopes.
        for suffix in ('.js', '.json', '.node'):
            if os.path.lexists(PHYSICAL / (PNG[:-1] + suffix)):
                raise ValueError('Legacy pngjs resolution shadow preserved')
        expected = {n[len(PNG):] for n in self.pins if n.startswith(PNG)}
        actual = set()
        for current, children, names in os.walk(PHYSICAL / PNG, followlinks=False):
            path = Path(current)
            self.held.directory(path)
            for child in children:
                self.held.directory(path / child)
            actual.update((path / n).relative_to(PHYSICAL / PNG).as_posix() for n in names)
        if actual != expected:
            raise ValueError('Closed physical pngjs dependency tree required')
        # Refuse ALL application-local importable Python modules/packages,
        # including indirect stdlib imports (enum, warnings, _socket, ...).
        # Existing hyphenated capture/test scripts cannot shadow an import.
        with os.scandir(PHYSICAL / 'preview-gateway') as entries:
            for entry in entries:
                stem = entry.name.split('.', 1)[0]
                loadable = entry.name.endswith(('.py', '.pyc', '.so')) or entry.is_dir(follow_symlinks=False) or entry.is_symlink()
                if loadable and stem.isidentifier() and entry.name not in ('node_modules', '__pycache__'):
                    raise ValueError('Legacy Python import shadow preserved')

    def check(self):
        self.p.require_no_portable()
        if os.path.lexists('/etc/droiddeck/capture-policy.json'):
            raise ValueError('Legacy portable policy conflict preserved')
        self.held.verify()
        self.namespace()
        self.held.verify()
        return True

    def baseline(self, digest):
        self.check()
        return {'schema': 1, 'kind': 'droidmate-original-legacy-dependencies',
                'packageSha256': digest, 'layout': 'legacy', 'physicalRoot': PHYSICAL.as_posix(),
                'cpuProfileSha256': sha(json.dumps({n: v for n, v in self.pins.items() if not n.startswith(PNG)},
                    sort_keys=True, separators=(',', ':')).encode('ascii')),
                'pngjsReferencePackageSha256': PACKAGE,
                'files': self.records, 'directories': self.directories}


def journal_identity(ctx, engine, journal):
    """Validate the durable identity, not a caller-supplied True callback."""
    journal.guard()
    intent = journal.read('intent.json')
    expected = {'packageSha256': ctx['release']['packageSha256'], 'layout': 'legacy'}
    if (journal.root != ROOT / expected['packageSha256'] or intent != journal.intent
            or intent.get('identity') != expected
            or intent.get('rootIdentity') != list(engine.directory_id(os.fstat(journal.fd)))):
        raise ValueError('Original physical legacy transaction identity required')
    journal.state()
    return True


def establish(ctx, engine, journal, material, compiler):
    """Called once under the coordinator journal lock BEFORE intent/effects.

    Existing baselines are verification-only. Full original preflight belongs
    only to creation: the enrolled Python video parent is a different argv.
    """
    bridge = compiler.source_module(ctx, 'tools/aggregate-legacy-video.py')
    ctx = bridge.retain_context(ctx)
    journal_identity(ctx, engine, journal)
    if engine.TARGETS.get('video') != VIDEO:
        raise ValueError('Owned video journal grammar/target integration required')
    with ExitStack() as stack:
        observer = bridge.helper(ctx, 'legacy-gpu-preflight.py')
        physical = Physical(observer, dependency_pins(material), stack)
        expected = physical.baseline(ctx['release']['packageSha256'])
        if os.path.lexists(journal.root / 'baseline.json'):
            if journal.read('baseline.json') != expected:
                raise ValueError('Original dependency baseline changed; preserve history')
        else:
            if journal.state() != 'new':
                raise ValueError('Missing legacy baseline cannot be recreated after intent')
            observer.inspect()  # Actual original process/source/native-lease observation.
            physical.check()
            if ctx['check']() is not True:
                raise ValueError('Source lost before durable legacy baseline')
            journal.write('baseline.json', expected)
        physical.check()
        if journal.read('baseline.json') != expected or ctx['check']() is not True:
            raise ValueError('Original durable legacy baseline required')
    return True


def public_reader(engine, digest, files):
    # Acquire the existing tree through the qualified verifier, then relinquish
    # ONLY our flock. Keep every FD/generation: a video lifetime must not hold
    # the staging writer lock needed by coordinator health/recovery.
    import fcntl
    tree = engine.PublicTree(PUBLIC / digest, dict(files), digest, create=False)
    try:
        fcntl.flock(tree.held['coordination.lock'][0], fcntl.LOCK_UN)
        tree.verify()
        return tree
    except BaseException:
        tree.close()
        raise


class Admission:
    """Independent read-only guard passed to the already-qualified RB parent."""
    def __init__(self, ctx, engine, journal, material, compiler):
        self.bridge = compiler.source_module(ctx, 'tools/aggregate-legacy-video.py')
        self.ctx = self.bridge.retain_context(ctx)
        self.c, self.j = engine, journal
        self.stack, self.closed = ExitStack(), False
        try:
            journal_identity(self.ctx, engine, journal)
            self.p = self.bridge.helper(self.ctx, 'legacy-gpu-preflight.py')
            self.physical = Physical(self.p, dependency_pins(material), self.stack)
            self.baseline = self.physical.baseline(self.ctx['release']['packageSha256'])
            self.view = compiler.browser_material(self.ctx, 'legacy')
            if self.view['runtimeDigest'] != self.ctx['release']['packageSha256']:
                raise ValueError('Original legacy public runtime binding required')
            self.video = video_mapping(self.ctx, engine, journal, self.bridge)
            self.gateway = engine.OwnedMapping(journal, 'gateway', self.view['mapping'])
            self.public_views = [self.stack.enter_context(public_reader(engine, digest, files))
                for digest, files in ((self.view['runtimeDigest'], self.view['runtimeFiles']),
                                      (self.view['frontendDigest'], self.view['frontendFiles']))]
            self.check()
        except BaseException:
            self.close()
            raise

    def local(self):
        if self.closed or self.ctx['check']() is not True:
            raise ValueError('Original signed legacy admission lost')
        journal_identity(self.ctx, self.c, self.j)
        # The parent starts video while the transaction is at loaded. No all-peer
        # health gate here: video legitimately precedes gateway/relay startup.
        if self.j.state() not in ('loaded', 'started', 'verified', 'accepted'):
            raise ValueError('Legacy video requires an active mapped installation')
        if self.j.read('baseline.json') != self.baseline:
            raise ValueError('Original physical dependency baseline required')
        self.physical.check()
        if self.video.verify() is not True or self.gateway.verify() is not True:
            raise ValueError('Live owned video AND gateway mappings required')
        if self.ctx['check']() is not True:
            raise ValueError('Source changed after legacy native/mapping admission')
        return True

    def check(self):
        self.local()
        for tree in self.public_views:
            tree.verify()
        # Catch dependency/journal/mapping loss during the LAST public-view check,
        # then catch either public tree changing during that final local check.
        self.local()
        for tree in self.public_views:
            tree.guard()
        return True

    def close(self):
        if not self.closed:
            self.closed = True
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def read_journal(ctx, engine):
    """Open immutable records WITHOUT taking the coordinator's writer lock.

    Video starts while that lock is held by the supervised installer. Holding
    another exclusive lock here would deadlock start/health/Undo. Every immutable
    record, ancestry and hash-linked phase is still checked by the engine.
    """
    j = engine.Journal.__new__(engine.Journal)
    j.root = ROOT / ctx['release']['packageSha256']
    j.dirs, j.files, j.closed, j.fd = {}, {}, False, None
    try:
        j.fd = j.directory(j.root)
        if stat.S_IMODE(os.fstat(j.fd).st_mode) != 0o700:
            raise ValueError('Private original legacy journal required')
        j.intent = j.read('intent.json')
        # Reuse the engine's complete intent/token schema without a lock or
        # enrollment path. No record is written or missing path constructed.
        value = j.intent
        if (type(value) is not dict or set(value) != {'schema', 'identity', 'rootIdentity', 'transactionToken'}
                or type(value['schema']) is not int or value['schema'] != 1
                or type(value['transactionToken']) is not str
                or not re.fullmatch('[a-f0-9]{32}', value['transactionToken'])):
            raise ValueError('Closed original legacy journal intent required')
        journal_identity(ctx, engine, j)
        return j
    except BaseException:
        j.close()
        raise


def run(ctx, engine, material, compiler):
    """Enrolled-only legacy-video dispatch. No creation, repair or service effects."""
    if os.name != 'posix' or os.geteuid() != 0 or 'DBUS_SYSTEM_BUS_ADDRESS' in os.environ:
        raise ValueError('Root unredirected Linux legacy entry required')
    bridge = compiler.source_module(ctx, 'tools/aggregate-legacy-video.py')
    ctx = bridge.retain_context(ctx)
    with read_journal(ctx, engine) as journal:
        with Admission(ctx, engine, journal, material, compiler) as guard:
            guard.check()
            return guard.bridge.run(guard.ctx, guard.check)
