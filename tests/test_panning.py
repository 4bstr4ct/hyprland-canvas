from canvas.panning import EdgeScrollState, PanningState


def test_start_pan_activates():
    """start_pan sets pan_active=True and returns PAN_ON."""
    state = PanningState(speed=1.0)
    result = state.start_pan()
    assert state.pan_active is True
    assert result == "PAN_ON"


def test_stop_pan_deactivates():
    """stop_pan sets pan_active=False."""
    state = PanningState(speed=1.0)
    state.start_pan()
    state.stop_pan()
    assert state.pan_active is False


def test_cursor_delta_accumulates_when_panning():
    """When panning, cursor movement produces inverted total delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.get_total_delta()
    assert dx == -10
    assert dy == -5


def test_cursor_delta_applies_speed():
    """Total delta is multiplied by speed."""
    state = PanningState(speed=2.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.get_total_delta()
    assert dx == -20
    assert dy == -10


def test_cursor_delta_applies_invert():
    """When inverted, delta is not negated (same direction as cursor)."""
    state = PanningState(speed=1.0)
    state.inverted = True
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.get_total_delta()
    assert dx == 10
    assert dy == 5


def test_no_delta_when_not_panning():
    """No delta when pan_active is False."""
    state = PanningState(speed=1.0)

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_total_delta_is_running_total():
    """get_total_delta returns running total, not per-frame delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx1, dy1 = state.get_total_delta()
    assert dx1 == -10
    assert dy1 == -5

    state.update_cursor(120, 110)

    dx2, dy2 = state.get_total_delta()
    assert dx2 == -20
    assert dy2 == -10


def test_get_total_delta_does_not_reset():
    """get_total_delta does not reset the accumulator."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx1, dy1 = state.get_total_delta()
    dx2, dy2 = state.get_total_delta()
    assert dx1 == dx2
    assert dy1 == dy2


def test_total_delta_accumulates_multiple_moves():
    """Multiple cursor movements accumulate into total delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 100)  # +10 right
    state.update_cursor(100, 100)  # -10 left (back to start)

    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_stop_pan_clears_total():
    """stop_pan clears total delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    state.stop_pan()
    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_is_dragging_property():
    """is_dragging reflects pan_active state."""
    state = PanningState(speed=1.0)
    assert state.is_dragging is False
    state.start_pan()
    assert state.is_dragging is True
    state.stop_pan()
    assert state.is_dragging is False


def test_max_speed_clamps_step():
    """max_speed caps per-frame cursor delta, not total."""
    state = PanningState(speed=1.0, max_speed=10)
    state.start_pan()

    state.update_cursor(0, 0)
    state.update_cursor(100, 100)  # would be -100,-100 but clamped to -10,-10

    dx, dy = state.get_total_delta()
    assert dx == -10
    assert dy == -10


def test_max_speed_none_no_clamp():
    """max_speed=None means no clamping."""
    state = PanningState(speed=1.0, max_speed=None)
    state.start_pan()

    state.update_cursor(0, 0)
    state.update_cursor(100, 100)

    dx, dy = state.get_total_delta()
    assert dx == -100
    assert dy == -100


def test_poller_alive_initially_true():
    """poller_alive starts as True."""
    state = PanningState(speed=1.0)
    assert state.poller_alive is True


# --- EdgeScrollState tests ---


def test_edge_scroll_inactive_by_default():
    """EdgeScrollState starts inactive."""
    es = EdgeScrollState()
    assert es.active is False


def test_edge_scroll_start():
    """start() activates and stores dragged address."""
    es = EdgeScrollState(enabled=True)
    result = es.start("0xabc")
    assert result == "EDGE_ON"
    assert es.active is True
    assert es.dragged_addr == "0xabc"


def test_edge_scroll_start_disabled():
    """start() returns EDGE_DISABLED when not enabled."""
    es = EdgeScrollState(enabled=False)
    result = es.start("0xabc")
    assert result == "EDGE_DISABLED"
    assert es.active is False


def test_edge_scroll_stop():
    """stop() deactivates and clears address."""
    es = EdgeScrollState(enabled=True)
    es.start("0xabc")
    result = es.stop()
    assert result == "EDGE_OFF"
    assert es.active is False
    assert es.dragged_addr == ""


def test_edge_scroll_compute_at_right_edge():
    """Cursor at right edge produces positive dx (scroll right)."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)
    es.start("0xabc")

    dx, dy = es.compute_scroll(1910, 540)
    assert dx > 0
    assert dy == 0


def test_edge_scroll_compute_at_left_edge():
    """Cursor at left edge produces negative dx (scroll left)."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)
    es.start("0xabc")

    dx, dy = es.compute_scroll(10, 540)
    assert dx < 0
    assert dy == 0


def test_edge_scroll_compute_at_bottom_edge():
    """Cursor at bottom edge produces positive dy."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)
    es.start("0xabc")

    dx, dy = es.compute_scroll(960, 1070)
    assert dx == 0
    assert dy > 0


def test_edge_scroll_compute_at_center():
    """Cursor at center produces zero scroll."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)
    es.start("0xabc")

    dx, dy = es.compute_scroll(960, 540)
    assert dx == 0
    assert dy == 0


def test_edge_scroll_compute_when_inactive():
    """compute_scroll returns (0,0) when not active."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)

    dx, dy = es.compute_scroll(10, 540)
    assert dx == 0
    assert dy == 0


def test_edge_scroll_quadratic_ramp():
    """Speed increases quadratically — closer to edge = much faster."""
    es = EdgeScrollState(threshold=50, speed=20.0, enabled=True)
    es.set_monitor_rect(0, 0, 1920, 1080)
    es.start("0xabc")

    dx_far, _ = es.compute_scroll(40, 540)
    dx_near, _ = es.compute_scroll(5, 540)
    assert abs(dx_near) > abs(dx_far)
