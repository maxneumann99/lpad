from __future__ import annotations

import os
import sys
import threading
from functools import lru_cache
from math import ceil
from pathlib import Path
from typing import List, Optional, Set

from PyQt5.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPointF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
    QPropertyAnimation,
)
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from Xlib import X, XK, display
except ImportError:  # pragma: no cover - optional dependency
    display = None  # type: ignore
    XK = None  # type: ignore
    X = None  # type: ignore

from .background import Background
from .config import Config
from .desktop_entries import DesktopEntry, data_dirs, iter_desktop_entries

COLUMNS = 7
ROWS = 5
ITEMS_PER_PAGE = COLUMNS * ROWS

ICON_EXTENSIONS = (".png", ".svg", ".xpm")


def _resolve_path(path: Path) -> Optional[Path]:
    try:
        return path.resolve(strict=True)
    except OSError:
        return None


@lru_cache()
def _icon_theme_roots() -> tuple[Path, ...]:
    seen: Set[Path] = set()
    roots: List[Path] = []

    for base in data_dirs():
        candidate = (base / "icons").expanduser()
        if not candidate.is_dir():
            continue
        resolved = _resolve_path(candidate)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)

    home_icons = Path.home() / ".icons"
    if home_icons.is_dir():
        resolved = _resolve_path(home_icons)
        if resolved and resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)

    return tuple(roots)


@lru_cache()
def _icon_file_roots() -> tuple[Path, ...]:
    roots = list(_icon_theme_roots())
    seen: Set[Path] = set(roots)

    for base in data_dirs():
        candidate = (base / "pixmaps").expanduser()
        if not candidate.is_dir():
            continue
        resolved = _resolve_path(candidate)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        roots.append(resolved)

    return tuple(roots)


def _ensure_icon_theme_paths() -> None:
    current_paths = []
    seen: Set[Path] = set()
    for existing in QIcon.themeSearchPaths():
        path = Path(existing)
        resolved = _resolve_path(path) if path.exists() else None
        if resolved and resolved not in seen:
            seen.add(resolved)
            current_paths.append(str(resolved))

    updated = False
    for root in _icon_theme_roots():
        if root in seen:
            continue
        seen.add(root)
        current_paths.append(str(root))
        updated = True

    if updated:
        QIcon.setThemeSearchPaths(current_paths)

    if not QIcon.themeName():
        theme = os.environ.get("XDG_ICON_THEME") or "hicolor"
        QIcon.setThemeName(theme)


@lru_cache(maxsize=256)
def _find_icon_file(name: str) -> Optional[Path]:
    raw = Path(name)
    if raw.is_absolute():
        return raw if raw.exists() else None

    candidates: List[str] = []
    if raw.suffix:
        candidates.append(name)
    else:
        candidates.append(name)
        candidates.extend(f"{name}{ext}" for ext in ICON_EXTENSIONS)

    for root in _icon_file_roots():
        for candidate in candidates:
            candidate_path = (root / candidate).expanduser()
            if candidate_path.exists():
                return candidate_path

        if "/" not in name and not raw.suffix:
            for ext in ICON_EXTENSIONS:
                for match in root.rglob(f"{name}{ext}"):
                    return match

    return None

SUPER_KEY_SYMS = (
    "Super_L",
    "Super_R",
    "Meta_L",
    "Meta_R",
)


