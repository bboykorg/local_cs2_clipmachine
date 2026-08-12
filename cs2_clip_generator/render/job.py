"""Job helpers shared by the queue and the UI."""

from __future__ import annotations

from ..core.models import JobState, RenderJob

STATE_LABELS = {
    JobState.PENDING: "Waiting",
    JobState.RUNNING: "Rendering…",
    JobState.DONE: "Done",
    JobState.FAILED: "Failed",
    JobState.CANCELLED: "Cancelled",
    JobState.SKIPPED: "Skipped",
}

STATE_COLORS = {
    JobState.PENDING: "#8b93a7",
    JobState.RUNNING: "#5b8cff",
    JobState.DONE: "#3ddc97",
    JobState.FAILED: "#ff5c72",
    JobState.CANCELLED: "#c58af9",
    JobState.SKIPPED: "#c9a227",
}


def state_label(job: RenderJob) -> str:
    if job.state == JobState.RUNNING and job.message:
        return job.message
    return STATE_LABELS.get(job.state, job.state.value)


def state_color(job: RenderJob) -> str:
    return STATE_COLORS.get(job.state, "#8b93a7")


def is_finished(job: RenderJob) -> bool:
    return job.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED, JobState.SKIPPED)


def summarise(jobs: list[RenderJob]) -> str:
    done = sum(1 for job in jobs if job.state == JobState.DONE)
    failed = sum(1 for job in jobs if job.state == JobState.FAILED)
    pending = sum(1 for job in jobs if job.state == JobState.PENDING)
    parts = [f"{done} done"]
    if pending:
        parts.append(f"{pending} waiting")
    if failed:
        parts.append(f"{failed} failed")
    return ", ".join(parts)
