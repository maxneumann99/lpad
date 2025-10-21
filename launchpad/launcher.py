"""Graphical launcher application similar to macOS Launchpad."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set
from urllib.parse import unquote, urlparse

from PyQt5 import QtCore, QtGui, QtWidgets

from .desktop_entry import DesktopEntry, load_entries, launch_entry

APP_NAME = "launchpad"
_config_root = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
CONFIG_DIR = _config_root / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
HIDDEN_FILE = CONFIG_DIR / "hidden.json"

ROWS = 7
COLUMNS = 5
PAGE_SIZE = ROWS * COLUMNS


def load_config() -> dict:
    """Load the launchpad configuration file if it exists."""

    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def resolve_background_image(config: dict) -> Optional[Path]:
    """Determine which image should be used for the background."""

    configured = config.get("background_image") if isinstance(config, dict) else None
    path = _normalise_path(configured)
    if path is not None:
        return path

    detected = detect_current_wallpaper()
    if detected is not None and detected.exists():
        return detected
    return None


def detect_current_wallpaper() -> Optional[Path]:
    """Try to detect the current desktop wallpaper."""

    if shutil.which("gsettings"):
        for key in ("org.gnome.desktop.background picture-uri-dark", "org.gnome.desktop.background picture-uri"):
            try:
                output = subprocess.check_output(
                    ["gsettings", "get", *key.split()],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
            path = _path_from_gsettings(output)
            if path is not None and path.exists():
                return path

    if shutil.which("xfconf-query"):
        try:
            output = subprocess.check_output(
                [
                    "xfconf-query",
                    "-c",
                    "xfce4-desktop",
                    "-p",
                    "/backdrop/screen0/monitor0/image-path",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            output = ""
        path = _normalise_path(output.strip())
        if path is not None:
            return path

    return None


def _path_from_gsettings(value: str) -> Optional[Path]:
    value = value.strip().strip("\"'")
    if not value or value.lower() == "none":
        return None

    parsed = urlparse(value)
    if parsed.scheme == "file":
        return _normalise_path(unquote(parsed.path))

    return _normalise_path(value)


def _normalise_path(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    cleaned = value.strip().strip("\"'")
    if not cleaned:
        return None
    candidate = Path(cleaned).expanduser()
    if candidate.exists():
        return candidate
    return None


class WallpaperBackground(QtWidgets.QLabel):
    """Label that displays the configured wallpaper or a fallback color."""

    backgroundChanged = QtCore.pyqtSignal()

    def __init__(self, config: dict, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setScaledContents(True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self._config = config
        self._source_pixmap: Optional[QtGui.QPixmap] = None
        self._fallback_color = QtGui.QColor(255, 255, 255)

    def refresh_wallpaper(self) -> None:
        path = resolve_background_image(self._config)
        if path is not None:
            pixmap = QtGui.QPixmap(str(path))
            if pixmap.isNull():
                pixmap = None
        else:
            pixmap = None
        self._source_pixmap = pixmap
        self._update_background()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: D401 - inherited docstring
        super().resizeEvent(event)
        self._update_background()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: D401 - inherited docstring
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self.refresh_wallpaper)

    def _update_background(self) -> None:
        if not self.size().isValid():
            return

        if self._source_pixmap is not None and not self._source_pixmap.isNull():
            scaled = self._source_pixmap.scaled(
                self.size(),
                QtCore.Qt.KeepAspectRatioByExpanding,
                QtCore.Qt.SmoothTransformation,
            )
            if scaled.size() != self.size():
                x = max((scaled.width() - self.width()) // 2, 0)
                y = max((scaled.height() - self.height()) // 2, 0)
                pixmap = scaled.copy(x, y, self.width(), self.height())
            else:
                pixmap = scaled
        else:
            pixmap = QtGui.QPixmap(self.size())
            pixmap.fill(self._fallback_color)

        if pixmap.isNull():
            return

        self.setPixmap(pixmap)
        self.backgroundChanged.emit()


class AppIconButton(QtWidgets.QToolButton):
    """Button representing an application icon with a label."""

    triggered = QtCore.pyqtSignal(DesktopEntry)
    request_hide = QtCore.pyqtSignal(DesktopEntry)

    def __init__(self, entry: DesktopEntry, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
        self.setText(entry.name)
        self.setIcon(self._load_icon(entry.icon))
        self.setIconSize(QtCore.QSize(96, 96))
        self.setAutoRaise(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._text_color = QtGui.QColor(QtCore.Qt.white)
        self._apply_stylesheet()

    def _load_icon(self, icon_name: Optional[str]) -> QtGui.QIcon:
        if not icon_name:
            return QtGui.QIcon.fromTheme("application-x-executable")
        icon = QtGui.QIcon.fromTheme(icon_name)
        if not icon.isNull():
            return icon
        if os.path.isabs(icon_name) and os.path.exists(icon_name):
            return QtGui.QIcon(icon_name)
        return QtGui.QIcon.fromTheme("application-x-executable")

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: D401
        if event.button() == QtCore.Qt.RightButton:
            menu = QtWidgets.QMenu(self)
            hide_action = menu.addAction("Скрыть")
            action = menu.exec_(self.mapToGlobal(event.pos()))
            if action == hide_action:
                self.request_hide.emit(self.entry)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: D401
        if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.pos()):
            self.triggered.emit(self.entry)
        super().mouseReleaseEvent(event)

    def set_text_color(self, color: QtGui.QColor) -> None:
        if color == self._text_color:
            return
        self._text_color = color
        self._apply_stylesheet()

    def _apply_stylesheet(self) -> None:
        rgba = (self._text_color.red(), self._text_color.green(), self._text_color.blue(), self._text_color.alpha())
        self.setStyleSheet(
            "QToolButton {"
            " background: transparent;"
            f" color: rgba({rgba[0]}, {rgba[1]}, {rgba[2]}, {rgba[3]});"
            " border: none;"
            "}"
            "QToolButton:hover {"
            " background: rgba(255, 255, 255, 30);"
            " border-radius: 12px;"
            "}"
            "QToolButton:pressed {"
            " background: rgba(255, 255, 255, 60);"
            " border-radius: 12px;"
            "}"
        )


class PaginationDots(QtWidgets.QWidget):
    """Widget that draws pagination dots."""

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._current = 0
        self._total = 0
        self.setFixedHeight(24)

    def set_state(self, current: int, total: int) -> None:
        self._current = current
        self._total = total
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: D401
        super().paintEvent(event)
        if self._total <= 1:
            return

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        available_width = self.width()
        dot_size = 10
        spacing = 10
        total_width = self._total * dot_size + (self._total - 1) * spacing
        start_x = max((available_width - total_width) // 2, 0)
        y = self.height() // 2

        for index in range(self._total):
            rect = QtCore.QRect(start_x + index * (dot_size + spacing), y - dot_size // 2, dot_size, dot_size)
            color = QtGui.QColor(255, 255, 255, 200 if index == self._current else 80)
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(rect)


class LauncherWindow(QtWidgets.QWidget):
    """Main window for the launchpad application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowSystemMenuHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._all_entries: List[DesktopEntry] = []
        self._filtered_entries: List[DesktopEntry] = []
        self._hidden_ids = self._load_hidden_ids()
        self._current_page = 0
        self._buttons: List[AppIconButton] = []
        self._config = load_config()

        self.background = WallpaperBackground(self._config, self)
        self.background.setGeometry(self.rect())
        self.background.lower()
        self.background.backgroundChanged.connect(self._update_label_colors)

        self.search_field = QtWidgets.QLineEdit(self)
        self.search_field.setPlaceholderText("Search")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedHeight(44)
        self.search_field.textChanged.connect(self._apply_filter)
        self.search_field.setStyleSheet(
            "QLineEdit {"
            " background: rgba(0, 0, 0, 160);"
            " color: white;"
            " border: 1px solid rgba(255, 255, 255, 60);"
            " border-radius: 20px;"
            " padding: 0 16px;"
            "}"
            "QLineEdit::placeholder { color: rgba(255, 255, 255, 180); }"
        )

        font = self.search_field.font()
        font.setPointSize(16)
        self.search_field.setFont(font)

        self.arrow_left = QtWidgets.QToolButton(self)
        self.arrow_left.setArrowType(QtCore.Qt.LeftArrow)
        self.arrow_left.clicked.connect(self._show_previous_page)
        self.arrow_left.setToolTip("Previous page")
        self.arrow_left.setAutoRaise(True)
        self.arrow_left.setIconSize(QtCore.QSize(32, 32))

        self.arrow_right = QtWidgets.QToolButton(self)
        self.arrow_right.setArrowType(QtCore.Qt.RightArrow)
        self.arrow_right.clicked.connect(self._show_next_page)
        self.arrow_right.setToolTip("Next page")
        self.arrow_right.setAutoRaise(True)
        self.arrow_right.setIconSize(QtCore.QSize(32, 32))

        self.pages = QtWidgets.QStackedWidget(self)
        self.pages.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.pages.installEventFilter(self)

        self.pagination = PaginationDots(self)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(60, 40, 60, 40)
        layout.setSpacing(20)
        layout.addWidget(self.search_field, alignment=QtCore.Qt.AlignHCenter)

        pages_container = QtWidgets.QHBoxLayout()
        pages_container.setContentsMargins(0, 0, 0, 0)
        pages_container.setSpacing(20)
        pages_container.addWidget(self.arrow_left, alignment=QtCore.Qt.AlignVCenter)
        pages_container.addWidget(self.pages, stretch=1)
        pages_container.addWidget(self.arrow_right, alignment=QtCore.Qt.AlignVCenter)

        layout.addLayout(pages_container, stretch=1)
        layout.addWidget(self.pagination, alignment=QtCore.Qt.AlignHCenter)

        self.installEventFilter(self)
        self._load_entries_async()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: D401
        super().showEvent(event)
        self.background.setGeometry(self.rect())
        QtCore.QTimer.singleShot(0, self.search_field.setFocus)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: D401
        super().resizeEvent(event)
        self.background.setGeometry(self.rect())
        QtCore.QTimer.singleShot(0, self._update_label_colors)

    # Data management -------------------------------------------------

    def _load_entries_async(self) -> None:
        QtCore.QTimer.singleShot(0, self._load_entries)

    def _load_entries(self) -> None:
        entries = load_entries()
        entries = [entry for entry in entries if entry.id not in self._hidden_ids]
        self._all_entries = entries
        self._apply_filter()

    def _apply_filter(self) -> None:
        text = self.search_field.text().strip().lower()
        if not text:
            filtered = self._all_entries
        else:
            filtered = [entry for entry in self._all_entries if text in entry.name.lower() or text in entry.comment.lower()]
        self._filtered_entries = filtered
        self._current_page = 0
        self._rebuild_pages()

    # Pagination ------------------------------------------------------

    def _rebuild_pages(self) -> None:
        while self.pages.count():
            widget = self.pages.widget(0)
            self.pages.removeWidget(widget)
            widget.deleteLater()

        self._buttons.clear()

        for page_entries in chunked(self._filtered_entries, PAGE_SIZE):
            page_widget = self._create_page(page_entries)
            self.pages.addWidget(page_widget)

        total_pages = max(1, math.ceil(len(self._filtered_entries) / PAGE_SIZE))
        self.pagination.set_state(self._current_page, total_pages)
        self.arrow_left.setEnabled(self._current_page > 0)
        self.arrow_right.setEnabled(self._current_page < total_pages - 1)
        if self.pages.count():
            self.pages.setCurrentIndex(min(self._current_page, self.pages.count() - 1))
        else:
            empty_widget = QtWidgets.QLabel("No applications found")
            empty_widget.setAlignment(QtCore.Qt.AlignCenter)
            self.pages.addWidget(empty_widget)
            self.pages.setCurrentWidget(empty_widget)
        self.pagination.set_state(self._current_page, total_pages)
        QtCore.QTimer.singleShot(0, self._update_label_colors)

    def _create_page(self, entries: Sequence[DesktopEntry]) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        widget.installEventFilter(self)
        grid = QtWidgets.QGridLayout(widget)
        grid.setContentsMargins(20, 20, 20, 20)
        grid.setHorizontalSpacing(30)
        grid.setVerticalSpacing(20)

        for index, entry in enumerate(entries):
            row = index // COLUMNS
            column = index % COLUMNS
            button = AppIconButton(entry)
            button.triggered.connect(self._launch_entry)
            button.request_hide.connect(self._hide_entry)
            button.installEventFilter(self)
            grid.addWidget(button, row, column, alignment=QtCore.Qt.AlignCenter)
            self._buttons.append(button)

        # Fill remaining cells with spacers to maintain layout
        total_cells = ROWS * COLUMNS
        for index in range(len(entries), total_cells):
            row = index // COLUMNS
            column = index % COLUMNS
            spacer = QtWidgets.QWidget()
            spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            grid.addWidget(spacer, row, column)

        return widget

    def _show_previous_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._update_page()

    def _show_next_page(self) -> None:
        total_pages = max(1, math.ceil(len(self._filtered_entries) / PAGE_SIZE))
        if self._current_page < total_pages - 1:
            self._current_page += 1
            self._update_page()

    def _update_page(self) -> None:
        self.pages.setCurrentIndex(self._current_page)
        total_pages = max(1, math.ceil(len(self._filtered_entries) / PAGE_SIZE))
        self.pagination.set_state(self._current_page, total_pages)
        self.arrow_left.setEnabled(self._current_page > 0)
        self.arrow_right.setEnabled(self._current_page < total_pages - 1)
        QtCore.QTimer.singleShot(0, self._update_label_colors)

    # Interaction -----------------------------------------------------

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: D401
        if event.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_PageUp):
            self._show_previous_page()
            event.accept()
            return
        if event.key() in (QtCore.Qt.Key_Right, QtCore.Qt.Key_PageDown):
            self._show_next_page()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def _launch_entry(self, entry: DesktopEntry) -> None:
        launch_entry(entry)
        self.close()

    def _hide_entry(self, entry: DesktopEntry) -> None:
        self._hidden_ids.add(entry.id)
        self._all_entries = [item for item in self._all_entries if item.id != entry.id]
        self._save_hidden_ids()
        self._apply_filter()

    # Hidden entries --------------------------------------------------

    def _load_hidden_ids(self) -> Set[str]:
        if HIDDEN_FILE.exists():
            try:
                with HIDDEN_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        return {str(item) for item in data}
            except (OSError, json.JSONDecodeError):
                pass
        return set()

    def _save_hidden_ids(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = sorted(self._hidden_ids)
        try:
            with HIDDEN_FILE.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError:
            pass

    # Appearance ------------------------------------------------------

    def _update_label_colors(self) -> None:
        pixmap = self.background.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        image = pixmap.toImage()
        for button in self._buttons:
            center = button.rect().center()
            mapped = button.mapTo(self.background, center)
            if not image.rect().contains(mapped):
                button.set_text_color(QtGui.QColor(QtCore.Qt.white))
                continue
            color = QtGui.QColor(image.pixel(mapped))
            luminance = QtGui.qGray(color.rgb())
            if luminance > 160:
                button.set_text_color(QtGui.QColor(20, 20, 20))
            else:
                button.set_text_color(QtGui.QColor(245, 245, 245))

    # Event handling --------------------------------------------------

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if event.type() == QtCore.QEvent.Wheel:
            if isinstance(event, QtGui.QWheelEvent):
                delta = event.angleDelta().y()
                if delta > 0:
                    self._show_previous_page()
                elif delta < 0:
                    self._show_next_page()
            return True
        return super().eventFilter(watched, event)


def chunked(items: Iterable[DesktopEntry], size: int) -> Iterable[List[DesktopEntry]]:
    """Yield chunks of *size* from *items*."""

    chunk: List[DesktopEntry] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def run() -> None:
    """Entry point for running the Launchpad UI."""

    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = LauncherWindow()
    window.showFullScreen()
    window.show()
    window.raise_()
    window.activateWindow()

    app.exec_()
