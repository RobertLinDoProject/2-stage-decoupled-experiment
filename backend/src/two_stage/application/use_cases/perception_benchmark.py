from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, NamedTuple, cast

from two_stage.application.dto.stage1 import (
    ErrorSampleRow,
    FieldMappingProposal,
    PerceptionDataQualityReport,
    PerceptionInputAudit,
    PerceptionInputInventory,
    PerceptionParadigm,
    PerceptionProfileDetection,
    PerceptionResultRow,
    RegimeStatisticRow,
    SourceFileInventory,
    Stage1PipelineConfig,
    Stage1RunSummary,
)
from two_stage.application.dto.stage2 import ArtifactRecord, MetricResult, StageStatusRecord
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.errors import DomainValidationError
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore

JsonObject = dict[str, object]

LEGACY_COUNT_LEVEL_PROFILE_ID = "project_count_level_csv_v0_1"
FORMAL_A_PREFIXED_PROFILE_ID = "fcu114_multimodel_a_prefixed_csv_v0_1"


class PerceptionSourceFiles(NamedTuple):
    profile_id: str
    samples: str
    models: str
    predictions: str


SOURCE_FILES_BY_PROFILE = {
    LEGACY_COUNT_LEVEL_PROFILE_ID: PerceptionSourceFiles(
        profile_id=LEGACY_COUNT_LEVEL_PROFILE_ID,
        samples="benchmark_samples.csv",
        models="perception_model_registry.csv",
        predictions="model_predictions_raw.csv",
    ),
    FORMAL_A_PREFIXED_PROFILE_ID: PerceptionSourceFiles(
        profile_id=FORMAL_A_PREFIXED_PROFILE_ID,
        samples="A1_benchmark_samples_combined.csv",
        models="A2_perception_model_registry.csv",
        predictions="A3_model_predictions_raw.csv",
    ),
}
OPTIONAL_FILES = ("split_manifest.csv",)
NOT_REQUIRED_STAGES = ("M3", "M4", "M5", "M6", "M7")

SAMPLE_FIELD_MAPPING = {
    "sample_id": "sample_id",
    "image_id": "source_ref",
    "dataset_split": "split",
    "benchmark_gt": "ground_truth_count",
    "perception_regime": "ground_truth_regime",
    "regime_rule_version": "regime_rule_version",
}
MODEL_FIELD_MAPPING = {
    "perception_model_id": "model_id",
    "perception_model_family": "paradigm",
    "perception_model_version": "model_version",
    "paper_result_eligible": "paper_result_eligible",
}
PREDICTION_FIELD_MAPPING = {
    "sample_id": "sample_id",
    "perception_model_id": "model_id",
    "benchmark_pred": "predicted_count",
    "predicted_regime": "predicted_regime",
    "inference_time_ms": "inference_time_ms",
}
A_SAMPLE_FIELD_MAPPING = {
    "sample_id": "sample_id",
    "dataset_id": "dataset_id",
    "perception_paradigm": "paradigm",
    "image_id": "source_ref",
    "dataset_split": "split",
    "scene_gt_count": "ground_truth_count",
    "task_annotation_count": "annotation_count_metadata",
    "gt_definition": "ground_truth_definition",
    "annotation_completeness": "annotation_completeness",
    "perception_regime": "ground_truth_regime",
    "count_error_eligible": "count_error_eligible",
    "paper_result_eligible": "sample_paper_result_eligible",
}
A_MODEL_FIELD_MAPPING = {
    "perception_model_id": "model_id",
    "perception_model_name": "model_name",
    "perception_model_family": "paradigm",
    "perception_model_version": "model_version",
    "checkpoint_or_weight_id": "checkpoint_or_weight_id",
    "compatible_dataset_id": "dataset_id",
    "count_output_definition": "count_output_definition",
    "model_selection_split": "model_selection_split",
    "inference_config_id": "inference_config_id",
    "paper_result_eligible": "model_paper_result_eligible",
}
A_PREDICTION_FIELD_MAPPING = {
    "sample_id": "sample_id",
    "dataset_id": "dataset_id",
    "perception_model_id": "model_id",
    "benchmark_pred": "predicted_count",
    "inference_time_ms": "inference_time_ms",
    "prediction_status": "prediction_status",
}


def _resolve_source_files(
    package_root: Path,
    config: Stage1PipelineConfig,
) -> PerceptionSourceFiles | None:
    for profile_id in (
        config.formal_a_prefixed_profile_id,
        config.count_level_bridge_profile_id,
    ):
        source_files = SOURCE_FILES_BY_PROFILE.get(profile_id)
        if source_files is None:
            continue
        if all(
            (package_root / file_name).exists()
            for file_name in (
                source_files.samples,
                source_files.models,
                source_files.predictions,
            )
        ):
            return source_files
    return None


def _all_required_file_names() -> list[str]:
    names = {
        file_name
        for source_files in SOURCE_FILES_BY_PROFILE.values()
        for file_name in (
            source_files.samples,
            source_files.models,
            source_files.predictions,
        )
    }
    return sorted(names)


