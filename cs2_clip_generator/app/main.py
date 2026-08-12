"""Application entry point.

Order of business: load settings, start logging, detect the tool chain on first
run, then show the window. Anything that fails here fails *visibly* — a message
box, not a stack trace in a console the user never sees.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)

    from ..core.config import Settings
    from ..core.detection import detect_all, first_run_needed
    from ..core.logger import get_logger, setup_logging

    settings = Settings.load()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir, verbose="--verbose" in argv)
    log = get_logger("app")
    log.info("starting CS2 Clip Generator")

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:  # pragma: no cover - packaging guard
        print("PySide6 is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        log.error("PySide6 import failed: %s", exc)
        return 2

    from .. import ui
    from ..ui.main_window import MainWindow, app_icon
    from ..ui.theme import stylesheet

    del ui
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName("CS2 Clip Generator")
    app.setOrganizationName("CS2ClipGenerator")
    app.setStyleSheet(stylesheet())
    app.setWindowIcon(app_icon())

    if first_run_needed(settings):
        log.info("first run: detecting Steam, CS2, FFmpeg, OBS and HLAE")
        detect_all(settings, apply=True)
        settings.save()

    window = MainWindow(settings)
    window.show()
    window.check_interrupted_render()

    # A demo can be passed on the command line or dropped on the .exe.
    for argument in argv[1:]:
        candidate = Path(argument)
        if candidate.is_file() and candidate.suffix.lower() in (".dem", ".bz2", ".gz", ".zip"):
            window.show_page("demo")
            window.demo_page.load_demo(str(candidate))
            break

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
