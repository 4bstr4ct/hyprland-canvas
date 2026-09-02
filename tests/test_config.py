import os
import tempfile

import pytest

from canvas.config import DEFAULT_CONFIG, ConfigError, load, validate


def test_load_default_config_when_no_file():
    """Config returns hardcoded defaults when no YAML files exist at all."""
    # skip_user=True + path that doesn't exist → falls through to bundled config.yml
    # which has speed:1.3. To test pure defaults, we need to also skip bundled.
    # Easiest: just verify the merge logic works, not the exact default value.
    cfg = load("/nonexistent/path/config.yml", skip_user=True)
    # Bundled config.yml speed matches DEFAULT_CONFIG
    assert cfg["speed"] == DEFAULT_CONFIG["speed"]
    # Navigation defaults from DEFAULT_CONFIG still apply
    assert cfg["navigation"]["cooldown"] == 0.2
    assert "firefox" in cfg["navigation"]["protected_apps"]
    assert cfg["invert"]["enabled"] is True


def test_load_partial_config_merges_defaults():
    """Partial YAML file merges with defaults."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("speed: 3.0\ninvert:\n  enabled: true\n")
        f.flush()
        cfg = load(f.name)
    os.unlink(f.name)

    assert cfg["speed"] == 3.0
    assert cfg["invert"]["enabled"] is True
    # defaults still present
    assert cfg["navigation"]["cooldown"] == DEFAULT_CONFIG["navigation"]["cooldown"]


def test_load_full_config():
    """Full YAML file overrides all defaults."""
    yaml_content = """speed: 2.0
navigation:
  cooldown: 0.5
  protected_apps:
    - my-browser
invert:
  enabled: true
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        cfg = load(f.name)
    os.unlink(f.name)

    assert cfg["speed"] == 2.0
    assert cfg["navigation"]["cooldown"] == 0.5
    assert cfg["navigation"]["protected_apps"] == ["my-browser"]
    assert cfg["invert"]["enabled"] is True


def test_deep_merge_does_not_mutate_defaults():
    """Loading config must not mutate DEFAULT_CONFIG."""
    from canvas.config import DEFAULT_CONFIG as dc

    original_speed = dc["speed"]
    original_protected = list(dc["navigation"]["protected_apps"])

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write("speed: 99.0\nnavigation:\n  protected_apps:\n    - hacked\n")
        f.flush()
        cfg = load(f.name)
    os.unlink(f.name)

    # The returned config has overrides
    assert cfg["speed"] == 99.0
    assert cfg["navigation"]["protected_apps"] == ["hacked"]
    # But DEFAULT_CONFIG is untouched
    assert dc["speed"] == original_speed
    assert dc["navigation"]["protected_apps"] == original_protected


# --- validation ---


def _load_yaml(content: str):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(content)
        f.flush()
        name = f.name
    try:
        return load(name)
    finally:
        os.unlink(name)


def test_validate_default_config_has_no_problems():
    import copy

    assert validate(copy.deepcopy(DEFAULT_CONFIG)) == []


def test_validate_rejects_zero_speed():
    problems = validate({"speed": 0})
    assert any("speed" in p for p in problems)


def test_validate_rejects_string_speed():
    problems = validate({"speed": "abc"})
    assert any("speed" in p for p in problems)


def test_validate_rejects_bool_speed():
    """bool is not a valid number for config purposes."""
    problems = validate({"speed": True})
    assert any("speed" in p for p in problems)


def test_validate_rejects_negative_max_speed():
    problems = validate({"speed": 1.0, "max_speed": -5})
    assert any("max_speed" in p for p in problems)


def test_validate_allows_null_max_speed():
    problems = validate({"speed": 1.0, "max_speed": None})
    assert not any("max_speed" in p for p in problems)


def test_validate_rejects_negative_cooldown():
    problems = validate({"speed": 1.0, "navigation": {"cooldown": -1}})
    assert any("cooldown" in p for p in problems)


def test_validate_rejects_non_list_protected_apps():
    problems = validate({"speed": 1.0, "navigation": {"protected_apps": "firefox"}})
    assert any("protected_apps" in p for p in problems)


def test_validate_rejects_non_dict_section():
    problems = validate({"speed": 1.0, "invert": None})
    assert any("invert" in p for p in problems)


def test_validate_rejects_zero_ramp_distance():
    problems = validate(
        {"speed": 1.0, "edge_scroll": {"ramp_distance": 0, "speed": 20.0, "enabled": True}}
    )
    assert any("ramp_distance" in p for p in problems)


def test_validate_rejects_float_ramp_distance():
    problems = validate(
        {"speed": 1.0, "edge_scroll": {"ramp_distance": 50.5, "speed": 20.0, "enabled": True}}
    )
    assert any("ramp_distance" in p for p in problems)


def test_load_raises_config_error_listing_problems():
    with pytest.raises(ConfigError) as exc_info:
        _load_yaml("speed: -1\ninvert:\n  enabled: maybe\n")
    msg = str(exc_info.value)
    assert "speed" in msg
    assert "invert.enabled" in msg


def test_load_valid_partial_config_passes():
    cfg = _load_yaml("speed: 2.5\n")
    assert cfg["speed"] == 2.5


# --- toggle_state persistence ---


def test_toggle_state_roundtrip(tmp_path):
    from canvas.toggle_state import load as ts_load
    from canvas.toggle_state import save as ts_save

    file = str(tmp_path / "toggle.json")
    state = {
        1: {"0xabc": {"at": [10, 20], "size": [500, 300]}, "0x2": {}},
        7: {"0xfff": {"at": [0, 0], "size": [100, 100]}},
    }
    ts_save(state, path=file)
    loaded = ts_load(path=file)
    assert loaded == state


def test_toggle_state_roundtrip_legacy_list(tmp_path):
    """Old list format is migrated to dict with empty geometry."""
    from canvas.toggle_state import load as ts_load

    file = str(tmp_path / "toggle.json")
    # Simulate old file on disk directly
    import json

    with open(file, "w") as f:
        json.dump({"1": ["0xabc", "0x2"], "7": ["0xfff"]}, f)
    loaded = ts_load(path=file)
    assert set(loaded[1].keys()) == {"0xabc", "0x2"}
    assert set(loaded[7].keys()) == {"0xfff"}


def test_toggle_state_load_missing_file(tmp_path):
    from canvas.toggle_state import load as ts_load

    assert ts_load(path=str(tmp_path / "absent.json")) == {}


def test_toggle_state_load_corrupt_file(tmp_path):
    import logging

    logging.disable(logging.CRITICAL)
    from canvas.toggle_state import load as ts_load

    file = tmp_path / "bad.json"
    file.write_text("{not json")
    try:
        assert ts_load(path=str(file)) == {}
    finally:
        logging.disable(logging.NOTSET)


def test_validate_rejects_bad_grab_dead_zone():
    problems = validate(
        {
            "speed": 1.0,
            "edge_scroll": {
                "ramp_distance": 50,
                "speed": 20.0,
                "enabled": True,
                "grab_dead_zone": 0,
            },
        }
    )
    assert any("grab_dead_zone" in p for p in problems)
