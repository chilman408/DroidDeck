# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('host_tested', Path(__file__).with_name('host-prerequisites.py'))
D = importlib.util.module_from_spec(spec)
spec.loader.exec_module(D)
NATIVE = sys.platform == 'linux' and os.geteuid() == 0


class Policy(unittest.TestCase):
    def test_closed_apt_commands_and_environment(self):
        with mock.patch.object(D, 'run') as run:
            D.apt('install', *D.BASE)
            args = run.call_args.args[0]
            self.assertEqual(args[0], '/usr/bin/apt-get')
            for option in ('--no-remove', '--no-upgrade', '--no-install-recommends'):
                self.assertIn(option, args)
            with self.assertRaises(ValueError):
                D.apt('dist-upgrade')
            with self.assertRaises(ValueError):
                D.apt('update', 'foreign')
        with mock.patch.dict(os.environ, {'APT_CONFIG': '/foreign', 'HTTP_PROXY': 'foreign'}):
            env = D.environment()
        self.assertEqual(env['APT_CONFIG'], str(D.STATE / 'apt-policy.conf'))
        self.assertNotIn('HTTP_PROXY', env)
        self.assertEqual(D.APT_POLICY.count(b'"false"'), 3)
        self.assertIn('pipewire-pulse', D.RUNTIME)
        self.assertIn('gstreamer1.0-plugins-ugly', D.RUNTIME)
        self.assertIn('gstreamer1.0-pipewire', D.RUNTIME)
        self.assertIn('gstreamer1.0-tools', D.RUNTIME)
        # Aurora is required, including independent host-side APK verification.
        # The explicit full JRE17 satisfies apksigner without selecting JRE25.
        for package in ('openjdk-17-jre', 'apksigner', 'aapt'):
            self.assertIn(package, D.RUNTIME)

    def test_exact_installed_package_evidence_and_architectures(self):
        raw = b''.join((name + ':amd64\tinstall ok installed\t1.0\n').encode()
                       for name in D.BASE + D.RUNTIME)
        with mock.patch.object(D, 'run', return_value=raw):
            self.assertEqual(len(D.package_versions()), len(D.BASE + D.RUNTIME))
        for bad in (raw.replace(b':amd64', b':arm64', 1), raw + raw,
                    raw.replace(b'install ok installed', b'install ok unpacked', 1),
                    raw.split(b'\n', 1)[1]):
            with self.subTest(bad=bad[:40]), mock.patch.object(D, 'run', return_value=bad), \
                    self.assertRaises(ValueError):
                D.package_versions()

    def test_cli_invalid_mode_never_runs_commands(self):
        with mock.patch.object(D, 'run') as run:
            for args in ([], ['--force'], ['--install-host', '--force']):
                with self.assertRaises(ValueError):
                    D.main(args)
            run.assert_not_called()


@unittest.skipUnless(NATIVE, 'Root Linux protected-path fixtures required')
class Native(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='droiddeck-host-test-', dir='/root')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.object(D, 'STATE', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)
        self.intent = {'fixture': 'original-machine-and-code'}
        D.write_new(self.fd, 'intent.json', D.encode(self.intent))
        D.write_new(self.fd, 'apt-policy.conf', D.APT_POLICY)

    def fixture(self):
        return mock.patch.multiple(D, clean_target=mock.Mock(), apt=mock.Mock(),
                                   repository=mock.Mock(), verify_repository=mock.Mock(),
                                   package_versions=mock.Mock(return_value={'fixture': '1'}))

    def test_complete_resume_and_readonly_preserve_original_records(self):
        with self.fixture():
            result = D.finish(self.fd, self.intent, '--resume-host')
            self.assertTrue(result['signedAptRequired'])
            self.assertFalse(result['androidInitialized'])
            identities = {p.name: p.stat().st_ino for p in self.root.iterdir()}
            with mock.patch.object(D, 'apt', side_effect=AssertionError('APT replayed')), \
                    mock.patch.object(D, 'repository', side_effect=AssertionError('repo replayed')), \
                    mock.patch.object(D, 'write_new', side_effect=AssertionError('read-only write')):
                self.assertTrue(D.finish(self.fd, self.intent, '--inspect-host')['readOnly'])
                D.finish(self.fd, self.intent, '--resume-host')
            self.assertEqual(identities, {p.name: p.stat().st_ino for p in self.root.iterdir()})

    def test_incomplete_and_foreign_or_out_of_order_records_refuse(self):
        with self.fixture(), self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, '--inspect-host')
        D.write_new(self.fd, D.PHASES[-1] + '.json', D.encode({'phase': D.PHASES[-1], 'intent': self.intent}))
        with self.fixture(), mock.patch.object(D, 'apt') as apt, self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, '--resume-host')
        apt.assert_not_called()

    def test_changed_original_machine_or_policy_refuses_before_apt(self):
        with self.fixture(), self.assertRaises(ValueError):
            D.finish(self.fd, {'fixture': 'different-machine'}, '--resume-host')
        (self.root / 'apt-policy.conf').write_bytes(b'unsafe')
        with self.fixture(), mock.patch.object(D, 'apt') as apt, self.assertRaises(ValueError):
            D.finish(self.fd, self.intent, '--resume-host')
        apt.assert_not_called()

    def test_publisher_digest_failure_never_executes_root_script(self):
        with mock.patch.object(D, 'run', return_value=b'wrong publisher response') as run, \
                self.assertRaises(ValueError):
            D.repository(self.fd)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], '/usr/bin/curl')
        self.assertEqual(D.protected(self.root / 'repository.sh'), b'wrong publisher response')
        with mock.patch.object(D, 'run') as run, self.assertRaises(ValueError):
            D.repository(self.fd)
        run.assert_not_called()

    def test_actual_process_death_retries_only_unrecorded_apt_phase(self):
        child = '''import importlib.util,os,sys
from pathlib import Path
from unittest import mock
spec=importlib.util.spec_from_file_location('h',sys.argv[1]);h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
h.STATE=Path(sys.argv[2]);fd=os.open(h.STATE,os.O_RDONLY|os.O_DIRECTORY)
with mock.patch.object(h,'clean_target'),mock.patch.object(h,'apt'):
 h.finish(fd,{'fixture':'original-machine-and-code'},'--resume-host',after_phase=lambda phase:os._exit(73))
'''
        result = subprocess.run([sys.executable, '-I', '-B', '-c', child,
                                 str(Path(D.__file__).absolute()), str(self.root)], timeout=20)
        self.assertEqual(result.returncode, 73)
        self.assertFalse((self.root / (D.PHASES[0] + '.json')).exists())
        with self.fixture(), mock.patch.object(D, 'apt') as apt:
            D.finish(self.fd, self.intent, '--resume-host')
            self.assertEqual(apt.call_args_list[0], mock.call('update'))

    def test_protected_paths_refuse_symlinks_and_writable_inputs(self):
        original = self.root / 'intent.json'
        alias = self.root / 'alias.json'
        alias.symlink_to(original)
        with self.assertRaises(OSError):
            D.protected(alias)
        original.chmod(0o622)
        with self.assertRaises(ValueError):
            D.protected(original)


if __name__ == '__main__':
    unittest.main()
