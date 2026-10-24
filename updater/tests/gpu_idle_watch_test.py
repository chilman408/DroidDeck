# SPDX-License-Identifier: GPL-3.0-only
"""Read-only regression of exact RC5 watchdog sources using generated observations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'src'


def load(name, relative, expected):
    path = SOURCE / relative
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('Tested signed-source identity changed')
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    # fcntl is not used by these observation-only fixtures on Windows.
    if sys.platform == 'win32':
        with patch.dict(sys.modules, {'fcntl': SimpleNamespace()}):
            spec.loader.exec_module(value)
    else:
        spec.loader.exec_module(value)
    return value


G = load('rc5_gpu', 'runtime/portable/gpu-activation.py',
         'd9d902c80847798f70012079e92d30d586d4780f5fd4fc139bf63fc26f79c2b3')
POLICY = load('rc5_gpu_policy', 'tools/waydroid-gpu-health-policy.py',
              '806611c8c8615f4bf6d259c82bc33282a809301b80f3568a47dadc815cb5dc4e')


class IdleWatch(unittest.TestCase):
    def run_watch(self, *, healthy):
        activation = G.Activation.__new__(G.Activation)
        calls = []
        sample = 0

        def status():
            nonlocal sample
            sample += 1
            return {'stage': 'ready' if sample <= 6 else 'restored', 'boot': 'fixture-boot', 'since': 0}

        def command(args, **_kwargs):
            calls.append(args)
            # Idle peers are inactive; any accidental peer-based GPU health fails.
            if args[0] == '/usr/bin/systemctl':
                return SimpleNamespace(returncode=3, stdout=b'inactive\n')
            self.assertEqual(args, ['/usr/bin/node', str(G.ROOT / 'runtime/portable/gpu-native.js')])
            return SimpleNamespace(returncode=0, stdout=json.dumps({'nativeGpuVerified': healthy}))

        activation.store = SimpleNamespace(status=status)
        activation.recover = unittest.mock.Mock()
        with patch.object(G, 'command', side_effect=command), \
                patch.object(G, 'uptime', return_value=1000000), \
                patch.object(G, 'boot_id', return_value='fixture-boot'), \
                patch.object(G.time, 'sleep') as sleep:
            activation.watch(POLICY)
        return activation, calls, sleep

    def test_idle_peers_and_long_uptime_never_trigger_gpu_recovery(self):
        activation, calls, sleep = self.run_watch(healthy=True)
        self.assertEqual(len(calls), 6)
        self.assertEqual(sleep.call_count, 6)
        activation.recover.assert_not_called()

    def test_three_real_native_failures_still_trigger_recovery(self):
        activation, calls, sleep = self.run_watch(healthy=False)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        activation.recover.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
