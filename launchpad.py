"""A KDE-friendly launchpad application inspired by macOS Launchpad.

This module implements a fullscreen application grid that reads ``.desktop``
entries from the system and user application directories. Applications are
displayed in a 5x7 matrix per page, support navigation via mouse wheel and
keyboard arrows, and close the launchpad when an application is launched.
"""

from __future__ import annotations

import configparser
import json
import math
import os
import re
import shlex
import subprocess
import sys
import uuid

import PyQt5.sip as sip
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from PyQt5.QtCore import (
    Qt,
    QSize,
    QEvent,
    QTimer,
    QPoint,
    QRect,
    QMimeData,
    QEasingCurve,
    QPropertyAnimation,
    pyqtSignal,
)
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColor, QDrag
from PyQt5.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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
SEARCH_FIELD_EXTRA_WIDTH = 120
GRID_HORIZONTAL_SPACING = 30
GRID_VERTICAL_SPACING = 30
PAGE_CONTAINER_MARGIN = 40
FOLDER_COLUMNS = 3

FULL_GRID_WIDTH = APP_TILE_WIDTH * APP_COLUMNS + GRID_HORIZONTAL_SPACING * (APP_COLUMNS - 1)
FULL_PAGE_WIDTH = FULL_GRID_WIDTH + PAGE_CONTAINER_MARGIN * 2


CONFIG_PATH = Path.home() / ".config/lpad/lpad.conf"
CONFIG_SECTION = "apps"
CONFIG_KEY = "order"
DEFAULT_FOLDER_NAME = "Папка"


@dataclass
class Application:
    """A representation of a desktop entry used by the launcher."""

    name: str
    exec: str
    icon_name: str
    desktop_file: Path


@dataclass
class FolderItem:
    """Container describing a folder made of multiple applications."""

    identifier: str
    name: str
    apps: List[Application]


LayoutItem = Application | FolderItem


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
    saved_order = _load_saved_order_paths()
    if saved_order:
        app_by_path = {str(app.desktop_file): app for app in apps}
        used_paths: set[str] = set()
        ordered_apps: List[Application] = []
        for path in saved_order:
            app = app_by_path.get(path)
            if app and path not in used_paths:
                ordered_apps.append(app)
                used_paths.add(path)
        if ordered_apps:
            ordered_apps.extend(
                [app for app in apps if str(app.desktop_file) not in used_paths]
            )
            apps = ordered_apps
    return apps


def _load_saved_layout_entries() -> list[dict]:
    """Return persisted layout entries including folders if present."""

    if not CONFIG_PATH.exists():
        return []

    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_PATH, encoding="utf-8")
    except OSError:
        return []

    if not config.has_option(CONFIG_SECTION, CONFIG_KEY):
        return []

    raw_value = config.get(CONFIG_SECTION, CONFIG_KEY, fallback="").strip()
    if not raw_value:
        return []

    if raw_value.startswith("["):
        try:
            data = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            cleaned = []
            for entry in data:
                if isinstance(entry, dict):
                    cleaned.append(entry)
            return cleaned
        return []

    entries: list[dict] = []
    for line in raw_value.splitlines():
        path = line.strip()
        if not path:
            continue
        entries.append({"type": "app", "path": path})
    return entries


def _load_saved_order_paths() -> List[str]:
    """Return a flattened list of desktop paths in stored order."""

    entries = _load_saved_layout_entries()
    if not entries:
        return []

    paths: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_type = entry.get("type")
        if entry_type == "folder":
            for path in entry.get("apps", []) or []:
                if isinstance(path, str):
                    paths.append(path)
        elif entry_type == "app":
            path = entry.get("path")
            if isinstance(path, str):
                paths.append(path)
    return paths


