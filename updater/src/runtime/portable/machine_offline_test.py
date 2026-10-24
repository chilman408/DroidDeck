# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest import mock


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


P = module('machine_offline_promote', 'promote-images.py')
G = module('offline_guest_fixtures', 'guest_payloads_test.py')
sha = lambda data: hashlib.sha256(data).hexdigest()
GENERATION, METADATA = 'a' * 64, 'b' * 64
RECORDS = [{'path': name, 'size': 10, 'sha256': 'c' * 64, 'mode': 0o700 if name.startswith('bin/') or name == 'logical-screencap' else 0o600} for name in sorted(G.NAMES)]


def receipt():
    return {'kind': 'protected-guest-payloads', 'version': 2, 'installable': False, 'readyForActivation': False,
            'pid': 123, 'birth': 456, 'dataDevice': 1, 'dataInode': 2, 'files': RECORDS,
            'helperProcessesStarted': False, 'accountsOrDrmChanged': False, 'browserPngFallback': False,
            'configurationDigest': GENERATION, 'metadataSha256': METADATA, 'accountValidated': True,
            'guestMountMatched': True, 'servicesActivated': False, 'androidOperationsExecuted': False}


class OfflinePolicy(unittest.TestCase):
    def test_receipt_is_closed_machine_bound_and_not_a_caller_file_allowlist(self):
        self.assertTrue(callable(getattr(P, '_machine_guest_receipt', None)), 'Independent offline receipt validation missing')
        self.assertEqual(P._machine_guest_receipt(json.dumps(receipt()).encode(), GENERATION, METADATA, RECORDS), receipt())
        for change in ({'version': 1}, {'version': 2.0}, {'metadataSha256': 'c' * 64}, {'configurationDigest': 'd' * 64},
                       {'accountValidated': False}, {'guestMountMatched': False}, {'password': 'generated'}, {'pid': True},
                       {'files': [{**r, 'sha256': 'd' * 64} for r in RECORDS]}, {'dataDevice': 0}):
            with self.assertRaises(ValueError):
                P._machine_guest_receipt(json.dumps({**receipt(), **change}).encode(), GENERATION, METADATA, RECORDS)
        with self.assertRaises(ValueError):
            P._machine_guest_receipt(b'{"version":2,"version":2}', GENERATION, METADATA, RECORDS)
        for files in (RECORDS[::-1], RECORDS[:-1], [{**r, 'size': True} for r in RECORDS],
                      [{**r, 'mode': 0o777} for r in RECORDS], [{**r, 'sha256': 'C' * 64} for r in RECORDS]):
            with self.assertRaises(ValueError):
                P._machine_guest_receipt(json.dumps({**receipt(), 'files': files}).encode(), GENERATION, METADATA, RECORDS)

    def test_fixed_readonly_cli_has_no_force_data_or_receipt_hash_override(self):
        self.assertTrue(callable(getattr(P, '_machine_offline_data_policy', None)), 'Machine offline policy missing')
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(P, 'load_copier') as copier, mock.patch.object(P, '_machine_offline_data_policy') as inspect:
            P.main(['--machine-offline-data-policy'])
            self.assertEqual(inspect.call_args.args, (copier.return_value,))
            for extra in ('--force', '/other/data', 'b' * 64):
                with self.assertRaises(ValueError):
                    P.main(['--machine-offline-data-policy', extra])

    def test_stopped_guard_requires_zero_service_pids_and_stopped_lxc(self):
        self.assertTrue(callable(getattr(P, '_assert_machine_guest_stopped', None)), 'Strict stopped guest guard missing')
        good = mock.Mock(returncode=0, stdout=b'ActiveState=inactive\nMainPID=0\nControlPID=0\n')
        stopped = mock.Mock(returncode=0, stdout=b'STOPPED\n')
        with mock.patch.object(P.subprocess, 'run', side_effect=[good] * 4 + [stopped]) as run:
            P._assert_machine_guest_stopped()
            self.assertEqual(run.call_args.args[0], ['/usr/bin/lxc-info', '-P', '/var/lib/waydroid/lxc', '-n', 'waydroid', '-sH'])
        for raw in (b'ActiveState=active\nMainPID=12\nControlPID=0\n', b'ActiveState=inactive\nMainPID=0\nControlPID=12\n',
                    b'ActiveState=inactive\nMainPID=0\n', b'x' * 4097):
            with mock.patch.object(P.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=raw)), self.assertRaises(ValueError):
                P._assert_machine_guest_stopped()
        with mock.patch.object(P.subprocess, 'run', side_effect=[good] * 4 + [mock.Mock(returncode=0, stdout=b'RUNNING\n')]), self.assertRaises(ValueError):
            P._assert_machine_guest_stopped()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux generated offline fixtures required')
