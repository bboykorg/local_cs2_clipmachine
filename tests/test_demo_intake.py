"""Demo intake: URL validation, downloading, archives, cache, validation."""

from __future__ import annotations

import bz2
import gzip
import http.server
import threading
import zipfile

import pytest

from cs2_clip_generator.core.errors import Cancelled, DemoError, DownloadError
from cs2_clip_generator.demo.cache import AnalysisCache
from cs2_clip_generator.demo.downloader import (
    download_demo,
    filename_from_response,
    validate_url,
)
from cs2_clip_generator.demo.extractor import extract_demo, is_archive, list_demos_in_zip
from cs2_clip_generator.demo.validation import detect_game, validate_demo
from cs2_clip_generator.utils.filesystem import sanitize_filename, unique_path

from .conftest import make_analysis

CS2_HEADER = b"PBDEMS2\x00" + b"\x00" * 8
CSGO_HEADER = b"HL2DEMO\x00" + b"\x00" * 8


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def test_only_http_urls_are_accepted():
    assert validate_url("  https://example.com/m.dem  ") == "https://example.com/m.dem"
    for bad in ("", "ftp://example.com/m.dem", "file:///etc/passwd", "javascript:alert(1)", "https://"):
        with pytest.raises(DownloadError):
            validate_url(bad)


def test_filename_comes_from_content_disposition_then_from_the_url():
    class Headers(dict):
        def get(self, key, default=None):  # noqa: ANN001
            return dict.get(self, key, default)

    assert (
        filename_from_response("https://x/y", Headers({"Content-Disposition": 'attachment; filename="match 1.dem"'}))
        == "match_1.dem"
    )
    assert filename_from_response("https://x/demos/match2.dem", Headers()) == "match2.dem"
    # Directory traversal in the header cannot escape the target folder.
    name = filename_from_response("https://x/y", Headers({"Content-Disposition": 'filename="../../evil.dem"'}))
    assert "/" not in name and "\\" not in name and ".." not in name.strip(".")


# ---------------------------------------------------------------------------
# Downloading (against a local HTTP server)
# ---------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = CS2_HEADER + b"x" * 5000
    content_type = "application/octet-stream"

    def do_GET(self):  # noqa: N802
        if self.path == "/missing":
            self.send_error(404, "Not Found")
            return
        if self.path == "/page":
            body = b"<!DOCTYPE html><html><body>Download here</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/match.dem")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # noqa: ANN002 - silence the test output
        pass


@pytest.fixture
def http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_download_reports_progress_and_writes_the_file(http_server, tmp_path):
    updates = []
    path = download_demo(f"{http_server}/match.dem", tmp_path, progress=updates.append)
    assert path.is_file()
    assert path.read_bytes().startswith(b"PBDEMS2")
    assert updates and updates[-1].fraction == 1.0
    assert updates[-1].speed_bps > 0
    assert not list(tmp_path.glob("*.part"))  # the temp file is renamed, not left behind


def test_download_follows_redirects(http_server, tmp_path):
    assert download_demo(f"{http_server}/redirect", tmp_path).is_file()


