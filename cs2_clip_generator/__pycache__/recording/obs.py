"""OBS Studio recorder, driven over obs-websocket v5.

OBS has shipped its WebSocket server since version 28, so nothing extra needs
installing: enable it in *Tools → WebSocket Server Settings*, note the port and
password, and this recorder can start and stop recordings and ask OBS where it
put the file.

The requests used are the documented v5 ones: ``GetVersion``, ``SetCurrentProgramScene``,
``StartRecord``, ``StopRecord`` (whose response carries ``outputPath``).
Playback runs in real time, so the pipeline records the interval and FFmpeg trims
the safety margins afterwards.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..core.errors import RecorderError
from ..core.logger import get_logger
from ..cs2.controller import RecorderHooks
from ..utils.process import which
from .base import Recorder, RecordingContext

log = get_logger("recorder")


def obs_client_available() -> bool:
    try:
        import obsws_python  # noqa: F401
    except Exception:
        return False
    return True


def find_obs_executable(explicit: str = "") -> str | None:
    if explicit and Path(explicit).is_file():
        return explicit
    candidates = [
        r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
        r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
    ]
    return which("obs64" if Path("C:/").exists() else "obs", extra_paths=candidates)


class OBSRecorder(Recorder):
    name = "obs"
    in_game = False

    def __init__(self, settings, ffmpeg=None) -> None:  # noqa: ANN001 - see base class
        super().__init__(settings, ffmpeg)
        self._client = None
        self._recording = False
        self._output_path: Path | None = None

    # -- capability ------------------------------------------------------
    def available(self, cs2_executable: str = "") -> tuple[bool, str]:
        del cs2_executable
        if not obs_client_available():
            return False, "The 'obsws-python' package is not installed"
        if not self.ffmpeg.available:
            return False, "FFmpeg is required to trim and re-encode the OBS recording"
        ok, detail = self.check_connection()
        return ok, detail

    def check_connection(self) -> tuple[bool, str]:
        """Try a real connection so Settings can show the truth, not a guess."""
        try:
            client = self._connect()
        except RecorderError as exc:
            return False, exc.title
        try:
            version = client.get_version()
            obs_version = getattr(version, "obs_version", "?")
            return True, f"Connected to OBS {obs_version}"
        except Exception as exc:  # pragma: no cover - network
            return False, f"OBS did not answer: {exc}"
        finally:
            self._disconnect()

    # -- connection ------------------------------------------------------
    def _connect(self):  # noqa: ANN202 - obsws_python client
        if self._client is not None:
            return self._client
        try:
            import obsws_python as obs
        except Exception as exc:
            raise RecorderError(
                title="OBS support is not installed.",
                reasons=["The 'obsws-python' package is missing"],
                actions=["Run: pip install obsws-python", "Choose another recorder in Settings"],
                detail=str(exc),
            ) from exc
        try:
            self._client = obs.ReqClient(
                host=self.settings.obs_host or "localhost",
                port=int(self.settings.obs_port or 4455),
                password=self.settings.obs_password or "",
                timeout=5,
            )
        except Exception as exc:
            raise RecorderError(
                title="Could not connect to OBS.",
                reasons=[
                    "OBS Studio is not running",
                    "The WebSocket server is disabled (Tools → WebSocket Server Settings)",
                    "The port or password in Settings is wrong",
                ],
                actions=["Start OBS", "Enable the WebSocket server", "Open Settings"],
                detail=str(exc),
            ) from exc
        return self._client

    def _disconnect(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            client.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass

    # -- lifecycle -------------------------------------------------------
    def begin_session(self, context: RecordingContext) -> None:
        client = self._connect()
        scene = self.settings.obs_scene
        if scene:
            try:
                client.set_current_program_scene(scene)
                log.info("OBS scene set to '%s'", scene)
            except Exception as exc:
                raise RecorderError(
                    title=f"The OBS scene '{scene}' could not be selected.",
                    reasons=["The scene does not exist in the current OBS profile"],
                    actions=["Check the scene name in Settings"],
                    detail=str(exc),
                ) from exc
        del context

    def hooks(self, context: RecordingContext) -> RecorderHooks:
        def start() -> None:
            client = self._connect()
            try:
                client.start_record()
                self._recording = True
                log.info("OBS recording started for %s", context.clip_name)
            except Exception as exc:
                raise RecorderError(
                    title="OBS refused to start recording.",
                    reasons=["Another recording is already running", "The output settings are invalid"],
                    actions=["Stop the running recording in OBS", "Retry"],
                    detail=str(exc),
                ) from exc

        def stop() -> None:
            if not self._recording:
                return
            client = self._connect()
            try:
                response = client.stop_record()
                self._recording = False
                path = getattr(response, "output_path", None) or getattr(response, "outputPath", None)
                if path:
                    self._output_path = Path(str(path))
                    context.metadata["obs_output"] = str(path)
                    log.info("OBS wrote %s", path)
            except Exception as exc:  # pragma: no cover - network
                log.error("stopping the OBS recording failed: %s", exc)

        return RecorderHooks(on_start=start, on_stop=stop, real_time_playback=True)

    # -- output ----------------------------------------------------------
    def finalise(self, context: RecordingContext, on_progress=None, cancel=None) -> Path:  # noqa: ANN001
        source = self._output_path or self._await_output(context)
        if source is None or not source.is_file():
            raise RecorderError(
                title="The OBS recording could not be found.",
                reasons=[
                    "OBS reported no output path",
                    "The recording folder is not accessible",
                ],
                actions=["Check the OBS recording path", "Retry"],
            )
        # OBS was recording a little before and after the interval; trim it back.
        duration = context.expected_duration
        start = context.safety_margin if context.safety_margin > 0 else None
        self.ffmpeg.encode(
            input_path=str(source),
            output_path=str(context.output_path),
            settings=context.video,
            start_seconds=start,
            duration_seconds=duration,
            on_progress=on_progress,
            cancel=cancel,
        )
        context.metadata["obs_source"] = str(source)
        return context.output_path

    def _await_output(self, context: RecordingContext, timeout: float = 15.0) -> Path | None:
        """OBS finishes writing the file slightly after ``StopRecord`` returns."""
        recorded = context.metadata.get("obs_output")
        if recorded:
            path = Path(str(recorded))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if path.is_file() and path.stat().st_size > 0:
                    return path
                time.sleep(0.5)
            return path if path.is_file() else None
        return None

    def end_session(self, context: RecordingContext | None = None) -> None:
        del context
        if self._recording:
            try:
                self._connect().stop_record()
            except Exception:  # pragma: no cover - best effort
                pass
            self._recording = False
        self._disconnect()

    def cleanup(self, context: RecordingContext) -> None:
        source = context.metadata.get("obs_source")
        if source and Path(str(source)).is_file():
            try:
                Path(str(source)).unlink()
            except OSError:
                pass
