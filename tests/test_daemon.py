"""Tests for canvas.daemon — DaemonState and helper functions."""

import json
from unittest.mock import MagicMock, patch

from canvas.daemon import DaemonState, _lua_escape
from canvas.panning import EdgeScrollParams, EdgeScrollState, PanningState


def _make_daemon_state(ipc: MagicMock | None = None) -> DaemonState:
    panning = PanningState(speed=1.0)
    edge_scroll = EdgeScrollState(ramp_distance=50, speed=20.0, enabled=True)
    navigator = MagicMock()
    if ipc is None:
        ipc = MagicMock()
    return DaemonState(panning=panning, edge_scroll=edge_scroll, navigator=navigator, ipc=ipc)


def _clients(clients_json: str) -> str:
    return clients_json


def test_handle_ipc_pan_start_fetches_baselines():
    """PAN_START fetches baselines and starts panning."""
    ipc = MagicMock()
    ipc.send.side_effect = [json.dumps({"id": 1}), "[]"]
    ds = _make_daemon_state(ipc)

    result = ds.handle_ipc("PAN_START")

    assert result == "PAN_ON"
    assert ds.panning.pan_active is True
    ipc.send.assert_any_call("j/clients")


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
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        _clients(
            '[{"address":"0xabc","floating":true,"at":[10,20],"workspace":{"id":1}},'
            '{"address":"0xdef","floating":false,"at":[30,40],"workspace":{"id":1}}]'
        ),
    ]
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xabc": (10, 20)}
    assert ds.baseline_workspace == 1


def test_fetch_baselines_scoped_to_active_workspace():
    """Windows on other workspaces must not enter the baseline snapshot."""
    ipc = MagicMock()
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        _clients(
            '[{"address":"0xaaa","floating":true,"at":[1,2],"workspace":{"id":1}},'
            '{"address":"0xbbb","floating":true,"at":[3,4],"workspace":{"id":2}}]'
        ),
    ]
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xaaa": (1, 2)}
    assert ds.baseline_workspace == 1


def test_fetch_baselines_empty_response():
    """fetch_baselines handles empty client list."""
    ipc = MagicMock()
    ipc.send.side_effect = [json.dumps({"id": 1}), "[]"]
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
    assert ds.baseline_workspace is None


def test_restore_baselines_moves_windows_back():
    """restore_baselines sends Lua to move windows to original positions."""
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}
    ds.baseline_workspace = 4

    ds.restore_baselines()

    ipc.eval_lua.assert_called_once()
    lua_code = ipc.eval_lua.call_args[0][0]
    assert "0xabc" in lua_code
    assert "b[1]" in lua_code
    assert "relative = false" in lua_code
    assert "workspace = 4" in lua_code


def test_restore_baselines_skips_when_empty():
    """restore_baselines does nothing if no baselines recorded."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)

    ds.restore_baselines()

    ipc.eval_lua.assert_not_called()


def test_move_windows_to_delta():
    """move_windows_to_delta generates Lua with correct offsets and workspace scope."""
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}
    ds.baseline_workspace = 2

    ds.move_windows_to_delta(50, -30)

    ipc.eval_lua.assert_called_once()
    lua_code = ipc.eval_lua.call_args[0][0]
    assert "b[1] + 50" in lua_code
    assert "b[2] + -30" in lua_code
    assert "relative = false" in lua_code
    assert "workspace = 2" in lua_code


def test_move_windows_to_delta_without_workspace_is_noop():
    """Without a captured workspace (failed fetch) no Lua must be dispatched."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.baselines = {"0xabc": (100, 200)}
    ds.baseline_workspace = None

    ds.move_windows_to_delta(50, 30)

    ipc.eval_lua.assert_not_called()


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
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        _clients('[{"address":"0xabc","floating":true,"workspace":{"id":1}}]'),
    ]
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xabc": (0, 0)}


