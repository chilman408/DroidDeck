# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, mock_open

spec = importlib.util.spec_from_file_location("native_stop", Path(__file__).resolve().parents[1] / "runtime/waydroid-display/native-stop.py")
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
OWNER = "a" * 32
ENV = f"TESLA_WAYDROID_RUNTIME_OWNER={OWNER}\0TESLA_WAYDROID_RUNTIME_ROLE=api\0".encode()


class NativeProcessTests(unittest.TestCase):
    def test_shell_video_capture_is_owned_only_with_matching_fixed_server_and_scid(self):
        video = ENV.replace(b"ROLE=api", b"ROLE=video")
        command = b"/system/bin/app_process\0/system/bin\0com.genymobile.scrcpy.Server\0" + b"4.0\0scid=54410001\0"
        self.assertTrue(native.shell_capture_owned(command, video, OWNER, "video"))
        self.assertTrue(native.shell_capture_owned(command, video, OWNER, "all"))
        for wrong in (command.replace(b"54410001", b"54410002"), command + b"scid=54410002\0",
                      command.replace(b"4.0", b"3.0"), command.replace(b"scrcpy.Server", b"other.Server"),
                      native.BASE + b"tesla-android-x86-api\0"):
            self.assertFalse(native.shell_capture_owned(wrong, video, OWNER, "all"))
        self.assertFalse(native.shell_capture_owned(command, video, "b" * 32, "all"))
        self.assertFalse(native.shell_capture_owned(command, ENV, OWNER, "all"))
        self.assertFalse(native.shell_capture_owned(command, video, OWNER, "audio"))

    def test_audio_can_drop_to_shell_uid_without_escaping_its_owned_cleanup(self):
        audio = ENV.replace(b"ROLE=api", b"ROLE=audio")
        self.assertTrue(native.environment_owned(audio, OWNER, "all", uid=2000))
        self.assertTrue(native.environment_owned(audio, OWNER, "audio", uid=2000))
        self.assertFalse(native.environment_owned(ENV, OWNER, "all", uid=2000))
        self.assertFalse(native.environment_owned(audio, OWNER, "all", uid=10000))

    def test_shell_cleanup_rechecks_command_and_role_before_signalling(self):
        environment = ENV.replace(b"ROLE=api", b"ROLE=video")
        prefix = b"/system/bin/app_process\0/system/bin\0com.genymobile.scrcpy.Server\0" + b"4.0\0"
        for command, expected in [(prefix + b"scid=54410001\0", True), (prefix + b"scid=54410002\0", False)]:
            with patch.object(native, "candidates", side_effect=[[4242], []]), \
                    patch.object(native, "birth", return_value=b"100"), patch.object(native, "process_uid", return_value=2000), \
                    patch.object(native, "guest", return_value=True), patch.object(native, "read_environment", return_value=environment), \
                    patch("builtins.open", mock_open(read_data=command)), \
                    patch.object(native.os, "pidfd_open", return_value=99, create=True), \
                    patch.object(native.signal, "pidfd_send_signal", create=True) as send, \
                    patch.object(native.os, "close"), patch.object(native.time, "sleep"):
                native.stop(OWNER, "video")
                self.assertEqual(send.called, expected)

    def test_all_cleanup_includes_capture_but_not_unrelated_roles(self):
        for role in ("video", "audio"):
            environment = ENV.replace(b"ROLE=api", b"ROLE=" + role.encode())
            self.assertTrue(native.environment_owned(environment, OWNER, "all"))
            self.assertTrue(native.environment_owned(environment, OWNER, role))
            self.assertFalse(native.environment_owned(environment, OWNER, "display"))
        self.assertFalse(native.environment_owned(ENV.replace(b"ROLE=api", b"ROLE=sshd"), OWNER, "all"))

    def test_vacancy_recognizes_only_fixed_capture_commands(self):
        prefix = b"/system/bin/app_process\0/system/bin\0com.genymobile.scrcpy.Server\0" + b"4.0\0"
        for scid in (b"54410001", b"54410002"):
            self.assertTrue(native.is_preview_command(prefix + b"scid=" + scid + b"\0control=false\0"))
        for command in (prefix + b"scid=00000000\0", prefix + b"scid=544100010\0",
                        prefix.replace(b"com.genymobile.scrcpy.Server", b"other.Server") + b"scid=54410001\0"):
            self.assertFalse(native.is_preview_command(command))

    def test_only_exact_single_ownership_and_role_fields_match(self):
        self.assertTrue(native.environment_owned(ENV, OWNER, "api"))
        self.assertTrue(native.environment_owned(ENV, OWNER, "all"))
        for value in (ENV + ENV, ENV.replace(b"=api", b"=display"), ENV.replace(b"OWNER=", b"OWNERS="), b""):
            self.assertFalse(native.environment_owned(value, OWNER, "api"))
        self.assertFalse(native.environment_owned(ENV, "b" * 32, "api"))

    def test_pid_handle_is_used_after_birth_and_ownership_recheck(self):
        with patch.object(native, "candidates", side_effect=[[4242], []]), patch.object(native, "birth", return_value=b"100"), \
                patch.object(native, "process_uid", return_value=0), \
                patch.object(native, "guest", return_value=True), patch.object(native, "read_environment", return_value=ENV), \
                patch.object(native.os, "pidfd_open", return_value=99, create=True) as opened, \
                patch.object(native.signal, "pidfd_send_signal", create=True) as send, \
                patch.object(native.os, "close") as closed, patch.object(native.time, "sleep"):
            native.stop(OWNER, "api")
            opened.assert_called_once_with(4242)
            send.assert_called_once_with(99, native.signal.SIGTERM)
            closed.assert_called_once_with(99)

    def test_reused_pid_cannot_be_signalled(self):
        with patch.object(native, "candidates", side_effect=[[4242], []]), patch.object(native, "birth", side_effect=[b"100", b"200"]), \
                patch.object(native, "process_uid", return_value=0), \
                patch.object(native, "guest", return_value=True), patch.object(native.os, "pidfd_open", return_value=99, create=True), \
                patch.object(native.signal, "pidfd_send_signal", create=True) as send, \
                patch.object(native.os, "close") as closed, patch.object(native.time, "sleep"):
            native.stop(OWNER, "api")
            send.assert_not_called()
            closed.assert_called_once_with(99)

    def test_only_still_owned_handles_escalate_after_the_grace_period(self):
        with patch.object(native, "candidates", side_effect=[[4242], []]), patch.object(native, "birth", return_value=b"100"), \
                patch.object(native, "process_uid", return_value=0), \
                patch.object(native, "guest", return_value=True), patch.object(native, "read_environment", return_value=ENV), \
                patch.object(native.os, "pidfd_open", return_value=99, create=True), \
                patch.object(native.signal, "pidfd_send_signal", create=True) as send, \
                patch.object(native.os, "close"), patch.object(native.time, "sleep"), \
                patch.object(native.time, "monotonic", side_effect=[0, 4, 4]), \
                patch.object(native.signal, "SIGKILL", 9, create=True):
            native.stop(OWNER, "api")
            send.assert_called_once_with(99, native.signal.SIGKILL)

    def test_permission_failure_is_not_reported_as_successful_cleanup(self):
        with patch.object(native, "candidates", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                native.stop(OWNER, "api")


if __name__ == "__main__":
    unittest.main()
