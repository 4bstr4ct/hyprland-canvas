"""Tests for canvas.__main__ — CLI entry points."""

import sys
from unittest.mock import patch

from canvas.__main__ import ctl_main, daemon_main


def test_ctl_main_no_args_exits():
    with patch.object(sys, "argv", ["canvas-ctl"]):
        try:
            ctl_main()
            raise AssertionError("should have exited")
        except SystemExit as e:
            assert e.code == 1


def test_ctl_main_unknown_command_exits():
    with patch.object(sys, "argv", ["canvas-ctl", "foobar"]):
        try:
            ctl_main()
            raise AssertionError("should have exited")
        except SystemExit as e:
            assert e.code == 1


def test_ctl_main_valid_command_sends():
    """canvas-ctl with valid command calls send_command and prints response."""
    with (
        patch.object(sys, "argv", ["canvas-ctl", "ping"]),
        patch("canvas.ipc.send_command", return_value="PONG") as mock_send,
        patch("builtins.print") as mock_print,
    ):
        ctl_main()
        mock_send.assert_called_with("PING")
        mock_print.assert_called_with("PONG")


def test_ctl_main_edge_start_command():
    """canvas-ctl edge-start normalizes to EDGE_START."""
    with (
        patch.object(sys, "argv", ["canvas-ctl", "edge-start"]),
        patch("canvas.ipc.send_command", return_value="EDGE_ON") as mock_send,
    ):
        ctl_main()
        mock_send.assert_called_with("EDGE_START")


def test_ctl_main_no_response_no_print():
    """canvas-ctl doesn't print if send_command returns empty string."""
    with (
        patch.object(sys, "argv", ["canvas-ctl", "ping"]),
        patch("canvas.ipc.send_command", return_value=""),
        patch("builtins.print") as mock_print,
    ):
        ctl_main()
        mock_print.assert_not_called()


def test_daemon_main_calls_run():
    """daemon_main delegates to daemon.run()."""
    with patch("canvas.daemon.run") as mock_run:
        daemon_main()
        mock_run.assert_called_once()
