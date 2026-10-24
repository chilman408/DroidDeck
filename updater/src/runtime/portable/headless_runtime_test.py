# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

FILE = Path(__file__).with_name('headless-runtime.py')
CONFIG = {'version': 1, 'user': 'alice', 'uid': 1200, 'gid': 1300, 'lanAddress': '192.168.10.8',
          'home': '/home/alice', 'port': 8443, 'hostname': 'device.teslaandroid.com'}
GENERATION = hashlib.sha256(json.dumps(CONFIG, separators=(',', ':')).encode()).hexdigest()


class RuntimePolicy(unittest.TestCase):
    def setUp(self):
        self.assertTrue(FILE.is_file(), 'Portable headless runtime is missing')
        spec = importlib.util.spec_from_file_location('portable_headless', FILE)
        self.p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.p)
        self.env = {'HOME': '/home/alice', 'XDG_RUNTIME_DIR': '/run/user/1200',
                    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1200/bus', 'WAYLAND_DISPLAY': 'wayland-teslaandroid',
                    'DROIDDECK_CONFIGURATION_SHA256': GENERATION}

    def test_generation_matches_javascript_field_order_not_sorted_json(self):
        encoded = json.dumps(dict(reversed(list(CONFIG.items())))).encode()
        self.assertEqual(self.p.decode_configuration(encoded, GENERATION), CONFIG)
        with self.assertRaises(ValueError):
            self.p.decode_configuration(encoded, 'f' * 64)

    def test_duplicate_extra_or_noncanonical_identity_is_rejected(self):
        invalid = [{**CONFIG, 'password': 'generated'}, {**CONFIG, 'uid': True}, {**CONFIG, 'user': 'root'},
                   {**CONFIG, 'port': 80}, {**CONFIG, 'hostname': 'UPPER.example'}, {**CONFIG, 'home': '/tmp/foreign'},
                   {**CONFIG, 'lanAddress': 3232238088}, {**CONFIG, 'home': '/home/con'},
                   {**CONFIG, 'hostname': 'localhost'}, {**CONFIG, 'hostname': '192.168.10.8'},
                   {**CONFIG, 'hostname': 'a' * 50 + '.' + 'b' * 40 + '.com'}]
        for value in invalid:
            generation = hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()
            with self.assertRaises(ValueError):
                self.p.decode_configuration(json.dumps(value).encode(), generation)
        with self.assertRaises(ValueError):
            self.p.decode_configuration(b'{"version":1,"version":1}', GENERATION)

    def test_nondefault_uid_gid_and_private_desktop_are_admitted(self):
        self.p.validate_identity(CONFIG, self.env, 1200, 1300, ('alice', 1200, 1300, '/home/alice'))
        for uid, gid, account in [(1000, 1300, ('alice', 1200, 1300, '/home/alice')),
                                   (1200, 1000, ('alice', 1200, 1300, '/home/alice')),
                                   (1200, 1300, ('alice', 1201, 1300, '/home/alice'))]:
            with self.assertRaises(ValueError):
                self.p.validate_identity(CONFIG, self.env, uid, gid, account)

    def test_wrong_desktop_environment_refuses_before_session_start(self):
        for key in ('HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'WAYLAND_DISPLAY'):
            with self.assertRaises(ValueError):
                self.p.validate_identity(CONFIG, {**self.env, key: 'foreign'}, 1200, 1300, ('alice', 1200, 1300, '/home/alice'))

    def test_policy_binds_both_machine_generation_and_exact_gpu_profile_bytes(self):
        config = b'generated Waydroid profile\n'
        policy = {'version': 1, 'configurationDigest': GENERATION,
                  'waydroidConfigurationSha256': hashlib.sha256(config).hexdigest()}
        self.p.validate_policy(json.dumps(policy).encode(), GENERATION, config)
        for raw, generation in [(config + b'changed', GENERATION), (config, 'f' * 64)]:
            with self.assertRaises(ValueError):
                self.p.validate_policy(json.dumps(policy).encode(), generation, raw)
        with self.assertRaises(ValueError):
            self.p.validate_policy(json.dumps({**policy, 'key': 'generated'}).encode(), GENERATION, config)

    def test_protocol_sources_stay_byte_pinned_and_never_invoke_legacy_main(self):
        root = FILE.parent
        protocol, adapter = self.p.load_protocol(root, read=lambda path: path.read_bytes())
        self.assertTrue(callable(protocol.run_session))
        self.assertTrue(callable(adapter.adapt_host))
        for changed in ('headless-session.py', 'host-platform-compat.py'):
            def read(path):
                return b'raise RuntimeError("untrusted code executed")' if path.name == changed else path.read_bytes()
            with self.assertRaises(ValueError):
                self.p.load_protocol(root, read=read)

    def admission_files(self):
        profile = b'generated Waydroid profile\n'
        files = {self.p.METADATA: json.dumps(CONFIG).encode(), self.p.WAYDROID: profile,
                 FILE.parent / 'policy.json': json.dumps({'version': 1, 'configurationDigest': GENERATION,
                    'waydroidConfigurationSha256': hashlib.sha256(profile).hexdigest()}).encode()}
        for name in self.p.PINS:
            files[FILE.parent / name] = (FILE.parent / name).read_bytes()
        return files

    def test_combined_admission_checks_nss_and_profile_without_starting_a_session(self):
        files = self.admission_files()
        protocol, adapter = self.p.admit(FILE.parent, read=lambda path: files[path], environment=self.env,
            uid=1200, gid=1300, lookup=lambda user: ('alice', 1200, 1300, '/home/alice'))
        self.assertTrue(callable(protocol.run_session))
        self.assertTrue(callable(adapter.adapt_host))
        with self.assertRaises(ValueError):
            self.p.admit(FILE.parent, read=lambda path: files[path], environment=self.env,
                uid=1200, gid=1300, lookup=lambda user: ('alice', 1201, 1300, '/home/alice'))

    def test_metadata_mutation_while_protocol_loads_is_not_admitted(self):
        files = self.admission_files()
        reads = 0
        def read(path):
            nonlocal reads
            if path == self.p.METADATA:
                reads += 1
                if reads > 1:
                    return b'changed generated metadata'
            return files[path]
        with self.assertRaises(ValueError):
            self.p.admit(FILE.parent, read=read, environment=self.env, uid=1200, gid=1300,
                         lookup=lambda user: ('alice', 1200, 1300, '/home/alice'))


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Root Linux protected-file fixtures required')
class ProtectedFiles(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('protected_headless', FILE)
        self.p = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.p)
        self.temp = tempfile.TemporaryDirectory(prefix='dd-headless-reader-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / 'generated'
        self.file.write_bytes(b'generated safe fixture')
        self.file.chmod(0o644)

    def test_protected_reader_does_not_follow_links_or_admit_writable_files(self):
        self.assertEqual(self.p.protected_read(self.file), b'generated safe fixture')
        self.file.chmod(0o666)
        with self.assertRaises(ValueError):
            self.p.protected_read(self.file)
        self.file.chmod(0o644)
        link = self.root / 'link'
        os.link(self.file, link)
        with self.assertRaises(ValueError):
            self.p.protected_read(self.file)
        link.unlink()
        link.symlink_to(self.file)
        with self.assertRaises((OSError, ValueError)):
            self.p.protected_read(link)

    def test_protected_reader_refuses_writable_or_linked_ancestor(self):
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            self.p.protected_read(self.file)
        self.root.chmod(0o700)
        link = self.root / 'parent-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.p.protected_read(link / self.file.name)

    def test_host_namespace_is_validated_without_executing_package_code(self):
        self.assertTrue(callable(getattr(self.p, 'admit_host_namespace', None)), 'Pre-import host admission is missing')
        host = self.root / 'host'
        for name in ('tools/__init__.py', 'tools/actions/__init__.py',
                     'tools/helpers/__init__.py', 'tools/actions/session_manager.py', 'tools/interfaces/IPlatform.py',
                     'tools/interfaces/IUserMonitor.py', 'tools/helpers/drivers.py'):
            file = host / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b'' if name.endswith('__init__.py') else b'raise RuntimeError("must not execute")\n')
            file.chmod(0o644)
        admitted = self.p.admit_host_namespace(host)
        self.assertEqual(len(admitted), 7)  # Installed interfaces is a Python namespace package.
        unsafe = host / 'tools/foreign.py'
        unsafe.write_bytes(b'raise RuntimeError("must not execute")\n')
        unsafe.chmod(0o666)
        with self.assertRaises(ValueError):
            self.p.admit_host_namespace(host)
        unsafe.unlink()
        unsafe.symlink_to(host / 'tools/actions/session_manager.py')
        with self.assertRaises((OSError, ValueError)):
            self.p.admit_host_namespace(host)


if __name__ == '__main__':
    unittest.main()
