"""Main entry point for Launchpad."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from .config import LaunchpadConfig
from .desktop import load_entries
from .widgets import LaunchpadWindow


def main() -> int:
    """Entry point for the Launchpad application."""
    app = QApplication(sys.argv)

    config = LaunchpadConfig.load()
    entries = load_entries(config.hidden_desktop_ids)

    background_path = resolve_background_path(config)
    background_pixmap = QPixmap()
    if background_path:
        background_pixmap = QPixmap(background_path)

    if background_pixmap.isNull():
        wallpaper = get_system_wallpaper()
        if wallpaper:
            background_pixmap = QPixmap(wallpaper)

    text_color = choose_text_color(background_pixmap)

    window = LaunchpadWindow(entries, config, background_pixmap, text_color)
    window.show()

    return app.exec()


def resolve_background_path(config: LaunchpadConfig) -> Optional[str]:
    path = config.background_image
    if path and Path(path).exists():
        return path
    if not path:
        default_path = Path.home() / ".config/lpad/background.jpg"
        if default_path.exists():
            return str(default_path)
    return None


def get_system_wallpaper() -> Optional[str]:
    commands = [
        [
            "gsettings",
            "get",
            "org.gnome.desktop.background",
            "picture-uri",
        ],
        [
            "gsettings",
            "get",
            "org.cinnamon.desktop.background",
            "picture-uri",
        ],
    ]
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if result.returncode == 0 and result.stdout:
            uri = result.stdout.strip().strip("'\"")
            if uri.startswith("file://"):
                path = uri[7:]
                if os.path.exists(path):
                    return path
            elif os.path.exists(uri):
                return uri
    return None


def choose_text_color(pixmap: QPixmap) -> QColor:
    if pixmap.isNull():
        return QColor(Qt.black)
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    if image.isNull():
        return QColor(Qt.black)
    total = 0
    sample_count = 0
    step_x = max(1, image.width() // 100)
    step_y = max(1, image.height() // 100)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            color = QColor(image.pixel(x, y))
            luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
            total += luminance
            sample_count += 1
    if sample_count == 0:
        return QColor(Qt.black)
    avg = total / sample_count
    return QColor(Qt.black) if avg > 128 else QColor(Qt.white)


if __name__ == "__main__":
    sys.exit(main())
