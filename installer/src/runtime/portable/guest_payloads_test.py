# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('promote', Path(__file__).with_name('promote-images.py'))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
sha = lambda b: hashlib.sha256(b).hexdigest()
NAMES = ('bin/tesla-android-x86-api', 'bin/tesla-android-x86-display-latest', 'bin/tesla-android-x86-touch',
         'location-bridge.jar', 'logical-capture.jar', 'scrcpy-server-v4.0.jar', 'logical-screencap')


class GuestPolicyTests(unittest.TestCase):
    def test_fixed_cli_no_path_pin_or_pid_override(self):
        with mock.patch.object(P.sys, 'platform', 'linux'), mock.patch.object(P.os, 'geteuid', return_value=0, create=True), mock.patch.object(P, 'load_copier'), mock.patch.object(P, '_install_guest_payloads') as install:
            P.main(['--guest-payloads'])
            self.assertEqual(len(install.call_args.args), 1)
            self.assertEqual(install.call_args.kwargs, {})
            for args in (['--guest-payloads', '/data'], ['--guest-payloads', '123']):
                with self.assertRaises(ValueError):
                    P.main(args)

    def test_no_stopped_or_invalid_container_can_be_admitted(self):
        with mock.patch.object(P.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=b'0\n')):
            with self.assertRaises(ValueError):
                P._guest_process_identity()
        with mock.patch.object(P.subprocess, 'run', return_value=mock.Mock(returncode=1, stdout=b'22\n')):
            with self.assertRaises(ValueError):
                P._guest_process_identity()

    def test_bounded_process_failure_reports_no_commit(self):
        with mock.patch.object(P, 'main', side_effect=P.subprocess.TimeoutExpired('generated', 5)), mock.patch.object(P, 'diagnostic') as report:
            self.assertEqual(P.run_cli(['--guest-payloads']), 1)
            self.assertIn('did not complete', report.call_args.args[0])


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux fixtures required')
class GuestInstallTests(unittest.TestCase):
    def setUp(self):
        self.copier = P.load_copier()
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-guest-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.tmp = self.base / 'guest-tmp'
        self.tmp.mkdir(mode=0o771)
        os.chown(self.tmp, 2000, 2000)
        self.source = self.base / 'source'
        self.source.mkdir(mode=0o700)
        (self.source / 'bin').mkdir(mode=0o700)
        self.pins = {}
        for name in NAMES:
            p = self.source / name
            data = ('generated non-executed fixture ' + name).encode()
            p.write_bytes(data)
            mode = 0o700 if name.startswith('bin/') or name == 'logical-screencap' else 0o600
            p.chmod(mode)
            self.pins[name] = (len(data), sha(data), mode)

    def context(self, stack, copier):
        chain = copier.open_chain(stack, self.tmp)
        before = self.tmp.lstat()
        def check():
            copier.check_chain(chain)
            self.assertEqual((self.tmp.stat().st_dev, self.tmp.stat().st_ino), (before.st_dev, before.st_ino))
        return {'tmp_chain': chain, 'check': check, 'pid': 123, 'birth': 456,
                'dataDevice': before.st_dev, 'dataInode': before.st_ino}

    def sources(self, stack, copier):
        result = {}
        for name, (size, digest, mode) in self.pins.items():
            p = self.source / name
            chain = copier.open_chain(stack, p.parent, root_owned=True)
            fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=chain[-1][1])
            stack.callback(os.close, fd)
            result[name] = (chain, p.name, fd, os.fstat(fd), (size, digest), mode)
        return result

    def install(self, **kwargs):
        return P._install_guest_payloads(self.copier, context=kwargs.get('context', self.context), sources=self.sources)

    def test_independent_pinned_files_private_empty_library_directory_no_execution_or_replay(self):
        r = self.install()
        self.assertFalse(r['helperProcessesStarted'])
        self.assertFalse(r['accountsOrDrmChanged'])
        self.assertFalse(r['browserPngFallback'])
        root = self.tmp / P.GUEST_TARGET
        self.assertEqual({p.relative_to(root).as_posix() for p in root.rglob('*')}, set(NAMES) | {'bin', 'lib64', 'droiddeck-guest-receipt.json'})
        for p in [root, root / 'bin', root / 'lib64']:
            self.assertEqual((p.stat().st_uid, p.stat().st_gid, p.stat().st_mode & 0o777), (0, 0, 0o700))
        for n, (_, digest, mode) in self.pins.items():
            p = root / n
            self.assertEqual((p.stat().st_uid, p.stat().st_gid, p.stat().st_mode & 0o777, p.stat().st_nlink), (0, 0, mode, 1))
            self.assertEqual(sha(p.read_bytes()), digest)
            self.assertNotEqual(p.stat().st_ino, (self.source / n).stat().st_ino)
        with self.assertRaises(ValueError):
            self.install()

    def test_existing_foreign_final_and_pending_refuse_without_replacement(self):
        (self.tmp / P.GUEST_TARGET).mkdir()
        (self.tmp / P.GUEST_TARGET / 'sentinel').write_bytes(b'preserve')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual((self.tmp / P.GUEST_TARGET / 'sentinel').read_bytes(), b'preserve')

    def test_corrupted_source_refuses_before_stage(self):
        (self.source / NAMES[0]).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_linked_source_cannot_be_opened(self):
        p = self.source / NAMES[0]
        p.unlink()
        p.symlink_to(self.source / NAMES[1])
        with self.assertRaises(OSError):
            self.install()
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_invalid_mount_context_refuses_before_stage(self):
        def refuse(*_):
            raise ValueError('generated mount mismatch')
        with self.assertRaises(ValueError):
            self.install(context=refuse)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_midstage_container_change_refuses_publication(self):
        def context(stack, copier):
            value = self.context(stack, copier)
            def check():
                if (self.tmp / 'pending-droiddeck-guest').exists():
                    raise ValueError('generated container changed')
            value['check'] = check
            return value
        with self.assertRaises(ValueError):
            self.install(context=context)
        self.assertFalse((self.tmp / P.GUEST_TARGET).exists())

    def test_raced_empty_final_directory_never_replaced(self):
        rename = P.rename_exclusive
        def race(fd, old, new):
            (self.tmp / new).mkdir()
            return rename(fd, old, new)
        with mock.patch.object(P, 'rename_exclusive', side_effect=race), self.assertRaises(OSError):
            self.install()
        self.assertEqual(list((self.tmp / P.GUEST_TARGET).iterdir()), [])

    def test_postpublish_sync_failure_retains_published_payload_and_reports_uncertain(self):
        sync = os.fsync
        def fail(fd):
            if (self.tmp / P.GUEST_TARGET).exists():
                raise OSError('generated disk error')
            return sync(fd)
        with mock.patch.object(P.os, 'fsync', side_effect=fail), self.assertRaises(P.PromotionUncertain):
            self.install()
        self.assertEqual(sha((self.tmp / P.GUEST_TARGET / NAMES[0]).read_bytes()), self.pins[NAMES[0]][1])


if __name__ == '__main__':
    unittest.main()
