# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('pilot_repair_tested', Path(__file__).with_name('pilot-reboot-repair.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


class Identity(unittest.TestCase):
    def test_exact_original_enabled_watcher_names_only(self):
        self.assertEqual(R.watcher_token('droiddeck-fresh-gpu-watch@012345abcdef.service'), '012345abcdef')
        for value in ('foreign', 'droiddeck-fresh-gpu-watch@../escape.service',
                      'droiddeck-fresh-gpu-worker@012345abcdef.service'):
            with self.assertRaises(ValueError):
                R.watcher_token(value)

    def test_transaction_namespace_is_sibling_of_recovery_source(self):
        self.assertEqual(str(R.GPU.parents[3] / 'gpu-transactions'), '/var/lib/droiddeck/gpu-transactions')


@unittest.skipUnless(os.geteuid() == 0, 'Root Linux isolated fixtures required')
class Journal(unittest.TestCase):
    def test_completed_step_never_replays_and_uncertain_step_refuses(self):
        with tempfile.TemporaryDirectory(dir='/root') as temp:
            fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                calls = []
                def operation():
                    calls.append(True)
                    return 'generated-token'
                self.assertEqual(R.once(fd, 'admission', operation), 'generated-token')
                self.assertEqual(R.once(fd, 'admission', operation), 'generated-token')
                self.assertEqual(calls, [True])
                def interrupted():
                    raise RuntimeError('generated interruption')
                with self.assertRaises(RuntimeError):
                    R.once(fd, 'activation', interrupted)
                with self.assertRaises(FileExistsError):
                    R.once(fd, 'activation', operation)
                self.assertEqual(calls, [True])
            finally:
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
