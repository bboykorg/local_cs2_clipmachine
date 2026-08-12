"""The main window: sidebar navigation, pages, status bar, crash recovery."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Settings
from ..core.models import MatchAnalysis
from ..render.queue import RenderQueue, resume_jobs
from . import theme
from .pages.dashboard import DashboardPage
from .pages.demo_page import DemoPage
from .pages.highlights_page import HighlightsPage
from .pages.montage_page import MontagePage
from .pages.render_page import RenderPage
from .pages.settings_page import SettingsPage
from .state import AppState
from .widgets.common import label

PAGES = [
    ("dashboard", "⌂   Dashboard"),
    ("demo", "◉   Demo"),
    ("highlights", "★   Highlights"),
    ("render", "▶   Render"),
    ("montage", "▣   Montage"),
    ("settings", "⚙   Settings"),
]


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.setWindowTitle("CS2 Clip Generator")
        self.resize(1360, 880)
        self.setMinimumSize(1120, 720)

        self.state = AppState(settings, self)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._sidebar())

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)

        self.dashboard_page = DashboardPage(self.state)
        self.demo_page = DemoPage(self.state)
        self.highlights_page = HighlightsPage(self.state)
        self.render_page = RenderPage(self.state)
        self.montage_page = MontagePage(self.state)
        self.settings_page = SettingsPage(self.state)
        self.pages = {
            "dashboard": self.dashboard_page,
            "demo": self.demo_page,
            "highlights": self.highlights_page,
            "render": self.render_page,
            "montage": self.montage_page,
            "settings": self.settings_page,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        status = QStatusBar()
        self.status_label = QLabel("Ready")
        status.addWidget(self.status_label, 1)
        self.backend_label = QLabel("")
        self.backend_label.setObjectName("faint")
        status.addPermanentWidget(self.backend_label)
        self.setStatusBar(status)

        self.state.status_message.connect(self.status_label.setText)
        self.state.navigate.connect(self._on_navigate)
        self.state.analysis_changed.connect(self._on_analysis)
        self.state.settings_changed.connect(lambda _: self._refresh_backend_label())

        self.show_page("dashboard")
        self._refresh_backend_label()

    # -- chrome ----------------------------------------------------------
    def _sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(212)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 8, 10, 14)
        layout.setSpacing(4)

        logo = label("CS2 CLIP", "logo")
        logo.setStyleSheet(
            f"color: {theme.TEXT}; font-size: 17px; font-weight: 700; padding: 18px 10px 0 10px;"
        )
        layout.addWidget(logo)
        layout.addWidget(label("GENERATOR", "logoSub"))

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in PAGES:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, page=key: self.show_page(page))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)

        layout.addStretch(1)
        self.sidebar_note = label("No demo loaded", "faint")
        self.sidebar_note.setWordWrap(True)
        self.sidebar_note.setStyleSheet(f"color: {theme.TEXT_FAINT}; padding: 0 8px;")
        layout.addWidget(self.sidebar_note)
        return sidebar

    def _refresh_backend_label(self) -> None:
        recording = self.state.settings.recording
        self.backend_label.setText(
            f"recorder: {recording.backend}   ·   cs2 control: {recording.playback_backend}"
        )

    # -- navigation ------------------------------------------------------
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        button = self.nav_buttons.get(key)
        if button and not button.isChecked():
            button.setChecked(True)

    def _on_navigate(self, target: str) -> None:
        """Handle both plain page keys and small commands from the pages."""
        if target.startswith("render:queue:"):
            ids = [value for value in target.split(":", 2)[2].split(",") if value]
            highlights = [h for h in (self.state.highlight(i) for i in ids) if h is not None]
            self.render_page.queue_highlights(highlights)
            self.show_page("render")
            return
        if target.startswith("preview:"):
            highlight = self.state.highlight(target.split(":", 1)[1])
            if highlight is not None:
                self.show_page("render")
                self.render_page.preview(highlight)
            return
        if target.startswith("demo:"):
            path = target.split(":", 1)[1]
            self.show_page("demo")
            self.demo_page.load_demo(path)
            return
        self.show_page(target)

    def _on_analysis(self, analysis: MatchAnalysis | None) -> None:
        if analysis is None:
            return
        self.sidebar_note.setText(f"{Path(analysis.demo_path).name}\n{len(self.state.highlights)} highlights")

    # -- crash recovery --------------------------------------------------
    def check_interrupted_render(self) -> None:
        """Offer to resume a render that was cut short by a crash or a kill."""
        state = RenderQueue.has_interrupted_render(self.state.settings)
        if state is None:
            return
        unfinished = state.unfinished
        answer = QMessageBox.question(
            self,
            "Interrupted render detected",
            f"{len(unfinished)} clip(s) from {Path(state.demo_path).name} were not finished.\n\nResume?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            RenderQueue.clear_journal(self.state.settings)
            return
        self.render_page.restore_jobs(resume_jobs(state))
        self.show_page("render")
        self.state.status(f"Restored {len(unfinished)} unfinished clips — press Start rendering")
        if Path(state.demo_path).is_file() and self.state.analysis is None:
            self.demo_page.load_demo(state.demo_path)

    # -- lifecycle -------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self.render_page.worker is not None and self.render_page.worker.isRunning():
            answer = QMessageBox.question(
                self,
                "Rendering in progress",
                "A clip is still rendering. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.render_page.cancel()
            self.render_page.worker.wait(4000)
        self.state.settings.save()
        event.accept()


def app_icon() -> QIcon:
    """A generated icon: no binary assets to ship or keep in sync."""
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPolygonF

    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0.0, QColor(theme.ACCENT))
    gradient.setColorAt(1.0, QColor(theme.ACCENT_ALT))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    painter.setBrush(QColor("#ffffff"))
    painter.drawPolygon(
        QPolygonF([QPointF(26, 20), QPointF(46, 32), QPointF(26, 44)])
    )
    painter.end()
    return QIcon(pixmap)
