import threading
import time

from canvas.ipc import IpcServer, send_command


def test_ipc_ping_pong(tmp_path):
    """Server responds to PING with PONG."""
    sock_path = str(tmp_path / "test.sock")
    received = []
    server = IpcServer(
        sock_path,
        lambda cmd: received.append(cmd) or "PONG" if cmd == "PING" else "OK",
    )

    server_thread = threading.Thread(target=server.serve, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    response = send_command("PING", sock_path=sock_path)
    assert response == "PONG"
    assert "PING" in received

    server.stop()
    server_thread.join(timeout=2)


def test_ipc_nav_command(tmp_path):
    """Server receives NAV_LEFT and returns OK."""
    sock_path = str(tmp_path / "test2.sock")
    received = []
    server = IpcServer(sock_path, lambda cmd: (received.append(cmd), "OK")[1])

    server_thread = threading.Thread(target=server.serve, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    response = send_command("NAV_LEFT", sock_path=sock_path)
    assert response == "OK"
    assert received == ["NAV_LEFT"]

    server.stop()
    server_thread.join(timeout=2)


def test_ipc_toggle_returns_state(tmp_path):
    """Server receives TOGGLE and returns the new state."""
    sock_path = str(tmp_path / "test3.sock")
    inverted = [False]

    def handler(cmd):
        if cmd == "TOGGLE":
            inverted[0] = not inverted[0]
            return "INVERTED" if inverted[0] else "NORMAL"
        elif cmd == "STATUS":
            return "INVERTED" if inverted[0] else "NORMAL"
        return "OK"

    server = IpcServer(sock_path, handler)
    server_thread = threading.Thread(target=server.serve, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    r1 = send_command("TOGGLE", sock_path=sock_path)
    assert r1 == "INVERTED"
    r2 = send_command("STATUS", sock_path=sock_path)
    assert r2 == "INVERTED"
    r3 = send_command("TOGGLE", sock_path=sock_path)
    assert r3 == "NORMAL"

    server.stop()
    server_thread.join(timeout=2)
