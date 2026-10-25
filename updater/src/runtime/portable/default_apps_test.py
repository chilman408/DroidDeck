# SPDX-License-Identifier: GPL-3.0-only
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('default_apps_tested', Path(__file__).with_name('default-apps.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)


class RequiredDefaults(unittest.TestCase):
    def test_signature_and_package_are_independent_fixed_requirements(self):
        D.signer('Signer #1 certificate SHA-256 digest: ' + D.CERTIFICATE)
        D.badging("package: name='com.aurora.store' versionCode='76' versionName='4.8.4' platformBuildVersionName='16'")
        for output in ('', 'Signer #1 certificate SHA-256 digest: ' + 'a' * 64,
                       ('Signer #1 certificate SHA-256 digest: ' + D.CERTIFICATE + '\n') * 2):
            with self.assertRaises(ValueError):
                D.signer(output)
        for package in ('com.google.android.gms', 'com.android.vending', 'other.app'):
            with self.assertRaises(ValueError):
                D.badging("package: name='" + package + "' versionCode='76' versionName='4.8.4'")

    def test_existing_app_is_never_replaced_or_reset(self):
        guest = mock.Mock()
        guest.inspect.return_value = {'installed': True, 'defaultsSatisfied': True}
        with mock.patch.object(D, 'hold_apk', side_effect=AssertionError('must not read APK')):
            result = D.install_missing(guest, '/never-read')
        self.assertTrue(result['existingAppPreserved'])
        guest.command.assert_not_called()

    def test_missing_app_installs_only_fixed_package_without_replace_flags(self):
        guest = mock.Mock()
        guest.inspect.side_effect = [{'installed': False}, {'installed': False}, {'defaultsSatisfied': True}]
        guest.command.return_value = 'Success'
        check = mock.Mock()
        @contextmanager
        def held(_filename):
            yield 42, check
        with mock.patch.object(D, 'hold_apk', held), mock.patch.object(D, 'run', side_effect=[
            'Signer #1 certificate SHA-256 digest: ' + D.CERTIFICATE,
            "package: name='com.aurora.store' versionCode='76' versionName='4.8.4'"]), mock.patch.object(D.os, 'lseek'):
            result = D.install_missing(guest, '/private/apk')
        self.assertEqual(guest.command.call_args.args[0], ['/system/bin/pm', 'install', '--user', '0', '-S', str(D.SIZE)])
        self.assertFalse(result['otherAppsChanged'])
        self.assertFalse(result['automaticAppUpdatesConfigured'])
        self.assertFalse(result['accountsConfigured'])
        self.assertEqual(check.call_count, 2)

    def test_missing_requirement_fails_build_inspection(self):
        with mock.patch.object(D.sys, 'platform', 'linux'), mock.patch.object(D.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(D, 'Guest') as guest, mock.patch('builtins.print'):
            guest.return_value.inspect.return_value = {'defaultsSatisfied': False}
            self.assertEqual(D.main(['--inspect']), 2)

    def test_android_missing_package_exit_is_not_confused_with_package_manager_failure(self):
        guest = D.Guest.__new__(D.Guest)
        guest.command = mock.Mock(side_effect=D.subprocess.CalledProcessError(1, ['pm'], output=b'', stderr=b''))
        self.assertFalse(guest.inspect()['defaultsSatisfied'])
        for code, output, error in ((2, b'', b''), (1, b'', b'Permission denied'), (1, b'failure', b'')):
            guest.command.side_effect = D.subprocess.CalledProcessError(code, ['pm'], output=output, stderr=error)
            with self.assertRaises(D.subprocess.CalledProcessError):
                guest.inspect()

    def test_wrong_signature_never_calls_install(self):
        guest = mock.Mock()
        guest.inspect.return_value = {'installed': False}
        @contextmanager
        def held(_filename):
            yield 42, lambda: None
        with mock.patch.object(D, 'hold_apk', held), mock.patch.object(D, 'run', return_value='foreign'), self.assertRaises(ValueError):
            D.install_missing(guest, '/private/apk')
        guest.command.assert_not_called()


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'Root Linux artifact fixtures only')
class PrivateArtifact(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir='/root', prefix='droiddeck-default-app-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / 'fixture.apk'
        self.file.write_bytes(b'test')
        self.file.chmod(0o600)
        for name, value in [('SIZE', 4), ('SHA256', D.hashlib.sha256(b'test').hexdigest())]:
            patch = mock.patch.object(D, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_held_bytes_and_inode_replacement_are_checked(self):
        with self.assertRaises(ValueError):
            with D.hold_apk(str(self.file)) as (_, check):
                check()
                other = self.root / 'replacement'
                other.write_bytes(b'test')
                other.chmod(0o600)
                other.replace(self.file)
                with self.assertRaises(ValueError):
                    check()
                # Context exit must also reject the replaced named object.

    def test_wrong_hash_links_and_writable_permissions_refuse(self):
        def refuses():
            with self.assertRaises(ValueError), D.hold_apk(str(self.file)):
                pass
        self.file.write_bytes(b'evil')
        refuses()
        self.file.write_bytes(b'test')
        self.file.chmod(0o644)
        refuses()
        self.file.chmod(0o600)
        linked = self.root / 'linked'
        os.link(self.file, linked)
        refuses()
        linked.unlink()
        self.file.unlink()
        self.file.symlink_to(linked)
        with self.assertRaises(OSError), D.hold_apk(str(self.file)):
            pass


if __name__ == '__main__':
    unittest.main()
