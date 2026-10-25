# SPDX-License-Identifier: GPL-3.0-only
"""Reuse the pilot privacy auditor with a separate immutable integrated-rc.2 pin."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('pilot_audit', Path(__file__).with_name('prepare-github-pilot.py'))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)
P.VERSION = '0.1.0-rc.2'
P.SOURCE = 'c0e3a5a4a6b1e41647159034227ea08b8ff6c139'
P.CANDIDATE = 'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28'
P.FILES = {'COPYING', 'INSTALL.md', 'SHA256SUMS', 'release.json', 'droiddeck-' + P.VERSION + '-source.tar'}
P.GATES |= {'interruptedStartupReboot', 'slowAndroidStartup'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('release', type=Path)
    parser.add_argument('new_output', type=Path)
    parser.add_argument('--credential-sha256', action='append', required=True)
    args = parser.parse_args()
    pins = P.fingerprints(args.credential_sha256)
    common = Path(__file__).with_name('pilot-public')
    overlay = Path(__file__).with_name('pilot-public-rc2')
    with tempfile.TemporaryDirectory(prefix='droiddeck-rc2-public-docs-') as directory:
        root = Path(directory)
        for name in ('README.md', 'SECURITY.md', 'docs/PILOT.md', 'docs/KNOWN_LIMITATIONS.md', '.gitignore'):
            source = overlay / name if (overlay / name).is_file() else common / name
            raw = P.read(source, P.MAX_FILE)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(raw)
        P.prepare(args.release, args.new_output, pins, root)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'publicationPrepared': False, 'error': type(error).__name__}))
        raise SystemExit(1)
