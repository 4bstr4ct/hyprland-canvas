"""Structured debug tracing controlled by CANVAS_DEBUG=1.

dbg() emits "<seconds> EVENT k=v k=v" lines at DEBUG level only when
tracing is enabled; when disabled it returns before any string
formatting, so hot paths stay free.
"""

import logging
import os
import time

log = logging.getLogger("canvas.debug")

_enabled = False
_start = 0.0


def enabled() -> bool:
    return _enabled


def enable_from_env() -> None:
    """Enable tracing unless CANVAS_DEBUG is unset/0/false."""
    global _enabled, _start
    _enabled = os.environ.get("CANVAS_DEBUG", "").lower() not in ("", "0", "false")
    if _enabled:
        _start = time.monotonic()
        log.debug("tracing enabled")


def reset_for_tests() -> None:
    global _enabled, _start
    _enabled = False
    _start = 0.0


def dbg(event: str, **fields: object) -> None:
    """Emit a structured trace line; no-op when tracing is off."""
    if not _enabled:
        return
    parts = [f"{time.monotonic() - _start:.3f}", event]
    parts.extend(f"{k}={v}" for k, v in fields.items())
    log.debug(" ".join(parts))
