"""Dark theme.

The look is built from a small palette and one style sheet rather than from
per-widget styling, so pages stay readable and everything shifts together when a
colour changes. "Glass" here means a translucent, slightly lighter surface over a
dark gradient background — cheap to render and it survives being resized, unlike
real blur.
"""

from __future__ import annotations

BACKGROUND = "#0b0d14"
BACKGROUND_ALT = "#11141f"
SURFACE = "rgba(255, 255, 255, 0.04)"
SURFACE_STRONG = "rgba(255, 255, 255, 0.07)"
BORDER = "rgba(255, 255, 255, 0.09)"
BORDER_STRONG = "rgba(255, 255, 255, 0.16)"

TEXT = "#eaecf5"
TEXT_MUTED = "#9aa3bd"
TEXT_FAINT = "#6a7391"

ACCENT = "#5b8cff"
ACCENT_ALT = "#a06bff"
ACCENT_SOFT = "rgba(91, 140, 255, 0.16)"
SUCCESS = "#3ddc97"
WARNING = "#f5c451"
DANGER = "#ff5c72"

#: Highlight kind → accent colour, used by cards, the timeline and the queue.
KIND_COLORS = {
    "ACE": "#ffb347",
    "4K": "#ff7ac6",
    "3K": "#a06bff",
    "2K": "#5b8cff",
    "CLUTCH": "#3ddc97",
    "KILL": "#7c88a8",
}

FONT_STACK = '"Segoe UI Variable", "Segoe UI", Inter, Roboto, system-ui, sans-serif'


def kind_color(kind: str) -> str:
    return KIND_COLORS.get(kind.upper(), ACCENT)


def stylesheet() -> str:
    return f"""
    * {{
        font-family: {FONT_STACK};
        color: {TEXT};
    }}
    QWidget#root {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {BACKGROUND}, stop:0.55 {BACKGROUND_ALT}, stop:1 #0d1020);
    }}
    QWidget#sidebar {{
        background: rgba(0, 0, 0, 0.25);
        border-right: 1px solid {BORDER};
    }}
    QLabel#logo {{
        font-size: 17px;
        font-weight: 700;
        letter-spacing: 0.5px;
        padding: 18px 18px 6px 18px;
    }}
    QLabel#logoSub {{
        color: {TEXT_FAINT};
        font-size: 11px;
        padding: 0 18px 18px 18px;
    }}
    QPushButton#navButton {{
        background: transparent;
        border: none;
        border-radius: 10px;
        padding: 10px 14px;
        text-align: left;
        font-size: 13px;
        color: {TEXT_MUTED};
    }}
    QPushButton#navButton:hover {{
        background: {SURFACE};
        color: {TEXT};
    }}
    QPushButton#navButton:checked {{
        background: {ACCENT_SOFT};
        color: {TEXT};
        font-weight: 600;
    }}

    QFrame#card {{
        background: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 16px;
    }}
    QFrame#cardStrong {{
        background: {SURFACE_STRONG};
        border: 1px solid {BORDER_STRONG};
        border-radius: 16px;
    }}
    QFrame#dropZone {{
        background: {SURFACE};
        border: 2px dashed {BORDER_STRONG};
        border-radius: 20px;
    }}
    QFrame#dropZoneActive {{
        background: {ACCENT_SOFT};
        border: 2px dashed {ACCENT};
        border-radius: 20px;
    }}
    QFrame#separator {{
        background: {BORDER};
        max-height: 1px;
        border: none;
    }}

    QLabel#h1 {{ font-size: 26px; font-weight: 700; }}
    QLabel#h2 {{ font-size: 18px; font-weight: 600; }}
    QLabel#h3 {{ font-size: 14px; font-weight: 600; }}
    QLabel#muted {{ color: {TEXT_MUTED}; font-size: 12px; }}
    QLabel#faint {{ color: {TEXT_FAINT}; font-size: 11px; }}
    QLabel#metric {{ font-size: 24px; font-weight: 700; }}
    QLabel#mono {{ font-family: "Cascadia Mono", Consolas, "JetBrains Mono", monospace; font-size: 11px;
                   color: {TEXT_MUTED}; }}

    QPushButton {{
        background: {SURFACE_STRONG};
        border: 1px solid {BORDER_STRONG};
        border-radius: 10px;
        padding: 8px 16px;
        font-size: 12px;
    }}
    QPushButton:hover {{ background: rgba(255,255,255,0.11); }}
    QPushButton:pressed {{ background: rgba(255,255,255,0.06); }}
    QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: transparent; }}
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_ALT});
        border: none;
        font-weight: 600;
        color: #ffffff;
        padding: 10px 20px;
    }}
    QPushButton#primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6c99ff, stop:1 #ad7cff);
    }}
    QPushButton#primary:disabled {{ background: {SURFACE_STRONG}; color: {TEXT_FAINT}; }}
    QPushButton#danger {{ border-color: rgba(255,92,114,0.45); color: {DANGER}; }}
    QPushButton#ghost {{ background: transparent; border: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
    QPushButton#chip {{
        border-radius: 12px;
        padding: 5px 12px;
        font-size: 11px;
        color: {TEXT_MUTED};
        background: transparent;
    }}
    QPushButton#chip:checked {{
        background: {ACCENT_SOFT};
        border-color: {ACCENT};
        color: {TEXT};
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {{
        background: rgba(0,0,0,0.28);
        border: 1px solid {BORDER};
        border-radius: 9px;
        padding: 7px 10px;
        font-size: 12px;
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {ACCENT};
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: #151a28;
        border: 1px solid {BORDER_STRONG};
        selection-background-color: {ACCENT_SOFT};
        outline: none;
    }}
    QCheckBox {{ font-size: 12px; spacing: 8px; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border-radius: 5px;
        border: 1px solid {BORDER_STRONG};
        background: rgba(0,0,0,0.3);
    }}
    QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    QProgressBar {{
        background: rgba(0,0,0,0.35);
        border: none;
        border-radius: 6px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        border-radius: 6px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_ALT});
    }}

    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
    QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.14); border-radius: 5px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.24); }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; border: none; height: 0; width: 0;
    }}

    QTableWidget {{
        background: transparent;
        border: none;
        gridline-color: {BORDER};
        font-size: 12px;
    }}
    QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {BORDER}; }}
    QTableWidget::item:selected {{ background: {ACCENT_SOFT}; }}
    QHeaderView::section {{
        background: transparent;
        border: none;
        border-bottom: 1px solid {BORDER_STRONG};
        color: {TEXT_FAINT};
        font-size: 11px;
        padding: 6px;
        text-align: left;
    }}
    QToolTip {{
        background: #171c2b;
        border: 1px solid {BORDER_STRONG};
        color: {TEXT};
        padding: 6px;
        border-radius: 6px;
    }}
    QStatusBar {{ color: {TEXT_MUTED}; border-top: 1px solid {BORDER}; }}
    QSplitter::handle {{ background: transparent; }}
    """
