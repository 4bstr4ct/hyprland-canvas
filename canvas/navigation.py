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
    ) -> None:
        self._ipc = ipc
        self._protected_apps = [a.lower() for a in protected_apps]
        self._cooldown = cooldown
        self._last_nav_time = 0.0
        # workspace id -> addresses that were TILED before canvas mode went on.
        # Only these are tiled again on OFF, so windows the user kept floating
        # before enabling canvas mode are never touched.
        loaded = toggle_state.load()
        self._canvas_mode_workspaces: dict[int, set[str]] = {
            ws: set(addrs) for ws, addrs in loaded.items()
        }

    def navigate(self, direction: str) -> None:
        """Navigate to the next/prev floating window, panning the canvas to center it."""
        current_time = time.time()
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
        current_index = -1
        for i, w in enumerate(floating):
            if w["address"] == current_addr:
                current_index = i
                break

        if current_index == -1:
            return

        # Find next non-protected window in direction
        new_index = current_index
        attempts = 0
        while attempts < len(floating):
            if direction == "right":
                new_index = (new_index + 1) % len(floating)
            else:
                new_index = (new_index - 1) % len(floating)

            if not self._is_protected(floating[new_index]):
                break
            attempts += 1

        if attempts >= len(floating):
            return  # all windows are protected

        center_x, center_y = self._get_monitor_center()
        # Re-fetch floating windows (positions may have changed)
        floating_updated = self._get_floating_windows(workspace_id)
        self._pan_to_window(
            floating_updated, floating[new_index]["address"], center_x, center_y, workspace_id
        )

    def _persist_canvas_state(self) -> None:
        toggle_state.save({ws: sorted(a) for ws, a in self._canvas_mode_workspaces.items()})

    def canvas_toggle(self) -> str:
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

        tiled_addrs = self._snapshot_tiled_windows(workspace_id)
        self._canvas_mode_workspaces[workspace_id] = tiled_addrs
        self._persist_canvas_state()
        self._set_all_floating(workspace_id, floating=True)
        return "CANVAS_ON"

    def _snapshot_tiled_windows(self, workspace_id: int) -> set[str]:
        """Addresses of currently tiled windows on the workspace (pre-canvas state)."""
        try:
            resp = self._ipc.send("j/clients")
            clients: list[dict[str, Any]] = json.loads(resp)
            return {
                w["address"]
                for w in clients
                if not w.get("floating")
                and w.get("address")
                and (ws := w.get("workspace")) is not None
                and ws.get("id") == workspace_id
            }
        except Exception as e:
            log.warning("snapshot tiled windows failed: %s", e)
            return set()

    def _tile_windows(self, workspace_id: int, addresses: set[str]) -> None:
        """Tile exactly the windows recorded in the snapshot, leaving others floating."""
        safe_addrs = [a for a in addresses if _VALID_ADDR.match(a)]
        if not safe_addrs:
            return
        ws_id = _safe_int(workspace_id, "workspace_id")
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
