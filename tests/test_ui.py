"""UI smoke tests.

They run head-less (``QT_QPA_PLATFORM=offscreen``) and assert the things that
actually break in a desktop app: a page that cannot be built, a signal wired to a
method that no longer exists, a filter that shows nothing. Skipped entirely when
PySide6 is not installed, so the backend test suite stays dependency-light.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cs2_clip_generator.core.config import Settings  # noqa: E402
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights  # noqa: E402

from .conftest import TICKRATE, kill, make_analysis  # noqa: E402

SEC = int(TICKRATE)


@pytest.fixture(scope="module")
def qt_app():  # noqa: ANN201
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qt_app, tmp_path):  # noqa: ANN001, ANN201
    from cs2_clip_generator.ui.main_window import MainWindow
    from cs2_clip_generator.ui.theme import stylesheet

    settings = Settings()
    settings.paths.output_dir = str(tmp_path / "clips")
    settings.paths.temp_dir = str(tmp_path / "temp")
    qt_app.setStyleSheet(stylesheet())
    window = MainWindow(settings)
    window.resize(1400, 900)
    yield window
    window.close()


@pytest.fixture
def loaded(window):  # noqa: ANN001, ANN201
    kills = [
        kill(tick=20 * SEC, weapon="awp", headshot=True, noscope=True),
        kill(tick=22 * SEC, weapon="awp", victim="76561198000000007"),
        kill(tick=90 * SEC, weapon="knife", victim="76561198000000008"),
    ]
    analysis = make_analysis(kills, rounds=2)
    highlights = detect_highlights(analysis, DetectorOptions.defaults())
    window.state.set_analysis(analysis, highlights)
    return window, analysis, highlights


def test_all_pages_build_and_can_be_shown(window, qt_app):
    for key in ("dashboard", "demo", "highlights", "render", "montage", "settings"):
        window.show_page(key)
        qt_app.processEvents()
        assert window.stack.currentWidget() is window.pages[key]


def test_pages_render_to_a_pixmap(window, qt_app):
    """A page that throws during painting would fail here."""
    for key in ("dashboard", "demo", "highlights", "render", "montage", "settings"):
        window.show_page(key)
        qt_app.processEvents()
        pixmap = window.grab()
        assert not pixmap.isNull()
        assert pixmap.width() > 800


def test_loading_an_analysis_fills_the_pages(loaded, qt_app):
    window, analysis, highlights = loaded
    qt_app.processEvents()
    assert window.highlights_page.player_combo.count() == len(analysis.players) + 1  # + "All players"
    assert len(window.highlights_page._cards) == len(highlights)
    assert "MIRAGE" in window.demo_page.map_label.text()
    assert window.dashboard_page.metric_highlights.value_label.text() == str(len(highlights))


def test_filters_and_search_change_what_is_shown(loaded, qt_app):
    window, _analysis, highlights = loaded
    page = window.highlights_page

    page.search_input.setText("knife")
    qt_app.processEvents()
    shown = page.visible_highlights()
    assert shown and all("knife" in " ".join(h.weapons) for h in shown)

    page.search_input.setText("")
    page.chip_buttons["2K"].setChecked(True)
    qt_app.processEvents()
    assert all(h.kind.value == "2K" for h in page.visible_highlights())

    page.chip_buttons["2K"].setChecked(False)
    qt_app.processEvents()
    assert len(page.visible_highlights()) == len(highlights)


def test_player_selection_filters_the_cards(loaded, qt_app):
    window, analysis, _highlights = loaded
    page = window.highlights_page
    steamid = analysis.players[0].steamid
    index = page.player_combo.findData(steamid)
    page.player_combo.setCurrentIndex(index)
    qt_app.processEvents()
    assert {h.player_steamid for h in page.visible_highlights()} == {steamid}


def test_generate_queues_a_job_on_the_render_page(loaded, qt_app):
    window, _analysis, highlights = loaded
    window.state.navigate.emit(f"render:queue:{highlights[0].id}")
    qt_app.processEvents()
    assert window.stack.currentWidget() is window.render_page
    assert len(window.render_page.jobs) == 1
    assert window.render_page.jobs[0].highlight.id == highlights[0].id


def test_auto_clip_queues_the_best_highlights(loaded, qt_app):
    window, _analysis, _highlights = loaded
    page = window.highlights_page
    page.max_clips.setValue(2)
    page.min_score.setValue(0)
    page._auto_clip()
    qt_app.processEvents()
    assert len(window.render_page.jobs) == 2
    scores = [job.highlight.score for job in window.render_page.jobs]
    assert scores == sorted(scores, reverse=True)


def test_clicking_the_timeline_reports_the_highlight(loaded, qt_app):
    window, _analysis, highlights = loaded
    page = window.highlights_page
    page._on_timeline_click(highlights[0].id)
    qt_app.processEvents()
    assert f"Round {highlights[0].round_number}" in page.timeline_detail.text()
    assert highlights[0].player_name in page.timeline_detail.text()


def test_manual_clip_dialog_produces_a_highlight(loaded, qt_app):
    window, analysis, _highlights = loaded
    from cs2_clip_generator.ui.pages.highlights_page import ManualClipDialog

    dialog = ManualClipDialog(analysis)
    dialog.start_tick.setValue(1000)
    dialog.end_tick.setValue(1000 + 10 * SEC)
    dialog._create()
    assert dialog.highlight is not None
    assert dialog.highlight.end_tick - dialog.highlight.start_tick == 10 * SEC
    assert dialog.highlight.player_steamid in {p.steamid for p in analysis.players}


def test_clip_range_dialog_round_trips_timestamps(loaded, qt_app):
    window, analysis, highlights = loaded
    from cs2_clip_generator.ui.pages.highlights_page import ClipRangeDialog

    dialog = ClipRangeDialog(highlights[0], analysis.tickrate)
    dialog.start_input.setText("1:00.000")
    dialog.end_input.setText("1:12.500")
    start, end = dialog.tick_range()
    assert start == int(60 * analysis.tickrate)
    assert end == int(72.5 * analysis.tickrate)


def test_settings_changes_are_saved_and_trigger_redetection(window, qt_app, loaded):
    page = window.settings_page
    page.window_spin.setValue(3.0)
    qt_app.processEvents()
    assert window.state.settings.clips.multikill_window_seconds == 3.0
    assert Settings.path().is_file()


def test_resolution_combo_reflects_settings_and_never_forces_720p(window, qt_app):
    """Regression: item data used to be a (w, h) tuple, which QComboBox.findData
    cannot match, so the combo silently stuck on the first entry (1280x720) and
    every save rewrote the resolution to 720p."""
    page = window.settings_page
    # Default video settings are 1080p; the combo must show that, not fall back
    # to the first item.
    assert page.resolution_combo.currentData() == "1920x1080"
    page._save()
    assert (window.state.settings.video.width, window.state.settings.video.height) == (1920, 1080)

    # Choosing another resolution must round-trip instead of collapsing to 720p.
    page._select(page.resolution_combo, "2560x1440")
    qt_app.processEvents()
    page._save()
    assert (window.state.settings.video.width, window.state.settings.video.height) == (2560, 1440)


def test_error_dialog_shows_reasons_without_a_traceback(qt_app):
    from cs2_clip_generator.core.errors import recording_failed
    from cs2_clip_generator.ui.widgets.error_dialog import ErrorDialog

    error = recording_failed("Traceback (most recent call last): ...")
    dialog = ErrorDialog(error)
    texts = [child.text() for child in dialog.findChildren(type(dialog.children()[1])) if hasattr(child, "text")]
    joined = " ".join(texts)
    assert "Unable to start CS2 recording." in joined
    assert "Traceback" not in joined
    assert "CS2 is not installed" in joined


def test_developer_mode_panel_appears_when_enabled(window, qt_app):
    window.state.settings.ui.developer_mode = True
    window.show_page("render")
    window.render_page._refresh_rows()
    qt_app.processEvents()
    assert window.render_page.developer_card.isVisibleTo(window.render_page)
    assert "CS2 process running" in window.render_page.developer_text.text()
    assert "POV slot" not in window.render_page.developer_text.text()  # nothing running yet
