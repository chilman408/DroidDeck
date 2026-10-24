# SPDX-License-Identifier: GPL-3.0-only
"""Fixed disposable .90 trials; no app, license, image or preference writes."""
import configparser
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = '/opt/teslaandroid-waydroid'
CFG = Path('/var/lib/waydroid/waydroid.cfg')
BACKUPS = Path('/var/lib/teslaandroid-waydroid/backups')
GPU_OVERRIDE = Path('/etc/systemd/system/tesla-waydroid-compositor.service.d/60-gpu-renderer.conf')
RUNTIME = 'tesla-waydroid-runtime.service'
SESSION = 'tesla-waydroid-headless-session.service'
COMPOSITOR = 'tesla-waydroid-compositor.service'
PREFS = [Path('/var/lib/teslaandroid-waydroid/' + name + '.json') for name in ['display', 'gps']]
PREFS += [Path('/home/hilman/.config/teslaandroid-waydroid/' + name + '.json')
          for name in ['browser-access', 'browser-audio', 'browser-video', 'rear-display']]
PROFILES = {'codec2': {'debug.stagefright.ccodec': '4'},
            'gpu': {'ro.waydroid.software_rendering': '0', 'persist.waydroid.use_subsurface': 'true'}}
GPU_CODE = ('[Service]\nExecStart=\nExecStart=/usr/bin/weston --backend=pipewire --renderer=gl '
            '--socket=wayland-teslaandroid --width=1280 --height=720 --idle-time=0 --no-config '
            '--shell=kiosk-shell.so\n').encode()
DECODER_XML = Path('/var/lib/waydroid/overlay/vendor/etc/media_codecs_ffmpeg_c2.xml')
DECODER_TEMP = DECODER_XML.with_name('.tesla-decoder-reuse-20261010.tmp')
DECODER_NAMES = ('c2.ffmpeg.h264.decoder', 'c2.ffmpeg.hevc.decoder')


