import subprocess
import unittest

from tools.vm_probe import probe_vm


class FakeAdb:
    def __init__(self, overrides=None):
        self.responses = {
            ("getprop", "ro.build.version.release"): "15\n",
            ("getprop", "ro.product.cpu.abi"): "x86_64\n",
            ("pm", "list", "packages"): "package:com.android.settings\n",
            ("ip", "-o", "addr", "show", "scope", "global"): "2: eth0 inet 192.0.2.5/24 scope global eth0\n",
            ("dumpsys", "SurfaceFlinger"): "Display 0\n",
            ("dumpsys", "media.audio_policy"): "Available output devices: AUDIO_DEVICE_OUT_SPEAKER\n",
        }
        self.responses.update(overrides or {})

    def shell(self, *args):
        return self.responses[args]


class VmProbeTests(unittest.TestCase):
    def test_clean_android_15_x86_guest_passes(self):
        report = probe_vm(FakeAdb())

        self.assertTrue(report["ok"])
        self.assertEqual("15", report["android_release"])
        self.assertEqual("x86_64", report["abi"])
        self.assertTrue(report["network_ok"])
        self.assertTrue(report["display_ok"])
        self.assertTrue(report["audio_ok"])
        self.assertEqual([], report["google_packages"])

    def test_android_14_fails(self):
        report = probe_vm(FakeAdb({("getprop", "ro.build.version.release"): "14\n"}))

        self.assertFalse(report["ok"])
        self.assertTrue(any("Android 15" in issue for issue in report["failures"]))

    def test_arm64_fails(self):
        report = probe_vm(FakeAdb({("getprop", "ro.product.cpu.abi"): "arm64-v8a\n"}))

        self.assertFalse(report["ok"])
        self.assertTrue(any("x86_64" in issue for issue in report["failures"]))

    def test_google_packages_fail_clean_base_check(self):
        packages = "package:com.android.vending\npackage:com.google.android.gms\n"
        report = probe_vm(FakeAdb({("pm", "list", "packages"): packages}))

        self.assertFalse(report["ok"])
        self.assertEqual(["com.android.vending", "com.google.android.gms"], report["google_packages"])

    def test_missing_display_audio_or_network_is_not_a_pass(self):
        report = probe_vm(FakeAdb({
            ("ip", "-o", "addr", "show", "scope", "global"): "",
            ("dumpsys", "SurfaceFlinger"): "Can't find service: SurfaceFlinger",
            ("dumpsys", "media.audio_policy"): "Can't find service: media.audio_policy",
        }))

        self.assertFalse(report["ok"])
        self.assertFalse(report["network_ok"])
        self.assertFalse(report["display_ok"])
        self.assertFalse(report["audio_ok"])

    def test_adb_failure_does_not_expose_serial_in_report(self):
        class FailingAdb:
            def shell(self, *args):
                raise subprocess.CalledProcessError(1, ["adb", "-s", "private-serial", "shell", *args])

        report = probe_vm(FailingAdb())

        self.assertFalse(report["ok"])
        self.assertNotIn("private-serial", str(report))


if __name__ == "__main__":
    unittest.main()
