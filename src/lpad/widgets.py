"""Qt widgets for the Launchpad UI."""
from __future__ import annotations

import math
import os
from typing import List, Optional

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from .config import LaunchpadConfig
from .desktop import DesktopEntry


class AnimatedStackedWidget(QStackedWidget):
    """A QStackedWidget with slide animations."""

    animation_finished = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._duration = 300
        self._current_animation: Optional[QPropertyAnimation] = None
        self._next_animation: Optional[QPropertyAnimation] = None

    def slide_to(self, index: int) -> None:
        if index == self.currentIndex() or index < 0 or index >= self.count():
            return
        if self._current_animation and self._current_animation.state() == QPropertyAnimation.Running:
            return
        current_widget = self.currentWidget()
        next_widget = self.widget(index)
        if current_widget is None or next_widget is None:
            self.setCurrentIndex(index)
            self.animation_finished.emit(index)
            return

        frame_rect = self.frameRect()
        width = frame_rect.width()
        if width <= 0:
            self.setCurrentIndex(index)
            self.animation_finished.emit(index)
            return
        direction = 1 if index > self.currentIndex() else -1

        current_start = QPoint(0, 0)
        current_end = QPoint(-direction * width, 0)
        next_start = QPoint(direction * width, 0)
        next_end = QPoint(0, 0)

        next_widget.setGeometry(QRect(next_start, frame_rect.size()))
        next_widget.show()

        self._current_animation = QPropertyAnimation(current_widget, b"pos", self)
        self._current_animation.setDuration(self._duration)
        self._current_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._current_animation.setStartValue(current_start)
        self._current_animation.setEndValue(current_end)

        self._next_animation = QPropertyAnimation(next_widget, b"pos", self)
        self._next_animation.setDuration(self._duration)
        self._next_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._next_animation.setStartValue(next_start)
        self._next_animation.setEndValue(next_end)

        def finalize() -> None:
            self.setCurrentIndex(index)
            current_widget.move(0, 0)
            next_widget.move(0, 0)
            self.animation_finished.emit(index)

        self._next_animation.finished.connect(finalize)
        self._current_animation.start()
        self._next_animation.start()


