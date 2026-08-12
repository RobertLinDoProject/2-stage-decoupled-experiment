from __future__ import annotations

from two_stage.domain.enums import PolicyStatus, RunPurpose


def is_policy_allowed(
    status: PolicyStatus,
    run_purpose: RunPurpose,
    *,
    approved_for_formal_run: bool,
    exploratory_draft_override: bool = False,
) -> bool:
    if status == PolicyStatus.DEPRECATED:
        return False

    if run_purpose == RunPurpose.DEVELOPMENT:
        return status in {PolicyStatus.DRAFT, PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED}

    if run_purpose == RunPurpose.EXPLORATORY:
        if status == PolicyStatus.DRAFT:
            return exploratory_draft_override
        return status in {PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED}

    if run_purpose == RunPurpose.FORMAL:
        return status == PolicyStatus.APPROVED and approved_for_formal_run

    return False
