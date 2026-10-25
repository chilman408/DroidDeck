# SPDX-License-Identifier: GPL-3.0-only
"""Finite disposable failure injection; no application data or profile writes."""
import importlib.util
import os
from pathlib import Path
import signal
import sys

ROOT = Path('/opt/droiddeck/graph-recovery-20261015br')


def main():
    if sys.platform != 'linux' or os.geteuid() != 0 or len(sys.argv) != 1 or Path(__file__) != ROOT / 'graph-interruption-fixture.py':
        raise ValueError('Fixed disposable interruption fixture required')
    filename = ROOT / 'graph-recovery.py'
    spec = importlib.util.spec_from_file_location('owned_graph_recovery', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def interrupted_switch():
        module.command(['/usr/bin/systemctl', 'stop', *module.GRAPH], timeout=130)
        for name in module.GRAPH:
            module.quiescent(name)
        print('Generated interruption after owned graph quiescence', flush=True)
        os.kill(os.getpid(), signal.SIGKILL)
    module.switch_graph = interrupted_switch
    module.main(['--restart'])


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Graph interruption fixture refused', file=sys.stderr)
        sys.exit(1)
