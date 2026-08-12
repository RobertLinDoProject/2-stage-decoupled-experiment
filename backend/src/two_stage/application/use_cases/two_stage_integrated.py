from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from two_stage.application.dto.decision_validation import M5DecisionValidationPipelineConfig
from two_stage.application.dto.integrated_report import IntegratedReportPipelineConfig
from two_stage.application.dto.m5 import M5ExploratoryPipelineConfig
from two_stage.application.dto.stage1 import Stage1PipelineConfig
from two_stage.application.dto.stage2 import (
    ArtifactRecord,
    Stage2PipelineConfig,
    StageStatusRecord,
)
from two_stage.application.dto.two_stage_integrated import (
    TwoStageIntegratedPipelineConfig,
    TwoStageIntegratedRunSummary,
)
from two_stage.application.use_cases.integrated_report import IntegratedReportRunner
from two_stage.application.use_cases.m5_controlled_perturbation import (
    M5ControlledPerturbationRunner,
)
from two_stage.application.use_cases.m5_decision_validation import M5DecisionValidationRunner
from two_stage.application.use_cases.perception_benchmark import Stage1PerceptionBenchmarkRunner
from two_stage.application.use_cases.stage2_topology_ideal import Stage2TopologyIdealRunner
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.enums import RunPurpose
from two_stage.domain.errors import DomainValidationError
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore
from two_stage.infrastructure.decision_adapters import GaiHttpAdapterConfig

JsonObject = dict[str, Any]


