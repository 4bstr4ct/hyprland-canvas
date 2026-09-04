"""Persistence for canvas-toggle snapshots.

Per workspace two sections:
- "tiled": addresses that were tiled before canvas mode was enabled.
  Only these are tiled again on OFF. Geometry here is informational
  (tiled placement is layout-owned) and only drives toggle ordering.
- "floating": last known floating geometry per address. Reapplied on
  the next ON so the canvas comes back where it was.

Written under XDG_RUNTIME_DIR (tmpfs): survives daemon restarts within
a session, resets on reboot — matching the session-scoped nature of
canvas mode itself.

Format v2 on disk: {"_v": 2, "<ws>": {"tiled": {...}, "floating": {...}}}.
Older files (list of addresses, or bare addr->geo without _v) are
auto-migrated on load: addresses kept for OFF targeting, geometry
dropped (old geos were tiled coords, useless as floating restore).
"""

import json
import logging
import os
from typing import Any

from canvas import debug

log = logging.getLogger("canvas.toggle")

FORMAT_VERSION = 2

Snapshot = dict[str, dict[str, list[int]]]
WorkspaceState = dict[str, Snapshot]  # {"tiled": Snapshot, "floating": Snapshot}
State = dict[int, WorkspaceState]


def _empty_workspace() -> WorkspaceState:
    return {"tiled": {}, "floating": {}}


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


def _parse_workspace(raw_ws: Any, version: int) -> WorkspaceState:
    """Parse one workspace entry, migrating old formats.

    v2: {"tiled": {...}, "floating": {...}} — used as-is.
    Older (list of addresses, or bare addr->geo without _v): addresses are
    kept for OFF targeting, geometry is dropped — old geos describe tiled
    slots, which must never be applied as floating positions.
    """
    if (
        version >= FORMAT_VERSION
        and isinstance(raw_ws, dict)
        and ("tiled" in raw_ws or "floating" in raw_ws)
    ):
        return {
            "tiled": _parse_snapshot(raw_ws.get("tiled", {})),
            "floating": _parse_snapshot(raw_ws.get("floating", {})),
        }
    addrs: set[str] = set()
    if isinstance(raw_ws, list):
        addrs = {str(a) for a in raw_ws if isinstance(a, str)}
    elif isinstance(raw_ws, dict):
        for addr in raw_ws:
            if isinstance(addr, str):
                addrs.add(addr)
    return {"tiled": {a: {} for a in addrs}, "floating": {}}


def load(path: str | None = None) -> State:
    """Load saved snapshots. Missing or corrupt file yields an empty state."""
    file = path or default_path()
    try:
        with open(file) as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("state must be an object")
        try:
            version = int(raw.get("_v", 0))
        except Exception:
            version = 0
        state: State = {}
        for k, v in raw.items():
            if k == "_v":
                continue
            try:
                ws_id = int(k)
            except Exception:
                continue
            state[ws_id] = _parse_workspace(v, version)
        if debug.enabled():
            debug.dbg2(
                "STATE_LOAD",
                path=file,
                workspaces=sorted(state.keys()),
                counts={
                    ws: {
                        "tiled": len(s.get("tiled", {})),
                        "floating": len(s.get("floating", {})),
                    }
                    for ws, s in state.items()
                },
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
    """Atomically persist snapshots (format v2 with tiled/floating sections)."""
    file = path or default_path()
    try:
        os.makedirs(os.path.dirname(file), exist_ok=True)
        tmp = file + ".tmp"
        # Sort for deterministic output
        serializable: dict[str, Any] = {"_v": FORMAT_VERSION}
        for ws, sections in sorted(state.items()):
            serializable[str(ws)] = {
                section: {addr: geo for addr, geo in sorted(snap.items())}
                for section, snap in sorted(sections.items())
            }
        with open(tmp, "w") as f:
            json.dump(serializable, f)
        os.replace(tmp, file)
        if debug.enabled():
            debug.dbg2(
                "STATE_SAVE",
                path=file,
                workspaces=sorted(state.keys()),
                counts={
                    ws: {
                        "tiled": len(s.get("tiled", {})),
                        "floating": len(s.get("floating", {})),
                    }
                    for ws, s in state.items()
                },
            )
            if debug.level() >= 2 and state:
                debug.dbg2("STATE_SAVE_DETAIL", state=state)
    except Exception as e:
        log.warning("could not write toggle state %s: %s", file, e)
        if debug.enabled():
            debug.dbg2("STATE_SAVE_ERROR", path=file, error=str(e))
