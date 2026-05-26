"""Navigate between floating windows on the infinite canvas."""

import json
import logging
import re
import time
from typing import Any

from canvas import hypr

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

    def __init__(self, protected_apps: list[str], cooldown: float = 0.2):
        self._protected_apps = [a.lower() for a in protected_apps]
        self._cooldown = cooldown
        self._last_nav_time = 0.0
        self._canvas_mode_workspaces: set[int] = set()

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
        self._pan_to_window(floating_updated, floating[new_index]["address"], center_x, center_y)

    def canvas_toggle(self) -> str:
        workspace_id = self._get_active_workspace_id()
        if workspace_id is None:
            return "ERROR:NO_WORKSPACE"

        if workspace_id in self._canvas_mode_workspaces:
            self._canvas_mode_workspaces.discard(workspace_id)
            self._set_all_floating(workspace_id, floating=False)
            return "CANVAS_OFF"
        else:
            self._canvas_mode_workspaces.add(workspace_id)
            self._set_all_floating(workspace_id, floating=True)
            return "CANVAS_ON"

    def _set_all_floating(self, workspace_id: int, floating: bool) -> None:
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
            hypr.eval_lua(lua)
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
    ) -> None:
        """Pan all floating windows so target window centers on monitor via Lua API."""
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

        lua = (
            f"local ws = hl.get_windows({{ floating = true }})\n"
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
                    f"local _t = hl.get_windows({{ floating = true }})\n"
                    f"for _, w in ipairs(_t) do\n"
                    f'  if tostring(w.address) == "{addr}" then\n'
                    f"    hl.dispatch(hl.dsp.focus({{ window = w }}))\n"
                    f"    break\n"
                    f"  end\n"
                    f"end\n"
                )

        hypr.eval_lua(lua)

    # --- Hyprland IPC helpers (via direct socket) ---

    def _get_active_workspace_id(self) -> int | None:
        try:
            resp = hypr.send("j/activeworkspace")
            ws: dict[str, Any] = json.loads(resp)
            return int(ws["id"])
        except Exception as e:
            log.debug("get_active_workspace_id failed: %s", e)
            return None

    def _get_floating_windows(self, workspace_id: int) -> list[dict[str, Any]]:
        try:
            resp = hypr.send("j/clients")
            clients: list[dict[str, Any]] = json.loads(resp)
            return [
                w
                for w in clients
                if w.get("floating") and w.get("workspace", {}).get("id") == workspace_id
            ]
        except Exception as e:
            log.debug("get_floating_windows failed: %s", e)
            return []

    def _get_focused_window(self) -> dict[str, Any] | None:
        try:
            resp = hypr.send("j/activewindow")
            result: dict[str, Any] = json.loads(resp)
            return result
        except Exception as e:
            log.debug("get_focused_window failed: %s", e)
            return None

    def _get_monitor_center(self) -> tuple[int, int]:
        try:
            resp = hypr.send("j/monitors")
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
