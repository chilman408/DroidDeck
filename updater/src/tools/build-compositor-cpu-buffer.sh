#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output="$source_root/runtime/waydroid-display/cpu-buffer-build"
# Fresh staging checkout only. Never replace an existing build directory.
mkdir -m 700 "$output"
protocols=$(pkg-config --variable=pkgdatadir wayland-protocols)
wayland-scanner client-header "$protocols/stable/xdg-shell/xdg-shell.xml" "$output/xdg-shell-client-protocol.h"
wayland-scanner private-code "$protocols/stable/xdg-shell/xdg-shell.xml" "$output/xdg-shell-protocol.c"
wayland-scanner client-header "$protocols/unstable/linux-dmabuf/linux-dmabuf-unstable-v1.xml" "$output/linux-dmabuf-client-protocol.h"
wayland-scanner private-code "$protocols/unstable/linux-dmabuf/linux-dmabuf-unstable-v1.xml" "$output/linux-dmabuf-protocol.c"
gcc -std=c11 -Wall -Wextra -Werror -O2 -I"$output" \
    "$source_root/runtime/waydroid-display/compositor-cpu-buffer-client.c" \
    "$output/xdg-shell-protocol.c" "$output/linux-dmabuf-protocol.c" \
    $(pkg-config --cflags --libs wayland-client gbm) -o "$output/client"
gcc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined -g \
    "$source_root/tools/compositor-cpu-buffer-pattern_test.c" -o "$output/pattern-test"
"$output/pattern-test"
