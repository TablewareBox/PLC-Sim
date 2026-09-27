"""Owned simulator-only PTY and framed TCP endpoints, no hardware discovery."""
from __future__ import annotations

import json
import math
import os
import select
import socket
import socketserver
import threading

MAX_MESSAGE = 1024 * 1024


def strict_json(data):
    def reject_constant(value):
        raise ValueError("nonfinite_json")
    def parse_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite_json")
        return result
    result = json.loads(data, parse_constant=reject_constant, parse_float=parse_float)
    if not isinstance(result, dict):
        raise ValueError("request_must_be_object")
    return result


def recv_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise EOFError("incomplete_frame")
        data.extend(chunk)
    return bytes(data)


def encode_frame(value):
    body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise ValueError("frame_too_large")
    return len(body).to_bytes(8, "big") + body


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class FramedEndpoint:
    """八字节大端长度与JSON对象；错误回执由调用方提供。"""

    def __init__(self, handler, host="127.0.0.1", port=0, *, timeout_s=2, error_response=None):
        if host != "127.0.0.1":
            raise ValueError("simulator_loopback_only")
        self.callback = handler
        self.timeout_s = timeout_s
        self.error_response = error_response
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(owner.timeout_s)
                try:
                    size = int.from_bytes(recv_exact(self.request, 8), "big")
                    if size <= 0 or size > MAX_MESSAGE:
                        raise ValueError("invalid_frame_length")
                    query = strict_json(recv_exact(self.request, size))
                    result = owner.callback(query)
                    if result is not None:
                        self.request.sendall(encode_frame(result))
                    else:
                        # Simulated lost reply. Closing bounds the caller's wait.
                        return
                except (ValueError, EOFError) as exc:
                    try:
                        if owner.error_response is not None:
                            result = owner.error_response(exc)
                            if result is not None:
                                self.request.sendall(encode_frame(result))
                    except OSError:
                        pass
                except (OSError, TimeoutError):
                    return

        self.server = _Server((host, port), Handler)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def framed_call(host, port, request, timeout_s=3):
    if host != "127.0.0.1":
        raise ValueError("simulator_loopback_only")
    with socket.create_connection((host, port), timeout_s) as sock:
        sock.sendall(encode_frame(request))
        length = int.from_bytes(recv_exact(sock, 8), "big")
        if not 0 < length <= MAX_MESSAGE:
            raise ValueError("invalid_frame_length")
        return strict_json(recv_exact(sock, length))


class PTYEndpoint:
    """One owned pseudoterminal; refuses any caller-provided device pathname."""

    def __init__(self, handler):
        # 仅构造PTY时加载POSIX依赖，TCP模块可独立导入。
        import tty

        self.handler = handler
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.closed = threading.Event()
        self.errors = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def _serve(self):
        buffer = bytearray()
        discard_frame = False
        while not self.closed.is_set():
            try:
                ready, _, _ = select.select([self.master], [], [], .05)
                if not ready:
                    continue
                data = os.read(self.master, 4096)
                if not data:
                    continue
                for char in data:
                    if char in (10, 13):
                        if discard_frame:
                            discard_frame = False
                            buffer.clear()
                            continue
                        if not buffer:
                            continue
                        line, buffer = bytes(buffer), bytearray()
                        try:
                            response = self.handler(line)
                        except Exception as exc:
                            self.errors.append({"type": type(exc).__name__, "message": str(exc)})
                            response = b"ERR\r\n"
                        if response is not None:
                            os.write(self.master, response)
                    else:
                        if discard_frame:
                            continue
                        buffer.append(char)
                        if len(buffer) > 8192:
                            buffer.clear()
                            discard_frame = True
                            os.write(self.master, b"ERR frame_too_large\r\n")
            except OSError:
                if not self.closed.is_set():
                    self.errors.append({"type": "OSError", "message": "pty_io_error"})
                return

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.closed.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


def unix_json_call(path, payload, timeout=900):
    raw = json.dumps(payload, allow_nan=False).encode() + b'\n'
    if len(raw) > 65536:
        raise ValueError('request_too_large')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(path))
        client.sendall(raw)
        with client.makefile('rb') as stream:
            response = stream.readline(1048577)
    if len(response) > 1048576 or not response.endswith(b'\n'):
        raise ValueError('incomplete_response')
    value = json.loads(response)
    if 'error' in value:
        raise RuntimeError(value['error'])
    return value