class SuperKeyListener(QObject):
    triggered = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._pressed = False
        self._active = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._display = None
        self._root = None
        self._keycodes: Set[int] = set()

        if display is None:
            return

        try:
            self._display = display.Display()
            self._root = self._display.screen().root
        except Exception:
            self._display = None
            self._root = None
            return

        self._install_grabs()
        if not self._keycodes:
            self._cleanup_display()
            return

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._active = True

    @property
    def is_active(self) -> bool:
        return self._active

    def stop(self) -> None:
        self._stop_event.set()
        self._active = False
        self._release_grabs()
        self._cleanup_display()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None

    def _install_grabs(self) -> None:
        if self._display is None or self._root is None or XK is None or X is None:
            return

        modifier_masks = self._modifier_combinations()

        for name in SUPER_KEY_SYMS:
            keysym = XK.string_to_keysym(name)
            if keysym == 0:
                continue
            keycode = self._display.keysym_to_keycode(keysym)
            if keycode == 0:
                continue
            self._keycodes.add(keycode)
            for mask in modifier_masks:
                try:
                    self._root.grab_key(keycode, mask, True, X.GrabModeAsync, X.GrabModeAsync)
                except Exception:
                    continue
        try:
            self._display.sync()
        except Exception:
            pass

    def _release_grabs(self) -> None:
        if self._display is None or self._root is None or X is None:
            return
        for keycode in self._keycodes:
            for mask in self._modifier_combinations():
                try:
                    self._root.ungrab_key(keycode, mask)
                except Exception:
                    continue
        try:
            self._display.sync()
        except Exception:
            pass
        self._keycodes.clear()

    def _run(self) -> None:
        if self._display is None or X is None:
            return
        while not self._stop_event.is_set():
            try:
                event = self._display.next_event()
            except (OSError, AttributeError):
                break
            except Exception:
                break
            if event.type == X.KeyPress and event.detail in self._keycodes:
                if not self._pressed:
                    self._pressed = True
                    self.triggered.emit()
            elif event.type == X.KeyRelease and event.detail in self._keycodes:
                self._pressed = False

    def _cleanup_display(self) -> None:
        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                pass
        self._display = None
        self._root = None

    @staticmethod
    def _modifier_combinations() -> Set[int]:
        if X is None:
            return {0}
        masks = [0, X.LockMask, X.Mod2Mask, X.Mod5Mask]
        combos: Set[int] = set()
        for mask in masks:
            combos.add(mask)
        combos.add(X.LockMask | X.Mod2Mask)
        combos.add(X.LockMask | X.Mod5Mask)
        combos.add(X.Mod2Mask | X.Mod5Mask)
        combos.add(X.LockMask | X.Mod2Mask | X.Mod5Mask)
        combos.add(getattr(X, "AnyModifier", 0))
        return combos or {0}


class SearchField(QLineEdit):
    navigate_requested = pyqtSignal(int)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Left:
            self.navigate_requested.emit(-1)
        elif event.key() == Qt.Key_Right:
            self.navigate_requested.emit(1)
        super().keyPressEvent(event)


class LaunchpadWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Launchpad")
        self.setWindowFlag(Qt.FramelessWindowHint, False)
        self.setWindowState(Qt.WindowFullScreen)

        self.config = Config.load()
        self.background = Background(self.config)

        self._super_listener = SuperKeyListener(self)
        self._super_listener.triggered.connect(self.handle_super_key)

        self._build_ui()
        self._load_entries()
        self._apply_background()
        if not self._super_listener.is_active:
            self.show_launcher()

    def handle_super_key(self) -> None:
        if self.isVisible():
            self.hide_launcher()
        else:
            self.show_launcher()

    def show_launcher(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self.search_field.setFocus)

    def hide_launcher(self) -> None:
        self.hide()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(20)

        self.search_field = SearchField()
        self.search_field.setPlaceholderText("Поиск приложений")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.textChanged.connect(self._filter_entries)
        self.search_field.setFixedHeight(48)
        self.search_field.setStyleSheet(
            "QLineEdit { padding: 0 16px; border-radius: 24px; font-size: 18px; }"
        )
        main_layout.addWidget(self.search_field)

        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(20)
        main_layout.addLayout(center_layout)

        self.left_button = NavigationButton("◀")
        self.left_button.clicked.connect(lambda: self.grid.navigate(-1))
        center_layout.addWidget(self.left_button)

        self.grid = AppGrid()
        self.grid.page_changed.connect(self._on_page_changed)
        self.grid.hide_requested.connect(self._hide_entry)
        self.search_field.navigate_requested.connect(self.grid.navigate)
        center_layout.addWidget(self.grid, 1)

        self.right_button = NavigationButton("▶")
        self.right_button.clicked.connect(lambda: self.grid.navigate(1))
        center_layout.addWidget(self.right_button)

        self.pagination = PaginationDots()
        main_layout.addWidget(self.pagination, alignment=Qt.AlignHCenter)


    def _load_entries(self) -> None:
        self._refresh_entries(self.search_field.text())

    def _filter_entries(self, text: str) -> None:
        query = text.strip().lower()
        if not query:
            filtered = self.entries
        else:
            filtered = [entry for entry in self.entries if query in entry.name.lower()]
        self._apply_entries(filtered)

    def _apply_entries(self, entries: List[DesktopEntry]) -> None:
        self.grid.set_entries(entries)
        self.pagination.update_state(self.grid.page_count, self.grid.current_page)
        self._update_arrow_visibility()

    def _refresh_entries(self, search_text: Optional[str] = None) -> None:
        self.entries = iter_desktop_entries(hidden=self.config.hidden)
        if search_text is None:
            search_text = self.search_field.text()
        self._filter_entries(search_text)

    def _hide_entry(self, entry: DesktopEntry) -> None:
        self.config.add_hidden(str(entry.desktop_file))
        search_text = self.search_field.text()
        QTimer.singleShot(0, lambda text=search_text: self._refresh_entries(text))

    def _apply_background(self) -> None:
        if self.background.pixmap:
            image_path = self.background.path
            if image_path:
                stylesheet = (
                    "QMainWindow {"
                    f"background-image: url('{image_path}');"
                    "background-position: center;"
                    "background-repeat: no-repeat;"
                    "background-color: #ffffff;"
                    "}"
                )
                self.setStyleSheet(stylesheet)
        else:
            palette = self.palette()
            palette.setColor(QPalette.Window, Qt.white)
            self.setPalette(palette)

        luminance = self.background.luminance()
        is_light = luminance > 0.6
        text_color = "#101010" if is_light else "#f0f0f0"
        field_bg = "rgba(255, 255, 255, 0.75)" if is_light else "rgba(0, 0, 0, 0.45)"
        placeholder_color = "#4a4a4a" if is_light else "#d0d0d0"
        self.grid.set_text_color(text_color)
        self.search_field.setStyleSheet(
            "QLineEdit { padding: 0 16px; border-radius: 24px; font-size: 18px;"
            f" color: {text_color};"
            f" background-color: {field_bg};"
            " }"
            f"QLineEdit::placeholder {{ color: {placeholder_color}; }}"
        )

    def _on_page_changed(self, current: int, total: int) -> None:
        self.pagination.update_state(total, current)
        self._update_arrow_visibility()

    def _update_arrow_visibility(self) -> None:
        self.left_button.setEnabled(self.grid.current_page > 0)
        self.right_button.setEnabled(self.grid.current_page < self.grid.page_count - 1)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.hide_launcher()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_PageUp):
            self.grid.navigate(-1)
            return
        if event.key() in (Qt.Key_Right, Qt.Key_PageDown):
            self.grid.navigate(1)
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._super_listener.stop()
        super().closeEvent(event)


class NavigationButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setFixedSize(56, 56)
        self.setStyleSheet(
            "QPushButton { border-radius: 28px; font-size: 18px; background-color: rgba(0,0,0,0.35);"
            " color: white; }"
            "QPushButton:disabled { background-color: rgba(0,0,0,0.15); color: rgba(255,255,255,0.4); }"
        )


