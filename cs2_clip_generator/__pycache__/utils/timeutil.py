"""Tick / seconds / clock-time conversions and human-readable formatting."""

from __future__ import annotations


def ticks_to_seconds(ticks: int, tickrate: float) -> float:
    return ticks / tickrate if tickrate else 0.0


def seconds_to_ticks(seconds: float, tickrate: float) -> int:
    return int(round(seconds * tickrate))


def format_duration(seconds: float) -> str:
    """``38:42`` for a match, ``1:02:03`` when it runs past an hour."""
    seconds = max(0.0, float(seconds))
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_timestamp(seconds: float) -> str:
    """``12:31.500`` — the format used by the manual clip editor."""
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    return f"{minutes}:{rest:06.3f}"


def parse_timestamp(text: str) -> float | None:
    """Inverse of :func:`format_timestamp`; accepts ``93``, ``1:33``, ``1:33.5``."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    total = 0.0
    for value in values:
        total = total * 60 + value
    return total


def format_bytes(num_bytes: float) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def format_speed(bytes_per_second: float) -> str:
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds != seconds or seconds == float("inf"):
        return "--:--"
    return format_duration(seconds)
