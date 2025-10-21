"""Desktop entry discovery utilities."""
from __future__ import annotations

import configparser
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from PySide6.QtGui import QIcon

DESKTOP_DIRS = [
    Path("/usr/share/applications"),
    Path.home() / ".local/share/applications",
]


@dataclass
class DesktopEntry:
    """Representation of a .desktop entry."""

    desktop_id: str
    name: str
    exec_cmd: str
    icon_name: Optional[str]
    icon_path: Optional[str]
    categories: List[str]

    def launch(self) -> None:
        """Launch the application using its Exec command."""
        if not self.exec_cmd:
            return
        command = substitute_exec(self.exec_cmd)
        if not command:
            return
        subprocess.Popen(command, shell=False)


def substitute_exec(exec_line: str) -> List[str]:
    """Expand Exec line placeholders from desktop entry."""
    # Simplified placeholder handling
    tokens = shlex.split(exec_line)
    result: List[str] = []
    for token in tokens:
        if token == "%u" or token == "%U" or token == "%f" or token == "%F":
            continue
        if token == "%k":
            continue
        if token.startswith("%%"):
            token = token.replace("%%", "%")
        result.append(token)
    return result


def iter_desktop_files() -> Iterable[Path]:
    """Yield all .desktop files in known directories."""
    for base in DESKTOP_DIRS:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.desktop")):
            if path.is_file():
                yield path


def load_desktop_entry(path: Path) -> Optional[DesktopEntry]:
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

    name = section.get("Name")
    exec_cmd = section.get("Exec")
    if not name or not exec_cmd:
        return None

    desktop_id = path.stem
    icon = section.get("Icon")
    categories = [c for c in section.get("Categories", "").split(";") if c]

    icon_path = resolve_icon_path(icon) if icon else None

    return DesktopEntry(
        desktop_id=desktop_id,
        name=name,
        exec_cmd=exec_cmd,
        icon_name=icon,
        icon_path=icon_path,
        categories=categories,
    )


def resolve_icon_path(icon_name: Optional[str]) -> Optional[str]:
    if not icon_name:
        return None
    if os.path.isabs(icon_name) and os.path.exists(icon_name):
        return icon_name
    icon = QIcon.fromTheme(icon_name)
    if not icon.isNull():
        # Grab first available size
        for size in (64, 128, 256, 512):
            pixmap = icon.pixmap(size, size)
            if not pixmap.isNull():
                image = pixmap.toImage()
                temp_path = os.path.join(Path.home(), ".cache", "lpad")
                os.makedirs(temp_path, exist_ok=True)
                output = os.path.join(temp_path, f"{icon_name}_{size}.png")
                image.save(output)
                return output
    # Try lookup relative to pixmaps
    possible = [
        Path("/usr/share/pixmaps") / icon_name,
        Path("/usr/share/pixmaps") / f"{icon_name}.png",
        Path("/usr/share/pixmaps") / f"{icon_name}.svg",
    ]
    for candidate in possible:
        if candidate.exists():
            return str(candidate)
    return None


def load_entries(hidden_ids: Iterable[str]) -> List[DesktopEntry]:
    entries: List[DesktopEntry] = []
    for path in iter_desktop_files():
        entry = load_desktop_entry(path)
        if not entry:
            continue
        if entry.desktop_id in hidden_ids:
            continue
        entries.append(entry)
    entries.sort(key=lambda e: e.name.lower())
    return entries
