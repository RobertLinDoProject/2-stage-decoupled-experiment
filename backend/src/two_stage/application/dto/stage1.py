from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from two_stage.application.dto.stage2 import ArtifactRecord, StageStatusRecord
from two_stage.domain.enums import RunPurpose

PipelineProfileId = Literal["stage1_perception_benchmark_v1"]
PerceptionParadigm = Literal["detection", "density"]
SplitName = Literal["train", "val", "test"]
Stage1MetricAvailability = Literal["available", "not_applicable", "unavailable"]


class Stage1PipelineConfig(BaseModel):
    pipeline_profile: PipelineProfileId = "stage1_perception_benchmark_v1"
    run_id: str
    run_purpose: RunPurpose = RunPurpose.EXPLORATORY
    perception_package_id: str = "fcu114_multimodel_v1"
    perception_package_path: str = (
        "03_資料與實驗輸入/A_perception_benchmark/fcu114_multimodel_v1"
    )
    dataset_id: str = "fcu114_perception_benchmark"
    dataset_version: str = "v1"
    evaluation_split: SplitName = "test"
    detection_profile_id: str = "project_count_level_detection_csv_v0_1"
    density_profile_id: str = "project_count_level_density_csv_v0_1"
    count_level_bridge_profile_id: str = "project_count_level_csv_v0_1"
    formal_a_prefixed_profile_id: str = "fcu114_multimodel_a_prefixed_csv_v0_1"
    importer_version: str = "perception_count_level_importer_v1.0.0"
    predicted_regime_policy: Literal["raw_input_only"] = "raw_input_only"
    residual_policy_id: str = "m2_empirical_residual_distribution_v1"
    residual_policy_version: str = "0.1.0"
    metric_definition_version: str = "metrics_spec_v0_3_perception_subset"
    outlier_policy: Literal["none"] = "none"
    require_detection_val: bool = False
    require_density_val: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SourceFileInventory(BaseModel):
    file_name: str
    relative_path: str
    present: bool
    checksum: str | None = None
    byte_size: int | None = None
    row_count: int | None = None
    encoding: str | None = None
    delimiter: str | None = None
    columns: list[str] = Field(default_factory=list)


class FieldMappingProposal(BaseModel):
    source_file: str
    source_field: str
    canonical_field: str
    confidence: Literal["explicit_profile", "needs_confirmation"]


class PerceptionInputInventory(BaseModel):
    package_id: str
    package_path: str
    source_present: bool
    required_files_present: bool
    files: list[SourceFileInventory]
    package_checksum: str | None = None
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PerceptionProfileDetection(BaseModel):
    detected_profile_id: str | None
    compatible_profiles: list[str] = Field(default_factory=list)
    paradigms_present: list[PerceptionParadigm] = Field(default_factory=list)
    model_count: int = 0
    split_names: list[str] = Field(default_factory=list)
    requires_field_mapping_confirmation: bool = False
    field_mapping: list[FieldMappingProposal] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PerceptionDataQualityReport(BaseModel):
    valid: bool
    sample_count: int = 0
    prediction_count: int = 0
    canonical_row_count: int = 0
    excluded_row_count: int = 0
    issues: list[dict[str, object]] = Field(default_factory=list)
    split_counts: dict[str, int] = Field(default_factory=dict)
    model_counts: dict[str, int] = Field(default_factory=dict)
    paradigm_counts: dict[str, int] = Field(default_factory=dict)


class PerceptionInputAudit(BaseModel):
    inventory: PerceptionInputInventory
    profile_detection: PerceptionProfileDetection
    quality_report: PerceptionDataQualityReport
    raw_data_modified: bool = False


class PerceptionResultRow(BaseModel):
    sample_id: str
    dataset_id: str
    split: str
    paradigm: PerceptionParadigm
    model_id: str
    model_version: str
    ground_truth_count: float
    predicted_count: float
    error: float
    absolute_error: float
    ground_truth_regime: str
    predicted_regime: str
    source_ref: str


class ErrorSampleRow(BaseModel):
    pool_id: str
    sample_id: str
    dataset_id: str
    split: str
    paradigm: PerceptionParadigm
    model_id: str
    ground_truth_regime: str
    error: float
    absolute_error: float
    weight: float = 1.0


class RegimeStatisticRow(BaseModel):
    pool_id: str
    dataset_id: str
    split: str
    paradigm: PerceptionParadigm
    model_id: str
    ground_truth_regime: str
    sample_count: int
    mean_error: float
    mean_absolute_error: float
    std_error: float
    min_error: float
    max_error: float
    p50_error: float
    p90_absolute_error: float
    p95_absolute_error: float
    max_absolute_error: float


class Stage1RunSummary(BaseModel):
    run_id: str
    pipeline_profile: PipelineProfileId
    run_purpose: RunPurpose
    status: Literal["succeeded", "failed"]
    stage_statuses: list[StageStatusRecord]
    perception_required: bool = True
    topology_required: bool = False
    m1_perception_results_checksum: str | None = None
    m1_canonical_row_count: int = 0
    m2_error_samples_checksum: str | None = None
    m2_error_sample_count: int = 0
    stage1_only: bool = True
    limitations: list[str] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
