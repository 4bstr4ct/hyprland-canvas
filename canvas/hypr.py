"""Direct Hyprland IPC via Unix socket — no subprocess needed.

Hyprland's .socket.sock speaks a one-shot request-response protocol:
  connect → send b"<command>" → read response until EOF → close

The server closes the connection after each response, so we can't
multiplex multiple commands on one socket. The module keeps a
pre-connected socket for zero-latency first send and reconnects
automatically on error. Thread-safe via lock.
"""

import contextlib
import logging
import os
import socket
import threading

log = logging.getLogger("canvas.hypr")


def _hypr_socket_path() -> str:
    """Resolve the Hyprland IPC socket path."""
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    uid = os.getuid()
    base = f"/run/user/{uid}/hypr"
    if sig and os.path.exists(f"{base}/{sig}/.socket.sock"):
        return f"{base}/{sig}/.socket.sock"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            sock = f"{base}/{d}/.socket.sock"
            if os.path.exists(sock):
                return sock
    raise FileNotFoundError("Hyprland socket not found")


# Module-level state: persistent connection
_socket_path: str | None = None
_socket: socket.socket | None = None
_lock = threading.Lock()


def _get_socket_path() -> str:
    global _socket_path
    if _socket_path is None:
        _socket_path = _hypr_socket_path()
    return _socket_path


def _connect() -> socket.socket:
    """Create a new connection to Hyprland IPC socket."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(_get_socket_path())
    return s


def send(command: str) -> str:
    """Send a command to Hyprland IPC socket, return response string.

    Uses persistent connection when possible, reconnects on error.
    Thread-safe via lock.
    """
    global _socket
    with _lock:
        # Try persistent socket first
        if _socket is not None:
            try:
                _socket.sendall(command.encode())
                _socket.shutdown(socket.SHUT_WR)
                resp = b""
                while True:
                    chunk = _socket.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                _socket.close()
                _socket = None  # Hyprland closes after one request-response
                return resp.decode().strip()
            except Exception as e:
                log.debug("persistent socket failed, reconnecting: %s", e)
                with contextlib.suppress(Exception):
                    _socket.close()
                _socket = None

        # Fresh connection
        s = _connect()
        try:
            s.sendall(command.encode())
            s.shutdown(socket.SHUT_WR)
            resp = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                resp += chunk
            s.close()
            return resp.decode().strip()
        finally:
            s.close()


def eval_lua(lua: str) -> str:
    """Execute Lua code via Hyprland eval. Returns response string."""
    return send(f"eval {lua}")


def get_cursor_pos() -> tuple[int, int]:
    """Query cursor position. Returns (x, y)."""
    resp = send("cursorpos")
    parts = resp.split(", ")
    return int(parts[0]), int(parts[1])
