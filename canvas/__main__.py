"""Canvas — Hyprland infinite desktop.

Usage:
    canvasd          Start the panning daemon
    canvas-ctl CMD   Send command to daemon
"""

import sys


def daemon_main() -> None:
    """Entry point for `canvasd`."""
    from canvas.config import ConfigError
    from canvas.daemon import run

    try:
        run()
    except ConfigError as exc:
        print(f"Invalid configuration:\n{exc}", file=sys.stderr)
        sys.exit(1)


def ctl_main() -> None:
    """Entry point for `canvas-ctl`."""
    from canvas.ipc import send_command

    if len(sys.argv) < 2:
        cmds = (
            "pan-start|pan-stop|nav-left|nav-right|nav-up|nav-down|toggle|"
            "canvas-toggle|edge-start|edge-stop|ping|status"
        )
        print(f"Usage: canvas-ctl <{cmds}>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].upper().replace("-", "_")
    valid = {
        "PAN_START",
        "PAN_STOP",
        "NAV_LEFT",
        "NAV_RIGHT",
        "NAV_UP",
        "NAV_DOWN",
        "TOGGLE",
        "CANVAS_TOGGLE",
        "EDGE_START",
        "EDGE_STOP",
        "PING",
        "STATUS",
    }
    if cmd not in valid:
        print(f"Unknown command: {sys.argv[1]}", file=sys.stderr)
        sys.exit(1)

    response = send_command(cmd)
    if not response or response.startswith("ERROR"):
        print(response or "ERROR: empty response from daemon", file=sys.stderr)
        sys.exit(1)
    print(response)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        daemon_main()
    else:
        ctl_main()
