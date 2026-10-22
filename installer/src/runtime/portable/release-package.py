# SPDX-License-Identifier: GPL-3.0-only
"""Deterministic pinned source-candidate packaging, not a complete installer."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile

SOURCE_REVISION = 'f04b0cae18894fe8e217938d13f7b32c24148177'
PREFIXES = ('runtime/', 'tools/', 'build/', 'config/', 'tests/native/', 'tests/java/')
SUFFIXES = {'.bp', '.c', '.conf', '.cpp', '.example', '.gitignore', '.h', '.java', '.js',
            '.json', '.md', '.pam', '.patch', '.py', '.service', '.sh', '.txt', '.xml'}
ASSETS = {'runtime/branding/droiddeck-logo.png', 'runtime/branding/donations-qr.svg', 'build/Containerfile'}
REQUIRED = {'runtime/portable/install-registration.py', 'runtime/portable/installation-defaults.py',
            'runtime/portable/host-prerequisites.py',
            'runtime/portable/prepare-user-session.py',
            'runtime/portable/stage-installer-sources.py',
            'runtime/portable/install-machine.py', 'runtime/portable/prime-installation-media.js',
            'runtime/portable/acquire-inputs.py', 'runtime/portable/prepare-install.js',
            'runtime/portable/publish-install-inputs.py',
            'runtime/portable/install-runtime.py', 'runtime/portable/setup-local-tls.py',
            'runtime/portable/local-tls-policy.js',
            'runtime/portable/default-lockscreen.py', 'runtime/portable/default-media.py',
            'runtime/portable/default-apps.py', 'runtime/portable/artifact-build.js',
            'config/portable-image-inputs.json', 'tests/java/GpsBridgeReflectionTest.java',
            'tests/java/LogicalCaptureValidationTest.java', 'tests/native/test_api.cpp',
            'tests/native/test_binder_library.c', 'tests/native/test_display_capture.c',
            'tests/native/display_capture_fixture.c', 'tests/native/test_touch_protocol.c',
            'tests/native/test_touch_mapping.c', 'tests/native/test_touch_keepalive.c'}
MAX_FILE, MAX_TOTAL, MAX_ARCHIVE = 1024 * 1024, 16 * 1024 * 1024, 20 * 1024 * 1024
ROOT = 'droiddeck-source-candidate/'
INSTALL_INPUTS = 'build/installation-inputs/'
GATES = ['signed-host-prerequisites-and-persistent-user-session',
         'publisher-images', 'native-java-library-builds', 'offline-registration',
         'browser-compatibility-and-tls', 'live-guest-and-gpu', 'installation-defaults',
         'fresh-package-browser-media-reboot-and-reconnect-acceptance']
LAUNCHER = br'''#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import sys

args = sys.argv[1:]
base = Path(__file__).resolve().parent / 'src/runtime/portable'
binary, flags = '/usr/bin/python3', ['-I', '-B']
if args in ([mode] for mode in ('--install-host', '--resume-host', '--inspect-host')):
    script = 'host-prerequisites.py'
elif len(args) == 2 and args[0] in ('--prepare-user-session', '--resume-user-session', '--inspect-user-session'):
    script = 'prepare-user-session.py'
elif args in ([mode] for mode in ('--stage-installer-sources', '--resume-installer-sources', '--inspect-installer-sources',
                                '--stage-gpu-sources', '--resume-gpu-sources', '--inspect-gpu-sources')):
    script = 'stage-installer-sources.py'
elif len(args) == 4 and args[0] == '--install-machine':
    script = 'install-machine.py'
elif len(args) == 5 and args[0] in ('--install-registration', '--resume-registration') and args[-1] == '-':
    script = 'install-registration.py'
elif len(args) == 2 and args[0] in ('--acquire-build-inputs', '--acquire-image-inputs'):
    script = 'acquire-inputs.py'
elif len(args) == 4 and args[0] in ('--publish-install-inputs', '--verify-install-inputs'):
    script = 'publish-install-inputs.py'
elif len(args) == 3 and args[0] == '--prepare-install':
    binary, flags = '/usr/bin/node', []
    script, args = 'prepare-install.js', args[1:]
elif len(args) == 7 and args[0] in ('--install-runtime', '--resume-runtime', '--verify-runtime') and args[-1] == '-':
    script = 'install-runtime.py'
elif len(args) == 1 and args[0] in ('--initialize-local-tls', '--resume-local-tls', '--inspect-local-tls'):
    script = 'setup-local-tls.py'
elif len(args) == 3 and args[0] == '--reproduce-artifacts':
    binary, flags = '/usr/bin/node', []
    script, args = 'artifact-build.js', ['reproduce', *args[1:]]
elif args == ['--inspect-defaults']:
    script, args = 'installation-defaults.py', ['--inspect']
elif len(args) == 2 and args[0] == '--initialize-defaults':
    script, args = 'installation-defaults.py', ['--initialize', args[1]]
elif args == ['--help']:
    print('DroidDeck source candidate, not a complete installer.\n'
          'Signed Ubuntu26.04 host setup (root): --install-host|--resume-host|--inspect-host\n'
          'Persistent user audio/video session (root): --prepare-user-session|--resume-user-session|--inspect-user-session USER\n'
          'Protected recipe/GPU sources (root): --stage-installer-sources|--resume-installer-sources|--inspect-installer-sources\n'
          'Late GPU recovery sources (root, AFTER registration/extensions): --stage-gpu-sources|--resume-gpu-sources|--inspect-gpu-sources\n'
          'Clean machine CANDIDATE (root): --install-machine USER PRIVATE_IPV4 HOSTNAME\n'
          'Registration: --install-registration USER ASSEMBLY RECIPES -\n'
          'Recovery: --resume-registration USER ASSEMBLY RECIPES -\n'
          'Offline runtime: --install-runtime|--resume-runtime|--verify-runtime USER ASSEMBLY RECIPES FRONTEND COMPAT_SOURCE -\n'
          'Local HTTPS (configured user, not root): --initialize-local-tls|--resume-local-tls|--inspect-local-tls\n'
          'Build twice (unprivileged): --reproduce-artifacts PUBLISHER_INPUTS_JSON NEW_OUTPUT\n'
          'Acquire (unprivileged): --acquire-build-inputs|--acquire-image-inputs NEW_OUTPUT\n'
          'Prepare (unprivileged): --prepare-install ACQUIRED_BUILD_INPUTS NEW_OUTPUT\n'
          'Protected assemblies (root): --publish-install-inputs|--verify-install-inputs USER PREPARED_INPUTS ROOT_PRIVATE_OUTPUT\n'
          'Defaults: --inspect-defaults or --initialize-defaults PUBLISHER_APK')
    sys.exit(0)
else:
    print('Explicit supported installation phase required; no changes performed', file=sys.stderr)
    sys.exit(1)
os.execve(binary, [binary, *flags, str(base / script), *args],
          {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
           'JAVA_HOME': '/usr/lib/jvm/java-17-openjdk-amd64'})
'''
GUIDE = b'''DroidDeck source candidate (NOT a released whole-machine installer)

This archive packages pinned committed sources and supported phase entrypoints.
The clean-machine entrypoint composes the currently implemented phases. It is
an installation CANDIDATE until that exact package passes a fresh whole trial.
It refuses existing Android/DroidDeck state, creates exclusive workspaces,
and records each started/completed operation. It never guesses a replay after
failure: retain all partial state and use the original component recovery CLI.
Do not rerun one-time initialization, registration or GPU activation. A failed
pre-intent or uncertain publication requires explicit inspection, not adoption.
The finite unprivileged media primer verifies the new local HTTPS certificate
and drains real H264/AAC sockets so required defaults need no human browser
gesture. It records no media and changes no browser/global certificate trust.
Host setup is restricted to clean Ubuntu26.04/x86_64. It installs signed APT
build/media/Waydroid prerequisites, never requests a full upgrade or removal,
and refuses an installed Android/DroidDeck target. The publisher repository
script must match its independent SHA256 before root execution. Explicit resume
uses the original machine/source intent and retries only unrecorded APT phases;
standard APT/dpkg recovery remains authoritative. Inspect never installs packages.
This phase does not initialize Android, install DroidDeck or configure TLS.
The selected user's PipeWire sockets and WirePlumber must be enabled and active,
including on a server image without a desktop login. The user-session phase
uses native logind/systemd operations to enable linger and those three fixed
distribution units. Completed inspection/resume never re-enables or restarts
them. No user group, sudo, account password or Android changes are made.
The protected-source phase checks the complete package manifest and independent
recipe/compatibility/GPU pins before publishing private copies outside /opt.
Pre-registration sources live under /root/droiddeck-installer-sources, NOT the
strict image/profile preparation namespace. Fixed /var/lib/droiddeck/gpu-recovery
is published separately ONLY after registration and runtime ownership extensions.
It refuses an existing output without its original journal, unknown files,
partial/changed bytes, links and writable ancestors. Resume retains original
inodes; completed resume/inspection never rewrites sources or starts services.
It includes exact historical base/guard/owned-recipe SOURCE inputs under
src/build/installation-inputs, not a Git history cache or installed machine data.
The narrow native/Java test source directories are included because all six
builders must execute their tests, not omit validation in a release build.
It contains no community images, downloaded APKs, native/Java build outputs,
signed-in Android data, credentials, DRM material or TLS private keys.
The approved DroidDeck logo is an owned source asset, not an Android image.

Verify against the independently pinned source revision BEFORE extraction.
Extract into a NEW private directory with umask 077; never reuse old partial
trees or extract directly over an installed system.
Never sudo an unverified or user-writable source tree. Existing privileged
phases require protected root-owned inputs, original journals and inactive
workers. Registration belongs BEFORE frontend/compatibility extensions.
Live guest helper publication also belongs BEFORE frontend/compatibility;
it requires the original closed inventory and controlled container setup.
After original registration/guest publication, stop workers and use the
runtime RESUME phase to add frontend/compatibility. Do not replay the original
base-only verifier after compatibility has displaced the original adapter.
Do not run installation phases against the populated working preview.

python3 droiddeck-install --help lists ONLY implemented composed phases.
Registration consumes separately prepared protected ASSEMBLY/RECIPES/config.
Artifact reproduction consumes separately acquired pinned publisher inputs;
it makes TWO fresh builds and compares actual payload bytes/receipt digests.
It does not fetch inputs, install prerequisites or install its built outputs.
Acquisition fetches only fixed direct publisher URLs and Git revisions into a
NEW private workspace; downloads must match independent pins. Failed/partial
workspaces are preserved, never adopted or overwritten; use a new directory.
Preparation uses packaged source recipes, locked script-free production npm,
the pinned frontend and two complete artifact builds. It does not need a local
DroidDeck Git checkout. Its output is NOT installed or activated by that command.
Protected assembly publication composes original candidate/dependency/artifact
validators into NEW root-private parents. Completed verification is read-only;
uncertain partial publication refuses adoption or automatic overwrite.
Offline runtime additionally consumes pinned FRONTEND and protected COMPAT_SOURCE.
Runtime verification is read-only and can inspect an active owned installation.
Local HTTPS runs as the configured user, consumes protected runtime configuration,
and generates a NEW private pair. It NEVER replaces/adopts existing keys. Explicit
resume preserves the original pair across interruption. It does NOT configure
browser trust: explicitly trust its public certificate on each client or use
a trusted reverse proxy. No private-key export command is provided.
Defaults run only after the browser capture route is active. Existing apps,
later volume choices and later security choices are preserved.
Fetch community images/APKs from their publisher; do not bundle or mirror them.

Original per-phase journals remain authoritative. Resume never adopts or
overwrites foreign/partial pre-intent files. Preserve uncertain state.
The candidate clean command composes ownership extensions and fresh activation,
but exact whole-command clean/reboot/recovery acceptance is still required.
The wholeInstallCommandAvailable release flag stays false until that acceptance;
the experimental command is explicitly labeled CANDIDATE in --help.
Client certificate trust is never configured automatically.
Passing package verification is NOT full fresh-install/release acceptance.
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')


def source_name(name):
    path = PurePosixPath(name)
    if (not re.fullmatch(r'[A-Za-z0-9_./@+-]{1,200}', name)
            or not name.startswith(PREFIXES) or str(path) != name
            or any(part in ('.', '..') or len(part) > 90 for part in path.parts)
            or name not in ASSETS and path.suffix not in SUFFIXES and path.name != '.gitignore'):
        raise ValueError('Closed source-only package path required')
    return name


def git(repo, args, *, stdin=None):
    result = subprocess.run(['git', '--no-pager', '--no-replace-objects', '--no-lazy-fetch',
        '-c', 'core.fsmonitor=false', '-C', str(Path(repo).resolve()), *args], input=stdin,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'})
    if len(result.stdout) > MAX_ARCHIVE * 2 or len(result.stderr) > MAX_FILE:
        raise ValueError('Bounded Git source input required')
    return result.stdout


def read_sources(repo):
    if not re.fullmatch('[0-9a-f]{40}', SOURCE_REVISION):
        raise ValueError('Reviewed fixed source revision required')
    commit = git(repo, ['cat-file', 'commit', SOURCE_REVISION])
    if hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest() != SOURCE_REVISION:
        raise ValueError('Pinned commit object required')
    listing = git(repo, ['ls-tree', '-rz', '--long', SOURCE_REVISION, '--', *(p[:-1] for p in PREFIXES)])
    entries = []
    for row in listing.split(b'\0'):
        if not row:
            continue
        match = re.fullmatch(rb'(100644|100755) blob ([0-9a-f]{40}) +([0-9]+)\t(.+)', row)
        if not match:
            raise ValueError('Regular committed source blobs only; no links/submodules')
        _, object_id, size, name = match.groups()
        name, size = source_name(name.decode('ascii')), int(size)
        if not 0 <= size <= MAX_FILE:
            raise ValueError('Bounded source files required')
        entries.append((name, object_id, size))
    if (not 1 <= len(entries) <= 1024 or sum(size for _, _, size in entries) > MAX_TOTAL
            or len({name.casefold() for name, _, _ in entries}) != len(entries)):
        raise ValueError('Bounded collision-free source inventory required')
    data = git(repo, ['cat-file', '--batch'], stdin=b''.join(obj + b'\n' for _, obj, _ in entries))
    files, offset = {}, 0
    for name, object_id, size in entries:
        header = object_id + b' blob ' + str(size).encode() + b'\n'
        if data[offset:offset + len(header)] != header:
            raise ValueError('Original Git blob header required')
        offset += len(header)
        raw = data[offset:offset + size]
        offset += size
        if data[offset:offset + 1] != b'\n' or hashlib.sha1(b'blob ' + str(size).encode() + b'\0' + raw).hexdigest().encode() != object_id:
            raise ValueError('Original bounded Git blob bytes required')
        offset += 1
        files[name] = raw
    if offset != len(data):
        raise ValueError('No extra Git source output allowed')
    return files


def read_install_sources(repo, files):
    # Regenerate reviewed historical SOURCE recipes at packaging time. No old
    # repository, installed payload, downloaded dependency or VM input is shipped.
    selected = ('runtime/portable/runtime-bundle.js', 'runtime/portable/runtime-candidate.js',
                'runtime/portable/runtime_candidate_inputs.js', 'runtime/portable/read-bounded.js')
    if not all(name in files for name in (*selected, 'runtime/portable/runtime-owned-install-plan.py')):
        raise ValueError('Complete pinned installation recipe generators required')
    result = {}
    with tempfile.TemporaryDirectory(prefix='droiddeck-source-recipes-') as temporary:
        root = Path(temporary)
        for name in selected:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(files[name])
        output = root / 'inputs'
        child = subprocess.run(['node', str(root / selected[2]), '--prepare', str(Path(repo).resolve()), str(output)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True,
            env={**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': '', 'GIT_TERMINAL_PROMPT': '0'})
        if len(child.stdout) > 4096 or len(child.stderr) > 16384:
            raise ValueError('Bounded fixed source recipe output required')
        for path in sorted(output.rglob('*')):
            if path.is_dir():
                continue
            if not path.is_file() or path.is_symlink():
                raise ValueError('Regular generated installation sources required')
            name = source_name(INSTALL_INPUTS + path.relative_to(output).as_posix())
            raw = path.read_bytes()
            if not 0 < len(raw) <= MAX_FILE:
                raise ValueError('Bounded generated installation source required')
            result[name] = raw
    tree = ast.parse(files['runtime/portable/runtime-owned-install-plan.py'])
    assignments = {node.targets[0].id: node.value for node in tree.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)}
    revision = ast.literal_eval(assignments['REVISION'])
    expression = assignments['PINS']
    if (not re.fullmatch('[0-9a-f]{40}', revision) or not isinstance(expression, ast.Call)
            or not isinstance(expression.func, ast.Name) or expression.func.id != 'MappingProxyType'
            or len(expression.args) != 1 or expression.keywords):
        raise ValueError('Closed literal owned source recipe required')
    pins = ast.literal_eval(expression.args[0])
    if type(pins) is not dict or len(pins) != 15:
        raise ValueError('Exactly fifteen reviewed owned recipe sources required')
    for name, (size, digest) in sorted(pins.items()):
        source_name(name)
        raw = git(repo, ['cat-file', 'blob', revision + ':' + name])
        if len(raw) != size or sha(raw) != digest:
            raise ValueError('Original independently pinned historical recipe source required')
        result[INSTALL_INPUTS + 'recipes/' + name] = raw
    result[INSTALL_INPUTS + 'recipes/bundle-manifest.json'] = encode({
        'version': 1, 'product': 'DroidDeck', 'kind': 'owned-install-recipe-source',
        'sourceRevision': revision, 'installable': False,
        'files': [{'path': name, 'size': size, 'sha256': digest} for name, (size, digest) in sorted(pins.items())]})
    if len(result) != 96:
        raise ValueError('Closed original base/guard/recipe source inventory required')
    return result


def render(files, revision):
    if not re.fullmatch('[0-9a-f]{40}', revision) or not REQUIRED.issubset(files):
        raise ValueError('Fixed complete source inventory required')
    if not 1 <= len(files) <= 1024 or sum(map(len, files.values())) > MAX_TOTAL:
        raise ValueError('Bounded source candidate required')
    for name, raw in files.items():
        source_name(name)
        if type(raw) is not bytes or not 0 <= len(raw) <= MAX_FILE:
            raise ValueError('Bounded exact source bytes required')
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError('Case-insensitive source collisions refused')
    payloads = {'README.txt': GUIDE, 'droiddeck-install': LAUNCHER,
                **{'src/' + name: raw for name, raw in files.items()}}
    manifest = encode({'version': 1, 'product': 'DroidDeck', 'kind': 'source-release-candidate',
        'sourceRevision': revision, 'installable': False, 'wholeInstallCommandAvailable': False,
        'servicesActivated': False, 'requiredGates': GATES,
        'files': [{'path': name, 'size': len(raw), 'sha256': sha(raw)} for name, raw in sorted(payloads.items())]})
    payloads['package-manifest.json'] = manifest
    output = io.BytesIO()
    # Canonical tar avoids zlib vs zlib-ng compressed-byte differences between
    # supported build hosts; verification compares the actual distribution bytes.
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, raw in sorted(payloads.items()):
            entry = tarfile.TarInfo(ROOT + name)
            entry.size, entry.mode, entry.mtime = len(raw), 0o755 if name == 'droiddeck-install' else 0o644, 0
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ''
            archive.addfile(entry, io.BytesIO(raw))
    raw = output.getvalue()
    if len(raw) > MAX_ARCHIVE:
        raise ValueError('Bounded candidate archive required')
    return raw, {'kind': 'source-release-candidate', 'sourceRevision': revision, 'sourceFiles': len(files),
                 'archiveSha256': sha(raw), 'manifestSha256': sha(manifest), 'installable': False,
                 'wholeInstallCommandAvailable': False, 'servicesActivated': False}


def absolute(filename):
    path = Path(filename)
    if not path.is_absolute() or str(path) != str(path.resolve()):
        raise ValueError('Canonical absolute archive path required')
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError('Real archive parent directories required')
    return path


def publish(filename, raw):
    path = absolute(filename)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as output:
            output.write(raw)
            output.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    if sys.platform == 'linux':
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_archive(filename):
    path = absolute(filename)
    named_before = path.lstat()
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                 | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= MAX_ARCHIVE:
            raise ValueError('Bounded single-link regular candidate archive required')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(MAX_ARCHIVE + 1)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        signature = lambda info: tuple(getattr(info, key) for key in fields)
        # Windows fstat/stat can expose different ctime semantics. Compare each
        # view to its own initial generation; common file identity must agree too.
        common = lambda info: tuple(getattr(info, key) for key in fields[:-1])
        if (len(raw) != before.st_size or signature(os.fstat(fd)) != signature(before)
                or common(before) != common(named_before)
                or signature(path.lstat()) != signature(named_before)):
            raise ValueError('Original archive generation required')
        return raw
    finally:
        os.close(fd)


def main(args):
    if len(args) != 3 or args[0] not in ('build', 'verify'):
        raise ValueError('Use build REPO NEW_ARCHIVE or verify REPO ARCHIVE; no revision override')
    files = read_sources(args[1])
    supplementary = read_install_sources(args[1], files)
    if set(files) & set(supplementary):
        raise ValueError('Generated installation source collisions refused')
    raw, receipt = render({**files, **supplementary}, SOURCE_REVISION)
    if args[0] == 'build':
        publish(args[2], raw)
    if read_archive(args[2]) != raw:
        raise ValueError('Archive differs from independent pinned-source reconstruction')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
    except Exception:
        print('Source candidate packaging refused; preserve any output, no installation performed', file=sys.stderr)
        sys.exit(1)
