# SPDX-License-Identifier: GPL-3.0-only
"""Build a bounded deterministic, source-only pilot hotfix; never include VM data."""
import hashlib
import io
from pathlib import Path
import sys
import tarfile

NAMES = ('fresh-gpu.py', 'gpu-activation.py', 'fresh_gpu_test.py', 'reboot-hotfix.py',
         'reboot_hotfix_test.py', 'pilot-reboot-repair.py', 'pilot_reboot_repair_test.py', 'README.md')


def package(public, output):
    root = public / 'patches/reboot-hotfix-1'
    if {p.name for p in root.iterdir()} != set(NAMES) or output.exists():
        raise ValueError('Exact source inventory and new output directory required')
    files = {}
    for name in NAMES:
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError('Single regular source files required')
        raw = path.read_bytes()
        if not 0 < len(raw) < 65536:
            raise ValueError('Bounded public hotfix sources required')
        files[name] = raw
    files['COPYING'] = (public / 'LICENSE').read_bytes()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600 if name.endswith('.py') else 0o644
            info.mtime = info.uid = info.gid = 0
            tar.addfile(info, io.BytesIO(raw))
    assets = {'droiddeck-0.1.0-rc.1-hotfix.1.tar': archive.getvalue(), 'INSTALL.md': files['README.md']}
    checksums = ''.join(hashlib.sha256(raw).hexdigest() + '  ' + name + '\n' for name, raw in sorted(assets.items())).encode()
    assets['SHA256SUMS'] = checksums
    output.mkdir()
    for name, raw in assets.items():
        with (output / name).open('xb') as stream:
            stream.write(raw)
        print(hashlib.sha256(raw).hexdigest() + '  ' + name)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise ValueError('Public source root and NEW artifact directory required')
    package(Path(sys.argv[1]), Path(sys.argv[2]))