class AppIconWidget(QFrame):
    """Widget representing an application icon."""

    launch_requested = Signal(DesktopEntry)
    hide_requested = Signal(DesktopEntry)

    def __init__(self, entry: DesktopEntry, text_color: QColor, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setFrameStyle(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(96, 96)
        pixmap = load_entry_pixmap(entry)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(icon_label)

        text_label = QLabel(entry.name)
        text_label.setAlignment(Qt.AlignCenter)
        text_label.setWordWrap(True)
        text_label.setStyleSheet(f"color: {text_color.name()};")
        layout.addWidget(text_label)

        self.icon_label = icon_label
        self.text_label = text_label

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.launch_requested.emit(self.entry)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.launch_requested.emit(self.entry)
        elif event.key() == Qt.Key_Delete:
            self.hide_requested.emit(self.entry)
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):  # type: ignore[override]
        menu = QMenu(self)
        hide_action = menu.addAction("Скрыть")
        chosen = menu.exec(event.globalPos())
        if chosen == hide_action:
            self.hide_requested.emit(self.entry)


def load_entry_pixmap(entry: DesktopEntry) -> QPixmap:
    if entry.icon_path and os.path.exists(entry.icon_path):
        pixmap = QPixmap(entry.icon_path)
        if not pixmap.isNull():
            return pixmap
    if entry.icon_name:
        icon = QIcon.fromTheme(entry.icon_name)
        if not icon.isNull():
            return icon.pixmap(96, 96)
    app = QApplication.instance()
    if app:
        return app.style().standardIcon(QStyle.SP_FileIcon).pixmap(96, 96)
    return QPixmap()


class IconPage(QWidget):
    """A grid of application icons."""

    launch_requested = Signal(DesktopEntry)
    hide_requested = Signal(DesktopEntry)

    def __init__(self, entries: List[DesktopEntry], text_color: QColor, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(32, 16, 32, 16)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(16)

        columns = 5
        rows = 7
        per_page = columns * rows

        for index, entry in enumerate(entries[:per_page]):
            row = index // columns
            column = index % columns
            widget = AppIconWidget(entry, text_color, self)
            widget.launch_requested.connect(self.launch_requested)
            widget.hide_requested.connect(self.hide_requested)
            grid.addWidget(widget, row, column)

        for column in range(columns):
            grid.setColumnStretch(column, 1)
        for row in range(rows):
            grid.setRowStretch(row, 1)


class PageIndicator(QWidget):
    """Dots indicating the number of pages."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.page_count = 0
        self.current_index = 0
        self.dot_size = 10
        self.dot_spacing = 12

    def set_page_count(self, count: int) -> None:
        self.page_count = count
        self.updateGeometry()
        self.update()

    def set_current_index(self, index: int) -> None:
        self.current_index = index
        self.update()

    def sizeHint(self):  # type: ignore[override]
        width = self.page_count * self.dot_size + max(0, self.page_count - 1) * self.dot_spacing
        return QSize(max(width, self.dot_size), self.dot_size * 2)

    def minimumSizeHint(self):  # type: ignore[override]
        return self.sizeHint()

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        total_width = self.page_count * self.dot_size + max(0, self.page_count - 1) * self.dot_spacing
        start_x = rect.center().x() - total_width / 2
        center_y = rect.center().y()
        for index in range(self.page_count):
            color = QColor("#444444")
            if index == self.current_index:
                color = QColor("#0078d7")
            x = start_x + index * (self.dot_size + self.dot_spacing)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPoint(int(x + self.dot_size / 2), center_y), self.dot_size // 2, self.dot_size // 2)


class LaunchpadWindow(QWidget):
    """Main Launchpad window."""

    def __init__(self, entries: List[DesktopEntry], config: LaunchpadConfig, background: QPixmap, text_color: QColor) -> None:
        super().__init__()
        self.config = config
        self.background_pixmap = background
        self.text_color = text_color
        self.all_entries = entries
        self.filtered_entries = entries

        self.setWindowTitle("Launchpad")
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setWindowState(Qt.WindowFullScreen)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(48, 32, 48, 32)
        root_layout.setSpacing(12)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Поиск")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.textChanged.connect(self.update_filter)
        root_layout.addWidget(self.search_field)

        self.stack = AnimatedStackedWidget()
        self.stack.animation_finished.connect(self.on_page_changed)
        root_layout.addWidget(self.stack, stretch=1)

        nav_layout = QHBoxLayout()
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(24)

        self.prev_button = QPushButton("◀")
        self.prev_button.setFixedSize(48, 48)
        self.prev_button.clicked.connect(self.previous_page)
        nav_layout.addWidget(self.prev_button)

        self.indicator = PageIndicator()
        nav_layout.addWidget(self.indicator, alignment=Qt.AlignCenter)

        self.next_button = QPushButton("▶")
        self.next_button.setFixedSize(48, 48)
        self.next_button.clicked.connect(self.next_page)
        nav_layout.addWidget(self.next_button)

        root_layout.addLayout(nav_layout)

        QApplication.instance().installEventFilter(self)
        self.populate_pages()

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        if self.background_pixmap.isNull():
            painter.fillRect(self.rect(), Qt.white)
        else:
            scaled = self.background_pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            painter.drawPixmap(self.rect(), scaled, scaled.rect())
        super().paintEvent(event)

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        self.search_field.setFocus(Qt.ActiveWindowFocusReason)

    def eventFilter(self, source, event):  # type: ignore[override]
        if event.type() == QEvent.KeyPress:
            key_event: QKeyEvent = event  # type: ignore[assignment]
            if key_event.key() == Qt.Key_Escape:
                QApplication.instance().quit()
                return True
            if source is not self.search_field and key_event.text():
                if not key_event.modifiers() & Qt.ControlModifier:
                    self.search_field.setFocus()
                    QApplication.sendEvent(self.search_field, event)
                    return True
        elif event.type() == QEvent.Wheel:
            delta = event.angleDelta().y()
            if delta < 0:
                self.next_page()
            elif delta > 0:
                self.previous_page()
            return True
        return super().eventFilter(source, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Right:
            self.next_page()
        elif event.key() == Qt.Key_Left:
            self.previous_page()
        else:
            super().keyPressEvent(event)

    def populate_pages(self) -> None:
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()

        per_page = 35
        total = len(self.filtered_entries)
        total_pages = max(1, math.ceil(total / per_page))

        for page_index in range(total_pages):
            start = page_index * per_page
            end = start + per_page
            subset = self.filtered_entries[start:end]
            page = IconPage(subset, self.text_color)
            page.launch_requested.connect(self.launch_entry)
            page.hide_requested.connect(self.hide_entry)
            self.stack.addWidget(page)

        self.stack.setCurrentIndex(0)
        self.indicator.set_page_count(total_pages)
        self.on_page_changed(0)

    def on_page_changed(self, index: int) -> None:
        self.indicator.set_current_index(index)
        self.prev_button.setEnabled(index > 0)
        self.next_button.setEnabled(index < self.stack.count() - 1)

    def update_filter(self, text: str) -> None:
        lowered = text.lower().strip()
        if not lowered:
            self.filtered_entries = self.all_entries
        else:
            self.filtered_entries = [
                entry
                for entry in self.all_entries
                if lowered in entry.name.lower()
                or any(lowered in category.lower() for category in entry.categories)
            ]
        self.populate_pages()

    def next_page(self) -> None:
        index = self.stack.currentIndex()
        if index < self.stack.count() - 1:
            self.stack.slide_to(index + 1)

    def previous_page(self) -> None:
        index = self.stack.currentIndex()
        if index > 0:
            self.stack.slide_to(index - 1)

    def launch_entry(self, entry: DesktopEntry) -> None:
        entry.launch()

    def hide_entry(self, entry: DesktopEntry) -> None:
        self.config.hide(entry.desktop_id)
        self.all_entries = [e for e in self.all_entries if e.desktop_id != entry.desktop_id]
        self.update_filter(self.search_field.text())


__all__ = [
    "LaunchpadWindow",
]
