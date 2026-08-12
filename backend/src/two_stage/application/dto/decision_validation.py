from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from two_stage.application.dto.stage2 import ArtifactRecord, StageStatusRecord
from two_stage.domain.enums import RunPurpose

PipelineProfileId = Literal["m5_decision_validation_exploratory_v1"]
TrialType = Literal["controlled_error"]
InputMode = Literal["perturbed_observation"]
InterfaceType = Literal["rule", "gai"]
DecisionStatus = Literal["parsed", "invalid_output", "unavailable", "timeout", "error"]
GaiMode = Literal["unavailable", "mock", "http"]


class M5DecisionValidationPipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "m5_decision_validation_exploratory_v1"
    run_id: str
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY

    m5_run_id: str | None = None
    m5_observation_artifact_id: str | None = None
    m5_observation_uri: str
    m5_observation_checksum: str
    m5_error_realizations_artifact_id: str | None = None
    m5_error_realizations_uri: str
    m5_error_realizations_checksum: str
    m3_topology_spec_artifact_id: str | None = None
    m3_topology_spec_uri: str
    m3_topology_spec_checksum: str
    m4_scenario_gt_artifact_id: str | None = None
    m4_scenario_gt_uri: str
    m4_scenario_gt_checksum: str

    decision_policy_id: str = "rule_capacity_relief_v1"
    decision_policy_version: str = "0.1.0"
    validator_policy_id: str = "validator_m5_controlled_observation_v1"
    validator_policy_version: str = "0.1.0"
    gai_mode: GaiMode = "unavailable"
    include_rule: bool = True
    include_mock_gai: bool = False
    live_gai_provider_enabled: bool = False
    formal_output_enabled: bool = False
    integrated_profile_enabled: bool = False
    max_targets_per_source: int = Field(default=1, ge=1)
    high_source_occupancy_threshold: float = Field(default=0.80, gt=0.0, le=1.0)
    target_occupancy_limit: float = Field(default=0.90, gt=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ObservedPopulationEntry(BaseModel):
    node_id: str
    condition_id: str | None = None
    dataset_id: str | None = None
    model_id: str | None = None
    paradigm: str | None = None
    split: str | None = None
    ground_truth_population: float
    observed_population: float
    raw_observed_population: float
    residual_error: float
    capacity: int
    occupancy_ratio: float = Field(ge=0.0)
    ground_truth_regime: str
    adjustment_reason: str
    invalid_observation: bool
    capacity_exceeded: bool
    residual_source_sample_id: str
    residual_source_pool_id: str | None = None


class PerturbedObservationRecord(BaseModel):
    trial_id: str
    scenario_id: str
    error_realization_id: str
    condition_id: str | None = None
    condition_label: str | None = None
    dataset_id: str | None = None
    model_id: str | None = None
    paradigm: str | None = None
    split: str | None = None
    trial_type: TrialType = "controlled_error"
    input_mode: InputMode = "perturbed_observation"
    observed_population: list[ObservedPopulationEntry]
    scenario_gt_artifact_id: str | None
    error_realization_artifact_id: str
    topology_artifact_id: str | None


class DecisionAction(BaseModel):
    action_id: str
    from_node: str
    to_node: str
    count: int = Field(gt=0)


class DecisionRequest(BaseModel):
    request_id: str
    trial_id: str
    scenario_id: str
    error_realization_id: str
    condition_id: str | None = None
    dataset_id: str | None = None
    model_id: str | None = None
    paradigm: str | None = None
    split: str | None = None
    trial_type: TrialType = "controlled_error"
    input_mode: InputMode = "perturbed_observation"
    observation_checksum: str
    topology_checksum: str
    m5_observation_source_stage_id: Literal["M5"] = "M5"


class ExploratoryDecisionResult(BaseModel):
    decision_id: str
    request_id: str
    interface_type: InterfaceType
    trial_id: str
    scenario_id: str
    error_realization_id: str
    condition_id: str | None = None
    dataset_id: str | None = None
    model_id: str | None = None
    paradigm: str | None = None
    split: str | None = None
    trial_type: TrialType = "controlled_error"
    input_mode: InputMode = "perturbed_observation"
    status: DecisionStatus = "parsed"
    actions: list[DecisionAction] = Field(default_factory=list)
    input_checksum: str
    topology_checksum: str
    capacity_checksum: str
    observation_checksum: str
    provider: str | None = None
    policy_id: str
    policy_version: str


class ValidationViolation(BaseModel):
    code: str
    message_zh_tw: str
    node_id: str | None = None
    edge_id: str | None = None
    action_id: str | None = None
    capacity: int | None = None
    post_occupancy: int | None = None
    exceeded_by: int | None = None


class ExploratoryValidationResult(BaseModel):
    validation_id: str
    decision_id: str
    interface_type: InterfaceType
    trial_id: str
    scenario_id: str
    error_realization_id: str
    condition_id: str | None = None
    dataset_id: str | None = None
    model_id: str | None = None
    paradigm: str | None = None
    split: str | None = None
    valid: bool
    validator_policy_id: str
    validator_policy_version: str
    ground_truth_source_stage_id: Literal["M4"] = "M4"
    ground_truth_checksum: str
    observation_checksum: str
    violations: list[ValidationViolation] = Field(default_factory=list)
    legality_score: float = Field(ge=0.0, le=1.0)
    priority_score: float = Field(ge=0.0, le=1.0)
    economy_score: float = Field(ge=0.0, le=1.0)
    fatal_legality_gate: bool
    action_consistency_score: float = Field(ge=0.0, le=1.0)
    expected_high_sources: list[str]
    recommended_sources: list[str]
    risk_tp: int = Field(ge=0)
    risk_fp: int = Field(ge=0)
    risk_fn: int = Field(ge=0)


class M5DecisionValidationRunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    m6_observation_checksum: str
    m7_ground_truth_checksum: str
    rule_decision_count: int
    gai_status: str
    validation_result_count: int
    m6_m7_only: bool = True
    integrated_profile_enabled: bool = False
    formal_output_enabled: bool = False
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
