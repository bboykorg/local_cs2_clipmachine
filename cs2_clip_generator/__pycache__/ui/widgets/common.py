"""Reusable widgets: cards, metrics, drop zone, timeline, highlight cards."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Highlight
from .. import theme

# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


class Card(QFrame):
    """A rounded translucent panel with a soft shadow."""

    def __init__(self, parent: QWidget | None = None, strong: bool = False, padding: int = 18) -> None:
        super().__init__(parent)
        self.setObjectName("cardStrong" if strong else "card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(padding, padding, padding, padding)
        self._layout.setSpacing(12)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.setGraphicsEffect(shadow)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget


def label(text: str, role: str = "", parent: QWidget | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text, parent)
    if role:
        widget.setObjectName(role)
    widget.setWordWrap(wrap)
    return widget


def heading(text: str, subtitle: str = "") -> QWidget:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(label(text, "h1"))
    if subtitle:
        layout.addWidget(label(subtitle, "muted", wrap=True))
    return container


def separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedHeight(1)
    return line


class Metric(QWidget):
    """One big number with a caption — the dashboard's basic unit."""

    def __init__(self, value: str, caption: str, color: str | None = None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.value_label = label(value, "metric")
        if color:
            self.value_label.setStyleSheet(f"color: {color};")
        layout.addWidget(self.value_label)
        layout.addWidget(label(caption, "faint"))

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class Badge(QLabel):
    """A small coloured pill: the highlight kind, a tag, a state."""

    def __init__(self, text: str, color: str = theme.ACCENT, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.set_color(color)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_color(self, color: str) -> None:
        # Qt reads an 8-digit hex colour as #AARRGGBB, so appending an alpha to a
        # #RRGGBB value silently produces a different colour. Build rgba() instead.
        rgb = QColor(color)
        fill = f"rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, 0.14)"
        border = f"rgba({rgb.red()}, {rgb.green()}, {rgb.blue()}, 0.38)"
        self.setStyleSheet(
            f"background: {fill}; color: {color}; border: 1px solid {border};"
            "border-radius: 9px; padding: 2px 9px; font-size: 10px; font-weight: 600;"
        )


class ProgressRow(QWidget):
    """A labelled progress bar that also shows a right-hand detail string."""

    def __init__(self, title: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.title_label = label(title, "h3")
        self.detail_label = label("", "faint")
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.title_label)
        top.addStretch(1)
        top.addWidget(self.detail_label)
        layout.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        layout.addWidget(self.bar)

    def set_progress(self, fraction: float | None, title: str = "", detail: str = "") -> None:
        if title:
            self.title_label.setText(title)
        self.detail_label.setText(detail)
        if fraction is None:
            self.bar.setRange(0, 0)  # indeterminate: total size unknown
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))


# ---------------------------------------------------------------------------
# Drag & drop
# ---------------------------------------------------------------------------


class DropZone(QFrame):
    """Accepts a dropped demo file (or archive) and reports the path."""

    file_dropped = Signal(str)
    browse_requested = Signal()

    SUFFIXES = (".dem", ".bz2", ".gz", ".zip")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(210)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        icon = label("⬇", "")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 30px; color: {theme.ACCENT};")
        title = label("Drop your CS2 demo here", "h2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = label(".dem · .dem.bz2 · .dem.gz · .dem.zip", "faint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = QPushButton("Select Demo")
        button.setObjectName("primary")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.browse_requested.emit)

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(hint)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

    # -- drag & drop -----------------------------------------------------
    def _acceptable(self, event: QDragEnterEvent | QDropEvent) -> str | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self.SUFFIXES):
                return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._acceptable(event):
            event.acceptProposedAction()
            self.setObjectName("dropZoneActive")
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self.setObjectName("dropZone")
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._acceptable(event)
        self.setObjectName("dropZone")
        self.style().unpolish(self)
        self.style().polish(self)
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