def decoder_candidate(data):
    if len(data) > 65536 or any(token in data.upper() for token in [b'<!DOCTYPE', b'<!ENTITY']):
        raise ValueError('Invalid decoder XML')
    try:
        root = ET.fromstring(data, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    except ET.ParseError:
        raise ValueError('Invalid decoder XML') from None
    if root.tag != 'MediaCodecs':
        raise ValueError('Unexpected decoder document')
    for name in DECODER_NAMES:
        codecs = root.findall("./Decoders/MediaCodec[@name='" + name + "']")
        if len(codecs) != 1:
            raise ValueError('Expected one fixed video decoder')
        features = codecs[0].findall("./Feature[@name='adaptive-playback']")
        if len(features) != 1 or features[0].attrib != {'name': 'adaptive-playback'}:
            raise ValueError('Unexpected adaptive playback declaration')
        codecs[0].remove(features[0])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def decoder_reuse_trial(rollback):
    protected(CFG)
    protected(BACKUPS, directory=True)
    protected(DECODER_XML.parent.parent, directory=True)
    backup = BACKUPS / 'decoder-reuse-20261010'

    def revert(manifest):
        if digest(CFG.read_bytes()) != manifest['config']:
            raise RuntimeError('Config changed; refusing stale decoder rollback')
        if DECODER_XML.exists():
            protected(DECODER_XML)
            if digest(DECODER_XML.read_bytes()) != manifest['candidate']:
                raise RuntimeError('Decoder overlay changed; refusing stale rollback')
        elif DECODER_XML.is_symlink():
            raise RuntimeError('Unsafe decoder overlay')
        if DECODER_TEMP.exists() or DECODER_TEMP.is_symlink():
            protected(DECODER_TEMP)
            if digest(DECODER_TEMP.read_bytes()) != manifest['candidate']:
                raise RuntimeError('Decoder staging file changed; refusing stale rollback')
        stop()
        if DECODER_XML.exists():
            DECODER_XML.unlink()
        if DECODER_TEMP.exists():
            DECODER_TEMP.unlink()
        start()
        ready()

    if rollback:
        protected(backup, directory=True)
        protected(backup / 'manifest.json')
        manifest = json.loads((backup / 'manifest.json').read_text())
        if manifest.get('profile') != 'decoder-reuse':
            raise RuntimeError('Invalid decoder backup')
        revert(manifest)
        print(json.dumps({'rollback': 'decoder-reuse', 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [RUNTIME, SESSION, COMPOSITOR]:
        if service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active before trial')
    if DECODER_XML.exists() or DECODER_XML.is_symlink() or DECODER_TEMP.exists() or DECODER_TEMP.is_symlink():
        raise RuntimeError('Existing decoder overlay must not be overwritten')
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(CFG.read_text())
    if parser.get('properties', 'ro.waydroid.software_rendering', fallback='') != '1' or GPU_OVERRIDE.exists():
        raise RuntimeError('Decoder reuse trial requires recovered software rendering')
    pid = run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
    if not pid.isdecimal() or int(pid) < 2:
        raise RuntimeError('Invalid container identity')
    source = Path('/proc') / pid / 'root/vendor/etc/media_codecs_ffmpeg_c2.xml'
    protected(source)
    original = source.read_bytes()
    changed = decoder_candidate(original)
    if run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10) != pid:
        raise RuntimeError('Container changed during preparation')
    before = preferences()
    manifest = {'profile': 'decoder-reuse', 'config': digest(CFG.read_bytes()),
                'original': digest(original), 'candidate': digest(changed), 'preferences': before}
    backup.mkdir(mode=0o700)
    save(backup / 'original.xml', original, 0o600)
    save(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    if not DECODER_XML.parent.exists():
        DECODER_XML.parent.mkdir(mode=0o755)
    protected(DECODER_XML.parent, directory=True)
    stop()
    try:
        save(DECODER_TEMP, changed, 0o644)
        os.replace(DECODER_TEMP, DECODER_XML)
        start()
        ready()
        mounted_pid = run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
        if not mounted_pid.isdecimal() or int(mounted_pid) < 2:
            raise RuntimeError('Invalid restarted container identity')
        mounted = Path('/proc') / mounted_pid / 'root/vendor/etc/media_codecs_ffmpeg_c2.xml'
        protected(mounted)
        if digest(mounted.read_bytes()) != manifest['candidate']:
            raise RuntimeError('Candidate decoder overlay not mounted')
        print(json.dumps({'trial': 'decoder-reuse', 'h264Ready': True,
                          'preferencesUnchanged': before == preferences(), 'paidPlaybackAccepted': False}))
    except Exception:
        revert(manifest)
        raise RuntimeError('Decoder trial failed; previous overlay and H264 restored')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def candidate(data, profile):
    if profile not in PROFILES or len(data) > 65536:
        raise ValueError('Invalid trial profile/config')
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode('utf-8'))
    except (configparser.Error, UnicodeError):
        raise ValueError('Invalid Waydroid config') from None
    if not parser.has_section('properties') or not parser.has_section('waydroid'):
        raise ValueError('Missing Waydroid config sections')
    for name, value in PROFILES[profile].items():
        parser.set('properties', name, value)
    stream = io.StringIO()
    parser.write(stream)
    return stream.getvalue().encode()


def protected(path, directory=False):
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != 0 or info.st_mode & 0o022 or (not directory and info.st_nlink != 1):
        raise RuntimeError('Unsafe protected trial path')
    return info


def run(args, timeout=120):
    # Child failures are reported without echoing raw Android output or config contents.
    result = subprocess.run(args, env={**os.environ, 'LD_LIBRARY_PATH': ROOT + '/libgbinder'},
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Trial command failed: ' + Path(args[0]).name)
    return result.stdout.decode('utf-8', errors='replace').strip()


def service(*args):
    return run(['/usr/bin/systemctl', *args])


def preferences():
    result = {}
    for path in PREFS:
        if not path.exists():
            result[str(path)] = None
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError('Unsafe preference path')
        result[str(path)] = digest(path.read_bytes())
    return result


def stop():
    service('stop', RUNTIME)
    service('stop', SESSION)
    run(['/usr/sbin/runuser', '-u', 'hilman', '--', '/usr/bin/env',
         'XDG_RUNTIME_DIR=/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus',
         'LD_LIBRARY_PATH=' + ROOT + '/libgbinder', '/usr/bin/waydroid', 'session', 'stop'], 30)
    service('stop', COMPOSITOR)


def start():
    # Offline regeneration only: no image/OTA downloads or app updates.
    run(['/usr/bin/waydroid', 'upgrade', '-o'], 60)
    # Waydroid regenerates LXC config, dropping our mandatory guest-only PID hook.
    run(['/usr/bin/node', ROOT + '/runtime/waydroid-display/pid-compat.js'], 15)
    service('daemon-reload')
    # Offline regeneration does not refresh the long-lived manager's open handles.
    service('restart', 'waydroid-container.service')
    for unit in [COMPOSITOR, SESSION, RUNTIME]:
        service('start', unit)


def ready():
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            pid = run(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], 10)
            if not pid.isdecimal() or int(pid) < 2:
                raise RuntimeError('Container not ready')
            result = run(['/usr/bin/nsenter', '-t', pid, '-n', '/usr/sbin/runuser', '-u', 'hilman',
                          '--', '/usr/bin/env', 'NODE_PATH=' + ROOT + '/runtime/preview-gateway/node_modules',
                          '/usr/bin/node', ROOT + '/runtime/preview-gateway/runtime-probe.js', 'video-relay'], 10)
            if result == 'READY':
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        time.sleep(1)
    raise RuntimeError('H264 recovery deadline exceeded')


def save(path, data, mode):
    if path.exists() or path.is_symlink():
        protected(path)
    protected(path.parent, directory=True)
    # Keep rollback's exact-content checks valid even if a write is interrupted.
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        if path.exists() or path.is_symlink():
            protected(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(backup, manifest):
    protected(backup, directory=True)
    protected(backup / 'config')
    original = (backup / 'config').read_bytes()
    if digest(original) != manifest['before']:
        raise RuntimeError('Backup checksum mismatch')
    current = digest(CFG.read_bytes())
    if current not in [manifest['candidate'], manifest['before']]:
        raise RuntimeError('Config changed after trial; refusing stale rollback')
    if manifest['profile'] == 'gpu':
        if current == manifest['candidate'] and (not GPU_OVERRIDE.exists() or GPU_OVERRIDE.read_bytes() != GPU_CODE):
            raise RuntimeError('GPU override changed after trial')
        if GPU_OVERRIDE.exists():
            protected(GPU_OVERRIDE)
            if GPU_OVERRIDE.read_bytes() != GPU_CODE:
                raise RuntimeError('GPU override changed after trial')
    stop()
    save(CFG, original, manifest['mode'])
    if manifest['profile'] == 'gpu' and GPU_OVERRIDE.exists():
        GPU_OVERRIDE.unlink()
    start()
    ready()


def main(args):
    if os.getuid() != 0 or len(args) != 1 or args[0] not in ['--codec2', '--gpu', '--retry-codec2', '--rollback-codec2', '--rollback-gpu', '--decoder-reuse', '--rollback-decoder-reuse']:
        raise RuntimeError('Fixed root trial profile required')
    if args[0] in ['--decoder-reuse', '--rollback-decoder-reuse']:
        decoder_reuse_trial(args[0].startswith('--rollback-'))
        return
    profile = args[0].split('-')[-1]
    rollback = args[0].startswith('--rollback-')
    retry = args[0].startswith('--retry-')
    backup = BACKUPS / ('render-' + profile + '-20261009')
    protected(CFG)
    protected(CFG.parent, directory=True)
    protected(BACKUPS, directory=True)
    if rollback:
        protected(backup / 'manifest.json')
        manifest = json.loads((backup / 'manifest.json').read_text())
        if manifest.get('profile') != profile or manifest.get('mode') != 0o644:
            raise RuntimeError('Invalid trial backup')
        restore(backup, manifest)
        print(json.dumps({'rollback': profile, 'h264Ready': True, 'preferencesRestored': False}))
        return
    for unit in [RUNTIME, SESSION, COMPOSITOR]:
        if service('is-active', unit) != 'active':
            raise RuntimeError('Owned runtime must be active before trial')
    if profile == 'gpu':
        protected(GPU_OVERRIDE.parent, directory=True)
        if GPU_OVERRIDE.exists() or GPU_OVERRIDE.is_symlink():
            raise RuntimeError('GPU override already exists')
    original = CFG.read_bytes()
    changed = candidate(original, profile)
    before = preferences()
    manifest = {'profile': profile, 'before': digest(original), 'candidate': digest(changed),
                'mode': stat.S_IMODE(CFG.stat().st_mode), 'preferences': before}
    if manifest['mode'] != 0o644:
        raise RuntimeError('Unexpected config mode')
    if retry:
        protected(backup, directory=True)
        protected(backup / 'manifest.json')
        previous = json.loads((backup / 'manifest.json').read_text())
        if any(previous.get(key) != manifest[key] for key in ['profile', 'before', 'candidate', 'mode']):
            raise RuntimeError('Retry differs from original config trial')
        protected(backup / 'config')
        if digest((backup / 'config').read_bytes()) != manifest['before']:
            raise RuntimeError('Retry backup checksum mismatch')
    else:
        backup.mkdir(mode=0o700)
        save(backup / 'config', original, 0o600)
        save(backup / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode(), 0o600)
    stop()
    try:
        save(CFG, changed, 0o644)
        if profile == 'gpu':
            save(GPU_OVERRIDE, GPU_CODE, 0o644)
        start()
        ready()
        print(json.dumps({'trial': profile, 'backup': str(backup), 'h264Ready': True,
                          'preferencesUnchanged': before == preferences(), 'paidPlaybackAccepted': False}))
    except Exception:
        restore(backup, manifest)
        raise RuntimeError('Trial failed; previous config and H264 restored')


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
