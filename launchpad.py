"""A KDE-friendly launchpad application inspired by macOS Launchpad.

This module implements a fullscreen application grid that reads ``.desktop``
entries from the system and user application directories. Applications are
displayed in a 5x7 matrix per page, support navigation via mouse wheel and
keyboard arrows, and close the launchpad when an application is launched.
"""

from __future__ import annotations

import math
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColor
from PyQt5.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


APP_ROWS = 5
APP_COLUMNS = 7
APPS_PER_PAGE = APP_ROWS * APP_COLUMNS
APP_TILE_WIDTH = 140


@dataclass
class Application:
    """A representation of a desktop entry used by the launcher."""

    name: str
    exec: str
    icon_name: str
    desktop_file: Path


def _iter_desktop_files() -> Iterable[Path]:
    """Yield all ``.desktop`` files from system and user application folders."""

    search_paths = [
        Path("/usr/share/applications"),
        Path.home() / ".local/share/applications",
    ]

    for base in search_paths:
        if not base.exists():
            continue
        for path in base.rglob("*.desktop"):
            if path.is_file():
                yield path


def _parse_desktop_file(path: Path) -> Application | None:
    """Parse relevant information from a ``.desktop`` file."""

    name = None
    exec_cmd = None
    icon_name = None

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    # Only consider entries inside [Desktop Entry]
                    section = line.strip("[]").strip()
                    if section != "Desktop Entry":
                        break
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key == "Name" and not name:
                    name = value
                elif key == "Exec" and not exec_cmd:
                    exec_cmd = value
                elif key == "Icon" and not icon_name:
                    icon_name = value
    except OSError:
        return None

    if not name or not exec_cmd:
        return None

    return Application(name=name, exec=exec_cmd, icon_name=icon_name or "", desktop_file=path)


def load_applications() -> List[Application]:
    """Load and sort all applications available in desktop directories."""

    apps: List[Application] = []
    for desktop_file in _iter_desktop_files():
        app = _parse_desktop_file(desktop_file)
        if app:
            apps.append(app)

    apps.sort(key=lambda item: item.name.lower())
    return apps


def _clean_exec(exec_cmd: str) -> str:
    """Remove field codes (``%f``, ``%u`` …) from ``Exec`` commands."""

    return re.sub(r"%[fFuUdDnNickvm]", "", exec_cmd).strip()


def _launch_application(app: Application) -> None:
    """Launch the application described by a desktop entry."""

    command = _clean_exec(app.exec)
    if not command:
        return

    try:
        subprocess.Popen(shlex.split(command))
    except OSError:
        # Silently ignore launch issues; nothing more we can do.
        pass


def _read_kde_wallpaper() -> QPixmap | None:
    """Attempt to read the current KDE wallpaper image and return as pixmap."""

    def _from_file(path: Path) -> QPixmap | None:
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap
        return None

    # KDE Plasma 5 stores wallpaper configuration in this file.
    plasma_config = Path.home() / ".config/plasma-org.kde.plasma.desktop-appletsrc"
    if plasma_config.exists():
        try:
            data = plasma_config.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"^Image=(.+)$", data, flags=re.MULTILINE)
            for match in reversed(matches):
                pixmap = _from_file(Path(os.path.expanduser(match.strip())))
                if pixmap:
                    return pixmap
        except OSError:
            pass

    # KDE 4 fallback.
    kde4_config = Path.home() / ".kde4/share/config/plasma-desktop-appletsrc"
    if kde4_config.exists():
        try:
            data = kde4_config.read_text(encoding="utf-8", errors="ignore")
            matches = re.findall(r"^Image=(.+)$", data, flags=re.MULTILINE)
            for match in reversed(matches):
                pixmap = _from_file(Path(os.path.expanduser(match.strip())))
                if pixmap:
                    return pixmap
        except OSError:
            pass

    return None


