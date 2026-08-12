from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from two_stage.domain.enums import PolicyStatus, RunPurpose

PipelineProfileId = Literal["stage2_topology_ideal_v1"]
TrialType = Literal["ideal"]
InputMode = Literal["ground_truth"]
DecisionStatus = Literal["parsed", "invalid_output"]
InterfaceType = Literal["rule", "gai"]
Regime = Literal["low", "medium", "high"]
StageStatusValue = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "stale",
    "not_required",
]


class Stage2PipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "stage2_topology_ideal_v1"
    run_id: str
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY
    topology_profile_id: str = "project_fcu_map_json_v0_1"
    topology_package_path: str = (
        "03_資料與實驗輸入/B_campus_topology/fcu_campus_m0_v1"
    )
    topology_source_id: str | None = None
    topology_runtime_input: str = "topology_spec.json"
    root_seed: int = 114
    scenario_count: int = 3
    total_population: int = 5000
    high_population_ratio: float = Field(default=0.65, ge=0.0, le=1.0)
    high_population_node_ids: list[str] = Field(default_factory=list)
    scenario_policy_id: str = "scenario_generator_stage2_topology_ideal_v1"
    scenario_policy_version: str = "0.1.0"
    scenario_policy_status: PolicyStatus = PolicyStatus.EXPERIMENTAL
    decision_policy_id: str = "rule_capacity_relief_v1"
    decision_policy_version: str = "0.1.0"
    validator_policy_id: str = "validator_stage2_capacity_topology_v1"
    validator_policy_version: str = "0.1.0"
    metric_definition_version: str = "metrics_spec_v0_3_action_and_risk_consistency"
    include_mock_gai: bool = True
    max_targets_per_source: int = Field(default=1, ge=1)
    high_source_occupancy_threshold: float = Field(default=0.80, gt=0.0, le=1.0)
    target_occupancy_limit: float = Field(default=0.90, gt=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TopologyNode(BaseModel):
    node_id: str
    node_name: str
    node_type: str
    capacity: int = Field(ge=0)
    is_exit: bool = False
    is_sink: bool = False
    enabled: bool = True
    cluster_id: str | None = None
    x: float | None = None
    y: float | None = None


class TopologyEdge(BaseModel):
    edge_id: str
    source: str
    target: str
    directed: bool = True
    enabled: bool = True
    hops: int = Field(default=1, ge=1)
    travel_cost: float = Field(default=1.0, ge=0.0)
    edge_capacity: int | None = Field(default=None, ge=0)
    edge_type: str | None = None


class TopologySpec(BaseModel):
    schema_version: str
    topology_id: str
    topology_version: str
    coordinate_system: str = "unspecified_project_map"
    origin: str | None = None
    x_unit: str | None = None
    y_unit: str | None = None
    background_image_ref: str | None = None
    background_width: int | None = None
    background_height: int | None = None
    map_version: str | None = None
    graph_directionality: str | None = None
    adjacency_semantics: str | None = None
    edge_cost_directionality: str | None = None
    rules: dict[str, object] = Field(default_factory=dict)
    nodes: list[TopologyNode]
    edges: list[TopologyEdge]


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class TopologyValidationReport(BaseModel):
    profile_id: str
    topology_id: str
    topology_version: str
    valid: bool
    node_count: int
    edge_count: int
    capacity_total: int
    source_checksums: dict[str, str]
    issues: list[ValidationIssue] = Field(default_factory=list)


class PopulationEntry(BaseModel):
    node_id: str
    ground_truth_population: int = Field(ge=0)
    capacity: int = Field(ge=0)
    occupancy_ratio: float = Field(ge=0.0)
    regime: Regime


class ScenarioRecord(BaseModel):
    scenario_id: str
    scenario_seed: int
    total_population: int
    high_population_node_ids: list[str]
    zone_counts: list[PopulationEntry]


class ScenarioManifest(BaseModel):
    artifact_role: Literal["scenario_gt"] = "scenario_gt"
    trial_type: TrialType = "ideal"
    scenario_policy_id: str
    scenario_policy_version: str
    scenario_policy_status: PolicyStatus
    root_seed: int
    scenario_count: int
    total_population: int
    scenario_gt_checksum: str
    topology_checksum: str
    invariants: dict[str, bool]


class ObservationManifest(BaseModel):
    artifact_role: Literal["ideal_observation"] = "ideal_observation"
    observed_population_source_stage_id: Literal["M4"] = "M4"
    observed_population: Literal["scenario_gt"] = "scenario_gt"
    trial_type: TrialType = "ideal"
    input_mode: InputMode = "ground_truth"
    error_realization_id: None = None
    observation_checksum: str
    scenario_gt_checksum: str
    note: str = "Ideal observation reuses M4 scenario_gt bytes; no error injection is performed."


class DecisionAction(BaseModel):
    action_id: str
    from_node: str
    to_node: str
    count: int = Field(gt=0)


class DecisionResult(BaseModel):
    decision_id: str
    interface_type: InterfaceType
    scenario_id: str
    error_realization_id: None = None
    trial_type: TrialType = "ideal"
    input_mode: InputMode = "ground_truth"
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


class ValidationResult(BaseModel):
    validation_id: str
    decision_id: str
    interface_type: InterfaceType
    scenario_id: str
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


class MetricResult(BaseModel):
    metric_id: str
    metric_version: str
    availability: Literal["available", "unavailable", "not_applicable"]
    value: float | None = None
    numerator: float | None = None
    denominator: float | None = None
    formula_inputs: dict[str, float | int | bool | str] = Field(default_factory=dict)
    aggregation: str | None = None
    n_trials: int | None = None
    filters: dict[str, str] = Field(default_factory=dict)
    definition_ref: str
    unavailable_reason: str | None = None


class ArtifactRecord(BaseModel):
    artifact_id: str
    stage_id: str
    file_name: str
    purpose: str
    uri: str
    absolute_path: str | None = None
    checksum: str
    media_type: str
    schema_name: str
    schema_version: str
    row_count: int | None = None
    byte_size: int


class StageStatusRecord(BaseModel):
    stage_id: str
    status: StageStatusValue
    reason: str | None = None
    active_artifacts: list[str] = Field(default_factory=list)


class Stage2RunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    m4_scenario_gt_checksum: str
    m6_observation_checksum: str
    m7_ground_truth_source_stage_id: Literal["M4"] = "M4"
    perception_required: bool = False
    perception_status: Literal["not_required"] = "not_required"
    stage2_only: bool = True
    limitations: list[str]
    artifacts: list[ArtifactRecord]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
