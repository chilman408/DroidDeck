# SPDX-License-Identifier: GPL-3.0-only
"""Promote immutable tested source bytes with explicit, bounded acceptance evidence."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('release_source_builder', Path(__file__).with_name('release-package.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)
VERSION = '0.1.0-rc.1'
LICENSE_SHA = '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986'
GATES = ('freshInstall', 'sourceReproducible', 'tls', 'browserDesktop', 'browserMobile',
         'media', 'gpu', 'defaults', 'rebootBeforeLogin', 'recovery', 'nativeTouchAfterIdle', 'roleHandoff')
FIELDS = {'version', 'sourceRevision', 'candidateSha256', 'freshBootId', 'rebootBootId',
          'phaseCount', 'idleSeconds', 'videoFrames', 'toneHz', 'gates', 'verificationCheckpoint'}
INSTALL = b'''# DroidDeck 0.1.0-rc.1

Initial Ubuntu/Unraid installation release candidate. Not a signed binary release
or an in-place upgrader. Tested environment: fresh Ubuntu26.04.1 x86_64, first
regular user UID1000, Unraid KVM with working VirGL3D,128GiB disk and6GiB RAM.
GPU mode-access preflight and real Android GPU verification are mandatory.

## Install

Use a CLEAN disposable Ubuntu VM and an existing regular administrative user.
Existing Android, DroidDeck, installation journals or private preferences refuse
installation. Do not run this on a working preview or populated VM.

Verify SHA256SUMS obtained through a trusted release channel BEFORE root
extraction. Checksums alone do not authenticate their own download source.
The archive contains source only; external images/APKs/SDK tools/libraries are
downloaded directly from pinned publisher locations, then checked. Two complete
native/Java/library builds must match. No signed-in data, DRM or private TLS keys
are included. Community images are not bundled or mirrored.

```sh
sha256sum -c SHA256SUMS
sudo mkdir -m755 /usr/local/share/droiddeck-0.1.0-rc.1
sudo tar --no-same-owner --no-same-permissions -xf droiddeck-0.1.0-rc.1-source.tar -C /usr/local/share/droiddeck-0.1.0-rc.1
sudo python3 -I -B /usr/local/share/droiddeck-0.1.0-rc.1/droiddeck-source-candidate/droiddeck-install --install-machine YOUR_UBUNTU_USER YOUR_VM_IPV4 droiddeck.invalid
```

Use the VM's actual private LAN IPv4 address. A fixed/reserved address is needed
for its HTTPS identity. The command prints each installation phase. Normal sudo
authentication is required; no passwordless policy is installed. Do not close a
foreground SSH install. For unattended SSH operation, launch that same command
with systemd-run Type=exec/UMask=0077 and monitor its journal. Do not enable the
temporary installer unit at boot. Installation starts the completed services;
only the admitted GPU watcher is enabled to coordinate automatic boot startup.

## Connect

Open https://YOUR_VM_IPV4:8443/ after installation completes. A new private TLS
pair is generated on that VM. Explicitly trust its PUBLIC certificate on each
client, or use an authenticated trusted reverse proxy. Never export key.pem.
The public certificate is ~/.config/droiddeck/cert.pem for the selected user;
verify its fingerprint through your authenticated VM connection before trusting.
This package does not install global browser/OS trust or bypass TLS verification.

Aurora Store is required and installed only if missing. Initial browser-route
media volume is100%; initial lockscreen is None. Later volume/security choices
are preserved. Optional browser login defaults OFF: keep8443 on a trusted LAN
and configure supported authentication before any external exposure. Do not use
Aurora to replace the image's Google Play Services. No account, certification or
DRM spoofing is performed. Software codec4/RGBX/AAC decoding is intentional;
Android rendering remains GPU-accelerated. Legacy H264 and audio sync are kept.

## Recovery and Scope

Never rerun the whole command on an installed or partially installed target.
Preserve /root/droiddeck-installation and all original component journals under
/var/lib/droiddeck*. Started-without-complete markers identify uncertain phases.
Use the original component's explicit recovery operation after inspection, not
force/adoption/deletion of partial state. GPU activation tokens are single-use.
For a failed initial trial, retain its disk offline and use a new clean OS disk.
This is not a universal in-place upgrade or automatic pre-intent repair tool.

The source TAR is the exact immutable candidate that was tested. Its internal
build-time candidate warnings/false release flags are intentionally frozen, not
rewritten after testing. release.json carries the post-test acceptance for THESE
exact bytes. Acceptance is an engineering attestation, not a digital signature
or a claim of support for every GPU, Ubuntu version, app/account or DRM service.
Apple TV and the deferred Edge issue are outside this release's acceptance.
No healthy-service testing expiry or PNG video-transport fallback is installed.

## Source and Licenses

DroidDeck source headers identify GPL-3.0-only; COPYING supplies the unchanged GNU
GPLv3 text from https://www.gnu.org/licenses/gpl-3.0.txt. Upstream components keep
their own licenses. Installation fetches their publisher sources and notices;
this source distribution does not relicense their images, APKs or dependencies.
Project names identify integrations only; no third-party endorsement, account
compatibility, certification or redistribution clearance is asserted.
'''


def unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate acceptance fields refused')
        result[name] = value
    return result


def acceptance(raw, candidate_sha, source_revision):
    if type(raw) is not bytes or not 0 < len(raw) <= 16384:
        raise ValueError('Bounded public acceptance record required')
    value = json.loads(raw, object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != FIELDS or value['version'] != VERSION
            or value['sourceRevision'] != source_revision or value['candidateSha256'] != candidate_sha
            or type(value['gates']) is not dict or set(value['gates']) != set(GATES)
            or any(value['gates'][gate] is not True for gate in GATES)):
        raise ValueError('Complete exact-candidate acceptance required; no unchecked gates')
    uuid = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
    if (not all(type(value[name]) is str and re.fullmatch(uuid, value[name]) for name in ('freshBootId', 'rebootBootId'))
            or value['freshBootId'] == value['rebootBootId']
            or type(value['phaseCount']) is not int or value['phaseCount'] != 35
            or type(value['idleSeconds']) is not int or not 75 <= value['idleSeconds'] <= 3600
            or type(value['videoFrames']) is not int or not 8 <= value['videoFrames'] <= 1000000
            or type(value['toneHz']) not in (int, float) or not 950 < value['toneHz'] < 1050
            or type(value['verificationCheckpoint']) is not str
            or not re.fullmatch(r'docs/checkpoints/[a-z0-9-]{1,100}\.md', value['verificationCheckpoint'])):
        raise ValueError('Exact bounded non-private acceptance observations required')
    return value


def payloads(candidate, source_receipt, evidence, license_raw):
    if hashlib.sha256(license_raw).hexdigest() != LICENSE_SHA:
        raise ValueError('Unchanged independently pinned GNU GPLv3 text required')
    value = acceptance(evidence, B.sha(candidate), source_receipt['sourceRevision'])
    files = {'droiddeck-' + VERSION + '-source.tar': candidate, 'INSTALL.md': INSTALL, 'COPYING': license_raw}
    release = {'kind': 'accepted-source-release-candidate', 'version': VERSION,
               'sourceRevision': source_receipt['sourceRevision'], 'candidateSha256': B.sha(candidate),
               'candidateUnchangedAfterTesting': True, 'digitallySigned': False,
               'installable': True, 'wholeInstallCommandAvailable': True, 'acceptance': value,
               'files': [{'path': name, 'size': len(raw), 'sha256': B.sha(raw)} for name, raw in sorted(files.items())]}
    files['release.json'] = B.encode(release)
    files['SHA256SUMS'] = ''.join(B.sha(raw) + '  ' + name + '\n' for name, raw in sorted(files.items())).encode('ascii')
    return files


def main(args):
    if len(args) != 4 or sys.platform == 'linux' and os.geteuid() == 0:
        raise ValueError('Unprivileged REPO CANDIDATE ACCEPTANCE_JSON NEW_RELEASE_DIRECTORY required')
    candidate = B.read_archive(args[1])
    sources = B.read_sources(args[0])
    supplementary = B.read_install_sources(args[0], sources)
    reconstructed, receipt = B.render({**sources, **supplementary}, B.SOURCE_REVISION)
    if reconstructed != candidate:
        raise ValueError('Independent exact pinned candidate reconstruction required')
    evidence = B.read_archive(args[2])
    # Git's Windows text checkout may use CRLF. The artifact must still match
    # the exact independently pinned publisher LF bytes.
    license_raw = B.read_archive(str(Path(__file__).with_name('gpl-3.0.txt').resolve())).replace(b'\r\n', b'\n')
    files = payloads(candidate, receipt, evidence, license_raw)
    output = B.absolute(args[3])
    output.mkdir(mode=0o700)
    for name, raw in sorted(files.items()):
        B.publish(str(output / name), raw)
    print(json.dumps({'version': VERSION, 'directory': str(output), 'files': len(files),
                      'candidateSha256': B.sha(candidate), 'candidateUnchangedAfterTesting': True}, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception as error:
        print('Release publication refused; preserve any partial output: ' + str(error), file=sys.stderr)
        sys.exit(1)