def _serialize_layout(items: Sequence[LayoutItem]) -> str:
    """Return a JSON string representing the provided layout items."""

    payload: list[dict] = []
    for item in items:
        if isinstance(item, FolderItem):
            payload.append(
                {
                    "type": "folder",
                    "id": item.identifier,
                    "name": item.name,
                    "apps": [str(app.desktop_file) for app in item.apps],
                }
            )
        else:
            payload.append({"type": "app", "path": str(item.desktop_file)})
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _save_layout(items: Sequence[LayoutItem]) -> None:
    """Persist the current layout to the config file."""

    config = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        try:
            config.read(CONFIG_PATH, encoding="utf-8")
        except OSError:
            config = configparser.ConfigParser()

    if CONFIG_SECTION not in config:
        config[CONFIG_SECTION] = {}

    config[CONFIG_SECTION][CONFIG_KEY] = _serialize_layout(items)

    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            config.write(fh)
    except OSError:
        # Failing to persist the order is non-critical; ignore errors silently.
        pass


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
        self._dot_diameter = 6
        self._dot_spacing = 16
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setFixedHeight(20)

    def sizeHint(self) -> QSize:  # type: ignore[override]
        visible_dots = max(1, self._page_count)
        width = visible_dots * self._dot_diameter
        width += max(0, visible_dots - 1) * self._dot_spacing
        width += 12  # Provide side padding so the dots do not touch the edges.
        return QSize(width, 20)

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.sizeHint()

    def set_page_count(self, count: int) -> None:
        self._page_count = count
        self.updateGeometry()
        self.update()

    def set_current_page(self, index: int) -> None:
        if self._page_count:
            self._current_page = max(0, min(index, self._page_count - 1))
        else:
            self._current_page = 0
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dot_diameter = self._dot_diameter
        spacing = self._dot_spacing
        visible_dots = max(1, self._page_count)
        active_index = self._current_page if self._page_count else 0
        total_width = dot_diameter + max(0, visible_dots - 1) * spacing
        start_x = (self.width() - total_width) // 2
        y = (self.height() - dot_diameter) // 2

        active_color = QColor(45, 45, 45)
        inactive_color = QColor(210, 210, 210)

        for index in range(visible_dots):
            color = active_color if index == active_index else inactive_color
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            x = start_x + index * spacing
            painter.drawEllipse(x, y, dot_diameter, dot_diameter)


class LaunchpadTileButton(QToolButton):
    """Base button implementing shared drag behaviour for tiles."""

    MIME_TYPE = "application/x-launchpad-item"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_start_pos: QPoint | None = None
        self._suppress_click = False
        self._drag_enabled = True
        self.setAcceptDrops(False)

    def set_drag_enabled(self, enabled: bool) -> None:
        self._drag_enabled = enabled

    def drag_payload(self) -> dict:
        raise NotImplementedError

    @property
    def item_key(self) -> str:
        raise NotImplementedError

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._drag_enabled:
            self._drag_start_pos = event.pos()
            self._suppress_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if not self._drag_enabled:
            super().mouseMoveEvent(event)
            return
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        try:
            payload = self.drag_payload()
        except NotImplementedError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(self.MIME_TYPE, json.dumps(payload).encode("utf-8"))
        drag.setMimeData(mime_data)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.pos())

        self.hide()
        result = Qt.IgnoreAction
        try:
            result = drag.exec_(Qt.MoveAction)
        finally:
            if not sip.isdeleted(self) and result != Qt.MoveAction:
                self.show()
        self._suppress_click = True
        self._drag_start_pos = None

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if self._suppress_click:
            event.accept()
            self._suppress_click = False
            self._drag_start_pos = None
            return
        super().mouseReleaseEvent(event)
        self._drag_start_pos = None

    def _format_label(self, text: str) -> str:
        """Return button text wrapped to fit within the tile width."""

        metrics = self.fontMetrics()
        max_width = max(20, APP_TILE_WIDTH - 20)
        max_lines = 2
        lines: list[str] = []
        index = 0
        length = len(text)

        while index < length and len(lines) < max_lines:
            remaining_lines = max_lines - len(lines)
            if remaining_lines == 1:
                remaining_text = text[index:].lstrip()
                elided = metrics.elidedText(remaining_text, Qt.ElideRight, max_width)
                lines.append(elided.rstrip())
                break

            current = ""
            while index < length:
                char = text[index]
                tentative = current + char
                if metrics.horizontalAdvance(tentative) <= max_width or not current:
                    current = tentative
                    index += 1
                else:
                    break

            if not current:
                if index < length:
                    current = text[index]
                    index += 1
                else:
                    break

            current = current.rstrip()
            if not current:
                continue

            lines.append(current)
            while index < length and text[index] == " ":
                index += 1

        if not lines:
            lines.append("")

        return "\n".join(lines)


