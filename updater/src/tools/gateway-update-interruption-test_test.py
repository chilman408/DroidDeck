# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('interruption_tested', Path(__file__).with_name('gateway-update-interruption-test.py'))
D = importlib.util.module_from_spec(spec); spec.loader.exec_module(D)


class Policy(unittest.TestCase):
    def test_fixed_source_and_digest_controller_cannot_select_arbitrary_pid(self):
        self.assertEqual(D.SOURCE.as_posix(), '/var/lib/droiddeck/gateway-update-controller-v3/sources/runtime/portable/gateway-update-chain.py')
        self.assertEqual(len(D.SOURCE_PIN), 64)
        tx = SimpleNamespace(unit='fixed-generated-unit', digest='c' * 64,
            g=SimpleNamespace(unit=lambda _: {'ActiveState': 'active', 'MainPID': '0', 'ControlPID': '0'}))
        with self.assertRaises(ValueError): D.controller_pid(None, tx)

    def test_only_exact_original_controller_process_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); process = root / '123'; process.mkdir()
            digest = 'c' * 64; state = {'ActiveState': 'active', 'MainPID': '123', 'ControlPID': '0'}
            command = b'\0'.join([b'/usr/bin/python3', b'-I', b'-B', str(D.SOURCE).encode('ascii'), b'run', digest.encode('ascii'), b''])
            (process / 'cmdline').write_bytes(command)
            systemd = Path('/etc/systemd/system'); template = 'droiddeck-gateway-update-v3@.service'
            g = SimpleNamespace(unit=lambda _: dict(state), command=lambda *_: SimpleNamespace(
                stdout=('FragmentPath=' + str(systemd / template) + '\nDropInPaths=\n').encode('ascii')))
            tx = SimpleNamespace(unit=template.replace('@.', '@' + digest + '.'), digest=digest, g=g)
            c = SimpleNamespace(B=SimpleNamespace(SYSTEMD=systemd, TEMPLATE=template))
            with patch.object(D, 'PROC', root), patch.object(D, 'owner_uid', lambda _: 0):
                self.assertEqual(D.controller_pid(c, tx), (123, state))
                (process / 'cmdline').write_bytes(command.replace(b'run\0', b'inspect-current\0'))
                with self.assertRaises(ValueError): D.controller_pid(c, tx)
                (process / 'cmdline').write_bytes(command)
                with patch.object(D, 'owner_uid', lambda _: 1000):
                    with self.assertRaises(ValueError): D.controller_pid(c, tx)

    def test_controller_override_refuses_before_process_access(self):
        g = SimpleNamespace(unit=lambda _: {'ActiveState': 'active', 'MainPID': '123', 'ControlPID': '0'},
            command=lambda *_: SimpleNamespace(stdout=b'FragmentPath=/tmp/foreign.service\nDropInPaths=\n'))
        tx = SimpleNamespace(unit='fixture', digest='c' * 64, g=g)
        c = SimpleNamespace(B=SimpleNamespace(SYSTEMD=Path('/etc/systemd/system'), TEMPLATE='original.service'))
        with self.assertRaises(ValueError): D.controller_pid(c, tx)

    def test_only_exact_name_moved_into_fixed_live_parent_is_an_exchange_event(self):
        def event(name, mask):
            raw = name + b'\0'; raw += b'\0' * ((-len(raw)) % 4)
            return D.struct.pack('iIII', 1, mask, 0, len(raw)) + raw
        raw = event(b'preview-gateway', D.MOVED_TO) + event(b'other', D.MOVED_TO) + event(b'preview-gateway', 0x100)
        self.assertEqual(D.exchange_events(raw, 'preview-gateway'), 1)
        with self.assertRaises(ValueError): D.exchange_events(raw[:-1], 'preview-gateway')


if __name__ == '__main__': unittest.main()
