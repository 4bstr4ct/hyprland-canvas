"""Panning via Hyprland cursor position polling.

Architecture:
- Poll cursor position via Hyprland IPC (canvas.hypr module)
- When pan_active, accumulate total delta from cursor movement
- Daemon uses absolute positioning (baseline + total_delta) to avoid drift
- PAN_START/PAN_STOP controlled by Hyprland keybinds
- Edge-scroll: when a dragged window's edge goes PAST the monitor edge,
  camera follows by moving all OTHER floating windows opposite.
  Only triggers on OVERFLOW — how much further the window went beyond
  its initial position relative to the edge. This prevents accidental
  scroll when grabbing a window that was already partially off-screen.
"""

import logging
import threading
import time
from dataclasses import dataclass

from canvas import hypr

log = logging.getLogger("canvas.panning")


@dataclass
class EdgeScrollParams:
    dragged_addr: str
    win_x: int
    win_y: int
    win_w: int
    win_h: int
    cursor_x: int
    cursor_y: int


class PanningState:
    """Thread-safe panning state with total delta tracking.

    Instead of consuming incremental deltas (which accumulate rounding
    errors), we track the total offset from the start position. The
    daemon uses this with baseline window positions for absolute moves,
    eliminating drift.
    """

    _IDLE_TIMEOUT = 0.5  # auto-stop after 500ms without cursor movement

    def __init__(self, speed: float = 1.0, max_speed: float | None = None):
        self.speed = speed
        self.max_speed = max_speed
        self._pan_active = False
        self._inverted = False
        self.poller_alive = True

        self._prev_x: int | None = None
        self._prev_y: int | None = None
        self._total_dx = 0.0
        self._total_dy = 0.0
        self._last_move_time: float = 0.0

        self._lock = threading.Lock()

    @property
    def pan_active(self) -> bool:
        with self._lock:
            return self._pan_active

    @property
    def inverted(self) -> bool:
        with self._lock:
            return self._inverted

    @inverted.setter
    def inverted(self, value: bool) -> None:
        with self._lock:
            self._inverted = value

    @property
    def is_dragging(self) -> bool:
        with self._lock:
            return self._pan_active

    def start_pan(self) -> str:
        """Activate panning mode."""
        with self._lock:
            self._pan_active = True
            self._prev_x = None
            self._prev_y = None
            self._total_dx = 0.0
            self._total_dy = 0.0
            self._last_move_time = time.monotonic()
            return "PAN_ON"

    def stop_pan(self) -> None:
        """Force-stop panning mode."""
        with self._lock:
            self._pan_active = False
            self._prev_x = None
            self._prev_y = None
            self._total_dx = 0.0
            self._total_dy = 0.0

    def update_cursor(self, x: int, y: int) -> None:
        """Feed cursor position. If panning, accumulate total delta."""
        with self._lock:
            if not self._pan_active:
                self._prev_x = x
                self._prev_y = y
                return

            if self._prev_x is not None and self._prev_y is not None:
                dx = x - self._prev_x
                dy = y - self._prev_y
                if dx != 0 or dy != 0:
                    sign = 1 if self._inverted else -1
                    step_x = dx * self.speed * sign
                    step_y = dy * self.speed * sign
                    if self.max_speed is not None:
                        step_x = max(-self.max_speed, min(self.max_speed, step_x))
                        step_y = max(-self.max_speed, min(self.max_speed, step_y))
                    self._total_dx += step_x
                    self._total_dy += step_y
                    self._last_move_time = time.monotonic()

            self._prev_x = x
            self._prev_y = y

    def check_idle_timeout(self) -> bool:
        """Auto-stop if cursor hasn't moved for _IDLE_TIMEOUT (release bind fallback)."""
        with self._lock:
            if self._pan_active and (time.monotonic() - self._last_move_time) > self._IDLE_TIMEOUT:
                self._pan_active = False
                self._prev_x = None
                self._prev_y = None
                self._total_dx = 0.0
                self._total_dy = 0.0
                return True
            return False

    def get_total_delta(self) -> tuple[int, int]:
        """Return total (dx, dy) offset from pan start. Does NOT reset.

        Each call returns the same value until more cursor movement
        happens. The daemon uses this with baseline positions for
        absolute moves, so drift cannot accumulate.
        """
        with self._lock:
            return int(round(self._total_dx)), int(round(self._total_dy))


