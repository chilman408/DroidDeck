import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build" / "build-vanilla.sh"
CONTAINERFILE = ROOT / "build" / "Containerfile"
MESON_REQUIREMENTS = ROOT / "build" / "meson-requirements.txt"
INSTALLER_PATCH = ROOT / "build" / "patches" / "aaropa-r101.patch"
MESA_RUST_PATCH = ROOT / "build" / "patches" / "aospext-mesa-rust.patch"
ALSA_PATCH = ROOT / "build" / "patches" / "alsa-utils-log-api.patch"
LOCK = ROOT / "config" / "source-lock.json"
BASH = r"C:\Program Files\Git\bin\bash.exe" if os.name == "nt" else "bash"


def bash_path(path: Path) -> str:
    if os.name == "nt":
        return f"/{path.drive[0].lower()}{path.as_posix()[2:]}"
    return str(path)


class BuildContractTests(unittest.TestCase):
    def test_builder_script_has_lf_line_endings(self):
        self.assertNotIn(b"\r", SCRIPT.read_bytes())

    def test_git_archive_exports_lf_shell_script(self):
        if not (ROOT / ".git").exists():
            self.skipTest("Git archive check requires the source checkout")

        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "kit.tar"
            subprocess.run(
                ["git", "archive", "--worktree-attributes", "--format=tar", f"--output={archive}", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            with tarfile.open(archive) as bundle:
                script = bundle.extractfile("build/build-vanilla.sh")
                self.assertIsNotNone(script)
                self.assertNotIn(b"\r", script.read())

    def test_container_enables_required_ubuntu_components(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("add-apt-repository -y universe", recipe)
        self.assertIn("add-apt-repository -y multiverse", recipe)
        self.assertIn(" aapt ", recipe)

    def test_container_pins_repo_launcher_with_lfs_support(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("--branch v2.68.1 https://gerrit.googlesource.com/git-repo", recipe)
        self.assertIn("e1e215a14ea5373419acb8753b6865de19d1f122", recipe)
        self.assertIn("repo init --help | grep -q -- --git-lfs", recipe)

    def test_builder_has_verified_installer_download_prerequisites(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(" wget ", recipe)
        self.assertIn("git -C bootable/aaropa apply", script)
        self.assertIn("set +u\n. build/envsetup.sh\nlunch bliss_x86_64-ap4a-userdebug\nset -u", script)

    def test_builder_includes_required_host_compile_tools(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")

        for package in ("rsync", "gperf", "kmod", "pkg-config"):
            self.assertIn(package, recipe)
        self.assertIn("command -v rsync", script)

    def test_builder_includes_legacy_ncurses_runtime_for_llvm19_tblgen(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("libncurses5", recipe)
        self.assertIn("libtinfo5", recipe)

    def test_builder_includes_ncurses_headers_for_dialog(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("libncurses-dev", recipe)

    def test_builder_pins_meson_compatible_with_mesa(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")
        requirements = MESON_REQUIREMENTS.read_text(encoding="utf-8")

        self.assertIn("meson==1.4.1 --hash=sha256:d5acc3abae2dad3c70ddcbd10acac92b78b144d34d43f40f5b8ac31dfd8a826a", requirements)
        self.assertIn("python3-venv", recipe)
        self.assertIn("python3 -m venv --system-site-packages /opt/meson", recipe)
        self.assertIn("python3-yaml", recipe)
        self.assertIn("python3-ply", recipe)
        self.assertIn("python3-pycparser", recipe)
        self.assertIn("pip install --no-deps --require-hashes", recipe)
        self.assertIn('ENV PATH="/opt/meson/bin:${PATH}"', recipe)

    def test_mesa_uses_android_rust_prebuilts_and_abort_strategy(self):
        script = SCRIPT.read_text(encoding="utf-8")
        patch = MESA_RUST_PATCH.read_text(encoding="utf-8")

        self.assertIn("prebuilts/rust/linux-x86/1.81.0/bin", script)
        self.assertIn("prebuilts/clang-tools/linux-x86/bin", script)
        self.assertIn("git -C glodroid/aospext apply", script)
        self.assertIn("'-C', 'panic=abort'", patch)
        parsed = subprocess.run(["git", "apply", "--stat", str(MESA_RUST_PATCH)], capture_output=True, text=True)
        self.assertEqual(0, parsed.returncode, parsed.stderr)

    def test_builder_verifies_cbindgen_binary(self):
        recipe = CONTAINERFILE.read_text(encoding="utf-8")

        self.assertIn("https://github.com/mozilla/cbindgen/releases/download/0.28.0/cbindgen", recipe)
        self.assertIn("94a94848bbfb7b0eb8dfd6f3b8093331913faee3bb61d3f42e21a2dcf693e3d2", recipe)
        self.assertIn("sha256sum -c -", recipe)
        self.assertIn("cbindgen --version | grep -Fx 'cbindgen 0.28.0'", recipe)

    def test_alsa_utils_selects_logging_api_by_library_version(self):
        script = SCRIPT.read_text(encoding="utf-8")
        patch = ALSA_PATCH.read_text(encoding="utf-8")

        self.assertIn("git -C external/alsa-utils apply", script)
        self.assertEqual(5, patch.count("+#if SND_LIB_VER(1, 2, 15) > SND_LIB_VERSION"))
        self.assertEqual(1, patch.count("+#if SND_LIB_VER(1, 2, 15) <= SND_LIB_VERSION"))
        parsed = subprocess.run(["git", "apply", "--stat", str(ALSA_PATCH)], capture_output=True, text=True)
        self.assertEqual(0, parsed.returncode, parsed.stderr)

    def test_installer_patch_pins_and_checks_all_release_assets(self):
        self.assertTrue(INSTALLER_PATCH.is_file())
        self.assertNotIn(b"\r", INSTALLER_PATCH.read_bytes())
        patch = INSTALLER_PATCH.read_text(encoding="utf-8")

        self.assertIn("releases/download/r101", patch)
        self.assertIn("set -eo pipefail", patch)
        self.assertIn("sha256sum -c -", patch)
        for asset in ("boot_hybrid.img", "grub-rescue.iso", "initrd_lib.tar.gz", "install.sfs"):
            self.assertIn(asset, patch)

    def run_builder(self, *extra):
        return subprocess.run(
            [BASH, bash_path(SCRIPT), "--source-dir", "/tmp/tesla-src-contract", "--output-dir", "/tmp/tesla-out-contract", "--lock", bash_path(LOCK), "--dry-run", *extra],
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHON_BIN": "python" if os.name == "nt" else "python3"},
        )

    def test_dry_run_uses_pinned_vanilla_x86_iso_recipe(self):
        result = self.run_builder()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("\r", result.stdout)
        self.assertIn("repo init -u https://github.com/BlissOS/platform_manifest.git -b db973c9fec45d90f4b85de56c6711e0227716e69 --manifest-upstream-branch voyager-x86 --git-lfs", result.stdout)
        self.assertNotIn("checkout --detach", result.stdout)
        self.assertIn("BLISS_BUILD_VARIANT=vanilla", result.stdout)
        self.assertIn("bliss_x86_64-ap4a-userdebug", result.stdout)
        self.assertIn("iso_img", result.stdout)
        self.assertIn("repo manifest -r", result.stdout)
        self.assertIn("apply pinned installer asset patch", result.stdout)
        self.assertNotIn("BLISS_BUILD_VARIANT=gapps", result.stdout)
        self.assertNotIn("userdata", result.stdout.lower())

    def test_missing_lock_fails_without_running(self):
        result = self.run_builder("--lock", "/tmp/nonexistent-tesla-lock.json")

        self.assertNotEqual(0, result.returncode)
        self.assertIn("lock file", result.stderr.lower())
        self.assertNotIn("repo sync", result.stdout)

    def test_existing_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            result = subprocess.run(
                [BASH, bash_path(SCRIPT), "--source-dir", "/tmp/tesla-src-contract", "--output-dir", bash_path(Path(output)), "--lock", bash_path(LOCK), "--dry-run"],
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PYTHON_BIN": "python" if os.name == "nt" else "python3"},
            )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("already exists", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
