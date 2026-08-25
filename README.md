# hyprland-canvas

Infinite canvas for Hyprland — pan all floating windows like an infinite desktop.

Drag the canvas with **SUPER+SHIFT+LMB**, navigate between windows, toggle canvas mode per workspace. Runs as an unprivileged user daemon — communicates directly with Hyprland via its IPC socket and Lua API.

## Why

Hyprland has no built-in infinite desktop. This daemon provides one by communicating with Hyprland the right way:

- **Direct Unix socket IPC** to Hyprland (~0.1ms per frame) — no subprocess overhead
- **Hyprland Lua API** (`hl.dsp.window.move`) moves windows without focusing them — no cursor warp or flicker
- Runs as an unprivileged user daemon — no special permissions needed
- Has a **Unix socket IPC** for keyboard-driven commands (navigate, toggle, invert)

## Features

| Feature | Keybind | Description |
|---------|---------|-------------|
| Pan canvas | SUPER+SHIFT+LMB | Drag to pan all floating windows |
| Edge-scroll | SUPER+LMB | Drag window past screen edge — camera follows |
| Navigate | SUPER+SHIFT+Left/Right | Jump to next/prev window, auto-pan to center |
| Canvas toggle | SUPER+SHIFT+C | Toggle all windows on workspace to/from floating |
| Invert | SUPER+SHIFT+G | Invert pan direction |

## Install

**uv (recommended):**
```bash
git clone https://github.com/zyrophix/hyprland-canvas.git
cd hyprland-canvas
uv tool install .
```

**pipx:**
```bash
git clone https://github.com/zyrophix/hyprland-canvas.git
cd hyprland-canvas
pipx install .
```

**Run from source (no install):**
```bash
git clone https://github.com/zyrophix/hyprland-canvas.git
cd hyprland-canvas
uv run canvasd
```

This gives you two commands:
- `canvasd` — the daemon
- `canvas-ctl` — send commands to the daemon

## Usage

### 1. Start the daemon

```bash
canvasd
```

### 2. Add Hyprland keybinds

Hyprland 0.55+ uses Lua for config. Add these binds:

```lua
-- Canvas: pan (mouse binds)
hl.key.bind({"SUPER", "SHIFT"}, "mouse:272", function()
    os.execute("canvas-ctl pan-start")
end, { mouse = true })

hl.key.bind({"SUPER", "SHIFT"}, "mouse:272", function()
    os.execute("canvas-ctl pan-stop")
end, { mouse = true, release = true })

-- Canvas: edge-scroll (drag window to screen edge → camera follows)
hl.bind("SUPER + mouse:272", function()
    hl.dispatch(hl.dsp.window.drag())
    hl.exec_cmd("canvas-ctl edge-start")
end, { mouse = true })

hl.bind("SUPER + mouse:272", function()
    hl.exec_cmd("canvas-ctl edge-stop")
end, { mouse = true, release = true })

-- Canvas: navigation
hl.key.bind({"SUPER", "SHIFT"}, "left", function()
    os.execute("canvas-ctl nav-left")
end)
hl.key.bind({"SUPER", "SHIFT"}, "right", function()
    os.execute("canvas-ctl nav-right")
end)

-- Canvas: toggle & invert
hl.key.bind({"SUPER", "SHIFT"}, "C", function()
    os.execute("canvas-ctl canvas-toggle")
end)
hl.key.bind({"SUPER", "SHIFT"}, "G", function()
    os.execute("canvas-ctl toggle")
end)
```

### 3. Control commands

```bash
canvas-ctl ping              # check if daemon is running
canvas-ctl status            # show pan direction and state
canvas-ctl pan-start         # start panning (called by mouse bind)
canvas-ctl pan-stop          # stop panning (called by mouse release bind)
canvas-ctl nav-left          # navigate to previous window
canvas-ctl nav-right         # navigate to next window
canvas-ctl canvas-toggle     # toggle floating on current workspace
canvas-ctl toggle            # invert pan direction
canvas-ctl edge-start       # start edge-scroll (called by mouse bind)
canvas-ctl edge-stop        # stop edge-scroll (called by mouse release bind)
```

## Configuration

Default config is bundled at `config.yml`. Override in `~/.config/canvas/config.yml`:

```yaml
speed: 1.6                    # pan speed multiplier
invert:
  enabled: false              # start with inverted pan direction
edge_scroll:
  enabled: true               # auto-pan when dragging window past screen edge
  ramp_distance: 50            # px of overflow to reach full speed
  speed: 20.0                  # max px/frame at full overflow (~1200 px/s at 60fps)
  # max_speed: 30             # optional: cap per-frame edge-scroll delta (pixels)
navigation:
  cooldown: 0.2               # seconds between nav commands
  protected_apps:             # these windows are skipped during navigation
    - brave-browser
    - chromium
    - firefox
```

## Architecture

```
canvasd (daemon)
├── hypr.py        Direct Unix socket IPC to Hyprland
├── panning.py     Cursor polling, pan state, edge-scroll state
├── navigation.py  Window navigation, canvas toggle
├── ipc.py         Unix socket server for canvas-ctl
├── config.py      YAML config with deep merge
└── daemon.py      Main loop, wires modules together
```

Key design decisions:

- **Cursor polling** — reads cursor position from Hyprland IPC, works on any Wayland setup
- **`hl.dsp.window.move({window=w})` without focus** — passing a window object bypasses auto-focus, so no cursor warp or feedback loop
- **Direct socket IPC** — one persistent socket connection instead of spawning a subprocess every frame
- **Workspace-scoped** — pan, edge-scroll and navigation only move floating windows on the current workspace; other workspaces are never touched
- **Idle timeout** (500ms) — auto-stops panning if Hyprland drops a mouse release event during active drag

## Requirements

- Hyprland 0.55+ (Lua config with `hl.*` API)
- Python 3.12+
- PyYAML

## License

MIT
