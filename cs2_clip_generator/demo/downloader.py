"""Download a demo from a URL — streaming, cancellable, honest about progress.

Security note: the downloader treats the response as *data only*. Nothing is
executed, the file name is sanitised, the target directory is fixed by the
caller, and only ``http``/``https`` are accepted.
"""

from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import Cancelled, DownloadError
from ..core.logger import get_logger
from ..utils.filesystem import free_space_mb, sanitize_filename, unique_path

log = get_logger("app")

USER_AGENT = "CS2ClipGenerator/1.0 (+local demo downloader)"
CHUNK_SIZE = 1 << 18  # 256 KiB
ALLOWED_SCHEMES = ("http", "https")
DEMO_SUFFIXES = (".dem", ".dem.bz2", ".dem.gz", ".dem.zip", ".bz2", ".gz", ".zip")


@dataclass
class DownloadProgress:
    downloaded: int
    total: int | None
    speed_bps: float
    eta_seconds: float | None

    @property
    def fraction(self) -> float | None:
        if not self.total:
            return None
        return min(1.0, self.downloaded / self.total)


ProgressCallback = Callable[[DownloadProgress], None]
CancelCallback = Callable[[], bool]


def validate_url(url: str) -> str:
    """Normalise and sanity-check a user-supplied URL."""
    url = (url or "").strip().strip('"').strip("'")
    if not url:
        raise DownloadError(title="Enter a demo URL first.", actions=["Paste a link to a .dem file"])
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise DownloadError(
            title="That link cannot be downloaded.",
            reasons=[f"Only http and https links are supported (got '{parsed.scheme or 'no scheme'}')"],
            actions=["Paste a direct link to a .dem file"],
        )
    if not parsed.netloc:
        raise DownloadError(
            title="That URL is not valid.",
            reasons=["The link has no host name"],
            actions=["Check the link and try again"],
        )
    return url


def filename_from_response(url: str, headers) -> str:  # noqa: ANN001 - http.client.HTTPMessage
    """Prefer Content-Disposition, fall back to the URL path."""
    disposition = headers.get("Content-Disposition") if headers else None
    if disposition:
        match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition)
        if match:
            candidate = urllib.parse.unquote(match.group(1)).strip()
            if candidate:
                return sanitize_filename(candidate, fallback="match.dem")
    path = urllib.parse.urlparse(url).path
    name = os.path.basename(urllib.parse.unquote(path)) or "match.dem"
    if not name.lower().endswith(DEMO_SUFFIXES):
        name = f"{name}.dem" if "." not in name else name
    return sanitize_filename(name, fallback="match.dem")


def _looks_like_html(head: bytes, content_type: str) -> bool:
    if "text/html" in content_type.lower():
        return True
    sniff = head[:512].lstrip().lower()
    return sniff.startswith((b"<!doctype html", b"<html", b"<?xml"))


def download_demo(
    url: str,
    target_dir: str | os.PathLike[str],
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download ``url`` into ``target_dir`` and return the local path.

    Raises :class:`Cancelled` if the caller asks to stop, and
    :class:`DownloadError` with actionable text for every network failure.
    """
    url = validate_url(url)
    directory = Path(target_dir)
    directory.mkdir(parents=True, exist_ok=True)

    # S310 on both calls: validate_url() above rejects anything but http/https,
    # so file:// and custom schemes can never reach urllib.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})  # noqa: S310
    try:
        # urllib follows redirects for http(s) automatically.
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
    except urllib.error.HTTPError as exc:
        raise DownloadError(
            title=f"The server refused the download (HTTP {exc.code}).",
            reasons=[
                "The link has expired" if exc.code in (403, 410) else "The file no longer exists"
                if exc.code == 404
                else f"The server replied with {exc.code} {exc.reason}",
            ],
            actions=["Get a fresh download link", "Download the demo in your browser and use Select Demo"],
            detail=str(exc),
        ) from exc
    except urllib.error.URLError as exc:
        raise DownloadError(
            title="The demo could not be downloaded.",
            reasons=["No internet connection", "The host could not be reached", "The connection timed out"],
            actions=["Check your connection", "Try again"],
            detail=str(exc),
        ) from exc
    except (TimeoutError, OSError) as exc:
        raise DownloadError(
            title="The connection to the server failed.",
            reasons=["The connection timed out"],
            actions=["Try again"],
            detail=str(exc),
        ) from exc

    with response:
        headers = response.headers
        content_type = headers.get("Content-Type", "") or ""
        total: int | None = None
        try:
            total = int(headers.get("Content-Length") or 0) or None
        except ValueError:
            total = None

        if total:
            needed_mb = total / (1024 * 1024) * 1.1
            free_mb = free_space_mb(directory)
            if free_mb < needed_mb:
                raise DownloadError(
                    title="Not enough disk space for this demo.",
                    reasons=[f"The demo needs about {needed_mb:.0f} MB, {free_mb:.0f} MB are free"],
                    actions=["Free up space", "Change the temporary folder in Settings"],
                )

        name = filename_from_response(url, headers)
        target = unique_path(directory / name)
        partial = target.with_name(target.name + ".part")

        downloaded = 0
        started = time.monotonic()
        last_report = 0.0
        first_chunk = True
        try:
            with open(partial, "wb") as handle:
                while True:
                    if cancel and cancel():
                        raise Cancelled()
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if first_chunk:
                        if _looks_like_html(chunk, content_type):
                            raise DownloadError(
                                title="That link returned a web page, not a demo.",
                                reasons=[
                                    "The URL points at a download *page* rather than the file itself",
                                    "The site requires a login or a captcha",
                                ],
                                actions=[
                                    "Copy the direct link to the .dem file",
                                    "Download it in your browser and use Select Demo",
                                ],
                            )
                        first_chunk = False
                    handle.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    if progress and (now - last_report > 0.15 or downloaded == total):
                        elapsed = max(1e-6, now - started)
                        speed = downloaded / elapsed
                        eta = ((total - downloaded) / speed) if (total and speed > 0) else None
                        progress(DownloadProgress(downloaded, total, speed, eta))
                        last_report = now
        except Cancelled:
            partial.unlink(missing_ok=True)
            log.info("download cancelled: %s", url)
            raise
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise DownloadError(
                title="The demo could not be saved.",
                reasons=["The disk is full", "The temporary folder is not writable"],
                actions=["Free up space", "Change the temporary folder in Settings"],
                detail=str(exc),
            ) from exc

    if downloaded == 0:
        partial.unlink(missing_ok=True)
        raise DownloadError(
            title="The server sent an empty file.",
            reasons=["The link is broken or the demo was removed"],
            actions=["Get a fresh link"],
        )

    os.replace(partial, target)
    log.info("downloaded %s (%d bytes) -> %s", url, downloaded, target)
    return target