def _field_mappings_for_profile(
    profile_id: str,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    if profile_id == FORMAL_A_PREFIXED_PROFILE_ID:
        return A_SAMPLE_FIELD_MAPPING, A_MODEL_FIELD_MAPPING, A_PREDICTION_FIELD_MAPPING
    return SAMPLE_FIELD_MAPPING, MODEL_FIELD_MAPPING, PREDICTION_FIELD_MAPPING


class PerceptionInputAuditor:
    def audit(
        self,
        *,
        source_root: str | Path,
        config: Stage1PipelineConfig,
    ) -> PerceptionInputAudit:
        package_root = Path(source_root) / config.perception_package_path
        inventory = self._inventory(package_root, config)
        if not inventory.required_files_present:
            quality = PerceptionDataQualityReport(
                valid=False,
                issues=[
                    {
                        "code": "PERCEPTION_SOURCE_MISSING",
                        "severity": "error",
                        "message": "Required perception benchmark files are not all present.",
                    }
                ],
            )
            profile = PerceptionProfileDetection(
                detected_profile_id=None,
                requires_field_mapping_confirmation=True,
                notes=["No compatible formal perception input package was found."],
            )
            return PerceptionInputAudit(
                inventory=inventory,
                profile_detection=profile,
                quality_report=quality,
            )

        source_files = _resolve_source_files(package_root, config)
        if source_files is None:
            raise DomainValidationError(
                "Perception inventory marked required files present but no source "
                "layout resolved."
            )
        samples = _read_csv_dicts(package_root / source_files.samples)
        models = _read_csv_dicts(package_root / source_files.models)
        predictions = _read_csv_dicts(package_root / source_files.predictions)
        sample_mapping, model_mapping, prediction_mapping = _field_mappings_for_profile(
            source_files.profile_id
        )
        field_mapping = [
            *self._mapping_for(source_files.samples, sample_mapping),
            *self._mapping_for(source_files.models, model_mapping),
            *self._mapping_for(source_files.predictions, prediction_mapping),
        ]
        quality = _evaluate_raw_quality(
            samples=samples,
            models=models,
            predictions=predictions,
            source_files=source_files,
        )
        profile = _detect_profile(
            config=config,
            samples=samples,
            models=models,
            quality=quality,
            field_mapping=field_mapping,
            source_files=source_files,
        )
        return PerceptionInputAudit(
            inventory=inventory,
            profile_detection=profile,
            quality_report=quality,
        )

    def _inventory(
        self,
        package_root: Path,
        config: Stage1PipelineConfig,
    ) -> PerceptionInputInventory:
        source_files = _resolve_source_files(package_root, config)
        required_names = (
            [source_files.samples, source_files.models, source_files.predictions]
            if source_files is not None
            else _all_required_file_names()
        )
        files: list[SourceFileInventory] = []
        for file_name in (*required_names, *OPTIONAL_FILES):
            path = package_root / file_name
            files.append(_inventory_file(package_root, path, file_name=file_name))
        required_present = source_files is not None
        package_checksum = _package_checksum(files) if package_root.exists() else None
        return PerceptionInputInventory(
            package_id=config.perception_package_id,
            package_path=config.perception_package_path,
            source_present=package_root.exists(),
            required_files_present=required_present,
            files=files,
            package_checksum=package_checksum,
        )

    @staticmethod
    def _mapping_for(
        source_file: str,
        mapping: dict[str, str],
    ) -> list[FieldMappingProposal]:
        return [
            FieldMappingProposal(
                source_file=source_file,
                source_field=source_field,
                canonical_field=canonical_field,
                confidence="explicit_profile",
            )
            for source_field, canonical_field in mapping.items()
        ]


class Stage1PerceptionBenchmarkRunner:
    """Executes Stage I perception benchmark without topology, M5, or integrated run."""

    def __init__(
        self,
        *,
        source_root: str | Path,
        artifact_root: str | Path,
        config: Stage1PipelineConfig,
    ) -> None:
        self.source_root = Path(source_root)
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.config = config
        self.artifacts: list[ArtifactRecord] = []

    def run(self) -> Stage1RunSummary:
        auditor = PerceptionInputAuditor()
        audit = auditor.audit(source_root=self.source_root, config=self.config)

        self._publish_json(
            stage_id="M0",
            file_name="pipeline_profile.json",
            purpose="Stage I perception benchmark pipeline profile.",
            payload=self.config.model_dump(mode="json"),
            schema_name="Stage1PipelineConfig",
        )
        self._publish_bytes(
            stage_id="M0",
            file_name="perception_input_audit.md",
            purpose="Read-only formal perception input audit.",
            content=_render_audit_markdown(audit).encode("utf-8"),
            media_type="text/markdown",
            schema_name="PerceptionInputAuditMarkdown",
        )
        self._publish_json(
            stage_id="M0",
            file_name="perception_input_inventory.json",
            purpose="Formal perception input file inventory and checksums.",
            payload=audit.inventory.model_dump(mode="json"),
            schema_name="PerceptionInputInventory",
            row_count=len(audit.inventory.files),
        )
        self._publish_json(
            stage_id="M0",
            file_name="perception_profile_detection.json",
            purpose="Perception input profile detection and field mapping.",
            payload=audit.profile_detection.model_dump(mode="json"),
            schema_name="PerceptionProfileDetection",
            row_count=len(audit.profile_detection.compatible_profiles),
        )
        self._publish_json(
            stage_id="M0",
            file_name="perception_data_quality_report.json",
            purpose="Perception raw input quality report.",
            payload=audit.quality_report.model_dump(mode="json"),
            schema_name="PerceptionDataQualityReport",
            row_count=len(audit.quality_report.issues),
        )
        if not audit.quality_report.valid:
            raise DomainValidationError("Perception input audit failed; M1 cannot run.")

        package_root = self.source_root / self.config.perception_package_path
        split_manifest_rows = _materialize_split_manifest_rows(package_root, self.config)
        split_manifest_artifact = self._publish_bytes(
            stage_id="M0",
            file_name="split_manifest.csv",
            purpose="Canonical split manifest materialized before M1.",
            content=_csv_bytes(split_manifest_rows),
            media_type="text/csv",
            schema_name="PerceptionSplitManifest",
            row_count=max(0, len(split_manifest_rows) - 1),
        )

        perception_rows, excluded_rows = self._build_perception_results(package_root)
        parquet_bytes = _parquet_bytes([row.model_dump(mode="json") for row in perception_rows])
        perception_artifact = self._publish_bytes(
            stage_id="M1",
            file_name="perception_results.parquet",
            purpose="Canonical perception benchmark results; only upstream source for M2.",
            content=parquet_bytes,
            media_type="application/vnd.apache.parquet",
            schema_name="PerceptionResults",
            row_count=len(perception_rows),
        )
        self._publish_bytes(
            stage_id="M1",
            file_name="perception_results.csv",
            purpose="Human-readable export of M1 canonical perception results.",
            content=_csv_bytes(_perception_csv_rows(perception_rows)),
            media_type="text/csv",
            schema_name="PerceptionResultsCsvExport",
            row_count=len(perception_rows),
        )
        self._publish_json(
            stage_id="M1",
            file_name="perception_manifest.json",
            purpose="M1 canonical materialization manifest.",
            payload={
                "canonical_artifact_uri": perception_artifact.uri,
                "canonical_checksum": perception_artifact.checksum,
                "canonical_row_count": len(perception_rows),
                "input_inventory_checksum": audit.inventory.package_checksum,
                "split_manifest_artifact_uri": split_manifest_artifact.uri,
                "importer_version": self.config.importer_version,
                "field_mapping": [
                    item.model_dump(mode="json") for item in audit.profile_detection.field_mapping
                ],
            },
            schema_name="PerceptionManifest",
        )
        self._publish_bytes(
            stage_id="M1",
            file_name="m1_quality_report.md",
            purpose="M1 canonical quality report.",
            content=_render_m1_quality_report(
                audit=audit,
                rows=perception_rows,
                excluded_rows=excluded_rows,
            ).encode("utf-8"),
            media_type="text/markdown",
            schema_name="M1QualityReport",
        )
        self._publish_bytes(
            stage_id="M1",
            file_name="excluded_samples.jsonl",
            purpose="Rows excluded from canonical perception results.",
            content=_jsonl_bytes(excluded_rows),
            media_type="application/x-ndjson",
            schema_name="ExcludedPerceptionSamples",
            row_count=len(excluded_rows),
        )

        if perception_artifact.absolute_path is None:
            raise DomainValidationError("M1 canonical artifact path is unavailable.")
        m2_input_rows = _read_perception_parquet(perception_artifact.absolute_path)
        error_samples, regime_stats = self._build_error_distribution(m2_input_rows)
        error_samples_artifact = self._publish_bytes(
            stage_id="M2",
            file_name="error_samples.parquet",
            purpose="Full empirical residual samples grouped by paradigm/model/split/regime.",
            content=_parquet_bytes([row.model_dump(mode="json") for row in error_samples]),
            media_type="application/vnd.apache.parquet",
            schema_name="EmpiricalResidualSamples",
            row_count=len(error_samples),
        )
        self._publish_json(
            stage_id="M2",
            file_name="error_distribution_summary.json",
            purpose="M2 empirical residual distribution summary.",
            payload=_error_distribution_summary(error_samples, regime_stats, self.config),
            schema_name="ErrorDistributionSummary",
            row_count=len(regime_stats),
        )
        self._publish_bytes(
            stage_id="M2",
            file_name="regime_statistics.parquet",
            purpose="Per-regime residual statistics without clipping.",
            content=_parquet_bytes([row.model_dump(mode="json") for row in regime_stats]),
            media_type="application/vnd.apache.parquet",
            schema_name="RegimeStatistics",
            row_count=len(regime_stats),
        )
        self._publish_json(
            stage_id="M2",
            file_name="m2_error_model.json",
            purpose="M2 empirical residual model metadata.",
            payload=_m2_error_model(error_samples_artifact, regime_stats, self.config),
            schema_name="M2ErrorModel",
            row_count=len(regime_stats),
        )
        self._publish_bytes(
            stage_id="M2",
            file_name="m2_quality_report.md",
            purpose="M2 quality report and residual pool coverage.",
            content=_render_m2_quality_report(error_samples, regime_stats).encode("utf-8"),
            media_type="text/markdown",
            schema_name="M2QualityReport",
        )

        metrics = _calculate_stage1_metrics(m2_input_rows, self.config)
        self._publish_json(
            stage_id="M8",
            file_name="metrics.json",
            purpose="Perception-only M8 metrics; decision/deployment metrics not applicable.",
            payload={"metrics": [metric.model_dump(mode="json") for metric in metrics]},
            schema_name="Stage1PerceptionMetrics",
            row_count=len(metrics),
        )
        report = _render_stage1_report(
            config=self.config,
            audit=audit,
            perception_artifact=perception_artifact,
            error_samples_artifact=error_samples_artifact,
            metrics=metrics,
        )
        self._publish_bytes(
            stage_id="M9",
            file_name="report.md",
            purpose="Frozen Stage I-only perception benchmark report.",
            content=report.encode("utf-8"),
            media_type="text/markdown",
            schema_name="Stage1FrozenReport",
        )
        self._publish_json(
            stage_id="M9",
            file_name="reproducibility_manifest.json",
            purpose="Stage I reproducibility manifest.",
            payload={
                "run_id": self.config.run_id,
                "pipeline_profile": self.config.pipeline_profile,
                "perception_package_id": self.config.perception_package_id,
                "detected_input_profile_id": audit.profile_detection.detected_profile_id,
                "predicted_regime_policy": self.config.predicted_regime_policy,
                "source_package_checksum": audit.inventory.package_checksum,
                "m1_perception_results_checksum": perception_artifact.checksum,
                "m2_error_samples_checksum": error_samples_artifact.checksum,
                "outlier_policy": self.config.outlier_policy,
                "m3_m4_m5_m6_m7": "not_required",
                "integrated_reliability": "not_applicable",
            },
            schema_name="Stage1ReproducibilityManifest",
        )
        self._publish_bytes(
            stage_id="M9",
            file_name="delivery_manifest.csv",
            purpose="Delivery manifest listing published Stage I artifacts.",
            content=self._delivery_manifest_csv().encode("utf-8"),
            media_type="text/csv",
            schema_name="DeliveryManifest",
            row_count=len(self.artifacts),
        )

        summary = Stage1RunSummary(
            run_id=self.config.run_id,
            pipeline_profile=self.config.pipeline_profile,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=self._stage_statuses(),
            m1_perception_results_checksum=perception_artifact.checksum,
            m1_canonical_row_count=len(perception_rows),
            m2_error_samples_checksum=error_samples_artifact.checksum,
            m2_error_sample_count=len(error_samples),
            limitations=[
                "Stage I-only perception benchmark; topology is not required.",
                "M3, M4, M5, M6 and M7 are NOT_REQUIRED for this profile.",
                "No perception error injection is executed.",
                "Predicted regime is raw-input-only; no predicted regime derivation is executed.",
                "No topology decision validation or integrated reliability is claimed.",
                "Decision metrics, R_ideal, R_deploy and Delta_R are NOT_APPLICABLE.",
            ],
            artifacts=self.artifacts.copy(),
            generated_at=self.config.created_at,
        )
        summary_artifact = self._publish_json(
            stage_id="M9",
            file_name="run_summary.json",
            purpose="Run status summary with Stage I conditional DAG statuses.",
            payload=summary.model_dump(mode="json"),
            schema_name="Stage1RunSummary",
        )
        summary.artifacts.append(summary_artifact)
        return summary

    def _build_perception_results(
        self,
        package_root: Path,
    ) -> tuple[list[PerceptionResultRow], list[JsonObject]]:
        source_files = _resolve_source_files(package_root, self.config)
        if source_files is None:
            raise DomainValidationError("No compatible perception source layout resolved.")
        samples = {
            _sample_key(row, source_files.profile_id): row
            for row in _read_csv_dicts(package_root / source_files.samples)
        }
        models = {
            row["perception_model_id"]: row
            for row in _read_csv_dicts(package_root / source_files.models)
        }
        predictions = _read_csv_dicts(package_root / source_files.predictions)
        rows: list[PerceptionResultRow] = []
        excluded: list[JsonObject] = []
        for prediction in predictions:
            sample_id = str(prediction.get("sample_id", "")).strip()
            model_id = str(prediction.get("perception_model_id", "")).strip()
            sample = samples.get(_prediction_sample_key(prediction, source_files.profile_id))
            model = models.get(model_id)
            dataset_id = (
                str(prediction.get("dataset_id", "")).strip()
                if source_files.profile_id == FORMAL_A_PREFIXED_PROFILE_ID
                else self.config.dataset_id
            )
            if not _prediction_status_is_success(prediction, source_files.profile_id):
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "prediction_status_not_success",
                    }
                )
                continue
            if sample is None or model is None:
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "missing_sample_or_model_reference",
                    }
                )
                continue
            if not _sample_is_eligible(sample) or not _model_is_eligible(model):
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "not_paper_or_count_error_eligible",
                    }
                )
                continue
            if not _model_dataset_compatible(
                prediction=prediction,
                model=model,
                profile_id=source_files.profile_id,
            ):
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "model_dataset_incompatible",
                    }
                )
                continue
            paradigm = _normalize_paradigm(model.get("perception_model_family"))
            if paradigm is None:
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "unknown_model_family_requires_mapping",
                    }
                )
                continue
            sample_paradigm = _normalize_paradigm(sample.get("perception_paradigm"))
            if sample_paradigm is not None and sample_paradigm != paradigm:
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": "sample_model_paradigm_mismatch",
                    }
                )
                continue
            try:
                ground_truth = _float_value(sample[_ground_truth_field(source_files.profile_id)])
                predicted = _float_value(prediction["benchmark_pred"])
            except (KeyError, ValueError) as exc:
                excluded.append(
                    {
                        "sample_id": sample_id,
                        "dataset_id": dataset_id,
                        "model_id": model_id,
                        "reason": f"invalid_count_value:{exc}",
                    }
                )
                continue
            error = predicted - ground_truth
            rows.append(
                PerceptionResultRow(
                    sample_id=sample_id,
                    dataset_id=dataset_id or str(sample.get("dataset_id", self.config.dataset_id)),
                    split=str(sample.get("dataset_split", "")).strip().lower(),
                    paradigm=paradigm,
                    model_id=model_id,
                    model_version=str(model.get("perception_model_version", "")).strip(),
                    ground_truth_count=ground_truth,
                    predicted_count=predicted,
                    error=error,
                    absolute_error=abs(error),
                    ground_truth_regime=str(sample.get("perception_regime", "")).strip().lower(),
                    predicted_regime=str(prediction.get("predicted_regime", "")).strip().lower(),
                    source_ref=str(sample.get("image_id", sample_id)).strip(),
                )
            )
        rows.sort(
            key=lambda row: (
                row.dataset_id,
                row.paradigm,
                row.model_id,
                row.split,
                row.sample_id,
            )
        )
        return rows, excluded

    def _build_error_distribution(
        self,
        rows: list[PerceptionResultRow],
    ) -> tuple[list[ErrorSampleRow], list[RegimeStatisticRow]]:
        samples: list[ErrorSampleRow] = []
        grouped_errors: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
        grouped_abs: dict[tuple[str, str, str, str, str], list[float]] = defaultdict(list)
        for row in rows:
            key = (
                row.dataset_id,
                row.model_id,
                row.paradigm,
                row.split,
                row.ground_truth_regime,
            )
            pool_id = _pool_id(key)
            grouped_errors[key].append(row.error)
            grouped_abs[key].append(row.absolute_error)
            samples.append(
                ErrorSampleRow(
                    pool_id=pool_id,
                    sample_id=row.sample_id,
                    dataset_id=row.dataset_id,
                    split=row.split,
                    paradigm=row.paradigm,
                    model_id=row.model_id,
                    ground_truth_regime=row.ground_truth_regime,
                    error=row.error,
                    absolute_error=row.absolute_error,
                )
            )
        stats = [
            RegimeStatisticRow(
                pool_id=_pool_id(key),
                dataset_id=key[0],
                model_id=key[1],
                paradigm=cast(PerceptionParadigm, key[2]),
                split=key[3],
                ground_truth_regime=key[4],
                sample_count=len(values),
                mean_error=_mean(values),
                mean_absolute_error=_mean(grouped_abs[key]),
                std_error=_std(values),
                min_error=min(values),
                max_error=max(values),
                p50_error=_quantile(values, 0.50),
                p90_absolute_error=_quantile(grouped_abs[key], 0.90),
                p95_absolute_error=_quantile(grouped_abs[key], 0.95),
                max_absolute_error=max(grouped_abs[key]),
            )
            for key, values in sorted(grouped_errors.items())
        ]
        samples.sort(key=lambda row: (row.pool_id, row.sample_id))
        return samples, stats

    def _stage_statuses(self) -> list[StageStatusRecord]:
        artifacts_by_stage: dict[str, list[str]] = defaultdict(list)
        for artifact in self.artifacts:
            artifacts_by_stage[artifact.stage_id].append(artifact.uri)
        records: list[StageStatusRecord] = []
        for stage_id in ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"):
            if stage_id in NOT_REQUIRED_STAGES:
                records.append(
                    StageStatusRecord(
                        stage_id=stage_id,
                        status="not_required",
                        reason=(
                            "Topology, decision validation, and error injection are "
                            "outside Stage I."
                        ),
                    )
                )
            else:
                records.append(
                    StageStatusRecord(
                        stage_id=stage_id,
                        status="succeeded",
                        active_artifacts=artifacts_by_stage.get(stage_id, []),
                    )
                )
        return records

    def _delivery_manifest_csv(self) -> str:
        rows = [
            ["file_name", "purpose", "format", "stage", "schema_version", "row_count", "checksum"]
        ]
        for artifact in self.artifacts:
            rows.append(
                [
                    artifact.file_name,
                    artifact.purpose,
                    artifact.media_type,
                    artifact.stage_id,
                    artifact.schema_version,
                    "" if artifact.row_count is None else str(artifact.row_count),
                    artifact.checksum,
                ]
            )
        return "\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows) + "\n"

    def _publish_json(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        payload: object,
        schema_name: str,
        row_count: int | None = None,
    ) -> ArtifactRecord:
        return self._publish_bytes(
            stage_id=stage_id,
            file_name=file_name,
            purpose=purpose,
            content=_json_bytes(payload),
            media_type="application/json",
            schema_name=schema_name,
            row_count=row_count,
        )

    def _publish_bytes(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        content: bytes,
        media_type: str,
        schema_name: str,
        row_count: int | None = None,
    ) -> ArtifactRecord:
        staged = self.artifact_store.stage(
            ArtifactPayload(
                relative_path=f"runs/{self.config.run_id}/{stage_id}/{file_name}",
                content=content,
                media_type=media_type,
                artifact_type=schema_name,
                schema_name=schema_name,
                schema_version="1.0.0",
            )
        )
        self.artifact_store.verify(staged)
        published = self.artifact_store.publish(staged)
        record = ArtifactRecord(
            artifact_id=published.artifact_id,
            stage_id=stage_id,
            file_name=file_name,
            purpose=purpose,
            uri=published.uri,
            absolute_path=published.absolute_path,
            checksum=published.checksum,
            media_type=media_type,
            schema_name=schema_name,
            schema_version="1.0.0",
            row_count=row_count,
            byte_size=published.byte_size,
        )
        self.artifacts.append(record)
        return record


