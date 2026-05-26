"""Unix domain socket IPC for canvas daemon.

Server runs in daemon, listens for commands.
Client (canvas-ctl) sends a command, gets a response, exits.

Security:
- SO_PEERCRED: rejects connections from processes with different UID
- Symlink check: refuses to bind if socket path is a symlink
"""

import logging
import os
import socket
import struct
import threading
from collections.abc import Callable

log = logging.getLogger("canvas.ipc")

_MY_UID = os.getuid()


def _default_socket_path() -> str:
    uid = os.getuid()
    run_dir = f"/run/user/{uid}"
    if os.path.isdir(run_dir):
        return os.path.join(run_dir, "canvas.sock")
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/user/{uid}"), "canvas.sock")


def _get_peer_uid(conn: socket.socket) -> int | None:
    """Get the UID of the process on the other end of a Unix socket.

    Uses SO_PEERCRED (Linux). Returns None on unsupported platforms.
    """
    try:
        cred = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iii"))
        _, uid, _ = struct.unpack("iii", cred)
        return int(uid)
    except (AttributeError, OSError):
        return None


class IpcServer:
    """Threaded Unix domain socket server.

    Args:
        sock_path: Path to the socket file.
        handler: Callable that receives a command string and returns a response string.
    """

    def __init__(self, sock_path: str | None = None, handler: Callable[[str], str] | None = None):
        self.sock_path = sock_path or _default_socket_path()
        self._handler = handler or (lambda cmd: "OK")
        self._stop_event = threading.Event()
        self._server_socket: socket.socket | None = None

    def serve(self) -> None:
        """Start listening. Blocks until stop() is called."""
        if os.path.islink(self.sock_path):
            log.error("socket path is a symlink, refusing to bind: %s", self.sock_path)
            return

        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(self.sock_path)
        self._server_socket.listen(5)
        self._server_socket.settimeout(0.5)

        while not self._stop_event.is_set():
            try:
                conn, _ = self._server_socket.accept()
            except TimeoutError:
                continue

            peer_uid = _get_peer_uid(conn)
            if peer_uid is not None and peer_uid != _MY_UID:
                log.warning("rejected IPC connection from uid %d", peer_uid)
                conn.close()
                continue

            try:
                data = conn.recv(1024)
                if data:
                    cmd = data.decode("utf-8").strip()
                    response = self._handler(cmd)
                    conn.sendall(response.encode("utf-8"))
            except Exception as e:
                log.debug("IPC client handler error: %s", e)
            finally:
                conn.close()

        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

    def stop(self) -> None:
        """Signal the server to stop."""
        self._stop_event.set()


def send_command(cmd: str, sock_path: str | None = None) -> str:
    """Send a command to the canvas daemon and return the response."""
    path = sock_path or _default_socket_path()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(path)
        sock.sendall(cmd.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)

        response = b""
        while True:
            chunk = sock.recv(1024)
            if not chunk:
                break
            response += chunk

        return response.decode("utf-8")
    except ConnectionRefusedError:
        return "ERROR: daemon not running"
    except FileNotFoundError:
        return "ERROR: socket not found — is canvasd running?"
    finally:
        sock.close()
