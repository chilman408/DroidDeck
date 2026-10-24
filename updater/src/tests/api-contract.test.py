#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


if len(sys.argv) != 2:
    raise SystemExit("usage: python3 tests/api-contract.test.py API_BINARY")

root = "http://127.0.0.1:8081"
with subprocess.Popen(
    [sys.argv[1], "1280", "720"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
) as server:
    try:
        for attempt in range(30):
            try:
                with urllib.request.urlopen(root + "/api/health", timeout=1) as response:
                    assert response.status == 200
                break
            except (OSError, urllib.error.URLError):
                if server.poll() is not None:
                    raise RuntimeError("API service exited before binding")
                time.sleep(0.1)
        else:
            raise RuntimeError("API service did not bind")

        def get_json(path):
            with urllib.request.urlopen(root + path, timeout=3) as response:
                assert response.headers["Content-Type"].startswith("application/json")
                return json.load(response)

        display = get_json("/api/displayState")
        assert (display["width"], display["height"]) == (1280, 720)
        assert (display["renderer"], display["fixedDisplay"]) == (0, 1)
        configuration = get_json("/api/configuration")
        assert configuration["persist.tesla-android.gps.is_active"] == 0
        assert configuration["persist.tesla-android.browser_audio.is_enabled"] == 0
        assert get_json("/api/deviceInfo")["device_model"] == "TeslaAndroid x86 VM preview"

        request = urllib.request.Request(
            root + "/api/displayState",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            assert error.code == 501
            assert json.load(error)["error"] == "Not available in the x86 VM preview"
        else:
            raise AssertionError("fixed display unexpectedly accepted a resize")
        print("API contract tests passed")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
