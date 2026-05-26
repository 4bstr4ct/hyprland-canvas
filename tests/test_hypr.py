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


def test_send_reconnects_on_error():
    """send() recovers from stale persistent socket by reconnecting."""
    ipc = HyprIPC("/tmp/test.sock")

    broken = MagicMock()
    broken.sendall.side_effect = ConnectionError("broken")
    broken.close.return_value = None
    ipc._socket = broken

    fresh = MagicMock()
    fresh.recv.side_effect = [b"recovered", b""]
    ipc._connect = lambda: fresh  # type: ignore[assignment]

    result = ipc.send("cursorpos")

    assert result == "recovered"
    broken.close.assert_called_once()


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
