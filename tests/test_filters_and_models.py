"""Filtering, searching, export, settings persistence and model round trips."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from cs2_clip_generator.core.config import Settings
from cs2_clip_generator.core.models import (
    Highlight,
    HighlightKind,
    HighlightTag,
    MatchAnalysis,
    Team,
    sort_highlights,
)
from cs2_clip_generator.highlights.detector import DetectorOptions, detect_highlights
from cs2_clip_generator.highlights.filters import (
    HighlightFilter,
    filter_highlights,
    highlights_to_csv_text,
    highlights_to_json,
    kills_to_csv,
)
from cs2_clip_generator.highlights.titles import pretty_map_name
from cs2_clip_generator.utils.timeutil import (
    format_duration,
    format_timestamp,
    parse_timestamp,
)

from .conftest import TICKRATE, kill, make_analysis

SEC = int(TICKRATE)


def _sample_highlights() -> list[Highlight]:
    kills = [
        kill(tick=20 * SEC, weapon="awp", headshot=True),
        kill(tick=22 * SEC, weapon="awp", victim="76561198000000007"),
        kill(tick=90 * SEC, weapon="knife", victim="76561198000000008", round_number=1),
        kill(tick=10_020, attacker="76561198000000002", victim="76561198000000009", round_number=2, weapon="ak47"),
    ]
    analysis = make_analysis(kills)
    return detect_highlights(analysis, DetectorOptions.defaults())


# ---------------------------------------------------------------------------
# Filtering and search
# ---------------------------------------------------------------------------


def test_filtering_by_kind_and_by_tag():
    highlights = _sample_highlights()
    assert all(h.kind == HighlightKind.MULTI_2K for h in filter_highlights(highlights, kinds=["2K"]))
    knives = filter_highlights(highlights, tags=[HighlightTag.KNIFE])
    assert knives and all(h.has_tag(HighlightTag.KNIFE) for h in knives)


def test_search_matches_kind_weapon_player_and_round():
    highlights = _sample_highlights()
    assert filter_highlights(highlights, query="awp")
    assert filter_highlights(highlights, query="knife")
    assert filter_highlights(highlights, query="round 2")
    assert filter_highlights(highlights, query="P1")
    assert not filter_highlights(highlights, query="nonexistent-token")


def test_search_terms_are_combined_with_and():
    highlights = _sample_highlights()
    assert filter_highlights(highlights, query="awp 2k")
    assert not filter_highlights(highlights, query="awp knife")


def test_min_score_and_player_filters():
    highlights = _sample_highlights()
    best = max(h.score for h in highlights)
    assert filter_highlights(highlights, min_score=best)
    filtered = HighlightFilter(player_steamid="76561198000000002").apply(highlights)
    assert {h.player_steamid for h in filtered} == {"76561198000000002"}


def test_sorting_directions_are_natural_per_key():
    highlights = _sample_highlights()
    by_score = sort_highlights(highlights, "score")
    assert [h.score for h in by_score] == sorted((h.score for h in highlights), reverse=True)
    by_round = sort_highlights(highlights, "round")
    assert [h.round_number for h in by_round] == sorted(h.round_number for h in highlights)
    by_time = sort_highlights(highlights, "time")
    assert [h.start_tick for h in by_time] == sorted(h.start_tick for h in highlights)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_json_export_round_trips_into_highlight_objects(tmp_path):
    highlights = _sample_highlights()
    path = highlights_to_json(highlights, tmp_path / "highlights.json")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    restored = [Highlight.from_dict(item) for item in payload]
    assert [h.id for h in restored] == [h.id for h in highlights]
    assert restored[0].kills[0].weapon == highlights[0].kills[0].weapon
    assert isinstance(restored[0].kind, HighlightKind)


def test_kills_csv_has_one_row_per_kill_and_opens_in_excel(tmp_path):
    highlights = _sample_highlights()
    path = kills_to_csv(highlights, tmp_path / "kills.csv")
    raw = Path(path).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM so Excel shows Cyrillic
    rows = list(csv.DictReader(Path(path).read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == sum(h.kill_count for h in highlights)
    assert {"round", "tick", "attacker", "weapon", "headshot"} <= set(rows[0])


def test_highlight_csv_text_has_a_header():
    text = highlights_to_csv_text(_sample_highlights())
    assert text.splitlines()[0].startswith("type,round,player")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_analysis_round_trips_through_json():
    analysis = make_analysis([kill(tick=100)])
    payload = json.loads(json.dumps(analysis.to_dict()))
    restored = MatchAnalysis.from_dict(payload)
    assert restored.map_name == analysis.map_name
    assert restored.tickrate == analysis.tickrate
    assert restored.players[0].slot == analysis.players[0].slot
    assert isinstance(restored.players[0].team, Team)
    assert restored.kills[0].tick == 100
    assert restored.stats.keys() == analysis.stats.keys()


def test_account_id_conversion_for_steam_commands():
    from cs2_clip_generator.core.models import Player

    player = Player(steamid="76561197960287930", name="x")
    assert player.account_id == 22202


def test_team_parsing_accepts_numbers_and_names():
    assert Team.parse(2) == Team.T
    assert Team.parse("CT") == Team.CT
    assert Team.parse("TERRORIST") == Team.T
    assert Team.parse(None) == Team.UNKNOWN


def test_player_stats_derived_values():
    from cs2_clip_generator.core.models import PlayerStats

    stats = PlayerStats(steamid="1", name="x", kills=10, deaths=5, headshots=5, damage=2000, rounds_played=20)
    assert stats.kd == 2.0
    assert stats.headshot_percentage == 50.0
    assert stats.adr == 100.0
    assert PlayerStats(steamid="1", name="x", kills=3, deaths=0).kd == 3.0
    assert PlayerStats(steamid="1", name="x").adr is None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_settings_round_trip_and_tolerate_unknown_or_missing_keys(tmp_path):
    settings = Settings()
    settings.clips.multikill_window_seconds = 9.5
    settings.video.fps = 120
    settings.scoring.base["ACE"] = 250.0
    path = settings.save(tmp_path / "settings.json")

    payload = json.loads(path.read_text())
    payload["unknown_section"] = {"x": 1}
    payload["clips"]["unknown_key"] = 5
    del payload["video"]["fps"]
    path.write_text(json.dumps(payload))

    loaded = Settings.load(path)
    assert loaded.clips.multikill_window_seconds == 9.5
    assert loaded.scoring.base["ACE"] == 250.0
    assert loaded.video.fps == 60  # default restored for the removed key


def test_a_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json at all")
    assert Settings.load(path).video.fps == 60


def test_quality_presets_set_concrete_values():
    settings = Settings()
    settings.apply_quality_preset("quality")
    assert (settings.video.width, settings.video.height) == (2560, 1440)
    assert settings.video.codec == "h265"
    settings.apply_quality_preset("fast")
    assert settings.video.bitrate_kbps == 12000
    settings.apply_quality_preset("nonsense")
    assert settings.ui.quality_preset == "custom"


def test_hlae_path_is_mirrored_into_the_recording_section(tmp_path):
    settings = Settings()
    settings.paths.hlae_executable = "C:/HLAE/HLAE.exe"
    settings.save(tmp_path / "s.json")
    assert settings.recording.hlae_executable == "C:/HLAE/HLAE.exe"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def test_duration_and_timestamp_formatting():
    assert format_duration(2322) == "38:42"
    assert format_duration(3723) == "1:02:03"
    assert format_timestamp(751.5) == "12:31.500"
    assert parse_timestamp("12:31.5") == 751.5
    assert parse_timestamp("93") == 93.0
    assert parse_timestamp("nonsense") is None


def test_map_names_are_prettified_without_a_hardcoded_whitelist():
    assert pretty_map_name("de_mirage") == "Mirage"
    assert pretty_map_name("de_ancient") == "Ancient"
    # An unknown workshop map must still produce something readable.
    assert pretty_map_name("de_brand_new_map") == "Brand New Map"
    assert pretty_map_name("") == "Unknown map"