class AppGrid(QWidget):
    page_changed = pyqtSignal(int, int)
    hide_requested = pyqtSignal(DesktopEntry)

    def __init__(self) -> None:
        super().__init__()
        self.entries: List[DesktopEntry] = []
        self.current_page = 0
        self._text_color = "#101010"

        self.setAttribute(Qt.WA_TranslucentBackground)

        self.scroll = QScrollArea()
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.viewport().setFocusPolicy(Qt.NoFocus)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.installEventFilter(self)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )

        self.container = QWidget()
        self.container.setAttribute(Qt.WA_TranslucentBackground)
        self.container.setAutoFillBackground(False)
        self.container_layout = QHBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(0)
        self.scroll.setWidget(self.container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

        self.pages: List[AppPage] = []
        self.animation = QPropertyAnimation(self.scroll.horizontalScrollBar(), b"value")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)

    @property
    def page_count(self) -> int:
        return max(1, len(self.pages))

    def set_entries(self, entries: List[DesktopEntry]) -> None:
        self.entries = entries
        self._rebuild_pages()
        self.set_page(0, animate=False)
        for page in self.pages:
            page.set_text_color(self._text_color)

    def _rebuild_pages(self) -> None:
        for page in self.pages:
            page.deleteLater()
        self.pages.clear()
        self._clear_container()

        if not self.entries:
            placeholder = AppPage([])
            placeholder.show_empty_message("Нет приложений")
            self.pages.append(placeholder)
            self.container_layout.addWidget(placeholder)
            self._update_page_sizes()
            return

        total_pages = ceil(len(self.entries) / ITEMS_PER_PAGE)
        for index in range(total_pages):
            slice_entries = self.entries[index * ITEMS_PER_PAGE : (index + 1) * ITEMS_PER_PAGE]
            page = AppPage(slice_entries)
            page.hide_requested.connect(self.hide_requested.emit)
            self.pages.append(page)
            self.container_layout.addWidget(page)
        self._update_page_sizes()

    def _clear_container(self) -> None:
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def set_page(self, index: int, *, animate: bool = True) -> None:
        if not self.pages:
            return
        index = max(0, min(index, len(self.pages) - 1))
        old = self.current_page
        self.current_page = index
        target_value = index * self.scroll.viewport().width()
        if animate:
            self.animation.stop()
            self.animation.setStartValue(self.scroll.horizontalScrollBar().value())
            self.animation.setEndValue(target_value)
            self.animation.start()
        else:
            self.scroll.horizontalScrollBar().setValue(target_value)
        if old != self.current_page:
            self.page_changed.emit(self.current_page, self.page_count)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_page_sizes()
        self.set_page(self.current_page, animate=False)

    def navigate(self, delta: int) -> None:
        self.set_page(self.current_page + delta)

    def set_text_color(self, color: str) -> None:
        self._text_color = color
        for page in self.pages:
            page.set_text_color(color)

    def _update_page_sizes(self) -> None:
        width = self.scroll.viewport().width()
        height = self.scroll.viewport().height()
        if width <= 0 or height <= 0:
            return
        for page in self.pages:
            page.setFixedSize(width, height)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Wheel:
            delta = event.angleDelta()
            if delta.x() > 0 or delta.y() > 0:
                self.navigate(-1)
                event.accept()
            elif delta.x() < 0 or delta.y() < 0:
                self.navigate(1)
                event.accept()
            if event.isAccepted():
                return True
        if event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Left, Qt.Key_PageUp):
                self.navigate(-1)
                event.accept()
                return True
            if key in (Qt.Key_Right, Qt.Key_PageDown):
                self.navigate(1)
                event.accept()
                return True
        return super().eventFilter(obj, event)


