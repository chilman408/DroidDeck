# SPDX-License-Identifier: GPL-3.0-only
"""Root-owned, independent bridge from Settings to the signed pilot updater.

Only the fixed public repository is consulted. Downloaded launchers are never
executed until the independently retained verifier authenticates source/payload.
The original controller owns all code exchanges, health and recovery decisions.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager

ROOT = Path('/var/lib/droiddeck/update-ui')
SOCKET = Path('/run/droiddeck-update-ui/control.sock')
CONTROLLER = Path('/var/lib/droiddeck/gateway-update-pilot/sources/runtime/portable/gateway-update-release.py')
ENROLLMENT = Path('/var/lib/droiddeck/gateway-update-pilot-enrollment/complete.json')
UNIT = 'droiddeck-update-ui.service'
API = 'https://api.github.com/repos/chilman408/DroidDeck/releases?per_page=30'
DOWNLOAD = 'https://github.com/chilman408/DroidDeck/releases/download/'
HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}
BUSY = {'queued', 'downloading', 'verifying', 'installing', 'rolling-back'}
PHASES = BUSY | {'complete', 'failed', 'interrupted'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


E = load('update_ui_verifier', Path(__file__).parents[2] / 'tools/update-installer-entry.py')


def version(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:-rc\.([1-9]\d*))?', value) if type(value) is str else None
    if not match or any(len(x) > 8 for x in match.groups() if x): raise ValueError('Canonical version required')
    return tuple(int(x) for x in match.groups()[:3]) + (int(match[4]) if match[4] else 10**9,)


def checked_url(url):
    value = urllib.parse.urlsplit(url)
    if (value.scheme != 'https' or value.hostname not in HOSTS or value.port not in (None, 443)
            or value.username or value.password or value.fragment):
        raise ValueError('Official HTTPS release origin required')
    return url


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, checked_url(newurl))


def download(url, maximum):
    request = urllib.request.Request(checked_url(url), headers={'User-Agent': 'DroidDeck-Pilot-Updater/1', 'Accept': 'application/vnd.github+json' if url == API else 'application/octet-stream'})
    # Ignore host/user proxy environment for this fixed public release channel.
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirect()).open(request, timeout=25) as response:
        if response.status != 200: raise ValueError('Complete official release required')
        chunks = []; total = 0; deadline = time.monotonic() + 90
        while True:
            chunk = response.read(min(65536, maximum + 1 - total))
            if not chunk: break
            chunks.append(chunk); total += len(chunk)
            if total > maximum or time.monotonic() > deadline: raise ValueError('Release download bound exceeded')
        return b''.join(chunks)


def select_offer(raw, installed):
    releases = json.loads(raw)
    if type(releases) is not list or len(releases) > 30: raise ValueError('Bounded public catalog required')
    offers = []
    for release in releases:
        if type(release) is not dict or release.get('draft') is not False: continue
        tag = release.get('tag_name', '')
        try: target = tag[1:]; target_version = version(target)
        except (ValueError, TypeError): continue
        if tag != 'v' + target or target_version <= version(installed): continue
        filename = 'droiddeck-' + target + '-update.pyz'
        assets = release.get('assets', [])
        if type(assets) is not list or len(assets) > 30: continue
        matches = [a for a in assets if type(a) is dict and a.get('name') == filename]
        if len(matches) != 1: continue
        asset = matches[0]; digest = asset.get('digest', '')
        url = DOWNLOAD + tag + '/' + filename
        if (asset.get('state') != 'uploaded' or asset.get('browser_download_url') != url
                or type(asset.get('size')) is not int or not 1 <= asset['size'] <= 32 * 1024 * 1024
                or type(digest) is not str or not re.fullmatch('sha256:[a-f0-9]{64}', digest) or type(release.get('id')) is not int): continue
        offers.append({'version': target, 'url': url, 'sha256': digest[7:], 'size': asset['size'], 'releaseId': release['id']})
    return max(offers, key=lambda x: version(x['version'])) if offers else None


def command(args, timeout=30):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd='/', env=ENV, timeout=timeout, check=True)


def protected(path, maximum=16384, mode=None):
    path = Path(path); fd = E.protected_directory(path.parent)
    try: child = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally: os.close(fd)
    try:
        before = os.fstat(child)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0 or before.st_mode & 0o022
                or before.st_nlink != 1 or before.st_size > maximum or mode is not None and stat.S_IMODE(before.st_mode) != mode):
            raise ValueError('Bounded root-owned record required')
        with os.fdopen(os.dup(child), 'rb') as stream: raw = stream.read(maximum + 1)
        if len(raw) != before.st_size or E.generation(before) != E.generation(os.fstat(child)) or E.generation(before) != E.generation(path.lstat()):
            raise ValueError('Protected record changed')
        return raw
    finally: os.close(child)


def read(name, default=None):
    try: return json.loads(protected(ROOT / name, 16384, 0o600))
    except FileNotFoundError: return default


def save(name, value):
    raw = E.encode(value); temporary = ROOT / ('.' + name + '-' + uuid.uuid4().hex)
    E.write(temporary, raw)
    os.replace(temporary, ROOT / name)
    fd = E.protected_directory(ROOT)
    try: os.fsync(fd)
    finally: os.close(fd)


@contextmanager
def locked(wait=False):
    import fcntl
    fd = os.open(ROOT / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        state = os.fstat(fd)
        if state.st_uid != 0 or not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or state.st_mode & 0o077:
            raise ValueError('Protected job lock required')
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield
    finally: os.close(fd)


def inspection():
    # Poll only small, original root-protected receipts. Full source/tree/GPU
    # authentication belongs to the original install/rollback controller and is
    # deliberately not repeated every few seconds by a browser status poll.
    history = CONTROLLER.parents[3] / 'history'
    roots = json.loads(protected(CONTROLLER.parents[3] / 'chain-roots.json'))
    fd = E.protected_directory(history)
    try:
        info = os.fstat(fd); names = sorted(os.listdir(fd))
        if roots['historyIdentity'] != [info.st_dev, info.st_ino] or len(names) > 32 or names != ['%04d.json' % n for n in range(1, len(names) + 1)]:
            raise ValueError('Original bounded consecutive history required')
    finally: os.close(fd)
    if not names: return {'current': None, 'phases': [], 'attempts': 0}
    row = json.loads(protected(history / names[-1], 65536, 0o600)); digest = row['digest']
    if not re.fullmatch('[a-f0-9]{64}', digest) or row['ordinal'] != len(names): raise ValueError('Original last release required')
    directory = Path('/var/lib/droiddeck/gateway-update-attempts') / digest
    fd = E.protected_directory(directory)
    try:
        info = os.fstat(fd)
        if row['transaction']['rootIdentity'] != [info.st_dev, info.st_ino]: raise ValueError('Original transaction directory required')
    finally: os.close(fd)
    intent = protected(directory / 'intent.json', 2 * 1024 * 1024, 0o600)
    if row['transaction']['intentGeneration'] != E.generation((directory / 'intent.json').lstat()):
        raise ValueError('Original history-bound transaction intent required')
    phases = []
    for phase in ('apply-intent', 'applied', 'healthy', 'rollback-request', 'rollback-intent', 'rolled-back', 'boot-code-restored'):
        try: raw = protected(directory / (phase + '.json'), 512, 0o600)
        except FileNotFoundError: continue
        if raw != E.encode({'schema': 1, 'intentSha256': hashlib.sha256(intent).hexdigest(), 'phase': phase}):
            raise ValueError('Original intent-bound phase marker required')
        phases.append(phase + '.json')
    return {'current': digest, 'phases': phases, 'attempts': len(names)}


def installed_version():
    try: return json.loads(protected('/opt/droiddeck/runtime/preview-gateway/release-version.json', 128))['version']
    except FileNotFoundError: return '0.1.0-rc.2'


def history_floor(state, installed):
    digest = state.get('current')
    if digest is None: return installed
    if not re.fullmatch('[a-f0-9]{64}', digest): raise ValueError('Original admitted digest required')
    # inspection() has independently authenticated this original admission.
    # Rolled-back targets remain consumed; never advertise replay as an update.
    statement = json.loads(protected(Path('/var/lib/droiddeck/update-admissions') / digest / 'statement.json', 8192, 0o600))
    target = statement['target']['version']
    if statement['bundleSha256'] != digest: raise ValueError('Original authenticated statement required')
    return max((installed, target), key=version)


def job_active():
    result = subprocess.run(['/usr/bin/systemctl', 'is-active', 'droiddeck-update-ui-job.service'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=5)
    return result.stdout.strip() in (b'active', b'activating', b'deactivating')


def snapshot():
    installed = installed_version(); version(installed)
    job = read('job.json')
    active = bool(job and job['phase'] in BUSY and job_active())
    # The original controller holds its own exclusive mutation fence while
    # applying. Progress must remain readable across that fence and gateway
    # restarts; defer its expensive read-only proof until the job has finished.
    state = {'phases': [], 'current': None} if active else inspection()
    phases = state.get('phases', [])
    if job and job['phase'] in BUSY | {'failed', 'interrupted'} and not active:
        # A crash/reboot is not success. The original controller remains the
        # sole recovery authority; never resume forward or clear its history.
        recovered = ('complete' if job['action'] == 'install' and job.get('digest') == state.get('current')
                     and 'healthy.json' in phases and 'rollback-request.json' not in phases and installed == job['target'] else
                     'complete' if job['action'] == 'rollback' and job['token'] == state.get('current') and 'rolled-back.json' in phases else
                     'failed' if job.get('digest') == state.get('current') and 'rolled-back.json' in phases else 'interrupted')
        job = {**job, 'phase': recovered}
    offer = read('offer.json')
    floor = max((installed, job['target']), key=version) if active else history_floor(state, installed)
    if offer and version(offer['version']) <= version(floor): offer = None
    return {'protocol': 1, 'installedVersion': installed,
            'available': None if not offer else {k: offer[k] for k in ('version', 'token')},
            'job': None if not job else {k: job[k] for k in ('id', 'action', 'phase', 'target')},
            'rollback': state.get('current') if 'healthy.json' in phases and 'rollback-request.json' not in phases and 'rolled-back.json' not in phases else None}


def dispatch(payload):
    if type(payload) is not dict or payload.get('action') not in ('check', 'install', 'rollback'): raise ValueError('Closed update action required')
    action = payload['action']
    if action == 'check':
        if set(payload) != {'action'}: raise ValueError('Closed check request required')
        with locked():
            if job_active(): raise ValueError('Update operation active')
            installed = installed_version()
            offer = select_offer(download(API, 2 * 1024 * 1024), history_floor(inspection(), installed))
            if offer: offer['token'] = uuid.uuid4().hex
            save('offer.json', offer)
        return snapshot()
    if set(payload) != {'action', 'token', 'requestId'} or not re.fullmatch('[a-f0-9]{32}', str(payload['requestId'])):
        raise ValueError('Closed correlated update request required')
    with locked():
        old = read('job.json')
        if old and old['id'] == payload['requestId']:
            if old['action'] != action or old['token'] != payload['token']: raise ValueError('Original request identity required')
            return snapshot()
        if job_active(): raise ValueError('Existing operation must be inspected, never replayed')
        if old and old['phase'] in BUSY:
            current = inspection()
            # Only a separately completed original controller permits a NEW
            # explicit request. Its rejected attempts remain non-replayable.
            if snapshot()['job']['phase'] in BUSY or not any(p in current['phases'] for p in ('healthy.json', 'rolled-back.json')) or (
                    'rollback-request.json' in current['phases'] and 'rolled-back.json' not in current['phases']):
                raise ValueError('Existing original recovery must finish first')
        if action == 'install':
            offer = read('offer.json')
            if not offer or payload['token'] != offer['token'] or version(offer['version']) <= version(history_floor(inspection(), installed_version())):
                raise ValueError('Exact checked newer release required')
            target = offer['version']
        else:
            current = snapshot()['rollback']
            if not current or payload['token'] != current: raise ValueError('Original healthy update required')
            offer = None; target = '0.1.0-rc.2'
        save('job.json', {'id': payload['requestId'], 'action': action, 'token': payload['token'], 'phase': 'queued', 'target': target, 'offer': offer})
        # Fixed unit/entrypoint only: no browser-provided command/path/URL.
        try:
            command(['/usr/bin/systemd-run', '--unit=droiddeck-update-ui-job', '--collect', '--property=Type=exec',
                     '--property=TimeoutStartSec=900', '/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), 'run-job', payload['requestId']])
        except Exception:
            job = read('job.json'); save('job.json', {**job, 'phase': 'failed'}); raise
    return snapshot()


def authenticate_file(path, offer):
    raw = protected(path, 32 * 1024 * 1024, 0o600)
    if len(raw) != offer['size'] or hashlib.sha256(raw).hexdigest() != offer['sha256']: raise ValueError('Exact official asset digest required')
    entries = E.package_entries(path)
    release, _manifest, files = E.authenticate(entries)
    if entries['__main__.py'] != files['tools/update-installer-entry.py'] or release['target']['version'] != offer['version']:
        raise ValueError('Signed launcher and checked target required')
    return release


def wait_original_phase(digest, phase, timeout=420):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try: state = inspection()
        except (OSError, ValueError):
            # An original marker can be between exclusive creation and fsync.
            # Its partial bytes never count as completion.
            time.sleep(2); continue
        if state['current'] != digest: raise ValueError('Original current release changed during recovery')
        if phase in state['phases']: return
        time.sleep(2)
    raise TimeoutError('Original recovery has not verified completion; preserve its attempt')


def run_job(identifier):
    with locked(wait=True):
        job = read('job.json')
        if not job or job['id'] != identifier or job['phase'] != 'queued': raise ValueError('Original queued job required')
        # The independent job process owns the lock for the entire operation.
        def phase(value):
            job['phase'] = value; save('job.json', job)
        try:
            if job['action'] == 'install':
                phase('downloading'); offer = job['offer']; path = ROOT / (identifier + '.pyz')
                E.write(path, download(offer['url'], 32 * 1024 * 1024))
                phase('verifying'); release = authenticate_file(path, offer)
                job['digest'] = release['bundleSha256']
                if version(offer['version']) <= version(installed_version()): raise ValueError('Target no longer newer')
                enrollment = json.loads(protected(ENROLLMENT))
                phase('installing')
                command(['/usr/bin/python3', '-I', '-B', str(path), 'install', enrollment['mode'], enrollment['package'], '--accept-existing-policy'], 850)
                if installed_version() != offer['version'] or 'healthy.json' not in inspection()['phases']:
                    raise ValueError('Actual installed identity and original health required')
            else:
                current = inspection()
                if current['current'] != job['token'] or 'healthy.json' not in current['phases'] or 'rolled-back.json' in current['phases']:
                    raise ValueError('Current release changed')
                phase('rolling-back')
                command(['/usr/bin/python3', '-I', '-B', str(CONTROLLER), 'rollback-request', job['token']])
                command(['/usr/bin/systemctl', 'restart', 'droiddeck-update@' + job['token'] + '.service'], 850)
                # Type=exec acknowledges process startup before recovery ends.
                # Keep the durable job alive until the ORIGINAL verifier writes
                # its intent-bound completion marker, including across reloads.
                wait_original_phase(job['token'], 'rolled-back.json')
            phase('complete')
        except Exception as error:
            job['errorCategory'] = type(error).__name__[:64]
            phase('failed'); raise


def unit_bytes(source):
    return ('[Unit]\nDescription=DroidDeck signed updates for Settings\nAfter=network-online.target\n'
            '[Service]\nType=simple\nUser=root\nUMask=0077\nRuntimeDirectory=droiddeck-update-ui\nRuntimeDirectoryMode=0750\n'
            'ExecStart=/usr/bin/python3 -I -B ' + str(source / 'runtime/portable/update-ui.py') + ' serve\n'
            'Restart=on-failure\nRestartSec=3\n[Install]\nWantedBy=multi-user.target\n').encode()


def enroll(source):
    """Install once from authenticated materialized source, never replace authority."""
    source = Path(source)
    os.close(E.protected_directory(source))
    protected(CONTROLLER)
    config = json.loads(protected('/etc/droiddeck/runtime.json'))
    if type(config.get('gid')) is not int or config['gid'] < 1000: raise ValueError('Original runtime group required')
    if not ROOT.exists():
        parent = E.protected_directory(ROOT.parent)
        try: os.mkdir(ROOT.name, 0o700, dir_fd=parent); os.fsync(parent)
        finally: os.close(parent)
    os.close(E.protected_directory(ROOT))
    receipt = read('service.json')
    path = Path('/etc/systemd/system') / UNIT
    if receipt:
        if protected(path, 8192, 0o644).decode() != receipt['unit']: raise ValueError('Original update UI service changed')
        if hashlib.sha256(protected(receipt['entry'], 65536)).hexdigest() != receipt['sha256']:
            raise ValueError('Original independent update UI source changed')
    else:
        if path.exists(): raise ValueError('Foreign update UI service refused')
        raw = unit_bytes(source); entry = source / 'runtime/portable/update-ui.py'
        save('service.json', {'unit': raw.decode(), 'entry': str(entry), 'sha256': hashlib.sha256(protected(entry, 65536)).hexdigest(), 'gid': config['gid'], 'uid': config['uid']})
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
        try: os.fchmod(fd, 0o644); os.write(fd, raw); os.fsync(fd)
        finally: os.close(fd)
        fd = E.protected_directory(path.parent)
        try: os.fsync(fd)
        finally: os.close(fd)
        command(['/usr/bin/systemctl', 'daemon-reload'])
    command(['/usr/bin/systemctl', 'enable', '--now', UNIT])
    return {'kind': 'droiddeck-update-ui-enrolled', 'protocol': 1, 'androidDataPreserved': True}


def serve():
    receipt = read('service.json'); gid = receipt['gid']; uid = receipt['uid']
    if type(uid) is not int or type(gid) is not int: raise ValueError('Original runtime identity required')
    directory = SOCKET.parent
    state = directory.lstat()
    if not stat.S_ISDIR(state.st_mode) or state.st_uid != 0 or state.st_mode & 0o022: raise ValueError('Protected socket directory required')
    os.chown(directory, 0, gid); os.chmod(directory, 0o750)
    if SOCKET.exists():
        state = SOCKET.lstat()
        if not stat.S_ISSOCK(state.st_mode) or state.st_uid != 0: raise ValueError('Foreign helper socket refused')
        SOCKET.unlink()
    slots = threading.BoundedSemaphore(8)
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            if not slots.acquire(False): return
            try:
                self.request.settimeout(30)
                _pid, peer, _group = struct.unpack('3i', self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if peer not in (0, uid): return
                line = self.rfile.readline(2049)
                if len(line) > 2048 or not line.endswith(b'\n'): return
                try:
                    request = json.loads(line)
                    if request == {'action': 'status'}: result = snapshot()
                    else: result = dispatch(request)
                    response = {'state': result}
                except Exception: response = {'error': 'Update request unavailable. Inspect current status before retrying.'}
                self.wfile.write(E.encode(response))
            finally: slots.release()
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        request_queue_size = 8
    with Server(str(SOCKET), Handler) as server:
        os.chown(SOCKET, 0, gid); os.chmod(SOCKET, 0o660)
        server.serve_forever()


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Root Linux update helper required')
    os.umask(0o077)
    if args == ['serve']: serve()
    elif len(args) == 2 and args[0] == 'run-job' and re.fullmatch('[a-f0-9]{32}', args[1]): run_job(args[1])
    else: raise ValueError('Fixed supervised update helper operation required')


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except Exception:
        print('Update helper stopped; original recovery records preserved.', file=sys.stderr); sys.exit(1)
