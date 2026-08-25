"""Tests for canvas.hypr module — Hyprland IPC abstraction."""

from unittest.mock import MagicMock, patch

from canvas.hypr import HyprIPC, eval_lua, get_cursor_pos, send


def _make_ipc_with_mock(mock_sock: MagicMock) -> HyprIPC:
    """Create HyprIPC with a mock _connect that returns mock_sock."""
    ipc = HyprIPC("/tmp/test.sock")
    ipc._connect = lambda: mock_sock  # type: ignore[assignment]
    return ipc


def test_send_calls_socket():
    """send() connects, sends command, reads response."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"ok", b""]
    ipc = _make_ipc_with_mock(mock_sock)

    result = ipc.send("cursorpos")

    assert result == "ok"
    mock_sock.sendall.assert_called_once_with(b"cursorpos")


def test_eval_lua_prefixes_eval():
    """eval_lua prepends 'eval ' to lua code."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"ok", b""]
    ipc = _make_ipc_with_mock(mock_sock)

    result = ipc.eval_lua("hl.dispatch(hl.dsp.no_op())")

    assert result == "ok"
    mock_sock.sendall.assert_called_once_with(b"eval hl.dispatch(hl.dsp.no_op())")


def test_get_cursor_pos_parses_response():
    """get_cursor_pos parses 'X, Y' response."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"100, 200", b""]
    ipc = _make_ipc_with_mock(mock_sock)

    x, y = ipc.get_cursor_pos()

    assert x == 100
    assert y == 200


def test_send_closes_socket_after_response():
    """send() always closes the connection (server closes after each response)."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"ok", b""]
    ipc = _make_ipc_with_mock(mock_sock)

    result = ipc.send("cursorpos")

    assert result == "ok"
    mock_sock.close.assert_called_once()


def test_send_response_size_limit():
    """An oversized response aborts instead of consuming unbounded memory."""
    import canvas.hypr as hypr_mod

    mock_sock = MagicMock()
    chunk = b"x" * 4096
    mock_sock.recv.side_effect = lambda *_: chunk  # never returns EOF
    ipc = _make_ipc_with_mock(mock_sock)

    original = hypr_mod._MAX_RESPONSE
    hypr_mod._MAX_RESPONSE = 8192
    try:
        try:
            ipc.send("j/clients")
            raise AssertionError("should have raised ConnectionError")
        except ConnectionError as e:
            assert "size limit" in str(e)
    finally:
        hypr_mod._MAX_RESPONSE = original


def test_module_level_send_delegates_to_default():
    """Module-level send() delegates to default HyprIPC instance."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"module_ok", b""]

    with patch("canvas.hypr._get_default") as mock_get:
        ipc = _make_ipc_with_mock(mock_sock)
        mock_get.return_value = ipc
        result = send("cursorpos")

    assert result == "module_ok"


def test_module_level_eval_lua_delegates():
    """Module-level eval_lua() delegates to default HyprIPC instance."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"lua_ok", b""]

    with patch("canvas.hypr._get_default") as mock_get:
        ipc = _make_ipc_with_mock(mock_sock)
        mock_get.return_value = ipc
        result = eval_lua("print('hi')")

    assert result == "lua_ok"


def test_module_level_get_cursor_pos_delegates():
    """Module-level get_cursor_pos() delegates to default HyprIPC instance."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"42, 84", b""]

    with patch("canvas.hypr._get_default") as mock_get:
        ipc = _make_ipc_with_mock(mock_sock)
        mock_get.return_value = ipc
        x, y = get_cursor_pos()

    assert x == 42
    assert y == 84


def test_send_empty_response():
    """send() handles empty response from Hyprland."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b""]
    ipc = _make_ipc_with_mock(mock_sock)

    result = ipc.send("cursorpos")

    assert result == ""


def test_send_connection_error():
    """send() raises when both persistent and fresh connections fail."""
    ipc = HyprIPC("/tmp/test.sock")
    ipc._connect = MagicMock(side_effect=ConnectionError("refused"))  # type: ignore[assignment]

    try:
        ipc.send("cursorpos")
        raise AssertionError("should have raised ConnectionError")
    except ConnectionError:
        pass


def test_get_cursor_pos_single_digit():
    """get_cursor_pos handles single-digit coordinates."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"0, 0", b""]
    ipc = _make_ipc_with_mock(mock_sock)

    x, y = ipc.get_cursor_pos()

    assert x == 0
    assert y == 0


def test_from_env_uses_hyprland_socket():
    """from_env() resolves socket path from HYPRLAND_INSTANCE_SIGNATURE."""
    with (
        patch("canvas.hypr.os.environ.get", return_value="test_sig"),
        patch("canvas.hypr.os.getuid", return_value=1000),
        patch("canvas.hypr.os.path.exists", return_value=True),
    ):
        ipc = HyprIPC.from_env()

    assert "test_sig" in ipc._socket_path
