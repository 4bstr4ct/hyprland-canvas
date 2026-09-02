"""Structured debug tracing controlled by CANVAS_DEBUG=1.

dbg() emits "<seconds> EVENT k=v k=v" lines at DEBUG level only when
tracing is enabled; when disabled it returns before any string
formatting, so hot paths stay free.
"""

import logging
import os
import time

log = logging.getLogger("canvas.debug")

_level = 0
_start = 0.0


def enabled() -> bool:
    return _level > 0


def level() -> int:
    return _level


def enable_from_env() -> None:
    """Enable tracing: CANVAS_DEBUG=1 (summary) or 2 (per-window)."""
    global _level, _start
    raw = os.environ.get("CANVAS_DEBUG", "").lower()
    if raw in ("", "0", "false"):
        _level = 0
    elif raw in ("2", "verbose", "debug"):
        _level = 2
    else:
        _level = 1
    if _level:
        _start = time.monotonic()
        log.debug("tracing enabled level=%d", _level)


def reset_for_tests() -> None:
    global _level, _start
    _level = 0
    _start = 0.0


def dbg(event: str, **fields: object) -> None:
    """Emit a structured trace line; no-op when tracing is off."""
    if not _level:
        return
    parts = [f"{time.monotonic() - _start:.3f}", event]
    parts.extend(f"{k}={v}" for k, v in fields.items())
    log.debug(" ".join(parts))


def dbg2(event: str, **fields: object) -> None:
    """Verbose per-window trace — only when CANVAS_DEBUG=2."""
    if _level < 2:
        return
    parts = [f"{time.monotonic() - _start:.3f}", event]
    parts.extend(f"{k}={v}" for k, v in fields.items())
    log.debug(" ".join(parts))
