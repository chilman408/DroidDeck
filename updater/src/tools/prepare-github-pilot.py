# SPDX-License-Identifier: GPL-3.0-only
"""Audit immutable release inputs, then create a new history-free public tree."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
from urllib.parse import unquote_to_bytes

VERSION = '0.1.0-rc.1'
SOURCE = 'f04b0cae18894fe8e217938d13f7b32c24148177'
CANDIDATE = 'ec5e233a186b2dc060c73bd2c974c37d5529992d245e63c621cadec3c553c929'
FILES = {'COPYING', 'INSTALL.md', 'SHA256SUMS', 'release.json', 'droiddeck-' + VERSION + '-source.tar'}
MAX_FILE = 1024 * 1024
GATES = {'freshInstall', 'sourceReproducible', 'tls', 'browserDesktop', 'browserMobile',
         'media', 'gpu', 'defaults', 'rebootBeforeLogin', 'recovery', 'nativeTouchAfterIdle', 'roleHandoff'}
PATTERNS = {
    'private-key-block': rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s*[A-Za-z0-9+/=]{16,}',
    'provider-access-token': rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b',
}
PRIVATE_NAMES = {'.env', '.netrc', '.git-credentials', 'id_rsa', 'id_ed25519', 'key.pem',
                 'credentials.json', 'known_hosts', 'authorized_keys'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, maximum=20 * 1024 * 1024):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
        raise ValueError('Bounded single-link regular publication input required')
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise ValueError('Publication input changed')
    return raw


def fingerprints(values):
    result = {}
    for value in values:
        match = re.fullmatch(r'([1-9][0-9]{0,2}):([a-f0-9]{64})', value)
        if not match or not 8 <= int(match[1]) <= 256:
            raise ValueError('Credential length:SHA256 required, never plaintext')
        result.setdefault(int(match[1]), set()).add(match[2])
    if sum(map(len, result.values())) < 2:
        raise ValueError('Both previously supplied credential fingerprints required')
    return result


def scan(raw, pins):
    reasons = {name for name, pattern in PATTERNS.items() if re.search(pattern, raw)}
    # Compare fingerprints without storing or reporting the credentials. Also
    # inspect URL/base64 token encodings; findings contain rule names only.
    tokens = set(re.findall(rb'[A-Za-z0-9_+/%=.!@#$^&*()-]{8,4096}', raw))
    candidates = set(tokens)
    for token in tokens:
        if b'%' in token:
            candidates.add(unquote_to_bytes(token))
        if len(token) % 4 == 0 and re.fullmatch(rb'[A-Za-z0-9+/]+={0,2}', token):
            try:
                candidates.add(base64.b64decode(token, validate=True))
            except ValueError:
                pass
    if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        try:
            candidates.add(raw.decode('utf-16').encode('utf-8'))
        except UnicodeError:
            reasons.add('unreadable-unicode')
    for token in candidates:
        for size, expected in pins.items():
            for offset in range(len(token) - size + 1):
                if sha(token[offset:offset + size]) in expected:
                    reasons.add('supplied-administration-credential')
                    break
    return sorted(reasons)


def inspect_release(root):
    if {item.name for item in root.iterdir()} != FILES:
        raise ValueError('Exactly the five accepted release files required')
    files = {name: read(root / name) for name in sorted(FILES)}
    checksum_rows = {}
    for line in files['SHA256SUMS'].decode('ascii').splitlines():
        match = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9.-]+)', line)
        if not match or match[2] in checksum_rows:
            raise ValueError('Unique closed release checksums required')
        checksum_rows[match[2]] = match[1]
    if set(checksum_rows) != FILES - {'SHA256SUMS'} or any(sha(files[name]) != pin for name, pin in checksum_rows.items()):
        raise ValueError('All release checksums must match')
    release = json.loads(files['release.json'])
    evidence = release['acceptance']
    if (release['version'] != VERSION or release['sourceRevision'] != SOURCE
            or release['candidateSha256'] != CANDIDATE or release['installable'] is not True
            or release['candidateUnchangedAfterTesting'] is not True
            or set(evidence['gates']) != GATES or any(value is not True for value in evidence['gates'].values())):
        raise ValueError('Exact accepted pilot release required')
    raw = files['droiddeck-' + VERSION + '-source.tar']
    if sha(raw) != CANDIDATE:
        raise ValueError('Immutable tested candidate required')
    sources = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (not member.isfile() or member.size > MAX_FILE or not path.parts
                    or path.parts[0] != 'droiddeck-source-candidate'
                    or len(path.parts) < 2 or str(path) != member.name
                    or any(part in ('.', '..', '.git') for part in path.parts)
                    or path.is_absolute()):
                raise ValueError('Closed regular source archive members required')
            name = PurePosixPath(*path.parts[1:]).as_posix()
            if name in sources or len(sources) >= 1100:
                raise ValueError('Unique bounded source inventory required')
            sources[name] = archive.extractfile(member).read(MAX_FILE + 1)
            if len(sources[name]) != member.size:
                raise ValueError('Complete source member required')
    manifest = json.loads(sources['package-manifest.json'])
    expected = {row['path']: (row['size'], row['sha256']) for row in manifest['files']}
    if (manifest['sourceRevision'] != SOURCE or len(expected) != len(manifest['files'])
            or set(expected) != set(sources) - {'package-manifest.json'}
            or any((len(sources[name]), sha(sources[name])) != record for name, record in expected.items())):
        raise ValueError('Complete original source manifest must match')
    return files, sources


def prepare(release_root, output, pins, template_root):
    release, sources = inspect_release(release_root)
    public = {'installer/' + name: raw for name, raw in sources.items()}
    public['LICENSE'] = release['COPYING']
    public['docs/INSTALL.md'] = release['INSTALL.md']
    for name in ('README.md', 'SECURITY.md', 'docs/PILOT.md', 'docs/KNOWN_LIMITATIONS.md', '.gitignore'):
        public[name] = read(template_root / name, MAX_FILE)
    findings = []
    for name, raw in {**{'release-assets/' + n: b for n, b in release.items() if not n.endswith('.tar')}, **public}.items():
        rules = scan(raw, pins)
        if PurePosixPath(name).name in PRIVATE_NAMES:
            rules.append('private-file-name')
        if rules:
            findings.append({'path': name, 'rules': rules})
    receipt = {'kind': 'public-pilot-publication-audit', 'version': VERSION, 'sourceRevision': SOURCE,
               'candidateSha256': CANDIDATE, 'publicFiles': len(public), 'archiveMembers': len(sources),
               'knownCredentialFingerprintsChecked': sum(map(len, pins.values())), 'findings': findings,
               'developmentHistoryIncluded': False, 'privateCheckpointsIncluded': False,
               'sourceCandidateModified': False}
    if findings:
        print(json.dumps(receipt, sort_keys=True))
        raise ValueError('Credential/privacy audit refused publication; output not created')
    if output.exists() or output.is_symlink():
        raise ValueError('New publication directory required, never overwrite')
    output.mkdir(mode=0o700)
    for name, raw in sorted(public.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
    print(json.dumps(receipt, sort_keys=True))
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('release', type=Path)
    parser.add_argument('new_output', type=Path)
    parser.add_argument('--credential-sha256', action='append', required=True)
    args = parser.parse_args()
    try:
        prepare(args.release, args.new_output, fingerprints(args.credential_sha256), Path(__file__).with_name('pilot-public'))
    except Exception as error:
        parser.exit(1, 'Pilot preparation stopped: ' + str(error) + '\n')
