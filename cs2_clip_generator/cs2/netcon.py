"""Remote console over TCP (``-netconport``).

When it is available this is the nicest way to drive CS2: a plain TCP socket
where each line is a console command, and the game's console output comes back
on the same socket. That gives real feedback — we can send ``echo`` markers and
wait until they come back, instead of hoping a fixed sleep was long enough.

Availability is *not* guaranteed. Valve has changed the behaviour more than once
and on some builds ``-netconport`` only opens when the Workshop Tools are
installed (``-tools``). So the application never assumes: it launches the game
with the flag, probes the port, and falls back to another playback backend when
the probe fails.
"""

from __future__ import annotations

import socket
import time
from collections.abc import Iterable

from ..core.logger import get_logger

log = get_logger("cs2")

DEFAULT_PORT = 29070


class NetConsole:
    """A line-oriented client for CS2's remote console."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 5.0) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = ""

    # -- connection ------------------------------------------------------
    def connect(self, retries: int = 1, delay: float = 1.0) -> bool:
        for attempt in range(max(1, retries)):
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                sock.settimeout(0.25)
                self._socket = sock
                log.info("netcon connected to %s:%s", self.host, self.port)
                return True
            except OSError as exc:
                log.debug("netcon connect attempt %d failed: %s", attempt + 1, exc)
                if attempt + 1 < retries:
                    time.sleep(delay)
        return False

    def wait_for_port(self, timeout: float = 60.0, poll_interval: float = 1.0) -> bool:
        """Block until the console port accepts a connection."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.connect(retries=1):
                return True
            time.sleep(poll_interval)
        return False

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def __enter__(self) -> NetConsole:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- commands --------------------------------------------------------
    def send(self, command: str) -> bool:
        if self._socket is None:
            return False
        payload = (command.rstrip("\n") + "\n").encode("utf-8", "replace")
        try:
            self._socket.sendall(payload)
            log.debug("netcon > %s", command)
            return True
        except OSError as exc:
            log.debug("netcon send failed: %s", exc)
            self.close()
            return False

    def send_all(self, commands: Iterable[str], delay: float = 0.0) -> bool:
        ok = True
        for command in commands:
            ok = self.send(command) and ok
            if delay:
                time.sleep(delay)
        return ok

    # -- reading ---------------------------------------------------------
    def read(self, timeout: float = 0.25) -> str:
        """Drain whatever the console has produced so far."""
        if self._socket is None:
            return ""
        chunks: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = self._socket.recv(8192)
            except (TimeoutError, socket.timeout):  # noqa: UP041 - socket.timeout alias
                break
            except OSError:
                self.close()
                break
            if not data:
                break
            chunks.append(data.decode("utf-8", "replace"))
        text = "".join(chunks)
        self._buffer += text
        if len(self._buffer) > 200_000:
            self._buffer = self._buffer[-100_000:]
        return text

    def wait_for(self, needle: str, timeout: float = 30.0) -> bool:
        """Wait until ``needle`` shows up in console output."""
        deadline = time.monotonic() + timeout
        if needle in self._buffer:
            return True
        while time.monotonic() < deadline:
            self.read(timeout=0.25)
            if needle in self._buffer:
                return True
        return False

    def ping(self, timeout: float = 5.0) -> bool:
        """Round-trip an ``echo`` to prove the console really is listening."""
        marker = f"cs2clip_ping_{int(time.time() * 1000)}"
        if not self.send(f"echo {marker}"):
            return False
        return self.wait_for(marker, timeout=timeout)

    def clear_buffer(self) -> None:
        self._buffer = ""


def probe(host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Is something listening on the console port right now?"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    """A port CS2 can bind: the preferred one if free, else an ephemeral one."""
    for candidate in (preferred, preferred + 1, preferred + 2):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
