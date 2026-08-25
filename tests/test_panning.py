from canvas.panning import EdgeScrollParams, EdgeScrollState, PanningState


def test_start_pan_activates():
    state = PanningState(speed=1.0)
    result = state.start_pan()
    assert state.pan_active is True
    assert result == "PAN_ON"


def test_stop_pan_deactivates():
    state = PanningState(speed=1.0)
    state.start_pan()
    state.stop_pan()
    assert state.pan_active is False


def test_cursor_delta_accumulates_when_panning():
    state = PanningState(speed=1.0)
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    dx, dy = state.get_total_delta()
    assert dx == -10
    assert dy == -5


def test_cursor_delta_applies_speed():
    state = PanningState(speed=2.0)
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    dx, dy = state.get_total_delta()
    assert dx == -20
    assert dy == -10


def test_cursor_delta_applies_invert():
    state = PanningState(speed=1.0)
    state.inverted = True
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    dx, dy = state.get_total_delta()
    assert dx == 10
    assert dy == 5


def test_no_delta_when_not_panning():
    state = PanningState(speed=1.0)
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_total_delta_is_running_total():
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
    state = PanningState(speed=1.0)
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    dx1, dy1 = state.get_total_delta()
    dx2, dy2 = state.get_total_delta()
    assert dx1 == dx2
    assert dy1 == dy2


def test_total_delta_accumulates_multiple_moves():
    state = PanningState(speed=1.0)
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 100)
    state.update_cursor(100, 100)
    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_stop_pan_clears_total():
    state = PanningState(speed=1.0)
    state.start_pan()
    state.update_cursor(100, 100)
    state.update_cursor(110, 105)
    state.stop_pan()
    dx, dy = state.get_total_delta()
    assert dx == 0
    assert dy == 0


def test_is_dragging_property():
    state = PanningState(speed=1.0)
    assert state.is_dragging is False
    state.start_pan()
    assert state.is_dragging is True
    state.stop_pan()
    assert state.is_dragging is False


def test_max_speed_clamps_step():
    state = PanningState(speed=1.0, max_speed=10)
    state.start_pan()
    state.update_cursor(0, 0)
    state.update_cursor(100, 100)
    dx, dy = state.get_total_delta()
    assert dx == -10
    assert dy == -10


def test_max_speed_none_no_clamp():
    state = PanningState(speed=1.0, max_speed=None)
    state.start_pan()
    state.update_cursor(0, 0)
    state.update_cursor(100, 100)
    dx, dy = state.get_total_delta()
    assert dx == -100
    assert dy == -100


def test_poller_alive_initially_true():
    state = PanningState(speed=1.0)
    assert state.poller_alive is True


# --- EdgeScrollState: ground-truth geometry model ---
# Monitor 1920x1080 at (0,0). Window 500x300. Dead zone 5px default.


def _start_edge(
    es: EdgeScrollState,
    win_x: int,
    win_y: int,
    cursor_x: int | None = None,
    cursor_y: int | None = None,
) -> str:
    return es.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=win_x,
            win_y=win_y,
            win_w=500,
            win_h=300,
            cursor_x=cursor_x if cursor_x is not None else win_x + 250,
            cursor_y=cursor_y if cursor_y is not None else win_y + 150,
        )
    )


def test_edge_scroll_inactive_by_default():
    es = EdgeScrollState()
    assert es.active is False
    assert es.confirmed_drag is False


def test_edge_scroll_start():
    es = EdgeScrollState(enabled=True)
    assert _start_edge(es, 700, 390) == "EDGE_ON"
    assert es.active is True
    assert es.dragged_addr == "0xabc"
    assert es.confirmed_drag is False


def test_edge_scroll_start_disabled():
    es = EdgeScrollState(enabled=False)
    result = es.start(
        EdgeScrollParams(
            dragged_addr="0xabc",
            win_x=100,
            win_y=100,
            win_w=500,
            win_h=300,
            cursor_x=350,
            cursor_y=250,
        )
    )
    assert result == "EDGE_DISABLED"
    assert es.active is False


def test_edge_scroll_stop():
    es = EdgeScrollState(enabled=True)
    _start_edge(es, 700, 390)
    assert es.stop() == "EDGE_OFF"
    assert es.active is False
    assert es.dragged_addr == ""


def test_no_scroll_until_window_really_moves():
    """Regression for phantom camera: mouse wanders after press but the
    window geometry never changes → strictly zero scroll."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1450, 390)  # right edge exactly at monitor edge

    # any amount of cursor movement is irrelevant now — only real geometry feeds
    es.update_geometry("0xabc", 1450, 390, 500, 300, 1450 + 250, 390 + 150)  # same position
    dx, dy = es.consume_delta()
    assert (dx, dy) == (0, 0)


def test_no_scroll_below_dead_zone():
    """Sub-dead-zone jitter of the window itself must not scroll."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1450, 390)

    # moved 2px < 5px dead zone
    es.update_geometry("0xabc", 1452, 391, 500, 300, 1452 + 250, 391 + 150)
    dx, dy = es.consume_delta()
    assert (dx, dy) == (0, 0)
    assert es.confirmed_drag is False


def test_confirmed_after_dead_zone_then_proximity_applies():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1400, 390)  # right edge 20px inside

    # drag 30px right: window now crosses edge by 10px, confirmed drag
    es.update_geometry("0xabc", 1430, 390, 500, 300, 1430 + 250, 390 + 150)
    assert es.confirmed_drag is True
    dx, dy = es.consume_delta()
    assert dx < 0  # camera right → other windows left
    assert dy == 0


