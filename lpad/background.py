from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from PyQt5.QtGui import QImage, QPixmap

from .config import Config


class Background:
    """Represents the launcher's background image or color."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = self._resolve_background_path()
        self.pixmap: Optional[QPixmap] = self._load_pixmap(self.path) if self.path else None

    def _resolve_background_path(self) -> Optional[Path]:
        if self.config.background:
            path = Path(os.path.expanduser(self.config.background))
            if path.exists():
                return path
        wallpaper = _detect_wallpaper()
        if wallpaper and wallpaper.exists():
            return wallpaper
        return None

    def luminance(self) -> float:
        if not self.pixmap or self.pixmap.isNull():
            # Assume bright (white) background when no image.
            return 1.0
        image = self.pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        sample = image.scaled(200, 200)
        total = 0.0
        count = sample.width() * sample.height()
        if count == 0:
            return 1.0
        for x in range(sample.width()):
            for y in range(sample.height()):
                pixel = sample.pixelColor(x, y)
                total += 0.2126 * pixel.redF() + 0.7152 * pixel.greenF() + 0.0722 * pixel.blueF()
        return total / count

    @staticmethod
    def _load_pixmap(path: Optional[Path]) -> Optional[QPixmap]:
        if not path:
            return None
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap


def _detect_wallpaper() -> Optional[Path]:
    try:
        output = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    if not output:
        return None

    # gsettings returns URIs like 'file:///path/to/image'.
    if output.startswith("'") and output.endswith("'"):
        output = output[1:-1]

    if output.startswith("file://"):
        output = output[7:]

    path = Path(output)
    return path if path.exists() else None
