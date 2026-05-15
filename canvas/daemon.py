"""Canvas daemon — main loop wiring all modules together."""

import logging
import threading
import time

from canvas.config import load
from canvas.hypr import eval_lua
from canvas.ipc import IpcServer
from canvas.navigation import Navigator
from canvas.panning import PanningState, cursor_poller

log = logging.getLogger("canvas")


def run() -> None:
    """Main entry point for the canvas daemon."""
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    cfg = load()
    log.info("loading config...")

    # --- Initialize modules ---
    state = PanningState(speed=cfg["speed"])
    state.inverted = cfg["invert"]["enabled"]

    navigator = Navigator(
        protected_apps=cfg["navigation"]["protected_apps"],
        cooldown=cfg["navigation"]["cooldown"],
    )

    # --- IPC handler ---
    def handle_ipc(cmd: str) -> str:
        if cmd == "PAN_START":
            return state.start_pan()
        elif cmd == "PAN_STOP":
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
        while True:
            time.sleep(0.016)

            # Auto-stop panning when cursor idle (fallback for unreliable release binds)
            state.check_idle_timeout()

            dx, dy = state.consume_delta()
            if dx == 0 and dy == 0:
                continue

            # Move all floating windows WITHOUT focusing them.
            # window.move({window=obj}) works with get_windows objects.
            # No focus = no cursor warp = no feedback loop = no flicker.
            try:
                lua = f"""
local ws = hl.get_windows({{ floating = true }})
for _, w in ipairs(ws) do
    hl.dispatch(hl.dsp.window.move({{ x = {dx}, y = {dy}, relative = true, window = w }}))
end
"""
                eval_lua(lua)
            except Exception as e:
                log.warning("window move failed: %s", e)

    except KeyboardInterrupt:
        log.info("shutting down...")
        stop_event.set()
        ipc.stop()
        cursor_thread.join(timeout=1)
        ipc_thread.join(timeout=1)
