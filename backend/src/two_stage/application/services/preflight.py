from __future__ import annotations

from uuid import uuid4

from two_stage.application.dto.preflight import (
    PreflightCheck,
    PreflightIssue,
    ResearchPreflightReport,
    ResearchReadiness,
    TechnicalPreflightReport,
    TechnicalReadiness,
)
from two_stage.domain.enums import (
    CheckStatus,
    IssueSeverity,
    PolicyStatus,
    PreflightStatus,
    ResourceStatus,
    RunPurpose,
)


def _overall_status(issues: list[PreflightIssue]) -> PreflightStatus:
    if any(issue.blocking for issue in issues):
        return PreflightStatus.FAILED
    if issues:
        return PreflightStatus.WARNING
    return PreflightStatus.PASSED


def evaluate_technical_preflight(
    readiness: TechnicalReadiness,
    *,
    run_purpose: RunPurpose,
) -> TechnicalPreflightReport:
    checks: list[PreflightCheck] = []
    issues: list[PreflightIssue] = []

    required_checks = {
        "artifact_store_ready": readiness.artifact_store_ready,
        "required_sources_present": readiness.required_sources_present,
    }

    for check_id, is_ready in required_checks.items():
        status = CheckStatus.PASSED if is_ready else CheckStatus.FAILED
        checks.append(
            PreflightCheck(
                check_id=check_id,
                status=status,
                message=f"{check_id} = {is_ready}",
            )
        )
        if not is_ready:
            issues.append(
                PreflightIssue(
                    code=f"TECHNICAL_{check_id.upper()}_FAILED",
                    severity=IssueSeverity.ERROR,
                    message=f"Technical dependency {check_id} is not ready.",
                    blocking=True,
                    context={"check_id": check_id},
                )
            )

    return TechnicalPreflightReport(
        report_id=f"TPF-{uuid4().hex[:16]}",
        run_purpose=run_purpose,
        status=_overall_status(issues),
        checks=checks,
        issues=issues,
    )


def evaluate_research_preflight(readiness: ResearchReadiness) -> ResearchPreflightReport:
    checks: list[PreflightCheck] = []
    issues: list[PreflightIssue] = []

    for policy_id, raw_status in readiness.policy_statuses.items():
        status = PolicyStatus(raw_status)
        allowed = _policy_allowed_for_research(
            status,
            readiness.run_purpose,
            readiness.exploratory_draft_override,
        )
        checks.append(
            PreflightCheck(
                check_id=f"policy:{policy_id}",
                status=CheckStatus.PASSED if allowed else CheckStatus.FAILED,
                message=f"Policy {policy_id} is {status.value}.",
                details={"policy_id": policy_id, "status": status.value},
            )
        )
        if not allowed:
            issues.append(
                PreflightIssue(
                    code="POLICY_NOT_ALLOWED_FOR_RUN_PURPOSE",
                    severity=IssueSeverity.ERROR,
                    message=f"Policy {policy_id} is not allowed for {readiness.run_purpose.value}.",
                    blocking=readiness.run_purpose == RunPurpose.FORMAL,
                    context={"policy_id": policy_id, "status": status.value},
                )
            )

    for resource_id, raw_status in readiness.resource_statuses.items():
        resource_status = ResourceStatus(raw_status)
        allowed = (
            readiness.run_purpose != RunPurpose.FORMAL
            or resource_status == ResourceStatus.APPROVED
        )
        checks.append(
            PreflightCheck(
                check_id=f"resource:{resource_id}",
                status=CheckStatus.PASSED if allowed else CheckStatus.FAILED,
                message=f"Resource {resource_id} is {resource_status.value}.",
                details={"resource_id": resource_id, "status": resource_status.value},
            )
        )
        if not allowed:
            issues.append(
                PreflightIssue(
                    code="RESOURCE_NOT_APPROVED_FOR_FORMAL",
                    severity=IssueSeverity.ERROR,
                    message=f"Resource {resource_id} must be approved for formal run.",
                    blocking=True,
                    context={"resource_id": resource_id, "status": resource_status.value},
                )
            )

    if readiness.run_purpose == RunPurpose.FORMAL:
        formal_blockers = {
            "SYNTHETIC_FIXTURE_FORBIDDEN": readiness.uses_synthetic_fixture,
            "MOCK_PROVIDER_FORBIDDEN": readiness.uses_mock_provider,
            "STALE_ARTIFACT_FORBIDDEN": readiness.has_stale_artifacts,
            "UNPUBLISHED_ARTIFACT_FORBIDDEN": readiness.has_unpublished_artifacts,
        }
        for code, triggered in formal_blockers.items():
            checks.append(
                PreflightCheck(
                    check_id=code.lower(),
                    status=CheckStatus.FAILED if triggered else CheckStatus.PASSED,
                    message=f"{code} = {triggered}",
                )
            )
            if triggered:
                issues.append(
                    PreflightIssue(
                        code=code,
                        severity=IssueSeverity.ERROR,
                        message=f"{code} blocks formal output.",
                        blocking=True,
                    )
                )

    return ResearchPreflightReport(
        report_id=f"RPF-{uuid4().hex[:16]}",
        run_purpose=readiness.run_purpose,
        status=_overall_status(issues),
        checks=checks,
        issues=issues,
    )


def _policy_allowed_for_research(
    status: PolicyStatus,
    run_purpose: RunPurpose,
    exploratory_draft_override: bool,
) -> bool:
    if status == PolicyStatus.DEPRECATED:
        return False
    if run_purpose == RunPurpose.DEVELOPMENT:
        return status in {PolicyStatus.DRAFT, PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED}
    if run_purpose == RunPurpose.EXPLORATORY:
        return status in {PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED} or (
            status == PolicyStatus.DRAFT and exploratory_draft_override
        )
    return status == PolicyStatus.APPROVED
