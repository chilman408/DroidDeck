# SPDX-License-Identifier: GPL-3.0-only
"""Fresh independent admission of a retained, inactive installation candidate."""
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from types import MappingProxyType

spec = importlib.util.spec_from_file_location('retained_owned_install_stage', Path(__file__).with_name('runtime-owned-install-stage.py'))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)
A, E, S = F.A, F.E, F.S


def _hold_parent(stack, path):
    chain, current = [], None
    for index, part in enumerate(path.parts):
        fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
        stack.callback(os.close, fd)
        private = index == len(path.parts) - 1
        identity = S._directory(os.fstat(fd), private)
        chain.append((current, part, fd, private, identity))
        current = fd

    def check():
        A._root_linux()
        for ancestor, name, fd, private, identity in chain:
            for info in (os.fstat(fd), os.stat(name, dir_fd=ancestor, follow_symlinks=False)):
                if S._directory(info, private) != identity:
                    raise ValueError('Original retained candidate parent required')
        S._entries(current, {'candidate'})

    check()
    return check


def hold_owned_install_candidate(stack, user, source, recipes, parent, raw):
    """Reconstruct authority from original inputs, never from the retained receipt."""
    A._root_linux()
    E.P.C._bounded(raw)
    source, recipes, parent = map(A._canonical, (source, recipes, parent))
    if parent == PurePosixPath('/root') or not parent.is_relative_to(PurePosixPath('/root')):
        raise ValueError('Private retained candidate parent below /root required')
    roots = source, recipes, parent
    if any(first.is_relative_to(second) or second.is_relative_to(first)
           for index, first in enumerate(roots) for second in roots[index + 1:]):
        raise ValueError('Disjoint retained candidate and original input namespaces required')
    parent_check = _hold_parent(stack, parent)
    held = E.hold_owned_install_plan(stack, user, source, recipes, raw)
    plan = held['read_plan']()
    records, _ = F._layout(plan)
    manifest = F._manifest(plan, records)
    # Reuse the larger publication bound, including the already held parent chain.
    budget = F._fd_budget(parent, records)
    parent_check()
    held['check']()
    candidate = F._hold_candidate(stack, parent / 'candidate', plan)

    def check():
        parent_check()
        held['check']()
        candidate['check']()
        held['check']()
        parent_check()

    def read(name):
        check()
        data = candidate['read'](name)
        check()
        return data

    def read_plan():
        check()
        data = held['read_plan']()
        check()
        return data

    check()
    return {'check': check, 'read': read, 'read_plan': read_plan, 'files': candidate['files'],
        'expectedRecords': MappingProxyType({**records, A.MANIFEST: (len(manifest), E._sha(manifest))}),
        'policy': {**held['policy'], 'kind': 'held-retained-owned-install-candidate',
            'candidatePath': str(parent / 'candidate'), 'candidateFilesVerified': 252,
            'retainedCandidateVerified': True, 'candidateBoundToFreshInputs': True,
            'rootPrivateInstallCandidateVerified': True, 'candidateParentInventoryVerified': True,
            'candidateManifestSha256': E._sha(manifest), 'rootPrivateCopyVerified': False,
            'rootPrivateDependencyCopyVerified': False, 'rootPrivateArtifactCopyVerified': False,
            'newOwnedInstallCandidatePublished': False, 'recipeFilesWritten': False,
            'finalPermissionsApplied': False, 'runtimeLinksMaterialized': False,
            'installationAuthorized': False, 'recoveryAuthorized': False, **budget}}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 6 or argv[0] != '--inspect-retained-candidate' or argv[5] != '-':
        raise ValueError('Only fresh read-only retained candidate admission is exposed')
    raw = sys.stdin.buffer.read(E.P.C.LIMIT + 1)
    with ExitStack() as stack:
        held = hold_owned_install_candidate(stack, argv[1], argv[2], argv[3], argv[4], raw)
        held['check']()
        print(json.dumps(held['policy'], sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, UnicodeError, subprocess.SubprocessError, E.P.configparser.Error):
        print('Retained installation candidate refused; no installation or recovery performed', file=sys.stderr)
        sys.exit(1)
