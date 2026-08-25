"""Persistence for canvas-toggle snapshots.

Stores which windows were tiled before canvas mode was enabled, keyed by
workspace id. Written under XDG_RUNTIME_DIR (tmpfs): survives daemon
restarts within a session, resets on reboot — matching the session-scoped
nature of canvas mode itself.
"""

import json
import logging
import os

log = logging.getLogger("canvas.toggle")


def default_path() -> str:
    uid = os.getuid()
    run_dir = f"/run/user/{uid}"
    base = (
        run_dir
        if os.path.isdir(run_dir)
        else os.environ.get("XDG_RUNTIME_DIR", f"/tmp/user/{uid}")
    )
    return os.path.join(base, "canvas", "toggle-state.json")


def load(path: str | None = None) -> dict[int, list[str]]:
    """Load saved snapshots. Missing or corrupt file yields an empty state."""
    file = path or default_path()
    try:
        with open(file) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("state must be an object")
        return {int(k): [str(a) for a in v] for k, v in raw.items()}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("could not read toggle state %s: %s", file, e)
        return {}


def save(state: dict[int, list[str]], path: str | None = None) -> None:
    """Atomically persist snapshots."""
    file = path or default_path()
    try:
        os.makedirs(os.path.dirname(file), exist_ok=True)
        tmp = file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({str(k): sorted(v) for k, v in state.items()}, f)
        os.replace(tmp, file)
    except Exception as e:
        log.warning("could not write toggle state %s: %s", file, e)
