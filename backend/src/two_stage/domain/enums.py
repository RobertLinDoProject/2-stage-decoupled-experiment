from __future__ import annotations

from enum import StrEnum


class RunPurpose(StrEnum):
    DEVELOPMENT = "development"
    EXPLORATORY = "exploratory"
    FORMAL = "formal"


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class PolicyBundleStatus(StrEnum):
    DRAFT = "draft"
    EXPERIMENTAL = "experimental"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class PolicyBundleReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"


class ResourceStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class ExperimentDraftStatus(StrEnum):
    DRAFT = "draft"
    TECHNICALLY_VALID = "technically_valid"
    SNAPSHOTTED = "snapshotted"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESUMABLE = "resumable"


class StageRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE = "stale"
    NOT_REQUIRED = "not_required"


class StageAttemptStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    VERIFYING = "verifying"
    PUBLISHING = "publishing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


class ImportJobStatus(StrEnum):
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    NEEDS_CONFIRMATION = "needs_confirmation"
    MATERIALIZING = "materializing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ArtifactPublicationState(StrEnum):
    STAGED = "staged"
    VERIFIED = "verified"
    PUBLISHED = "published"
    ABANDONED = "abandoned"


class PreflightStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class CheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EventSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DecisionInterfaceType(StrEnum):
    RULE = "rule"
    GAI = "gai"
    MOCK = "mock"


class PipelineProfile(StrEnum):
    STAGE2_TOPOLOGY_IDEAL_V1 = "stage2_topology_ideal_v1"
    STAGE1_PERCEPTION_BENCHMARK_V1 = "stage1_perception_benchmark_v1"
    TWO_STAGE_INTEGRATED_V1 = "two_stage_integrated_v1"
    M5_CONTROLLED_PERTURBATION_EXPLORATORY_V1 = (
        "m5_controlled_perturbation_exploratory_v1"
    )
    M5_DECISION_VALIDATION_EXPLORATORY_V1 = "m5_decision_validation_exploratory_v1"
    M8_M9_INTEGRATED_REPORT_EXPLORATORY_V1 = "m8_m9_integrated_report_exploratory_v1"
