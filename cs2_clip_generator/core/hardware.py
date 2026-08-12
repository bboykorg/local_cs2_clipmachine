"""Hardware inventory for the Dashboard and the pre-render checks."""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..utils.process import run


@dataclass
class HardwareInfo:
    cpu: str = "Unknown CPU"
    cores: int = 0
    ram_total_mb: float = 0.0
    ram_available_mb: float = 0.0
    gpus: tuple[str, ...] = ()
    os_name: str = ""

    @property
    def gpu(self) -> str:
        return self.gpus[0] if self.gpus else "Unknown GPU"

    @property
    def ram_total_gb(self) -> float:
        return self.ram_total_mb / 1024

    @property
    def ram_available_gb(self) -> float:
        return self.ram_available_mb / 1024


def _cpu_name() -> str:
    if os.name == "nt":  # pragma: no cover - Windows only
        result = run(["wmic", "cpu", "get", "name", "/value"], timeout=15)
        for line in (result.stdout or "").splitlines():
            if line.lower().startswith("name="):
                return line.split("=", 1)[1].strip()
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
    else:
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.is_file():
            for line in cpuinfo.read_text(errors="replace").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine() or "Unknown CPU"


def _gpu_names() -> tuple[str, ...]:
    names: list[str] = []
    if shutil.which("nvidia-smi"):
        result = run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], timeout=15)
        names += [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if os.name == "nt" and not names:  # pragma: no cover - Windows only
        result = run(["wmic", "path", "win32_VideoController", "get", "name", "/value"], timeout=20)
        names += [
            line.split("=", 1)[1].strip()
            for line in (result.stdout or "").splitlines()
            if line.lower().startswith("name=") and line.split("=", 1)[1].strip()
        ]
    if not names and shutil.which("lspci"):
        result = run(["lspci"], timeout=15)
        for line in (result.stdout or "").splitlines():
            if "VGA compatible controller" in line or "3D controller" in line:
                names.append(line.split(":", 2)[-1].strip())
    return tuple(dict.fromkeys(names))


def _memory() -> tuple[float, float]:
    try:
        import psutil  # type: ignore

        memory = psutil.virtual_memory()
        return memory.total / (1024 * 1024), memory.available / (1024 * 1024)
    except Exception:
        pass
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, float] = {}
        for line in meminfo.read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    values[parts[0].rstrip(":")] = float(parts[1]) / 1024
                except ValueError:
                    continue
        return values.get("MemTotal", 0.0), values.get("MemAvailable", values.get("MemFree", 0.0))
    return 0.0, 0.0


def collect_hardware() -> HardwareInfo:
    total, available = _memory()
    return HardwareInfo(
        cpu=_cpu_name(),
        cores=os.cpu_count() or 0,
        ram_total_mb=total,
        ram_available_mb=available,
        gpus=_gpu_names(),
        os_name=f"{platform.system()} {platform.release()}",
    )


def suggested_encoder(hardware: HardwareInfo) -> str:
    """Which hardware encoder family the GPU most likely supports."""
    gpu = " ".join(hardware.gpus).lower()
    if "nvidia" in gpu or "geforce" in gpu or "rtx" in gpu or "quadro" in gpu:
        return "nvenc"
    if "radeon" in gpu or "amd" in gpu:
        return "amf"
    if "intel" in gpu or "arc" in gpu or "uhd" in gpu or "iris" in gpu:
        return "qsv"
    return "cpu"


def disk_report(paths: list[str]) -> list[tuple[str, float, float]]:
    """``(path, free_mb, total_mb)`` for each configured folder."""
    out: list[tuple[str, float, float]] = []
    for path in paths:
        target = Path(path)
        while not target.exists() and target.parent != target:
            target = target.parent
        try:
            usage = shutil.disk_usage(str(target))
            out.append((str(path), usage.free / (1024 * 1024), usage.total / (1024 * 1024)))
        except OSError:
            continue
    return out
