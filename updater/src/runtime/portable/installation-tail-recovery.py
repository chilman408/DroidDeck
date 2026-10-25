# SPDX-License-Identifier: GPL-3.0-only
"""Explicit RC2 defaults-tail recovery; never manufacture old phase receipts."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


def load(name):
    spec = importlib.util.spec_from_file_location('tail_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = load('host-prerequisites')
ROOT = Path('/var/lib/droiddeck/installation-tail-recovery')
STATE = Path('/root/droiddeck-installation')
TAIL = ('31-required-defaults', '32-wake-initial-display', '33-verify-runtime', '34-verify-gpu')


def token(value):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{12}', value):
        raise ValueError('Exact current original GPU admission required')
    return value


def interrupted(intent, config, machine_pin, package_pin, phases, markers):
    expected = {name + suffix: H.encode({'phase': name}) for name in phases if name not in TAIL
                for suffix in ('.started.json', '.complete.json')}
    expected[TAIL[0] + '.started.json'] = H.encode({'phase': TAIL[0]})
    if (len(phases) != 35 or tuple(phases[-4:]) != TAIL or len(expected) != 63
            or intent != {'schema': 1, 'configuration': config,
                          'machineSha256': machine_pin, 'packageManifestSha256': package_pin}
            or type(intent.get('schema')) is not int or markers != expected):
        raise ValueError('Exact original31-complete/defaults-started-only boundary required')


def boundary(records, phases, config, machine_pin, current):
    base = load('update-base')
    token(current)
    names = os.listdir(records.dirs.open(STATE))
    if 'complete.json' in names:
        raise ValueError('Completed installation must use original inspection, not recovery')
    markers = {name: records.read(STATE / name) for name in names
               if name.endswith(('.started.json', '.complete.json'))}
    intent_raw = records.read(STATE / 'intent.json')
    interrupted(base.canonical(intent_raw), config, machine_pin, base.PACKAGE_PIN, phases, markers)
    original = base.canonical(records.read(STATE / 'gpu.json'))
    if set(original) != {'transaction'}:
        raise ValueError('Original installation GPU transaction required')
    token(original['transaction'])
    if original['transaction'] == current:
        raise ValueError('Explicit restored-to-ready GPU succession required')
    return {'schema': 1, 'packageManifestSha256': base.PACKAGE_PIN,
            'machineSha256': machine_pin, 'configurationSha256': base.S.digest(H.encode(config)),
            'installationIntentSha256': base.S.digest(intent_raw),
            'phaseRecords': {name: base.S.digest(raw) for name, raw in sorted(markers.items())},
            'originalGpu': original['transaction'], 'currentGpu': current}


def gpu_pair(admissions, statuses, manifests, original, current, config, metadata_pin, cpu, gpu, boot):
    if (not 2 <= len(admissions) <= 16 or set(admissions) != set(statuses) or set(admissions) != set(manifests)
            or original not in admissions or current not in admissions or original == current):
        raise ValueError('Closed bounded original-to-current GPU succession required')
    reference = None
    for name, admitted in admissions.items():
        token(name)
        if (set(admitted) != {'version', 'kind', 'transaction', 'configuration', 'metadataSha256',
                             'guestReceiptSha256', 'dataDevice', 'dataInode'}
                or type(admitted['version']) is not int or admitted['version'] != 1
                or admitted['kind'] != 'original-owned-fresh-gpu' or admitted['transaction'] != name
                or admitted['configuration'] != config or admitted['metadataSha256'] != metadata_pin
                or type(admitted['dataDevice']) is not int or type(admitted['dataInode']) is not int
                or admitted['dataDevice'] < 0 or admitted['dataInode'] < 1
                or not re.fullmatch('[a-f0-9]{64}', admitted['guestReceiptSha256'])):
            raise ValueError('Original immutable same-installation GPU admissions required')
        common = {k: v for k, v in admitted.items() if k != 'transaction'}
        if reference is not None and reference != common:
            raise ValueError('Foreign GPU metadata/data/guest-receipt lineage refused')
        reference = common
        expected = {'version': 1, 'cpu': cpu, 'gpu': gpu, 'beforeLayer': 'absent',
                    'dataDevice': admitted['dataDevice'], 'dataInode': admitted['dataInode']}
        state = statuses[name]
        if (manifests[name] != expected or set(state) != {'boot', 'since', 'stage'}
                or type(state['since']) not in (int, float) or not 0 <= state['since'] < 1e12
                or type(state['boot']) is not str or not re.fullmatch('[a-f0-9-]{36}', state['boot'])
                or state['stage'] != ('ready' if name == current else 'restored')
                or name == current and state['boot'] != boot):
            raise ValueError('All predecessors terminal restored and current boot native-ready required')
    return reference


def awake(output):
    # SDK36 also emits numeric per-display-group fields under the same name.
    values = re.findall(r'^\s*mWakefulness=(\w+)\s*$', output, re.M)
    return [value for value in values if not value.isdecimal()] == ['Awake']


def receipt_generations(records, current):
    # Current ready status is legitimately replaced during later boot recovery.
    # Immutable receipts remain bound to their original inode AND generation.
    mutable = '/var/lib/droiddeck/gpu-transactions/' + token(current) + '/status.json'
    return {name: list(row[3]) for name, row in sorted(records.files.items()) if name != mutable}


def live(records, authority, config):
    trial = load('gpu-fallback-trial')
    original_raw, _files = trial.base_sources()
    for name in ('intent.json', 'complete.json'):
        if records.read(trial.BASE / name) != original_raw:
            raise ValueError('Original protected source publication changed')
    names = os.listdir(records.dirs.open(trial.ADMISSIONS))
    if not 2 <= len(names) <= 16 or any(not re.fullmatch(r'[a-f0-9]{12}\.json', name) for name in names):
        raise ValueError('Only original bounded admission records allowed')
    admissions, statuses, manifests, admission_pins = {}, {}, {}, {}
    module, active, _policy = trial.activation(authority['currentGpu'], trial.BASE)
    G = module.G
    with active.store.lock():
        G.require_quiet(G.unit('droiddeck-install-baseline-20261021gy.service'))
        for name in sorted(names):
            ident = name[:-5]
            raw = records.read(trial.ADMISSIONS / name)
            admissions[ident] = json.loads(raw)
            admission_pins[ident] = G.sha(raw)
            directory = module.TRANSACTIONS / ident
            statuses[ident] = G.validate_status(json.loads(records.read(directory / 'status.json')))
            manifests[ident] = json.loads(records.read(directory / 'manifest.json'))
            G.require_quiet(G.unit('droiddeck-fresh-gpu-worker@' + ident + '.service'))
            if ident != authority['currentGpu']:
                G.require_quiet(G.unit('droiddeck-fresh-gpu-watch@' + ident + '.service'))
        trial.template()
        trial.unit_layout(G.WATCHER, '')
        watchers = json.loads(G.command(['/usr/bin/systemctl', 'list-units', '--all', '--output=json', '--no-pager',
                                        '--state=active', 'droiddeck-fresh-gpu-watch@*.service']).stdout)
        if (len(watchers) != 1 or watchers[0].get('unit') != G.WATCHER
                or watchers[0].get('active') != 'active' or watchers[0].get('sub') != 'running'
                or G.unit(G.WATCHER)['ActiveState'] != 'active'
                or int(G.unit(G.WATCHER)['MainPID']) < 2
                or G.command(['/usr/bin/systemctl', 'is-enabled', G.WATCHER]).stdout != b'enabled\n'):
            raise ValueError('Unique independent enabled original GPU watcher required')
        metadata = records.read(Path('/etc/droiddeck/runtime.json'), mode=0o644, maximum=16384)
        if json.loads(metadata) != config:
            raise ValueError('Original configured runtime metadata required')
        common = gpu_pair(admissions, statuses, manifests, authority['originalGpu'], authority['currentGpu'],
                          config, G.sha(metadata), G.CPU, G.GPU, G.boot_id())
        if not active.native():
            raise ValueError('Actual original native GPU proof required')
        apps = load('default-apps')
        guest = apps.Guest()
        data = str(common['dataDevice']) + ':' + str(common['dataInode'])
        if (guest.command(['/system/bin/stat', '-c', '%d:%i', '/data']) != data
                or guest.inspect().get('defaultsSatisfied') is not True
                or not awake(guest.command(['/system/bin/dumpsys', 'power']))):
            raise ValueError('Original data, publisher Aurora and awake initial display required')
        expected = {'default-media': {'version': 1, 'kind': 'browser-media-default', 'androidData': data,
                                     'stream': 3, 'route': 'remote_submix', 'maximum': 15},
                    'default-lockscreen': {'version': 1, 'kind': 'lockscreen-default', 'androidData': data,
                                          'user': 0, 'sdk': 36}}
        defaults = {}
        for directory, value in expected.items():
            for name in ('intent.json', 'complete.json'):
                path = Path('/var/lib/droiddeck') / directory / name
                raw = records.read(path, maximum=4096)
                if raw != H.encode(value):
                    raise ValueError('Original completed defaults required; no reinitialization')
                defaults[directory + '/' + name] = G.sha(raw)
        # Historical initialization is authoritative; never reset later choices.
        guest.check()
        records.check()
        return {'gpuAdmissions': admission_pins, 'sourcePublicationSha256': G.sha(original_raw),
                'defaults': defaults, 'androidDataIdentity': data}


def certificate(proof):
    if (proof.get('kind') != 'installation-tail-audit' or proof.get('installationCompletionVerified') is not False
            or proof.get('originalInstallationOwnershipVerified') is not True
            or proof.get('completedPhasesVerified') != 31 or proof.get('recoveredTailPhasesVerified') != 4):
        raise ValueError('Fresh original-owned complete tail audit required')
    authority = proof['tailAuthority']
    return {'schema': 1, 'kind': 'verified-installation-tail-recovery',
            'base': proof['base'], 'authoritySha256': hashlib.sha256(H.encode(authority)).hexdigest(),
            'originalGpu': token(authority['originalGpu']), 'currentGpu': token(authority['currentGpu']),
            'originalCompletedPhases': 31, 'recoveredTailPhases': list(TAIL),
            'originalCompletionWritten': False, 'updateAdmissionCreated': False}


def seal(prove):
    first = certificate(prove())
    parent = H.protected(ROOT.parent, directory=True)
    try:
        os.mkdir(ROOT.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
        fd = H.protected(ROOT, directory=True)
        try:
            H.write_new(fd, 'intent.json', H.encode(first))
            if certificate(prove()) != first:
                raise ValueError('Tail authority changed; preserve incomplete recovery intent')
            H.write_new(fd, 'complete.json', H.encode(first))
        finally:
            os.close(fd)
    finally:
        os.close(parent)
    return first


def verify(package):
    base = load('update-base')
    with ExitStack() as stack:
        records = base.Records(stack)
        directory = records.dirs.open(ROOT)
        info = os.fstat(directory)
        if (stat.S_IMODE(info.st_mode) != 0o700
                or set(os.listdir(directory)) != {'intent.json', 'complete.json'}):
            raise ValueError('Complete original recovery certificate required')
        raw = records.read(ROOT / 'intent.json')
        value = base.canonical(raw)
        if records.read(ROOT / 'complete.json') != raw:
            raise ValueError('Uncertain recovery certificate refused')
        proof = base.inspect(package, _tail_token=token(value['currentGpu']))
        if H.encode(certificate(proof)) != raw:
            raise ValueError('Original recovered authority no longer matches')
        records.check()
        proof.pop('tailAuthority')
        return {**proof, 'kind': 'installed-update-base-inspection', 'installationCompletionVerified': True,
                'completionAuthority': 'separate-verified-tail-recovery', 'recoveryCertificateVerified': True,
                'originalCompletionWritten': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    base = load('update-base')
    base.R.A._root_linux()
    if len(args) != 3 or args[0] not in ('audit', 'certify'):
        raise ValueError('audit or certify ABSOLUTE_ORIGINAL_RC2_PACKAGE CURRENT_GPU_TOKEN required')
    current = token(args[2])
    prove = lambda: base.inspect(args[1], _tail_token=current)
    result = prove() if args[0] == 'audit' else seal(prove)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Tail recovery unverified; preserve original phases and all state', file=sys.stderr)
        sys.exit(1)
