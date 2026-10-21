#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --source-dir DIR --output-dir DIR --lock FILE [--dry-run]" >&2
  exit 2
}

source_dir=
output_dir=
lock=
dry_run=false
while (($#)); do
  case "$1" in
    --source-dir|--output-dir|--lock)
      (($# >= 2)) || usage
      case "$1" in
        --source-dir) source_dir=$2 ;;
        --output-dir) output_dir=$2 ;;
        --lock) lock=$2 ;;
      esac
      shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    *) usage ;;
  esac
done

[[ -n $source_dir && -n $output_dir && -n $lock ]] || usage
[[ -f $lock ]] || { echo "Lock file not found: $lock" >&2; exit 2; }
[[ ! -e $output_dir ]] || { echo "Output path already exists: $output_dir" >&2; exit 2; }

python_bin=${PYTHON_BIN:-python3}
python_lock=$lock
if command -v cygpath >/dev/null 2>&1; then
  python_lock=$(cygpath -w "$lock")
fi
lock_data=$("$python_bin" - "$python_lock" <<'PY'
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        bliss = json.load(stream)["blissos"]
    url, branch, revision = (bliss[key] for key in ("url", "branch", "revision"))
    if not url.startswith("https://") or not branch or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid BlissOS source lock")
except (OSError, KeyError, ValueError, TypeError) as error:
    sys.exit(f"Invalid lock file: {error}")
print(url, branch, revision, sep="\n")
PY
)
mapfile -t pinned <<<"$lock_data"
url=${pinned[0]%$'\r'}
branch=${pinned[1]%$'\r'}
revision=${pinned[2]%$'\r'}

if $dry_run; then
  printf 'cd %q\n' "$source_dir"
  printf 'repo init -u %q -b %q --manifest-upstream-branch %q --git-lfs\n' "$url" "$revision" "$branch"
  echo 'verify pinned manifest is Android 15'
  echo 'repo sync -c --force-sync --no-tags --no-clone-bundle --optimized-fetch --prune'
  echo 'repo manifest -r -o resolved-manifest.xml'
  echo 'apply pinned installer asset patch'
  echo 'export BLISS_BUILD_VARIANT=vanilla'
  echo '. build/envsetup.sh'
  echo 'lunch bliss_x86_64-ap4a-userdebug'
  echo 'make iso_img'
  printf 'copy one ISO and resolved-manifest.xml to new output path %q; write SHA-256\n' "$output_dir"
  exit 0
fi

command -v repo >/dev/null || { echo 'repo launcher is required' >&2; exit 2; }
command -v sha256sum >/dev/null || { echo 'sha256sum is required' >&2; exit 2; }
command -v rsync >/dev/null || { echo 'rsync is required' >&2; exit 2; }
[[ -d $(dirname "$output_dir") ]] || { echo 'Output parent does not exist' >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
installer_patch=$script_dir/patches/aaropa-r101.patch
[[ -f $installer_patch ]] || { echo 'Pinned installer patch is missing' >&2; exit 2; }
mesa_rust_patch=$script_dir/patches/aospext-mesa-rust.patch
[[ -f $mesa_rust_patch ]] || { echo 'Mesa Rust cross-file patch is missing' >&2; exit 2; }
alsa_patch=$script_dir/patches/alsa-utils-log-api.patch
[[ -f $alsa_patch ]] || { echo 'ALSA logging API patch is missing' >&2; exit 2; }
mkdir -p -- "$source_dir"
cd "$source_dir"
repo init -u "$url" -b "$revision" --manifest-upstream-branch "$branch" --git-lfs
[[ $(git -C .repo/manifests rev-parse HEAD) == "$revision" ]] || { echo 'Manifest revision mismatch' >&2; exit 1; }
[[ $(git -C .repo/manifests config branch.default.merge) == "$revision" ]] || { echo 'Manifest tracking revision mismatch' >&2; exit 1; }
"$python_bin" - <<'PY'
import sys
import xml.etree.ElementTree as ET

revision = ET.parse('.repo/manifests/default.xml').getroot().find('default').get('revision')
if not revision or not revision.startswith('refs/tags/android-15.0.0_r'):
    sys.exit(f'Expected Android 15 base, found {revision!r}')
PY
repo sync -c --force-sync --no-tags --no-clone-bundle --optimized-fetch --prune
[[ $(git -C .repo/manifests rev-parse HEAD) == "$revision" ]] || { echo 'Manifest moved during sync' >&2; exit 1; }
repo manifest -r -o resolved-manifest.xml
if ! git -C bootable/aaropa apply --reverse --check "$installer_patch" 2>/dev/null; then
  git -C bootable/aaropa apply --check "$installer_patch"
  git -C bootable/aaropa apply "$installer_patch"
fi
if ! git -C glodroid/aospext apply --reverse --check "$mesa_rust_patch" 2>/dev/null; then
  git -C glodroid/aospext apply --check "$mesa_rust_patch"
  git -C glodroid/aospext apply "$mesa_rust_patch"
fi
if ! git -C external/alsa-utils apply --reverse --check "$alsa_patch" 2>/dev/null; then
  git -C external/alsa-utils apply --check "$alsa_patch"
  git -C external/alsa-utils apply "$alsa_patch"
fi
rust_bin=$PWD/prebuilts/rust/linux-x86/1.81.0/bin
bindgen_bin=$PWD/prebuilts/clang-tools/linux-x86/bin
[[ -x $rust_bin/rustc && -x $bindgen_bin/bindgen ]] || { echo 'Android Rust and bindgen prebuilts are required' >&2; exit 2; }
export PATH="$rust_bin:$bindgen_bin:$PATH"
export BLISS_BUILD_VARIANT=vanilla
set +u
. build/envsetup.sh
lunch bliss_x86_64-ap4a-userdebug
set -u
make iso_img

mapfile -d '' -t isos < <(find out/target/product -maxdepth 3 -type f -name '*.iso' -print0)
[[ ${#isos[@]} == 1 ]] || { echo "Expected exactly one ISO, found ${#isos[@]}" >&2; exit 1; }
staging=$(mktemp -d "$output_dir.partial.XXXXXX")
cp -- "${isos[0]}" "$staging/TeslaAndroid-R15-x86-vanilla.iso"
cp -- resolved-manifest.xml "$staging/resolved-manifest.xml"
cp -- "$installer_patch" "$staging/aaropa-r101.patch"
(
  cd "$staging"
  sha256sum TeslaAndroid-R15-x86-vanilla.iso > TeslaAndroid-R15-x86-vanilla.iso.sha256
)
mv -- "$staging" "$output_dir"
echo "Built clean candidate at $output_dir"
