"""Additional tests for canvas.daemon — covering edge cases and daemon state."""

from unittest.mock import MagicMock, patch

from canvas.daemon import DaemonState
from canvas.panning import EdgeScrollParams, EdgeScrollState, PanningState


def _make_daemon_state(ipc: MagicMock | None = None) -> DaemonState:
    panning = PanningState(speed=1.0)
    edge_scroll = EdgeScrollState(ramp_distance=50, speed=20.0, enabled=True)
    navigator = MagicMock()
    if ipc is None:
        ipc = MagicMock()
    return DaemonState(panning=panning, edge_scroll=edge_scroll, navigator=navigator, ipc=ipc)


def test_fetch_monitor_rect_focused():
    ipc = MagicMock()
    ipc.send.return_value = '[{"focused":true,"x":0,"y":0,"width":2560,"height":1440}]'
    ds = _make_daemon_state(ipc)
    ds._fetch_monitor_rect()
    assert ds.edge_scroll._monitor_w == 2560
    assert ds.edge_scroll._monitor_h == 1440


def test_fetch_monitor_rect_fallback_first():
    ipc = MagicMock()
    ipc.send.return_value = '[{"focused":false,"x":0,"y":0,"width":1920,"height":1080}]'
    ds = _make_daemon_state(ipc)
    ds._fetch_monitor_rect()
    assert ds.edge_scroll._monitor_w == 1920


def test_fetch_monitor_rect_error():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    ds = _make_daemon_state(ipc)
    ds._fetch_monitor_rect()  # should not raise


def test_handle_ipc_canvas_toggle():
    ds = _make_daemon_state()
    ds.handle_ipc("CANVAS_TOGGLE")
    ds.navigator.canvas_toggle.assert_called_once()


def test_handle_ipc_edge_start_no_cursor():
    ipc = MagicMock()
    ipc.send.return_value = '{"address":"0xabc","at":[100,200],"size":[500,300]}'
    ds = _make_daemon_state(ipc)
    with patch("canvas.daemon.get_cursor_pos", side_effect=ConnectionError("fail")):
        result = ds.handle_ipc("EDGE_START")
    assert result == "EDGE_NO_CURSOR"


def test_edge_scroll_move_ipc_error():
    ipc = MagicMock()
    ipc.eval_lua.side_effect = ConnectionError("fail")
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
    ds.edge_scroll_workspace = 1
    ds.edge_scroll_move(10, -5)  # should not raise


def test_move_windows_to_delta_no_baselines():
    """Without baselines (or workspace) the move must be a no-op."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.move_windows_to_delta(10, 20)
    ipc.eval_lua.assert_not_called()


def test_fetch_baselines_window_with_short_at():
    ipc = MagicMock()
    ipc.send.side_effect = [
        '{"id":1}',
        '[{"address":"0xabc","floating":true,"at":[10]}]',
    ]
    ds = _make_daemon_state(ipc)
    ds.fetch_baselines()
    assert ds.baselines == {}


def test_fetch_monitor_rect_empty_list():
    ipc = MagicMock()
    ipc.send.return_value = "[]"
    ds = _make_daemon_state(ipc)
    ds._fetch_monitor_rect()  # should not raise, defaults stay


# --- idle pan stop drops baselines ---


def test_idle_pan_stop_clears_baselines():
    """Idle timeout must drop baselines so shutdown cannot restore stale positions."""
    import time as time_mod

    ds = _make_daemon_state()
    ds.panning.start_pan()
    ds.baselines = {"0x1": (10, 20)}
    ds.panning._last_move_time = time_mod.monotonic() - 1.0

    assert ds.handle_idle_pan_stop() is True
    assert ds.panning.pan_active is False
    assert ds.baselines == {}


def test_idle_pan_stop_noop_while_active():
    ds = _make_daemon_state()
    ds.panning.start_pan()
    ds.baselines = {"0x1": (10, 20)}

    assert ds.handle_idle_pan_stop() is False
    assert ds.panning.pan_active is True
    assert ds.baselines == {"0x1": (10, 20)}


def test_restore_skipped_after_idle_stop():
    """Full chain: idle stop → shutdown restore is a no-op (no window movement)."""
    import time as time_mod

    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.panning.start_pan()
    ds.baselines = {"0xabc": (100, 200)}
    ds.panning._last_move_time = time_mod.monotonic() - 1.0

    ds.handle_idle_pan_stop()
    ds.restore_baselines()

    ipc.eval_lua.assert_not_called()


def test_shutdown_restores_only_when_pan_still_active():
    """SIGTERM during active pan → windows restored; after PAN_STOP → untouched."""
    ipc = MagicMock()
    ds = _make_daemon_state(ipc)
    ds.panning.start_pan()
    ds.baselines = {"0xabc": (100, 200)}
    ds.baseline_workspace = 1

    ds.restore_baselines()  # pan active: baseline restore intended
    ipc.eval_lua.assert_called_once()

    ipc.reset_mock()
    ds.handle_ipc("PAN_STOP")
    ds.restore_baselines()
    ipc.eval_lua.assert_not_called()