class EdgeScrollState:
    """Thread-safe edge-scroll state with overflow-based activation.

    Scroll triggers only when a window edge goes PAST the monitor edge
    AND has moved further out than it was at drag start (overflow > 0).

    This prevents accidental scrolling when:
    - Grabbing a window that was already partially off-screen
    - The window is merely near the edge but hasn't crossed it
    - Dragging a window back toward the screen from off-screen

    Speed ramps linearly: progress = min(overflow / ramp_distance, 1.0).
    Direction: overflow on right → camera right → other windows move left.
    """

    _IDLE_TIMEOUT = 0.5

    def __init__(
        self,
        ramp_distance: int = 50,
        speed: float = 20.0,
        max_speed: float | None = None,
        enabled: bool = True,
    ) -> None:
        self.ramp_distance = ramp_distance
        self.speed = speed
        self.max_speed = max_speed
        self.enabled = enabled
        self._active = False
        self._dragged_addr: str = ""
        self._monitor_x = 0
        self._monitor_y = 0
        self._monitor_w = 1920
        self._monitor_h = 1080
        self._win_w = 0
        self._win_h = 0
        self._offset_x = 0
        self._offset_y = 0
        self._initial_dist_left = 0
        self._initial_dist_right = 0
        self._initial_dist_top = 0
        self._initial_dist_bottom = 0
        self._last_move_time: float = 0.0
        self._pending_dx = 0.0
        self._pending_dy = 0.0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def dragged_addr(self) -> str:
        with self._lock:
            return self._dragged_addr

    def set_monitor_rect(self, x: int, y: int, w: int, h: int) -> None:
        with self._lock:
            self._monitor_x = x
            self._monitor_y = y
            self._monitor_w = w
            self._monitor_h = h

    def start(self, params: EdgeScrollParams) -> str:
        """Activate edge-scroll, storing window geometry, cursor offset, and initial distances."""
        with self._lock:
            if not self.enabled:
                return "EDGE_DISABLED"
            self._active = True
            self._dragged_addr = params.dragged_addr
            self._win_w = params.win_w
            self._win_h = params.win_h
            self._offset_x = params.cursor_x - params.win_x
            self._offset_y = params.cursor_y - params.win_y

            mx = self._monitor_x
            my = self._monitor_y
            mw = self._monitor_w
            mh = self._monitor_h

            self._initial_dist_left = params.win_x - mx
            self._initial_dist_right = (mx + mw) - (params.win_x + params.win_w)
            self._initial_dist_top = params.win_y - my
            self._initial_dist_bottom = (my + mh) - (params.win_y + params.win_h)

            self._pending_dx = 0.0
            self._pending_dy = 0.0
            self._last_move_time = time.monotonic()
            return "EDGE_ON"

    def stop(self) -> str:
        """Deactivate edge-scroll."""
        with self._lock:
            self._active = False
            self._dragged_addr = ""
            self._pending_dx = 0.0
            self._pending_dy = 0.0
            return "EDGE_OFF"

    def update_cursor(self, cursor_x: int, cursor_y: int) -> None:
        """Feed cursor position from cursor_poller.

        Derives window position, checks overflow past monitor edges.
        Only scrolls when window edge is past monitor AND overflow > 0.
        """
        with self._lock:
            if not self._active or not self.enabled:
                return

            win_x = cursor_x - self._offset_x
            win_y = cursor_y - self._offset_y
            win_right = win_x + self._win_w
            win_bottom = win_y + self._win_h

            mx = self._monitor_x
            my = self._monitor_y
            mw = self._monitor_w
            mh = self._monitor_h
            rd = self.ramp_distance

            cur_dist_left = win_x - mx
            cur_dist_right = (mx + mw) - win_right
            cur_dist_top = win_y - my
            cur_dist_bottom = (my + mh) - win_bottom

            # Left edge: window goes past monitor left
            if cur_dist_left < 0:
                overflow = self._initial_dist_left - cur_dist_left
                if overflow > 0:
                    progress = min(overflow / rd, 1.0)
                    self._pending_dx -= self.speed * progress

            # Right edge: window goes past monitor right
            elif cur_dist_right < 0:
                overflow = self._initial_dist_right - cur_dist_right
                if overflow > 0:
                    progress = min(overflow / rd, 1.0)
                    self._pending_dx += self.speed * progress

            # Top edge: window goes past monitor top
            if cur_dist_top < 0:
                overflow = self._initial_dist_top - cur_dist_top
                if overflow > 0:
                    progress = min(overflow / rd, 1.0)
                    self._pending_dy -= self.speed * progress

            # Bottom edge: window goes past monitor bottom
            elif cur_dist_bottom < 0:
                overflow = self._initial_dist_bottom - cur_dist_bottom
                if overflow > 0:
                    progress = min(overflow / rd, 1.0)
                    self._pending_dy += self.speed * progress

            if self._pending_dx != 0 or self._pending_dy != 0:
                self._last_move_time = time.monotonic()

    def consume_delta(self) -> tuple[int, int]:
        """Return and clear pending (dx, dy) for moving other windows.

        Returns inverted values: right overflow → camera right → other
        windows move LEFT (negative dx). Same for vertical.
        Clamps by max_speed if set.
        """
        with self._lock:
            if not self._active:
                return 0, 0
            dx = self._pending_dx
            dy = self._pending_dy
            self._pending_dx = 0.0
            self._pending_dy = 0.0
            if self.max_speed is not None:
                dx = max(-self.max_speed, min(self.max_speed, dx))
                dy = max(-self.max_speed, min(self.max_speed, dy))
            return -int(round(dx)), -int(round(dy))

    def check_idle_timeout(self) -> bool:
        """Auto-stop if no edge activity for _IDLE_TIMEOUT."""
        with self._lock:
            if self._active and (time.monotonic() - self._last_move_time) > self._IDLE_TIMEOUT:
                self._active = False
                self._dragged_addr = ""
                self._pending_dx = 0.0
                self._pending_dy = 0.0
                return True
            return False


def cursor_poller(
    state: PanningState,
    edge_scroll: EdgeScrollState,
    stop_event: threading.Event,
) -> None:
    """Poll cursor position at ~60Hz, feed to PanningState and EdgeScrollState."""
    err_count = 0
    while not stop_event.is_set():
        try:
            x, y = hypr.get_cursor_pos()
            state.update_cursor(x, y)
            edge_scroll.update_cursor(x, y)
            err_count = 0
        except Exception as e:
            err_count += 1
            if err_count <= 3:
                log.warning("cursor poll error #%d: %s", err_count, e)
            if err_count > 10:
                log.error("too many cursor poll failures, stopping poller")
                state.poller_alive = False
                stop_event.set()
                return
        stop_event.wait(0.016)
