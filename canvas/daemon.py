"""Canvas daemon — main loop wiring all modules together."""

import json
import logging
import threading
import time

from canvas.config import load
from canvas.hypr import eval_lua, send
from canvas.ipc import IpcServer
from canvas.navigation import Navigator
from canvas.panning import PanningState, cursor_poller

log = logging.getLogger("canvas")


def _fetch_floating_baselines() -> dict[str, tuple[int, int]]:
    """Fetch current positions of all floating windows as baseline.

    Returns {address: (x, y)} for absolute positioning during pan.
    """
    try:
        resp = send("j/clients")
        clients = json.loads(resp)
        baselines = {}
        for w in clients:
            if w.get("floating"):
                addr = w.get("address", "")
                at = w.get("at", [0, 0])
                if addr and len(at) >= 2:
                    baselines[addr] = (at[0], at[1])
        return baselines
    except Exception as e:
        log.warning("fetch baselines failed: %s", e)
        return {}


def run() -> None:
    """Main entry point for the canvas daemon."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    cfg = load()
    log.info("loading config...")

    # --- Initialize modules ---
    state = PanningState(speed=cfg["speed"], max_speed=cfg.get("max_speed"))
    state.inverted = cfg["invert"]["enabled"]

    navigator = Navigator(
        protected_apps=cfg["navigation"]["protected_apps"],
        cooldown=cfg["navigation"]["cooldown"],
    )

    baselines: dict[str, tuple[int, int]] = {}

    # --- IPC handler ---
    def handle_ipc(cmd: str) -> str:
        nonlocal baselines
        if cmd == "PAN_START":
            baselines = _fetch_floating_baselines()
            return state.start_pan()
        elif cmd == "PAN_STOP":
            baselines = {}
            state.stop_pan()
            return "PAN_OFF"
        elif cmd == "NAV_LEFT":
            navigator.navigate("left")
            return "OK"
        elif cmd == "NAV_RIGHT":
            navigator.navigate("right")
            return "OK"
        elif cmd == "TOGGLE":
            state.inverted = not state.inverted
            return "INVERTED" if state.inverted else "NORMAL"
        elif cmd == "CANVAS_TOGGLE":
            return navigator.canvas_toggle()
        elif cmd == "PING":
            return "PONG"
        elif cmd == "STATUS":
            inv = "INVERTED" if state.inverted else "NORMAL"
            pan = "PANNING" if state.is_dragging else "IDLE"
            return f"{inv} {pan}"
        else:
            return f"UNKNOWN: {cmd}"

    ipc = IpcServer(handler=handle_ipc)

    # --- Start threads ---
    stop_event = threading.Event()

    ipc_thread = threading.Thread(target=ipc.serve, daemon=True)
    ipc_thread.start()

    cursor_thread = threading.Thread(target=cursor_poller, args=(state, stop_event), daemon=True)
    cursor_thread.start()

    log.info("ready — SUPER+SHIFT+LMB to pan, release to stop")

    # --- Main loop (~60 FPS) ---
    try:
        prev_total = (0, 0)
        while True:
            time.sleep(0.016)

            # Auto-stop panning when cursor idle (fallback for unreliable release binds)
            state.check_idle_timeout()

            if not state.pan_active:
                prev_total = (0, 0)
                continue

            total_dx, total_dy = state.get_total_delta()
            if total_dx == 0 and total_dy == 0:
                continue
            if (total_dx, total_dy) == prev_total:
                continue
            prev_total = (total_dx, total_dy)

            # Move all floating windows to absolute positions from baseline.
            # Each frame independently computes position = baseline + total_delta,
            # so rounding errors from previous frames do NOT accumulate.
            try:
                lines = ["local ws = hl.get_windows({ floating = true })"]
                lines.append("local bd = {")
                for addr, (bx, by) in baselines.items():
                    lines.append(f'  ["{addr}"] = {{{bx}, {by}}},')
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
                eval_lua("\n".join(lines))
            except Exception as e:
                log.warning("window move failed: %s", e)

    except KeyboardInterrupt:
        log.info("shutting down...")
        stop_event.set()
        ipc.stop()
        cursor_thread.join(timeout=1)
        ipc_thread.join(timeout=1)
