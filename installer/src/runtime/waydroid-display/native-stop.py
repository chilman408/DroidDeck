#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Stop only lease-tagged guest descendants using PID-safe Linux handles."""
import os
import re
import signal
import sys
import time

BASE = b"/data/local/tmp/tesla-waydroid-test-20261003/bin/"
BINARIES = {BASE + b"tesla-android-x86-api", BASE + b"tesla-android-x86-display-latest"}
ROLES = ("api", "display", "video", "audio")


def is_preview_command(command):
    fields = command.split(b"\0")
    if fields[0] in BINARIES:
        return True
    return (fields[0] in (b"/system/bin/app_process", b"/system/bin/app_process32", b"/system/bin/app_process64")
            and fields[1:4] == [b"/system/bin", b"com.genymobile.scrcpy.Server", b"4.0"]
            and any(value in fields[4:] for value in (b"scid=54410001", b"scid=54410002")))


def environment_owned(environment, lease, role, uid=0):
    fields = environment.split(b"\0")
    owners = [value for value in fields if value.startswith(b"TESLA_WAYDROID_RUNTIME_OWNER=")]
    roles = [value for value in fields if value.startswith(b"TESLA_WAYDROID_RUNTIME_ROLE=")]
    expected_roles = [value.encode() for value in ROLES] if role == "all" else [role.encode()]
    if uid == 2000:
        expected_roles = [value for value in expected_roles if value in (b"audio", b"video")]
    elif uid != 0:
        return False
    return owners == [b"TESLA_WAYDROID_RUNTIME_OWNER=" + lease.encode()] and len(roles) == 1 and roles[0] in [
        b"TESLA_WAYDROID_RUNTIME_ROLE=" + value for value in expected_roles
    ]


def shell_capture_owned(command, environment, lease, role):
    if not environment_owned(environment, lease, role, uid=2000):
        return False
    fields = command.split(b"\0")
    if fields[0] not in (b"/system/bin/app_process", b"/system/bin/app_process32", b"/system/bin/app_process64") or \
            fields[1:4] != [b"/system/bin", b"com.genymobile.scrcpy.Server", b"4.0"]:
        return False
    capture_role = [value for value in environment.split(b"\0") if value.startswith(b"TESLA_WAYDROID_RUNTIME_ROLE=")][0]
    expected = b"scid=54410001" if capture_role.endswith(b"=video") else b"scid=54410002"
    return [value for value in fields[4:] if value.startswith(b"scid=")] == [expected]


def birth(pid):
    with open(f"/proc/{pid}/stat", "rb") as source:
        value = source.read(4096)
    return value[value.rfind(b") ") + 2:].split()[19]


def process_uid(pid):
    return os.stat(f"/proc/{pid}").st_uid


def guest(pid):
    if process_uid(pid) not in (0, 2000):
        return False
    with open(f"/proc/{pid}/status", "rb") as source:
        lines = source.read(65536).splitlines()
    namespace = [line.split()[1:] for line in lines if line.startswith(b"NSpid:")]
    return len(namespace) == 1 and len(namespace[0]) >= 2


def read_environment(pid):
    with open(f"/proc/{pid}/environ", "rb") as source:
        value = source.read(65537)
    if len(value) > 65536:
        raise RuntimeError("Guest process environment exceeds limit")
    return value


def candidates(lease, role):
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if process_owned(pid, lease, role):
                yield pid
        except (FileNotFoundError, ProcessLookupError):
            continue


def process_owned(pid, lease, role):
    if not guest(pid):
        return False
    uid = process_uid(pid)
    environment = read_environment(pid)
    if uid == 2000:
        with open(f"/proc/{pid}/cmdline", "rb") as source:
            return shell_capture_owned(source.read(4096), environment, lease, role)
    return environment_owned(environment, lease, role, uid=uid)


def assert_vacant():
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            if not guest(pid):
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as source:
                command = source.read(4096)
            if is_preview_command(command):
                raise RuntimeError("Unowned native preview process remains")
        except (FileNotFoundError, ProcessLookupError):
            continue


def stop(lease, role):
    deadline = time.monotonic() + 6
    while True:
        remaining = list(candidates(lease, role))
        if not remaining:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Owned native descendants did not close")
        for pid in remaining:
            fd = None
            try:
                original = birth(pid)
                fd = os.pidfd_open(pid)
                if birth(pid) != original or not process_owned(pid, lease, role):
                    continue
                # The handle cannot signal an unrelated process after PID reuse.
                escalation = signal.SIGKILL if time.monotonic() > deadline - 3 else signal.SIGTERM
                signal.pidfd_send_signal(fd, escalation)
            except (FileNotFoundError, ProcessLookupError):
                continue
            finally:
                if fd is not None:
                    os.close(fd)
        time.sleep(0.05)


def main():
    if os.geteuid() != 0 or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise RuntimeError("Native cleanup requires root and Linux PID handles")
    if sys.argv[1:] == ["--assert-vacant"]:
        assert_vacant()
        print("VACANT")
        return
    if len(sys.argv) != 3 or not re.fullmatch(r"[a-f0-9]{32}", sys.argv[1]) or sys.argv[2] not in (*ROLES, "all"):
        raise RuntimeError("Invalid native lease cleanup")
    stop(sys.argv[1], sys.argv[2])
    print("REMOVED")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Native ownership cleanup failed", file=sys.stderr)
        sys.exit(1)
