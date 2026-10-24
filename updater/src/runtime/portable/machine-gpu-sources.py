# SPDX-License-Identifier: GPL-3.0-only
"""Hold fixed component source generations; no installation or activation entry."""
import hashlib
import os
from pathlib import Path
import sys
import types

ROOT = Path('/opt/droiddeck/machine-gpu-runtime')
PINS = {
    'runtime/portable/promote-images.py': (253820, 'e4f67d1c43c8a81486b1e6de02a157b3a5f52d1665730053cfff7afbc8a75616'),
    'runtime/portable/stage-images.py': (12509, '077de6f524d575ee67750d7f54df62d3c08115ec0490937bff14f4e99cedc59a'),
    'runtime/portable/machine-gpu-supervision.py': (13262, '34cb29f173e2f0ab65f69db7605e87a1e9bb49c789c8a7f435a5369fd54326c3'),
    'runtime/portable/effective-units.py': (5285, '5df4aeb90df7d10fcf2d052438b53465b411a475ecc0ebc37afdddb89d2a6f4e'),
    'tools/waydroid-layer-property.py': (5278, '568e12583f931d6ad464bff79692b11c3dbc4ff0de88725c7f0e214a2861698f'),
    'tools/waydroid-gpu-health-policy.py': (1127, '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e'),
}
INVENTORY = {
    '': {'runtime', 'tools'}, 'runtime': {'portable'},
    'runtime/portable': {'promote-images.py', 'stage-images.py', 'machine-gpu-supervision.py', 'effective-units.py'},
    'tools': {'waydroid-layer-property.py', 'waydroid-gpu-health-policy.py'},
}
ROLES = {'recovery': 'runtime/portable/promote-images.py', 'supervision': 'runtime/portable/machine-gpu-supervision.py',
         'units': 'runtime/portable/effective-units.py', 'health': 'tools/waydroid-gpu-health-policy.py'}


def hold_component_sources(copier, stack, root=ROOT):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root Linux protected component source admission required')
    root = Path(copier.canonical(str(root)))
    directories, records = {}, {}
    for relative, inventory in INVENTORY.items():
        chain = copier.open_chain(stack, root / relative, root_owned=True)
        directory = chain[-1][1]
        copier.protected(os.fstat(directory), 0o700)
        if set(os.listdir(directory)) != inventory:
            raise ValueError('Closed component source directory required')
        directories[relative] = chain
    for relative, (size, digest) in PINS.items():
        parent, name = relative.rsplit('/', 1)
        directory = directories[parent][-1][1]
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        stack.callback(os.close, fd)
        info = os.fstat(fd)
        copier.regular(info, size)
        copier.protected(info, 0o600)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            encoded = stream.read(size + 1)
        if len(encoded) != size or hashlib.sha256(encoded).hexdigest() != digest:
            raise ValueError('Component source differs from independent fixed recipe')
        copier.check_image(directory, name, fd, info, private=True)
        records[relative] = (directory, name, fd, info, encoded)

    def check():
        for relative, chain in directories.items():
            copier.check_chain(chain, root_owned=True)
            directory = chain[-1][1]
            copier.protected(os.fstat(directory), 0o700)
            if set(os.listdir(directory)) != INVENTORY[relative]:
                raise ValueError('Held component source inventory changed')
        for relative, (directory, name, fd, info, _encoded) in records.items():
            copier.check_image(directory, name, fd, info, private=True)
            if copier.digest_fd(fd, info.st_size) != PINS[relative][1]:
                raise ValueError('Held component source generation changed')
            copier.check_image(directory, name, fd, info, private=True)

    def load(role):
        if type(role) is not str or role not in ROLES:
            raise ValueError('Closed verified component role required')
        check()
        relative = ROLES[role]
        module = types.ModuleType('droiddeck_machine_verified_' + role)
        module.__file__ = str(root / relative)
        # Execute only the already-held hash-matched bytes, never reopen a path/import worker code.
        exec(compile(records[relative][4], module.__file__, 'exec'), module.__dict__)
        check()
        return module

    check()
    return {'check': check, 'load': load, 'policy': {
        'kind': 'protected-machine-gpu-component-sources', 'componentSourcesVerified': True,
        'componentCount': len(PINS), 'installedRootMatched': root == ROOT,
        'installedRecipeVerified': False, 'effectiveGraphVerified': False,
        'independentRecoveryArmed': False, 'servicesActivated': False,
        'androidOperationsExecuted': False, 'installable': False, 'readyForActivation': False}}


def hold_verified_worker(copier, stack, root=ROOT, *, observe=None, identify=None, current_boot=None):
    sources = hold_component_sources(copier, stack, root)
    supervision = sources['load']('supervision')
    peer = supervision.hold_worker_supervision(stack, admit=sources['check'], observe=observe,
        identify=identify, current_boot=current_boot)
    return {**sources['policy'], **peer}
