"""``cs2clip`` — the command line face of the application.

Everything the GUI can do to a demo, the CLI can do too, which is what makes the
pipeline testable::

    cs2clip doctor
    cs2clip analyze match.dem
    cs2clip highlights match.dem --player "Player1" --json highlights.json
    cs2clip render highlights.json --demo match.dem --max 5
    cs2clip montage clip1.mp4 clip2.mp4 -o montage.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .core.config import Settings
from .core.detection import detect_all
from .core.errors import AppError, Cancelled
from .core.hardware import collect_hardware
from .core.logger import get_logger, setup_logging
from .core.models import Highlight, MatchAnalysis, sort_highlights
from .demo.cache import AnalysisCache
from .demo.downloader import download_demo
from .demo.extractor import extract_demo, is_archive
from .demo.parser import get_parser
from .demo.validation import validate_demo
from .highlights.detector import DetectorOptions, detect_highlights, update_player_stats
from .highlights.filters import filter_highlights, highlights_to_json, kills_to_csv
from .highlights.titles import pretty_map_name
from .render.pipeline import RenderPipeline, build_jobs, describe_environment
from .utils.timeutil import format_duration

log = get_logger("app")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.load()
    settings.ensure_dirs()
    setup_logging(settings.logs_dir, verbose=getattr(args, "verbose", False))
    if getattr(args, "no_cache", False):
        settings.ui.developer_mode = settings.ui.developer_mode
    return settings


def _prepare_demo(path_or_url: str, settings: Settings) -> Path:
    """Accept a local file, an archive or a URL and return a ready ``.dem``."""
    if path_or_url.lower().startswith(("http://", "https://")):
        print(f"Downloading {path_or_url}")

        def progress(update) -> None:  # noqa: ANN001 - DownloadProgress
            fraction = update.fraction
            if fraction is not None:
                print(f"\r  {fraction:.0%} ({update.downloaded / 1e6:.1f} MB)", end="", flush=True)

        target = download_demo(path_or_url, settings.paths.temp_dir, progress=progress)
        print()
    else:
        target = Path(path_or_url)
    if is_archive(target):
        print(f"Extracting {target.name}")
        target = extract_demo(target, settings.paths.temp_dir)
    return target


def _analyse(demo: Path, settings: Settings, use_cache: bool = True) -> MatchAnalysis:
    parser = get_parser()
    cache = AnalysisCache(settings.cache_dir)
    if use_cache:
        cached = cache.load(demo, parser.version())
        if cached is not None:
            print(f"Using cached analysis for {demo.name}")
            return cached

    last = [-1]

    def progress(fraction: float, message: str) -> None:
        percent = int(fraction * 100)
        if percent // 5 != last[0] // 5:
            last[0] = percent
            bar = "█" * (percent // 5) + "░" * (20 - percent // 5)
            print(f"\r  {bar} {percent:3d}%  {message:<32}", end="", flush=True)

    analysis = parser.parse(str(demo), progress=progress)
    print()
    cache.store(analysis, parser.version())
    return analysis


def _detector_options(settings: Settings, args: argparse.Namespace) -> DetectorOptions:
    clips = settings.clips
    if getattr(args, "window", None):
        clips.multikill_window_seconds = float(args.window)
    if getattr(args, "no_merge", False):
        clips.merge_overlapping = False
    return DetectorOptions(clips=clips, scoring=settings.scoring)


def _resolve_player(analysis: MatchAnalysis, name: str | None) -> str | None:
    if not name:
        return None
    player = analysis.player_by_name(name) or analysis.player(name)
    if player is None:
        candidates = [p for p in analysis.players if name.lower() in p.name.lower()]
        if len(candidates) == 1:
            player = candidates[0]
    if player is None:
        raise SystemExit(
            f"Player '{name}' is not in this demo. Available: " + ", ".join(p.name for p in analysis.players)
        )
    return player.steamid


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    report = detect_all(settings, apply=True)
    settings.save()

    print("Tools")
    for line in report.as_lines():
        print(f"  {line}")

    environment = describe_environment(settings)
    print("\nEncoders")
    encoders = environment["encoders"]
    print(f"  {', '.join(encoders) if encoders else 'none detected (is FFmpeg installed?)'}")

    print("\nRecorders")
    for name, usable, detail in environment["recorders"]:  # type: ignore[misc]
        print(f"  {'✓' if usable else '⚠'} {name:8} {detail}")

    print("\nCS2 playback backends")
    for name, usable, detail in environment["playback"]:  # type: ignore[misc]
        print(f"  {'✓' if usable else '⚠'} {name:8} {detail}")

    hardware = collect_hardware()
    print("\nHardware")
    print(f"  CPU  {hardware.cpu} ({hardware.cores} threads)")
    print(f"  GPU  {hardware.gpu}")
    print(f"  RAM  {hardware.ram_available_gb:.1f} / {hardware.ram_total_gb:.1f} GB free")
    print(f"\nSettings file: {Settings.path()}")
    print(f"Logs:          {settings.logs_dir}")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    demo = _prepare_demo(args.demo, settings)

    validation = validate_demo(demo)
    for line in validation.as_lines():
        print(f"  {line}")
    if not validation.ok:
        return 2

    analysis = _analyse(demo, settings, use_cache=not args.no_cache)
    highlights = detect_highlights(analysis, _detector_options(settings, args))
    update_player_stats(analysis, _detector_options(settings, args))

    print(f"\n{pretty_map_name(analysis.map_name).upper()}")
    print(f"{len(analysis.rounds)} rounds · {format_duration(analysis.duration_seconds)} · {analysis.tickrate:g} tick")
    print(f"Parser: {analysis.parser_name} {analysis.parser_version}")

    for team_label, team_value in (("Terrorists", 2), ("Counter-Terrorists", 3)):
        members = [p for p in analysis.players if int(p.team) == team_value]
        if not members:
            continue
        print(f"\n{team_label}")
        print(
            f"  {'Player':<22}{'slot':>5}{'K':>5}{'D':>5}{'K/D':>6}{'HS%':>6}{'ADR':>7}"
            f"{'2K':>4}{'3K':>4}{'4K':>4}{'ACE':>5}{'CL':>4}"
        )
        for player in members:
            stats = analysis.stats.get(player.steamid)
            if stats is None:
                continue
            adr = f"{stats.adr:.0f}" if stats.adr else "-"
            print(
                f"  {player.name[:21]:<22}{player.slot or '-':>5}{stats.kills:>5}{stats.deaths:>5}"
                f"{stats.kd:>6.2f}{stats.headshot_percentage:>5.0f}%{adr:>7}"
                f"{stats.multi_2k:>4}{stats.multi_3k:>4}{stats.multi_4k:>4}{stats.aces:>5}{stats.clutches:>4}"
            )

    print(f"\n{len(highlights)} highlights detected")
    if analysis.warnings:
        print("\nWarnings")
        for warning in analysis.warnings:
            print(f"  ⚠ {warning}")

    if args.json:
        payload = analysis.to_dict()
        payload["highlights"] = [h.to_dict() for h in highlights]
        Path(args.json).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nAnalysis written to {args.json}")
    return 0


def cmd_highlights(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    demo = _prepare_demo(args.demo, settings)
    analysis = _analyse(demo, settings, use_cache=not args.no_cache)
    options = _detector_options(settings, args)
    highlights = detect_highlights(analysis, options)

    steamid = _resolve_player(analysis, args.player)
    selected = filter_highlights(
        highlights,
        kinds=args.kind or (),
        min_score=args.min_score if args.min_score is not None else 0.0,
        query=args.search or "",
        sort_key=args.sort,
    )
    if steamid:
        selected = [h for h in selected if h.player_steamid == steamid]
    if args.max:
        selected = selected[: args.max]

    print(f"\n{'Type':<8}{'Round':>6}  {'Player':<20}{'Kills':>6}{'Score':>7}  {'Length':>7}  Title")
    for highlight in selected:
        length = highlight.duration_seconds(analysis.tickrate)
        print(
            f"{highlight.kind.value:<8}{highlight.round_number:>6}  {highlight.player_name[:19]:<20}"
            f"{highlight.kill_count:>6}{highlight.score:>7.1f}  {length:>6.1f}s  {highlight.title}"
        )
    print(f"\n{len(selected)} highlights")

    if args.json:
        highlights_to_json(selected, args.json)
        print(f"Highlights written to {args.json}")
    if args.csv:
        kills_to_csv(selected, args.csv)
        print(f"Kills written to {args.csv}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    if args.output:
        settings.paths.output_dir = args.output

    source = Path(args.highlights)
    if not source.is_file():
        raise SystemExit(f"{source} does not exist")
    payload = json.loads(source.read_text(encoding="utf-8"))
    raw_highlights = payload.get("highlights", payload) if isinstance(payload, dict) else payload
    highlights = [Highlight.from_dict(item) for item in raw_highlights]

    demo_path = args.demo or (payload.get("demo_path") if isinstance(payload, dict) else None)
    if not demo_path:
        raise SystemExit("Pass --demo: the highlights file does not say which demo it came from")
    demo = _prepare_demo(str(demo_path), settings)
    analysis = _analyse(demo, settings, use_cache=True)

    if args.player:
        steamid = _resolve_player(analysis, args.player)
        highlights = [h for h in highlights if h.player_steamid == steamid]
    if args.min_score is not None:
        highlights = [h for h in highlights if h.score >= args.min_score]
    highlights = sort_highlights(highlights, "score")
    if args.max:
        highlights = highlights[: args.max]
    if not highlights:
        print("Nothing to render.")
        return 0

    pipeline = RenderPipeline(settings, analysis)
    jobs = build_jobs(highlights, analysis, settings)

    print(f"Rendering {len(jobs)} clips to {settings.paths.output_dir}")
    for job in jobs:
        print(f"  {job.label}")

    def on_progress(job, fraction, message) -> None:  # noqa: ANN001
        print(
            f"\r  {job.highlight.kind.value:<7} R{job.highlight.round_number:<3} {fraction:5.0%} {message:<40}",
            end="",
            flush=True,
        )

    try:
        report = pipeline.run(jobs, on_progress=on_progress)
    except Cancelled:
        print("\nCancelled.")
        return 130
    print()

    for note in report.notes:
        print(f"  · {note}")
    for clip in report.clips:
        print(f"  ✓ {clip.video} ({clip.duration_seconds:.1f}s)")
    for job, reason in report.failed:
        print(f"  ✗ {job.label}: {reason}")
    return 0 if report.clips and not report.failed else (1 if report.failed else 0)


def cmd_montage(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    from .montage.creator import MontageCreator, MontageSettings

    montage_settings = MontageSettings(
        transition=args.transition,
        intro_path=args.intro or "",
        outro_path=args.outro or "",
        music_path=args.music or "",
        video=settings.video,
    )
    creator = MontageCreator()
    output = args.output or str(Path(settings.paths.output_dir) / "montage.mp4")

    def progress(fraction: float, message: str) -> None:
        print(f"\r  {fraction:5.0%} {message:<40}", end="", flush=True)

    result = creator.create(args.clips, output, montage_settings, on_progress=progress)
    print(f"\nMontage written to {result}")
    return 0


def cmd_cache(args: argparse.Namespace) -> int:
    settings = _load_settings(args)
    cache = AnalysisCache(settings.cache_dir)
    if args.clear:
        removed = cache.clear()
        print(f"Removed {removed} cached analyses.")
        return 0
    entries = cache.entries()
    total_mb = cache.size_bytes() / (1024 * 1024)
    print(f"{len(entries)} cached analyses ({total_mb:.1f} MB) in {settings.cache_dir}")
    for entry in entries[:20]:
        print(f"  {entry.map_name:<14} {os.path.basename(entry.demo_path)}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cs2clip",
        description="Local CS2 highlight clip generator (demo → highlights → CS2 playback → MP4).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging on the console")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check the installation and report what is available")
    doctor.set_defaults(func=cmd_doctor)

    analyze = subparsers.add_parser("analyze", help="parse a demo and print the match overview")
    analyze.add_argument("demo", help="path to a .dem/.dem.bz2/.dem.gz/.dem.zip file, or a URL")
    analyze.add_argument("--json", help="write the full analysis to this file")
    analyze.add_argument("--window", type=float, help="multi-kill window in seconds (default 7)")
    analyze.add_argument("--no-merge", action="store_true", help="do not merge overlapping clips")
    analyze.add_argument("--no-cache", action="store_true", help="ignore the analysis cache")
    analyze.set_defaults(func=cmd_analyze)

    highlights = subparsers.add_parser("highlights", help="list the highlights of a demo")
    highlights.add_argument("demo")
    highlights.add_argument("--player", help="player name or SteamID64")
    highlights.add_argument("--kind", action="append", help="filter by kind (ACE, 4K, 3K, 2K, CLUTCH, KILL)")
    highlights.add_argument("--search", help="free-text search")
    highlights.add_argument("--min-score", type=float, dest="min_score")
    highlights.add_argument("--max", type=int, help="keep only the N best")
    highlights.add_argument("--sort", default="score", choices=["score", "round", "time", "kills", "player"])
    highlights.add_argument("--json", help="write the highlights to this file")
    highlights.add_argument("--csv", help="write the kills to this CSV file")
    highlights.add_argument("--window", type=float)
    highlights.add_argument("--no-merge", action="store_true")
    highlights.add_argument("--no-cache", action="store_true")
    highlights.set_defaults(func=cmd_highlights)

    render = subparsers.add_parser("render", help="render clips from a highlights JSON file")
    render.add_argument("highlights", help="highlights.json produced by 'cs2clip highlights --json'")
    render.add_argument("--demo", help="demo to play back (required if the file does not name one)")
    render.add_argument("--player")
    render.add_argument("--max", type=int)
    render.add_argument("--min-score", type=float, dest="min_score")
    render.add_argument("--output", help="output folder")
    render.set_defaults(func=cmd_render)

    montage = subparsers.add_parser("montage", help="join finished clips into one video")
    montage.add_argument("clips", nargs="+")
    montage.add_argument("-o", "--output")
    montage.add_argument("--transition", default="none", choices=["none", "fade"])
    montage.add_argument("--intro")
    montage.add_argument("--outro")
    montage.add_argument("--music")
    montage.set_defaults(func=cmd_montage)

    cache = subparsers.add_parser("cache", help="inspect or clear the analysis cache")
    cache.add_argument("--clear", action="store_true")
    cache.set_defaults(func=cmd_cache)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except AppError as exc:
        print(f"\n{exc.as_text()}", file=sys.stderr)
        if exc.actions:
            print("\nWhat to try:", file=sys.stderr)
            for action in exc.actions:
                print(f"  • {action}", file=sys.stderr)
        log.error("%s (%s)", exc.title, exc.detail)
        return 1
    except Cancelled:
        print("Cancelled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