def test_http_errors_become_actionable_messages(http_server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        download_demo(f"{http_server}/missing", tmp_path)
    assert "404" in exc.value.title
    assert exc.value.actions


def test_an_html_page_is_rejected_instead_of_saved_as_a_demo(http_server, tmp_path):
    with pytest.raises(DownloadError) as exc:
        download_demo(f"{http_server}/page", tmp_path)
    assert "web page" in exc.value.title.lower()
    assert not list(tmp_path.glob("*.dem"))


def test_a_dead_host_does_not_hang_forever(tmp_path):
    with pytest.raises(DownloadError):
        download_demo("http://127.0.0.1:1/m.dem", tmp_path, timeout=2.0)


def test_cancelling_a_download_removes_the_partial_file(http_server, tmp_path):
    target = tmp_path / "downloads"
    with pytest.raises(Cancelled):
        download_demo(f"{http_server}/match.dem", target, cancel=lambda: True)
    assert not list(target.iterdir())


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def test_archive_detection():
    assert is_archive("m.dem.bz2") and is_archive("m.dem.gz") and is_archive("m.dem.zip")
    assert not is_archive("m.dem")


def test_bz2_and_gzip_demos_are_unpacked(tmp_path):
    payload = CS2_HEADER + b"y" * 2048
    bz2_path = tmp_path / "match.dem.bz2"
    bz2_path.write_bytes(bz2.compress(payload))
    gz_path = tmp_path / "match2.dem.gz"
    gz_path.write_bytes(gzip.compress(payload))

    for archive in (bz2_path, gz_path):
        extracted = extract_demo(archive, tmp_path / "out")
        assert extracted.suffix == ".dem"
        assert extracted.read_bytes() == payload


def test_zip_with_several_demos_is_listed_and_a_member_can_be_chosen(tmp_path):
    archive = tmp_path / "matches.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("first.dem", CS2_HEADER + b"1")
        zf.writestr("second.dem", CS2_HEADER + b"2")
        zf.writestr("readme.txt", "ignore me")

    entries = list_demos_in_zip(archive)
    assert {entry.name for entry in entries} == {"first.dem", "second.dem"}

    extracted = extract_demo(archive, tmp_path / "out", member="second.dem")
    assert extracted.read_bytes().endswith(b"2")


def test_a_zip_without_demos_is_a_clear_error(tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("notes.txt", "nothing here")
    with pytest.raises(DemoError):
        extract_demo(archive, tmp_path / "out")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_game_detection_by_magic_bytes(tmp_path):
    cs2 = tmp_path / "cs2.dem"
    cs2.write_bytes(CS2_HEADER)
    csgo = tmp_path / "csgo.dem"
    csgo.write_bytes(CSGO_HEADER)
    junk = tmp_path / "junk.dem"
    junk.write_bytes(b"not a demo at all")

    assert detect_game(cs2) == "cs2"
    assert detect_game(csgo) == "csgo"
    assert detect_game(junk) == "unknown"


def test_a_csgo_demo_fails_validation_with_an_explanation(tmp_path):
    csgo = tmp_path / "csgo.dem"
    csgo.write_bytes(CSGO_HEADER + b"x" * 4096)
    result = validate_demo(csgo)
    assert not result.ok
    assert result.game == "csgo"
    assert any("CS:GO" in detail for _label, _ok, detail in result.checks)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_round_trip_and_invalidation_by_parser_version(tmp_path):
    demo = tmp_path / "match.dem"
    demo.write_bytes(CS2_HEADER + b"z" * 100_000)
    analysis = make_analysis()
    analysis.demo_path = str(demo)

    cache = AnalysisCache(tmp_path / "cache")
    assert cache.load(demo, "1.0") is None
    cache.store(analysis, "1.0")

    loaded = cache.load(demo, "1.0")
    assert loaded is not None
    assert loaded.map_name == analysis.map_name
    assert len(loaded.players) == len(analysis.players)
    assert loaded.players[0].slot == analysis.players[0].slot

    assert cache.load(demo, "2.0") is None  # a new parser invalidates the entry
    assert cache.entries()
    assert cache.clear() >= 1
    assert cache.load(demo, "1.0") is None


def test_cache_key_changes_when_the_demo_content_changes(tmp_path):
    first = tmp_path / "a.dem"
    first.write_bytes(CS2_HEADER + b"a" * 1000)
    second = tmp_path / "b.dem"
    second.write_bytes(CS2_HEADER + b"b" * 1000)
    assert AnalysisCache.key_for(first) != AnalysisCache.key_for(second)


# ---------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------


def test_filenames_survive_cyrillic_emoji_and_windows_reserved_names():
    assert sanitize_filename("Подсосник blick'a") == "Подсосник_blick'a"
    assert sanitize_filename('bad/\\:*?"<>|name') == "bad_name"
    assert sanitize_filename("   ") == "clip"
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("a" * 300, max_length=50) == "a" * 50


def test_clip_filenames_are_descriptive_and_sortable():
    from cs2_clip_generator.core.models import Highlight, HighlightKind
    from cs2_clip_generator.highlights.titles import clip_filename

    highlight = Highlight(
        id="x", kind=HighlightKind.ACE, player_steamid="1", player_name="Player1", round_number=17
    )
    assert clip_filename(highlight) == "ACE_Round17_Player1.mp4"

    clutch = Highlight(
        id="y", kind=HighlightKind.CLUTCH, player_steamid="1", player_name="Player 1", round_number=3, clutch_vs=3
    )
    assert clip_filename(clutch) == "1v3_Round03_Player_1.mp4"


def test_unique_path_never_overwrites(tmp_path):
    first = tmp_path / "clip.mp4"
    first.write_bytes(b"1")
    second = unique_path(first)
    assert second.name == "clip (2).mp4"
