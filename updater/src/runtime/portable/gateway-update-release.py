# SPDX-License-Identifier: GPL-3.0-only
"""Public pilot entrypoint around the tested cumulative gateway update engine.

One-time administrator enrollment explicitly binds an existing accepted restart
policy. No original installation receipt, runtime, GPU source or data is rewritten.
All later operations execute only independently retained root-protected sources.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('pilot_cumulative_engine', Path(__file__).with_name('gateway-update-chain.py'))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)
B, P, T, H, E, G = C.B, C.P, C.T, C.H, C.E, C.G
SELF = 'runtime/portable/gateway-update-release.py'
ENROLLMENT = Path('/var/lib/droiddeck/gateway-update-pilot-enrollment')
PUBLIC_KEY = '302a300506032b65700321009d63ba428047b862b5cd1fb5ac994814d985b9a015ecf5805de1261d7825e69c'
KEY_PIN = '4e0cb3fe1781c97522154c0a33d9678873cfb837808331ba218ad9c70425b34f'
B.CONTROLLER = Path('/var/lib/droiddeck/gateway-update-pilot')
B.ROOT = Path('/var/lib/droiddeck/gateway-update-attempts')
B.SOURCE = B.CONTROLLER / 'sources'
B.TEMPLATE = 'droiddeck-update@.service'
B.BOOT = 'droiddeck-update-boot.service'
B.SELF = SELF
B.OVERLAYS = (*B.OVERLAYS, 'gateway-update-release.py')
C.PREPARING, C.HISTORY = B.CONTROLLER / 'preparing', B.CONTROLLER / 'history'


def reviewed_policy():
    return G.protected_read(Path(__file__).with_name('needrestart-droiddeck.conf'), maximum=4096)


def public_trust(*, create=False):
    raw = P.encode({'schema': 1, 'kind': 'droiddeck-update-trust', 'channel': 'pilot', 'publicKeySpkiHex': PUBLIC_KEY})
    if P.sha(B.A.trust(raw)) != KEY_PIN: raise ValueError('Original public pilot signing key required')
    if create and not os.path.lexists(B.A.TRUST): T.write_new(B.A.TRUST, raw)
    actual = G.protected_read(B.A.TRUST, maximum=4096)
    if actual != raw: raise ValueError('Existing update trust cannot be changed, adopted or rotated')
    return {'keySha256': KEY_PIN, 'generation': E.generation(B.A.TRUST.lstat()), 'sha256': P.sha(raw)}


def enroll_policy(mode, package, *, accept_existing):
    if os.path.lexists(ENROLLMENT): raise FileExistsError('Original enrollment exists; never replay or overwrite it')
    exists = os.path.lexists(B.POLICY)
    if exists and not accept_existing:
        raise ValueError('Existing restart policy requires explicit --accept-existing-policy administrator enrollment')
    policy = None
    if exists:
        raw = G.protected_read(B.POLICY, 0o644, 4096)
        if raw != reviewed_policy(): raise ValueError('Existing policy differs; leave it intact for administrator review')
        policy = E.generation(B.POLICY.lstat())
    first = B.A.base_proof(mode, package)
    if first.get('originalInstallationOwnershipVerified') is not True:
        raise ValueError('Original configured RC2 ownership must verify before enrollment')
    trust = public_trust(create=True)
    T.mkdir_new(ENROLLMENT)
    value = {'schema': 1, 'kind': 'droiddeck-pilot-update-enrollment', 'machineSha256': T.machine_pin(),
        'mode': mode, 'package': str(Path(package)), 'originalBaseProofSha256': P.sha(P.encode(first)),
        'policyGeneration': policy, 'policySha256': None if policy is None else P.sha(reviewed_policy()),
        'administratorAcceptedExistingPolicy': exists and accept_existing, 'publicTrust': trust,
        'rootIdentity': T.identity(ENROLLMENT)}
    T.write_new(ENROLLMENT / 'intent.json', P.encode(value))
    if (B.A.base_proof(mode, package) != first or public_trust() != trust
            or exists and E.generation(B.POLICY.lstat()) != policy):
        raise ValueError('Original installation changed; preserve incomplete enrollment')
    T.write_new(ENROLLMENT / 'complete.json', P.encode(value))
    return value


def inspect_enrollment():
    raw = G.protected_read(ENROLLMENT / 'intent.json'); value = P.document(raw)
    keys = {'schema', 'kind', 'machineSha256', 'mode', 'package', 'originalBaseProofSha256', 'policyGeneration',
            'policySha256', 'administratorAcceptedExistingPolicy', 'publicTrust', 'rootIdentity'}
    if (set(value) != keys or type(value['schema']) is not int or value['schema'] != 1
            or value['kind'] != 'droiddeck-pilot-update-enrollment' or value['mode'] not in ('completed', 'recovered')
            or value['machineSha256'] != T.machine_pin() or value['rootIdentity'] != T.identity(ENROLLMENT)
            or G.protected_read(ENROLLMENT / 'complete.json') != raw or public_trust() != value['publicTrust']):
        raise ValueError('Original completed explicit public-key/policy enrollment required')
    P.digest(value['originalBaseProofSha256'])
    if value['policyGeneration'] is not None:
        if (value['administratorAcceptedExistingPolicy'] is not True
                or E.generation(B.POLICY.lstat()) != value['policyGeneration']
                or P.sha(G.protected_read(B.POLICY, 0o644, 4096)) != value['policySha256']
                or value['policySha256'] != P.sha(reviewed_policy())):
            raise ValueError('Same administrator-enrolled policy inode, generation and bytes required')
    elif value['policySha256'] is not None or value['administratorAcceptedExistingPolicy'] is not False:
        raise ValueError('Only original new-policy enrollment may omit a policy generation')
    return value


def policy_authority():
    value = inspect_enrollment()
    if value['policyGeneration'] is None: return None
    return {'enrollmentSha256': P.sha(P.encode(value)),
            'enrollmentGeneration': E.generation((ENROLLMENT / 'intent.json').lstat())}


B.policy_authority = policy_authority


def bootstrap(source, pin, mode, package, *, accept_existing=False):
    if P.sha(P.encode(B.rows(B.overlays(source)))) != P.digest(pin):
        raise ValueError('Exact published independently reviewed updater sources required')
    if not os.path.lexists(ENROLLMENT): enroll_policy(mode, package, accept_existing=accept_existing)
    enrollment = inspect_enrollment()
    if (enrollment['mode'], enrollment['package']) != (mode, str(Path(package))):
        raise ValueError('Original enrolled package and completion authority required')
    if os.path.lexists(B.CONTROLLER):
        C.inspect_controller()
    else:
        B.A.base_proof(mode, package)
        C.enroll(source, pin)
    B.arm_boot()
    context = C.inspect_controller()
    return {'kind': 'droiddeck-pilot-updater-enrolled', 'root': str(B.CONTROLLER),
            'controllerSha256': P.sha(P.encode(context)), 'releaseKeySha256': KEY_PIN,
            'androidDataPreserved': True, 'originalRuntimeAndGpuServicesRestarted': False}


def main(args):
    if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Explicit root Linux operation required')
    os.umask(0o077)
    if len(args) in (5, 6) and args[0] == 'bootstrap':
        if len(args) == 6 and args[5] != '--accept-existing-policy': raise ValueError('Explicit policy flag required')
        print(json.dumps(bootstrap(*args[1:5], accept_existing=len(args) == 6), sort_keys=True), flush=True)
    else:
        # The engine's runtime path check must refer to this retained wrapper.
        C.__file__ = __file__
        inspect_enrollment()
        C.main(args)


if __name__ == '__main__': main(sys.argv[1:])
