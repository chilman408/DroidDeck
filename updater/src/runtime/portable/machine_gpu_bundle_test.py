# SPDX-License-Identifier: GPL-3.0-only
"""Generated root-private payloads only; private mount namespace, no service actions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).parent
BUNDLE = HERE / 'machine-gpu-bundle.js'
TARGET = Path('/opt/droiddeck')


def run_node(mode, directory):
    node = shutil.which('node')
    if not node:
        raise ValueError('Native Node required')
    if mode == 'build':
        return subprocess.run([node, '-e', 'require(process.argv[1]).buildMachineGpuBundle({output:process.argv[2]})',
            str(BUNDLE), str(directory)], check=True, capture_output=True, text=True, timeout=15)
    return subprocess.run([node, str(BUNDLE), 'verify', str(directory)], check=True,
        capture_output=True, text=True, timeout=15)


def namespace_child(directory, original_namespace):
    parent_namespace = os.readlink('/proc/' + str(os.getppid()) + '/ns/mnt')
    if os.geteuid() != 0 or parent_namespace != original_namespace or os.readlink('/proc/self/ns/mnt') == parent_namespace:
        raise ValueError('Independent root-private mount namespace required')
    mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    if any(any(v.startswith(('shared:', 'master:')) for v in mount[6:mount.index('-')]) for mount in mounts):
        raise ValueError('Private mount propagation required')
    directory = Path(directory)
    if (not directory.is_absolute() or str(directory) != os.path.abspath(directory) or len(directory.parts) != 4
            or directory.parts[1] != 'root' or not directory.parent.name.startswith('droiddeck-source-payload-') or directory.name != 'payload'):
        raise ValueError('Generated root-private fixture required')
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for path in [Path(parent), *(Path(parent) / name for name in dirs + files)]:
            info = path.lstat()
            expected = 0o700 if stat.S_ISDIR(info.st_mode) else 0o600
            if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != expected or stat.S_ISLNK(info.st_mode):
                raise ValueError('Root-private payload fixture required')
            if not stat.S_ISDIR(info.st_mode) and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                raise ValueError('Single-link regular payload required')
    run_node('verify', directory)
    if not stat.S_ISDIR(TARGET.lstat().st_mode):
        raise ValueError('Existing isolated mount target required')
    subprocess.run(['/usr/bin/mount', '--bind', str(directory), str(TARGET)], check=True, capture_output=True, timeout=10)
    subprocess.run(['/usr/bin/mount', '-o', 'remount,bind,ro', str(TARGET)], check=True, capture_output=True, timeout=10)
    if not os.statvfs(TARGET).f_flag & os.ST_RDONLY:
        raise ValueError('Read-only fixture mount required')
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', str(TARGET / 'machine-gpu-entry.py'), '--verify-sources'],
        check=True, capture_output=True, text=True, timeout=15,
        env={'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    policy = json.loads(result.stdout)
    if (policy.get('bootstrapSourceVerified') is not True or policy.get('verifiedSourceCount') != 10
            or policy.get('bootstrapPathMatched') is not True or policy.get('installedRootMatched') is not True
            or any(policy.get(k) is not False for k in ('installedEntryVerified', 'installedRecipeVerified', 'effectiveGraphVerified',
                'independentRecoveryArmed', 'servicesActivated', 'androidOperationsExecuted', 'installable', 'readyForActivation'))):
        raise ValueError('Closed inert launcher source proof required')
    run_node('verify', directory)
    print(json.dumps({'privateMountNamespace': True, 'readOnlyPayloadMount': True, 'verifiedDependencySources': 10,
        'defaultPathsMatched': True, 'servicesActivated': False, 'androidOperationsExecuted': False, 'readyForActivation': False}))


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Root Linux private-namespace fixtures required')
class InstalledPayload(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-source-payload-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.root / 'payload'
        run_node('build', self.payload)
        self.namespace = os.readlink('/proc/self/ns/mnt')

    def child(self):
        return subprocess.run(['/usr/bin/unshare', '--mount', '--propagation', 'private',
            '/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), '--namespace-child', str(self.payload), self.namespace],
            capture_output=True, text=True, timeout=45)

    def test_actual_default_path_launcher_in_private_readonly_namespace(self):
        before = TARGET.lstat()
        entry = TARGET / 'machine-gpu-entry.py'
        before_entry = entry.lstat() if entry.exists() else None
        result = self.child()
        self.assertEqual(result.returncode, 0, result.stderr)
        proof = json.loads(result.stdout)
        self.assertEqual(proof, {'privateMountNamespace': True, 'readOnlyPayloadMount': True, 'verifiedDependencySources': 10,
            'defaultPathsMatched': True, 'servicesActivated': False, 'androidOperationsExecuted': False, 'readyForActivation': False})
        self.assertEqual(os.readlink('/proc/self/ns/mnt'), self.namespace)
        after = TARGET.lstat()
        self.assertEqual((after.st_dev, after.st_ino, after.st_mode), (before.st_dev, before.st_ino, before.st_mode))
        self.assertEqual(entry.lstat() if entry.exists() else None, before_entry)

    def test_forged_payload_receipt_never_executes_changed_launcher(self):
        sentinel = self.root / 'foreign-executed'
        entry = self.payload / 'machine-gpu-entry.py'
        entry.write_text('from pathlib import Path\nPath(' + repr(str(sentinel)) + ").write_text('executed')\n", encoding='utf-8')
        marker = self.payload / 'bundle-manifest.json'
        manifest = json.loads(marker.read_bytes())
        record = next(r for r in manifest['files'] if r['path'] == 'machine-gpu-entry.py')
        record.update(size=entry.stat().st_size, sha256=hashlib.sha256(entry.read_bytes()).hexdigest())
        marker.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        result = self.child()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertFalse(sentinel.exists())
        self.assertEqual(os.readlink('/proc/self/ns/mnt'), self.namespace)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--namespace-child':
        try:
            namespace_child(sys.argv[2], sys.argv[3])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            print('Generated namespace payload refused; no services activated', file=sys.stderr)
            sys.exit(1)
    else:
        unittest.main()
