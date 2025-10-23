#!/usr/bin/env python3
"""Background helper that listens for launchpad commands over a UNIX socket."""

from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

INSTALL_ROOT = Path.home() / ".local/apps/lpad"
LAUNCHPAD_SCRIPT = INSTALL_ROOT / "launchpad.py"
SOCKET_PATH = Path.home() / ".local/run/lpad-daemon.sock"
BUFFER_SIZE = 4096


class LpadDaemon:
    """Daemon process that launches the LPAD UI on request."""

    def __init__(self) -> None:
        self._running = True

    def _cleanup_socket(self) -> None:
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
        except OSError:
            pass

    def _handle_signal(self, _signum, _frame) -> None:
        self._running = False

    def _handle_open(self) -> tuple[bool, str]:
        if not LAUNCHPAD_SCRIPT.exists():
            return False, f"Launchpad script missing: {LAUNCHPAD_SCRIPT}"
        try:
            subprocess.Popen(
                [sys.executable, str(LAUNCHPAD_SCRIPT)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            return False, f"Failed to start launchpad: {exc}"
        return True, "Launchpad started"

    def _handle_ping(self) -> tuple[bool, str]:
        return True, "pong"

    def _process_command(self, command: str) -> str:
        normalized = command.strip().lower()
        if not normalized:
            return "ERROR Empty command"
        if normalized == "open":
            success, message = self._handle_open()
        elif normalized == "ping":
            success, message = self._handle_ping()
        else:
            success, message = False, f"Unknown command: {command}"
        status = "OK" if success else "ERROR"
        return f"{status} {message}"

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

        if SOCKET_PATH.exists():
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.connect(str(SOCKET_PATH))
                print("lpad-daemon: already running", file=sys.stderr)
                return 0
            except OSError:
                self._cleanup_socket()

        try:
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(SOCKET_PATH))
            os.chmod(str(SOCKET_PATH), 0o600)
            server.listen(5)
            server.settimeout(1.0)
        except OSError as exc:
            print(f"Unable to start daemon socket: {exc}", file=sys.stderr)
            self._cleanup_socket()
            return 1

        atexit.register(self._cleanup_socket)

        while self._running:
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            with client:
                chunks: list[bytes] = []
                while True:
                    try:
                        data = client.recv(BUFFER_SIZE)
                    except OSError:
                        chunks = []
                        break
                    if not data:
                        break
                    chunks.append(data)
                command = b"".join(chunks).decode("utf-8", errors="ignore")
                response = self._process_command(command)
                try:
                    client.sendall(response.encode("utf-8"))
                except OSError:
                    pass

        try:
            server.close()
        except OSError:
            pass
        self._cleanup_socket()
        return 0


def main() -> int:
    daemon = LpadDaemon()
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
