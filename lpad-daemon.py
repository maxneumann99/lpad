#!/usr/bin/env python3
"""Background helper that listens for launchpad commands over a UNIX socket."""

from __future__ import annotations

import atexit
import os
import signal
import socket
import sys
import subprocess
import time
from pathlib import Path

INSTALL_ROOT = Path.home() / ".local/apps/lpad"
LAUNCHPAD_SCRIPT = INSTALL_ROOT / "launchpad.py"
SOCKET_PATH = Path.home() / ".local/run/lpad-daemon.sock"
CONTROL_SOCKET_PATH = Path.home() / ".local/run/lpad-ui.sock"
BUFFER_SIZE = 4096


def _resolve_launchpad_script() -> Path | None:
    """Return the best available path to launchpad.py."""

    local = Path(__file__).resolve().parent / "launchpad.py"
    for candidate in (LAUNCHPAD_SCRIPT, local):
        if candidate.exists():
            return candidate
    return None


class LpadDaemon:
    """Daemon process that launches the LPAD UI on request."""

    def __init__(self) -> None:
        self._running = True
        self._launchpad_process: subprocess.Popen | None = None

    def _cleanup_socket(self) -> None:
        try:
            if SOCKET_PATH.exists():
                SOCKET_PATH.unlink()
        except OSError:
            pass

    def _handle_signal(self, _signum, _frame) -> None:
        self._running = False

    def _launchpad_running(self) -> bool:
        return self._launchpad_process is not None and self._launchpad_process.poll() is None

    def _terminate_launchpad(self) -> None:
        process = self._launchpad_process
        if not process:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
            except OSError:
                pass
        self._launchpad_process = None

    def _send_launchpad_command(
        self, command: str, retries: int = 0, delay: float = 0.2
    ) -> tuple[bool, str]:
        for attempt in range(retries + 1):
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect(str(CONTROL_SOCKET_PATH))
                    sock.sendall(command.encode("utf-8"))
                    sock.shutdown(socket.SHUT_WR)
                    chunks: list[bytes] = []
                    while True:
                        data = sock.recv(BUFFER_SIZE)
                        if not data:
                            break
                        chunks.append(data)
                    response = b"".join(chunks).decode("utf-8", errors="ignore").strip()
                    if not response:
                        return False, "Empty response from launchpad"
                    if response.upper().startswith("OK"):
                        return True, response
                    return False, response
            except (FileNotFoundError, ConnectionRefusedError, socket.error, OSError):
                if attempt >= retries:
                    return False, "Launchpad unreachable"
                time.sleep(delay)
        return False, "Launchpad unreachable"

    def _wait_for_launchpad_ready(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._launchpad_process and self._launchpad_process.poll() is not None:
                self._launchpad_process = None
                return False
            ok, _ = self._send_launchpad_command("ping", retries=0)
            if ok:
                return True
            time.sleep(0.2)
        return False

    def _start_launchpad(self) -> tuple[bool, str]:
        launchpad_script = _resolve_launchpad_script()
        if launchpad_script is None:
            return False, "Launchpad script not found"

        try:
            if CONTROL_SOCKET_PATH.exists():
                CONTROL_SOCKET_PATH.unlink()
        except OSError:
            pass

        try:
            self._launchpad_process = subprocess.Popen(
                [
                    sys.executable,
                    str(launchpad_script),
                    "--resident",
                    "--control-socket",
                    str(CONTROL_SOCKET_PATH),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            self._launchpad_process = None
            return False, f"Failed to start launchpad: {exc}"

        if not self._wait_for_launchpad_ready():
            self._terminate_launchpad()
            return False, "Launchpad did not become ready"
        return True, "Launchpad ready"

    def _ensure_launchpad(self) -> tuple[bool, str]:
        if self._launchpad_running():
            return True, "Launchpad already running"
        return self._start_launchpad()

    def _handle_open(self) -> tuple[bool, str]:
        ok, message = self._ensure_launchpad()
        if not ok:
            return False, message

        ok, response = self._send_launchpad_command("open", retries=2)
        if ok:
            return True, "Launchpad shown"

        # Retry once by restarting the launchpad process.
        self._terminate_launchpad()
        ok, message = self._start_launchpad()
        if not ok:
            return False, message
        ok, response = self._send_launchpad_command("open", retries=2)
        if ok:
            return True, "Launchpad shown"
        return False, response

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
        atexit.register(self._terminate_launchpad)

        # Start the launchpad process immediately so the UI is ready to show on demand.
        ready, message = self._ensure_launchpad()
        if not ready:
            print(f"lpad-daemon: {message}", file=sys.stderr)

        while self._running:
            try:
                client, _ = server.accept()
            except socket.timeout:
                if self._launchpad_process and self._launchpad_process.poll() is not None:
                    self._launchpad_process = None
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
        self._terminate_launchpad()
        return 0


def main() -> int:
    daemon = LpadDaemon()
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
