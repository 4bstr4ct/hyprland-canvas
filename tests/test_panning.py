from canvas.panning import PanningState


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
    """When panning, cursor movement produces inverted delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.consume_delta()
    assert dx == -10
    assert dy == -5


def test_cursor_delta_applies_speed():
    """Delta is multiplied by speed."""
    state = PanningState(speed=2.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.consume_delta()
    assert dx == -20
    assert dy == -10


def test_cursor_delta_applies_invert():
    """When inverted, delta is not negated (same direction as cursor)."""
    state = PanningState(speed=1.0)
    state.inverted = True
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.consume_delta()
    assert dx == 10
    assert dy == 5


def test_no_delta_when_not_panning():
    """No delta when pan_active is False."""
    state = PanningState(speed=1.0)

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx, dy = state.consume_delta()
    assert dx == 0
    assert dy == 0


def test_consume_resets_accumulator():
    """consume_delta resets accumulator to zero."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    dx1, dy1 = state.consume_delta()
    dx2, dy2 = state.consume_delta()
    assert dx1 == -10
    assert dy1 == -5
    assert dx2 == 0
    assert dy2 == 0


def test_delta_clamped():
    """Delta is clamped to _MAX_DELTA."""
    state = PanningState(speed=100.0)
    state.start_pan()

    state.update_cursor(0, 0)
    state.update_cursor(100, 100)

    dx, dy = state.consume_delta()
    assert abs(dx) <= state._MAX_DELTA
    assert abs(dy) <= state._MAX_DELTA


def test_consume_zeros_overshoot():
    """consume_delta zeros accumulator completely, no catch-up after clamping."""
    state = PanningState(speed=10.0)
    state.start_pan()

    state.update_cursor(0, 0)
    state.update_cursor(100, 0)  # acc = -1000, clamped to -50

    dx1, dy1 = state.consume_delta()
    assert dx1 == -state._MAX_DELTA  # clamped
    dx2, dy2 = state.consume_delta()
    assert dx2 == 0  # no leftover "catch-up"


def test_stop_pan_clears_accumulator():
    """stop_pan clears any accumulated delta."""
    state = PanningState(speed=1.0)
    state.start_pan()

    state.update_cursor(100, 100)
    state.update_cursor(110, 105)

    state.stop_pan()
    dx, dy = state.consume_delta()
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
