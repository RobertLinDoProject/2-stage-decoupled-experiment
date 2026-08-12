from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from two_stage.domain.enums import CheckStatus, IssueSeverity, PreflightStatus, RunPurpose


class PreflightIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    blocking: bool
    context: dict[str, object] = Field(default_factory=dict)


class PreflightCheck(BaseModel):
    check_id: str
    status: CheckStatus
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class BasePreflightReport(BaseModel):
    report_id: str
    report_type: str
    run_purpose: RunPurpose
    status: PreflightStatus
    checks: list[PreflightCheck] = Field(default_factory=list)
    issues: list[PreflightIssue] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TechnicalReadiness(BaseModel):
    artifact_store_ready: bool = False
    required_sources_present: bool = False


class ResearchReadiness(BaseModel):
    run_purpose: RunPurpose
    policy_statuses: dict[str, str] = Field(default_factory=dict)
    resource_statuses: dict[str, str] = Field(default_factory=dict)
    uses_synthetic_fixture: bool = False
    uses_mock_provider: bool = False
    has_stale_artifacts: bool = False
    has_unpublished_artifacts: bool = False
    exploratory_draft_override: bool = False


class TechnicalPreflightReport(BasePreflightReport):
    report_type: str = "technical"


class ResearchPreflightReport(BasePreflightReport):
    report_type: str = "research"
