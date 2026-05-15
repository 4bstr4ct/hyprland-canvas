"""Load YAML configuration with sensible defaults."""

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "speed": 1.6,
    "navigation": {
        "cooldown": 0.2,
        "protected_apps": [
            "brave-browser",
            "chromium",
            "chromium-browser",
            "google-chrome",
            "firefox",
            "firefoxdeveloperedition",
            "librewolf",
            "vivaldi",
            "opera",
            "microsoft-edge",
        ],
    },
    "invert": {
        "enabled": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override wins on conflicts."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path: str | None = None, skip_user: bool = False) -> dict[str, Any]:
    """Load config from YAML file, merging with defaults.

    Search order:
        1. Explicit path
        2. ~/.config/canvas/config.yml (unless skip_user=True)
        3. <project_dir>/config.yml (bundled default)
        4. Hardcoded DEFAULT_CONFIG
    """
    candidates: list[str] = []

    if path:
        candidates.append(path)

    if not skip_user:
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        candidates.append(os.path.join(xdg, "canvas", "config.yml"))

    candidates.append(str(Path(__file__).resolve().parent.parent / "config.yml"))

    for candidate in candidates:
        if os.path.isfile(candidate):
            with open(candidate) as f:
                user_cfg = yaml.safe_load(f) or {}
            return _deep_merge(DEFAULT_CONFIG, user_cfg)

    return copy.deepcopy(DEFAULT_CONFIG)
