"""只使用临时目录内自建Unix端点；不连接旧服务。"""
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import tempfile
import threading

import pytest

from protocol_transport import unix_json_call


@contextmanager
def endpoint(chunks):
    with tempfile.TemporaryDirectory(prefix="ulunix-") as temp:
        path = Path(temp) / "s.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.settimeout(2)
        listener.bind(str(path))
        listener.listen(1)
        seen, errors = [], []

        def serve():
            try:
                with listener.accept()[0] as conn:
                    conn.settimeout(2)
                    with conn.makefile('rb') as stream:
                        seen.append(stream.readline())
                    for chunk in chunks:
                        conn.sendall(chunk)
            except BrokenPipeError:
                pass  # 客户端到达响应上限后可以先关闭连接。
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            yield path, seen
        finally:
            thread.join(timeout=3)
            listener.close()
            assert not thread.is_alive()
            assert not errors, errors


def test_fragmented_response_and_single_request_bytes():
    with endpoint([b'{"phase":', b'"finished"}', b'\n']) as (path, seen):
        result = unix_json_call(path, {'method': 'status'}, timeout=1)
    assert result == {'phase': 'finished'}
    assert seen == [b'{"method": "status"}\n']


def test_error_response_preserves_remote_failure():
    with endpoint([b'{"error":"stopped"}\n']) as (path, seen):
        with pytest.raises(RuntimeError, match='stopped'):
            unix_json_call(path, {'method':'status'}, timeout=1)
    assert len(seen) == 1


@pytest.mark.parametrize('reply', [b'', b'{"phase":"finished"}', b'x' * 1048577])
def test_empty_truncated_or_oversized_reply_is_rejected(reply):
    with endpoint([reply]) as (path, seen):
        with pytest.raises(ValueError, match='incomplete_response'):
            unix_json_call(path, {'method':'status'}, timeout=1)
    assert len(seen) == 1  # 不自动重试，发送后的效果保持未知。


def test_large_request_is_rejected_before_connecting():
    with pytest.raises(ValueError, match='request_too_large'):
        unix_json_call('/nonexistent/unix.sock', {'body':'x' * 65536})


def test_nonfinite_request_is_rejected_before_connecting():
    with pytest.raises(ValueError):
        unix_json_call('/nonexistent/unix.sock', {'value':float('nan')})


def test_original_trusted_response_boundary_is_preserved():
    # 与旧rpc保持一致：响应尚未改用TCP帧模块的严格对象解析器。
    with endpoint([b'{"value":NaN}\n']) as (path, _):
        result = unix_json_call(path, {'method':'status'}, timeout=1)
    assert result['value'] != result['value']


def test_first_newline_finishes_one_response():
    with endpoint([b'{"value":1}\n{"value":2}\n']) as (path, seen):
        assert unix_json_call(path, {'method':'status'}, timeout=1) == {'value':1}
    assert len(seen) == 1
