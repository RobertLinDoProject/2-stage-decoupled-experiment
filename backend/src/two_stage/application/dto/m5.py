from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from two_stage.application.dto.stage2 import ArtifactRecord, StageStatusRecord
from two_stage.domain.enums import RunPurpose

JsonDict = dict[str, Any]
PipelineProfileId = Literal["m5_controlled_perturbation_exploratory_v1"]
PerceptionParadigm = Literal["detection", "density"]
SamplingReplacement = Literal["with_replacement", "without_replacement"]
NegativeHandling = Literal["lower_bound_zero", "mark_invalid", "fail_trial"]
RoundingPolicy = Literal["nearest_integer", "floor", "ceil", "none"]
CapacityHandling = Literal["flag_only", "clip_to_capacity", "fail_trial"]
InsufficientPoolPolicy = Literal["skip_condition", "fail_run"]

PAPER_ALIGNED_M5_POLICY_ID = "paper_aligned_m5_empirical_residual_policy_v1"


class M5ExploratoryPipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "m5_controlled_perturbation_exploratory_v1"
    run_id: str
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY

    stage1_run_id: str | None = None
    stage2_run_id: str | None = None
    m2_error_samples_artifact_id: str | None = None
    m2_error_samples_uri: str
    m2_error_samples_checksum: str
    m3_topology_spec_artifact_id: str | None = None
    m3_topology_spec_uri: str
    m3_topology_spec_checksum: str
    m4_scenario_gt_artifact_id: str | None = None
    m4_scenario_gt_uri: str
    m4_scenario_gt_checksum: str

    m5_policy_bundle_snapshot_id: str | None = None
    m5_policy_bundle_snapshot_hash: str | None = None
    m5_policy_bundle: JsonDict = Field(default_factory=dict)
    exploratory_policy_override: bool = True
    formal_output_enabled: bool = False
    integrated_profile_enabled: bool = False
    paper_aligned_policy_id: str = PAPER_ALIGNED_M5_POLICY_ID

    residual_dataset_id: str | None = None
    residual_model_id: str | None = None
    residual_paradigm: PerceptionParadigm | None = None
    residual_split: str = "test"
    residual_pool_strategy: str = "paper_aligned_all_models_separate_paradigm_v1"
    residual_grouping_strategy: str = "regime_conditioned_pool_lookup_v0_1"
    residual_assignment_strategy: str = "nodewise_regime_conditioned_v0_1"
    assignment_unit: str = "scenario_node"
    observation_adjustment_family: str = "preserve_raw_then_adjust_decision_input_v0_1"

    root_seed: int = 114
    trial_count: int = Field(default=300, ge=1, le=1000)
    minimum_pool_size: int = Field(default=30, ge=1)
    insufficient_pool_policy: InsufficientPoolPolicy = "skip_condition"
    sampling_replacement: SamplingReplacement = "with_replacement"
    negative_handling: NegativeHandling = "lower_bound_zero"
    rounding: RoundingPolicy = "nearest_integer"
    capacity_handling: CapacityHandling = "flag_only"
    exploratory_aggregation: str = "exploratory_by_paradigm_model_condition_v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class M5ErrorRealizationRow(BaseModel):
    error_realization_id: str
    trial_id: str
    trial_index: int
    condition_id: str
    condition_label: str
    dataset_id: str
    model_id: str
    paradigm: PerceptionParadigm
    split: str
    scenario_id: str
    node_id: str
    ground_truth_population: float
    ground_truth_regime: str
    residual_error: float
    raw_observed_population: float
    observed_population: float
    adjustment_reason: str
    invalid_observation: bool
    capacity: int
    capacity_exceeded: bool
    capacity_exceeded_by: float
    residual_source_sample_id: str
    residual_source_pool_id: str
    residual_source_artifact_id: str
    scenario_gt_artifact_id: str
    topology_artifact_id: str
    policy_ref: str
    seed: int


class M5ObservedPopulationEntry(BaseModel):
    node_id: str
    condition_id: str
    dataset_id: str
    model_id: str
    paradigm: PerceptionParadigm
    split: str
    ground_truth_population: float
    observed_population: float
    raw_observed_population: float
    residual_error: float
    capacity: int
    occupancy_ratio: float
    ground_truth_regime: str
    adjustment_reason: str
    invalid_observation: bool
    capacity_exceeded: bool
    residual_source_sample_id: str
    residual_source_pool_id: str


class M5PerturbedObservationRecord(BaseModel):
    trial_id: str
    scenario_id: str
    error_realization_id: str
    condition_id: str
    condition_label: str
    dataset_id: str
    model_id: str
    paradigm: PerceptionParadigm
    split: str
    trial_type: Literal["controlled_error"] = "controlled_error"
    input_mode: Literal["perturbed_observation"] = "perturbed_observation"
    observed_population: list[M5ObservedPopulationEntry]
    scenario_gt_artifact_id: str | None
    error_realization_artifact_id: str
    topology_artifact_id: str | None


class M5ExploratoryRunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    m5_error_realizations_checksum: str
    m5_observation_checksum: str
    controlled_error_trials: int
    assignment_row_count: int
    condition_count: int = 0
    skipped_condition_count: int = 0
    m5_only: bool = True
    integrated_profile_enabled: bool = False
    formal_output_enabled: bool = False
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