def _inventory_file(package_root: Path, path: Path, *, file_name: str) -> SourceFileInventory:
    if not path.exists():
        return SourceFileInventory(
            file_name=file_name,
            relative_path=file_name,
            present=False,
        )
    columns: list[str] = []
    row_count: int | None = None
    if path.suffix.lower() == ".csv":
        rows = _read_csv_dicts(path)
        row_count = len(rows)
        columns = list(rows[0].keys()) if rows else _read_csv_header(path)
    return SourceFileInventory(
        file_name=file_name,
        relative_path=str(path.relative_to(package_root)).replace("\\", "/"),
        present=True,
        checksum=_checksum_file(path),
        byte_size=path.stat().st_size,
        row_count=row_count,
        encoding="utf-8-sig" if path.suffix.lower() == ".csv" else None,
        delimiter="," if path.suffix.lower() == ".csv" else None,
        columns=columns,
    )


def _ground_truth_field(profile_id: str) -> str:
    if profile_id == FORMAL_A_PREFIXED_PROFILE_ID:
        return "scene_gt_count"
    return "benchmark_gt"


def _sample_key(row: dict[str, str], profile_id: str) -> str:
    sample_id = str(row.get("sample_id", "")).strip()
    if profile_id == FORMAL_A_PREFIXED_PROFILE_ID:
        dataset_id = str(row.get("dataset_id", "")).strip()
        return f"{sample_id}|{dataset_id}"
    return sample_id


