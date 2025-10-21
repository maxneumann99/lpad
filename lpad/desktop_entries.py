from __future__ import annotations

import configparser
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path.home() / ".local" / "share" / "applications",
]


@dataclass
class DesktopEntry:
    name: str
    exec_line: str
    icon: Optional[str]
    desktop_file: Path

    def command(self) -> str:
        return _clean_exec(self.exec_line)

    def launch(self) -> None:
        cmd = self.command()
        if not cmd:
            return
        subprocess.Popen(cmd, shell=True)


def _clean_exec(exec_line: str) -> str:
    if not exec_line:
        return ""
    # Drop desktop entry field codes such as %U, %f, etc.
    cleaned = []
    for token in shlex.split(exec_line, posix=True):
        if token.startswith("%"):
            continue
        cleaned.append(token)
    if not cleaned:
        return ""
    return " ".join(shlex.quote(part) if any(ch.isspace() for ch in part) else part for part in cleaned)


def _parse_desktop(path: Path) -> Optional[DesktopEntry]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        return None

    if "Desktop Entry" not in parser:
        return None
    section = parser["Desktop Entry"]

    if section.get("NoDisplay", "false").lower() == "true":
        return None
    if section.get("Hidden", "false").lower() == "true":
        return None

    name = section.get("Name")
    exec_line = section.get("Exec")
    icon = section.get("Icon")

    if not name or not exec_line:
        return None

    return DesktopEntry(name=name, exec_line=exec_line, icon=icon, desktop_file=path)


def iter_desktop_entries(hidden: Iterable[str] = ()) -> List[DesktopEntry]:
    hidden_set = {os.path.abspath(h) for h in hidden}
    entries: List[DesktopEntry] = []
    for base_dir in DESKTOP_DIRS:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*.desktop")):
            abs_path = os.path.abspath(path)
            if abs_path in hidden_set:
                continue
            entry = _parse_desktop(path)
            if entry:
                entries.append(entry)
    entries.sort(key=lambda e: e.name.lower())
    return entries
