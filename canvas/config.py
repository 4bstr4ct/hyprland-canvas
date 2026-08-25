"""Load YAML configuration with sensible defaults."""

import copy
import os
from pathlib import Path
from typing import Any, TypeGuard

import yaml


class ConfigError(Exception):
    """Raised when the configuration contains invalid values."""


DEFAULT_CONFIG: dict[str, Any] = {
    "speed": 1.6,
    "max_speed": None,
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
        "enabled": True,
    },
    "edge_scroll": {
        "enabled": True,
        "ramp_distance": 50,
        "speed": 20.0,
        "max_speed": None,
        "grab_dead_zone": 5,
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


def _is_num(value: object) -> TypeGuard[int | float]:
    """True for int/float but not bool (bool is an int subclass in Python)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(cfg: dict[str, Any]) -> list[str]:
    """Validate configuration values. Returns a list of human-readable problems.

    An empty list means the config is safe to run with.
    """
    errors: list[str] = []

    speed = cfg.get("speed")
    if not _is_num(speed) or speed <= 0:
        errors.append(f"speed must be a number > 0, got {speed!r}")

    max_speed = cfg.get("max_speed")
    if max_speed is not None and (not _is_num(max_speed) or max_speed <= 0):
        errors.append(f"max_speed must be a number > 0 or null, got {max_speed!r}")

    nav = cfg.get("navigation")
    if not isinstance(nav, dict):
        errors.append("navigation section must be a mapping")
    else:
        cooldown = nav.get("cooldown")
        if not _is_num(cooldown) or cooldown < 0:
            errors.append(f"navigation.cooldown must be a number >= 0, got {cooldown!r}")
        apps = nav.get("protected_apps")
        if not isinstance(apps, list) or not all(isinstance(a, str) for a in apps):
            errors.append("navigation.protected_apps must be a list of strings")

    invert = cfg.get("invert")
    if not isinstance(invert, dict):
        errors.append("invert section must be a mapping")
    elif not isinstance(invert.get("enabled"), bool):
        errors.append(f"invert.enabled must be a boolean, got {invert.get('enabled')!r}")

    es = cfg.get("edge_scroll")
    if not isinstance(es, dict):
        errors.append("edge_scroll section must be a mapping")
    else:
        rd = es.get("ramp_distance")
        if not isinstance(rd, int) or isinstance(rd, bool) or rd <= 0:
            errors.append(f"edge_scroll.ramp_distance must be an integer > 0, got {rd!r}")
        es_speed = es.get("speed")
        if not _is_num(es_speed) or es_speed <= 0:
            errors.append(f"edge_scroll.speed must be a number > 0, got {es_speed!r}")
        es_max = es.get("max_speed")
        if es_max is not None and (not _is_num(es_max) or es_max <= 0):
            errors.append(f"edge_scroll.max_speed must be a number > 0 or null, got {es_max!r}")
        if not isinstance(es.get("enabled"), bool):
            errors.append(f"edge_scroll.enabled must be a boolean, got {es.get('enabled')!r}")
        gdz = es.get("grab_dead_zone")
        if not isinstance(gdz, int) or isinstance(gdz, bool) or gdz <= 0:
            errors.append(f"edge_scroll.grab_dead_zone must be an integer > 0, got {gdz!r}")

    return errors


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
            cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)
            problems = validate(cfg)
            if problems:
                raise ConfigError("\n".join(problems))
            return cfg

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    problems = validate(cfg)
    if problems:
        raise ConfigError("\n".join(problems))
    return cfg