class PageIndicator(QWidget):
    """Widget showing pagination dots representing available pages."""

    def __init__(self, page_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page_count = page_count
        self._current_page = 0
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setFixedHeight(20)

    def set_page_count(self, count: int) -> None:
        self._page_count = count
        self.update()

    def set_current_page(self, index: int) -> None:
        self._current_page = index
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = 6
        spacing = 16
        total_width = max(0, (self._page_count - 1) * spacing)
        start_x = (self.width() - total_width) // 2
        y = self.height() // 2

        active_color = QColor(45, 45, 45)
        inactive_color = QColor(210, 210, 210)

        for index in range(self._page_count):
            color = active_color if index == self._current_page else inactive_color
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            x = start_x + index * spacing
            painter.drawEllipse(x, y - radius // 2, radius, radius)


class ApplicationButton(QToolButton):
    """Button representing a single application entry."""

    def __init__(self, app: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setText(app.name)
        self.setIcon(self._create_icon(app.icon_name))
        self.setIconSize(QSize(64, 64))
        self.setAutoRaise(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(APP_TILE_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet(
            "QToolButton { padding: 10px; text-align: center; }\n"
            "QToolButton::menu-indicator { image: none; }"
        )
        self.clicked.connect(self._on_clicked)

    @staticmethod
    def _create_icon(icon_name: str) -> QIcon:
        if icon_name and os.path.isabs(icon_name) and os.path.exists(icon_name):
            return QIcon(icon_name)
        if icon_name:
            icon = QIcon.fromTheme(icon_name)
            if not icon.isNull():
                return icon
        return QApplication.style().standardIcon(QApplication.style().SP_DesktopIcon)

    def _on_clicked(self) -> None:
        _launch_application(self._app)
        window = self.window()
        if window:
            window.close()


class LaunchpadWindow(QWidget):
    """Main fullscreen window containing the paginated application grid."""

    def __init__(self, apps: List[Application]) -> None:
        super().__init__()
        self._apps = apps
        self._background = _read_kde_wallpaper()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)

        self._page_indicator = PageIndicator(max(1, math.ceil(len(apps) / APPS_PER_PAGE)))
        self._stack = QStackedWidget()

        self._create_pages()

        left_button = QPushButton("◀")
        right_button = QPushButton("▶")
        for button in (left_button, right_button):
            button.setFixedSize(60, 60)
            button.setStyleSheet(
                "background-color: rgba(0, 0, 0, 0.4); color: white; border: none; border-radius: 30px;"
            )

        left_button.clicked.connect(self.previous_page)
        right_button.clicked.connect(self.next_page)

        content_layout = QHBoxLayout()
        content_layout.addWidget(left_button, alignment=Qt.AlignVCenter)
        content_layout.addWidget(self._stack, stretch=1)
        content_layout.addWidget(right_button, alignment=Qt.AlignVCenter)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(60, 60, 60, 40)
        main_layout.setSpacing(20)
        main_layout.addStretch()
        main_layout.addLayout(content_layout)
        main_layout.addWidget(self._page_indicator, alignment=Qt.AlignCenter)
        main_layout.addStretch()

        self._update_page_indicator()

    def _create_pages(self) -> None:
        for index in range(0, len(self._apps), APPS_PER_PAGE):
            page_apps = self._apps[index : index + APPS_PER_PAGE]
            page_widget = QWidget()
            grid = QGridLayout(page_widget)
            grid.setContentsMargins(40, 40, 40, 40)
            grid.setSpacing(30)

            for position, app in enumerate(page_apps):
                row = position // APP_COLUMNS
                column = position % APP_COLUMNS
                button = ApplicationButton(app)
                grid.addWidget(button, row, column)

            # Fill remaining cells with spacers for consistent layout.
            total_cells = APP_ROWS * APP_COLUMNS
            for position in range(len(page_apps), total_cells):
                row = position // APP_COLUMNS
                column = position % APP_COLUMNS
                spacer = QWidget()
                spacer.setFixedWidth(APP_TILE_WIDTH)
                spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
                grid.addWidget(spacer, row, column)

            self._stack.addWidget(page_widget)

        if self._stack.count() == 0:
            empty_widget = QWidget()
            message = QLabel("No applications found")
            message.setAlignment(Qt.AlignCenter)
            layout = QVBoxLayout(empty_widget)
            layout.addWidget(message)
            self._stack.addWidget(empty_widget)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._background and not self._background.isNull():
            painter = QPainter(self)
            pixmap = self._background.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = (self.width() - pixmap.width()) // 2
            y = (self.height() - pixmap.height()) // 2
            painter.drawPixmap(x, y, pixmap)
        else:
            super().paintEvent(event)

    def next_page(self) -> None:
        if self._stack.count() <= 1:
            return
        current = self._stack.currentIndex()
        self._stack.setCurrentIndex((current + 1) % self._stack.count())
        self._update_page_indicator()

    def previous_page(self) -> None:
        if self._stack.count() <= 1:
            return
        current = self._stack.currentIndex()
        self._stack.setCurrentIndex((current - 1) % self._stack.count())
        self._update_page_indicator()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.angleDelta().y() < 0:
            self.next_page()
        elif event.angleDelta().y() > 0:
            self.previous_page()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.next_page()
        elif event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.previous_page()
        elif event.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)

    def _update_page_indicator(self) -> None:
        self._page_indicator.set_page_count(self._stack.count())
        self._page_indicator.set_current_page(self._stack.currentIndex())


def main() -> None:
    apps = load_applications()
    app = QApplication(sys.argv)
    window = LaunchpadWindow(apps)
    window.showFullScreen()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

