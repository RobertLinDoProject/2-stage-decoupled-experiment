from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from two_stage.application.dto.stage2 import ArtifactRecord, StageStatusRecord
from two_stage.domain.enums import RunPurpose

PipelineProfileId = Literal["two_stage_integrated_v1"]
ProfileStatus = Literal["gated", "enabled"]
GaiAdapterMode = Literal["unavailable", "mock", "http"]


def integrated_required_stages() -> list[str]:
    return ["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"]


def gai_adapter_modes() -> list[GaiAdapterMode]:
    return ["unavailable", "mock", "http"]


def formal_gai_adapter_modes() -> list[Literal["http"]]:
    return ["http"]


def integrated_blocking_reasons() -> list[str]:
    return []


class GaiDecisionAdapterContract(BaseModel):
    contract_id: str = "gai_decision_adapter_contract_v1"
    contract_version: str = "1.0.0"
    status: Literal["skeleton"] = "skeleton"
    allowed_modes: list[GaiAdapterMode] = Field(
        default_factory=gai_adapter_modes
    )
    formal_allowed_modes: list[Literal["http"]] = Field(
        default_factory=formal_gai_adapter_modes
    )
    live_provider_required_for_formal: bool = True
    required_request_fields: list[str] = Field(
        default_factory=lambda: [
            "experiment_id",
            "run_id",
            "request_id",
            "trial_id",
            "scenario_id",
            "error_realization_id",
            "observed_population",
            "topology",
            "capacities",
            "allowed_action_schema",
            "decision_policy_version",
            "input_checksum",
        ]
    )
    forbidden_request_fields: list[str] = Field(
        default_factory=lambda: ["ground_truth_population", "scenario_gt", "d_star"]
    )
    required_gai_metadata: list[str] = Field(
        default_factory=lambda: [
            "provider",
            "model",
            "model_version",
            "prompt_template_version",
            "temperature",
            "seed",
            "request_checksum",
            "raw_response",
            "parsed_response",
            "parse_repair_flag",
            "retry_count",
            "latency",
            "token_usage",
            "timeout_or_error",
        ]
    )
    invalid_output_rule_violation_separation: bool = True
    retry_must_reuse_request_checksum: bool = True


class TwoStageIntegratedPipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "two_stage_integrated_v1"
    run_id: str | None = None
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY
    profile_status: ProfileStatus = "enabled"
    execution_enabled: bool = True
    formal_output_enabled: bool = False
    exploratory_output_enabled: bool = True
    live_gai_provider_enabled: bool = False
    mock_gai_allowed_for_formal: bool = False
    m5_policy_bundle_id: str | None = None
    m5_policy_bundle_checksum: str | None = None
    gai_mode: GaiAdapterMode = "unavailable"
    include_rule: bool = True
    include_mock_gai: bool = False
    perception_package_path: str = (
        "03_資料與實驗輸入/A_perception_benchmark/fcu114_multimodel_v1"
    )
    topology_profile_id: str = "project_fcu_map_json_v0_1"
    topology_package_path: str = (
        "03_資料與實驗輸入/B_campus_topology/fcu_campus_m0_v1"
    )
    topology_source_id: str | None = None
    root_seed: int = 114
    scenario_count: int = Field(default=3, ge=1)
    total_population: int = Field(default=5000, ge=1)
    high_population_ratio: float = Field(default=0.65, ge=0.0, le=1.0)
    high_population_node_ids: list[str] = Field(default_factory=list)
    trial_count: int = Field(default=300, ge=1, le=1000)
    minimum_pool_size: int = Field(default=30, ge=1)
    residual_model_id: str | None = None
    residual_paradigm: Literal["detection", "density"] | None = None
    residual_split: str = "test"
    metric_definition_version: str = "metrics_spec_v0_4_deployment_valid_rate"
    required_stages: list[str] = Field(default_factory=integrated_required_stages)
    blocked_stages_until_approval: list[str] = Field(
        default_factory=list
    )
    blocking_reasons: list[str] = Field(default_factory=integrated_blocking_reasons)
    gai_adapter_contract: GaiDecisionAdapterContract = Field(
        default_factory=GaiDecisionAdapterContract
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TwoStageIntegratedReadinessSummary(BaseModel):
    pipeline_profile: PipelineProfileId = "two_stage_integrated_v1"
    profile_status: ProfileStatus = "enabled"
    execution_enabled: bool = True
    formal_output_enabled: bool = False
    required_stages: list[str] = Field(default_factory=integrated_required_stages)
    blocking_reasons: list[str] = Field(default_factory=integrated_blocking_reasons)
    gai_adapter_contract: GaiDecisionAdapterContract = Field(
        default_factory=GaiDecisionAdapterContract
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TwoStageIntegratedRunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId = "two_stage_integrated_v1"
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    child_run_ids: dict[str, str]
    r_ideal_available: bool
    r_deploy_available: bool
    delta_r_available: bool
    metric_count: int
    formal_output_enabled: bool = False
    integrated_profile_enabled: bool = True
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
