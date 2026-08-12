from __future__ import annotations

from dataclasses import dataclass

from two_stage.domain.enums import RunStatus, StageAttemptStatus, StageRunStatus
from two_stage.domain.errors import ExperimentStateError


@dataclass(frozen=True, slots=True)
class StageTransition:
    previous: str
    next: str
    reason: str | None = None


def assert_stage_success_requires_published_artifact(
    attempt_status: StageAttemptStatus,
    artifact_published: bool,
) -> None:
    if attempt_status == StageAttemptStatus.SUCCEEDED and not artifact_published:
        raise ExperimentStateError("A succeeded stage attempt must reference a published artifact.")


def can_resume_run(status: RunStatus) -> bool:
    return status in {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.RESUMABLE}


def next_retry_attempt_no(previous_attempt_numbers: list[int]) -> int:
    if not previous_attempt_numbers:
        return 1
    return max(previous_attempt_numbers) + 1


def mark_stage_stale(reason: str) -> StageTransition:
    return StageTransition(
        previous=StageRunStatus.SUCCEEDED.value,
        next=StageRunStatus.STALE.value,
        reason=reason,
    )
