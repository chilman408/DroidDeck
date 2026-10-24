# SPDX-License-Identifier: GPL-3.0-only
"""Bounded generated-media proof on the accepted disposable post-recovery graph."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types
import urllib.request
import uuid

ROOT = Path('/opt/droiddeck/post-recovery-media-20261015bs')
STATE = Path('/run/droiddeck-post-media-20261015bs')
UNIT = 'droiddeck-post-recovery-media-20261015bs.service'
VIDEO = Path('/opt/droiddeck/generated-playback-validation-20261014bi')
AUDIO = Path('/opt/droiddeck/generated-audio-validation-20261014bn')
VIDEO_UNIT = 'droiddeck-generated-playback-20261014bi.service'
AUDIO_UNIT = 'droiddeck-generated-audio-fixture-20261014bn.service'
CAPTURE_UNIT = 'droiddeck-generated-audio-capture-20261014bn.service'
MEDIA_DIGEST = 'bc0486708a3552da97f57d1ae026f81c12cf688201429eb0607d49a22c2a5681'
PHASE = 'admission'
PINS = {
    VIDEO / 'generated-playback.js': 'e33dcb344bac29f3c648c88a9a63933e14d7f47e5c935ed0b2ae9c8034083c7e',
    VIDEO / 'generated-playback-check.js': '7a8aac492417b42c5f50d590b662bc01ee399abb21044838ca726e3701d06e3c',
    VIDEO / 'generated-playback-pixels.py': '8f961f39dbd1004ff19a2a3102574a6f01c58b6ea107f931682fe036b784b989',
    AUDIO / 'generated-audio-fixture.js': '47074a8281fe90a7047d5ed4bfd0176ead784e18be511d6a2fc3950186d61682',
    AUDIO / 'generated-audio-check.js': '0c64e57afb4c3d13985a905139d6798fffb13c04a14343052697db89cd822b55',
    AUDIO / 'generated-tone-stats.js': 'fe3871e1f9188a16cc599265b4a09308c4e53b51e883175f7b7147a042fb011f',
    AUDIO / 'raw-audio-probe.js': '71d868b1883d6010ec26b28f866130967b595e8199d8377661be33cfb2c70f87',
    AUDIO / 'generated-playback.js': 'e33dcb344bac29f3c648c88a9a63933e14d7f47e5c935ed0b2ae9c8034083c7e',
    AUDIO / 'generated-playback-check.js': '7a8aac492417b42c5f50d590b662bc01ee399abb21044838ca726e3701d06e3c',
}


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def report(video, audio):
    if (not isinstance(video, dict) or set(video) != {'kind', 'frames', 'colorFrames', 'motionSamples',
            'afterH264Decode', 'savedPixels', 'verified', 'browserDecodedFrames', 'audioVerified', 'browserStreamVerified'} or
            video['kind'] != 'generated-playback-pixels' or video['afterH264Decode'] is not True or
            video['savedPixels'] is not False or video['verified'] is not True or
            video['audioVerified'] is not False or video['browserStreamVerified'] is not False or
            not all(integer(video[key], 0, 64) for key in ('frames', 'colorFrames', 'motionSamples')) or
            not 8 <= video['colorFrames'] <= video['frames'] or not 3 <= video['motionSamples'] <= video['colorFrames'] or
            not integer(video['browserDecodedFrames'], 1, 20000)):
        raise ValueError('Actual generated picture/motion required')
    if (not isinstance(audio, dict) or set(audio) != {'kind', 'bytes', 'frames', 'seconds', 'nonzeroSamples', 'peak',
            'tone', 'generatedPcmVerified', 'savedAudio', 'browserAudioVerified', 'installable'} or
            audio['kind'] != 'generated-android-audio' or audio['generatedPcmVerified'] is not True or
            any(audio[key] is not False for key in ('savedAudio', 'browserAudioVerified', 'installable')) or
            not integer(audio['frames'], 48000, 524288) or not integer(audio['bytes'], 192000, 2097152) or
            audio['bytes'] != audio['frames'] * 4 or type(audio['seconds']) not in (int, float) or
            audio['seconds'] != audio['frames'] / 48000 or
            not integer(audio['nonzeroSamples'], 4800, audio['frames'] * 2) or not integer(audio['peak'], 1, 32768)):
        raise ValueError('Actual generated PCM required')
    tone = audio['tone']
    if (not isinstance(tone, dict) or set(tone) != {'frequencyHz', 'sampleRate', 'windows', 'matchingWindows'} or
            tone['frequencyHz'] != 1000 or tone['sampleRate'] != 48000 or
            not integer(tone['windows'], 100, 1092) or tone['windows'] != audio['frames'] // 480 or
            not integer(tone['matchingWindows'], 50, tone['windows']) or tone['matchingWindows'] < tone['windows'] * .7):
        raise ValueError('Known generated tone required')
    return {'kind': 'post-recovery-generated-media', 'generatedMediaVerified': True,
            'video': {key: video[key] for key in ('frames', 'colorFrames', 'motionSamples')},
            'audio': {'seconds': audio['seconds'], 'frequencyHz': 1000, 'matchingWindows': tone['matchingWindows'], 'windows': tone['windows']},
            'nativeGpuVerified': True, 'sameGraphGeneration': True, 'cleanupVerified': True,
            'browserStreamVerified': False, 'syncVerified': False, 'savedMedia': False, 'installable': False}


class Proof:
    def __init__(self, *, admit, identity, vacant, video, audio, cleanup, native):
        self.admit, self.identity, self.vacant = admit, identity, vacant
        self.video, self.audio, self.cleanup, self.native = video, audio, cleanup, native

    def run(self):
        self.admit()
        generation = self.identity()
        def fence():
            if self.identity() != generation:
                raise ValueError('Accepted graph generation changed')
        try:
            self.vacant()
            video = self.video()
            fence()
            audio = self.audio()
            fence()
        finally:
            self.cleanup()
        fence()
        self.vacant()
        if self.native() is not True:
            raise ValueError('Final native GPU did not verify')
        fence()
        return report(video, audio)


def child_detail(data):
    message = data.decode('utf-8', errors='replace')
    video = re.search(r'Generated Android playback inspection incomplete: phase=([a-z-]{1,32})(?:, pixelPhase=([a-z-]{1,32}))?', message)
    if video:
        return 'video=' + video[1] + ('; pixel=' + video[2] if video[2] else '')
    audio = re.search(r'Generated Android audio incomplete; phase=([a-z-]{1,32}); scope=([a-z-]{1,32}); child=([a-z-]{1,32})', message)
    return 'audio=' + audio[1] + '; scope=' + audio[2] + '; child=' + audio[3] if audio else 'unavailable'


def command(args, timeout=10, maximum=8192):
    try:
        value = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout, check=True).stdout
    except subprocess.CalledProcessError as error:
        print('Generated child boundary: ' + child_detail(error.stderr), file=sys.stderr)
        raise
    if len(value) > maximum:
        raise ValueError('Generated report bound')
    return value.decode().strip()


def unit(name, timeout=10):
    return dict(line.split('=', 1) for line in command(['/usr/bin/systemctl', 'show', name,
        '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID', '-p', 'Description'], timeout).splitlines())


def owned_unit(state, description):
    if state['MainPID'] == '0' and state['ControlPID'] == '0' and state['ActiveState'] in ('inactive', 'failed'):
        return False
    if state['Description'] != description:
        raise ValueError('Foreign fixture unit; no stop')
    return True


class Budget:
    def __init__(self, clock):
        self.clock, self.deadline = clock, clock() + 45

    def remaining(self, maximum):
        left = self.deadline - self.clock()
        if left <= 0:
            raise TimeoutError('Owned cleanup budget exhausted')
        return min(left, maximum)


def clean_all(actions, clock=time.monotonic):
    budget, failed = Budget(clock), False
    for action in actions:
        try:
            action(budget)
        except Exception:
            failed = True
    if failed:
        raise ValueError('Owned media cleanup incomplete')


def owned_pages(dump, urls):
    lines, found = dump.splitlines(), False
    pattern = re.compile(r'^(\s*)\* Hist\s+#\d+: ActivityRecord\{[^}\n]*\borg\.lineageos\.jelly/(?:\.MainActivity|org\.lineageos\.jelly\.MainActivity)\b')
    for record in re.findall(r'ActivityRecord\{([^}\n]*)\}', dump):
        if 'org.lineageos.jelly' in record:
            component = re.search(r'\borg\.lineageos\.jelly/([A-Za-z0-9_.$]+)', record)
            if not component or component[1] not in ('.MainActivity', 'org.lineageos.jelly.MainActivity'):
                raise ValueError('Other Jelly activity; no package-wide stop')
    for index, line in enumerate(lines):
        match = pattern.search(line)
        if not match:
            continue
        block = []
        for following in lines[index + 1:]:
            if following.strip() and len(following) - len(following.lstrip()) <= len(match[1]):
                break
            block.append(following)
        intents = re.findall(r'^\s*Intent \{[^\n]*\bdat=([^\s}]+)', '\n'.join(block), re.M)
        if len(intents) != 1 or intents[0] not in urls:
            raise ValueError('Foreign or ambiguous Jelly task; no app stop')
        found = True
    if not found and 'org.lineageos.jelly/.MainActivity' in dump:
        raise ValueError('Jelly activity ownership not parseable')
    return found


def wait_pages_closed(read, urls, clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + 5
    while True:
        try:
            if not owned_pages(read(), urls):
                return
        except ValueError:
            pass  # Observe retirement only; never repeat the admitted app stop.
        if clock() >= deadline:
            raise TimeoutError('Generated activity retirement not verified')
        sleep(.1)


def video_command():
    return ['/usr/bin/env', 'PATH=/system/bin:/apex/com.android.runtime/bin:/usr/bin:/usr/sbin:/bin',
            'ANDROID_ROOT=/system', 'ANDROID_DATA=/data', '/usr/bin/node', str(VIDEO / 'generated-playback-check.js')]


def publish_record(filename, data, write=os.write):
    filename = Path(filename)
    temporary = filename.with_name('.pending-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            while data:
                count = write(fd, data)
                if count <= 0:
                    raise ValueError('Generated page receipt incomplete')
                data = data[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.name == 'nt':
            os.rename(temporary, filename)  # Windows rename never replaces an existing target.
        else:
            rename = ctypes.CDLL(None, use_errno=True).renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(temporary), -100, os.fsencode(filename), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, 'Exclusive receipt publication failed')
            directory = os.open(filename.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def fixture_status(value, kind):
    return isinstance(value, dict) and value.get('kind') == kind and value.get('sha256') == MEDIA_DIGEST


def wait_status(port, kind, active=False):
    deadline = time.monotonic() + 15
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while True:
        try:
            with opener.open('http://192.168.240.1:' + str(port) + '/status', timeout=2) as response:
                raw = response.read(2049)
            if len(raw) > 2048:
                raise ValueError('Generated lease bound')
            value = json.loads(raw)
            if fixture_status(value, kind) and (not active or value.get('active') is True):
                return value
        except (OSError, ValueError):
            pass
        if time.monotonic() >= deadline:
            raise ValueError('Generated fixture not ready or progressing')
        time.sleep(.2)


def load_source(path, pin, read):
    data = read(path)
    if hashlib.sha256(data).hexdigest() != pin:
        raise ValueError('Immutable media source changed')
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


class Context:
    def __init__(self, token):
        self.token = token
        # Bootstrap only the previously verified root-private reader, not its lifecycle.
        source = Path('/opt/droiddeck/graph-recovery-20261015br/graph-recovery.py')
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_uid, info.st_gid, info.st_nlink) != (0, 0, 1) or info.st_mode & 0o022:
            raise ValueError('Protected reader source required')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != '5f40637f4e3141c03c025c51228539af6d122da534374d31270c851163c365b1':
            raise ValueError('Protected reader pin changed')
        module = types.ModuleType('post_media_reader')
        module.__file__ = str(source)
        exec(compile(data, str(source), 'exec'), module.__dict__)
        self.read = module.protected_read
        self.read(Path(__file__))

    def sources(self, filenames):
        for filename in filenames:
            digest = PINS[filename]
            if hashlib.sha256(self.read(filename)).hexdigest() != digest:
                raise ValueError('Accepted media source changed')

    def description(self, role):
        return 'DroidDeck post-recovery ' + role + ' ' + self.token

    def stop(self, name, role, budget=None):
        budget = budget or Budget(time.monotonic)
        if owned_unit(unit(name, budget.remaining(2)), self.description(role)):
            command(['/usr/bin/systemctl', 'stop', name], timeout=budget.remaining(8))

    def page_record(self, role, url):
        pid = command(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])
        if not re.fullmatch(r'[1-9][0-9]{0,9}', pid) or int(pid) < 2:
            raise ValueError('Generated page container unavailable')
        birth = Path('/proc/' + pid + '/stat').read_text().rpartition(') ')[2].split()[19]
        value = {'owner': self.token, 'pid': pid, 'birth': birth, 'url': url,
                 'boot': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        info = STATE.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
            raise ValueError('Private generated-page state required')
        filename = STATE / (self.token + '-' + role + '.json')
        publish_record(filename, (json.dumps(value, sort_keys=True) + '\n').encode())
        self.read(filename, 4096)

    def close_pages(self, budget):
        records = []
        for role in ('video', 'audio'):
            try:
                value = json.loads(self.read(STATE / (self.token + '-' + role + '.json'), 4096))
            except FileNotFoundError:
                continue
            if (set(value) != {'owner', 'pid', 'birth', 'url', 'boot'} or value['owner'] != self.token or
                    value['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip() or
                    not isinstance(value['pid'], str) or not re.fullmatch(r'[1-9][0-9]{0,9}', value['pid']) or int(value['pid']) < 2 or
                    not isinstance(value['birth'], str) or not value['birth'].isdigit() or
                    not isinstance(value['url'], str) or not re.fullmatch(
                        r'http://192\.168\.240\.1:18186/|http://192\.168\.240\.1:18187/\?proof=[0-9a-f]{32}', value['url'])):
                raise ValueError('Foreign generated-page receipt')
            records.append(value)
        if not records:
            return
        def guest_generation():
            pid = command(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'], budget.remaining(2))
            if any(record['pid'] != pid for record in records):
                raise ValueError('Generated page guest changed')
            birth = Path('/proc/' + pid + '/stat').read_text().rpartition(') ')[2].split()[19]
            if any(record['birth'] != birth for record in records):
                raise ValueError('Generated page process generation changed')
            return pid
        pid = guest_generation()
        prefix = ['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', '/system/bin/env',
                  'PATH=/system/bin:/apex/com.android.runtime/bin', 'ANDROID_ROOT=/system', 'ANDROID_DATA=/data']
        def dump():
            return command(prefix + ['/system/bin/dumpsys', 'activity', 'activities'], budget.remaining(5), maximum=4 * 1024 * 1024)
        urls = {record['url'] for record in records}
        if owned_pages(dump(), urls):
            guest_generation()
            command(prefix + ['/system/bin/am', 'force-stop', 'org.lineageos.jelly'], budget.remaining(5))
            wait_pages_closed(dump, urls)
        guest_generation()

    def cleanup(self):
        global PHASE
        previous, PHASE = PHASE, 'cleanup'
        # Audio cleanup is owner-fenced and also handles an interrupted pre-capture.
        def audio(budget):
            self.sources([AUDIO / name for name in ('generated-audio-check.js', 'generated-audio-fixture.js',
                                                   'generated-playback.js', 'generated-playback-check.js')])
            command(['/usr/bin/node', str(AUDIO / 'generated-audio-check.js'), '--cleanup', self.token], budget.remaining(20))
        clean_all((audio, self.close_pages, lambda budget: self.stop(AUDIO_UNIT, 'audio', budget),
                   lambda budget: self.stop(VIDEO_UNIT, 'video', budget)))
        PHASE = previous

    def fixture(self, name, filename, role):
        state = unit(name)
        if state['MainPID'] != '0' or state['ControlPID'] != '0' or state['ActiveState'] not in ('inactive', 'failed'):
            raise ValueError('Fixture namespace occupied')
        command(['/usr/bin/systemd-run', '--unit=' + name, '--description=' + self.description(role), '--collect',
                 '--uid=1000', '--gid=1000', '--property=Type=exec', '--property=RuntimeMaxSec=60s',
                 '--property=TimeoutStopSec=5s', '/usr/bin/node', str(filename), '--serve'])
        return wait_status(18186 if role == 'video' else 18187, 'generated-playback' if role == 'video' else 'generated-audio-fixture')

    def video(self):
        global PHASE
        PHASE = 'video-fixture'
        self.fixture(VIDEO_UNIT, VIDEO / 'generated-playback.js', 'video')
        pid = command(['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-pH'])
        if not re.fullmatch(r'[1-9][0-9]{0,9}', pid) or int(pid) < 2:
            raise ValueError('Accepted container unavailable')
        prefix = ['/usr/bin/nsenter', '-t', pid, '-m', '-p', '-r', '--', '/system/bin/env',
                  'PATH=/system/bin:/apex/com.android.runtime/bin', 'ANDROID_ROOT=/system', 'ANDROID_DATA=/data']
        command(prefix + ['/system/bin/am', 'force-stop', 'org.lineageos.jelly'])
        self.page_record('video', 'http://192.168.240.1:18186/')
        command(prefix + ['/system/bin/am', 'start', '-W', '-a', 'android.intent.action.VIEW',
                          '-d', 'http://192.168.240.1:18186/', '-n', 'org.lineageos.jelly/.MainActivity'], timeout=20)
        PHASE = 'video-progress'
        wait_status(18186, 'generated-playback', active=True)
        PHASE = 'video-decode'
        result = json.loads(command(video_command(), timeout=25))
        self.stop(VIDEO_UNIT, 'video')
        return result

    def audio(self):
        global PHASE
        PHASE = 'audio-fixture'
        page = self.fixture(AUDIO_UNIT, AUDIO / 'generated-audio-fixture.js', 'audio')
        token = page.get('pageToken')
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
            raise ValueError('Generated audio page identity unavailable')
        self.page_record('audio', 'http://192.168.240.1:18187/?proof=' + token)
        PHASE = 'audio-capture'
        return json.loads(command(['/usr/bin/node', str(AUDIO / 'generated-audio-check.js'), '--inspect', self.token], timeout=45))

    def proof(self):
        self.sources(PINS)
        source = Path('/opt/droiddeck/gpu-boot-20261015bq/gpu-boot.py')
        module = load_source(source, 'b158bb4971e29933e8738911c2b730286739b0e533e396e6538b7d092050758a', self.read)
        coordinator = module.context()
        def identity():
            coordinator.admit()
            with coordinator.store.lock():
                state = coordinator.store.status()
            if state['stage'] != 'ready' or state['boot'] != coordinator.boot():
                raise ValueError('Accepted current-boot graph required')
            return state, coordinator.peers()
        def vacant():
            state = unit('tesla-waydroid-runtime.service')
            if owned_unit(state, 'never-owned'):
                raise ValueError('Runtime must remain stopped')
            for name in (VIDEO_UNIT, AUDIO_UNIT, CAPTURE_UNIT):
                value = unit(name)
                if value['MainPID'] != '0' or value['ControlPID'] != '0' or value['ActiveState'] not in ('inactive', 'failed'):
                    raise ValueError('Capture or fixture remains active')
            if command(['/usr/bin/python3', '/opt/droiddeck/runtime/waydroid-display/native-stop.py', '--assert-vacant']) != 'VACANT':
                raise ValueError('Native capture remains active')
        def admit():
            receipt = json.loads(self.read(Path('/var/lib/droiddeck/graph-recovery-20261015br/receipt.json'), 4096))
            if (set(receipt) != {'version', 'boot', 'phase'} or type(receipt['version']) is not int or
                    receipt['version'] != 1 or receipt['boot'] != coordinator.boot() or receipt['phase'] != 'complete'):
                raise ValueError('Completed current-boot recovery receipt required')
            identity()
            coordinator.verify()
        return Proof(admit=admit, identity=identity, vacant=vacant, video=self.video,
                     audio=self.audio, cleanup=self.cleanup, native=coordinator.verify)


def main(args):
    if (len(args) != 2 or args[0] not in ('--inspect', '--cleanup') or not re.fullmatch(r'[0-9a-f]{32}', args[1]) or
            sys.platform != 'linux' or os.geteuid() != 0 or Path(__file__) != ROOT / 'post-recovery-media.py'):
        raise ValueError('Fixed disposable generated-media command required')
    field = 'MainPID' if args[0] == '--inspect' else 'ControlPID'
    if unit(UNIT)[field] != str(os.getpid()):
        raise ValueError('Exact supervised media proof required')
    ctx = Context(args[1])
    if args[0] == '--cleanup':
        ctx.cleanup()
        print('Owned post-recovery media cleanup complete')
    else:
        print(json.dumps(ctx.proof().run(), sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Post-recovery generated-media proof incomplete; phase=' + PHASE + '; class=' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