def _prediction_sample_key(row: dict[str, str], profile_id: str) -> str:
    sample_id = str(row.get("sample_id", "")).strip()
    if profile_id == FORMAL_A_PREFIXED_PROFILE_ID:
        dataset_id = str(row.get("dataset_id", "")).strip()
        return f"{sample_id}|{dataset_id}"
    return sample_id


def _prediction_status_is_success(row: dict[str, str], profile_id: str) -> bool:
    if profile_id != FORMAL_A_PREFIXED_PROFILE_ID:
        return True
    return str(row.get("prediction_status", "")).strip().lower() == "success"


def _sample_is_eligible(row: dict[str, str]) -> bool:
    return _truthy(row.get("count_error_eligible", "true")) and _truthy(
        row.get("paper_result_eligible", "true")
    )


def _model_is_eligible(row: dict[str, str]) -> bool:
    return _truthy(row.get("paper_result_eligible", "true"))


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _model_dataset_compatible(
    *,
    prediction: dict[str, str],
    model: dict[str, str],
    profile_id: str,
) -> bool:
    if profile_id != FORMAL_A_PREFIXED_PROFILE_ID:
        return True
    return str(prediction.get("dataset_id", "")).strip() == str(
        model.get("compatible_dataset_id", "")
    ).strip()


