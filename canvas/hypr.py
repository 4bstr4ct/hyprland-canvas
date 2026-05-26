"""Direct Hyprland IPC via Unix socket — no subprocess needed.

Hyprland's .socket.sock speaks a one-shot request-response protocol:
  connect → send b"<command>" → read response until EOF → close

The server closes the connection after each response, so we can't
multiplex multiple commands on one socket. The module keeps a
pre-connected socket for zero-latency first send and reconnects
automatically on error. Thread-safe via lock.

WARNING: Hyprland processes IPC synchronously — an unclosed connection
freezes the compositor for up to 5 seconds. Always close promptly.
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


class HyprIPC:
    """Thread-safe Hyprland IPC client with persistent socket.

    Encapsulates connection state as instance attributes instead of
    module globals, enabling testability via dependency injection.
    """

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "HyprIPC":
        """Create HyprIPC by auto-detecting socket path from environment."""
        return cls(_hypr_socket_path())

    def _connect(self) -> socket.socket:
        """Create a new connection to Hyprland IPC socket."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self._socket_path)
        return s

    def _recv_response(self, s: socket.socket) -> str:
        """Read full response from socket until server closes."""
        resp = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        return resp.decode().strip()

    def send(self, command: str) -> str:
        """Send a command to Hyprland IPC socket, return response string.

        Uses persistent connection when possible, reconnects on error.
        Thread-safe via lock.
        """
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.sendall(command.encode())
                    self._socket.shutdown(socket.SHUT_WR)
                    resp = self._recv_response(self._socket)
                    self._socket.close()
                    self._socket = None
                    return resp
                except Exception as e:
                    log.debug("persistent socket failed, reconnecting: %s", e)
                    with contextlib.suppress(Exception):
                        self._socket.close()
                    self._socket = None

            s = self._connect()
            try:
                s.sendall(command.encode())
                s.shutdown(socket.SHUT_WR)
                return self._recv_response(s)
            finally:
                s.close()

    def eval_lua(self, lua: str) -> str:
        """Execute Lua code via Hyprland eval. Returns response string."""
        return self.send(f"eval {lua}")

    def get_cursor_pos(self) -> tuple[int, int]:
        """Query cursor position. Returns (x, y)."""
        resp = self.send("cursorpos")
        parts = resp.split(", ")
        return int(parts[0]), int(parts[1])


_default: HyprIPC | None = None
_default_lock = threading.Lock()


def _get_default() -> HyprIPC:
    """Get or create the default module-level HyprIPC instance."""
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = HyprIPC.from_env()
    return _default


def send(command: str) -> str:
    """Send a command via the default HyprIPC instance."""
    return _get_default().send(command)


def eval_lua(lua: str) -> str:
    """Execute Lua code via the default HyprIPC instance."""
    return _get_default().eval_lua(lua)


def get_cursor_pos() -> tuple[int, int]:
    """Query cursor position via the default HyprIPC instance."""
    return _get_default().get_cursor_pos()
