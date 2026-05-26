"""Tests for canvas.daemon — DaemonState and helper functions."""

from unittest.mock import MagicMock

from canvas.daemon import DaemonState, _lua_escape
from canvas.panning import PanningState


def _make_daemon_state(ipc: MagicMock | None = None) -> DaemonState:
    panning = PanningState(speed=1.0)
    navigator = MagicMock()
    if ipc is None:
        ipc = MagicMock()
    return DaemonState(panning=panning, navigator=navigator, ipc=ipc)


def test_handle_ipc_pan_start_fetches_baselines():
    """PAN_START fetches baselines and starts panning."""
    ipc = MagicMock()
    ipc.send.return_value = "[]"
    ds = _make_daemon_state(ipc)

    result = ds.handle_ipc("PAN_START")

    assert result == "PAN_ON"
    assert ds.panning.pan_active is True
    ipc.send.assert_called_with("j/clients")


def test_handle_ipc_pan_stop_clears_baselines():
    """PAN_STOP stops panning and clears baselines."""
    ds = _make_daemon_state()
    ds.panning.start_pan()
    ds.baselines = {"0x1": (100, 200)}

    result = ds.handle_ipc("PAN_STOP")

    assert result == "PAN_OFF"
    assert ds.panning.pan_active is False
    assert ds.baselines == {}


def test_handle_ipc_toggle():
    """TOGGLE flips inverted state."""
    ds = _make_daemon_state()
    ds.panning.inverted = True

    result = ds.handle_ipc("TOGGLE")
    assert result == "NORMAL"
    assert ds.panning.inverted is False

    result2 = ds.handle_ipc("TOGGLE")
    assert result2 == "INVERTED"
    assert ds.panning.inverted is True


def test_handle_ipc_nav():
    """NAV_LEFT/NAV_RIGHT delegates to navigator."""
    ds = _make_daemon_state()

    assert ds.handle_ipc("NAV_LEFT") == "OK"
    ds.navigator.navigate.assert_called_with("left")

    assert ds.handle_ipc("NAV_RIGHT") == "OK"
    ds.navigator.navigate.assert_called_with("right")


def test_handle_ipc_ping():
    assert _make_daemon_state().handle_ipc("PING") == "PONG"


def test_handle_ipc_status():
    ds = _make_daemon_state()
    ds.panning.inverted = True
    assert ds.handle_ipc("STATUS") == "INVERTED IDLE"

    ds.panning.start_pan()
    assert ds.handle_ipc("STATUS") == "INVERTED PANNING"


def test_handle_ipc_unknown():
    assert _make_daemon_state().handle_ipc("FOOBAR") == "UNKNOWN: FOOBAR"


def test_fetch_baselines_parses_clients():
    """fetch_baselines extracts floating window addresses and positions."""
    ipc = MagicMock()
    ipc.send.return_value = (
        '[{"address":"0xabc","floating":true,"at":[10,20]},'
        '{"address":"0xdef","floating":false,"at":[30,40]}]'
    )
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xabc": (10, 20)}


def test_fetch_baselines_empty_response():
    """fetch_baselines handles empty client list."""
    ipc = MagicMock()
    ipc.send.return_value = "[]"
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {}


def test_fetch_baselines_bad_json():
    """fetch_baselines handles invalid JSON gracefully."""
    ipc = MagicMock()
    ipc.send.return_value = "not json"
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {}


def test_restore_baselines_moves_windows_back():
    """restore_baselines sends Lua to move windows to original positions."""
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}

    ds.restore_baselines()

    ipc.eval_lua.assert_called_once()
    lua_code = ipc.eval_lua.call_args[0][0]
    assert "0xabc" in lua_code
    assert "b[1]" in lua_code
    assert "relative = false" in lua_code


def test_restore_baselines_skips_when_empty():
    """restore_baselines does nothing if no baselines recorded."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)

    ds.restore_baselines()

    ipc.eval_lua.assert_not_called()


def test_move_windows_to_delta():
    """move_windows_to_delta generates Lua with correct offsets."""
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}

    ds.move_windows_to_delta(50, -30)

    ipc.eval_lua.assert_called_once()
    lua_code = ipc.eval_lua.call_args[0][0]
    assert "b[1] + 50" in lua_code
    assert "b[2] + -30" in lua_code
    assert "relative = false" in lua_code


def test_lua_escape():
    """_lua_escape handles backslashes, quotes, and newlines."""
    assert _lua_escape("hello") == "hello"
    assert _lua_escape('a"b') == 'a\\"b'
    assert _lua_escape("a\\b") == "a\\\\b"
    assert _lua_escape("a\nb") == "a\\nb"
    assert _lua_escape("0x1a2b") == "0x1a2b"


def test_fetch_baselines_window_without_at():
    """fetch_baselines uses default [0,0] when 'at' field missing."""
    ipc = MagicMock()
    ipc.send.return_value = '[{"address":"0xabc","floating":true}]'
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xabc": (0, 0)}


def test_fetch_baselines_window_with_empty_address():
    """fetch_baselines skips windows with empty address."""
    ipc = MagicMock()
    ipc.send.return_value = '[{"address":"","floating":true,"at":[10,20]}]'
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {}


def test_fetch_baselines_ipc_error():
    """fetch_baselines handles IPC send failure gracefully."""
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("socket failed")
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {}


def test_restore_baselines_ipc_error():
    """restore_baselines handles IPC eval_lua failure gracefully."""
    ipc = MagicMock()
    ipc.eval_lua.side_effect = ConnectionError("socket failed")
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}

    ds.restore_baselines()  # should not raise

    assert ds.baselines == {"0xabc": (100, 200)}


def test_move_windows_to_delta_ipc_error():
    """move_windows_to_delta handles IPC failure gracefully."""
    ipc = MagicMock()
    ipc.eval_lua.side_effect = ConnectionError("socket failed")
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}

    ds.move_windows_to_delta(50, 50)  # should not raise


def test_fetch_baselines_multiple_floating():
    """fetch_baselines correctly extracts multiple floating windows."""
    ipc = MagicMock()
    ipc.send.return_value = (
        '[{"address":"0xaaa","floating":true,"at":[10,20]},'
        '{"address":"0xbbb","floating":true,"at":[300,400]},'
        '{"address":"0xccc","floating":false,"at":[500,600]}]'
    )
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xaaa": (10, 20), "0xbbb": (300, 400)}
