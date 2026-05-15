"""Panning via Hyprland cursor position polling.

Architecture:
- Poll cursor position via Hyprland IPC (canvas.hypr module)
- When pan_active, compute inverse delta and apply to floating windows
- Suppress + set_baseline prevent feedback loop during window moves
- PAN_START/PAN_STOP controlled by Hyprland keybinds
"""

import logging
import threading
import time

from canvas import hypr

log = logging.getLogger("canvas.panning")


class PanningState:
    """Thread-safe panning state with cursor delta tracking."""

    _MAX_DELTA = 50
    _IDLE_TIMEOUT = 0.5  # auto-stop after 500ms without cursor movement

    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self._pan_active = False
        self._inverted = False

        self._prev_x: int | None = None
        self._prev_y: int | None = None
        self._acc_x = 0.0
        self._acc_y = 0.0
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
            self._acc_x = 0.0
            self._acc_y = 0.0
            self._last_move_time = time.monotonic()
            return "PAN_ON"

    def stop_pan(self) -> None:
        """Force-stop panning mode."""
        with self._lock:
            self._pan_active = False
            self._prev_x = None
            self._prev_y = None
            self._acc_x = 0.0
            self._acc_y = 0.0

    def update_cursor(self, x: int, y: int) -> None:
        """Feed cursor position. If panning, accumulate inverse delta."""
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
                    self._acc_x += dx * self.speed * sign
                    self._acc_y += dy * self.speed * sign
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
                self._acc_x = 0.0
                self._acc_y = 0.0
                return True
            return False

    def consume_delta(self) -> tuple[int, int]:
        """Return accumulated (dx, dy), clamped. Resets accumulator."""
        with self._lock:
            dx = int(round(self._acc_x))
            dy = int(round(self._acc_y))
            dx = max(-self._MAX_DELTA, min(self._MAX_DELTA, dx))
            dy = max(-self._MAX_DELTA, min(self._MAX_DELTA, dy))
            # Zero out completely — no "catch-up" after release
            self._acc_x = 0.0
            self._acc_y = 0.0
            return dx, dy


def cursor_poller(state: PanningState, stop_event: threading.Event) -> None:
    """Poll cursor position at ~60Hz, feed to PanningState."""
    err_count = 0
    while not stop_event.is_set():
        try:
            x, y = hypr.get_cursor_pos()
            state.update_cursor(x, y)
            err_count = 0
        except Exception as e:
            err_count += 1
            if err_count <= 3:
                log.warning("cursor poll error #%d: %s", err_count, e)
            if err_count > 10:
                log.error("too many cursor poll failures, stopping poller")
                return
        stop_event.wait(0.016)
