# SPDX-License-Identifier: GPL-3.0-only
"""Original-owned fresh GPU transactions; no replay, adoption or healthy expiry."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import socket
import stat
import sys
import time


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G = load('gpu-activation')
ROOT = Path('/var/lib/droiddeck/gpu-recovery')
ADMISSIONS = Path('/var/lib/droiddeck/gpu-admissions')
TRANSACTIONS = Path('/var/lib/droiddeck/gpu-transactions')
SESSION_RECOVERY = 'droiddeck-session-recovery.service'


def session_recovery_token():
    owner = G.unit(SESSION_RECOVERY)
    if (owner['ActiveState'] != 'activating' or str(os.getpid()) not in
            (owner['MainPID'], owner['ControlPID'])):
        raise ValueError('Supervised session recovery main process required')
    raw = G.command(['/usr/bin/systemctl', 'list-units', '--all', '--output=json', '--no-pager',
                     '--state=active', 'droiddeck-fresh-gpu-watch@*.service']).stdout
    if len(raw) > 16384:
        raise ValueError('Bounded unique active GPU watcher required')
    rows = json.loads(raw)
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise ValueError('Exactly one active original GPU watcher required')
    name = rows[0].get('unit')
    match = re.fullmatch(r'droiddeck-fresh-gpu-watch@([a-f0-9]{12})\.service', name or '')
    if not match or rows[0].get('active') != 'active' or rows[0].get('sub') != 'running':
        raise ValueError('Original running watcher identity required')
    return match[1]


def configure(token):
    if type(token) is not str or not re.fullmatch('[0-9a-f]{12}', token):
        raise ValueError('Exact admitted fresh transaction identifier required')
    G.ROOT = ROOT
    G.STATE = TRANSACTIONS / token
    G.WORKER = 'droiddeck-fresh-gpu-worker@' + token + '.service'
    G.WATCHER = 'droiddeck-fresh-gpu-watch@' + token + '.service'
    G.ON, G.OFF = 'pending-droiddeck-layer-on-' + token, 'pending-droiddeck-layer-off-' + token
    G.CFG_ON, G.CFG_OFF = 'pending-droiddeck-gpu-on-' + token + '.cfg', 'pending-droiddeck-gpu-off-' + token + '.cfg'
    G.SCOPE = 'fresh-' + token
    G.ALLOC = 'mesa-allocator-' + G.SCOPE
    G.READY, G.RESTORED = 'pending-droiddeck-allocator-' + G.SCOPE + '-ready', 'pending-droiddeck-allocator-' + G.SCOPE + '-restored'
    # The allocator primitive binds a scope to both its retained marker and receipt.
    return token


class Fresh(G.Activation):
    SESSION_START_TIMEOUT = 210

    def __init__(self, p, admission):
        super().__init__(p)
        self.admission = admission
        self.pins = admission['metadataSha256'], admission['guestReceiptSha256']
        self.config = admission['configuration']

    def recover(self):
        super().recover()
        if G.unit(G.WATCHER)['MainPID'] == str(os.getpid()):
            return load('cpu-recovery').Recovery(self, G).watch()

    def watch(self, policy):
        if self.store.status()['stage'] == 'restored':
            return load('cpu-recovery').Recovery(self, G).watch()
        return super().watch(policy)

    def resume_boot(self):
        with self.store.lock():
            state = self.store.status()
            if state['boot'] == G.boot_id() or state['stage'] not in ('ready', 'starting', 'verifying-native'):
                return
            # These stages follow completed profile changes. A reboot can interrupt
            # service startup, but must never replay activation or resume a mutator.
            self.metadata()
            G.require_quiet(G.unit(G.WORKER))
            if G.sha(G.protected_read(G.WORK / 'waydroid.cfg', 0o644)) != G.GPU:
                raise ValueError('Original installed GPU profile required for boot resume')
            self.store.stage('starting')
        G.command(['/usr/bin/systemctl', 'start', G.UNITS[3], G.UNITS[2], G.UNITS[1]], timeout=self.SESSION_START_TIMEOUT)
        deadline = G.time.monotonic() + 150
        while G.time.monotonic() < deadline:
            try:
                healthy = self.native()
            except (OSError, ValueError, G.subprocess.SubprocessError):
                healthy = False
            if healthy:
                self.store.stage('ready')
                G.command(['/usr/bin/systemctl', 'start', '--no-block', G.UNITS[0]])
                return
            G.time.sleep(5)
        self.store.stage('recovery-required')

    def restart_session(self):
        # A container-only restart can crash its retained Weston compositor and
        # restart the session a second time against a live Waydroid daemon.
        # Keep the independent watcher armed, with a bounded starting phase.
        with self.store.lock():
            state = self.store.status()
            if state['stage'] != 'ready' or state['boot'] != G.boot_id():
                raise ValueError('Current accepted GPU transaction required')
            watcher = G.unit(G.WATCHER)
            if watcher['ActiveState'] != 'active' or int(watcher['MainPID']) < 2:
                raise ValueError('Independent recovery must remain armed')
            G.require_quiet(G.unit(G.WORKER))
            self.metadata()
            self.store.stage('starting')
            try:
                G.command(['/usr/bin/systemctl', 'stop', *G.UNITS], timeout=100)
                for name in G.UNITS:
                    G.require_quiet(G.unit(name))
                failed = [name for name in G.UNITS if G.unit(name)['ActiveState'] == 'failed']
                if failed:
                    G.command(['/usr/bin/systemctl', 'reset-failed', *failed], timeout=15)
                G.command(['/usr/bin/systemctl', 'start', G.UNITS[3]], timeout=45)
                G.command(['/usr/bin/systemctl', 'start', G.UNITS[2], G.UNITS[1]], timeout=self.SESSION_START_TIMEOUT)
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if self.native():
                        self.store.stage('ready')
                        G.command(['/usr/bin/systemctl', 'start', '--no-block', G.UNITS[0]])
                        print('Owned display stack recovered; native GPU verified', flush=True)
                        return
                    time.sleep(2)
                raise ValueError('Recovered native GPU readiness deadline')
            except Exception:
                self.store.stage('recovery-required')
                raise

    def metadata(self):
        raw = G.protected_read(Path('/etc/droiddeck/runtime.json'), 0o644, 16384)
        account = pwd.getpwnam(self.config['user'])
        if (G.sha(raw) != self.pins[0] or json.loads(raw) != self.config
                or (account.pw_uid, account.pw_gid, account.pw_dir) !=
                tuple(self.config[k] for k in ('uid', 'gid', 'home'))):
            raise ValueError('Original admitted metadata and existing account required')

    def profiles(self):
        self.metadata()
        original = G.protected_read(G.PROFILE / 'original.cfg')
        if G.sha(original) != self.p.WAYDROID_CONFIGURATION_SHA256:
            raise ValueError('Original profile source changed')
        result = tuple(self.p.profile_with_detected_protocols(self.p.profile_candidate(original, gpu=gpu)) for gpu in (False, True))
        if tuple(map(G.sha, result)) != (G.CPU, G.GPU):
            raise ValueError('Reviewed GPU/CPU profile pair changed')
        self.p._verify_profile_codecs(self.c)
        return result

    def offline(self, stack):
        self.p._assert_codec_stopped()
        self.metadata()
        data = Path(self.config['home']) / '.local/share/waydroid/data'
        paths = (data, data / 'property', data / 'local/tmp' / self.p.GUEST_TARGET)
        chains = [self.c.open_chain(stack, path) for path in paths]
        receipt_fd, receipt_info, raw = self.p._read_profile_file(self.c, stack, chains[2][-1][1], 'droiddeck-guest-receipt.json', maximum=65536)
        if G.sha(raw) != self.pins[1]:
            raise ValueError('Original admitted guest receipt changed')

        def check():
            self.p._assert_codec_stopped()
            self.metadata()
            for chain in chains:
                self.c.check_chain(chain)
            info = os.fstat(chains[0][-1][1])
            if ((info.st_dev, info.st_ino) != tuple(self.admission[k] for k in ('dataDevice', 'dataInode'))
                    or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (self.config['uid'], self.config['gid'], 0o771)):
                raise ValueError('Original Android data directory required')
            for path, descriptor, _device, _inode in chains[1][:-1]:
                ancestor = os.fstat(descriptor)
                if ancestor.st_uid not in (0, self.config['uid']) or (path != data and ancestor.st_mode & 0o022):
                    raise ValueError('Unsafe property ancestor')
            for chain in chains[1:]:
                self.c.protected(os.fstat(chain[-1][1]), 0o700)
            self.c.check_image(chains[2][-1][1], 'droiddeck-guest-receipt.json', receipt_fd, receipt_info, private=True)
            if self.c.digest_fd(receipt_fd, len(raw)) != self.pins[1]:
                raise ValueError('Original guest receipt bytes changed')
        check()
        return {'property': paths[1], 'check': check,
            **{key: self.admission[key] for key in ('dataDevice', 'dataInode')}}

def admit(args):
    C = load('runtime-compat-install')
    R = C.R
    if len(args) != 6 or args[-1] != '-':
        raise ValueError('Original installation sources and metadata required')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        authority = R.K.hold_final_install_layout(stack, args[0], args[1], args[2], raw)
        held = R.A._hold_pinned_tree(stack, args[3], owner=R.A._account(args[0])[1:3], records_reader=C.B.records, manifest_limit=65536)
        C.install(authority, held, args[4], mode='--verify')
        R._inactive()
        p, _policy = G.sources()
        copier = p.load_copier()
        config_raw = authority['read_file']('/etc/droiddeck/runtime.json')
        config = json.loads(config_raw)
        data = Path(config['home']) / '.local/share/waydroid/data'
        data_chain = copier.open_chain(stack, data)
        guest = copier.open_chain(stack, data / 'local/tmp' / p.GUEST_TARGET)
        _fd, _info, receipt_raw = p._read_profile_file(copier, stack, guest[-1][1], 'droiddeck-guest-receipt.json', maximum=65536)
        receipt = json.loads(receipt_raw)
        selected = p._guest_sources(stack, copier)
        records = [{'path': name, 'size': value[4][0], 'sha256': value[4][1], 'mode': value[5]} for name, value in sorted(selected.items())]
        info = os.fstat(data_chain[-1][1])
        if (receipt.get('kind') != 'protected-guest-payloads' or receipt.get('version') != 1 or receipt.get('files') != records
                or (receipt.get('dataDevice'), receipt.get('dataInode')) != (info.st_dev, info.st_ino)):
            raise ValueError('Original independently reproduced guest payload receipt required')
        dirs = R._Directories(stack)
        for path in (ADMISSIONS, TRANSACTIONS):
            parent = dirs.open(path.parent)
            try:
                os.mkdir(path.name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            if stat.S_IMODE(os.fstat(dirs.open(path)).st_mode) != 0o700:
                raise ValueError('Private GPU transaction namespace required')
        token = configure(secrets.token_hex(6))
        value = {'version': 1, 'kind': 'original-owned-fresh-gpu', 'transaction': token,
            'configuration': config, 'metadataSha256': G.sha(config_raw), 'guestReceiptSha256': G.sha(receipt_raw),
            'dataDevice': info.st_dev, 'dataInode': info.st_ino}
        C.B.write_new(dirs.open(ADMISSIONS), token + '.json', R._encode(value), 0o600)
        Fresh(p, value).prepare()
        print(json.dumps({'transaction': token, 'kind': 'fresh-gpu-admitted', 'servicesActivated': False}))


def notify():
    address = os.environ.get('NOTIFY_SOCKET', '')
    if not address or address[0] not in ('/', '@'):
        raise ValueError('Systemd readiness channel required')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
        connection.connect('\0' + address[1:] if address.startswith('@') else address)
        connection.sendall(b'READY=1')


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0 or Path(__file__) != ROOT / 'runtime/portable/fresh-gpu.py':
        raise ValueError('Fixed protected fresh GPU installation required')
    G.ROOT = ROOT
    if args[:1] == ['--admit']:
        return admit(args[1:])
    if args == ['--restart-session']:
        args = [args[0], session_recovery_token()]
    elif len(args) != 2 or args[0] not in ('--activate', '--watch', '--recover', '--inspect'):
        raise ValueError('Exact fresh GPU lifecycle operation required')
    token = configure(args[1])
    admission = json.loads(G.protected_read(ADMISSIONS / (token + '.json')))
    if (set(admission) != {'version', 'kind', 'transaction', 'configuration', 'metadataSha256', 'guestReceiptSha256', 'dataDevice', 'dataInode'}
            or admission['version'] != 1 or admission['kind'] != 'original-owned-fresh-gpu' or admission['transaction'] != token):
        raise ValueError('Original immutable GPU admission required')
    p, policy = G.sources()
    activation = Fresh(p, admission)
    activation.metadata()
    if args[0] == '--restart-session':
        return activation.restart_session()
    if args[0] == '--watch':
        notify()
        activation.resume_boot()
        return activation.watch(policy)
    if args[0] == '--inspect':
        print(json.dumps({'status': activation.store.status(), 'nativeGpuVerified': activation.native()}))
        return
    getattr(activation, args[0][2:])()


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Fresh GPU lifecycle incomplete: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
