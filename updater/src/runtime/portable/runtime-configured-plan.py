# SPDX-License-Identifier: GPL-3.0-only
"""Read-only configuration binding using the held candidate's reviewed validator."""
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

_spec = importlib.util.spec_from_file_location('configured_placement_plan', Path(__file__).with_name('runtime-install-plan.py'))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)
A = L.A
LIMIT = 16384
FIELDS = ('version', 'user', 'uid', 'gid', 'lanAddress', 'home', 'port', 'hostname')
SCRIPT = """const fs=require('node:fs');
const {normalizeRuntimeConfig}=require(process.argv[1]);
const raw=fs.readFileSync(0);if(raw.length>16384)throw Error('Bounded configuration required');
const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw));
process.stdout.write(JSON.stringify(normalizeRuntimeConfig(value)));"""


def _bounded(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= LIMIT:
        raise ValueError('Bounded configuration metadata required')


def _normalize(source, raw):
    # Call only after the entire root-private, independently pinned source tree is held.
    _bounded(raw)
    validator = str(source / 'assembly/runtime/portable/runtime-config.js')
    try:
        result = subprocess.run(['/usr/bin/node', '-e', SCRIPT, validator], input=raw,
            capture_output=True, timeout=10, cwd='/', close_fds=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        if result.returncode != 0 or result.stderr or not 0 < len(result.stdout) <= LIMIT:
            raise ValueError('Configuration validation refused')
        value = json.loads(result.stdout)
        if (type(value) is not dict or tuple(value) != FIELDS
                or json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii') != result.stdout):
            raise ValueError('Canonical reviewed validator result required')
        return value, result.stdout
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ValueError('Configuration validation refused') from None


def hold_configured_runtime_plan(stack, user, source, raw):
    A._root_linux()
    _bounded(raw)
    source = A._canonical(source)
    held = L.hold_runtime_install_plan(stack, user, source)
    held['check']()
    configuration, compact = _normalize(source, raw)
    held['check']()
    base = held['read_plan']()
    value = json.loads(base)
    if {key: configuration[key] for key in ('user', 'uid', 'gid', 'home')} != value['targetAccount']:
        raise ValueError('Configuration must match independently resolved target account')
    canonical = (json.dumps(configuration, indent=2, ensure_ascii=True) + '\n').encode('ascii')
    digests = {'configurationDigest': hashlib.sha256(compact).hexdigest(),
        'sourceMetadataSha256': hashlib.sha256(raw).hexdigest(),
        'canonicalMetadataSha256': hashlib.sha256(canonical).hexdigest(),
        'placementPlanSha256': hashlib.sha256(base).hexdigest()}
    value.update({'kind': 'configured-runtime-artifact-placement-plan', 'configuration': configuration,
        'configurationPath': '/etc/droiddeck/runtime.json', 'configurationIncluded': True,
        'profilePolicyVerified': False, **digests})
    plan = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode('ascii')
    held['check']()

    def checked(raw):
        held['check']()
        result = raw
        held['check']()
        return result

    def read_source(name):
        held['check']()
        result = held['read_source'](name)
        held['check']()
        return result

    return {'check': held['check'], 'read_plan': lambda: checked(plan),
        'read_configuration': lambda: checked(canonical), 'read_source': read_source, 'policy': {
            **held['policy'], 'kind': 'held-configured-runtime-placement-plan',
            'configurationIncluded': True, 'configurationMatched': True,
            'configurationValidatorExecuted': True, 'sourceValidationCodeExecuted': True,
            'profilePolicyVerified': False, **digests, 'planSha256': hashlib.sha256(plan).hexdigest()}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4 or argv[0] != '--inspect-configured-plan' or argv[3] != '-':
        raise ValueError('Only fixed read-only configuration binding with bounded stdin is exposed')
    raw = sys.stdin.buffer.read(LIMIT + 1)
    with ExitStack() as stack:
        held = hold_configured_runtime_plan(stack, argv[1], argv[2], raw)
        held['read_plan']()
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError):
        print('Configured runtime planning refused; no changes performed', file=sys.stderr)
        sys.exit(1)
