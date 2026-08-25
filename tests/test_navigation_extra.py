"""Additional tests for canvas.navigation — covering IPC-dependent methods."""

import json
from unittest.mock import MagicMock

from canvas.navigation import Navigator, _safe_int


def _make_nav(ipc: MagicMock | None = None) -> Navigator:
    if ipc is None:
        ipc = MagicMock()
    return Navigator(ipc=ipc, protected_apps=[], cooldown=0.0)


def _make_window(
    class_name: str,
    address: str,
    at_x: int,
    at_y: int,
    size_w: int,
    size_h: int,
    floating: bool = True,
    workspace_id: int = 1,
) -> dict:
    return {
        "class": class_name,
        "address": address,
        "at": [at_x, at_y],
        "size": [size_w, size_h],
        "floating": floating,
        "workspace": {"id": workspace_id},
    }


def test_get_active_workspace_id():
    ipc = MagicMock()
    ipc.send.return_value = json.dumps({"id": 3})
    nav = _make_nav(ipc)
    assert nav._get_active_workspace_id() == 3


def test_get_active_workspace_id_error():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    assert nav._get_active_workspace_id() is None


def test_get_floating_windows():
    ipc = MagicMock()
    w1 = _make_window("a", "0x1", 10, 20, 400, 300, floating=True, workspace_id=1)
    w2 = _make_window("b", "0x2", 50, 60, 400, 300, floating=False, workspace_id=1)
    w3 = _make_window("c", "0x3", 90, 100, 400, 300, floating=True, workspace_id=2)
    ipc.send.return_value = json.dumps([w1, w2, w3])
    nav = _make_nav(ipc)
    result = nav._get_floating_windows(1)
    assert len(result) == 1
    assert result[0]["address"] == "0x1"


def test_get_floating_windows_error():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    assert nav._get_floating_windows(1) == []


def test_get_focused_window():
    ipc = MagicMock()
    ipc.send.return_value = json.dumps({"class": "kitty", "address": "0x1"})
    nav = _make_nav(ipc)
    result = nav._get_focused_window()
    assert result["class"] == "kitty"


def test_get_focused_window_error():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    assert nav._get_focused_window() is None


def test_get_monitor_center_focused():
    ipc = MagicMock()
    ipc.send.return_value = json.dumps(
        [
            {"focused": True, "x": 0, "y": 0, "width": 1920, "height": 1080},
            {"focused": False, "x": 1920, "y": 0, "width": 1920, "height": 1080},
        ]
    )
    nav = _make_nav(ipc)
    cx, cy = nav._get_monitor_center()
    assert cx == 960
    assert cy == 540


def test_get_monitor_center_fallback_first():
    ipc = MagicMock()
    ipc.send.return_value = json.dumps(
        [
            {"focused": False, "x": 1920, "y": 0, "width": 1920, "height": 1080},
        ]
    )
    nav = _make_nav(ipc)
    cx, cy = nav._get_monitor_center()
    assert cx == 2880


def test_get_monitor_center_error():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    cx, cy = nav._get_monitor_center()
    assert cx == 960
    assert cy == 540


def test_pan_to_window():
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    nav = _make_nav(ipc)
    windows = [_make_window("kitty", "0x1", 100, 200, 400, 300)]
    nav._pan_to_window(windows, "0x1", 960, 540)
    ipc.eval_lua.assert_called_once()
    lua = ipc.eval_lua.call_args[0][0]
    assert "0x1" in lua
    assert "relative = true" in lua


def test_pan_to_window_target_not_found():
    ipc = MagicMock()
    nav = _make_nav(ipc)
    windows = [_make_window("kitty", "0x1", 100, 200, 400, 300)]
    nav._pan_to_window(windows, "0x999", 960, 540)
    ipc.eval_lua.assert_not_called()


def test_set_all_floating_make_float():
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    nav = _make_nav(ipc)
    nav._set_all_floating(1, floating=True)
    ipc.eval_lua.assert_called_once()
    lua = ipc.eval_lua.call_args[0][0]
    assert "floating = false" in lua
    assert "float" in lua


def test_set_all_floating_make_tiled():
    ipc = MagicMock()
    ipc.eval_lua.return_value = "ok"
    nav = _make_nav(ipc)
    nav._set_all_floating(1, floating=False)
    lua = ipc.eval_lua.call_args[0][0]
    assert "floating = true" in lua


def test_set_all_floating_error():
    ipc = MagicMock()
    ipc.eval_lua.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    nav._set_all_floating(1, floating=True)  # should not raise


def test_canvas_toggle_no_workspace():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    assert nav.canvas_toggle() == "ERROR:NO_WORKSPACE"


def test_safe_int_valid():
    assert _safe_int(42, "x") == 42
    assert _safe_int("100", "x") == 100


def test_safe_int_invalid():
    import pytest

    with pytest.raises(ValueError, match="unsafe Lua value"):
        _safe_int("abc", "x")


def test_safe_int_none():
    import pytest

    with pytest.raises(ValueError, match="unsafe Lua value"):
        _safe_int(None, "x")


def test_navigate_no_workspace():
    ipc = MagicMock()
    ipc.send.side_effect = ConnectionError("fail")
    nav = _make_nav(ipc)
    nav.navigate("right")  # should not raise


def test_navigate_no_focused():
    ipc = MagicMock()
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        json.dumps([]),
        Exception("no focused"),
    ]
    nav = _make_nav(ipc)
    nav.navigate("right")  # should not raise


def test_navigate_focused_not_in_list():
    ipc = MagicMock()
    w = _make_window("a", "0x1", 100, 100, 400, 300)
    ipc.send.side_effect = [
        json.dumps({"id": 1}),
        json.dumps([w]),
        json.dumps({"address": "0x999"}),
    ]
    nav = _make_nav(ipc)
    nav.navigate("right")  # should not raise


def test_pan_to_window_scopes_lua_to_workspace():
    ipc = MagicMock()
    nav = _make_nav(ipc)
    windows = [_make_window("kitty", "0x1", 100, 200, 400, 300)]
    nav._pan_to_window(windows, "0x1", 960, 540, workspace_id=7)
    lua = ipc.eval_lua.call_args[0][0]
    assert lua.count("workspace = 7") == 2  # move loop + focus loop