class AppPage(QWidget):
    hide_requested = pyqtSignal(DesktopEntry)

    def __init__(self, entries: List[DesktopEntry]) -> None:
        super().__init__()
        self.entries = entries
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(40, 20, 40, 20)
        self.grid.setHorizontalSpacing(30)
        self.grid.setVerticalSpacing(30)

        self.empty_label: Optional[QLabel] = None
        self.buttons: List[AppButton] = []

        if entries:
            self._populate(entries)

    def _populate(self, entries: List[DesktopEntry]) -> None:
        for index, entry in enumerate(entries):
            row = index // COLUMNS
            column = index % COLUMNS
            button = AppButton(entry)
            button.clicked.connect(entry.launch)
            button.hide_requested.connect(self.hide_requested.emit)
            self.grid.addWidget(button, row, column, alignment=Qt.AlignCenter)
            self.buttons.append(button)
        for row in range(ROWS):
            self.grid.setRowStretch(row, 1)
        for column in range(COLUMNS):
            self.grid.setColumnStretch(column, 1)

    def show_empty_message(self, text: str) -> None:
        if not self.empty_label:
            self.empty_label = QLabel(text)
            font = QFont()
            font.setPointSize(22)
            self.empty_label.setFont(font)
            self.empty_label.setAlignment(Qt.AlignCenter)
            self.grid.addWidget(self.empty_label, 0, 0, ROWS, COLUMNS)

    def set_text_color(self, color: str) -> None:
        for button in self.buttons:
            button.set_text_color(color)
        if self.empty_label:
            self.empty_label.setStyleSheet(f"color: {color};")


class AppButton(QToolButton):
    hide_requested = pyqtSignal(DesktopEntry)

    def __init__(self, entry: DesktopEntry) -> None:
        super().__init__()
        self.entry = entry
        self.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(96, 96))
        self.setText(entry.name)
        self.setToolTip(entry.name)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumSize(120, 140)
        self._update_icon(entry)

    def _update_icon(self, entry: DesktopEntry) -> None:
        icon: Optional[QIcon] = None
        if entry.icon:
            raw_path = Path(entry.icon)
            candidate_paths = []
            if raw_path.is_absolute():
                candidate_paths.append(raw_path)
            else:
                candidate_paths.append((entry.desktop_file.parent / raw_path).expanduser())
                candidate_paths.append(raw_path.expanduser())

            for candidate in candidate_paths:
                if not candidate.exists():
                    continue
                icon = QIcon(str(candidate))
                if icon and not icon.isNull():
                    break
                icon = None

            if not icon or icon.isNull():
                _ensure_icon_theme_paths()
                themed_icon = QIcon.fromTheme(entry.icon)
                if themed_icon and not themed_icon.isNull():
                    icon = themed_icon

            if (not icon or icon.isNull()) and not raw_path.is_absolute():
                fallback_path = _find_icon_file(entry.icon)
                if fallback_path:
                    icon = QIcon(str(fallback_path))
        if not icon or icon.isNull():
            icon = QApplication.style().standardIcon(QApplication.style().SP_FileIcon)
        self.setIcon(icon)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        menu = QMenu(self)
        hide_action = QAction("Скрыть", self)
        hide_action.triggered.connect(lambda: self.hide_requested.emit(self.entry))
        menu.addAction(hide_action)
        menu.exec_(event.globalPos())

    def set_text_color(self, color: str) -> None:
        self.setStyleSheet(
            "QToolButton {"
            f"color: {color};"
            "font-size: 14px;"
            "border: none;"
            "padding: 8px;"
            "background-color: transparent;"
            "}"
            "QToolButton::menu-indicator { width: 0; height: 0; }"
        )


class PaginationDots(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.total = 1
        self.current = 0
        self.setFixedHeight(30)

    def update_state(self, total: int, current: int) -> None:
        self.total = max(1, total)
        self.current = max(0, min(current, self.total - 1))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        diameter = 12
        spacing = 12
        total_width = self.total * diameter + (self.total - 1) * spacing
        start_x = (self.width() - total_width) / 2
        y = self.height() / 2 - diameter / 2
        for index in range(self.total):
            if index == self.current:
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.setPen(Qt.NoPen)
            else:
                painter.setBrush(QColor(255, 255, 255, 120))
                painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(start_x + index * (diameter + spacing) + diameter / 2, y + diameter / 2), diameter / 2, diameter / 2)
        painter.end()


def main() -> None:
    """Launch the application, handling Ctrl+C gracefully."""

    app = QApplication(sys.argv)
    window: Optional[LaunchpadWindow] = None
    exit_code = 0
    try:
        window = LaunchpadWindow()
        exit_code = app.exec_()
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        if window is not None:
            window.close()
        app.quit()

    sys.exit(exit_code)
