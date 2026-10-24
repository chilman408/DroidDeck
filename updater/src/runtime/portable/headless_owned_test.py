# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).with_name('headless-owned-runtime.py')
ROOT = FILE.parent
CONFIG = {'version': 1, 'user': 'alice', 'uid': 1200, 'gid': 1300, 'lanAddress': '192.168.10.8',
          'home': '/home/alice', 'port': 8443, 'hostname': 'device.teslaandroid.com'}
sha = lambda b: hashlib.sha256(b).hexdigest()
GENERATION = sha(json.dumps(CONFIG, separators=(',', ':')).encode())
SOFTWARE, GPU = b'generated software profile\n', b'generated GPU profile\n'


def load():
    if not FILE.exists():
        raise AssertionError('Owned-profile headless runtime is missing')
    spec = importlib.util.spec_from_file_location('owned_headless', FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy():
    return {'version': 2, 'configurationDigest': GENERATION, 'originalConfigurationSha256': sha(b'generated fresh profile'),
            'profiles': {'software': sha(SOFTWARE), 'gpu': sha(GPU)}}


class OwnedPolicy(unittest.TestCase):
    def test_only_the_exact_owned_pair_is_admitted(self):
        p = load()
        encoded = json.dumps(policy()).encode()
        self.assertEqual(p.select_profile(encoded, GENERATION, SOFTWARE), 'software')
        self.assertEqual(p.select_profile(encoded, GENERATION, GPU), 'gpu')
        for value in (b'foreign', SOFTWARE + b'changed', b'generated fresh profile'):
            with self.assertRaises(ValueError):
                p.select_profile(encoded, GENERATION, value)
        with self.assertRaises(ValueError):
            p.select_profile(encoded, 'f' * 64, GPU)

    def test_schema_downgrade_duplicate_hashes_and_unknown_fields_are_refused(self):
        p = load()
        for value in ({**policy(), 'version': 1}, {**policy(), 'profiles': {'software': sha(GPU), 'gpu': sha(GPU)}},
                      {**policy(), 'profiles': {**policy()['profiles'], 'arbitrary': sha(b'foreign')}},
                      {**policy(), 'originalConfigurationSha256': sha(GPU)}, {**policy(), 'password': 'generated'},
                      {**policy(), 'profiles': {'software': True, 'gpu': sha(GPU)}}):
            with self.assertRaises(ValueError):
                p.select_profile(json.dumps(value).encode(), GENERATION, GPU)
        with self.assertRaises(ValueError):
            p.select_profile(b'{"version":2,"version":2}', GENERATION, GPU)

    def files(self, selected=GPU):
        files = {Path('/etc/droiddeck/runtime.json'): json.dumps(CONFIG).encode(),
                 Path('/var/lib/waydroid/waydroid.cfg'): selected, ROOT / 'policy.json': json.dumps(policy()).encode()}
        for name in ('headless-runtime.py', 'headless-session.py', 'host-platform-compat.py'):
            files[ROOT / name] = (ROOT / name).read_bytes()
        return files

    def admit(self, files, read=None, **overrides):
        env = {'HOME': '/home/alice', 'XDG_RUNTIME_DIR': '/run/user/1200',
               'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1200/bus', 'WAYLAND_DISPLAY': 'wayland-teslaandroid',
               'DROIDDECK_CONFIGURATION_SHA256': GENERATION}
        return load().admit_owned(ROOT, read=read or (lambda filename: files[filename]), environment=env,
                                  uid=1200, gid=1300, lookup=overrides.get('lookup', lambda user: ('alice', 1200, 1300, '/home/alice')))

    def test_combined_admission_reuses_accepted_protocol_and_preserves_policy_bytes(self):
        for selected, name in ((GPU, 'gpu'), (SOFTWARE, 'software')):
            files = self.files(selected)
            before = dict(files)
            base, protocol, adapter, ticket = self.admit(files)
            self.assertTrue(callable(protocol.run_session))
            self.assertTrue(callable(adapter.adapt_host))
            self.assertEqual(ticket['profile'], name)
            self.assertEqual(ticket['configurationSha256'], sha(selected))
            self.assertEqual(ticket['policySha256'], sha(files[ROOT / 'policy.json']))
            self.assertEqual(files, before)
            self.assertEqual(base.PINS['headless-session.py'], '744be141e0b26b507d70ac77275f2507f2e28a4659a0f0489206d5fd384334ba')

    def test_foreign_nss_and_changed_base_source_never_admit(self):
        files = self.files()
        with self.assertRaises(ValueError):
            self.admit(files, lookup=lambda user: ('alice', 1201, 1300, '/home/alice'))
        files[ROOT / 'headless-runtime.py'] = b'raise RuntimeError("untrusted base executed")\n'
        with self.assertRaises(ValueError):
            self.admit(files)

    def test_raw_policy_change_is_detected_even_if_selected_profile_is_unchanged(self):
        files = self.files()
        reads = 0
        def read(filename):
            nonlocal reads
            if filename == ROOT / 'policy.json':
                reads += 1
                if reads > 1:
                    value = policy()
                    value['profiles']['software'] = sha(b'foreign replacement pair')
                    return json.dumps(value).encode()
            return files[filename]
        with self.assertRaises(ValueError):
            self.admit(files, read)

    def test_allowed_profile_transition_during_admission_still_refuses_that_attempt(self):
        files = self.files()
        reads = 0
        def read(filename):
            nonlocal reads
            if filename == Path('/var/lib/waydroid/waydroid.cfg'):
                reads += 1
                if reads > 1:
                    return SOFTWARE
            return files[filename]
        with self.assertRaises(ValueError):
            self.admit(files, read)

    def test_cli_overrides_refuse_before_loading_any_session_source(self):
        for args in (['--activate'], ['--inspect', '/override'], ['--force']):
            with self.assertRaises(ValueError):
                load().main(args)


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux bootstrap fixtures required')
class Bootstrap(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dd-owned-bootstrap-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'headless-runtime.py'
        self.file.write_bytes((ROOT / self.file.name).read_bytes())
        self.file.chmod(0o644)

    def test_bootstrap_reads_only_pinned_protected_source_without_running_legacy_main(self):
        module = load().load_base(self.root)
        self.assertTrue(callable(module.admit))
        self.file.write_bytes(b'raise RuntimeError("untrusted source executed")\n')
        with self.assertRaises(ValueError):
            load().load_base(self.root)

    def test_bootstrap_refuses_writable_hardlinked_and_symlink_sources(self):
        self.file.chmod(0o666)
        with self.assertRaises(ValueError):
            load().load_base(self.root)
        self.file.chmod(0o644)
        link = self.root / 'linked'
        os.link(self.file, link)
        with self.assertRaises(ValueError):
            load().load_base(self.root)
        link.unlink()
        self.file.rename(link)
        self.file.symlink_to(link)
        with self.assertRaises((ValueError, OSError)):
            load().load_base(self.root)

    def test_bootstrap_refuses_writable_or_linked_namespace(self):
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            load().load_base(self.root)
        self.root.chmod(0o700)
        link = self.root / 'parent-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            load().load_base(link)


if __name__ == '__main__':
    unittest.main()
