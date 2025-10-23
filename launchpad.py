"""A KDE-friendly launchpad application inspired by macOS Launchpad.

This module implements a fullscreen application grid that reads ``.desktop``
entries from the system and user application directories. Applications are
displayed in a 5x7 matrix per page, support navigation via mouse wheel and
keyboard arrows, and close the launchpad when an application is launched.
"""

from __future__ import annotations

import configparser
import math
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from PyQt5.QtCore import (
    Qt,
    QSize,
    QEvent,
    QTimer,
    QPoint,
    QMimeData,
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

FULL_GRID_WIDTH = APP_TILE_WIDTH * APP_COLUMNS + GRID_HORIZONTAL_SPACING * (APP_COLUMNS - 1)
FULL_PAGE_WIDTH = FULL_GRID_WIDTH + PAGE_CONTAINER_MARGIN * 2


CONFIG_PATH = Path.home() / ".config/lpad/lpad.conf"
CONFIG_SECTION = "apps"
CONFIG_KEY = "order"


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


def _load_saved_order_paths() -> List[str]:
    """Return a list of desktop file paths representing stored app order."""

    if not CONFIG_PATH.exists():
        return []

    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_PATH, encoding="utf-8")
    except OSError:
        return []

    if not config.has_option(CONFIG_SECTION, CONFIG_KEY):
        return []

    value = config.get(CONFIG_SECTION, CONFIG_KEY, fallback="")
    return [line.strip() for line in value.splitlines() if line.strip()]


def _save_app_order(apps: List[Application]) -> None:
    """Persist the current order of applications to the config file."""

    config = configparser.ConfigParser()
    if CONFIG_PATH.exists():
        try:
            config.read(CONFIG_PATH, encoding="utf-8")
        except OSError:
            config = configparser.ConfigParser()

    if CONFIG_SECTION not in config:
        config[CONFIG_SECTION] = {}

    config[CONFIG_SECTION][CONFIG_KEY] = "\n".join(str(app.desktop_file) for app in apps)

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


class ApplicationButton(QToolButton):
    """Button representing a single application entry."""

    def __init__(self, app: Application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = app
        self._drag_start_pos: QPoint | None = None
        self._suppress_click = False
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
        self.setAcceptDrops(False)

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

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
            self._suppress_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData(
            "application/x-launchpad-app",
            str(self._app.desktop_file).encode("utf-8"),
        )
        drag.setMimeData(mime_data)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.pos())

        drag.exec_(Qt.MoveAction)
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

    @property
    def desktop_path(self) -> str:
        """Return the full path to the desktop file represented by the button."""

        return str(self._app.desktop_file)

    def _format_label(self, text: str) -> str:
        """Return the button label wrapped to fit within the tile width."""

        metrics = self.fontMetrics()
        max_width = max(20, APP_TILE_WIDTH - 20)  # account for padding
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
                current = text[index]
                index += 1

            current = current.rstrip()
            if not current:
                continue

            lines.append(current)
            while index < length and text[index] == " ":
                index += 1

        if not lines:
            lines.append("")

        return "\n".join(lines)


