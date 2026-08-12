from __future__ import annotations

import csv
import hashlib
import html
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from two_stage.application.dto.integrated_report import (
    IntegratedReportPipelineConfig,
    IntegratedReportRunSummary,
)
from two_stage.application.dto.stage2 import (
    ArtifactRecord,
    MetricResult,
    StageStatusRecord,
)
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.enums import RunPurpose
from two_stage.domain.errors import DomainValidationError
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore

JsonObject = dict[str, Any]


class IntegratedReportRunner:
    """Builds exploratory M8/M9 reliability metrics from frozen upstream artifacts."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        config: IntegratedReportPipelineConfig,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.config = config
        self.artifacts: list[ArtifactRecord] = []

    def run(self) -> IntegratedReportRunSummary:
        self._reject_out_of_scope_run()
        bindings = self._load_and_validate_bindings()
        binding_artifact = self._publish_json(
            stage_id="M0",
            file_name="m8_m9_input_binding_manifest.json",
            purpose="M8/M9 exploratory upstream artifact binding and preflight result.",
            payload=bindings,
            schema_name="IntegratedReportInputBindingManifest",
            row_count=len(bindings["required_inputs"]),
        )

        ideal_metrics = _read_stage2_metrics(bindings["required_inputs"]["stage2_metrics"]["path"])
        validation_summary = _read_json_object(
            bindings["required_inputs"]["m7_validation_summary"]["path"]
        )
        adapter_status = _read_json_object(
            bindings["required_inputs"]["m6_decision_adapter_status"]["path"]
        )
        validation_rollups = _validation_rollups_from_summary(validation_summary)
        deploy_validations = (
            []
            if validation_rollups
            else _read_validation_results(
                bindings["required_inputs"]["m7_validation_results"]["path"]
            )
        )

        metrics = (
            self._calculate_metrics_from_rollups(
                ideal_metrics=ideal_metrics,
                validation_rollups=validation_rollups,
                validation_summary=validation_summary,
                adapter_status=adapter_status,
            )
            if validation_rollups
            else self._calculate_metrics(
                ideal_metrics=ideal_metrics,
                deploy_validations=deploy_validations,
                validation_summary=validation_summary,
                adapter_status=adapter_status,
            )
        )
        metrics_artifact = self._publish_json(
            stage_id="M8",
            file_name="integrated_metrics.json",
            purpose=(
                "Exploratory integrated reliability metrics from Stage II ideal and "
                "M5 perturbed decision validation."
            ),
            payload={
                "schema_version": "1.0.0",
                "pipeline_profile": self.config.pipeline_profile,
                "run_id": self.config.run_id,
                "metric_definition_version": self.config.metric_definition_version,
                "formal_output_enabled": False,
                "integrated_profile_enabled": False,
                "metrics": [metric.model_dump(mode="json") for metric in metrics],
            },
            schema_name="ExploratoryIntegratedMetrics",
            row_count=len(metrics),
        )
        metrics_csv_artifact = self._publish_bytes(
            stage_id="M8",
            file_name="metrics.csv",
            purpose="Human-readable exploratory integrated metrics table.",
            content=_metrics_csv(metrics).encode("utf-8-sig"),
            media_type="text/csv",
            schema_name="IntegratedMetricsCsv",
            row_count=len(metrics),
        )
        violations_artifact = self._publish_bytes(
            stage_id="M8",
            file_name="violations.parquet",
            purpose="M7 violation rows used by the exploratory integrated report.",
            content=(
                _violation_rollups_parquet_bytes(validation_rollups)
                if validation_rollups
                else _violations_parquet_bytes(deploy_validations)
            ),
            media_type="application/vnd.apache.parquet",
            schema_name="IntegratedViolationRows",
            row_count=(
                _violation_rollup_row_count(validation_rollups)
                if validation_rollups
                else sum(len(item.get("violations", [])) for item in deploy_validations)
            ),
        )

        report_md = self._render_report(metrics=metrics, adapter_status=adapter_status)
        report_md_artifact = self._publish_bytes(
            stage_id="M9",
            file_name="report.md",
            purpose="Frozen exploratory integrated reliability report.",
            content=report_md.encode("utf-8"),
            media_type="text/markdown",
            schema_name="ExploratoryIntegratedReport",
            row_count=None,
        )
        report_html_artifact = self._publish_bytes(
            stage_id="M9",
            file_name="report.html",
            purpose="Frozen exploratory integrated reliability report in HTML.",
            content=_html_report(report_md).encode("utf-8"),
            media_type="text/html",
            schema_name="ExploratoryIntegratedReportHtml",
            row_count=None,
        )
        self._publish_json(
            stage_id="M9",
            file_name="reproducibility_manifest.json",
            purpose="Replay metadata for exploratory M8/M9 integrated report.",
            payload=self._reproducibility_manifest(
                bindings=bindings,
                produced_artifacts=[
                    binding_artifact,
                    metrics_artifact,
                    metrics_csv_artifact,
                    violations_artifact,
                    report_md_artifact,
                    report_html_artifact,
                ],
            ),
            schema_name="IntegratedReportReproducibilityManifest",
            row_count=None,
        )
        self._publish_bytes(
            stage_id="M9",
            file_name="delivery_manifest.csv",
            purpose="Delivery manifest listing exploratory M8/M9 published artifacts.",
            content=self._delivery_manifest_csv().encode("utf-8-sig"),
            media_type="text/csv",
            schema_name="DeliveryManifest",
            row_count=len(self.artifacts),
        )

        r_ideal_available = any(
            metric.metric_id == "R_ideal" and metric.availability == "available"
            for metric in metrics
        )
        r_deploy_available = any(
            metric.metric_id == "R_deploy" and metric.availability == "available"
            for metric in metrics
        )
        delta_r_available = any(
            metric.metric_id == "Delta_R" and metric.availability == "available"
            for metric in metrics
        )
        summary = IntegratedReportRunSummary(
            run_id=self.config.run_id,
            pipeline_profile=self.config.pipeline_profile,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=_stage_statuses(),
            r_ideal_available=r_ideal_available,
            r_deploy_available=r_deploy_available,
            delta_r_available=delta_r_available,
            metric_count=len(metrics),
            limitations=_limitations(),
            artifacts=self.artifacts.copy(),
        )
        summary_artifact = self._publish_json(
            stage_id="M9",
            file_name="run_summary.json",
            purpose="Run status summary for exploratory M8/M9 integrated report.",
            payload=summary.model_dump(mode="json"),
            schema_name="IntegratedReportRunSummary",
            row_count=None,
        )
        summary.artifacts.append(summary_artifact)
        return summary

    def _reject_out_of_scope_run(self) -> None:
        if self.config.run_purpose == RunPurpose.FORMAL:
            raise DomainValidationError("Exploratory integrated report blocks formal runs.")
        if not self.config.exploratory_output_enabled:
            raise DomainValidationError("Exploratory report output must be explicitly enabled.")
        if self.config.formal_output_enabled:
            raise DomainValidationError(
                "Exploratory integrated report cannot enable formal output."
            )
        if self.config.integrated_profile_enabled:
            raise DomainValidationError("two_stage_integrated_v1 remains disabled.")

    def _load_and_validate_bindings(self) -> JsonObject:
        required: dict[str, JsonObject] = {
            "stage2_metrics": {
                "role": "ideal_reliability_source",
                "stage_id": "M8",
                "uri": self.config.stage2_metrics_uri,
                "checksum": self.config.stage2_metrics_checksum,
                "artifact_id": self.config.stage2_metrics_artifact_id,
                "file_name": "metrics.json",
            },
            "m7_validation_results": {
                "role": "perturbed_validation_source",
                "stage_id": "M7",
                "uri": self.config.m7_validation_results_uri,
                "checksum": self.config.m7_validation_results_checksum,
                "artifact_id": self.config.m7_validation_results_artifact_id,
                "file_name": "validation_results.json",
            },
            "m7_validation_summary": {
                "role": "perturbed_validation_summary",
                "stage_id": "M7",
                "uri": self.config.m7_validation_summary_uri,
                "checksum": self.config.m7_validation_summary_checksum,
                "artifact_id": self.config.m7_validation_summary_artifact_id,
                "file_name": "validation_summary.json",
            },
            "m6_decision_adapter_status": {
                "role": "decision_adapter_availability",
                "stage_id": "M6",
                "uri": self.config.m6_decision_adapter_status_uri,
                "checksum": self.config.m6_decision_adapter_status_checksum,
                "artifact_id": self.config.m6_decision_adapter_status_artifact_id,
                "file_name": "decision_adapter_status.json",
            },
        }
        for item in required.values():
            path = _artifact_uri_to_path(self.artifact_root, str(item["uri"]))
            if not path.exists():
                raise DomainValidationError(f"Published input artifact is missing: {item['uri']}")
            checksum = _checksum_file(path)
            if checksum != item["checksum"]:
                raise DomainValidationError(
                    f"Checksum mismatch for {item['uri']}: expected {item['checksum']}, "
                    f"got {checksum}"
                )
            item["path"] = str(path)
            item["byte_size"] = path.stat().st_size

        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "integrated_report_only": True,
            "required_inputs": required,
            "linked_sources": {
                "stage2_run_id": self.config.stage2_run_id,
                "decision_validation_run_id": self.config.decision_validation_run_id,
                "m5_observation_checksum": self.config.m5_observation_checksum,
                "m4_scenario_gt_checksum": self.config.m4_scenario_gt_checksum,
            },
            "forbidden_behavior": {
                "two_stage_integrated_v1": False,
                "formal_output": False,
                "recompute_m7_validator_logic": False,
                "unavailable_as_zero": False,
            },
        }

    def _calculate_metrics(
        self,
        *,
        ideal_metrics: list[MetricResult],
        deploy_validations: list[JsonObject],
        validation_summary: JsonObject,
        adapter_status: JsonObject,
    ) -> list[MetricResult]:
        definition_ref = "docs/research_decisions/metric_definitions.md"
        metrics: list[MetricResult] = []
        ideal_by_interface = {
            metric.filters.get("interface_type", "unknown"): metric
            for metric in ideal_metrics
            if metric.metric_id == "ideal_reliability" and metric.availability == "available"
        }
        deploy_by_condition: dict[tuple[str, str, str, str, str, str], list[JsonObject]] = (
            defaultdict(list)
        )
        for result in deploy_validations:
            deploy_by_condition[_deploy_group_key(result)].append(result)

        deploy_interfaces = {key[0] for key in deploy_by_condition}
        for group_key in sorted(deploy_by_condition):
            interface_type, condition_id, dataset_id, model_id, paradigm, split = group_key
            filters = _metric_filters(
                interface_type=interface_type,
                condition_id=condition_id,
                dataset_id=dataset_id,
                model_id=model_id,
                paradigm=paradigm,
                split=split,
            )
            ideal = ideal_by_interface.get(interface_type)
            if ideal is not None:
                metrics.append(
                    MetricResult(
                        metric_id="R_ideal",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value,
                        formula_inputs={
                            "source_metric": "ideal_reliability",
                            "source_stage": "Stage II M8",
                        },
                        aggregation="interface_macro",
                        n_trials=ideal.n_trials,
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            else:
                metrics.append(
                    _unavailable_metric(
                        "R_ideal",
                        filters,
                        "Matching Stage II ideal reliability is unavailable.",
                        self.config.metric_definition_version,
                        definition_ref,
                    )
                )

            deploy_results = deploy_by_condition[group_key]
            valid_count = float(sum(1 for result in deploy_results if result.get("valid") is True))
            denominator = float(len(deploy_results))
            r_deploy = valid_count / denominator if denominator else None
            metrics.extend(
                [
                    MetricResult(
                        metric_id="R_deploy",
                        metric_version=self.config.metric_definition_version,
                        availability="available" if r_deploy is not None else "unavailable",
                        value=r_deploy,
                        numerator=valid_count,
                        denominator=denominator,
                        formula_inputs={
                            "source_metric": "valid_rate",
                            "source_stage": "M7",
                            "trial_type": "controlled_error",
                            "valid_count": valid_count,
                            "evaluated_count": denominator,
                        },
                        aggregation="condition_ratio",
                        n_trials=len(deploy_results),
                        filters=filters,
                        definition_ref=definition_ref,
                        unavailable_reason=(
                            None if r_deploy is not None else "No validation results."
                        ),
                    ),
                    MetricResult(
                        metric_id="valid_rate",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=r_deploy,
                        numerator=valid_count,
                        denominator=denominator,
                        aggregation="condition_ratio",
                        n_trials=len(deploy_results),
                        filters={**filters, "trial_type": "controlled_error"},
                        definition_ref=definition_ref,
                    ),
                ]
            )
            action_values = [
                float(result["action_consistency_score"])
                for result in deploy_results
                if result.get("action_consistency_score") is not None
            ]
            metrics.append(
                MetricResult(
                    metric_id="action_consistency_score",
                    metric_version=self.config.metric_definition_version,
                    availability="available" if action_values else "unavailable",
                    value=(sum(action_values) / len(action_values) if action_values else None),
                    formula_inputs={
                        "source_metric": "action_consistency_score",
                        "source_stage": "M7",
                    },
                    aggregation="condition_mean",
                    n_trials=len(action_values),
                    filters={**filters, "trial_type": "controlled_error"},
                    definition_ref=definition_ref,
                    unavailable_reason=None if action_values else "No action consistency values.",
                )
            )
            risk_inputs_present = any("risk_tp" in result for result in deploy_results)
            if risk_inputs_present:
                risk_tp = sum(int(result.get("risk_tp") or 0) for result in deploy_results)
                risk_fp = sum(int(result.get("risk_fp") or 0) for result in deploy_results)
                risk_fn = sum(int(result.get("risk_fn") or 0) for result in deploy_results)
                metrics.append(
                    MetricResult(
                        metric_id="risk_f_beta",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=_risk_f_beta(risk_tp, risk_fp, risk_fn),
                        formula_inputs={
                            "risk_tp": risk_tp,
                            "risk_fp": risk_fp,
                            "risk_fn": risk_fn,
                            "beta": 2.0,
                        },
                        aggregation="condition_micro",
                        n_trials=len(deploy_results),
                        filters={**filters, "trial_type": "controlled_error"},
                        definition_ref=definition_ref,
                    )
                )
            else:
                metrics.append(
                    _unavailable_metric(
                        "risk_f_beta",
                        {**filters, "trial_type": "controlled_error"},
                        "No risk TP/FP/FN values are available.",
                        self.config.metric_definition_version,
                        definition_ref,
                    )
                )
            if ideal is not None and ideal.value is not None and r_deploy is not None:
                metrics.append(
                    MetricResult(
                        metric_id="Delta_R",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value - r_deploy,
                        formula_inputs={
                            "R_ideal": ideal.value,
                            "R_deploy": r_deploy,
                            "delta_formula": "R_ideal - R_deploy",
                        },
                        aggregation="condition_delta",
                        n_trials=len(deploy_results),
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            else:
                metrics.append(
                    _unavailable_metric(
                        "Delta_R",
                        filters,
                        "Delta_R requires both matching R_ideal and R_deploy.",
                        self.config.metric_definition_version,
                        definition_ref,
                    )
                )

        interfaces_without_deploy = (set(ideal_by_interface) | {"rule", "gai"}) - deploy_interfaces
        for interface_type in sorted(interfaces_without_deploy):
            filters = _metric_filters(
                interface_type=interface_type,
                condition_id="unavailable",
                dataset_id="unavailable",
                model_id="unavailable",
                paradigm="unavailable",
                split="unavailable",
            )
            ideal = ideal_by_interface.get(interface_type)
            if ideal is not None:
                metrics.append(
                    MetricResult(
                        metric_id="R_ideal",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value,
                        formula_inputs={
                            "source_metric": "ideal_reliability",
                            "source_stage": "Stage II M8",
                        },
                        aggregation="interface_macro",
                        n_trials=ideal.n_trials,
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            reason = "No perturbed validation results are available for this interface."
            if interface_type == "gai":
                reason = _gai_unavailable_reason(adapter_status)
            metrics.append(
                _unavailable_metric(
                    "R_deploy",
                    filters,
                    reason,
                    self.config.metric_definition_version,
                    definition_ref,
                )
            )
            metrics.append(
                _unavailable_metric(
                    "Delta_R",
                    filters,
                    "Delta_R requires matching perturbed validation results.",
                    self.config.metric_definition_version,
                    definition_ref,
                )
            )

        metrics.append(
            MetricResult(
                metric_id="perturbed_validation_coverage",
                metric_version=self.config.metric_definition_version,
                availability="available",
                value=(
                    float(validation_summary.get("result_count", 0))
                    / float(validation_summary.get("result_count", 0))
                    if int(validation_summary.get("result_count", 0)) > 0
                    else None
                ),
                numerator=float(validation_summary.get("result_count", 0)),
                denominator=float(validation_summary.get("result_count", 0)),
                aggregation="run_coverage",
                n_trials=int(validation_summary.get("result_count", 0)),
                filters={"trial_type": "controlled_error"},
                definition_ref=definition_ref,
            )
        )
        return metrics

    def _calculate_metrics_from_rollups(
        self,
        *,
        ideal_metrics: list[MetricResult],
        validation_rollups: list[JsonObject],
        validation_summary: JsonObject,
        adapter_status: JsonObject,
    ) -> list[MetricResult]:
        definition_ref = "docs/research_decisions/metric_definitions.md"
        metrics: list[MetricResult] = []
        ideal_by_interface = {
            metric.filters.get("interface_type", "unknown"): metric
            for metric in ideal_metrics
            if metric.metric_id == "ideal_reliability" and metric.availability == "available"
        }
        rollups_by_condition = {
            _rollup_group_key(rollup): rollup for rollup in validation_rollups
        }
        deploy_interfaces = {key[0] for key in rollups_by_condition}
        for group_key in sorted(rollups_by_condition):
            interface_type, condition_id, dataset_id, model_id, paradigm, split = group_key
            rollup = rollups_by_condition[group_key]
            filters = _metric_filters(
                interface_type=interface_type,
                condition_id=condition_id,
                dataset_id=dataset_id,
                model_id=model_id,
                paradigm=paradigm,
                split=split,
            )
            ideal = ideal_by_interface.get(interface_type)
            if ideal is not None:
                metrics.append(
                    MetricResult(
                        metric_id="R_ideal",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value,
                        formula_inputs={
                            "source_metric": "ideal_reliability",
                            "source_stage": "Stage II M8",
                        },
                        aggregation="interface_macro",
                        n_trials=ideal.n_trials,
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            else:
                metrics.append(
                    _unavailable_metric(
                        "R_ideal",
                        filters,
                        "Matching Stage II ideal reliability is unavailable.",
                        self.config.metric_definition_version,
                        definition_ref,
                    )
                )

            denominator = float(rollup.get("result_count") or 0)
            valid_count = float(rollup.get("valid_count") or 0)
            r_deploy = valid_count / denominator if denominator else None
            metrics.extend(
                [
                    MetricResult(
                        metric_id="R_deploy",
                        metric_version=self.config.metric_definition_version,
                        availability="available" if r_deploy is not None else "unavailable",
                        value=r_deploy,
                        numerator=valid_count,
                        denominator=denominator,
                        formula_inputs={
                            "source_metric": "valid_rate",
                            "source_stage": "M7",
                            "trial_type": "controlled_error",
                            "valid_count": valid_count,
                            "evaluated_count": denominator,
                        },
                        aggregation="condition_ratio",
                        n_trials=int(denominator),
                        filters=filters,
                        definition_ref=definition_ref,
                        unavailable_reason=(
                            None if r_deploy is not None else "No validation results."
                        ),
                    ),
                    MetricResult(
                        metric_id="valid_rate",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=r_deploy,
                        numerator=valid_count,
                        denominator=denominator,
                        aggregation="condition_ratio",
                        n_trials=int(denominator),
                        filters={**filters, "trial_type": "controlled_error"},
                        definition_ref=definition_ref,
                    ),
                ]
            )
            action_count = int(rollup.get("action_consistency_count") or 0)
            action_sum = float(rollup.get("action_consistency_sum") or 0.0)
            metrics.append(
                MetricResult(
                    metric_id="action_consistency_score",
                    metric_version=self.config.metric_definition_version,
                    availability="available" if action_count else "unavailable",
                    value=(action_sum / action_count if action_count else None),
                    formula_inputs={
                        "source_metric": "action_consistency_score",
                        "source_stage": "M7",
                    },
                    aggregation="condition_mean",
                    n_trials=action_count,
                    filters={**filters, "trial_type": "controlled_error"},
                    definition_ref=definition_ref,
                    unavailable_reason=None if action_count else "No action consistency values.",
                )
            )
            risk_tp = int(rollup.get("risk_tp") or 0)
            risk_fp = int(rollup.get("risk_fp") or 0)
            risk_fn = int(rollup.get("risk_fn") or 0)
            metrics.append(
                MetricResult(
                    metric_id="risk_f_beta",
                    metric_version=self.config.metric_definition_version,
                    availability="available",
                    value=_risk_f_beta(risk_tp, risk_fp, risk_fn),
                    formula_inputs={
                        "risk_tp": risk_tp,
                        "risk_fp": risk_fp,
                        "risk_fn": risk_fn,
                        "beta": 2.0,
                    },
                    aggregation="condition_micro",
                    n_trials=int(denominator),
                    filters={**filters, "trial_type": "controlled_error"},
                    definition_ref=definition_ref,
                )
            )
            if ideal is not None and ideal.value is not None and r_deploy is not None:
                metrics.append(
                    MetricResult(
                        metric_id="Delta_R",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value - r_deploy,
                        formula_inputs={
                            "R_ideal": ideal.value,
                            "R_deploy": r_deploy,
                            "delta_formula": "R_ideal - R_deploy",
                        },
                        aggregation="condition_delta",
                        n_trials=int(denominator),
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            else:
                metrics.append(
                    _unavailable_metric(
                        "Delta_R",
                        filters,
                        "Delta_R requires both matching R_ideal and R_deploy.",
                        self.config.metric_definition_version,
                        definition_ref,
                    )
                )

        interfaces_without_deploy = (set(ideal_by_interface) | {"rule", "gai"}) - deploy_interfaces
        for interface_type in sorted(interfaces_without_deploy):
            filters = _metric_filters(
                interface_type=interface_type,
                condition_id="unavailable",
                dataset_id="unavailable",
                model_id="unavailable",
                paradigm="unavailable",
                split="unavailable",
            )
            ideal = ideal_by_interface.get(interface_type)
            if ideal is not None:
                metrics.append(
                    MetricResult(
                        metric_id="R_ideal",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=ideal.value,
                        formula_inputs={
                            "source_metric": "ideal_reliability",
                            "source_stage": "Stage II M8",
                        },
                        aggregation="interface_macro",
                        n_trials=ideal.n_trials,
                        filters=filters,
                        definition_ref=definition_ref,
                    )
                )
            reason = "No perturbed validation results are available for this interface."
            if interface_type == "gai":
                reason = _gai_unavailable_reason(adapter_status)
            metrics.append(
                _unavailable_metric(
                    "R_deploy",
                    filters,
                    reason,
                    self.config.metric_definition_version,
                    definition_ref,
                )
            )
            metrics.append(
                _unavailable_metric(
                    "Delta_R",
                    filters,
                    "Delta_R requires matching perturbed validation results.",
                    self.config.metric_definition_version,
                    definition_ref,
                )
            )

        result_count = int(validation_summary.get("result_count", 0))
        metrics.append(
            MetricResult(
                metric_id="perturbed_validation_coverage",
                metric_version=self.config.metric_definition_version,
                availability="available",
                value=(float(result_count) / float(result_count) if result_count > 0 else None),
                numerator=float(result_count),
                denominator=float(result_count),
                aggregation="run_coverage",
                n_trials=result_count,
                filters={"trial_type": "controlled_error"},
                definition_ref=definition_ref,
            )
        )
        return metrics

    def _render_report(self, *, metrics: list[MetricResult], adapter_status: JsonObject) -> str:
        available = [metric for metric in metrics if metric.availability == "available"]
        unavailable = [metric for metric in metrics if metric.availability != "available"]
        lines = [
            "# Exploratory Integrated Reliability Report",
            "",
            f"- Run ID: `{self.config.run_id}`",
            f"- Pipeline profile: `{self.config.pipeline_profile}`",
            f"- Run purpose: `{self.config.run_purpose.value}`",
            "- Scope: M8/M9 report-only exploratory comparison.",
            "- This report stage may be consumed by exploratory `two_stage_integrated_v1`.",
            "- This is not a formal report.",
            "- `R_deploy` uses M7 valid rate for controlled perturbed observations.",
            "- Action Consistency and Risk Consistency are supporting diagnostics.",
            "- M8 reads M7 validation outputs; it does not re-run validator logic.",
            (
                "- GAI status: "
                f"`{cast(JsonObject, adapter_status.get('gai', {})).get('status', 'unknown')}`"
            ),
            "",
            "## Available Metrics",
            "",
        ]
        for metric in available:
            lines.append(
                f"- `{metric.metric_id}` {metric.filters}: {metric.value} "
                f"(n={metric.n_trials}, aggregation={metric.aggregation})"
            )
        lines.extend(["", "## Unavailable Metrics", ""])
        for metric in unavailable:
            lines.append(
                f"- `{metric.metric_id}` {metric.filters}: unavailable; "
                f"reason={metric.unavailable_reason}"
            )
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in _limitations())
        return "\n".join(lines) + "\n"

    def _reproducibility_manifest(
        self,
        *,
        bindings: JsonObject,
        produced_artifacts: list[ArtifactRecord],
    ) -> JsonObject:
        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_id": self.config.run_id,
            "run_purpose": self.config.run_purpose.value,
            "input_bindings": bindings["required_inputs"],
            "metric_definition_version": self.config.metric_definition_version,
            "produced_artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "stage_id": artifact.stage_id,
                    "file_name": artifact.file_name,
                    "uri": artifact.uri,
                    "checksum": artifact.checksum,
                    "row_count": artifact.row_count,
                }
                for artifact in produced_artifacts
            ],
            "limitations": _limitations(),
        }

    def _delivery_manifest_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "file_name",
                "stage_id",
                "schema_name",
                "schema_version",
                "row_count",
                "checksum",
                "uri",
            ]
        )
        for artifact in self.artifacts:
            writer.writerow(
                [
                    artifact.file_name,
                    artifact.stage_id,
                    artifact.schema_name,
                    artifact.schema_version,
                    artifact.row_count if artifact.row_count is not None else "",
                    artifact.checksum,
                    artifact.uri,
                ]
            )
        return output.getvalue()

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
        row_count: int | None,
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


def _stage_statuses() -> list[StageStatusRecord]:
    output = [StageStatusRecord(stage_id="M0", status="succeeded")]
    for stage_id in ("M1", "M2", "M3", "M4", "M5", "M6", "M7"):
        output.append(
            StageStatusRecord(
                stage_id=stage_id,
                status="not_required",
                reason="M8/M9 exploratory report consumes published upstream artifacts.",
            )
        )
    output.append(StageStatusRecord(stage_id="M8", status="succeeded"))
    output.append(StageStatusRecord(stage_id="M9", status="succeeded"))
    return output


def _limitations() -> list[str]:
    return [
        "This profile is an exploratory M8/M9 report-only comparison.",
        "It consumes published Stage II ideal metrics and M6/M7 perturbed validation artifacts.",
        "It does not execute M1-M7; standalone report runs do not create a parent integrated run.",
        "R_deploy is computed from M7 valid rate; action and risk consistency are "
        "supporting diagnostics.",
        "M8 aggregates M7 outputs only; it does not re-implement validator logic.",
        "GAI remains unavailable unless a real or explicit mock upstream result exists.",
        "Formal run purpose remains blocked.",
    ]


def _read_stage2_metrics(path: str) -> list[MetricResult]:
    payload = _read_json_object(path)
    rows = payload.get("metrics")
    if not isinstance(rows, list):
        raise DomainValidationError("Stage II metrics.json must contain a metrics array.")
    return [MetricResult.model_validate(row) for row in rows]


def _read_validation_results(path: str) -> list[JsonObject]:
    payload = _read_json_object(path)
    rows = payload.get("results") or payload.get("validations")
    if not isinstance(rows, list):
        raise DomainValidationError("M7 validation_results.json must contain results.")
    return [cast(JsonObject, row) for row in rows if isinstance(row, dict)]


def _read_json_object(path: str) -> JsonObject:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DomainValidationError(f"JSON artifact must contain an object: {path}")
    return data


def _validation_rollups_from_summary(summary: JsonObject) -> list[JsonObject]:
    rollups = summary.get("condition_rollups")
    if not isinstance(rollups, list):
        return []
    return [cast(JsonObject, item) for item in rollups if isinstance(item, dict)]


def _artifact_uri_to_path(artifact_root: Path, uri: str) -> Path:
    if not uri.startswith("artifact://"):
        raise DomainValidationError(f"Unsupported artifact URI for M8/M9 input: {uri}")
    relative = uri.removeprefix("artifact://")
    if Path(relative).is_absolute():
        raise DomainValidationError("Artifact URI must be relative to artifact root.")
    target = (artifact_root / "published" / relative).resolve()
    try:
        target.relative_to((artifact_root / "published").resolve())
    except ValueError as exc:
        raise DomainValidationError(f"Artifact URI path traversal rejected: {uri}") from exc
    return target


def _metrics_csv(metrics: list[MetricResult]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "metric_id",
            "availability",
            "value",
            "numerator",
            "denominator",
            "aggregation",
            "n_trials",
            "filters",
            "unavailable_reason",
        ]
    )
    for metric in metrics:
        writer.writerow(
            [
                metric.metric_id,
                metric.availability,
                metric.value if metric.value is not None else "",
                metric.numerator if metric.numerator is not None else "",
                metric.denominator if metric.denominator is not None else "",
                metric.aggregation or "",
                metric.n_trials if metric.n_trials is not None else "",
                json.dumps(metric.filters, ensure_ascii=False, sort_keys=True),
                metric.unavailable_reason or "",
            ]
        )
    return output.getvalue()


def _violations_parquet_bytes(validations: list[JsonObject]) -> bytes:
    rows: list[JsonObject] = []
    for result in validations:
        for violation in cast(list[JsonObject], result.get("violations", [])):
            rows.append(
                {
                    "validation_id": result.get("validation_id"),
                    "decision_id": result.get("decision_id"),
                    "interface_type": result.get("interface_type"),
                    "trial_id": result.get("trial_id"),
                    "scenario_id": result.get("scenario_id"),
                    "error_realization_id": result.get("error_realization_id"),
                    "condition_id": result.get("condition_id"),
                    "dataset_id": result.get("dataset_id"),
                    "model_id": result.get("model_id"),
                    "paradigm": result.get("paradigm"),
                    "split": result.get("split"),
                    "violation_code": violation.get("code"),
                    "message_zh_tw": violation.get("message_zh_tw"),
                    "node_id": violation.get("node_id"),
                    "edge_id": violation.get("edge_id"),
                    "action_id": violation.get("action_id"),
                }
            )
    return _parquet_bytes(rows)


def _violation_rollups_parquet_bytes(rollups: list[JsonObject]) -> bytes:
    rows: list[JsonObject] = []
    for rollup in rollups:
        violation_counts = rollup.get("violation_counts")
        if not isinstance(violation_counts, dict):
            continue
        for code, count in violation_counts.items():
            rows.append(
                {
                    "interface_type": rollup.get("interface_type"),
                    "condition_id": rollup.get("condition_id"),
                    "dataset_id": rollup.get("dataset_id"),
                    "model_id": rollup.get("model_id"),
                    "paradigm": rollup.get("paradigm"),
                    "split": rollup.get("split"),
                    "violation_code": str(code),
                    "violation_count": int(count),
                    "row_type": "aggregated_condition_violation_count",
                }
            )
    return _parquet_bytes(rows)


def _violation_rollup_row_count(rollups: list[JsonObject]) -> int:
    total = 0
    for rollup in rollups:
        violation_counts = rollup.get("violation_counts")
        if isinstance(violation_counts, dict):
            total += len(violation_counts)
    return total


def _deploy_group_key(result: JsonObject) -> tuple[str, str, str, str, str, str]:
    return (
        str(result.get("interface_type") or "unknown"),
        str(result.get("condition_id") or "unscoped"),
        str(result.get("dataset_id") or "unscoped"),
        str(result.get("model_id") or "unscoped"),
        str(result.get("paradigm") or "unscoped"),
        str(result.get("split") or "unscoped"),
    )


def _rollup_group_key(rollup: JsonObject) -> tuple[str, str, str, str, str, str]:
    return (
        str(rollup.get("interface_type") or "unknown"),
        str(rollup.get("condition_id") or "unscoped"),
        str(rollup.get("dataset_id") or "unscoped"),
        str(rollup.get("model_id") or "unscoped"),
        str(rollup.get("paradigm") or "unscoped"),
        str(rollup.get("split") or "unscoped"),
    )


def _metric_filters(
    *,
    interface_type: str,
    condition_id: str,
    dataset_id: str,
    model_id: str,
    paradigm: str,
    split: str,
) -> dict[str, str]:
    return {
        "interface_type": interface_type,
        "condition_id": condition_id,
        "dataset_id": dataset_id,
        "model_id": model_id,
        "paradigm": paradigm,
        "split": split,
        "trial_scope": "all",
        "run_scope": "exploratory_m8_m9_report",
    }


def _risk_f_beta(tp: int, fp: int, fn: int, *, beta: float = 2.0) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    denominator = (beta * beta * precision) + recall
    if denominator == 0:
        return 0.0
    return ((1 + beta * beta) * precision * recall) / denominator


def _parquet_bytes(rows: list[JsonObject]) -> bytes:
    pa = _pyarrow()
    pq = _pyarrow_parquet()
    sink = pa.BufferOutputStream()
    if not rows:
        table = pa.table(
            {
                "validation_id": pa.array([], type=pa.string()),
                "decision_id": pa.array([], type=pa.string()),
                "interface_type": pa.array([], type=pa.string()),
                "trial_id": pa.array([], type=pa.string()),
                "scenario_id": pa.array([], type=pa.string()),
                "error_realization_id": pa.array([], type=pa.string()),
                "condition_id": pa.array([], type=pa.string()),
                "dataset_id": pa.array([], type=pa.string()),
                "model_id": pa.array([], type=pa.string()),
                "paradigm": pa.array([], type=pa.string()),
                "split": pa.array([], type=pa.string()),
                "violation_code": pa.array([], type=pa.string()),
                "message_zh_tw": pa.array([], type=pa.string()),
                "node_id": pa.array([], type=pa.string()),
                "edge_id": pa.array([], type=pa.string()),
                "action_id": pa.array([], type=pa.string()),
            }
        )
    else:
        ordered = [{key: row.get(key) for key in sorted(rows[0])} for row in rows]
        table = pa.Table.from_pylist(ordered)
    pq.write_table(table, sink, compression="zstd", version="2.6")
    return cast(bytes, sink.getvalue().to_pybytes())


def _pyarrow() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError(
            "pyarrow is required to write M8/M9 Parquet artifacts."
        ) from exc
    return pa


def _pyarrow_parquet() -> Any:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError("pyarrow.parquet is required for M8/M9 artifacts.") from exc
    return pq


def _unavailable_metric(
    metric_id: str,
    filters: dict[str, str],
    reason: str,
    metric_version: str,
    definition_ref: str,
) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        metric_version=metric_version,
        availability="unavailable",
        value=None,
        filters=filters,
        definition_ref=definition_ref,
        unavailable_reason=reason,
    )


def _gai_unavailable_reason(adapter_status: JsonObject) -> str:
    gai = adapter_status.get("gai")
    if isinstance(gai, dict) and isinstance(gai.get("message"), str):
        return str(gai["message"])
    return "GAI validation results are unavailable."


def _html_report(markdown: str) -> str:
    body = "\n".join(
        f"<p>{html.escape(line)}</p>" if line else "<br />"
        for line in markdown.splitlines()
    )
    return (
        "<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\">"
        "<title>Exploratory Integrated Reliability Report</title></head>"
        f"<body>{body}</body></html>"
    )


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