class ApplicationButton(LaunchpadTileButton):
    """Button representing a single application entry."""

    def __init__(self, app: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIcon(self._create_icon(app.icon_name))
        self.setIconSize(QSize(64, 64))
        self.setAutoRaise(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(APP_TILE_WIDTH)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet(
            "QToolButton { padding: 10px; text-align: center; }\n"
            "QToolButton::menu-indicator { image: none; }"
        )
        self.setText(self._format_label(app.name))
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

    @property
    def desktop_path(self) -> str:
        """Return the full path to the desktop file represented by the button."""

        return str(self._app.desktop_file)

    @property
    def item_key(self) -> str:  # type: ignore[override]
        return self.desktop_path

    def drag_payload(self) -> dict:  # type: ignore[override]
        return {
            "kind": "app",
            "key": self.desktop_path,
            "path": self.desktop_path,
        }


class FolderButton(LaunchpadTileButton):
    """Button representing a folder containing multiple applications."""

    folder_open_requested = pyqtSignal(str, QRect)

    def __init__(self, folder: FolderItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._folder = folder
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self._update_icon()
        self.setIconSize(QSize(64, 64))
        self.setAutoRaise(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(APP_TILE_WIDTH)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet(
            "QToolButton { padding: 10px; text-align: center; }\n"
            "QToolButton::menu-indicator { image: none; }"
        )
        self.setText(self._format_label(folder.name))
        self.clicked.connect(self._on_clicked)

    def _update_icon(self) -> None:
        previews = self._folder.apps[:4]
        if not previews:
            fallback = QIcon.fromTheme("folder")
            if fallback.isNull():
                fallback = QApplication.style().standardIcon(QApplication.style().SP_DirClosedIcon)
            self.setIcon(fallback)
            return

        base_size = 96
        pixmap = QPixmap(base_size, base_size)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRect(4, 4, base_size - 8, base_size - 8)
        painter.setPen(QColor(255, 255, 255, 80))
        painter.setBrush(QColor(40, 40, 40, 230))
        painter.drawRoundedRect(rect, 18, 18)

        inner_margin = 14
        spacing = 8
        inner = rect.adjusted(inner_margin, inner_margin, -inner_margin, -inner_margin)
        cell_width = max(1, (inner.width() - spacing) // 2)
        cell_height = max(1, (inner.height() - spacing) // 2)
        icon_size = min(cell_width, cell_height)
        offset_x = inner.left() + (inner.width() - (icon_size * 2 + spacing)) // 2
        offset_y = inner.top() + (inner.height() - (icon_size * 2 + spacing)) // 2

        for index, app in enumerate(previews):
            icon = ApplicationButton._create_icon(app.icon_name)
            tile = icon.pixmap(icon_size, icon_size)
            if tile.isNull():
                continue
            row = index // 2
            column = index % 2
            x = offset_x + column * (icon_size + spacing)
            y = offset_y + row * (icon_size + spacing)
            painter.drawPixmap(x, y, icon_size, icon_size, tile)

        painter.end()
        self.setIcon(QIcon(pixmap))

    def set_folder_name(self, name: str) -> None:
        self._folder.name = name
        self.setText(self._format_label(name))

    def set_folder_apps(self, apps: List[Application]) -> None:
        self._folder.apps = apps
        self._update_icon()

    def _on_clicked(self) -> None:
        rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        self.folder_open_requested.emit(self._folder.identifier, rect)

    @property
    def item_key(self) -> str:  # type: ignore[override]
        return f"folder:{self._folder.identifier}"

    def drag_payload(self) -> dict:  # type: ignore[override]
        return {
            "kind": "folder",
            "key": self.item_key,
            "folder_id": self._folder.identifier,
        }

    @property
    def folder(self) -> FolderItem:
        return self._folder


class ClickableLabel(QLabel):
    """Label emitting a clicked signal when activated."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class FolderPopup(QWidget):
    """Popup window showing the contents of a folder."""

    rename_requested = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, folder: FolderItem, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._folder = folder
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(
            "background-color: rgba(255, 255, 255, 235);"
            "border-radius: 20px;"
            "color: black;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._name_label = ClickableLabel(folder.name)
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(self._name_label, alignment=Qt.AlignHCenter)
        self._name_label.clicked.connect(self._enter_edit_mode)

        self._name_edit = QLineEdit(folder.name)
        self._name_edit.setAlignment(Qt.AlignCenter)
        self._name_edit.hide()
        layout.addWidget(self._name_edit, alignment=Qt.AlignHCenter)
        self._name_edit.editingFinished.connect(self._finish_edit)

        self._apps_container = QWidget(self)
        grid = QGridLayout(self._apps_container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        self._grid = grid
        layout.addWidget(self._apps_container)

        self._populate_apps()
        self._update_size()

    @property
    def folder_id(self) -> str:
        return self._folder.identifier

    def update_name(self, name: str) -> None:
        self._name_label.setText(name)
        if self._name_edit.isVisible():
            self._name_edit.setText(name)

    def closeEvent(self, event):  # type: ignore[override]
        self.closed.emit()
        super().closeEvent(event)

    def _enter_edit_mode(self) -> None:
        self._name_edit.setText(self._name_label.text())
        self._name_label.hide()
        self._name_edit.show()
        self._name_edit.setFocus()
        self._name_edit.selectAll()

    def _finish_edit(self) -> None:
        new_name = self._name_edit.text().strip() or DEFAULT_FOLDER_NAME
        self._name_label.setText(new_name)
        self._name_edit.hide()
        self._name_label.show()
        self.rename_requested.emit(new_name)

    def _populate_apps(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for index, app in enumerate(self._folder.apps):
            button = ApplicationButton(app, self._apps_container)
            button.set_drag_enabled(False)
            row = index // FOLDER_COLUMNS
            column = index % FOLDER_COLUMNS
            self._grid.addWidget(button, row, column)
        self._update_size()

    def _update_size(self) -> None:
        columns = min(FOLDER_COLUMNS, max(1, len(self._folder.apps)))
        width = columns * APP_TILE_WIDTH + max(0, columns - 1) * 20 + 48
        self.setFixedWidth(width)


class ApplicationGridWidget(QWidget):
    """Container widget responsible for handling drag-and-drop interactions."""

    reorder_requested = pyqtSignal(str, str, bool)
    merge_requested = pyqtSignal(dict, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tiles: list[LaunchpadTileButton] = []
        self._placeholder_index: int | None = None
        self._placeholder = QWidget(self)
        self._placeholder.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._placeholder.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._placeholder.setStyleSheet(
            "border: 2px dashed rgba(80, 80, 80, 160);"
            " border-radius: 26px; background-color: rgba(255, 255, 255, 80);"
        )
        self._placeholder.hide()
        self._active_animations: list[QPropertyAnimation] = []
        self.setAcceptDrops(True)

    def add_button(self, button: LaunchpadTileButton, row: int, column: int) -> None:
        """Add a tile button to the grid at the specified position."""

        button.setParent(self)
        button.resize(button.sizeHint())
        button.show()
        button.installEventFilter(self)
        self._tiles.append(button)
        self._update_placeholder_size(button)
        self._reflow_tiles()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return QSize(FULL_GRID_WIDTH, self._calculated_height())

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.sizeHint()

    def eventFilter(self, obj, event):
        if isinstance(obj, LaunchpadTileButton) and event is not None:
            if event.type() in (QEvent.Hide, QEvent.Show, QEvent.HideToParent, QEvent.ShowToParent):
                QTimer.singleShot(0, self._reflow_tiles)
        return super().eventFilter(obj, event)

    def dragEnterEvent(self, event):  # type: ignore[override]
        if event.mimeData().hasFormat(LaunchpadTileButton.MIME_TYPE):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # type: ignore[override]
        if not event.mimeData().hasFormat(LaunchpadTileButton.MIME_TYPE):
            super().dragMoveEvent(event)
            return
        target = self._determine_drop_target(event.pos())
        if target is None:
            self._clear_placeholder()
            event.ignore()
            return
        target_button, insert_before, on_icon = target
        if on_icon:
            self._clear_placeholder()
        else:
            self._show_placeholder(target_button, insert_before)
        event.acceptProposedAction()

    def dropEvent(self, event):  # type: ignore[override]
        if not event.mimeData().hasFormat(LaunchpadTileButton.MIME_TYPE):
            super().dropEvent(event)
            return

        target = self._determine_drop_target(event.pos())
        if target is None:
            self._clear_placeholder()
            event.ignore()
            return

        target_button, insert_before, on_icon = target
        try:
            payload = json.loads(bytes(event.mimeData().data(LaunchpadTileButton.MIME_TYPE)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}

        source_key = payload.get("key") if isinstance(payload, dict) else None
        if not isinstance(source_key, str):
            self._clear_placeholder()
            event.ignore()
            return

        target_key = target_button.item_key
        if source_key == target_key:
            self._clear_placeholder()
            event.ignore()
            return

        if on_icon:
            self._clear_placeholder()
            self.merge_requested.emit(payload, target_key)
        else:
            self.reorder_requested.emit(source_key, target_key, insert_before)
            self._clear_placeholder()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):  # type: ignore[override]
        self._clear_placeholder()
        super().dragLeaveEvent(event)

    def _determine_drop_target(
        self, position: QPoint
    ) -> tuple[LaunchpadTileButton, bool, bool] | None:
        """Return drop target, placement side and whether cursor is on an icon."""

        tiles = [tile for tile in self._tiles if tile.isVisible()]
        if not tiles:
            return None

        vertical_padding = max(1, GRID_VERTICAL_SPACING // 2)

        first_rect = tiles[0].geometry()
        if position.y() < first_rect.top() - vertical_padding:
            return tiles[0], True, False

        last_rect = tiles[-1].geometry()
        if position.y() > last_rect.bottom() + vertical_padding:
            return tiles[-1], False, False

        rows: list[list[LaunchpadTileButton]] = []
        for index, tile in enumerate(tiles):
            row_index = index // APP_COLUMNS
            if row_index >= len(rows):
                rows.append([])
            rows[row_index].append(tile)

        for row_tiles in rows:
            row_top = min(btn.geometry().top() for btn in row_tiles) - vertical_padding
            row_bottom = max(btn.geometry().bottom() for btn in row_tiles) + vertical_padding

            if position.y() < row_top:
                return row_tiles[0], True, False
            if position.y() > row_bottom:
                continue

            first_rect = row_tiles[0].geometry()
            if position.x() < first_rect.left():
                return row_tiles[0], True, False

            for idx, tile in enumerate(row_tiles):
                rect = tile.geometry()
                if rect.contains(position):
                    return tile, True, True
                next_tile = row_tiles[idx + 1] if idx + 1 < len(row_tiles) else None
                if next_tile:
                    gap_start = rect.right()
                    gap_end = next_tile.geometry().left()
                    if gap_end > gap_start and gap_start <= position.x() <= gap_end:
                        return next_tile, True, False

            last_rect = row_tiles[-1].geometry()
            if position.x() > last_rect.right():
                return row_tiles[-1], False, False

        return None

    def _visible_tiles(self) -> list[LaunchpadTileButton]:
        return [tile for tile in self._tiles if tile.isVisible()]

    def _show_placeholder(self, target_button: LaunchpadTileButton, insert_before: bool) -> None:
        visible = self._visible_tiles()
        try:
            target_index = visible.index(target_button)
        except ValueError:
            self._clear_placeholder()
            return
        index = target_index if insert_before else target_index + 1
        index = max(0, min(index, len(visible)))
        if self._placeholder_index == index:
            return
        self._placeholder_index = index
        self._update_placeholder_size()
        self._reflow_tiles()

    def _clear_placeholder(self) -> None:
        if self._placeholder_index is None:
            return
        self._placeholder_index = None
        self._reflow_tiles()

    def _update_placeholder_size(self, reference: QWidget | None = None) -> None:
        if reference is None:
            for tile in self._tiles:
                if tile.isVisible():
                    reference = tile
                    break
        if reference is None:
            return
        size = reference.sizeHint()
        width = max(APP_TILE_WIDTH, size.width()) if size.isValid() else APP_TILE_WIDTH
        height = size.height() if size.isValid() else reference.height()
        if height <= 0:
            height = reference.sizeHint().height()
        if height <= 0:
            height = 120
        self._placeholder.setFixedSize(width, height)

    def _reflow_tiles(self) -> None:
        self._stop_animations()
        self.setUpdatesEnabled(False)
        try:
            visible = self._visible_tiles()
            if self._placeholder_index is None:
                self._placeholder.hide()
                widgets: list[QWidget] = visible
            else:
                index = max(0, min(self._placeholder_index, len(visible)))
                widgets = visible[:index] + [self._placeholder] + visible[index:]
                self._placeholder.show()
            row_height = self._row_height()
            total_widgets = len(widgets)
            self._update_container_height(total_widgets, row_height)

            for position, widget in enumerate(widgets):
                target_rect = self._target_rect(position, row_height, widget)
                if widget is self._placeholder:
                    widget.setGeometry(target_rect)
                    widget.raise_()
                    continue

                current_rect = widget.geometry()
                if not current_rect.isValid() or current_rect.size().isEmpty():
                    widget.setGeometry(target_rect)
                    continue
                if current_rect == target_rect:
                    continue
                animation = QPropertyAnimation(widget, b"geometry", self)
                animation.setDuration(220)
                animation.setEasingCurve(QEasingCurve.OutCubic)
                animation.setStartValue(current_rect)
                animation.setEndValue(target_rect)
                animation.finished.connect(lambda w=animation: self._on_animation_finished(w))
                animation.start()
                self._active_animations.append(animation)
        finally:
            self.setUpdatesEnabled(True)
            self.update()

    def _calculated_height(self) -> int:
        visible_count = len(self._visible_tiles())
        if self._placeholder_index is not None:
            visible_count += 1
        if visible_count == 0:
            return 0
        row_height = self._row_height()
        rows = math.ceil(visible_count / APP_COLUMNS)
        return rows * row_height + max(0, rows - 1) * GRID_VERTICAL_SPACING

    def _row_height(self) -> int:
        heights: list[int] = []
        for tile in self._tiles:
            if not tile.isVisible():
                continue
            hint = tile.sizeHint()
            height = hint.height() if hint.isValid() else tile.height()
            if height <= 0:
                height = tile.geometry().height()
            if height <= 0:
                height = 120
            heights.append(height)
        if self._placeholder.isVisible():
            heights.append(self._placeholder.height())
        if not heights:
            return 140
        return max(heights)

    def _target_rect(self, position: int, row_height: int, widget: QWidget) -> QRect:
        column = position % APP_COLUMNS
        row = position // APP_COLUMNS
        x = column * (APP_TILE_WIDTH + GRID_HORIZONTAL_SPACING)
        y = row * (row_height + GRID_VERTICAL_SPACING)
        if widget is self._placeholder:
            width = self._placeholder.width()
            if width <= 0:
                width = APP_TILE_WIDTH
        else:
            width = widget.width() or APP_TILE_WIDTH
        return QRect(x, y, width, row_height)

    def _update_container_height(self, item_count: int, row_height: int) -> None:
        if item_count <= 0:
            self.setFixedHeight(0)
        else:
            rows = math.ceil(item_count / APP_COLUMNS)
            height = rows * row_height + max(0, rows - 1) * GRID_VERTICAL_SPACING
            self.setFixedHeight(height)
        self.updateGeometry()

    def _stop_animations(self) -> None:
        while self._active_animations:
            animation = self._active_animations.pop()
            animation.stop()
            animation.deleteLater()

    def _on_animation_finished(self, animation: QPropertyAnimation) -> None:
        try:
            self._active_animations.remove(animation)
        except ValueError:
            pass
        animation.deleteLater()



class LaunchpadWindow(QWidget):
    """Main fullscreen window containing the paginated application grid."""

    def __init__(self, apps: List[Application]) -> None:
        super().__init__()
        self._all_apps = apps
        self._layout_items: List[LayoutItem] = self._build_layout_items(apps)
        self._background = _read_kde_wallpaper()
        self._search_query = ""
        self._folder_buttons: dict[str, FolderButton] = {}
        self._folder_popup: FolderPopup | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)

        self._page_indicator = PageIndicator(max(1, math.ceil(len(apps) / APPS_PER_PAGE)))
        self._stack = QStackedWidget()
        self._stack.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._stack.setFixedWidth(FULL_PAGE_WIDTH)

        self._search_field = QLineEdit()
        self._search_field.setPlaceholderText("Поиск приложений")
        self._search_field.setClearButtonEnabled(True)
        self._search_field.setFixedHeight(40)
        self._search_field.setFixedWidth(APP_TILE_WIDTH * APP_COLUMNS + SEARCH_FIELD_EXTRA_WIDTH)
        self._search_field.setStyleSheet(
            "QLineEdit {"
            " padding: 0 18px;"
            " border-radius: 20px;"
            " border: 1px solid rgba(0, 0, 0, 80);"
            " background-color: rgba(255, 255, 255, 200);"
            " color: black;"
            " font-size: 16px;"
            "}"
            "QLineEdit:focus { border-color: rgba(64, 128, 255, 160); }"
        )
        self._search_field.textChanged.connect(self._on_search_text_changed)
        self._search_field.installEventFilter(self)

        self._filtered_items: List[LayoutItem] = []
        self._update_filtered_items()

        left_button = QPushButton("◀")
        right_button = QPushButton("▶")
        for button in (left_button, right_button):
            button.setFixedSize(60, 60)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet(
                "background-color: rgba(0, 0, 0, 0.4); color: white; border: none; border-radius: 30px;"
            )

        left_button.clicked.connect(self.previous_page)
        right_button.clicked.connect(self.next_page)

        content_layout = QHBoxLayout()
        content_layout.setSpacing(30)
        content_layout.addStretch(1)
        content_layout.addWidget(left_button, alignment=Qt.AlignVCenter)
        content_layout.addSpacing(10)
        content_layout.addWidget(self._stack, alignment=Qt.AlignTop)
        content_layout.addSpacing(10)
        content_layout.addWidget(right_button, alignment=Qt.AlignVCenter)
        content_layout.addStretch(1)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(60, 60, 60, 40)
        main_layout.setSpacing(20)
        main_layout.addWidget(self._search_field, alignment=Qt.AlignHCenter)
        main_layout.addSpacing(10)
        main_layout.addLayout(content_layout, stretch=1)
        main_layout.addWidget(self._page_indicator, alignment=Qt.AlignCenter)
        main_layout.addStretch()

        self._update_page_indicator()
        QTimer.singleShot(0, self._search_field.setFocus)

    def eventFilter(self, obj, event):
        if obj is self._search_field and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape:
                self.close()
                return True
            if event.modifiers() == Qt.NoModifier and key in (Qt.Key_Right, Qt.Key_Down):
                self.next_page()
                return True
            if event.modifiers() == Qt.NoModifier and key in (Qt.Key_Left, Qt.Key_Up):
                self.previous_page()
                return True
        return super().eventFilter(obj, event)

    def _build_layout_items(self, apps: List[Application]) -> List[LayoutItem]:
        entries = _load_saved_layout_entries()
        if not entries:
            return list(apps)

        app_lookup = {str(app.desktop_file): app for app in apps}
        used: set[str] = set()
        layout: List[LayoutItem] = []

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            if entry_type == "folder":
                paths: list[str] = []
                for path in entry.get("apps", []) or []:
                    if isinstance(path, str) and path in app_lookup and path not in used:
                        paths.append(path)
                if len(paths) >= 2:
                    identifier = entry.get("id")
                    if not isinstance(identifier, str) or not identifier:
                        identifier = str(uuid.uuid4())
                    name = entry.get("name")
                    if not isinstance(name, str) or not name.strip():
                        name = DEFAULT_FOLDER_NAME
                    folder_apps = [app_lookup[path] for path in paths]
                    for path in paths:
                        used.add(path)
                    layout.append(FolderItem(identifier, name, folder_apps))
                    continue
                for path in paths:
                    layout.append(app_lookup[path])
                    used.add(path)
                continue
            if entry_type == "app":
                path = entry.get("path")
                if isinstance(path, str) and path in app_lookup and path not in used:
                    layout.append(app_lookup[path])
                    used.add(path)

        for app in apps:
            path = str(app.desktop_file)
            if path not in used:
                layout.append(app)

        return layout

    def _clear_pages(self) -> None:
        self._close_folder_popup()
        self._folder_buttons.clear()
        while self._stack.count():
            widget = self._stack.widget(0)
            self._stack.removeWidget(widget)
            widget.deleteLater()

    def _rebuild_pages(self) -> None:
        self._clear_pages()
        items = self._filtered_items
        empty_text = "Нет установленных приложений"
        if self._all_apps and not items:
            empty_text = "Ничего не найдено"
        self._create_pages(items, empty_text)
        if self._stack.count() > 0:
            self._stack.setCurrentIndex(0)
        self._update_page_indicator()

    def _create_pages(self, items: List[LayoutItem], empty_text: str) -> None:
        for index in range(0, len(items), APPS_PER_PAGE):
            page_items = items[index : index + APPS_PER_PAGE]
            page_widget = QWidget()
            page_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            page_layout = QVBoxLayout(page_widget)
            page_layout.setContentsMargins(
                PAGE_CONTAINER_MARGIN,
                PAGE_CONTAINER_MARGIN,
                PAGE_CONTAINER_MARGIN,
                PAGE_CONTAINER_MARGIN,
            )
            page_layout.setSpacing(0)

            grid_container = ApplicationGridWidget()
            grid_container.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            grid_container.setFixedWidth(FULL_GRID_WIDTH)
            grid_container.reorder_requested.connect(self._on_reorder_requested)
            grid_container.merge_requested.connect(self._on_merge_requested)

            for position, item in enumerate(page_items):
                row = position // APP_COLUMNS
                column = position % APP_COLUMNS
                if isinstance(item, FolderItem):
                    button = FolderButton(item)
                    button.folder_open_requested.connect(self._on_folder_button_clicked)
                    self._folder_buttons[item.identifier] = button
                else:
                    button = ApplicationButton(item)
                grid_container.add_button(button, row, column)
            page_widget.setFixedWidth(FULL_PAGE_WIDTH)
            page_layout.addWidget(grid_container, alignment=Qt.AlignTop | Qt.AlignLeft)
            page_layout.addStretch(1)

            self._stack.addWidget(page_widget)

        if self._stack.count() == 0:
            empty_widget = QWidget()
            empty_widget.setFixedWidth(FULL_PAGE_WIDTH)
            message = QLabel(empty_text)
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
        if self._handle_search_key(event):
            return
        if event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.next_page()
        elif event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.previous_page()
        elif event.key() in (Qt.Key_Escape, Qt.Key_Q):
            self.close()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow():
            self.close()

    def _handle_search_key(self, event) -> bool:
        modifiers = event.modifiers()
        if modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False

        key = event.key()
        if key == Qt.Key_Backspace:
            self._search_field.setFocus()
            self._search_field.backspace()
            return True
        if key == Qt.Key_Delete:
            self._search_field.setFocus()
            self._search_field.del_()
            return True

        text = event.text()
        if text and (text.isprintable() or text == " "):
            self._search_field.setFocus()
            self._search_field.insert(text)
            return True

        return False

    def _on_search_text_changed(self, text: str) -> None:
        self._search_query = text.strip().lower()
        self._update_filtered_items()

    def _update_page_indicator(self) -> None:
        self._page_indicator.set_page_count(self._stack.count())
        self._page_indicator.set_current_page(self._stack.currentIndex())

    def _update_filtered_items(self) -> None:
        if not self._search_query:
            self._filtered_items = list(self._layout_items)
        else:
            query = self._search_query
            self._filtered_items = [
                app for app in self._all_apps if query in app.name.lower()
            ]
        self._rebuild_pages()

    def _on_reorder_requested(self, source_key: str, target_key: str, insert_before: bool) -> None:
        if source_key == target_key:
            return

        source_index, source_item = self._find_item(source_key)
        target_index, _ = self._find_item(target_key)
        if source_index is None or source_item is None or target_index is None:
            return

        item = self._layout_items.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        if not insert_before:
            target_index += 1
        target_index = max(0, min(target_index, len(self._layout_items)))
        self._layout_items.insert(target_index, item)
        _save_layout(self._layout_items)
        self._update_filtered_items()

    @staticmethod
    def _item_key(item: LayoutItem) -> str:
        if isinstance(item, FolderItem):
            return f"folder:{item.identifier}"
        return str(item.desktop_file)

    def _find_item(self, key: str) -> tuple[int | None, LayoutItem | None]:
        for index, item in enumerate(self._layout_items):
            if self._item_key(item) == key:
                return index, item
        return None, None

    def _find_folder(self, folder_id: str) -> FolderItem | None:
        for item in self._layout_items:
            if isinstance(item, FolderItem) and item.identifier == folder_id:
                return item
        return None

    def _on_merge_requested(self, payload: dict, target_key: str) -> None:
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if kind != "app":
            return
        source_key = payload.get("key")
        if not isinstance(source_key, str) or source_key == target_key:
            return

        source_index, source_item = self._find_item(source_key)
        target_index, target_item = self._find_item(target_key)
        if (
            source_index is None
            or target_index is None
            or source_item is None
            or target_item is None
            or not isinstance(source_item, Application)
        ):
            return

        if isinstance(target_item, Application):
            insert_index = min(source_index, target_index)
            source_app = source_item
            target_app = target_item
            for idx in sorted({source_index, target_index}, reverse=True):
                self._layout_items.pop(idx)
            folder = FolderItem(str(uuid.uuid4()), DEFAULT_FOLDER_NAME, [target_app, source_app])
            self._layout_items.insert(insert_index, folder)
        elif isinstance(target_item, FolderItem):
            source_app = self._layout_items.pop(source_index)
            if not isinstance(source_app, Application):
                return
            if source_index < target_index:
                target_index -= 1
            folder = self._layout_items[target_index]
            if not isinstance(folder, FolderItem):
                return
            existing = {str(app.desktop_file) for app in folder.apps}
            if str(source_app.desktop_file) not in existing:
                folder.apps.append(source_app)
        else:
            return

        _save_layout(self._layout_items)
        self._update_filtered_items()

    def _on_folder_button_clicked(self, folder_id: str, anchor_rect: QRect) -> None:
        folder = self._find_folder(folder_id)
        if not folder:
            return
        self._open_folder_popup(folder, anchor_rect)

    def _open_folder_popup(self, folder: FolderItem, anchor_rect: QRect) -> None:
        self._close_folder_popup()
        popup = FolderPopup(folder, self)
        popup.rename_requested.connect(lambda name, fid=folder.identifier: self._on_folder_renamed(fid, name))
        popup.closed.connect(self._on_folder_popup_closed)
        popup.adjustSize()

        screen_geometry = QApplication.desktop().availableGeometry(self)
        popup_size = popup.size()
        x = anchor_rect.center().x() - popup_size.width() // 2
        y = anchor_rect.bottom() + 12
        x = max(screen_geometry.left() + 20, min(x, screen_geometry.right() - popup_size.width() - 20))
        y = max(screen_geometry.top() + 20, min(y, screen_geometry.bottom() - popup_size.height() - 20))
        popup.move(x, y)
        popup.show()
        self._folder_popup = popup

    def _close_folder_popup(self) -> None:
        if self._folder_popup:
            try:
                self._folder_popup.closed.disconnect(self._on_folder_popup_closed)
            except TypeError:
                pass
            self._folder_popup.close()
            self._folder_popup = None

    def _on_folder_popup_closed(self) -> None:
        self._folder_popup = None

    def _on_folder_renamed(self, folder_id: str, new_name: str) -> None:
        folder = self._find_folder(folder_id)
        if not folder:
            return
        name = new_name.strip() or DEFAULT_FOLDER_NAME
        folder.name = name
        button = self._folder_buttons.get(folder_id)
        if button:
            button.set_folder_name(name)
        if self._folder_popup and self._folder_popup.folder_id == folder_id:
            self._folder_popup.update_name(name)
        _save_layout(self._layout_items)


def main() -> None:
    apps = load_applications()
    app = QApplication(sys.argv)
    window = LaunchpadWindow(apps)
    window.showFullScreen()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

