"""Persistence for canvas-toggle snapshots.

Stores which windows were tiled before canvas mode was enabled, keyed by
workspace id. Written under XDG_RUNTIME_DIR (tmpfs): survives daemon
restarts within a session, resets on reboot — matching the session-scoped
nature of canvas mode itself.

New format (geometry-preserving): workspace -> address -> {at:[x,y], size:[w,h]}
Old format (list of addresses) is auto-migrated on load (geometry unknown).
"""

import json
import logging
import os
from typing import Any

from canvas import debug

log = logging.getLogger("canvas.toggle")

Snapshot = dict[str, dict[str, list[int]]]
State = dict[int, Snapshot]


def default_path() -> str:
    uid = os.getuid()
    run_dir = f"/run/user/{uid}"
    base = (
        run_dir
        if os.path.isdir(run_dir)
        else os.environ.get("XDG_RUNTIME_DIR", f"/tmp/user/{uid}")
    )
    return os.path.join(base, "canvas", "toggle-state.json")


def _parse_snapshot(raw_snap: Any) -> Snapshot:
    if isinstance(raw_snap, list):
        # Old format: list of addresses — geometry unknown
        return {str(a): {} for a in raw_snap if isinstance(a, str)}
    if isinstance(raw_snap, dict):
        out: Snapshot = {}
        for addr, geo in raw_snap.items():
            if not isinstance(addr, str):
                continue
            if isinstance(geo, dict) and "at" in geo and "size" in geo:
                try:
                    at = [int(geo["at"][0]), int(geo["at"][1])]
                    size = [int(geo["size"][0]), int(geo["size"][1])]
                    out[addr] = {"at": at, "size": size}
                except Exception:
                    out[addr] = {}
            elif isinstance(geo, (dict, str)):
                out[addr] = {}
        return out
    return {}


def load(path: str | None = None) -> State:
    """Load saved snapshots. Missing or corrupt file yields an empty state."""
    file = path or default_path()
    try:
        with open(file) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("state must be an object")
        state: State = {}
        for k, v in raw.items():
            try:
                ws_id = int(k)
            except Exception:
                continue
            state[ws_id] = _parse_snapshot(v)
        if debug.enabled():
            debug.dbg2(
                "STATE_LOAD",
                path=file,
                workspaces=sorted(state.keys()),
                counts={ws: len(s) for ws, s in state.items()},
            )
            if debug.level() >= 2 and state:
                debug.dbg2("STATE_LOAD_DETAIL", state=state)
        return state
    except FileNotFoundError:
        if debug.enabled():
            debug.dbg2("STATE_LOAD", path=file, workspaces=[], counts={})
        return {}
    except Exception as e:
        log.warning("could not read toggle state %s: %s", file, e)
        if debug.enabled():
            debug.dbg2("STATE_LOAD_ERROR", path=file, error=str(e))
        return {}


def save(state: State, path: str | None = None) -> None:
    """Atomically persist snapshots (new geometry-preserving format)."""
    file = path or default_path()
    try:
        os.makedirs(os.path.dirname(file), exist_ok=True)
        tmp = file + ".tmp"
        # Sort for deterministic output
        serializable = {
            str(ws): {addr: geo for addr, geo in sorted(snap.items())}
            for ws, snap in sorted(state.items())
        }
        with open(tmp, "w") as f:
            json.dump(serializable, f)
        os.replace(tmp, file)
        if debug.enabled():
            debug.dbg2(
                "STATE_SAVE",
                path=file,
                workspaces=sorted(state.keys()),
                counts={ws: len(s) for ws, s in state.items()},
            )
            if debug.level() >= 2 and state:
                debug.dbg2("STATE_SAVE_DETAIL", state=state)
    except Exception as e:
        log.warning("could not write toggle state %s: %s", file, e)
        if debug.enabled():
            debug.dbg2("STATE_SAVE_ERROR", path=file, error=str(e))
