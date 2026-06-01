"""Canvas daemon — main loop wiring all modules together."""

import json
import logging
import re
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from canvas.config import load
from canvas.hypr import HyprIPC, get_cursor_pos
from canvas.ipc import IpcServer
from canvas.navigation import Navigator
from canvas.panning import EdgeScrollParams, EdgeScrollState, PanningState, cursor_poller

log = logging.getLogger("canvas")

_VALID_ADDR = re.compile(r"^0x[0-9a-fA-F]+$")


def _lua_escape(s: str) -> str:
    """Escape a string for safe interpolation into a Lua double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


class DaemonState:
    """Encapsulates daemon runtime state — replaces closure + nonlocal.

    Makes state testable via dependency injection (mock HyprIPC, etc).
    """

    def __init__(
        self,
        panning: PanningState,
        edge_scroll: EdgeScrollState,
        navigator: Navigator,
        ipc: HyprIPC,
    ) -> None:
        self.panning = panning
        self.edge_scroll = edge_scroll
        self.navigator = navigator
        self.ipc = ipc
        self.baselines: dict[str, tuple[int, int]] = {}

    def _fetch_focused_window(self) -> dict[str, Any]:
        """Get focused window info: address, position, size."""
        try:
            resp = self.ipc.send("j/activewindow")
            w: dict[str, Any] = json.loads(resp)
            return w
        except Exception:
            return {}

    def _fetch_monitor_rect(self) -> None:
        """Fetch focused monitor geometry for edge-scroll."""
        try:
            resp = self.ipc.send("j/monitors")
            monitors: list[dict[str, Any]] = json.loads(resp)
            for m in monitors:
                if m.get("focused", False):
                    self.edge_scroll.set_monitor_rect(
                        m.get("x", 0),
                        m.get("y", 0),
                        m.get("width", 1920),
                        m.get("height", 1080),
                    )
                    return
            if monitors:
                m = monitors[0]
                self.edge_scroll.set_monitor_rect(
                    m.get("x", 0),
                    m.get("y", 0),
                    m.get("width", 1920),
                    m.get("height", 1080),
                )
        except Exception as e:
            log.debug("fetch monitor rect failed: %s", e)

    _IPC_DISPATCH: dict[str, str] = {
        "PAN_START": "_handle_pan_start",
        "PAN_STOP": "_handle_pan_stop",
        "NAV_LEFT": "_handle_nav_left",
        "NAV_RIGHT": "_handle_nav_right",
        "EDGE_START": "_handle_edge_start",
        "EDGE_STOP": "_handle_edge_stop",
        "TOGGLE": "_handle_toggle",
        "CANVAS_TOGGLE": "_handle_canvas_toggle",
        "PING": "_handle_ping",
        "STATUS": "_handle_status",
    }

    def handle_ipc(self, cmd: str) -> str:
        """Process an IPC command, return response string."""
        handler_name = self._IPC_DISPATCH.get(cmd)
        if handler_name is not None:
            handler: Callable[[], str] = getattr(self, handler_name)
            return handler()
        return f"UNKNOWN: {cmd}"

    def _handle_pan_start(self) -> str:
        self.fetch_baselines()
        return self.panning.start_pan()

    def _handle_pan_stop(self) -> str:
        self.panning.stop_pan()
        self.baselines = {}
        return "PAN_OFF"

    def _handle_nav_left(self) -> str:
        self.navigator.navigate("left")
        return "OK"

    def _handle_nav_right(self) -> str:
        self.navigator.navigate("right")
        return "OK"

    def _handle_edge_start(self) -> str:
        win = self._fetch_focused_window()
        addr = str(win.get("address", ""))
        if not addr:
            return "EDGE_NO_WINDOW"
        at = win.get("at", [0, 0])
        size = win.get("size", [0, 0])
        try:
            cx, cy = get_cursor_pos()
        except Exception:
            return "EDGE_NO_CURSOR"
        self._fetch_monitor_rect()
        return self.edge_scroll.start(
            EdgeScrollParams(
                dragged_addr=addr,
                win_x=at[0] if len(at) >= 2 else 0,
                win_y=at[1] if len(at) >= 2 else 0,
                win_w=size[0] if len(size) >= 2 else 0,
                win_h=size[1] if len(size) >= 2 else 0,
                cursor_x=cx,
                cursor_y=cy,
            )
        )

    def _handle_edge_stop(self) -> str:
        return self.edge_scroll.stop()

    def _handle_toggle(self) -> str:
        self.panning.inverted = not self.panning.inverted
        return "INVERTED" if self.panning.inverted else "NORMAL"

    def _handle_canvas_toggle(self) -> str:
        return self.navigator.canvas_toggle()

    def _handle_ping(self) -> str:
        return "PONG"

    def _handle_status(self) -> str:
        inv = "INVERTED" if self.panning.inverted else "NORMAL"
        pan = "PANNING" if self.panning.is_dragging else "IDLE"
        return f"{inv} {pan}"

    def fetch_baselines(self) -> None:
        """Fetch current positions of all floating windows as baseline."""
        try:
            resp = self.ipc.send("j/clients")
            clients = json.loads(resp)
            baselines: dict[str, tuple[int, int]] = {}
            for w in clients:
                if w.get("floating"):
                    addr = w.get("address", "")
                    at = w.get("at", [0, 0])
                    if addr and len(at) >= 2:
                        baselines[addr] = (at[0], at[1])
            self.baselines = baselines
        except Exception as e:
            log.warning("fetch baselines failed: %s", e)
            self.baselines = {}

    def restore_baselines(self) -> None:
        """Move all floating windows back to their pre-pan positions.

        Called on graceful shutdown so windows don't stay displaced.
        """
        if not self.baselines:
            return
        try:
            lines = ["local ws = hl.get_windows({ floating = true })"]
            lines.append("local bd = {")
            for addr, (bx, by) in self.baselines.items():
                safe_addr = _lua_escape(addr)
                lines.append(f'  ["{safe_addr}"] = {{{bx}, {by}}},')
            lines.append("}")
            lines.append("for _, w in ipairs(ws) do")
            lines.append("  local b = bd[tostring(w.address)]")
            lines.append("  if b then")
            lines.append(
                "    hl.dispatch(hl.dsp.window.move({"
                " x = b[1], y = b[2],"
                " relative = false, window = w }))"
            )
            lines.append("  end")
            lines.append("end")
            self.ipc.eval_lua("\n".join(lines))
            log.info("restored %d windows to pre-pan positions", len(self.baselines))
        except Exception as e:
            log.warning("restore baselines failed: %s", e)

    def move_windows_to_delta(self, total_dx: int, total_dy: int) -> None:
        """Move all floating windows to baseline + total_delta (absolute positioning)."""
        try:
            lines = ["local ws = hl.get_windows({ floating = true })"]
            lines.append("local bd = {")
            for addr, (bx, by) in self.baselines.items():
                safe_addr = _lua_escape(addr)
                lines.append(f'  ["{safe_addr}"] = {{{bx}, {by}}},')
            lines.append("}")
            lines.append("for _, w in ipairs(ws) do")
            lines.append("  local b = bd[tostring(w.address)]")
            lines.append("  if b then")
            lines.append(
                f"    hl.dispatch(hl.dsp.window.move({{"
                f" x = b[1] + {total_dx},"
                f" y = b[2] + {total_dy},"
                f" relative = false, window = w }}))"
            )
            lines.append("  end")
            lines.append("end")
            self.ipc.eval_lua("\n".join(lines))
        except Exception as e:
            log.warning("window move failed: %s", e)

    def edge_scroll_move(self, dx: int, dy: int) -> None:
        """Move all floating windows EXCEPT the dragged one by (dx, dy) relative.

        Camera follows the dragged window: other windows move opposite.
        Cursor at right edge → camera right → other windows move left.
        """
        if dx == 0 and dy == 0:
            return
        dragged = self.edge_scroll.dragged_addr
        try:
            safe_addr = _lua_escape(dragged)
            lua = (
                f"local ws = hl.get_windows({{ floating = true }})\n"
                f"for _, w in ipairs(ws) do\n"
                f'  if tostring(w.address) ~= "{safe_addr}" then\n'
                f"    hl.dispatch(hl.dsp.window.move({{"
                f" x = {dx}, y = {dy},"
                f" relative = true, window = w }}))\n"
                f"  end\n"
                f"end\n"
            )
            self.ipc.eval_lua(lua)
        except Exception as e:
            log.warning("edge-scroll move failed: %s", e)


def run() -> None:
    """Main entry point for the canvas daemon."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    cfg = load()
    log.info("loading config...")

    ipc = HyprIPC.from_env()
    state = PanningState(speed=cfg["speed"], max_speed=cfg.get("max_speed"))
    state.inverted = cfg["invert"]["enabled"]

    edge_cfg = cfg.get("edge_scroll", {})
    edge_scroll = EdgeScrollState(
        ramp_distance=edge_cfg.get("ramp_distance", 50),
        speed=edge_cfg.get("speed", 20.0),
        max_speed=edge_cfg.get("max_speed"),
        enabled=edge_cfg.get("enabled", True),
    )

    navigator = Navigator(
        ipc=ipc,
        protected_apps=cfg["navigation"]["protected_apps"],
        cooldown=cfg["navigation"]["cooldown"],
    )

    daemon_state = DaemonState(
        panning=state, edge_scroll=edge_scroll, navigator=navigator, ipc=ipc
    )

    ipc_server = IpcServer(handler=daemon_state.handle_ipc)

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)

    ipc_thread = threading.Thread(target=ipc_server.serve, daemon=True)
    ipc_thread.start()

    cursor_thread = threading.Thread(
        target=cursor_poller, args=(state, edge_scroll, stop_event), daemon=True
    )
    cursor_thread.start()

    log.info("ready — SUPER+SHIFT+LMB to pan, SUPER+LMB to edge-scroll")

    target_interval = 1.0 / 60.0
    prev_time = time.monotonic()

    try:
        prev_total = (0, 0)
        while not stop_event.is_set():
            state.check_idle_timeout()

            if not state.pan_active:
                prev_total = (0, 0)
            else:
                total_dx, total_dy = state.get_total_delta()
                if (total_dx, total_dy) != (0, 0) and (total_dx, total_dy) != prev_total:
                    prev_total = (total_dx, total_dy)
                    try:
                        daemon_state.move_windows_to_delta(total_dx, total_dy)
                    except Exception as e:
                        log.warning("window move failed: %s", e)

            edge_scroll.check_idle_timeout()
            if edge_scroll.active:
                es_dx, es_dy = edge_scroll.consume_delta()
                if es_dx != 0 or es_dy != 0:
                    daemon_state.edge_scroll_move(es_dx, es_dy)

            now = time.monotonic()
            elapsed = now - prev_time
            time.sleep(max(0, target_interval - elapsed))
            prev_time = time.monotonic()

    except KeyboardInterrupt as exc:
        log.info("shutting down: %s", exc)

    if not state.poller_alive:
        log.error("cursor poller died — cannot track cursor position")

    stop_event.set()
    daemon_state.restore_baselines()
    ipc_server.stop()
    cursor_thread.join(timeout=1)
    ipc_thread.join(timeout=1)