def _evaluate_raw_quality(
    *,
    samples: list[dict[str, str]],
    models: list[dict[str, str]],
    predictions: list[dict[str, str]],
    source_files: PerceptionSourceFiles,
) -> PerceptionDataQualityReport:
    issues: list[dict[str, object]] = []
    sample_mapping, model_mapping, prediction_mapping = _field_mappings_for_profile(
        source_files.profile_id
    )
    required_sample_fields = set(sample_mapping)
    required_model_fields = set(model_mapping)
    required_prediction_fields = set(prediction_mapping)
    if source_files.profile_id == LEGACY_COUNT_LEVEL_PROFILE_ID:
        required_prediction_fields -= {"predicted_regime", "inference_time_ms"}
    for file_name, rows, required_fields in [
        (source_files.samples, samples, required_sample_fields),
        (source_files.models, models, required_model_fields),
        (source_files.predictions, predictions, required_prediction_fields),
    ]:
        columns = set(rows[0]) if rows else set()
        missing = sorted(required_fields - columns)
        if missing:
            issues.append(
                {
                    "code": "MISSING_REQUIRED_COLUMNS",
                    "severity": "error",
                    "file": file_name,
                    "columns": missing,
                }
            )
    sample_keys = [_sample_key(row, source_files.profile_id) for row in samples]
    duplicate_samples = _duplicates(sample_keys)
    if duplicate_samples:
        issues.append(
            {
                "code": "DUPLICATE_SAMPLE_KEY",
                "severity": "error",
                "sample_keys": duplicate_samples,
            }
        )
    model_ids = [row.get("perception_model_id", "") for row in models]
    duplicate_models = _duplicates(model_ids)
    if duplicate_models:
        issues.append(
            {
                "code": "DUPLICATE_MODEL_ID",
                "severity": "error",
                "model_ids": duplicate_models,
            }
        )
    sample_by_key = {
        _sample_key(row, source_files.profile_id): row
        for row in samples
    }
    sample_key_set = set(sample_keys)
    model_id_set = set(model_ids)
    unknown_samples = sorted(
        {
            _prediction_sample_key(row, source_files.profile_id)
            for row in predictions
            if _prediction_sample_key(row, source_files.profile_id) not in sample_key_set
        }
    )
    unknown_models = sorted(
        {
            row.get("perception_model_id", "")
            for row in predictions
            if row.get("perception_model_id", "") not in model_id_set
        }
    )
    if unknown_samples:
        issues.append(
            {
                "code": "PREDICTION_SAMPLE_NOT_IN_GT",
                "severity": "error",
                "sample_ids": unknown_samples[:20],
                "count": len(unknown_samples),
            }
        )
    if unknown_models:
        issues.append(
            {
                "code": "PREDICTION_MODEL_NOT_IN_REGISTRY",
                "severity": "error",
                "model_ids": unknown_models,
            }
        )
    model_by_id = {row.get("perception_model_id", ""): row for row in models}
    incompatible_refs = []
    for prediction in predictions:
        model = model_by_id.get(prediction.get("perception_model_id", ""))
        if model is None:
            continue
        if not _model_dataset_compatible(
            prediction=prediction,
            model=model,
            profile_id=source_files.profile_id,
        ):
            incompatible_refs.append(
                {
                    "sample_id": prediction.get("sample_id", ""),
                    "dataset_id": prediction.get("dataset_id", ""),
                    "model_id": prediction.get("perception_model_id", ""),
                    "compatible_dataset_id": model.get("compatible_dataset_id", ""),
                }
            )
    if incompatible_refs:
        issues.append(
            {
                "code": "PREDICTION_MODEL_DATASET_INCOMPATIBLE",
                "severity": "error",
                "count": len(incompatible_refs),
                "examples": incompatible_refs[:20],
            }
        )
    split_counts = Counter(str(row.get("dataset_split", "")).lower() for row in samples)
    model_counts = Counter(str(row.get("perception_model_id", "")) for row in predictions)
    paradigm_counts: Counter[str] = Counter()
    for model in models:
        paradigm = _normalize_paradigm(model.get("perception_model_family"))
        if paradigm is None:
            issues.append(
                {
                    "code": "MODEL_FAMILY_NEEDS_MAPPING",
                    "severity": "error",
                    "model_id": model.get("perception_model_id", ""),
                    "family": model.get("perception_model_family", ""),
                }
            )
        else:
            paradigm_counts[paradigm] += 1
    canonical_row_count = 0
    excluded_row_count = 0
    invalid_count_refs: list[dict[str, str]] = []
    for prediction in predictions:
        sample = sample_by_key.get(_prediction_sample_key(prediction, source_files.profile_id))
        model = model_by_id.get(prediction.get("perception_model_id", ""))
        if not _prediction_status_is_success(prediction, source_files.profile_id):
            excluded_row_count += 1
            continue
        if sample is None or model is None:
            excluded_row_count += 1
            continue
        if not _sample_is_eligible(sample) or not _model_is_eligible(model):
            excluded_row_count += 1
            continue
        if _normalize_paradigm(model.get("perception_model_family")) is None:
            excluded_row_count += 1
            continue
        sample_paradigm = _normalize_paradigm(sample.get("perception_paradigm"))
        model_paradigm = _normalize_paradigm(model.get("perception_model_family"))
        if sample_paradigm is not None and sample_paradigm != model_paradigm:
            excluded_row_count += 1
            continue
        if not _model_dataset_compatible(
            prediction=prediction,
            model=model,
            profile_id=source_files.profile_id,
        ):
            excluded_row_count += 1
            continue
        try:
            _float_value(sample[_ground_truth_field(source_files.profile_id)])
            _float_value(prediction["benchmark_pred"])
        except (KeyError, ValueError):
            invalid_count_refs.append(
                {
                    "sample_id": prediction.get("sample_id", ""),
                    "dataset_id": prediction.get("dataset_id", ""),
                    "model_id": prediction.get("perception_model_id", ""),
                }
            )
            excluded_row_count += 1
            continue
        canonical_row_count += 1
    if invalid_count_refs:
        issues.append(
            {
                "code": "INVALID_COUNT_VALUE",
                "severity": "error",
                "count": len(invalid_count_refs),
                "examples": invalid_count_refs[:20],
            }
        )
    return PerceptionDataQualityReport(
        valid=not any(issue["severity"] == "error" for issue in issues),
        sample_count=len(samples),
        prediction_count=len(predictions),
        canonical_row_count=canonical_row_count,
        excluded_row_count=excluded_row_count,
        issues=issues,
        split_counts=dict(sorted(split_counts.items())),
        model_counts=dict(sorted(model_counts.items())),
        paradigm_counts=dict(sorted(paradigm_counts.items())),
    )


