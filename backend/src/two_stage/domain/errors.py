from __future__ import annotations


class TwoStageError(Exception):
    code = "TWO_STAGE_ERROR"


class DomainValidationError(TwoStageError):
    code = "DOMAIN_VALIDATION_ERROR"


class ContractValidationError(TwoStageError):
    code = "CONTRACT_VALIDATION_ERROR"


class ArtifactNotFoundError(TwoStageError):
    code = "ARTIFACT_NOT_FOUND"


class StageDependencyError(TwoStageError):
    code = "STAGE_DEPENDENCY_ERROR"


class ExperimentStateError(TwoStageError):
    code = "EXPERIMENT_STATE_ERROR"


class ExternalServiceError(TwoStageError):
    code = "EXTERNAL_SERVICE_ERROR"


class DecisionParseError(TwoStageError):
    code = "DECISION_PARSE_ERROR"


class MetricDefinitionNotApprovedError(TwoStageError):
    code = "METRIC_DEFINITION_NOT_APPROVED"


class PolicyNotAllowedError(TwoStageError):
    code = "POLICY_NOT_ALLOWED_FOR_RUN_PURPOSE"


class ArtifactPublicationError(TwoStageError):
    code = "ARTIFACT_PUBLICATION_ERROR"
