# SPDX-License-Identifier: GPL-3.0-only
"""Finite systemd shutdown tests using generated dummy processes only."""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid


def command(args, timeout=10, check=True):
    return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout, check=check).stdout.decode().strip()


@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 0, 'Requires native root systemd')
class Shutdown(unittest.TestCase):
    def trial(self, slow_cleanup):
        unit = 'droiddeck-graph-shutdown-' + uuid.uuid4().hex + '.service'
        filename = str(Path(__file__).resolve())
        directory = tempfile.TemporaryDirectory(prefix='droiddeck-shutdown-', dir='/run')
        self.addCleanup(directory.cleanup)
        main_ready = str(Path(directory.name) / 'main-ready')
        cleanup_ready = str(Path(directory.name) / 'cleanup-ready')
        spec = importlib.util.spec_from_file_location('graph_shutdown', Path(__file__).with_name('graph-recovery.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = module.expected_supervision()
        self.assertEqual(settings['KillSignal'], 9)
        properties = ['Type=exec', 'KillMode=' + settings['KillMode'], 'KillSignal=SIGKILL',
                      'FinalKillSignal=SIGKILL', 'SendSIGKILL=yes',
                      'TimeoutStopFailureMode=' + settings['TimeoutStopFailureMode'],
                      'TimeoutStopSec=' + str(settings['TimeoutStopUSec'] // 1000000),
                      'RuntimeMaxSec=' + str(settings['RuntimeMaxUSec'] // 1000000), 'Restart=no',
                      'ExecStopPost=' + ('/usr/bin/python3 -B ' + filename + ' --linger ' + cleanup_ready if slow_cleanup else '/usr/bin/true')]
        arguments = ['/usr/bin/systemd-run', '--quiet', '--unit=' + unit]
        for value in properties:
            arguments.append('--property=' + value)
        try:
            command(arguments + ['/usr/bin/python3', '-B', filename, '--linger', main_ready])
            deadline = time.monotonic() + 5
            while not Path(main_ready).exists():
                if time.monotonic() >= deadline:
                    self.fail('Generated main did not acknowledge handler installation')
                time.sleep(.01)
            start = time.monotonic()
            command(['/usr/bin/systemctl', 'stop', unit], timeout=65, check=False)
            duration = time.monotonic() - start
            print('Dummy shutdown slowCleanup=' + str(slow_cleanup) + ' duration=' + format(duration, '.3f') + 's', flush=True)
            fields = dict(line.split('=', 1) for line in command(['/usr/bin/systemctl', 'show', unit,
                '-p', 'ActiveState', '-p', 'MainPID', '-p', 'ControlPID', '-p', 'Result']).splitlines())
            self.assertEqual((fields['MainPID'], fields['ControlPID']), ('0', '0'))
            self.assertIn(fields['ActiveState'], ('inactive', 'failed'))
            self.assertLess(duration, 65 if slow_cleanup else 5)
            if slow_cleanup:
                self.assertGreater(duration, 59)
                self.assertTrue(Path(cleanup_ready).is_file())
                # Main SIGKILL may remain the overall Result despite later cleanup timeout.
                self.assertIn(fields['Result'], ('signal', 'timeout'))
                import dbus
                bus = dbus.SystemBus()
                manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'),
                                         'org.freedesktop.systemd1.Manager')
                obj = bus.get_object('org.freedesktop.systemd1', manager.GetUnit(unit, timeout=5))
                properties = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
                post = properties.Get('org.freedesktop.systemd1.Service', 'ExecStopPost', timeout=5)
                self.assertEqual(tuple(map(int, post[0][-2:])), (2, 9))
        finally:
            command(['/usr/bin/systemctl', 'kill', '--kill-whom=all', '--signal=SIGKILL', unit], check=False)
            command(['/usr/bin/systemctl', 'stop', unit], timeout=65, check=False)
            command(['/usr/bin/systemctl', 'reset-failed', unit], check=False)

    def test_sigterm_ignoring_main_is_immediately_quiesced(self):
        self.trial(False)

    def test_sigterm_ignoring_cleanup_is_bounded_before_watcher_timeout(self):
        self.trial(True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--linger':
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        descriptor = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        time.sleep(600)
    else:
        unittest.main(verbosity=2)