def _detect_profile(
    *,
    config: Stage1PipelineConfig,
    samples: list[dict[str, str]],
    models: list[dict[str, str]],
    quality: PerceptionDataQualityReport,
    field_mapping: list[FieldMappingProposal],
    source_files: PerceptionSourceFiles,
) -> PerceptionProfileDetection:
    paradigms = sorted(
        {
            paradigm
            for model in models
            if (paradigm := _normalize_paradigm(model.get("perception_model_family"))) is not None
        }
    )
    compatible_profiles: list[str] = []
    if "detection" in paradigms:
        compatible_profiles.append(config.detection_profile_id)
    if "density" in paradigms:
        compatible_profiles.append(config.density_profile_id)
    compatible_profiles.append(source_files.profile_id)
    split_names = sorted({str(row.get("dataset_split", "")).lower() for row in samples})
    notes: list[str] = []
    if "detection" not in paradigms:
        notes.append("No detection model family detected.")
    if "density" not in paradigms:
        notes.append("No density model family detected.")
    if config.require_detection_val and "detection" in paradigms and "val" not in split_names:
        notes.append("Detection profile requires val split but it is missing.")
    if config.require_density_val and "density" in paradigms and "val" not in split_names:
        notes.append("Density profile requires val split but it is missing.")
    if source_files.profile_id == FORMAL_A_PREFIXED_PROFILE_ID:
        notes.append(
            "A3 has no predicted_regime column; regime_confusion metrics are unavailable."
        )
    detected = source_files.profile_id if quality.valid and compatible_profiles else None
    return PerceptionProfileDetection(
        detected_profile_id=detected,
        compatible_profiles=sorted(set(compatible_profiles)),
        paradigms_present=paradigms,
        model_count=len(models),
        split_names=split_names,
        requires_field_mapping_confirmation=not quality.valid,
        field_mapping=field_mapping,
        notes=notes,
    )


def _materialize_split_manifest_rows(
    package_root: Path,
    config: Stage1PipelineConfig,
) -> list[list[str]]:
    source = package_root / "split_manifest.csv"
    if source.exists():
        rows = _read_csv_rows(source)
        return rows
    source_files = _resolve_source_files(package_root, config)
    if source_files is None:
        raise DomainValidationError("No compatible perception source layout resolved.")
    samples = _read_csv_dicts(package_root / source_files.samples)
    models = _read_csv_dicts(package_root / source_files.models)
    paradigms = sorted(
        {
            paradigm
            for model in models
            if (paradigm := _normalize_paradigm(model.get("perception_model_family"))) is not None
        }
    )
    rows = [
        [
            "sample_id",
            "split",
            "paradigm",
            "image_ref",
            "annotation_ref",
            "prediction_ref",
            "group_id",
        ]
    ]
    for sample in sorted(samples, key=lambda row: row.get("sample_id", "")):
        sample_paradigm = _normalize_paradigm(sample.get("perception_paradigm"))
        row_paradigms = [sample_paradigm] if sample_paradigm is not None else paradigms
        for paradigm in row_paradigms:
            rows.append(
                [
                    str(sample.get("sample_id", "")),
                    str(sample.get("dataset_split", "")).lower(),
                    paradigm,
                    str(sample.get("image_id", "")),
                    "",
                    source_files.predictions,
                    str(sample.get("dataset_id", config.dataset_id)),
                ]
            )
    return rows


