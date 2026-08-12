"""Safe subprocess helpers.

Every external command is built as an argument *list* and run without a shell,
so nothing coming from a demo file, a URL or a player name can ever be
interpreted as a shell command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ..core.errors import Cancelled
from ..core.logger import get_logger

log = get_logger("app")

IS_WINDOWS = os.name == "nt"


def no_window_kwargs() -> dict:
    """Keep console windows from flashing up on Windows."""
    if not IS_WINDOWS:
        return {}
    startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}  # type: ignore[attr-defined]


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    args: Sequence[str],
    timeout: float | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    log_name: str = "app",
) -> CommandResult:
    """Run a command to completion, capturing output. Never uses a shell."""
    logger = get_logger(log_name)
    logger.debug("run: %s", " ".join(map(str, args)))
    try:
        completed = subprocess.run(  # noqa: S603 - args is a list, shell=False
            [str(a) for a in args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=False,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        logger.debug("run failed, executable missing: %s", exc)
        return CommandResult(127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        logger.debug("run timed out after %ss", timeout)
        return CommandResult(124, exc.stdout or "", exc.stderr or "timeout")
    if completed.returncode != 0:
        logger.debug("exit=%s stderr=%s", completed.returncode, (completed.stderr or "")[-2000:])
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def stream(
    args: Sequence[str],
    on_line: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    log_name: str = "app",
    cwd: str | None = None,
) -> CommandResult:
    """Run a command, forwarding stderr lines as they appear.

    FFmpeg reports progress on stderr, which is why this exists. Cancellation
    terminates the child process and raises :class:`Cancelled`.
    """
    logger = get_logger(log_name)
    logger.debug("stream: %s", " ".join(map(str, args)))
    try:
        proc = subprocess.Popen(  # noqa: S603 - args is a list, shell=False
            [str(a) for a in args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=cwd,
            **no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        return CommandResult(127, "", str(exc))

    collected: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            collected.append(line)
            if len(collected) > 4000:
                del collected[:2000]
            logger.debug("%s", line)
            if on_line:
                on_line(line)
            if should_cancel and should_cancel():
                terminate(proc)
                raise Cancelled()
    finally:
        proc.stdout.close()
    returncode = proc.wait()
    output = "\n".join(collected)
    return CommandResult(returncode, output, output)


def terminate(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """Ask nicely, then insist."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        proc.kill()
    except OSError:
        pass


def which(name: str, extra_paths: Iterable[str] = ()) -> str | None:
    """`shutil.which` plus a list of explicit candidate paths."""
    for candidate in extra_paths:
        if candidate and os.path.isfile(candidate):
            return candidate
    found = shutil.which(name)
    return found


def process_names_running(names: Iterable[str]) -> list[str]:
    """Return which of the given process names are currently running."""
    wanted = {n.lower() for n in names}
    running: list[str] = []
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name"]):
            name = (proc.info.get("name") or "").lower()
            if name in wanted and name not in running:
                running.append(name)
        return running
    except Exception:  # pragma: no cover - psutil optional
        pass
    if IS_WINDOWS:  # pragma: no cover - Windows only
        result = run(["tasklist", "/fo", "csv", "/nh"], timeout=15)
        text = result.stdout.lower()
        return [name for name in wanted if name in text]
    result = run(["ps", "-eo", "comm"], timeout=15)
    text = result.stdout.lower()
    return [name for name in wanted if name in text]


def kill_processes(names: Iterable[str]) -> bool:
    """Terminate processes by executable name. Returns True if any was killed."""
    killed = False
    try:
        import psutil  # type: ignore

        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() in {n.lower() for n in names}:
                try:
                    proc.terminate()
                    killed = True
                except Exception:
                    continue
        if killed:
            wanted = {n.lower() for n in names}
            _, alive = psutil.wait_procs(
                [p for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() in wanted],
                timeout=5,
            )
            for proc in alive:
                try:
                    proc.kill()
                except Exception:
                    continue
        return killed
    except Exception:  # pragma: no cover - psutil optional
        pass
    for name in names:
        if IS_WINDOWS:  # pragma: no cover - Windows only
            if run(["taskkill", "/f", "/im", name], timeout=20).ok:
                killed = True
        else:
            if run(["pkill", "-f", name], timeout=20).ok:
                killed = True
    return killed


class BackgroundProcess:
    """A long-lived child process whose output is tailed into the log."""

    def __init__(self, args: Sequence[str], log_name: str = "app", cwd: str | None = None) -> None:
        self.args = [str(a) for a in args]
        self.log_name = log_name
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.lines: list[str] = []
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        logger = get_logger(self.log_name)
        logger.info("starting: %s", " ".join(self.args))
        self.proc = subprocess.Popen(  # noqa: S603 - args is a list, shell=False
            self.args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self.cwd,
            **no_window_kwargs(),
        )
        self._thread = threading.Thread(target=self._tail, name="proc-tail", daemon=True)
        self._thread.start()

    def _tail(self) -> None:
        logger = get_logger(self.log_name)
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.rstrip("\r\n")
            self.lines.append(text)
            if len(self.lines) > 2000:
                del self.lines[:1000]
            logger.debug("%s", text)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc is not None:
            terminate(self.proc)
