from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from two_stage.application.dto.stage2 import ArtifactRecord, StageStatusRecord
from two_stage.domain.enums import RunPurpose

PipelineProfileId = Literal["m8_m9_integrated_report_exploratory_v1"]


class IntegratedReportPipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "m8_m9_integrated_report_exploratory_v1"
    run_id: str
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY

    stage2_run_id: str | None = None
    decision_validation_run_id: str | None = None
    stage2_metrics_artifact_id: str | None = None
    stage2_metrics_uri: str
    stage2_metrics_checksum: str
    m7_validation_results_artifact_id: str | None = None
    m7_validation_results_uri: str
    m7_validation_results_checksum: str
    m7_validation_summary_artifact_id: str | None = None
    m7_validation_summary_uri: str
    m7_validation_summary_checksum: str
    m6_decision_adapter_status_artifact_id: str | None = None
    m6_decision_adapter_status_uri: str
    m6_decision_adapter_status_checksum: str
    m5_observation_artifact_id: str | None = None
    m5_observation_uri: str | None = None
    m5_observation_checksum: str | None = None
    m4_scenario_gt_artifact_id: str | None = None
    m4_scenario_gt_uri: str | None = None
    m4_scenario_gt_checksum: str | None = None

    metric_definition_version: str = "metrics_spec_v0_3_action_and_risk_consistency"
    exploratory_output_enabled: bool = True
    formal_output_enabled: bool = False
    integrated_profile_enabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntegratedReportRunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    r_ideal_available: bool
    r_deploy_available: bool
    delta_r_available: bool
    metric_count: int
    integrated_report_only: bool = True
    formal_output_enabled: bool = False
    integrated_profile_enabled: bool = False
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
