from unittest.mock import MagicMock, patch

from canvas.navigation import Navigator


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
    """Create a fake window dict as hyprctl would return."""
    return {
        "class": class_name,
        "address": address,
        "at": [at_x, at_y],
        "size": [size_w, size_h],
        "floating": floating,
        "workspace": {"id": workspace_id},
    }


def test_navigate_right_cycles():
    """Navigate right cycles through windows, skipping protected."""
    windows = [
        _make_window("terminal", "0x1", 100, 100, 400, 300),
        _make_window("firefox", "0x2", 600, 100, 800, 600),  # protected
        _make_window("editor", "0x3", 100, 500, 400, 300),
    ]

    nav = Navigator(ipc=MagicMock(), protected_apps=["firefox"], cooldown=0.0)

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_get_floating_windows", return_value=windows),
        patch.object(nav, "_get_focused_window", return_value=windows[0]),
        patch.object(nav, "_get_monitor_center", return_value=(960, 540)),
        patch.object(nav, "_pan_to_window") as mock_pan,
    ):
        nav.navigate("right")
        mock_pan.assert_called_once()
        called_addr = mock_pan.call_args[0][1]
        assert called_addr == "0x3"


def test_navigate_left_cycles():
    """Navigate left goes to previous window."""
    windows = [
        _make_window("terminal", "0x1", 100, 100, 400, 300),
        _make_window("editor", "0x3", 100, 500, 400, 300),
    ]

    nav = Navigator(ipc=MagicMock(), protected_apps=[], cooldown=0.0)

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_get_floating_windows", return_value=windows),
        patch.object(nav, "_get_focused_window", return_value=windows[1]),
        patch.object(nav, "_get_monitor_center", return_value=(960, 540)),
        patch.object(nav, "_pan_to_window") as mock_pan,
    ):
        nav.navigate("left")
        called_addr = mock_pan.call_args[0][1]
        assert called_addr == "0x1"


def test_navigate_single_window_does_nothing():
    """With only one floating window, navigation does nothing."""
    windows = [_make_window("terminal", "0x1", 100, 100, 400, 300)]

    nav = Navigator(ipc=MagicMock(), protected_apps=[], cooldown=0.0)

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_get_floating_windows", return_value=windows),
        patch.object(nav, "_get_focused_window", return_value=windows[0]),
        patch.object(nav, "_pan_to_window") as mock_pan,
    ):
        nav.navigate("right")
        mock_pan.assert_not_called()


def test_is_protected():
    """Protected apps are detected by class name."""
    nav = Navigator(ipc=MagicMock(), protected_apps=["firefox", "brave-browser"], cooldown=0.0)
    assert nav._is_protected({"class": "firefox"}) is True
    assert nav._is_protected({"class": "brave-browser"}) is True
    assert nav._is_protected({"class": "kitty"}) is False


def test_navigate_all_protected_does_nothing():
    """When all windows are protected, navigation does nothing."""
    windows = [
        _make_window("firefox", "0x1", 100, 100, 400, 300),
        _make_window("brave-browser", "0x2", 600, 100, 800, 600),
    ]

    nav = Navigator(ipc=MagicMock(), protected_apps=["firefox", "brave-browser"], cooldown=0.0)

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_get_floating_windows", return_value=windows),
        patch.object(nav, "_get_focused_window", return_value=windows[0]),
        patch.object(nav, "_pan_to_window") as mock_pan,
    ):
        nav.navigate("right")
        mock_pan.assert_not_called()


def test_navigate_cooldown_blocks_rapid_calls():
    """Cooldown prevents rapid successive navigation calls."""
    windows = [
        _make_window("a", "0x1", 100, 100, 400, 300),
        _make_window("b", "0x2", 600, 100, 400, 300),
    ]

    nav = Navigator(ipc=MagicMock(), protected_apps=[], cooldown=10.0)  # 10s cooldown

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_get_floating_windows", return_value=windows),
        patch.object(nav, "_get_focused_window", return_value=windows[0]),
        patch.object(nav, "_pan_to_window") as mock_pan,
    ):
        nav.navigate("right")
        assert mock_pan.call_count == 1
        nav.navigate("right")  # blocked by cooldown
        assert mock_pan.call_count == 1


def test_canvas_toggle():
    """canvas_toggle switches between CANVAS_ON and CANVAS_OFF."""
    nav = Navigator(ipc=MagicMock(), protected_apps=[], cooldown=0.0)

    with (
        patch.object(nav, "_get_active_workspace_id", return_value=1),
        patch.object(nav, "_set_all_floating"),
    ):
        result1 = nav.canvas_toggle()
        assert result1 == "CANVAS_ON"
        assert 1 in nav._canvas_mode_workspaces

        result2 = nav.canvas_toggle()
        assert result2 == "CANVAS_OFF"
        assert 1 not in nav._canvas_mode_workspaces