class TwoStageIntegratedRunner:
    """Runs the development/exploratory M0-M9 DAG from existing stage runners."""

    def __init__(
        self,
        *,
        source_root: str | Path,
        artifact_root: str | Path,
        config: TwoStageIntegratedPipelineConfig,
        gai_http_config: GaiHttpAdapterConfig | None = None,
    ) -> None:
        self.source_root = Path(source_root)
        self.artifact_root = Path(artifact_root)
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.config = config
        self.gai_http_config = gai_http_config
        self.artifacts: list[ArtifactRecord] = []

    def run(self) -> TwoStageIntegratedRunSummary:
        self._reject_out_of_scope_run()
        run_id = _required_run_id(self.config)
        child_run_ids = {
            "stage1": f"{run_id}-stage1",
            "stage2": f"{run_id}-stage2",
            "m5": f"{run_id}-m5",
            "m6_m7": f"{run_id}-m6-m7",
            "m8_m9": f"{run_id}-m8-m9",
        }

        stage1 = Stage1PerceptionBenchmarkRunner(
            source_root=self.source_root,
            artifact_root=self.artifact_root,
            config=Stage1PipelineConfig(
                run_id=child_run_ids["stage1"],
                run_purpose=self.config.run_purpose,
                perception_package_path=self.config.perception_package_path,
            ),
        ).run()
        self.artifacts.extend(stage1.artifacts)

        stage2 = Stage2TopologyIdealRunner(
            source_root=self.source_root,
            artifact_root=self.artifact_root,
            config=Stage2PipelineConfig(
                run_id=child_run_ids["stage2"],
                run_purpose=RunPurpose.EXPLORATORY,
                topology_profile_id=self.config.topology_profile_id,
                topology_package_path=self.config.topology_package_path,
                topology_source_id=self.config.topology_source_id,
                root_seed=self.config.root_seed,
                scenario_count=self.config.scenario_count,
                total_population=self.config.total_population,
                high_population_ratio=self.config.high_population_ratio,
                high_population_node_ids=self.config.high_population_node_ids,
                include_mock_gai=False,
            ),
        ).run()
        self.artifacts.extend(stage2.artifacts)

        m2_error_samples = _artifact_by_suffix(stage1.artifacts, "/M2/error_samples.parquet")
        m3_topology = _artifact_by_suffix(stage2.artifacts, "/M3/topology_spec.json")
        m4_scenario_gt = _artifact_by_suffix(stage2.artifacts, "/M4/scenario_gt.jsonl")
        m5 = M5ControlledPerturbationRunner(
            artifact_root=self.artifact_root,
            config=M5ExploratoryPipelineConfig(
                run_id=child_run_ids["m5"],
                run_purpose=RunPurpose.EXPLORATORY,
                stage1_run_id=child_run_ids["stage1"],
                stage2_run_id=child_run_ids["stage2"],
                m2_error_samples_artifact_id=m2_error_samples.artifact_id,
                m2_error_samples_uri=m2_error_samples.uri,
                m2_error_samples_checksum=m2_error_samples.checksum,
                m3_topology_spec_artifact_id=m3_topology.artifact_id,
                m3_topology_spec_uri=m3_topology.uri,
                m3_topology_spec_checksum=m3_topology.checksum,
                m4_scenario_gt_artifact_id=m4_scenario_gt.artifact_id,
                m4_scenario_gt_uri=m4_scenario_gt.uri,
                m4_scenario_gt_checksum=m4_scenario_gt.checksum,
                root_seed=self.config.root_seed,
                trial_count=self.config.trial_count,
                minimum_pool_size=self.config.minimum_pool_size,
                residual_model_id=self.config.residual_model_id,
                residual_paradigm=self.config.residual_paradigm,
                residual_split=self.config.residual_split,
            ),
        ).run()
        self.artifacts.extend(m5.artifacts)

        m5_errors = _artifact_by_suffix(m5.artifacts, "/M5/error_realizations.parquet")
        m5_observation = _artifact_by_suffix(
            m5.artifacts,
            "/M5/perturbed_observation_population.jsonl",
        )
        m6_m7 = M5DecisionValidationRunner(
            artifact_root=self.artifact_root,
            config=M5DecisionValidationPipelineConfig(
                run_id=child_run_ids["m6_m7"],
                run_purpose=RunPurpose.EXPLORATORY,
                m5_run_id=child_run_ids["m5"],
                m5_observation_artifact_id=m5_observation.artifact_id,
                m5_observation_uri=m5_observation.uri,
                m5_observation_checksum=m5_observation.checksum,
                m5_error_realizations_artifact_id=m5_errors.artifact_id,
                m5_error_realizations_uri=m5_errors.uri,
                m5_error_realizations_checksum=m5_errors.checksum,
                m3_topology_spec_artifact_id=m3_topology.artifact_id,
                m3_topology_spec_uri=m3_topology.uri,
                m3_topology_spec_checksum=m3_topology.checksum,
                m4_scenario_gt_artifact_id=m4_scenario_gt.artifact_id,
                m4_scenario_gt_uri=m4_scenario_gt.uri,
                m4_scenario_gt_checksum=m4_scenario_gt.checksum,
                gai_mode=self.config.gai_mode,
                include_rule=self.config.include_rule,
                include_mock_gai=self.config.include_mock_gai,
                live_gai_provider_enabled=self.config.live_gai_provider_enabled,
            ),
            gai_http_config=self.gai_http_config,
        ).run()
        self.artifacts.extend(m6_m7.artifacts)

        stage2_metrics = _artifact_by_suffix(stage2.artifacts, "/M8/metrics.json")
        m6_adapter_status = _artifact_by_suffix(
            m6_m7.artifacts,
            "/M6/decision_adapter_status.json",
        )
        m7_validation_results = _artifact_by_suffix(
            m6_m7.artifacts,
            "/M7/validation_results.json",
        )
        m7_validation_summary = _artifact_by_suffix(
            m6_m7.artifacts,
            "/M7/validation_summary.json",
        )
        m8_m9 = IntegratedReportRunner(
            artifact_root=self.artifact_root,
            config=IntegratedReportPipelineConfig(
                run_id=child_run_ids["m8_m9"],
                run_purpose=RunPurpose.EXPLORATORY,
                stage2_run_id=child_run_ids["stage2"],
                decision_validation_run_id=child_run_ids["m6_m7"],
                stage2_metrics_artifact_id=stage2_metrics.artifact_id,
                stage2_metrics_uri=stage2_metrics.uri,
                stage2_metrics_checksum=stage2_metrics.checksum,
                m7_validation_results_artifact_id=m7_validation_results.artifact_id,
                m7_validation_results_uri=m7_validation_results.uri,
                m7_validation_results_checksum=m7_validation_results.checksum,
                m7_validation_summary_artifact_id=m7_validation_summary.artifact_id,
                m7_validation_summary_uri=m7_validation_summary.uri,
                m7_validation_summary_checksum=m7_validation_summary.checksum,
                m6_decision_adapter_status_artifact_id=m6_adapter_status.artifact_id,
                m6_decision_adapter_status_uri=m6_adapter_status.uri,
                m6_decision_adapter_status_checksum=m6_adapter_status.checksum,
                m5_observation_artifact_id=m5_observation.artifact_id,
                m5_observation_uri=m5_observation.uri,
                m5_observation_checksum=m5_observation.checksum,
                m4_scenario_gt_artifact_id=m4_scenario_gt.artifact_id,
                m4_scenario_gt_uri=m4_scenario_gt.uri,
                m4_scenario_gt_checksum=m4_scenario_gt.checksum,
                metric_definition_version=self.config.metric_definition_version,
            ),
        ).run()
        self.artifacts.extend(m8_m9.artifacts)

        summary = TwoStageIntegratedRunSummary(
            run_id=run_id,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=_stage_statuses(),
            child_run_ids=child_run_ids,
            r_ideal_available=m8_m9.r_ideal_available,
            r_deploy_available=m8_m9.r_deploy_available,
            delta_r_available=m8_m9.delta_r_available,
            metric_count=m8_m9.metric_count,
            formal_output_enabled=False,
            limitations=_limitations(),
            artifacts=self.artifacts.copy(),
        )
        summary_artifact = self._publish_json(
            stage_id="M9",
            file_name="two_stage_integrated_run_summary.json",
            purpose="Parent integrated run summary for development/exploratory M0-M9 DAG.",
            payload=summary.model_dump(mode="json", exclude={"artifacts"}),
            schema_name="TwoStageIntegratedRunSummary",
            row_count=None,
        )
        manifest_artifact = self._publish_bytes(
            stage_id="M9",
            file_name="two_stage_integrated_delivery_manifest.csv",
            purpose="Parent delivery manifest listing child and parent integrated artifacts.",
            content=self._delivery_manifest_csv().encode("utf-8-sig"),
            media_type="text/csv",
            schema_name="TwoStageIntegratedDeliveryManifest",
            row_count=len(self.artifacts),
        )
        summary.artifacts.extend([summary_artifact, manifest_artifact])
        return summary

    def _reject_out_of_scope_run(self) -> None:
        if self.config.run_purpose == RunPurpose.FORMAL:
            raise DomainValidationError(
                "two_stage_integrated_v1 formal execution remains gated."
            )
        if not self.config.execution_enabled:
            raise DomainValidationError("two_stage_integrated_v1 execution_enabled must be true.")
        if not self.config.exploratory_output_enabled:
            raise DomainValidationError(
                "two_stage_integrated_v1 exploratory_output_enabled must be true."
            )
        if self.config.formal_output_enabled:
            raise DomainValidationError(
                "Development/exploratory integrated runner cannot enable formal output."
            )
        if self.config.gai_mode == "http" and not self.config.live_gai_provider_enabled:
            raise DomainValidationError("Live GAI provider is required for gai_mode=http.")



    def _publish_json(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        payload: object,
        schema_name: str,
        row_count: int | None,
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
        run_id = _required_run_id(self.config)
        staged = self.artifact_store.stage(
            ArtifactPayload(
                relative_path=f"runs/{run_id}/{stage_id}/{file_name}",
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

    def _delivery_manifest_csv(self) -> str:
        handle = io.StringIO()
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "artifact_id",
                "stage_id",
                "file_name",
                "uri",
                "checksum",
                "schema_name",
                "row_count",
            ],
        )
        writer.writeheader()
        for artifact in self.artifacts:
            writer.writerow(
                {
                    "artifact_id": artifact.artifact_id,
                    "stage_id": artifact.stage_id,
                    "file_name": artifact.file_name,
                    "uri": artifact.uri,
                    "checksum": artifact.checksum,
                    "schema_name": artifact.schema_name,
                    "row_count": artifact.row_count,
                }
            )
        return handle.getvalue()


def _required_run_id(config: TwoStageIntegratedPipelineConfig) -> str:
    if config.run_id is None:
        raise DomainValidationError("two_stage_integrated_v1 requires run_id at runtime.")
    return config.run_id


def _artifact_by_suffix(artifacts: list[ArtifactRecord], suffix: str) -> ArtifactRecord:
    for artifact in artifacts:
        if artifact.uri.endswith(suffix):
            return artifact
    raise DomainValidationError(f"Required integrated artifact missing: {suffix}")


def _stage_statuses() -> list[StageStatusRecord]:
    return [
        StageStatusRecord(
            stage_id=stage_id,
            status="succeeded",
            reason="two_stage_integrated_v1 development/exploratory DAG completed.",
        )
        for stage_id in ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9")
    ]


def _limitations() -> list[str]:
    return [
        "This is an exploratory two_stage_integrated_v1 deployment-effect run.",
        "Formal integrated execution and formal paper conclusions remain gated.",
        "M5 uses paper-aligned empirical residual propagation from M2 samples.",
        "Density and Detection residual conditions are evaluated separately.",
        "R_deploy is computed from M7 valid rate; action and risk consistency are "
        "supporting diagnostics.",
        "Mock or unavailable GAI outputs are not formal GAI evidence.",
        "If no live GAI provider is configured, only Rule-based deployment effect is reported.",
    ]


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