class ApplicationGridWidget(QWidget):
    """Container widget responsible for handling drag-and-drop reordering."""

    reorder_requested = pyqtSignal(str, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(GRID_HORIZONTAL_SPACING)
        self._grid.setVerticalSpacing(GRID_VERTICAL_SPACING)
        self._grid.setColumnStretch(APP_COLUMNS, 1)
        self._grid.setRowStretch(APP_ROWS, 1)
        self._buttons: list[ApplicationButton] = []
        self.setAcceptDrops(True)

    def add_button(self, button: ApplicationButton, row: int, column: int) -> None:
        """Add an application button to the grid at the specified position."""

        self._grid.addWidget(button, row, column)
        self._buttons.append(button)

    def dragEnterEvent(self, event):  # type: ignore[override]
        if event.mimeData().hasFormat("application/x-launchpad-app"):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # type: ignore[override]
        if not event.mimeData().hasFormat("application/x-launchpad-app"):
            super().dragMoveEvent(event)
            return
        if self._determine_drop_target(event.pos()) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event):  # type: ignore[override]
        if not event.mimeData().hasFormat("application/x-launchpad-app"):
            super().dropEvent(event)
            return

        target = self._determine_drop_target(event.pos())
        if target is None:
            event.ignore()
            return

        target_button, insert_before = target
        source_path = bytes(event.mimeData().data("application/x-launchpad-app")).decode("utf-8")
        target_path = target_button.desktop_path
        if source_path and source_path != target_path:
            self.reorder_requested.emit(source_path, target_path, insert_before)
        event.acceptProposedAction()

    def _determine_drop_target(
        self, position: QPoint
    ) -> tuple[ApplicationButton, bool] | None:
        """Return the drop target button and placement side for the position."""

        buttons = [button for button in self._buttons if button.isVisible()]
        if not buttons:
            return None

        vertical_padding = max(1, GRID_VERTICAL_SPACING // 2)

        first_rect = buttons[0].geometry()
        if position.y() < first_rect.top() - vertical_padding:
            return buttons[0], True

        last_rect = buttons[-1].geometry()
        if position.y() > last_rect.bottom() + vertical_padding:
            return buttons[-1], False

        rows: list[list[ApplicationButton]] = []
        for index, button in enumerate(buttons):
            row_index = index // APP_COLUMNS
            if row_index >= len(rows):
                rows.append([])
            rows[row_index].append(button)

        for row_buttons in rows:
            row_top = min(btn.geometry().top() for btn in row_buttons) - vertical_padding
            row_bottom = max(btn.geometry().bottom() for btn in row_buttons) + vertical_padding

            if position.y() < row_top:
                return row_buttons[0], True
            if position.y() > row_bottom:
                continue

            first_rect = row_buttons[0].geometry()
            if position.x() < first_rect.left():
                return row_buttons[0], True

            for idx, button in enumerate(row_buttons):
                rect = button.geometry()
                if rect.left() <= position.x() <= rect.right():
                    # Cursor is above an icon, ignore so that only the gaps react.
                    return None
                next_button = row_buttons[idx + 1] if idx + 1 < len(row_buttons) else None
                if next_button:
                    gap_start = rect.right()
                    gap_end = next_button.geometry().left()
                    if gap_end > gap_start and gap_start <= position.x() <= gap_end:
                        return next_button, True

            last_rect = row_buttons[-1].geometry()
            if position.x() > last_rect.right():
                return row_buttons[-1], False

        return None



class LaunchpadWindow(QWidget):
    """Main fullscreen window containing the paginated application grid."""

    def __init__(self, apps: List[Application]) -> None:
        super().__init__()
        self._apps = apps
        self._background = _read_kde_wallpaper()
        self._search_query = ""

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

        self._filtered_apps: List[Application] = []
        self._update_filtered_apps()

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

    def _clear_pages(self) -> None:
        while self._stack.count():
            widget = self._stack.widget(0)
            self._stack.removeWidget(widget)
            widget.deleteLater()

    def _rebuild_pages(self) -> None:
        self._clear_pages()
        apps = self._filtered_apps
        empty_text = "Нет установленных приложений"
        if self._apps and not apps:
            empty_text = "Ничего не найдено"
        self._create_pages(apps, empty_text)
        if self._stack.count() > 0:
            self._stack.setCurrentIndex(0)
        self._update_page_indicator()

    def _create_pages(self, apps: List[Application], empty_text: str) -> None:
        for index in range(0, len(apps), APPS_PER_PAGE):
            page_apps = apps[index : index + APPS_PER_PAGE]
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

            for position, app in enumerate(page_apps):
                row = position // APP_COLUMNS
                column = position % APP_COLUMNS
                button = ApplicationButton(app)
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
        self._update_filtered_apps()

    def _update_page_indicator(self) -> None:
        self._page_indicator.set_page_count(self._stack.count())
        self._page_indicator.set_current_page(self._stack.currentIndex())

    def _update_filtered_apps(self) -> None:
        if not self._search_query:
            self._filtered_apps = list(self._apps)
        else:
            query = self._search_query
            self._filtered_apps = [app for app in self._apps if query in app.name.lower()]
        self._rebuild_pages()

    def _on_reorder_requested(self, source_path: str, target_path: str, insert_before: bool) -> None:
        index_lookup = {str(app.desktop_file): idx for idx, app in enumerate(self._apps)}
        source_index = index_lookup.get(source_path)
        target_index = index_lookup.get(target_path)
        if source_index is None or target_index is None:
            return
        if source_index == target_index:
            return

        app = self._apps.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        if not insert_before:
            target_index += 1
        target_index = max(0, min(target_index, len(self._apps)))
        self._apps.insert(target_index, app)
        _save_app_order(self._apps)
        self._update_filtered_apps()


def main() -> None:
    apps = load_applications()
    app = QApplication(sys.argv)
    window = LaunchpadWindow(apps)
    window.showFullScreen()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

