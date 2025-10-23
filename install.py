#!/usr/bin/env python3
"""Install helper that copies launchpad assets into the user application tree."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

INSTALL_ROOT = Path.home() / ".local/apps/lpad"
REPO_ROOT = Path(__file__).resolve().parent
FILES_TO_INSTALL = ["launchpad.py", "lpad-daemon.py", "lpad-command.py"]


def copy_file(source: Path, destination: Path) -> None:
    """Copy ``source`` to ``destination`` preserving metadata when possible."""

    if not source.exists():
        raise FileNotFoundError(f"Missing required file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> int:
    INSTALL_ROOT.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for name in FILES_TO_INSTALL:
        source = REPO_ROOT / name
        try:
            copy_file(source, INSTALL_ROOT / name)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        copied.append(name)

    print(f"Installed {', '.join(copied)} to {INSTALL_ROOT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
