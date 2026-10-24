#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
import base64
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path


def png_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def test_png():
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff"))
        + png_chunk(b"IEND", b"")
    )


if sys.argv[1:] == ["-p"]:
    sys.stdout.buffer.write(test_png())
    raise SystemExit(0)

def check_frame(connection):
    connection.settimeout(5)
    key = base64.b64encode(b"teslaandroidtest").decode("ascii")
    connection.sendall(
        (
            "GET / HTTP/1.1\r\n"
            "Host: 127.0.0.1:9091\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "sec-websocket-key: " + key + "\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
    )
    received = bytearray()
    while b"\r\n\r\n" not in received:
        part = connection.recv(4096)
        if not part:
            raise AssertionError("connection closed before handshake")
        received.extend(part)
    header, frame = bytes(received).split(b"\r\n\r\n", 1)
    assert b"101 Switching Protocols" in header
    frame_holder = [frame]

    def read_exact(count):
        while len(frame_holder[0]) < count:
            part = connection.recv(4096)
            if not part:
                raise AssertionError("connection closed before frame")
            frame_holder[0] += part
        result, frame_holder[0] = frame_holder[0][:count], frame_holder[0][count:]
        return result

    first, second = read_exact(2)
    assert first == 0x82 and second & 0x80 == 0
    size = second & 0x7F
    if size == 126:
        size = struct.unpack(">H", read_exact(2))[0]
    elif size == 127:
        size = struct.unpack(">Q", read_exact(8))[0]
    assert read_exact(size) == test_png()


if len(sys.argv) != 2:
    raise SystemExit("usage: python3 tests/display-stream.test.py DISPLAY_SERVICE_BINARY")

with tempfile.TemporaryDirectory(prefix="ta-display-") as directory:
    fake_capture = Path(directory) / "screencap"
    fake_capture.write_bytes(Path(__file__).read_bytes())
    fake_capture.chmod(0o700)
    with subprocess.Popen(
        [sys.argv[1], str(fake_capture)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    ) as server:
        try:
            for attempt in range(30):
                try:
                    connection = socket.create_connection(("127.0.0.1", 9091), timeout=2)
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError("display service exited before binding")
                    time.sleep(0.1)
            else:
                raise RuntimeError("display service did not bind")
            with connection:
                check_frame(connection)
                with socket.create_connection(("127.0.0.1", 9091), timeout=2) as concurrent:
                    check_frame(concurrent)
            time.sleep(0.3)
            with socket.create_connection(("127.0.0.1", 9091), timeout=2) as reconnect:
                check_frame(reconnect)
            print("display websocket PNG, concurrent viewer, and reconnect passed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
