"""Canvas daemon — main loop wiring all modules together."""

import json
import logging
import re
import signal
import threading
import time

from canvas.config import load
from canvas.hypr import HyprIPC
from canvas.ipc import IpcServer
from canvas.navigation import Navigator
from canvas.panning import PanningState, cursor_poller

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
        navigator: Navigator,
        ipc: HyprIPC,
    ) -> None:
        self.panning = panning
        self.navigator = navigator
        self.ipc = ipc
        self.baselines: dict[str, tuple[int, int]] = {}

    def handle_ipc(self, cmd: str) -> str:
        """Process an IPC command, return response string."""
        if cmd == "PAN_START":
            self.fetch_baselines()
            return self.panning.start_pan()
        elif cmd == "PAN_STOP":
            self.panning.stop_pan()
            self.baselines = {}
            return "PAN_OFF"
        elif cmd == "NAV_LEFT":
            self.navigator.navigate("left")
            return "OK"
        elif cmd == "NAV_RIGHT":
            self.navigator.navigate("right")
            return "OK"
        elif cmd == "TOGGLE":
            self.panning.inverted = not self.panning.inverted
            return "INVERTED" if self.panning.inverted else "NORMAL"
        elif cmd == "CANVAS_TOGGLE":
            return self.navigator.canvas_toggle()
        elif cmd == "PING":
            return "PONG"
        elif cmd == "STATUS":
            inv = "INVERTED" if self.panning.inverted else "NORMAL"
            pan = "PANNING" if self.panning.is_dragging else "IDLE"
            return f"{inv} {pan}"
        else:
            return f"UNKNOWN: {cmd}"

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


def run() -> None:
    """Main entry point for the canvas daemon."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    cfg = load()
    log.info("loading config...")

    ipc = HyprIPC.from_env()
    state = PanningState(speed=cfg["speed"], max_speed=cfg.get("max_speed"))
    state.inverted = cfg["invert"]["enabled"]

    navigator = Navigator(
        protected_apps=cfg["navigation"]["protected_apps"],
        cooldown=cfg["navigation"]["cooldown"],
    )

    daemon_state = DaemonState(panning=state, navigator=navigator, ipc=ipc)

    ipc_server = IpcServer(handler=daemon_state.handle_ipc)

    stop_event = threading.Event()

    def _shutdown(signum: int, _frame: object) -> None:
        log.info("received signal %d, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)

    ipc_thread = threading.Thread(target=ipc_server.serve, daemon=True)
    ipc_thread.start()

    cursor_thread = threading.Thread(target=cursor_poller, args=(state, stop_event), daemon=True)
    cursor_thread.start()

    log.info("ready — SUPER+SHIFT+LMB to pan, release to stop")

    target_interval = 1.0 / 60.0
    prev_time = time.monotonic()

    try:
        prev_total = (0, 0)
        while not stop_event.is_set():
            state.check_idle_timeout()

            if not state.pan_active:
                prev_total = (0, 0)
                now = time.monotonic()
                elapsed = now - prev_time
                time.sleep(max(0, target_interval - elapsed))
                prev_time = time.monotonic()
                continue

            total_dx, total_dy = state.get_total_delta()
            if total_dx == 0 and total_dy == 0:
                now = time.monotonic()
                elapsed = now - prev_time
                time.sleep(max(0, target_interval - elapsed))
                prev_time = time.monotonic()
                continue
            if (total_dx, total_dy) == prev_total:
                now = time.monotonic()
                elapsed = now - prev_time
                time.sleep(max(0, target_interval - elapsed))
                prev_time = time.monotonic()
                continue
            prev_total = (total_dx, total_dy)

            try:
                daemon_state.move_windows_to_delta(total_dx, total_dy)
            except Exception as e:
                log.warning("window move failed: %s", e)

            now = time.monotonic()
            elapsed = now - prev_time
            time.sleep(max(0, target_interval - elapsed))
            prev_time = time.monotonic()

    except KeyboardInterrupt:
        log.info("shutting down...")

    if not state.poller_alive:
        log.error("cursor poller died — cannot track cursor position")

    stop_event.set()
    daemon_state.restore_baselines()
    ipc_server.stop()
    cursor_thread.join(timeout=1)
    ipc_thread.join(timeout=1)
