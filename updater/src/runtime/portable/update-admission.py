# SPDX-License-Identifier: GPL-3.0-only
"""Signed runtime update admission only; no extraction, execution or activation."""
from contextlib import ExitStack
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile


def load(name):
    spec = importlib.util.spec_from_file_location('admission_' + name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('update-package')
ROOT = Path('/var/lib/droiddeck/update-admissions')
TRUST = Path('/var/lib/droiddeck/update-trust.json')
MACHINE = Path('/etc/machine-id')
SPKI = bytes.fromhex('302a300506032b6570032100')
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/root', 'LANG': 'C', 'LC_ALL': 'C'}
FILES = {'intent.json', 'bundle.tar', 'statement.json', 'signature.bin', 'complete.json'}


def trust(raw):
    value = P.document(raw)
    if (type(value) is not dict or set(value) != {'schema', 'kind', 'channel', 'publicKeySpkiHex'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'droiddeck-update-trust' or value['channel'] != 'pilot'
            or type(value['publicKeySpkiHex']) is not str):
        raise ValueError('Explicit administrator-enrolled pilot release key required')
    key = bytes.fromhex(value['publicKeySpkiHex'])
    if len(key) != 44 or not key.startswith(SPKI) or key.hex() != value['publicKeySpkiHex']:
        raise ValueError('Canonical Ed25519 public SPKI required; never a private key')
    return key


def signature(key, statement, signed):
    if len(signed) != 64 or not 1 <= len(statement) <= 8192:
        raise ValueError('Bounded release statement and Ed25519 signature required')
    # Fixed verifier only. These anonymous files contain PUBLIC metadata, not keys
    # for signing or payload code; retained descriptors prevent path substitution.
    with ExitStack() as stack:
        handles = []
        for raw in (key, statement, signed):
            file = stack.enter_context(tempfile.TemporaryFile())
            file.write(raw)
            file.flush()
            file.seek(0)
            handles.append(file.fileno())
        names = ['/proc/self/fd/' + str(fd) for fd in handles]
        result = subprocess.run(['/usr/bin/openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER',
                                 '-inkey', names[0], '-rawin', '-in', names[1], '-sigfile', names[2]],
                                pass_fds=tuple(handles), env=ENV, cwd='/', stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
        if result.returncode != 0:
            raise ValueError('Release signature did not verify against enrolled key')


def authenticate(bundle, statement, signed, trust_raw):
    key = trust(trust_raw)
    value = P.document(statement)
    required = {'schema', 'kind', 'channel', 'keySha256', 'bundleSha256', 'manifestSha256',
                'base', 'target', 'platform', 'dataPolicy'}
    if (type(value) is not dict or set(value) != required or type(value['schema']) is not int
            or value['schema'] != 1 or value['kind'] != 'droiddeck-runtime-release'
            or value['channel'] != 'pilot' or value['keySha256'] != P.sha(key)
            or value['dataPolicy'] != 'preserve'):
        raise ValueError('Closed domain-separated runtime release statement required')
    signature(key, statement, signed)
    reviewed = P.review(bundle, value['bundleSha256'])
    with tarfile.open(fileobj=io.BytesIO(bundle), mode='r:') as archive:
        manifest_raw = archive.extractfile(P.MANIFEST).read()
    manifest = P.document(manifest_raw)
    if (P.sha(manifest_raw) != value['manifestSha256']
            or any(value[name] != manifest[name] for name in ('base', 'target', 'platform', 'dataPolicy'))):
        raise ValueError('Signed target/base/manifest must match the exact reviewed payload')
    return manifest, reviewed, P.sha(key)


def base_proof(mode, package):
    base = load('update-base')
    if mode == 'completed':
        return base.inspect(package)
    if mode == 'recovered':
        return load('installation-tail-recovery').verify(package)
    raise ValueError('Explicit original completed or separately recovered base required')


def bind(manifest, proof, mode):
    if (proof.get('kind') != 'installed-update-base-inspection' or proof.get('base') != P.BASE
            or proof.get('originalInstallationOwnershipVerified') is not True
            or proof.get('readOnly') is not True or proof.get('servicesChanged') is not False
            or not proof.get('objects') or not proof.get('receiptGenerations')
            or mode == 'completed' and proof.get('completedPhasesVerified') != 35
            or mode == 'recovered' and (proof.get('installationCompletionVerified') is not True
                or proof.get('recoveryCertificateVerified') is not True
                or proof.get('completionAuthority') != 'separate-verified-tail-recovery'
                or proof.get('completedPhasesVerified') != 31 or proof.get('recoveredTailPhasesVerified') != 4)
            or mode not in ('completed', 'recovered')):
        raise ValueError('Fresh original-owned completed base, not an unsealed audit, required')
    components = proof.get('components')
    if (type(components) is not list or len(components) != 3
            or [row['name'] for row in components] != sorted(P.COMPONENTS)
            or any(row['root'] != P.COMPONENTS[row['name']] for row in components)):
        raise ValueError('All original fixed installed component inventories required')
    owned = {row['name']: row['files'] for row in components}
    for row in manifest['components']:
        if P.inventory(row['before']) != P.inventory(owned[row['name']]):
            raise ValueError('Update before inventory differs from original installed code')
    return P.sha(P.encode(proof))


def machine(records):
    info = MACHINE.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if mode not in (0o444, 0o644):
        raise ValueError('Original machine identity required')
    raw = records.read(MACHINE, mode=mode, maximum=128)
    if not re.fullmatch(b'[a-f0-9]{32}\n?', raw):
        raise ValueError('Exact machine identity required')
    return P.sha(raw)


def private_directory(records, path):
    fd = records.dirs.open(path)
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (0, 0, 0o700):
        raise ValueError('Original root-private admission directory required')
    return fd


def report(intent, *, created):
    return {'kind': 'original-owned-update-admission', 'admission': intent['bundleSha256'],
            'base': intent['base'], 'target': intent['target'], 'releaseKeySha256': intent['keySha256'],
            'releaseStatementAuthenticated': True, 'originalInstallationOwnershipVerified': True,
            'durableAdmissionCreated': created, 'readOnly': not created, 'installable': False,
            'targetArtifactsReproduced': False, 'independentRecoveryHandoffVerified': False,
            'readyForApply': False, 'payloadExecuted': False, 'servicesChanged': False,
            'androidDataRead': False, 'tlsPrivateKeyRead': False}


def retained_objects(records, directory):
    result = {}
    for name in sorted(FILES - {'complete.json'}):
        path = directory / name
        records.read(path, maximum=P.MAX_ARCHIVE if name == 'bundle.tar' else 32768)
        result[name] = list(records.files[str(path)][3])
    return result


def admit(mode, package, bundle_path, statement_path, signature_path):
    base = load('update-base')
    base.R.A._root_linux()
    with ExitStack() as stack:
        records = base.Records(stack)
        trust_raw = records.read(TRUST, maximum=4096)
        trust(trust_raw)  # Missing trust fails BEFORE payload reads or journal creation.
        bundle = records.read(bundle_path, maximum=P.MAX_ARCHIVE)
        statement = records.read(statement_path, maximum=8192)
        signed = records.read(signature_path, maximum=64)
        manifest, _review, key = authenticate(bundle, statement, signed, trust_raw)
        first = base_proof(mode, package)
        proof_pin = bind(manifest, first, mode)
        machine_pin = machine(records)
        parent = records.dirs.open(ROOT.parent)
        try:
            os.mkdir(ROOT.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        root = private_directory(records, ROOT)
        import fcntl
        fcntl.flock(root, fcntl.LOCK_EX | fcntl.LOCK_NB)
        name = P.sha(bundle)
        os.mkdir(name, mode=0o700, dir_fd=root)
        os.fsync(root)
        fd = private_directory(records, ROOT / name)
        info = os.fstat(fd)
        intent = {'schema': 1, 'kind': 'signed-original-owned-update-admission',
                  'mode': mode, 'package': str(Path(package)), 'base': manifest['base'], 'target': manifest['target'],
                  'machineSha256': machine_pin, 'baseProofSha256': proof_pin, 'trustSha256': P.sha(trust_raw),
                  'keySha256': key, 'bundleSha256': name, 'statementSha256': P.sha(statement),
                  'signatureSha256': P.sha(signed), 'directoryIdentity': [info.st_dev, info.st_ino]}
        for filename, raw in (('intent.json', P.encode(intent)), ('bundle.tar', bundle),
                              ('statement.json', statement), ('signature.bin', signed)):
            records.check()
            base.H.write_new(fd, filename, raw)
        if bind(manifest, base_proof(mode, package), mode) != proof_pin:
            raise ValueError('Base changed; preserve partial admission and never replay')
        objects = retained_objects(records, ROOT / name)
        records.check()
        base.H.write_new(fd, 'complete.json', P.encode({'schema': 1, 'intentSha256': P.sha(P.encode(intent)),
                                                      'objects': objects}))
        records.check()
        if set(os.listdir(fd)) != FILES:
            raise ValueError('Admission namespace changed before completion report')
        return report(intent, created=True)


def inspect(name, package):
    base = load('update-base')
    base.R.A._root_linux()
    P.digest(name)
    with ExitStack() as stack:
        records = base.Records(stack)
        private_directory(records, ROOT)
        fd = private_directory(records, ROOT / name)
        if set(os.listdir(fd)) != FILES:
            raise ValueError('Completed original admission required; no partial adoption')
        intent_raw = records.read(ROOT / name / 'intent.json', maximum=32768)
        intent = P.document(intent_raw)
        expected_keys = {'schema', 'kind', 'mode', 'package', 'base', 'target', 'machineSha256', 'baseProofSha256',
                         'trustSha256', 'keySha256', 'bundleSha256', 'statementSha256', 'signatureSha256', 'directoryIdentity'}
        info = os.fstat(fd)
        if (type(intent) is not dict or set(intent) != expected_keys or type(intent['schema']) is not int
                or intent['schema'] != 1 or intent['kind'] != 'signed-original-owned-update-admission'
                or intent['directoryIdentity'] != [info.st_dev, info.st_ino] or intent['bundleSha256'] != name
                or intent['package'] != str(Path(package)) or intent['machineSha256'] != machine(records)):
            raise ValueError('Original machine/package/admission identity required')
        trust_raw = records.read(TRUST, maximum=4096)
        bundle = records.read(ROOT / name / 'bundle.tar', maximum=P.MAX_ARCHIVE)
        statement = records.read(ROOT / name / 'statement.json', maximum=8192)
        signed = records.read(ROOT / name / 'signature.bin', maximum=64)
        if (P.sha(trust_raw) != intent['trustSha256'] or P.sha(bundle) != name
                or P.sha(statement) != intent['statementSha256'] or P.sha(signed) != intent['signatureSha256']
                or records.read(ROOT / name / 'complete.json') != P.encode({'schema': 1, 'intentSha256': P.sha(intent_raw),
                    'objects': retained_objects(records, ROOT / name)})):
            raise ValueError('Original admission payload/trust/completion required')
        manifest, _review, key = authenticate(bundle, statement, signed, trust_raw)
        if (manifest['base'] != intent['base'] or manifest['target'] != intent['target'] or key != intent['keySha256']
                or bind(manifest, base_proof(intent['mode'], package), intent['mode']) != intent['baseProofSha256']):
            raise ValueError('Installed base or authenticated target changed')
        records.check()
        if set(os.listdir(fd)) != FILES:
            raise ValueError('Admission namespace changed during inspection')
        return report(intent, created=False)


def main(args=None):
    args = sys.argv[1:] if args is None else args
    if len(args) == 6 and args[0] == 'admit' and args[1] in ('completed', 'recovered'):
        result = admit(*args[1:])
    elif len(args) == 3 and args[0] == 'inspect':
        result = inspect(*args[1:])
    else:
        raise ValueError('admit completed|recovered PACKAGE BUNDLE STATEMENT SIGNATURE or inspect ADMISSION PACKAGE')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Update admission refused; preserve original state and partial admissions', file=sys.stderr)
        sys.exit(1)
