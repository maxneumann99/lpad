"""Utilities for loading and launching desktop entry files."""
from __future__ import annotations

from dataclasses import dataclass
import configparser
import os
import shlex
import subprocess
from typing import Iterable, List, Optional

DESKTOP_DIRS = [
    "/usr/share/applications",
    os.path.expanduser("~/.local/share/applications"),
]


@dataclass
class DesktopEntry:
    """Representation of a parsed ``.desktop`` file."""

    id: str
    name: str
    comment: str
    exec: str
    icon: Optional[str]
    terminal: bool

    @property
    def command(self) -> List[str]:
        """Return the command suitable for :func:`subprocess.Popen`.

        The Exec field can contain placeholders like ``%u`` or ``%f``.
        The specification states that these placeholders should be removed
        when launching an application without additional parameters.
        """

        fields_to_strip = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N"}
        parts = shlex.split(self.exec, posix=True)
        return [part for part in parts if part not in fields_to_strip]


def iter_desktop_files() -> Iterable[str]:
    """Yield all desktop files from known application directories."""

    for base_dir in DESKTOP_DIRS:
        if not os.path.isdir(base_dir):
            continue
        for root, _dirs, files in os.walk(base_dir):
            for filename in files:
                if filename.endswith(".desktop"):
                    yield os.path.join(root, filename)


def load_desktop_entry(path: str) -> Optional[DesktopEntry]:
    """Parse the provided ``.desktop`` file.

    Only applications (``Type=Application``) with an ``Exec`` command are
    returned. Hidden applications are ignored.
    """

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None

    if not parser.has_section("Desktop Entry"):
        return None

    section = parser["Desktop Entry"]
    if section.get("Type", "Application").lower() != "application":
        return None

    if section.getboolean("NoDisplay", fallback=False):
        return None

    exec_command = section.get("Exec")
    if not exec_command:
        return None

    desktop_id = os.path.splitext(os.path.basename(path))[0]
    name = section.get("Name", desktop_id)
    comment = section.get("Comment", "")
    icon = section.get("Icon")
    terminal = section.getboolean("Terminal", fallback=False)

    return DesktopEntry(
        id=desktop_id,
        name=name,
        comment=comment,
        exec=exec_command,
        icon=icon,
        terminal=terminal,
    )


def load_entries() -> List[DesktopEntry]:
    """Return a sorted list of available desktop entries."""

    entries = [entry for path in iter_desktop_files() if (entry := load_desktop_entry(path))]
    entries.sort(key=lambda item: (item.name.lower(), item.id.lower()))
    return entries


def launch_entry(entry: DesktopEntry) -> None:
    """Launch the application represented by ``entry``."""

    command = entry.command
    if not command:
        return

    env = os.environ.copy()
    if entry.terminal:
        terminal_emulators = [
            ["x-terminal-emulator", "-e"],
            ["gnome-terminal", "--"],
            ["konsole", "-e"],
            ["xfce4-terminal", "-e"],
        ]
        for emulator in terminal_emulators:
            try:
                subprocess.Popen(emulator + command, env=env)
                return
            except FileNotFoundError:
                continue
    else:
        try:
            subprocess.Popen(command, env=env)
        except FileNotFoundError:
            # Fallback to gtk-launch if the binary is not found directly
            try:
                subprocess.Popen(["gtk-launch", entry.id], env=env)
            except FileNotFoundError:
                pass