def _calculate_stage1_metrics(
    rows: list[PerceptionResultRow],
    config: Stage1PipelineConfig,
) -> list[MetricResult]:
    metrics: list[MetricResult] = []
    definition_ref = "docs/research_decisions/metric_definitions.md"
    groups: dict[tuple[str, str, str, str], list[PerceptionResultRow]] = defaultdict(list)
    for row in rows:
        groups[(row.dataset_id, row.model_id, row.paradigm, row.split)].append(row)
    for key, group_rows in sorted(groups.items()):
        errors = [row.error for row in group_rows]
        abs_errors = [row.absolute_error for row in group_rows]
        filters = {
            "dataset_id": key[0],
            "model_id": key[1],
            "paradigm": key[2],
            "split": key[3],
        }
        for metric_id, value in [
            ("mae", _mean(abs_errors)),
            ("mse", _mean([value * value for value in errors])),
            ("rmse", math.sqrt(_mean([value * value for value in errors]))),
            ("mean_signed_error", _mean(errors)),
            ("std_error", _std(errors)),
            ("p90_absolute_error", _quantile(abs_errors, 0.90)),
            ("max_error", max(abs_errors)),
        ]:
            metrics.append(
                MetricResult(
                    metric_id=metric_id,
                    metric_version=config.metric_definition_version,
                    availability="available",
                    value=value,
                    aggregation="perception_group",
                    n_trials=len(group_rows),
                    filters=filters,
                    definition_ref=definition_ref,
                )
            )
        if all(row.predicted_regime for row in group_rows):
            confusion = Counter(
                f"{row.ground_truth_regime}->{row.predicted_regime}" for row in group_rows
            )
            metrics.append(
                MetricResult(
                    metric_id="regime_confusion",
                    metric_version=config.metric_definition_version,
                    availability="available",
                    value=None,
                    formula_inputs=dict(sorted(confusion.items())),
                    aggregation="perception_group_counts",
                    n_trials=len(group_rows),
                    filters=filters,
                    definition_ref=definition_ref,
                )
            )
        else:
            metrics.append(
                MetricResult(
                    metric_id="regime_confusion",
                    metric_version=config.metric_definition_version,
                    availability="unavailable",
                    value=None,
                    aggregation="perception_group_counts",
                    n_trials=len(group_rows),
                    filters=filters,
                    definition_ref=definition_ref,
                    unavailable_reason=(
                        "predicted_regime is absent from raw perception input and "
                        "predicted_regime_policy is raw_input_only."
                    ),
                )
            )
        metrics.append(
            MetricResult(
                metric_id="sample_count",
                metric_version=config.metric_definition_version,
                availability="available",
                value=float(len(group_rows)),
                numerator=float(len(group_rows)),
                denominator=float(len(rows)),
                aggregation="perception_group_count",
                n_trials=len(group_rows),
                filters=filters,
                definition_ref=definition_ref,
            )
        )
    for metric_id in [
        "invalid_output_rate",
        "rule_violation_rate",
        "capacity_violation_rate",
        "topology_violation_rate",
        "action_consistency_score",
        "risk_f_beta",
        "R_ideal",
        "R_deploy",
        "Delta_R",
    ]:
        metrics.append(
            MetricResult(
                metric_id=metric_id,
                metric_version=config.metric_definition_version,
                availability="not_applicable",
                value=None,
                definition_ref=definition_ref,
                unavailable_reason=(
                    "Stage I perception benchmark does not execute topology decision validation, "
                    "controlled error injection, or integrated reliability."
                ),
            )
        )
    return metrics


def _render_audit_markdown(audit: PerceptionInputAudit) -> str:
    lines = [
        "# Perception Input Audit",
        "",
        f"- Package: `{audit.inventory.package_id}`",
        f"- Package path: `{audit.inventory.package_path}`",
        f"- Source present: `{audit.inventory.source_present}`",
        f"- Required files present: `{audit.inventory.required_files_present}`",
        f"- Raw data modified by audit: `{audit.raw_data_modified}`",
        "",
        "## Files",
        "",
        "| File | Present | Rows | Checksum |",
        "|---|---:|---:|---|",
    ]
    for file in audit.inventory.files:
        lines.append(
            f"| `{file.file_name}` | {file.present} | {file.row_count or ''} | "
            f"`{file.checksum or ''}` |"
        )
    lines.extend(
        [
            "",
            "## Profile Detection",
            "",
            f"- Detected profile: `{audit.profile_detection.detected_profile_id}`",
            "- Compatible profiles: "
            + ", ".join(f"`{profile}`" for profile in audit.profile_detection.compatible_profiles),
            "- Paradigms present: "
            + ", ".join(f"`{paradigm}`" for paradigm in audit.profile_detection.paradigms_present),
            "",
            "## Quality",
            "",
            f"- Valid: `{audit.quality_report.valid}`",
            f"- Samples: `{audit.quality_report.sample_count}`",
            f"- Predictions: `{audit.quality_report.prediction_count}`",
            f"- Issues: `{len(audit.quality_report.issues)}`",
            f"- Canonical rows expected by profile: `{audit.quality_report.canonical_row_count}`",
            f"- Excluded prediction rows: `{audit.quality_report.excluded_row_count}`",
        ]
    )
    if audit.profile_detection.notes:
        lines.extend(["", "## Notes", ""])
        for note in audit.profile_detection.notes:
            lines.append(f"- {note}")
    for issue in audit.quality_report.issues:
        lines.append(f"- `{issue.get('code')}`: {issue.get('message', issue)}")
    return "\n".join(lines) + "\n"


def _render_m1_quality_report(
    *,
    audit: PerceptionInputAudit,
    rows: list[PerceptionResultRow],
    excluded_rows: list[JsonObject],
) -> str:
    return "\n".join(
        [
            "# M1 Perception Benchmark Quality Report",
            "",
            f"- Canonical rows: `{len(rows)}`",
            f"- Excluded rows: `{len(excluded_rows)}`",
            f"- Source package checksum: `{audit.inventory.package_checksum}`",
            "- Error definition: `predicted_count - ground_truth_count`.",
            (
                "- Detection and density model families are materialized with separate "
                "paradigm values."
            ),
        ]
    ) + "\n"


def _render_m2_quality_report(
    error_samples: list[ErrorSampleRow],
    regime_stats: list[RegimeStatisticRow],
) -> str:
    paradigm_counts = Counter(row.paradigm for row in error_samples)
    return "\n".join(
        [
            "# M2 Error Distribution Quality Report",
            "",
            f"- Empirical residual samples: `{len(error_samples)}`",
            f"- Residual pools: `{len(regime_stats)}`",
            f"- Paradigm counts: `{dict(sorted(paradigm_counts.items()))}`",
            "- Outlier clipping / winsorization: `not_applied`.",
            "- M2 reads M1 `perception_results.parquet`; raw input is not read by M2.",
        ]
    ) + "\n"


def _render_stage1_report(
    *,
    config: Stage1PipelineConfig,
    audit: PerceptionInputAudit,
    perception_artifact: ArtifactRecord,
    error_samples_artifact: ArtifactRecord,
    metrics: list[MetricResult],
) -> str:
    available_metrics = [metric for metric in metrics if metric.availability == "available"]
    unavailable_metrics = [metric for metric in metrics if metric.availability == "unavailable"]
    not_applicable = [metric for metric in metrics if metric.availability == "not_applicable"]
    lines = [
        "# Stage I Perception Benchmark Report",
        "",
        f"- Run ID: `{config.run_id}`",
        f"- Pipeline profile: `{config.pipeline_profile}`",
        f"- Perception package: `{config.perception_package_id}`",
        f"- Detected input profile: `{audit.profile_detection.detected_profile_id}`",
        "- Scope: Stage I-only perception benchmark.",
        "- Topology decision validation has not been executed.",
        "- Controlled error injection has not been executed.",
        "- Integrated reliability, R_ideal, R_deploy and Delta_R are not claimed.",
        "",
        "## Canonical Artifacts",
        "",
        f"- M1 perception results: `{perception_artifact.checksum}`",
        f"- M2 empirical residual samples: `{error_samples_artifact.checksum}`",
        f"- Source package checksum: `{audit.inventory.package_checksum}`",
        "",
        "## Available Metrics",
        "",
    ]
    for metric in available_metrics:
        value = "" if metric.value is None else f"{metric.value:.6g}"
        lines.append(f"- `{metric.metric_id}` {metric.filters}: {value}")
    lines.extend(["", "## Unavailable", ""])
    for metric in unavailable_metrics:
        lines.append(f"- `{metric.metric_id}` {metric.filters}: {metric.unavailable_reason}")
    lines.extend(["", "## Not Applicable", ""])
    for metric in not_applicable:
        lines.append(f"- `{metric.metric_id}`: NOT_APPLICABLE")
    return "\n".join(lines) + "\n"