class TimelineWidget(QWidget):
    """The whole match on one line, with a marker per highlight.

    Markers are drawn from the highlight's tick position, coloured by kind and
    sized by score, so the shape of a match is visible at a glance. Clicking a
    marker emits its highlight id.
    """

    highlight_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(74)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._highlights: list[Highlight] = []
        self._total_ticks = 1
        self._tickrate = 64.0
        self._hit_boxes: list[tuple[QRectF, str]] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def set_data(self, highlights: Sequence[Highlight], total_ticks: int, tickrate: float) -> None:
        self._highlights = list(highlights)
        self._total_ticks = max(1, int(total_ticks))
        self._tickrate = tickrate or 64.0
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        position = event.position() if hasattr(event, "position") else QPoint(event.x(), event.y())
        for rect, highlight_id in self._hit_boxes:
            if rect.adjusted(-6, -10, 6, 10).contains(position):
                self.highlight_clicked.emit(highlight_id)
                return

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        baseline = self.height() / 2

        # The rail.
        gradient = QLinearGradient(0, 0, width, 0)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 26))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 12))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        painter.drawRoundedRect(QRectF(10, baseline - 2, max(1, width - 20), 4), 2, 2)

        # Start and end labels.
        painter.setPen(QPen(QColor(theme.TEXT_FAINT)))
        font = QFont(painter.font())
        font.setPointSizeF(8.0)
        painter.setFont(font)
        from ...utils.timeutil import format_duration

        painter.drawText(QRectF(4, baseline + 8, 60, 16), Qt.AlignmentFlag.AlignLeft, "0:00")
        painter.drawText(
            QRectF(width - 64, baseline + 8, 60, 16),
            Qt.AlignmentFlag.AlignRight,
            format_duration(self._total_ticks / self._tickrate),
        )

        self._hit_boxes.clear()
        if not self._highlights:
            painter.setPen(QPen(QColor(theme.TEXT_FAINT)))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No highlights yet")
            return

        best = max((h.score for h in self._highlights), default=1.0) or 1.0
        last_label_x = -1000.0
        for highlight in sorted(self._highlights, key=lambda h: h.first_kill_tick):
            fraction = min(1.0, max(0.0, highlight.first_kill_tick / self._total_ticks))
            x = 10 + fraction * max(1, width - 20)
            radius = 4 + 5 * min(1.0, highlight.score / best)
            color = QColor(theme.kind_color(highlight.kind.value))

            glow = QColor(color)
            glow.setAlpha(60)
            painter.setBrush(QBrush(glow))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(x - radius * 1.8, baseline - radius * 1.8, radius * 3.6, radius * 3.6))

            painter.setBrush(QBrush(color))
            rect = QRectF(x - radius, baseline - radius, radius * 2, radius * 2)
            painter.drawEllipse(rect)
            self._hit_boxes.append((rect, highlight.id))

            # Label only the notable markers, and only when there is room:
            # overlapping labels in a busy match are worse than none.
            if highlight.score >= best * 0.6 and x - last_label_x > 46:
                painter.setPen(QPen(color))
                painter.drawText(
                    QRectF(x - 26, baseline - 30, 52, 14),
                    Qt.AlignmentFlag.AlignCenter,
                    highlight.kind.value,
                )
                last_label_x = x


# ---------------------------------------------------------------------------
# Highlight card
# ---------------------------------------------------------------------------


class HighlightCard(Card):
    """One highlight, with Preview / Generate / Edit actions."""

    preview_requested = Signal(str)
    generate_requested = Signal(str)
    edit_requested = Signal(str)
    selection_changed = Signal(str, bool)

    def __init__(self, highlight: Highlight, tickrate: float, parent: QWidget | None = None) -> None:
        super().__init__(parent, padding=14)
        self.highlight = highlight
        self.setMinimumWidth(300)

        top = QHBoxLayout()
        top.setSpacing(8)
        kind = highlight.kind.value
        if highlight.clutch_vs and kind == "CLUTCH":
            kind = f"1v{highlight.clutch_vs}"
        top.addWidget(Badge(kind, theme.kind_color(highlight.kind.value)))
        top.addWidget(label(f"Round {highlight.round_number}", "muted"))
        top.addStretch(1)
        score = label(f"★ {highlight.score:.0f}", "h3")
        score.setStyleSheet(f"color: {theme.WARNING};")
        top.addWidget(score)
        self.body().addLayout(top)

        self.body().addWidget(label(highlight.title or kind, "h3", wrap=True))
        self.body().addWidget(label(highlight.player_name, "muted"))

        details = [
            f"{highlight.kill_count} kill" + ("s" if highlight.kill_count != 1 else ""),
            f"{highlight.duration_seconds(tickrate):.0f}s",
        ]
        if highlight.headshot_count:
            details.append(f"{highlight.headshot_count} HS")
        self.body().addWidget(label(" · ".join(details), "faint"))

        if highlight.tags:
            tags = QHBoxLayout()
            tags.setSpacing(4)
            for tag in highlight.tags[:4]:
                tags.addWidget(Badge(tag.value.replace("_", " ").title(), theme.TEXT_FAINT))
            tags.addStretch(1)
            self.body().addLayout(tags)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        preview = QPushButton("Preview")
        preview.setObjectName("ghost")
        preview.setToolTip("Open this moment in CS2 without recording")
        preview.clicked.connect(lambda: self.preview_requested.emit(highlight.id))
        edit = QPushButton("Edit")
        edit.setObjectName("ghost")
        edit.setToolTip("Adjust the start and end of this clip")
        edit.clicked.connect(lambda: self.edit_requested.emit(highlight.id))
        generate = QPushButton("Generate")
        generate.setObjectName("primary")
        generate.clicked.connect(lambda: self.generate_requested.emit(highlight.id))
        actions.addWidget(preview)
        actions.addWidget(edit)
        actions.addStretch(1)
        actions.addWidget(generate)
        self.body().addLayout(actions)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def chip_row(
    options: Iterable[tuple[str, str]],
    on_toggle: Callable[[str, bool], None],
    checked: Sequence[str] = (),
) -> tuple[QWidget, dict[str, QPushButton]]:
    """A row of toggleable filter chips."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    buttons: dict[str, QPushButton] = {}
    for key, text in options:
        button = QPushButton(text)
        button.setObjectName("chip")
        button.setCheckable(True)
        button.setChecked(key in checked)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.toggled.connect(lambda state, k=key: on_toggle(k, state))
        layout.addWidget(button)
        buttons[key] = button
    layout.addStretch(1)
    return container, buttons


def fade_in(widget: QWidget, duration: int = 220) -> None:
    """A subtle appearance animation; purely cosmetic, never blocking."""
    animation = QPropertyAnimation(widget, b"windowOpacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
