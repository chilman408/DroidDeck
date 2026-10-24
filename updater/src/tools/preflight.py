import argparse
import json
import re
import shutil
from pathlib import Path


GIB = 1024**3


def assess_host(facts: dict, *, require_vm_host: bool = True) -> dict:
    blocking = []
    warnings = []

    if require_vm_host:
        if not facts.get("cpu_virtualization"):
            blocking.append("CPU virtualization flag (vmx or svm) is unavailable.")
        if not facts.get("kvm_usable"):
            blocking.append("KVM is unusable: check /dev/kvm, BIOS virtualization, and permissions.")
        if not facts.get("bridges"):
            blocking.append("No LAN bridge found; configure an Unraid bridge before VM networking.")

    free_gib = facts.get("build_free_bytes", 0) / GIB
    ram_gib = facts.get("ram_total_bytes", 0) / GIB
    available_ram_gib = facts.get("ram_available_bytes", 0) / GIB
    if free_gib <= 0:
        blocking.append("Build-volume free space could not be measured.")
    elif free_gib < 400:
        warnings.append(f"Only {free_gib:.0f} GiB build space is free; 400 GiB is conservative full-build guidance.")
    if ram_gib <= 0:
        blocking.append("Host RAM could not be measured.")
    elif ram_gib < 64:
        warnings.append(f"Host has {ram_gib:.0f} GiB RAM; 64 GiB is conservative full-build guidance.")
    if available_ram_gib <= 0:
        warnings.append("Available host RAM could not be measured.")
    elif available_ram_gib < 32:
        warnings.append(f"Only {available_ram_gib:.0f} GiB RAM is currently available; 32 GiB is conservative full-build guidance.")
    if 0 < available_ram_gib < 16:
        warnings.append(f"Only {available_ram_gib:.0f} GiB RAM is currently available; free at least 16 GiB before starting the target 8 GiB VM.")

    return {"blocking": blocking, "warnings": warnings}


def collect_host_facts(build_dir: Path = Path(".")) -> dict:
    kvm_usable = False
    try:
        with open("/dev/kvm", "rb+", buffering=0):
            kvm_usable = True
    except OSError:
        pass

    try:
        cpu_info = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    except OSError:
        cpu_info = ""
    cpu_virtualization = bool(re.search(r"\b(vmx|svm)\b", cpu_info))

    try:
        bridges = sorted(path.name for path in Path("/sys/class/net").iterdir() if (path / "bridge").is_dir())
    except OSError:
        bridges = []

    meminfo = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            meminfo[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        build_free_bytes = shutil.disk_usage(build_dir).free
    except OSError:
        build_free_bytes = 0

    return {
        "kvm_usable": kvm_usable,
        "cpu_virtualization": cpu_virtualization,
        "bridges": bridges,
        "ram_total_bytes": meminfo.get("MemTotal", 0),
        "ram_available_bytes": meminfo.get("MemAvailable", 0),
        "build_free_bytes": build_free_bytes,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only build and VM-host preflight")
    parser.add_argument("--build-dir", type=Path, default=Path("."))
    parser.add_argument("--build-only", action="store_true", help="assess build resources without Unraid KVM or bridge requirements")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    facts = collect_host_facts(args.build_dir)
    result = assess_host(facts, require_vm_host=not args.build_only)
    report = {"mode": "build-only" if args.build_only else "vm-host", "facts": facts, **result}

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"RAM: {facts['ram_total_bytes'] / GIB:.0f} GiB")
        print(f"Available RAM: {facts['ram_available_bytes'] / GIB:.0f} GiB")
        print(f"Free build space: {facts['build_free_bytes'] / GIB:.0f} GiB")
        print(f"LAN bridges: {', '.join(facts['bridges']) or 'none'}")
        for issue in result["blocking"]:
            print(f"BLOCK: {issue}")
        for issue in result["warnings"]:
            print(f"WARN: {issue}")

    return 2 if result["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