def test_approach_within_ramp_gives_proportional_speed():
    """Window edge inside ramp zone but not past boundary already assists."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    # grab far from edge so first update only confirms the drag
    _start_edge(es, 900, 390)
    es.update_geometry("0xabc", 950, 390, 500, 300, 950 + 250, 390 + 150)  # confirm (>5px move)

    # now push window so its right edge is 10px from boundary (dist=10 < ramp=50)
    es.update_geometry("0xabc", 1410, 390, 500, 300, 1410 + 250, 390 + 150)
    dx, _ = es.consume_delta()
    expected = -int(round(20.0 * ((50 - 10) / 50)))  # progress 0.8
    assert dx == expected


def test_past_edge_full_speed():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 900, 390)
    es.update_geometry("0xabc", 950, 390, 500, 300, 950 + 250, 390 + 150)  # confirm

    es.update_geometry(
        "0xabc", 1600, 390, 500, 300, 1600 + 250, 390 + 150
    )  # right edge 180px past
    dx, _ = es.consume_delta()
    assert dx == -20  # clamped to full speed


def test_left_edge_assists_rightward_camera():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 600, 390)
    es.update_geometry("0xabc", 650, 390, 500, 300, 650 + 250, 390 + 150)  # confirm

    es.update_geometry("0xabc", -30, 390, 500, 300, -30 + 250, 390 + 150)  # left edge 30px past
    dx, _ = es.consume_delta()
    assert dx > 0  # camera left → other windows right


def test_top_bottom_edges():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 710, 400)
    es.update_geometry("0xabc", 760, 450, 500, 300, 760 + 250, 450 + 150)  # confirm

    es.update_geometry("0xabc", 760, -40, 500, 300, 760 + 250, -40 + 150)  # top edge 40px past
    _, dy_top = es.consume_delta()

    es.update_geometry("0xabc", 760, 830, 500, 300, 760 + 250, 830 + 150)  # bottom edge 50px past
    _, dy_bot = es.consume_delta()

    assert dy_top > 0  # camera up → windows down
    assert dy_bot < 0  # camera down → windows up


def test_corner_full_speed_both_axes():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 700, 400)
    es.update_geometry("0xabc", 750, 450, 500, 300, 750 + 250, 450 + 150)

    es.update_geometry(
        "0xabc", 1700, 850, 500, 300, 1700 + 250, 850 + 150
    )  # right+bottom far past
    dx, dy = es.consume_delta()
    assert dx == -20
    assert dy == -20


def test_address_mismatch_stops_session():
    """Focus left the grabbed window mid-press → session disarms itself."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1450, 390)

    es.update_geometry("0xother", 1430, 390, 500, 300, 1430 + 250, 390 + 150)

    assert es.active is False
    assert es.dragged_addr == ""
    dx, dy = es.consume_delta()
    assert (dx, dy) == (0, 0)


def test_window_inside_screen_no_scroll_when_moved():
    """Confirmed drag but window stays well inside → no assist anywhere."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 600, 300)
    # confirmed, edges far from bounds
    es.update_geometry("0xabc", 640, 340, 500, 300, 640 + 250, 340 + 150)
    dx, dy = es.consume_delta()
    assert (dx, dy) == (0, 0)


def test_max_speed_clamps_pending():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, max_speed=5.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 900, 390)
    es.update_geometry("0xabc", 950, 390, 500, 300, 950 + 250, 390 + 150)
    es.update_geometry("0xabc", 1700, 390, 500, 300, 1700 + 250, 390 + 150)
    dx, _ = es.consume_delta()
    assert abs(dx) <= 5


def test_idle_timeout_fires_on_still_grab():
    """Press-and-hold without any movement self-cleans via idle timeout."""
    import time as time_mod

    es = EdgeScrollState(ramp_distance=50, speed=20.0)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1450, 390)
    es._last_move_time = time_mod.monotonic() - 1.0

    assert es.check_idle_timeout() is True
    assert es.active is False


# --- cursor-inside invariant ---


def test_cursor_leaving_window_disarms_session():
    """The session lives only while the pointer stays on the dragged window."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 600, 300)
    es.update_geometry("0xabc", 640, 340, 500, 300, 890, 490)  # confirm, cursor inside

    # cursor slides far off the window while geometry keeps changing
    es.update_geometry("0xabc", 700, 400, 500, 300, 1800, 900)
    assert es.active is False
    assert es.dragged_addr == ""
    dx, dy = es.consume_delta()
    assert (dx, dy) == (0, 0)


def test_cursor_inside_with_margin_keeps_session():
    es = EdgeScrollState(ramp_distance=50, speed=20.0, grab_dead_zone=5)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 1400, 390)

    # cursor slightly outside the rect but within CURSOR_MARGIN (8px)
    es.update_geometry("0xabc", 1435, 390, 500, 300, 1935 + 4, 540)  # 1939 vs edge 1935+8
    # window right edge 1935 past monitor; cursor at 1939 is inside margin zone → still armed
    assert es.active is True


def test_cursor_far_outside_before_confirmation_disarms():
    """Press armed but the very first geometry tick shows cursor elsewhere."""
    es = EdgeScrollState(ramp_distance=50, speed=20.0)
    es.set_monitor_rect(0, 0, 1920, 1080)
    _start_edge(es, 600, 300)

    es.update_geometry("0xabc", 600, 300, 500, 300, 50, 50)
    assert es.active is False
