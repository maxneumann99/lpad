#!/usr/bin/env python3
"""Command-line control utility for the LPAD daemon."""

from __future__ import annotations

import argparse
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

INSTALL_ROOT = Path.home() / ".local/apps/lpad"
SOCKET_PATH = Path.home() / ".local/run/lpad-daemon.sock"
AUTOSTART_DIR = Path.home() / ".config/autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "lpad-daemon.desktop"


def _resolve_paths(candidates: Iterable[Path]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def _daemon_path() -> Path | None:
    local = Path(__file__).resolve().parent / "lpad-daemon.py"
    installed = INSTALL_ROOT / "lpad-daemon.py"
    return _resolve_paths([local, installed])


def _launchpad_path() -> Path | None:
    local = Path(__file__).resolve().parent / "launchpad.py"
    installed = INSTALL_ROOT / "launchpad.py"
    return _resolve_paths([local, installed])


def _start_daemon_process() -> tuple[bool, str]:
    daemon = _daemon_path()
    if daemon is None:
        return False, "Cannot locate lpad-daemon.py"
    try:
        subprocess.Popen(
            [sys.executable, str(daemon)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        return False, f"Failed to start daemon: {exc}"
    return True, "Daemon starting"


def _send_command(command: str, retries: int = 1, delay: float = 0.2) -> tuple[bool, str]:
    for attempt in range(retries + 1):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(str(SOCKET_PATH))
                sock.sendall(command.encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
                chunks: list[bytes] = []
                while True:
                    data = sock.recv(4096)
                    if not data:
                        break
                    chunks.append(data)
                response = b"".join(chunks).decode("utf-8", errors="ignore").strip()
                if not response:
                    return False, "No response from daemon"
                if response.upper().startswith("OK"):
                    return True, response
                return False, response
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            if attempt >= retries:
                return False, "Unable to reach daemon"
            time.sleep(delay)
    return False, "Unable to reach daemon"


def _ensure_daemon_running() -> tuple[bool, str]:
    ok, _ = _send_command("ping", retries=0)
    if ok:
        return True, "Daemon already running"
    started, message = _start_daemon_process()
    if not started:
        return False, message
    # Give the daemon a moment to create the socket before retrying.
    time.sleep(0.5)
    ok, response = _send_command("ping", retries=2)
    if not ok:
        return False, response
    return True, "Daemon started"


def _enable_autostart() -> tuple[bool, str]:
    daemon = _daemon_path()
    if daemon is None:
        return False, "Cannot locate lpad-daemon.py"

    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    exec_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(daemon))}"
    content = """[Desktop Entry]
Type=Application
Name=LPAD Daemon
Exec={exec_cmd}
X-GNOME-Autostart-enabled=true
""".format(exec_cmd=exec_cmd)

    try:
        AUTOSTART_FILE.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, f"Failed to write autostart file: {exc}"
    return True, f"Autostart enabled via {AUTOSTART_FILE}"


def _disable_autostart() -> tuple[bool, str]:
    if not AUTOSTART_FILE.exists():
        return True, "Autostart already disabled"
    try:
        AUTOSTART_FILE.unlink()
    except OSError as exc:
        return False, f"Failed to remove autostart file: {exc}"
    return True, "Autostart disabled"


def _print_result(result: tuple[bool, str]) -> int:
    ok, message = result
    stream = sys.stdout if ok else sys.stderr
    print(message, file=stream)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Control the LPAD daemon")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--open", action="store_true", help="Open the launchpad UI")
    group.add_argument("--start", action="store_true", help="Start the LPAD daemon")
    group.add_argument("--enable", action="store_true", help="Enable daemon autostart")
    group.add_argument("--disable", action="store_true", help="Disable daemon autostart")

    args = parser.parse_args()

    if args.open:
        if _launchpad_path() is None:
            print("launchpad.py is not installed", file=sys.stderr)
            return 1
        ok, message = _ensure_daemon_running()
        if not ok:
            print(message, file=sys.stderr)
            return 1
        ok, message = _send_command("open", retries=2)
        if not ok:
            print(message, file=sys.stderr)
            return 1
        print(message)
        return 0

    if args.start:
        return _print_result(_ensure_daemon_running())

    if args.enable:
        return _print_result(_enable_autostart())

    if args.disable:
        return _print_result(_disable_autostart())

    return 0


if __name__ == "__main__":
    sys.exit(main())
