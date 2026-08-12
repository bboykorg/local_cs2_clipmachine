"""Error presentation.

Users never see a traceback. They see what went wrong, why it probably happened,
and buttons that do something about it — while the traceback goes to the log.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ...core.errors import AppError
from .. import theme
from .common import label, separator


class ErrorDialog(QDialog):
    """Title, plausible reasons, and the actions that fix them."""

    def __init__(
        self,
        error: AppError,
        parent: QWidget | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_open_logs: Callable[[], None] | None = None,
        on_retry: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CS2 Clip Generator")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setStyleSheet(f"QDialog {{ background: {theme.BACKGROUND_ALT}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(12)

        icon = label("⚠", "")
        icon.setStyleSheet(f"font-size: 24px; color: {theme.WARNING};")
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        title = label(error.title, "h2", wrap=True)
        header.addWidget(title, 1)
        layout.addLayout(header)

        if error.reasons:
            layout.addWidget(separator())
            layout.addWidget(label("Possible reasons", "faint"))
            for reason in error.reasons:
                layout.addWidget(label(f"•  {reason}", "muted", wrap=True))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        wanted = {action.lower() for action in error.actions}
        if on_open_settings and any("settings" in action for action in wanted):
            settings_button = QPushButton("Open Settings")
            settings_button.setObjectName("ghost")
            settings_button.clicked.connect(lambda: (self.accept(), on_open_settings()))
            buttons.addWidget(settings_button)
        if on_open_logs:
            logs_button = QPushButton("Open Logs Folder")
            logs_button.setObjectName("ghost")
            logs_button.clicked.connect(on_open_logs)
            buttons.addWidget(logs_button)
        if on_retry and any("retry" in action for action in wanted):
            retry_button = QPushButton("Retry")
            retry_button.setObjectName("primary")
            retry_button.clicked.connect(lambda: (self.accept(), on_retry()))
            buttons.addWidget(retry_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        # Remaining suggestions that have no button of their own.
        extras = [
            action
            for action in error.actions
            if not any(token in action.lower() for token in ("settings", "retry", "logs"))
        ]
        if extras:
            layout.addWidget(label("What to try: " + " · ".join(extras), "faint", wrap=True))


def show_error(
    error: AppError,
    parent: QWidget | None = None,
    on_open_settings: Callable[[], None] | None = None,
    on_open_logs: Callable[[], None] | None = None,
    on_retry: Callable[[], None] | None = None,
) -> None:
    ErrorDialog(error, parent, on_open_settings, on_open_logs, on_retry).exec()