def _error_distribution_summary(
    error_samples: list[ErrorSampleRow],
    regime_stats: list[RegimeStatisticRow],
    config: Stage1PipelineConfig,
) -> JsonObject:
    return {
        "schema_version": "1.0.0",
        "pipeline_profile": config.pipeline_profile,
        "residual_definition": "predicted_count - ground_truth_count",
        "outlier_policy": config.outlier_policy,
        "residual_sample_count": len(error_samples),
        "pool_count": len(regime_stats),
        "pools": [row.model_dump(mode="json") for row in regime_stats],
    }


def _m2_error_model(
    error_samples_artifact: ArtifactRecord,
    regime_stats: list[RegimeStatisticRow],
    config: Stage1PipelineConfig,
) -> JsonObject:
    models: dict[str, dict[str, dict[str, JsonObject]]] = defaultdict(lambda: defaultdict(dict))
    for row in regime_stats:
        models[row.paradigm][row.model_id][row.ground_truth_regime] = {
            "pool_id": row.pool_id,
            "n": row.sample_count,
            "mean": row.mean_error,
            "std": row.std_error,
            "min": row.min_error,
            "max": row.max_error,
            "quantiles": {
                "p50": row.p50_error,
                "p90_abs": row.p90_absolute_error,
                "p95_abs": row.p95_absolute_error,
            },
            "split": row.split,
        }
    return {
        "schema_version": "1.0.0",
        "error_source": "empirical_benchmark_residual",
        "residual_definition": "predicted_count - ground_truth_count",
        "sampling_unit": "benchmark_sample_count",
        "sampling_granularity": "per_dataset_model_paradigm_split_regime",
        "dependency": "empirical_samples_preserved",
        "perturbation": "not_executed",
        "capacity_clipping": False,
        "outlier_policy": config.outlier_policy,
        "error_samples_artifact_uri": error_samples_artifact.uri,
        "error_samples_checksum": error_samples_artifact.checksum,
        "models": models,
    }


def _perception_csv_rows(rows: list[PerceptionResultRow]) -> list[list[str]]:
    header = [
        "sample_id",
        "dataset_id",
        "split",
        "paradigm",
        "model_id",
        "model_version",
        "ground_truth_count",
        "predicted_count",
        "error",
        "absolute_error",
        "ground_truth_regime",
        "predicted_regime",
        "source_ref",
    ]
    return [
        header,
        *[
            [
                row.sample_id,
                row.dataset_id,
                row.split,
                row.paradigm,
                row.model_id,
                row.model_version,
                _number_cell(row.ground_truth_count),
                _number_cell(row.predicted_count),
                _number_cell(row.error),
                _number_cell(row.absolute_error),
                row.ground_truth_regime,
                row.predicted_regime,
                row.source_ref,
            ]
            for row in rows
        ],
    ]


def _read_perception_parquet(path: str) -> list[PerceptionResultRow]:
    pq = _pyarrow_parquet()
    table = pq.read_table(path)
    columns = table.to_pydict()
    rows: list[PerceptionResultRow] = []
    for index in range(table.num_rows):
        rows.append(
            PerceptionResultRow(
                sample_id=str(columns["sample_id"][index]),
                dataset_id=str(columns["dataset_id"][index]),
                split=str(columns["split"][index]),
                paradigm=cast(PerceptionParadigm, columns["paradigm"][index]),
                model_id=str(columns["model_id"][index]),
                model_version=str(columns["model_version"][index]),
                ground_truth_count=float(columns["ground_truth_count"][index]),
                predicted_count=float(columns["predicted_count"][index]),
                error=float(columns["error"][index]),
                absolute_error=float(columns["absolute_error"][index]),
                ground_truth_regime=str(columns["ground_truth_regime"][index]),
                predicted_regime=str(columns["predicted_regime"][index]),
                source_ref=str(columns["source_ref"][index]),
            )
        )
    return rows


def _parquet_bytes(rows: list[JsonObject]) -> bytes:
    pa = _pyarrow()
    pq = _pyarrow_parquet()
    sink = pa.BufferOutputStream()
    if not rows:
        table = pa.table({})
    else:
        ordered = [
            {key: row.get(key) for key in sorted(rows[0])}
            for row in rows
        ]
        table = pa.Table.from_pylist(ordered)
    pq.write_table(table, sink, compression="zstd", version="2.6")
    return cast(bytes, sink.getvalue().to_pybytes())


def _pyarrow() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError(
            "pyarrow is required to write canonical Parquet artifacts."
        ) from exc
    return pa


def _pyarrow_parquet() -> Any:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError(
            "pyarrow.parquet is required for perception artifacts."
        ) from exc
    return pq


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [list(row) for row in csv.reader(handle)]


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), [])


def _csv_bytes(rows: list[list[str]]) -> bytes:
    text = "\n".join(",".join(_csv_cell(str(cell)) for cell in row) for row in rows) + "\n"
    return text.encode("utf-8")


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _jsonl_bytes(rows: list[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) + b"\n" for row in rows)


def _package_checksum(files: list[SourceFileInventory]) -> str:
    digest = hashlib.sha256()
    for file in sorted(files, key=lambda item: item.file_name):
        if not file.present or file.checksum is None:
            continue
        digest.update(file.file_name.encode())
        digest.update(file.checksum.encode())
    return f"sha256:{digest.hexdigest()}"


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _normalize_paradigm(value: object) -> PerceptionParadigm | None:
    text = str(value or "").strip().lower()
    if any(token in text for token in ["detect", "yolo", "bbox", "box"]):
        return "detection"
    if any(token in text for token in ["density", "csr", "crowd", "map"]):
        return "density"
    return None


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if value and count > 1)


def _float_value(value: object) -> float:
    number = float(str(value).strip())
    if not math.isfinite(number):
        raise ValueError("non_finite")
    return number


def _pool_id(key: tuple[str, str, str, str, str]) -> str:
    safe = "_".join(part.replace(" ", "_").replace("/", "_") for part in key)
    digest = hashlib.sha256("|".join(key).encode()).hexdigest()[:10]
    return f"POOL-{safe}-{digest}"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _number_cell(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.12g}"


def _csv_cell(value: str) -> str:
    output = value.replace('"', '""')
    if output.startswith(("=", "+", "-", "@")):
        output = "'" + output
    if any(char in output for char in [",", '"', "\n"]):
        return f'"{output}"'
    return output
