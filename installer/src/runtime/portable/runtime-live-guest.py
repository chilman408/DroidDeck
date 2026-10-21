# SPDX-License-Identifier: GPL-3.0-only
"""Publish guest helpers from an independently admitted original owned installation."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sys


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load('runtime-registration-transaction')
P = _load('promote-images')


def bound_validator(authority, user):
    original = authority['read_file']('/etc/droiddeck/runtime.json')
    value = json.loads(original)
    account = R.K.L._account(user)
    if tuple(value[k] for k in ('user', 'uid', 'gid', 'home')) != account:
        raise ValueError('Original admitted runtime account required')
    generation = R._sha(json.dumps(value, separators=(',', ':'), ensure_ascii=True).encode('ascii'))

    def validate(encoded):
        authority['check']()
        if (type(encoded) is not bytes or encoded != original
                or authority['read_file']('/etc/droiddeck/runtime.json') != original
                or R.K.L._account(user) != account):
            raise ValueError('Actual metadata must match original independently admitted configuration')
        return {'config': dict(value), 'configurationDigest': generation}
    validate(original)
    return validate


def publish(authority, user):
    R.A._root_linux()
    P._assert_guest_runtime_inactive()
    # Android is deliberately running for exact mounted-data identity admission.
    # The source authority and committed original object journal are unchanged.
    R._finish(authority, R.JOURNAL, check_runtime=P._assert_guest_runtime_inactive)
    validate = bound_validator(authority, user)

    def context(stack, copier):
        admitted = P._machine_guest_context(stack, copier, validate=validate)
        check = admitted['check']

        def bound_check():
            authority['check']()
            check()
            authority['check']()
        bound_check()
        return {**admitted, 'check': bound_check}

    # Current reviewed runtime/native commands use this fixed compatibility path.
    # The separate machine-GPU guest path remains a different unpublished component.
    result = P._install_guest_payloads(P.load_copier(), context=context)
    authority['check']()
    return {**result, 'kind': 'original-owned-runtime-guest-payloads',
        'originalOwnedRegistrationVerified': True, 'sourceAccountValidated': True,
        'actualMetadataBound': True, 'servicesActivated': False,
        'browserVerified': False, 'runtimeRecoveryVerified': False}


def main(args=None):
    args = sys.argv[1:] if args is None else args
    R.A._root_linux()
    if len(args) != 5 or args[0] != '--install-new' or args[-1] != '-':
        raise ValueError('Only fixed new live guest payload publication supported')
    raw = sys.stdin.buffer.read(R.O.E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        authority = R.K.hold_final_install_layout(stack, args[1], args[2], args[3], raw)
        print(json.dumps(publish(authority, args[1]), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Fresh guest publication incomplete; preserve original/partial targets, no services activated', file=sys.stderr)
        sys.exit(1)
