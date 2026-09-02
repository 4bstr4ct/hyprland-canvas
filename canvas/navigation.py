"""Navigate between floating windows on the infinite canvas."""

import json
import logging
import re
import time
from typing import Any

from canvas import toggle_state
from canvas.hypr import HyprIPC

log = logging.getLogger("canvas.navigation")

_VALID_ADDR = re.compile(r"^0x[0-9a-fA-F]+$")


def _safe_int(value: object, name: str) -> int:
    """Coerce to int, raising ValueError if impossible.

    Prevents accidental string interpolation into Lua — all values
    inserted into Lua f-strings MUST pass through this or _VALID_ADDR.
    """
    try:
        result: int = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsafe Lua value: {name}={value!r}") from exc
    return result


class Navigator:
    """Handles navigation between floating windows with auto-pan."""

    def __init__(
        self,
        ipc: HyprIPC,
        protected_apps: list[str],
        cooldown: float = 0.2,
        preserve_geometry: bool = True,
    ) -> None:
        self._ipc = ipc
        self._protected_apps = [a.lower() for a in protected_apps]
        self._cooldown = cooldown
        self._preserve_geometry = preserve_geometry
        self._last_nav_time = 0.0
        # workspace id -> snapshot of TILED windows before canvas ON.
        # Only these are tiled again on OFF. When preserve_geometry is true,
        # each entry also stores at/size to restore exact floating geometry.
        raw = toggle_state.load()
        self._canvas_mode_workspaces: dict[int, dict[str, dict[str, list[int]]]] = {}
        for ws, snap in raw.items():
            if isinstance(snap, list):
                # Legacy format (list of addresses) — from mocked load in tests or old file
                self._canvas_mode_workspaces[ws] = {str(a): {} for a in snap if isinstance(a, str)}
            elif isinstance(snap, dict):
                self._canvas_mode_workspaces[ws] = dict(snap)
            else:
                self._canvas_mode_workspaces[ws] = {}

    @staticmethod
    def _window_center(w: dict[str, Any]) -> tuple[int, int]:
        return w["at"][0] + w["size"][0] // 2, w["at"][1] + w["size"][1] // 2

    @staticmethod
    def _window_bounds(w: dict[str, Any]) -> dict[str, int]:
        x, y = w["at"][0], w["at"][1]
        ww, wh = w["size"][0], w["size"][1]
        return {
            "left": x,
            "right": x + ww,
            "top": y,
            "bottom": y + wh,
            "center_x": x + ww // 2,
            "center_y": y + wh // 2,
        }

    @staticmethod
    def _overlap_h(b1: dict[str, int], b2: dict[str, int]) -> bool:
        return not (b1["right"] <= b2["left"] or b1["left"] >= b2["right"])

    @staticmethod
    def _overlap_v(b1: dict[str, int], b2: dict[str, int]) -> bool:
        return not (b1["bottom"] <= b2["top"] or b1["top"] >= b2["bottom"])

    def _find_spatial_target(
        self,
        floating: list[dict[str, Any]],
        current_bounds: dict[str, int],
        current_center: tuple[int, int],
        direction: str,
    ) -> dict[str, Any] | None:
        cx, cy = current_center
        candidates = [w for w in floating if not self._is_protected(w)]
        if not candidates:
            return None

        # Tier 1: overlapping band + direction
        aligned: list[tuple[dict[str, Any], int]] = []
        for w in candidates:
            b = self._window_bounds(w)
            wx, wy = b["center_x"], b["center_y"]
            if direction == "left" and self._overlap_v(current_bounds, b) and wx < cx:
                aligned.append((w, cx - wx))
            elif direction == "right" and self._overlap_v(current_bounds, b) and wx > cx:
                aligned.append((w, wx - cx))
            elif direction == "up" and self._overlap_h(current_bounds, b) and wy < cy:
                aligned.append((w, cy - wy))
            elif direction == "down" and self._overlap_h(current_bounds, b) and wy > cy:
                aligned.append((w, wy - cy))
        if aligned:
            return sorted(aligned, key=lambda x: x[1])[0][0]

        # Tier 2: any window in direction
        same_dir: list[tuple[dict[str, Any], int]] = []
        for w in candidates:
            b = self._window_bounds(w)
            wx, wy = b["center_x"], b["center_y"]
            if direction == "left" and wx < cx:
                same_dir.append((w, cx - wx))
            elif direction == "right" and wx > cx:
                same_dir.append((w, wx - cx))
            elif direction == "up" and wy < cy:
                same_dir.append((w, cy - wy))
            elif direction == "down" and wy > cy:
                same_dir.append((w, wy - cy))
        if same_dir:
            return sorted(same_dir, key=lambda x: x[1])[0][0]

        # Tier 3: wrap — farthest in opposite direction
        opp = {"left": "right", "right": "left", "up": "down", "down": "up"}[direction]
        wrap: list[tuple[dict[str, Any], int]] = []
        for w in candidates:
            b = self._window_bounds(w)
            wx, wy = b["center_x"], b["center_y"]
            if opp == "left" and wx < cx:
                wrap.append((w, cx - wx))
            elif opp == "right" and wx > cx:
                wrap.append((w, wx - cx))
            elif opp == "up" and wy < cy:
                wrap.append((w, cy - wy))
            elif opp == "down" and wy > cy:
                wrap.append((w, wy - cy))
        if wrap:
            return sorted(wrap, key=lambda x: x[1])[0][0]
        return None

    def navigate(self, direction: str) -> None:
        """Navigate to the nearest floating window in direction, panning canvas."""
        current_time = time.monotonic()
        if current_time - self._last_nav_time < self._cooldown:
            return
        self._last_nav_time = current_time

        workspace_id = self._get_active_workspace_id()
        if workspace_id is None:
            return

        floating = self._get_floating_windows(workspace_id)
        if len(floating) <= 1:
            return

        focused = self._get_focused_window()
        if focused is None or "address" not in focused:
            return

        current_addr = focused["address"]
        current_win = next((w for w in floating if w["address"] == current_addr), None)
        if current_win is None:
            return

        current_bounds = self._window_bounds(current_win)
        current_center = self._window_center(current_win)

        target = self._find_spatial_target(floating, current_bounds, current_center, direction)
        # Fallback: circular index order (legacy behavior) when no spatial candidate
        target_addr: str | None = None
        if target is not None:
            target_addr = target["address"]
        else:
            # No window in that direction spatially — cycle by index (protected-aware)
            current_index = next(
                (i for i, w in enumerate(floating) if w["address"] == current_addr), -1
            )
            if current_index != -1:
                idx = current_index
                for _ in range(len(floating)):
                    if direction in ("right", "down"):
                        idx = (idx + 1) % len(floating)
                    else:
                        idx = (idx - 1) % len(floating)
                    if not self._is_protected(floating[idx]):
                        target_addr = floating[idx]["address"]
                        break

        if target_addr is None:
            return

        center_x, center_y = self._get_monitor_center()
        floating_updated = self._get_floating_windows(workspace_id)
        self._pan_to_window(floating_updated, target_addr, center_x, center_y, workspace_id)

    def _persist_canvas_state(self) -> None:
        toggle_state.save(self._canvas_mode_workspaces)

    def canvas_toggle(self) -> str:
        # Backward-compat alias — single word `canvas-toggle` still means "all"
        return self.canvas_toggle_all()

    def canvas_toggle_all(self) -> str:
        workspace_id = self._get_active_workspace_id()
        if workspace_id is None:
            return "ERROR:NO_WORKSPACE"

        if workspace_id in self._canvas_mode_workspaces:
            snapshot = self._canvas_mode_workspaces.pop(workspace_id)
            self._persist_canvas_state()
            if snapshot:
                self._tile_windows(workspace_id, snapshot)
                return "CANVAS_OFF"
            # Canvas was enabled on an already all-floating workspace:
            # nothing to tile back, and that is not an error.
            return "CANVAS_OFF"

        tiled_snapshot = self._snapshot_tiled_windows(workspace_id)
        self._canvas_mode_workspaces[workspace_id] = tiled_snapshot
        self._persist_canvas_state()
        self._set_all_floating(workspace_id, floating=True)
        return "CANVAS_ON"

    def canvas_toggle_single(self) -> str:
        focused = self._get_focused_window()
        if focused is None or "address" not in focused:
            return "ERROR:NO_FOCUS"
        addr = str(focused["address"])
        if not _VALID_ADDR.match(addr):
            return "ERROR:BAD_ADDRESS"
        was_floating = bool(focused.get("floating"))
        try:
            lua = (
                f'local w = nil\n'
                f'for _, win in ipairs(hl.get_windows({{}})) do\n'
                f'  if tostring(win.address) == "{addr}" then w = win; break end\n'
                f'end\n'
                f'if w then hl.dispatch(hl.dsp.window.float({{'
                f' action = "toggle", window = w }})) end'
            )
            self._ipc.eval_lua(lua)
        except Exception as e:
            log.warning("toggle single failed: %s", e)
            return "ERROR:TOGGLE_FAILED"
        return "TILED" if was_floating else "FLOATED"

    def _snapshot_tiled_windows(
        self, workspace_id: int
    ) -> dict[str, dict[str, list[int]]]:
        """Snapshot of currently tiled windows on the workspace (pre-canvas state).

        When preserve_geometry is true, each entry stores at/size for exact restore.
        """
        try:
            resp = self._ipc.send("j/clients")
            clients: list[dict[str, Any]] = json.loads(resp)
            snap: dict[str, dict[str, list[int]]] = {}
            for w in clients:
                if w.get("floating"):
                    continue
                addr = w.get("address")
                if not addr or not isinstance(addr, str):
                    continue
                wsw = w.get("workspace")
                if not isinstance(wsw, dict) or wsw.get("id") != workspace_id:
                    continue
                if self._preserve_geometry:
                    at = w.get("at", [0, 0])
                    size = w.get("size", [0, 0])
                    try:
                        snap[addr] = {
                            "at": [int(at[0]), int(at[1])],
                            "size": [int(size[0]), int(size[1])],
                        }
                    except Exception:
                        snap[addr] = {}
                else:
                    snap[addr] = {}
            return snap
        except Exception as e:
            log.warning("snapshot tiled windows failed: %s", e)
            return {}

    def _tile_windows(
        self, workspace_id: int, snapshot: dict[str, dict[str, list[int]]]
    ) -> None:
        """Tile exactly the windows recorded in the snapshot, leaving others floating.

        When geometry was preserved, moves/resizes each floating window to its
        saved at/size before toggling it tiled (mirrors v2's restore path).
        """
        safe_addrs = [a for a in snapshot if _VALID_ADDR.match(a)]
        if not safe_addrs:
            return
        ws_id = _safe_int(workspace_id, "workspace_id")
        if self._preserve_geometry and any(snapshot.get(a, {}).get("at") for a in safe_addrs):
            # Geometry-aware restore: move+resize then toggle
            lines = ["local geos = {"]
            for addr in sorted(safe_addrs):
                geo = snapshot.get(addr, {})
                at = geo.get("at", [0, 0])
                size = geo.get("size", [0, 0])
                try:
                    ax, ay = int(at[0]), int(at[1])
                    sw, sh = int(size[0]), int(size[1])
                except Exception:
                    ax, ay, sw, sh = 0, 0, 0, 0
                lines.append(f'  ["{addr}"] = {{at={{{ax},{ay}}}, size={{{sw},{sh}}}}},')
            lines.append("}")
            lines.append(f"local ws = hl.get_windows({{ floating = true, workspace = {ws_id} }})")
            lines.append("for _, w in ipairs(ws) do")
            lines.append("  local g = geos[tostring(w.address)]")
            lines.append("  if g then")
            lines.append(
                "    hl.dispatch(hl.dsp.window.move({"
                " x=g.at[1], y=g.at[2], relative=false, window=w }))"
            )
            lines.append(
                "    hl.dispatch(hl.dsp.window.resize({"
                " width=g.size[1], height=g.size[2], window=w }))"
            )
            lines.append(
                "    hl.dispatch(hl.dsp.window.float({ action = \"toggle\", window = w }))"
            )
            lines.append("  end")
            lines.append("end")
        else:
            lines = ["local targets = {"]
            lines.extend(f'  ["{a}"] = true,' for a in sorted(safe_addrs))
            lines.append("}")
            lines.append(f"local ws = hl.get_windows({{ floating = true, workspace = {ws_id} }})")
            lines.append("for _, w in ipairs(ws) do")
            lines.append("  if targets[tostring(w.address)] then")
            lines.append("    hl.dispatch(hl.dsp.focus({ window = w }))")
            lines.append('    hl.dispatch(hl.dsp.window.float({ action = "toggle" }))')
            lines.append("  end")
            lines.append("end")
        try:
            self._ipc.eval_lua("\n".join(lines))
        except Exception as e:
            log.warning("tile windows failed: %s", e)

    def _set_all_floating(self, workspace_id: int, floating: bool) -> None:
        """Make every currently-tiled window on the workspace floating (canvas ON).

        The inverse is intentionally NOT done here: turning canvas off must
        tile only the windows recorded in the snapshot (_tile_windows), so
        windows that were already floating before canvas mode survive.
        """
        try:
            ws_id = _safe_int(workspace_id, "workspace_id")
            fl = "false" if floating else "true"
            lua = (
                f"local ws = hl.get_windows({{ floating = {fl}, "
                f"workspace = {ws_id} }}) "
                f"for _, w in ipairs(ws) do "
                f"hl.dispatch(hl.dsp.focus({{ window = w }})) "
                f'hl.dispatch(hl.dsp.window.float({{ action = "toggle" }})) end'
            )
            self._ipc.eval_lua(lua)
        except Exception as e:
            log.warning("set_all_floating failed: %s", e)

    def _is_protected(self, window: dict[str, Any]) -> bool:
        """Check if window class matches a protected app."""
        window_class = window.get("class", "").lower()
        return any(app in window_class for app in self._protected_apps)

    def _pan_to_window(
        self,
        floating_windows: list[dict[str, Any]],
        target_addr: Any,
        center_x: int,
        center_y: int,
        workspace_id: int | None = None,
    ) -> None:
        """Pan the workspace's floating windows so the target centers on monitor."""
        target = None
        for w in floating_windows:
            if w["address"] == target_addr:
                target = w
                break

        if target is None:
            return

        target_cx = target["at"][0] + target["size"][0] // 2
        target_cy = target["at"][1] + target["size"][1] // 2

        dx = center_x - target_cx
        dy = center_y - target_cy

        safe_dx = _safe_int(dx, "dx")
        safe_dy = _safe_int(dy, "dy")

        ws_filter = ""
        if workspace_id is not None:
            ws_filter = f", workspace = {_safe_int(workspace_id, 'workspace_id')}"

        lua = (
            f"local ws = hl.get_windows({{ floating = true{ws_filter} }})\n"
            f"for _, w in ipairs(ws) do\n"
            f"  hl.dispatch(hl.dsp.window.move({{"
            f" x = {safe_dx}, y = {safe_dy},"
            f" relative = true, window = w }}))\n"
            f"end\n"
        )

        # Focus target by iterating floating windows and matching address
        # (avoids Lua injection from class names)
        if not self._is_protected(target):
            addr = target.get("address", "")
            if _VALID_ADDR.match(addr):
                lua += (
                    f"local _t = hl.get_windows({{ floating = true{ws_filter} }})\n"
                    f"for _, w in ipairs(_t) do\n"
                    f'  if tostring(w.address) == "{addr}" then\n'
                    f"    hl.dispatch(hl.dsp.focus({{ window = w }}))\n"
                    f"    break\n"
                    f"  end\n"
                    f"end\n"
                )

        self._ipc.eval_lua(lua)

    def _get_active_workspace_id(self) -> int | None:
        try:
            resp = self._ipc.send("j/activeworkspace")
            ws: dict[str, Any] = json.loads(resp)
            return int(ws["id"])
        except Exception as e:
            log.debug("get_active_workspace_id failed: %s", e)
            return None

    def _get_floating_windows(self, workspace_id: int) -> list[dict[str, Any]]:
        try:
            resp = self._ipc.send("j/clients")
            clients: list[dict[str, Any]] = json.loads(resp)
            return [
                w
                for w in clients
                if w.get("floating")
                and (ws := w.get("workspace")) is not None
                and ws.get("id") == workspace_id
            ]
        except Exception as e:
            log.debug("get_floating_windows failed: %s", e)
            return []

    def _get_focused_window(self) -> dict[str, Any] | None:
        try:
            resp = self._ipc.send("j/activewindow")
            result: dict[str, Any] = json.loads(resp)
            return result
        except Exception as e:
            log.debug("get_focused_window failed: %s", e)
            return None

    def _get_monitor_center(self) -> tuple[int, int]:
        try:
            resp = self._ipc.send("j/monitors")
            monitors: list[dict[str, Any]] = json.loads(resp)
            for m in monitors:
                if m.get("focused", False):
                    return m["x"] + m["width"] // 2, m["y"] + m["height"] // 2
            if monitors:
                m = monitors[0]
                return m["x"] + m["width"] // 2, m["y"] + m["height"] // 2
        except Exception as e:
            log.debug("get_monitor_center failed: %s", e)
        return 960, 540
