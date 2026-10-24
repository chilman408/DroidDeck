import argparse
import json
import subprocess


GOOGLE_PACKAGES = {"com.android.vending", "com.google.android.gms", "com.google.android.gsf"}


class SubprocessAdb:
    def __init__(self, serial: str):
        self.serial = serial

    def shell(self, *args: str) -> str:
        result = subprocess.run(
            ["adb", "-s", self.serial, "shell", *args],
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        return result.stdout


def probe_vm(adb_client) -> dict:
    failures = []

    def query(label: str, *args: str) -> str:
        try:
            return adb_client.shell(*args).strip()
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            failures.append(f"{label} query failed ({type(error).__name__}).")
            return ""

    release = query("Android release", "getprop", "ro.build.version.release")
    abi = query("ABI", "getprop", "ro.product.cpu.abi")
    package_output = query("packages", "pm", "list", "packages")
    network_output = query("network", "ip", "-o", "addr", "show", "scope", "global")
    display_output = query("display", "dumpsys", "SurfaceFlinger")
    audio_output = query("audio", "dumpsys", "media.audio_policy")

    packages = {line.removeprefix("package:").strip() for line in package_output.splitlines() if line.startswith("package:")}
    google_packages = sorted(packages & GOOGLE_PACKAGES)
    network_ok = any(" inet " in f" {line} " for line in network_output.splitlines())
    display_ok = "Display" in display_output and "Can't find service" not in display_output
    audio_ok = "AUDIO_DEVICE_OUT" in audio_output and "Can't find service" not in audio_output

    if release != "15":
        failures.append(f"Expected Android 15, got {release or 'unknown'}.")
    if abi != "x86_64":
        failures.append(f"Expected x86_64 ABI, got {abi or 'unknown'}.")
    if google_packages:
        failures.append(f"Clean base contains Google packages: {', '.join(google_packages)}.")
    if not network_ok:
        failures.append("No global IPv4 address detected in guest.")
    if not display_ok:
        failures.append("SurfaceFlinger display not detected.")
    if not audio_ok:
        failures.append("Audio output device not detected.")

    return {
        "ok": not failures,
        "android_release": release,
        "abi": abi,
        "network_ok": network_ok,
        "display_ok": display_ok,
        "audio_ok": audio_ok,
        "google_packages": google_packages,
        "failures": failures,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only ADB probe of a clean Android 15 x86_64 VM")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = probe_vm(SubprocessAdb(args.serial))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for key, value in report.items():
            print(f"{key}: {value}")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