class OfflineContext(unittest.TestCase):
    def setUp(self):
        self.fixture = G.GuestInstallTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.copier, self.root = self.fixture.copier, self.fixture.base
        self.home = self.root / 'home'
        self.data = self.home / '.local/share/waydroid/data'
        self.tmp = self.data / 'local/tmp'
        self.tmp.mkdir(parents=True)
        self.data.chmod(0o771)
        os.chown(self.data, 1500, 1600)
        self.tmp.chmod(0o771)
        os.chown(self.tmp, 2000, 2000)
        self.property = self.data / 'property'
        self.property.mkdir(mode=0o700)
        (self.property / 'persistent_properties').write_bytes(b'generated-private-document-must-not-be-read')
        self.metadata_root = self.root / 'metadata'
        self.metadata_root.mkdir(mode=0o755)
        self.metadata = self.metadata_root / 'runtime.json'
        self.config = {'version': 1, 'user': 'generated', 'uid': 1500, 'gid': 1600,
                       'home': str(self.home), 'lanAddress': '192.168.50.5', 'port': 9443, 'hostname': 'droiddeck.example.com'}
        self.metadata.write_text(json.dumps(self.config))
        self.metadata.chmod(0o644)
        def context(stack, copier):
            chain = copier.open_chain(stack, self.tmp)
            return {'tmp_chain': chain, 'check': lambda: copier.check_chain(chain), 'pid': 123, 'birth': 456,
                    'dataDevice': self.data.stat().st_dev, 'dataInode': self.data.stat().st_ino,
                    'configurationDigest': GENERATION, 'metadataSha256': sha(self.metadata.read_bytes()),
                    'accountValidated': True, 'guestMountMatched': True}
        P._install_machine_guest_payloads(self.copier, context=context, sources=self.fixture.sources)
        self.guest = self.tmp / P.MACHINE_GUEST_TARGET
        self.marker = self.guest / 'droiddeck-guest-receipt.json'

    def validate(self, encoded):
        self.assertEqual(json.loads(encoded), self.config)
        return {'config': self.config, 'configurationDigest': GENERATION}

    def admit(self, stack, **kw):
        return P._machine_offline_context(stack, self.copier, self.metadata_root, validate=self.validate,
            inactive=kw.get('inactive', lambda: None), sources=self.fixture.sources)

    def test_holds_nondefault_machine_data_and_recipe_without_reading_saved_properties(self):
        read, names = P._read_profile_file, []
        def observe(*args, **kwargs):
            names.append(args[3])
            return read(*args, **kwargs)
        with ExitStack() as stack, mock.patch.object(P, '_read_profile_file', side_effect=observe):
            ctx = self.admit(stack)
            ctx['check']()
            self.assertEqual(ctx['property'], self.property)
            self.assertEqual(ctx['dataInode'], self.data.stat().st_ino)
            self.assertFalse(ctx['accountValidated'])
            self.assertFalse(ctx['guestPayloadsVerified'])
            self.assertFalse(ctx['servicesStoppedVerified'])
        self.assertNotIn('persistent_properties', names)

    def test_busy_guard_runs_before_any_metadata_or_android_file_read(self):
        def busy():
            raise ValueError('generated active services')
        with ExitStack() as stack, mock.patch.object(P, '_read_profile_file', side_effect=AssertionError('must not read')), self.assertRaises(ValueError):
            self.admit(stack, inactive=busy)

    def test_v1_stale_false_proof_or_other_machine_receipt_refuses(self):
        original = self.marker.read_bytes()
        for change in ({'version': 1}, {'configurationDigest': 'c' * 64}, {'metadataSha256': 'c' * 64},
                       {'guestMountMatched': False}, {'dataInode': self.data.stat().st_ino + 1}):
            self.marker.write_text(json.dumps({**json.loads(original), **change}))
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.admit(stack)
        self.marker.write_bytes(original)

    def test_rewritten_payload_and_receipt_hashes_cannot_replace_independent_recipe(self):
        file = self.guest / G.NAMES[0]
        file.write_bytes(b'generated replacement')
        value = json.loads(self.marker.read_bytes())
        next(r for r in value['files'] if r['path'] == G.NAMES[0]).update(size=file.stat().st_size, sha256=sha(file.read_bytes()))
        self.marker.write_text(json.dumps(value))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)

    def test_extra_private_file_and_nonempty_library_directory_refuse(self):
        for relative in ('unexpected.pem', 'lib64/foreign.so'):
            extra = self.guest / relative
            extra.write_bytes(b'generated')
            with ExitStack() as stack, self.assertRaises(ValueError):
                self.admit(stack)
            extra.unlink()

    def test_hardlinked_receipt_and_symlinked_payload_refuse(self):
        link = self.root / 'linked'
        os.link(self.marker, link)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        link.unlink()
        file = self.guest / G.NAMES[0]
        file.unlink()
        file.symlink_to(self.fixture.source / G.NAMES[0])
        with ExitStack() as stack, self.assertRaises((ValueError, OSError)):
            self.admit(stack)

    def test_metadata_or_payload_change_after_admission_refuses_recheck(self):
        with ExitStack() as stack:
            ctx = self.admit(stack)
            self.metadata.write_bytes(b'generated changed metadata')
            with self.assertRaises(ValueError):
                ctx['check']()
        self.metadata.write_text(json.dumps(self.config))
        with ExitStack() as stack:
            ctx = self.admit(stack)
            (self.guest / G.NAMES[0]).write_bytes(b'generated changed payload')
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_same_bytes_receipt_inode_replacement_or_property_namespace_change_refuses(self):
        with ExitStack() as stack:
            ctx = self.admit(stack)
            new = self.guest / 'replacement'
            new.write_bytes(self.marker.read_bytes())
            new.chmod(0o600)
            os.replace(new, self.marker)
            with self.assertRaises(ValueError):
                ctx['check']()
        with ExitStack() as stack:
            ctx = self.admit(stack)
            self.property.chmod(0o777)
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_foreign_data_owner_or_relocated_same_receipt_cannot_admit_other_data(self):
        os.chown(self.data, 1501, 1601)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)
        os.chown(self.data, 1500, 1600)
        value = json.loads(self.marker.read_bytes())
        value['dataDevice'] += 1
        self.marker.write_text(json.dumps(value))
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)

    def test_independent_source_mutation_after_admission_refuses(self):
        with ExitStack() as stack:
            ctx = self.admit(stack)
            (self.fixture.source / G.NAMES[0]).write_bytes(b'generated source changed')
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_service_restart_after_admission_refuses_recheck(self):
        inactive = mock.Mock()
        with ExitStack() as stack:
            ctx = self.admit(stack, inactive=inactive)
            inactive.side_effect = ValueError('generated service restarted')
            with self.assertRaises(ValueError):
                ctx['check']()

    def test_same_bytes_payload_replacement_and_writable_guest_directory_refuse(self):
        with ExitStack() as stack:
            ctx = self.admit(stack)
            file = self.guest / G.NAMES[0]
            replacement = self.root / 'replacement-payload'
            replacement.write_bytes(file.read_bytes())
            replacement.chmod(0o700)
            os.replace(replacement, file)
            with self.assertRaises(ValueError):
                ctx['check']()
        (self.guest / 'bin').chmod(0o777)
        with ExitStack() as stack, self.assertRaises(ValueError):
            self.admit(stack)

    def test_policy_exports_hashes_only_and_no_activation_claim(self):
        with ExitStack() as stack:
            ctx = self.admit(stack)
            with mock.patch.object(P, '_machine_offline_context', return_value=ctx):
                result = P._machine_offline_data_policy(self.copier)
        self.assertNotIn('generated-private-document', json.dumps(result))
        self.assertNotIn(str(self.home), json.dumps(result))
        for flag in ('accountValidated', 'guestPayloadsVerified', 'servicesStoppedVerified', 'servicesActivated',
                     'androidOperationsExecuted', 'installable', 'readyForActivation'):
            self.assertFalse(result[flag])


if __name__ == '__main__':
    unittest.main()
