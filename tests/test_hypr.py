"""Tests for canvas.hypr module — Hyprland IPC abstraction."""

from unittest.mock import MagicMock, patch

from canvas.hypr import eval_lua, get_cursor_pos, send


def test_send_calls_socket():
    """send() connects, sends command, reads response."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"ok", b""]

    with (
        patch("canvas.hypr.socket.socket", return_value=mock_sock),
        patch("canvas.hypr._get_socket_path", return_value="/tmp/test.sock"),
    ):
        result = send("cursorpos")

    assert result == "ok"
    mock_sock.connect.assert_called_once_with("/tmp/test.sock")
    mock_sock.sendall.assert_called_once_with(b"cursorpos")


def test_eval_lua_prefixes_eval():
    """eval_lua prepends 'eval ' to lua code."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"ok", b""]

    with (
        patch("canvas.hypr.socket.socket", return_value=mock_sock),
        patch("canvas.hypr._get_socket_path", return_value="/tmp/test.sock"),
    ):
        result = eval_lua("hl.dispatch(hl.dsp.no_op())")

    assert result == "ok"
    mock_sock.sendall.assert_called_once_with(b"eval hl.dispatch(hl.dsp.no_op())")


def test_get_cursor_pos_parses_response():
    """get_cursor_pos parses 'X, Y' response."""
    mock_sock = MagicMock()
    mock_sock.recv.side_effect = [b"100, 200", b""]

    with (
        patch("canvas.hypr.socket.socket", return_value=mock_sock),
        patch("canvas.hypr._get_socket_path", return_value="/tmp/test.sock"),
    ):
        x, y = get_cursor_pos()

    assert x == 100
    assert y == 200


def test_send_reconnects_on_error():
    """send() recovers from stale persistent socket by reconnecting."""
    import canvas.hypr as hypr_mod

    # Set up a broken persistent socket
    broken = MagicMock()
    broken.sendall.side_effect = ConnectionError("broken")
    broken.close.return_value = None
    hypr_mod._socket = broken

    # Fresh socket that works
    fresh = MagicMock()
    fresh.recv.side_effect = [b"recovered", b""]

    call_count = [0]

    def mock_socket(*a, **kw):
        call_count[0] += 1
        return fresh

    with (
        patch("canvas.hypr.socket.socket", side_effect=mock_socket),
        patch("canvas.hypr._get_socket_path", return_value="/tmp/test.sock"),
    ):
        result = send("cursorpos")

    assert result == "recovered"
    broken.close.assert_called_once()
