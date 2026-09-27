import json
import os
import select
import socket
import threading

import pytest

from protocol_transport import FramedEndpoint, PTYEndpoint, encode_frame, recv_exact


def test_fragmented_eight_byte_header_and_body():
    seen = []
    with FramedEndpoint(lambda q: seen.append(q) or {"Success": True, "Data": q}) as endpoint:
        with socket.create_connection((endpoint.host, endpoint.port), 2) as sock:
            frame = encode_frame({"MethodName": "Status", "Paramters": {}})
            for b in frame:
                sock.sendall(bytes([b]))
            length = int.from_bytes(recv_exact(sock, 8), "big")
            result = json.loads(recv_exact(sock, length))
            assert result["Success"] is True
    assert len(seen) == 1


def test_oversize_rejected_without_calling_instrument():
    seen = []
    with FramedEndpoint(lambda q: seen.append(q), error_response=lambda exc: {"error": str(exc)}) as endpoint:
        with socket.create_connection((endpoint.host, endpoint.port), 2) as sock:
            sock.sendall((2**40).to_bytes(8, "big"))
            n = int.from_bytes(recv_exact(sock, 8), "big")
            assert json.loads(recv_exact(sock, n))["error"] == "invalid_frame_length"
    assert seen == []


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY需要POSIX")
def test_real_pty_fragmentation_no_echo_and_cleanup():
    seen = []
    with PTYEndpoint(lambda line: seen.append(line) or b"S S 1.0000 g\r\n") as p:
        fd = os.open(p.path, os.O_RDWR | os.O_NOCTTY)
        try:
            os.write(fd, b"S"); os.write(fd, b"I\r\n")
            assert select.select([fd], [], [], 2)[0]
            assert os.read(fd, 256) == b"S S 1.0000 g\r\n"
        finally:
            os.close(fd)
    assert seen == [b"SI"]
    assert not p.thread.is_alive()


def test_invalid_frame_without_error_policy_closes_without_callback():
    seen = []
    with FramedEndpoint(lambda q: seen.append(q)) as endpoint:
        with socket.create_connection((endpoint.host, endpoint.port), 2) as sock:
            sock.sendall((0).to_bytes(8, "big"))
            assert sock.recv(1) == b""
    assert not seen


def test_lost_reply_keeps_callback_effect():
    seen = []
    with FramedEndpoint(lambda q: seen.append(q)) as endpoint:
        with socket.create_connection((endpoint.host, endpoint.port), 2) as sock:
            sock.sendall(encode_frame({"id": 1}))
            assert sock.recv(1) == b""
    assert seen == [{"id": 1}]


def test_refuses_non_loopback_listener():
    with pytest.raises(ValueError, match="simulator_loopback_only"):
        FramedEndpoint(lambda q: q, host="0.0.0.0")


@pytest.mark.parametrize("body", [b'{"value":1e999}', b'{"value":NaN}', b'[]'])
def test_invalid_json_is_rejected_before_callback(body):
    seen = []
    with FramedEndpoint(lambda q: seen.append(q), error_response=lambda exc: {"error": str(exc)}) as endpoint:
        with socket.create_connection((endpoint.host, endpoint.port), 2) as sock:
            sock.sendall(len(body).to_bytes(8, "big") + body)
            n = int.from_bytes(recv_exact(sock, 8), "big")
            assert "error" in json.loads(recv_exact(sock, n))
    assert seen == []


@pytest.mark.skipif(not hasattr(os, "openpty"), reason="PTY需要POSIX")
def test_oversized_pty_frame_discards_entire_frame_before_resync():
    received = []
    synchronized = threading.Event()

    def callback(line):
        received.append(line)
        if line == b"PING":
            synchronized.set()
        return b"ok\r\n"

    with PTYEndpoint(callback) as endpoint:
        # The legal-looking tail belongs to the oversized frame, not a command.
        frame = b"x" * 8193 + b"M104 S60\rPING\r"
        written = 0
        while written < len(frame):
            written += os.write(endpoint.slave, frame[written:])
        assert synchronized.wait(2), "Endpoint did not recover at the next valid frame"
        assert received == [b"PING"]