def test_fetch_baselines_window_with_empty_address():
    """fetch_baselines skips windows with empty address."""
    ipc = MagicMock()
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        _clients('[{"address":"","floating":true,"at":[10,20]}]'),
    ]
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
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        _clients(
            '[{"address":"0xaaa","floating":true,"at":[10,20],"workspace":{"id":1}},'
            '{"address":"0xbbb","floating":true,"at":[300,400],"workspace":{"id":1}},'
            '{"address":"0xccc","floating":false,"at":[500,600],"workspace":{"id":1}}]'
        ),
    ]
    ds = _make_daemon_state(ipc)

    ds.fetch_baselines()

    assert ds.baselines == {"0xaaa": (10, 20), "0xbbb": (300, 400)}


def test_handle_ipc_edge_start():
    """EDGE_START fetches window, cursor, workspace and activates edge-scroll."""
    ipc = MagicMock()
    ipc.send.side_effect = [
        '{"address":"0xabc","at":[100,200],"size":[500,300]}',
        json.dumps({"id": 3}),
        '[{"focused":true,"x":0,"y":0,"width":1920,"height":1080}]',
    ]
    ds = _make_daemon_state(ipc)

    with patch("canvas.daemon.get_cursor_pos", return_value=(350, 350)):
        result = ds.handle_ipc("EDGE_START")
        assert result == "EDGE_ON"
        assert ds.edge_scroll.active is True
        assert ds.edge_scroll.dragged_addr == "0xabc"
        assert ds.edge_scroll_workspace == 3


def test_handle_ipc_edge_start_no_workspace():
    """EDGE_START without a resolvable workspace must not activate."""
    ipc = MagicMock()
    ipc.send.side_effect = [
        '{"address":"0xabc","at":[100,200],"size":[500,300]}',
        Exception("workspace query failed"),
    ]
    ds = _make_daemon_state(ipc)

    with patch("canvas.daemon.get_cursor_pos", return_value=(350, 350)):
        result = ds.handle_ipc("EDGE_START")

    assert result == "EDGE_NO_WORKSPACE"
    assert ds.edge_scroll.active is False


def test_handle_ipc_edge_start_no_window():
    """EDGE_START with no active window returns EDGE_NO_WINDOW."""
    ipc = MagicMock()
    ipc.send.side_effect = Exception("no window")
    ds = _make_daemon_state(ipc)

    result = ds.handle_ipc("EDGE_START")
    assert result == "EDGE_NO_WINDOW"


def test_handle_ipc_edge_stop():
    """EDGE_STOP deactivates edge-scroll."""
    ds = _make_daemon_state()
    ds.edge_scroll.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=100,
            win_y=200,
            win_w=500,
            win_h=300,
            cursor_x=350,
            cursor_y=350,
        )
    )

    result = ds.handle_ipc("EDGE_STOP")
    assert result == "EDGE_OFF"
    assert ds.edge_scroll.active is False


def test_edge_scroll_move_excludes_dragged():
    """edge_scroll_move generates Lua that excludes dragged window, workspace-scoped."""
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    ds = _make_daemon_state(ipc)
    ds.edge_scroll.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=100,
            win_y=200,
            win_w=500,
            win_h=300,
            cursor_x=350,
            cursor_y=350,
        )
    )
    ds.edge_scroll_workspace = 5

    ds.edge_scroll_move(10, -5)

    ipc.eval_lua.assert_called_once()
    lua_code = ipc.eval_lua.call_args[0][0]
    assert "0xabc" in lua_code
    assert "~=" in lua_code
    assert "relative = true" in lua_code
    assert "workspace = 5" in lua_code


def test_edge_scroll_move_without_workspace_is_noop():
    """Without a captured workspace edge moves must never dispatch."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.edge_scroll.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=100,
            win_y=200,
            win_w=500,
            win_h=300,
            cursor_x=350,
            cursor_y=350,
        )
    )
    ds.edge_scroll_workspace = None

    ds.edge_scroll_move(10, -5)

    ipc.eval_lua.assert_not_called()


def test_edge_scroll_move_zero_delta_is_noop():
    """edge_scroll_move with (0,0) does nothing."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.edge_scroll.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=100,
            win_y=200,
            win_w=500,
            win_h=300,
            cursor_x=350,
            cursor_y=350,
        )
    )

    ds.edge_scroll_move(0, 0)

    ipc.eval_lua.assert_not_called()
