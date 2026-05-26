"""Panning via Hyprland cursor position polling.

Architecture:
- Poll cursor position via Hyprland IPC (canvas.hypr module)
- When pan_active, accumulate total delta from cursor movement
- Daemon uses absolute positioning (baseline + total_delta) to avoid drift
- PAN_START/PAN_STOP controlled by Hyprland keybinds
- Edge-scroll: when dragging a window near screen edge, camera follows
  the window by moving all OTHER floating windows in the opposite direction
"""

import logging
import threading
import time

from canvas import hypr

log = logging.getLogger("canvas.panning")


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
    """Thread-safe edge-scroll state.

    When a window is dragged near a monitor edge, camera follows by
    moving all OTHER floating windows in the opposite direction.
    The dragged window stays under cursor control (Hyprland drag).

    Speed increases quadratically as cursor approaches the edge.
    """

    def __init__(
        self,
        threshold: int = 50,
        speed: float = 20.0,
        enabled: bool = True,
    ) -> None:
        self.threshold = threshold
        self.speed = speed
        self.enabled = enabled
        self._active = False
        self._dragged_addr: str = ""
        self._monitor_x = 0
        self._monitor_y = 0
        self._monitor_w = 1920
        self._monitor_h = 1080
        self._cursor_x = 0
        self._cursor_y = 0
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

    def start(self, dragged_addr: str) -> str:
        """Activate edge-scroll, remembering which window is dragged."""
        with self._lock:
            if not self.enabled:
                return "EDGE_DISABLED"
            self._active = True
            self._dragged_addr = dragged_addr
            return "EDGE_ON"

    def stop(self) -> str:
        """Deactivate edge-scroll."""
        with self._lock:
            self._active = False
            self._dragged_addr = ""
            return "EDGE_OFF"

    def get_cursor_pos(self) -> tuple[int, int]:
        """Return last known cursor position (from cursor_poller)."""
        with self._lock:
            return self._cursor_x, self._cursor_y

    def compute_scroll(self, cursor_x: int, cursor_y: int) -> tuple[int, int]:
        """Calculate scroll offset (dx, dy) based on cursor position.

        Returns (0, 0) if cursor not in edge zone or edge-scroll inactive.
        Direction: cursor at right edge → positive dx (scroll right/camera right).
        Speed: quadratic ramp, speed * progress² at edge.
        """
        with self._lock:
            if not self._active or not self.enabled:
                return 0, 0

            dx = 0
            dy = 0
            mx = self._monitor_x
            my = self._monitor_y
            mw = self._monitor_w
            mh = self._monitor_h
            th = self.threshold

            dist_left = cursor_x - mx
            dist_right = (mx + mw) - cursor_x
            dist_top = cursor_y - my
            dist_bottom = (my + mh) - cursor_y

            if dist_left < th:
                progress = 1.0 - dist_left / th
                dx = -int(self.speed * progress * progress)
            elif dist_right < th:
                progress = 1.0 - dist_right / th
                dx = int(self.speed * progress * progress)

            if dist_top < th:
                progress = 1.0 - dist_top / th
                dy = -int(self.speed * progress * progress)
            elif dist_bottom < th:
                progress = 1.0 - dist_bottom / th
                dy = int(self.speed * progress * progress)

            return dx, dy


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
            with edge_scroll._lock:
                edge_scroll._cursor_x = x
                edge_scroll._cursor_y = y
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

