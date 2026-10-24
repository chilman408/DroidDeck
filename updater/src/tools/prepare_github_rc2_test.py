# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntegratedPublication(unittest.TestCase):
    def test_pins_and_gates_are_separate_from_existing_release(self):
        original = load('original_pilot', 'prepare-github-pilot.py')
        integrated = load('integrated_pilot', 'prepare-github-rc2.py').P
        self.assertEqual(original.VERSION, '0.1.0-rc.1')
        self.assertEqual(len(original.GATES), 12)
        self.assertEqual(integrated.VERSION, '0.1.0-rc.2')
        self.assertEqual(integrated.SOURCE, 'c0e3a5a4a6b1e41647159034227ea08b8ff6c139')
        self.assertEqual(integrated.CANDIDATE, 'a0c211c91c65395e79f8c444bd7cff0b0f22e0477182520037eaf0d32d6f3c28')
        self.assertEqual(integrated.GATES - original.GATES,
                         {'interruptedStartupReboot', 'slowAndroidStartup'})
        self.assertEqual(len(integrated.FILES), 5)
        self.assertNotIn('droiddeck-0.1.0-rc.1-source.tar', integrated.FILES)

    def test_templates_have_integrated_install_command_and_no_credentials(self):
        integrated = load('integrated_templates', 'prepare-github-rc2.py').P
        pins = integrated.fingerprints([
            '14:0cd71a894ee0024eeb38f48dc674bf814096542dc3ea9b091728081d639695ad',
            '14:3bc4e88edacc78d37f173e864f90c7ffa22370911c58662031e8d0b90b58b8b1'])
        for path in (ROOT / 'pilot-public-rc2').rglob('*.md'):
            self.assertEqual(integrated.scan(path.read_bytes(), pins), [])
        readme = (ROOT / 'pilot-public-rc2' / 'README.md').read_text()
        self.assertIn('--install-machine', readme)
        self.assertIn('droiddeck-0.1.0-rc.2-source.tar', readme)
        self.assertNotIn('192.168.2.', readme)


if __name__ == '__main__':
    unittest.main()
