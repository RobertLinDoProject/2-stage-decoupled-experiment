from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from two_stage.application.dto.decision_validation import (
    DecisionAction,
    DecisionRequest,
    ExploratoryDecisionResult,
    ExploratoryValidationResult,
    M5DecisionValidationPipelineConfig,
    M5DecisionValidationRunSummary,
    PerturbedObservationRecord,
    ValidationViolation,
)
from two_stage.application.dto.stage2 import (
    ArtifactRecord,
    ScenarioRecord,
    StageStatusRecord,
    TopologyEdge,
    TopologyNode,
    TopologySpec,
)
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.enums import RunPurpose
from two_stage.domain.errors import DomainValidationError
from two_stage.domain.ports.decision_interface import DecisionRequestPayload
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore
from two_stage.infrastructure.decision_adapters import (
    GaiDecisionAdapterCall,
    GaiHttpAdapterConfig,
    GeminiFlashLiteDecisionAdapter,
    HttpGaiDecisionAdapter,
    UnavailableGaiDecisionAdapter,
    create_gai_decision_adapter,
)

JsonObject = dict[str, Any]


@dataclass(slots=True)
class M6M7StreamStats:
    request_count: int = 0
    decision_count: int = 0
    rule_decision_count: int = 0
    validation_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    condition_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    violation_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    gai_status_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    condition_rollups: dict[tuple[str, str, str, str, str, str], JsonObject] = field(
        default_factory=dict
    )


class M5DecisionValidationRunner:
    """Runs exploratory M6/M7 decision validation from a published M5 observation."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        config: M5DecisionValidationPipelineConfig,
        gai_http_config: GaiHttpAdapterConfig | None = None,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.config = config
        self.gai_http_config = gai_http_config
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.artifacts: list[ArtifactRecord] = []
        self.gai_calls: list[GaiDecisionAdapterCall] = []

    def run(self) -> M5DecisionValidationRunSummary:
        self._reject_out_of_scope_run()
        bindings = self._load_and_validate_bindings()
        binding_artifact = self._publish_json(
            stage_id="M0",
            file_name="m6_m7_input_binding_manifest.json",
            purpose="M6/M7 exploratory upstream artifact binding and preflight result.",
            payload=bindings,
            schema_name="M5DecisionValidationInputBindingManifest",
            row_count=len(bindings["required_inputs"]),
        )

        topology = _read_topology_spec(bindings["required_inputs"]["m3"]["path"])
        scenarios = _read_scenario_jsonl(bindings["required_inputs"]["m4"]["path"])

        request_path = self._temp_artifact_path("M6", "decision_requests.jsonl")
        decision_path = self._temp_artifact_path("M6", "decision_results.json")
        validation_path = self._temp_artifact_path("M7", "validation_results.json")
        stream_stats = self._write_decision_validation_artifacts(
            observation_path=bindings["required_inputs"]["m5_observation"]["path"],
            topology=topology,
            scenarios=scenarios,
            request_path=request_path,
            decision_path=decision_path,
            validation_path=validation_path,
        )

        requests_artifact = self._publish_file(
            stage_id="M6",
            file_name="decision_requests.jsonl",
            purpose="M6 decision requests built from the published M5 perturbed observation.",
            source_path=request_path,
            media_type="application/jsonl",
            schema_name="M6DecisionRequests",
            row_count=stream_stats.request_count,
        )

        decisions_artifact = self._publish_file(
            stage_id="M6",
            file_name="decision_results.json",
            purpose="M6 exploratory Rule/mock decision outputs from the same observation.",
            source_path=decision_path,
            media_type="application/json",
            schema_name="M6DecisionResults",
            row_count=stream_stats.decision_count,
        )
        gai_trace_artifacts = self._publish_gai_trace_artifacts()

        adapter_status = self._adapter_status(
            rule_decision_count=stream_stats.rule_decision_count,
            gai_status_counts=dict(stream_stats.gai_status_counts),
            gai_trace_artifacts=gai_trace_artifacts,
        )
        adapter_status_artifact = self._publish_json(
            stage_id="M6",
            file_name="decision_adapter_status.json",
            purpose="Decision adapter availability and mock/live GAI boundary.",
            payload=adapter_status,
            schema_name="M6DecisionAdapterStatus",
            row_count=None,
        )

        validations_artifact = self._publish_file(
            stage_id="M7",
            file_name="validation_results.json",
            purpose="M7 validation results using M4 scenario_gt as the only truth source.",
            source_path=validation_path,
            media_type="application/json",
            schema_name="M7ValidationResults",
            row_count=stream_stats.validation_count,
        )
        validation_summary = self._validation_summary_from_stats(stats=stream_stats)
        validation_summary_artifact = self._publish_json(
            stage_id="M7",
            file_name="validation_summary.json",
            purpose="M7 exploratory validation summary without deployment reliability claims.",
            payload=validation_summary,
            schema_name="M7ValidationSummary",
            row_count=None,
        )
        quality_artifact = self._publish_bytes(
            stage_id="M7",
            file_name="m7_quality_report.md",
            purpose="M6/M7 exploratory quality report and limitations.",
            content=self._quality_report(
                bindings=bindings,
                request_count=stream_stats.request_count,
                decision_count=stream_stats.decision_count,
                validation_count=stream_stats.validation_count,
                validation_summary=validation_summary,
            ).encode("utf-8"),
            media_type="text/markdown",
            schema_name="M7QualityReport",
            row_count=None,
        )

        self._publish_json(
            stage_id="M7",
            file_name="m6_m7_reproducibility_manifest.json",
            purpose="Replay metadata for M6/M7 exploratory decision validation.",
            payload=self._reproducibility_manifest(
                bindings=bindings,
                produced_artifacts=[
                    binding_artifact,
                    requests_artifact,
                    decisions_artifact,
                    *gai_trace_artifacts,
                    adapter_status_artifact,
                    validations_artifact,
                    validation_summary_artifact,
                    quality_artifact,
                ],
            ),
            schema_name="M5DecisionValidationReproducibilityManifest",
            row_count=None,
        )

        summary_payload = {
            "run_id": self.config.run_id,
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "m6_m7_only": True,
            "integrated_profile_enabled": False,
            "formal_output_enabled": False,
            "m6_observation_checksum": self.config.m5_observation_checksum,
            "m7_ground_truth_checksum": self.config.m4_scenario_gt_checksum,
            "rule_decision_count": stream_stats.rule_decision_count,
            "gai_status": cast(str, adapter_status["gai"]["status"]),
            "validation_result_count": stream_stats.validation_count,
            "limitations": _limitations(),
        }
        self._publish_json(
            stage_id="M7",
            file_name="m6_m7_exploratory_run_summary.json",
            purpose="Machine-readable M6/M7 exploratory run summary.",
            payload=summary_payload,
            schema_name="M5DecisionValidationRunSummary",
            row_count=None,
        )

        return M5DecisionValidationRunSummary(
            run_id=self.config.run_id,
            pipeline_profile=self.config.pipeline_profile,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=_stage_statuses(),
            m6_observation_checksum=self.config.m5_observation_checksum,
            m7_ground_truth_checksum=self.config.m4_scenario_gt_checksum,
            rule_decision_count=cast(int, summary_payload["rule_decision_count"]),
            gai_status=cast(str, summary_payload["gai_status"]),
            validation_result_count=stream_stats.validation_count,
            limitations=_limitations(),
            artifacts=self.artifacts,
        )

    def _reject_out_of_scope_run(self) -> None:
        if self.config.run_purpose == RunPurpose.FORMAL:
            raise DomainValidationError("M6/M7 exploratory decision validation blocks formal runs.")
        if self.config.formal_output_enabled:
            raise DomainValidationError("M6/M7 exploratory run cannot enable formal output.")
        if self.config.integrated_profile_enabled:
            raise DomainValidationError("two_stage_integrated_v1 remains disabled.")
        if not self.config.include_rule and self.config.gai_mode != "mock":
            raise DomainValidationError("At least one executable decision adapter is required.")
        if self.config.gai_mode == "http" and not self.config.live_gai_provider_enabled:
            raise DomainValidationError("Live GAI API is not configured for this system.")
        if self.config.gai_mode == "http" and self.gai_http_config is None:
            raise DomainValidationError("Live GAI HTTP endpoint is not configured for this system.")

    def _load_and_validate_bindings(self) -> JsonObject:
        required: dict[str, JsonObject] = {
            "m5_observation": {
                "role": "perturbed_observation_for_decision",
                "stage_id": "M5",
                "uri": self.config.m5_observation_uri,
                "checksum": self.config.m5_observation_checksum,
                "artifact_id": self.config.m5_observation_artifact_id,
                "file_name": "perturbed_observation_population.jsonl",
            },
            "m5_error_realizations": {
                "role": "error_realization_trace",
                "stage_id": "M5",
                "uri": self.config.m5_error_realizations_uri,
                "checksum": self.config.m5_error_realizations_checksum,
                "artifact_id": self.config.m5_error_realizations_artifact_id,
                "file_name": "error_realizations.parquet",
            },
            "m3": {
                "role": "topology_capacity_context",
                "stage_id": "M3",
                "uri": self.config.m3_topology_spec_uri,
                "checksum": self.config.m3_topology_spec_checksum,
                "artifact_id": self.config.m3_topology_spec_artifact_id,
                "file_name": "topology_spec.json",
            },
            "m4": {
                "role": "validation_ground_truth",
                "stage_id": "M4",
                "uri": self.config.m4_scenario_gt_uri,
                "checksum": self.config.m4_scenario_gt_checksum,
                "artifact_id": self.config.m4_scenario_gt_artifact_id,
                "file_name": "scenario_gt.jsonl",
            },
        }
        for key, item in required.items():
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
            if key == "m5_observation" and "/M5/" not in str(item["uri"]):
                raise DomainValidationError("M6 must consume a published M5 observation artifact.")

        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "m6_m7_only": True,
            "required_inputs": required,
            "truth_source_contract": {
                "m6_observation_source_stage_id": "M5",
                "m7_ground_truth_source_stage_id": "M4",
                "m7_must_not_use_observation_as_truth": True,
            },
            "forbidden_behavior": {
                "m8_m9_reporting": False,
                "r_deploy": False,
                "delta_r": False,
                "two_stage_integrated_v1": False,
                "fake_gai_api_result": False,
            },
        }

    def _write_decision_validation_artifacts(
        self,
        *,
        observation_path: str,
        topology: TopologySpec,
        scenarios: list[ScenarioRecord],
        request_path: Path,
        decision_path: Path,
        validation_path: Path,
    ) -> M6M7StreamStats:
        request_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
        node_by_id = {node.node_id: node for node in topology.nodes}
        edge_lookup = _edge_lookup(topology)
        reverse_directed_lookup = _reverse_directed_lookup(topology)
        stats = M6M7StreamStats()
        first_decision = True
        first_validation = True

        with (
            request_path.open("wb") as request_handle,
            decision_path.open("wb") as decision_handle,
            validation_path.open("wb") as validation_handle,
        ):
            _write_results_object_prefix(
                decision_handle,
                {
                    "schema_version": "1.0.0",
                    "pipeline_profile": self.config.pipeline_profile,
                    "run_id": self.config.run_id,
                    "m5_observation_checksum": self.config.m5_observation_checksum,
                    "topology_checksum": self.config.m3_topology_spec_checksum,
                    "gai_mode": self.config.gai_mode,
                },
            )
            _write_results_object_prefix(
                validation_handle,
                {
                    "schema_version": "1.0.0",
                    "pipeline_profile": self.config.pipeline_profile,
                    "run_id": self.config.run_id,
                    "ground_truth_source_stage_id": "M4",
                    "ground_truth_checksum": self.config.m4_scenario_gt_checksum,
                    "observation_checksum": self.config.m5_observation_checksum,
                },
            )
            for observation in _iter_perturbed_observation_jsonl(observation_path):
                request = self._build_request(observation)
                request_handle.write(_json_bytes(request.model_dump(mode="json")) + b"\n")
                stats.request_count += 1

                decisions = self._build_decisions(
                    topology=topology,
                    observations=[observation],
                    observation_checksum=self.config.m5_observation_checksum,
                    topology_checksum=self.config.m3_topology_spec_checksum,
                )
                for decision in decisions:
                    first_decision = _write_json_array_item(
                        decision_handle,
                        decision.model_dump(mode="json"),
                        first=first_decision,
                    )
                    stats.decision_count += 1
                    if decision.interface_type == "rule":
                        stats.rule_decision_count += 1
                    if decision.interface_type == "gai":
                        stats.gai_status_counts[decision.status] += 1

                validations = self._validate_decisions(
                    topology=topology,
                    scenarios=scenarios,
                    decisions=decisions,
                    ground_truth_checksum=self.config.m4_scenario_gt_checksum,
                    scenario_by_id=scenario_by_id,
                    node_by_id=node_by_id,
                    edge_lookup=edge_lookup,
                    reverse_directed_lookup=reverse_directed_lookup,
                )
                for validation in validations:
                    first_validation = _write_json_array_item(
                        validation_handle,
                        validation.model_dump(mode="json"),
                        first=first_validation,
                    )
                    self._accumulate_validation_stats(stats, validation)
            _write_results_object_suffix(decision_handle)
            _write_results_object_suffix(validation_handle)
        return stats

    def _build_requests(
        self,
        *,
        observations: list[PerturbedObservationRecord],
    ) -> list[DecisionRequest]:
        return [
            self._build_request(item)
            for item in sorted(
                observations,
                key=lambda row: (row.scenario_id, row.condition_id or "", row.trial_id),
            )
        ]

    def _build_request(self, item: PerturbedObservationRecord) -> DecisionRequest:
        return DecisionRequest(
            request_id=f"REQ-{item.trial_id}",
            trial_id=item.trial_id,
            scenario_id=item.scenario_id,
            error_realization_id=item.error_realization_id,
            condition_id=item.condition_id,
            dataset_id=item.dataset_id,
            model_id=item.model_id,
            paradigm=item.paradigm,
            split=item.split,
            observation_checksum=self.config.m5_observation_checksum,
            topology_checksum=self.config.m3_topology_spec_checksum,
        )

    def _build_decisions(
        self,
        *,
        topology: TopologySpec,
        observations: list[PerturbedObservationRecord],
        observation_checksum: str,
        topology_checksum: str,
    ) -> list[ExploratoryDecisionResult]:
        decisions: list[ExploratoryDecisionResult] = []
        http_adapter = self._http_gai_adapter()
        unavailable_adapter = UnavailableGaiDecisionAdapter()
        for observation in sorted(
            observations,
            key=lambda row: (row.scenario_id, row.condition_id or "", row.trial_id),
        ):
            actions = self._rule_actions(topology, observation)
            input_checksum = self._decision_input_checksum(
                observation=observation,
                observation_checksum=observation_checksum,
                topology_checksum=topology_checksum,
            )
            if self.config.include_rule:
                decisions.append(
                    ExploratoryDecisionResult(
                        decision_id=f"DEC-RULE-{observation.scenario_id}-{observation.trial_id}",
                        request_id=f"REQ-{observation.trial_id}",
                        interface_type="rule",
                        trial_id=observation.trial_id,
                        scenario_id=observation.scenario_id,
                        error_realization_id=observation.error_realization_id,
                        condition_id=observation.condition_id,
                        dataset_id=observation.dataset_id,
                        model_id=observation.model_id,
                        paradigm=observation.paradigm,
                        split=observation.split,
                        actions=actions,
                        input_checksum=input_checksum,
                        topology_checksum=topology_checksum,
                        capacity_checksum=topology_checksum,
                        observation_checksum=observation_checksum,
                        provider=None,
                        policy_id=self.config.decision_policy_id,
                        policy_version=self.config.decision_policy_version,
                    )
                )
            if self.config.gai_mode == "mock" or self.config.include_mock_gai:
                decisions.append(
                    ExploratoryDecisionResult(
                        decision_id=f"DEC-GAI-{observation.scenario_id}-{observation.trial_id}",
                        request_id=f"REQ-{observation.trial_id}",
                        interface_type="gai",
                        trial_id=observation.trial_id,
                        scenario_id=observation.scenario_id,
                        error_realization_id=observation.error_realization_id,
                        condition_id=observation.condition_id,
                        dataset_id=observation.dataset_id,
                        model_id=observation.model_id,
                        paradigm=observation.paradigm,
                        split=observation.split,
                        actions=actions,
                        input_checksum=input_checksum,
                        topology_checksum=topology_checksum,
                        capacity_checksum=topology_checksum,
                        observation_checksum=observation_checksum,
                        provider="mock_gai_capacity_relief_adapter_v1",
                        policy_id="mock_gai_capacity_relief_adapter_v1",
                        policy_version=self.config.decision_policy_version,
                    )
                )
            elif self.config.gai_mode == "http":
                gai_request = self._gai_request_payload(
                    topology=topology,
                    observation=observation,
                    input_checksum=input_checksum,
                )
                call = (
                    http_adapter.decide_with_trace(gai_request)
                    if http_adapter is not None
                    else unavailable_adapter.decide_with_trace(gai_request)
                )
                self.gai_calls.append(call)
                provider_metadata = call.result.provider_metadata
                decisions.append(
                    ExploratoryDecisionResult(
                        decision_id=call.result.decision_id,
                        request_id=f"REQ-{observation.trial_id}",
                        interface_type="gai",
                        trial_id=observation.trial_id,
                        scenario_id=observation.scenario_id,
                        error_realization_id=observation.error_realization_id,
                        condition_id=observation.condition_id,
                        dataset_id=observation.dataset_id,
                        model_id=observation.model_id,
                        paradigm=observation.paradigm,
                        split=observation.split,
                        status=cast(Any, call.result.status),
                        actions=[
                            DecisionAction(
                                action_id=action.action_id,
                                from_node=action.from_node,
                                to_node=action.to_node,
                                count=action.count,
                            )
                            for action in call.result.actions
                        ],
                        input_checksum=call.result.input_checksum,
                        topology_checksum=topology_checksum,
                        capacity_checksum=topology_checksum,
                        observation_checksum=observation_checksum,
                        provider=provider_metadata.provider if provider_metadata else None,
                        policy_id=(
                            http_adapter.policy_id
                            if http_adapter is not None
                            else unavailable_adapter.policy_id
                        ),
                        policy_version=(
                            http_adapter.policy_version
                            if http_adapter is not None
                            else unavailable_adapter.policy_version
                        ),
                    )
                )
        return decisions

    def _decision_input_checksum(
        self,
        *,
        observation: PerturbedObservationRecord,
        observation_checksum: str,
        topology_checksum: str,
    ) -> str:
        return _checksum_text(
            "|".join(
                [
                    self.config.run_id,
                    observation.trial_id,
                    observation.scenario_id,
                    observation.error_realization_id,
                    observation.condition_id or "",
                    observation.dataset_id or "",
                    observation.model_id or "",
                    observation.paradigm or "",
                    observation.split or "",
                    observation_checksum,
                    topology_checksum,
                ]
            )
        )

    def _gai_request_payload(
        self,
        *,
        topology: TopologySpec,
        observation: PerturbedObservationRecord,
        input_checksum: str,
    ) -> DecisionRequestPayload:
        return DecisionRequestPayload(
            experiment_id=self.config.pipeline_profile,
            run_id=self.config.run_id,
            request_id=f"REQ-{observation.trial_id}",
            trial_id=observation.trial_id,
            scenario_id=observation.scenario_id,
            error_realization_id=observation.error_realization_id,
            observed_population=tuple(
                entry.model_dump(mode="json") for entry in observation.observed_population
            ),
            topology=topology.model_dump(mode="json"),
            capacities={node.node_id: node.capacity for node in topology.nodes},
            allowed_action_schema=_allowed_decision_action_schema(),
            decision_policy_version=self.config.decision_policy_version,
            input_checksum=input_checksum,
        )

    def _http_gai_adapter(self) -> HttpGaiDecisionAdapter | GeminiFlashLiteDecisionAdapter | None:
        if self.config.gai_mode != "http" or self.gai_http_config is None:
            return None
        if self.gai_http_config.budget_max_requests_per_run <= 0:
            raise DomainValidationError("GAI budget blocks all provider requests for this run.")
        return create_gai_decision_adapter(config=self.gai_http_config)

    def _rule_actions(
        self,
        topology: TopologySpec,
        observation: PerturbedObservationRecord,
    ) -> list[DecisionAction]:
        node_by_id = {node.node_id: node for node in topology.nodes}
        population = {
            entry.node_id: int(round(entry.observed_population))
            for entry in observation.observed_population
        }
        incoming_reserved: dict[str, int] = defaultdict(int)
        actions: list[DecisionAction] = []
        high_entries = sorted(
            [
                entry
                for entry in observation.observed_population
                if entry.occupancy_ratio >= self.config.high_source_occupancy_threshold
                and entry.node_id in node_by_id
                and not node_by_id[entry.node_id].is_exit
                and not node_by_id[entry.node_id].is_sink
            ],
            key=lambda entry: (-entry.occupancy_ratio, _node_sort_key(entry.node_id)),
        )
        edge_candidates = _edge_candidates(topology)
        for entry in high_entries:
            source_node = node_by_id[entry.node_id]
            observed_source_population = int(round(entry.observed_population))
            desired_after = int(source_node.capacity * 0.70)
            excess = max(0, observed_source_population - desired_after)
            if excess == 0:
                continue
            for edge in edge_candidates.get(entry.node_id, []):
                target = node_by_id[edge.target]
                target_limit = int(target.capacity * self.config.target_occupancy_limit)
                current_target_population = population.get(target.node_id, 0)
                margin = (
                    target_limit - current_target_population - incoming_reserved[target.node_id]
                )
                if margin <= 0:
                    continue
                move_count = min(excess, margin)
                if move_count <= 0:
                    continue
                actions.append(
                    DecisionAction(
                        action_id=f"A-{observation.trial_id}-{len(actions) + 1:03d}",
                        from_node=entry.node_id,
                        to_node=target.node_id,
                        count=move_count,
                    )
                )
                incoming_reserved[target.node_id] += move_count
                break
        return actions

    def _adapter_status(
        self,
        *,
        rule_decision_count: int,
        gai_status_counts: dict[str, int],
        gai_trace_artifacts: list[ArtifactRecord],
    ) -> JsonObject:
        gai_decision_count = sum(gai_status_counts.values())

        if self.config.gai_mode == "mock" or self.config.include_mock_gai:
            gai_status: JsonObject = {
                "status": "mock",
                "provider": "mock_gai_capacity_relief_adapter_v1",
                "formal_eligible": False,
                "live_provider": False,
                "decision_count": gai_decision_count,
                "status_counts": dict(sorted(gai_status_counts.items())),
                "message": "Mock GAI is exploratory only and is not a live GAI API result.",
            }
        elif self.config.gai_mode == "http":
            parsed_count = gai_status_counts.get("parsed", 0)
            terminal_status = "available" if parsed_count == gai_decision_count else "error"
            if gai_decision_count == 0:
                terminal_status = "blocked"
            gai_status = {
                "status": terminal_status,
                "provider": self.gai_http_config.provider if self.gai_http_config else "http",
                "model": self.gai_http_config.model if self.gai_http_config else None,
                "formal_eligible": False,
                "live_provider": self.gai_http_config is not None,
                "decision_count": gai_decision_count,
                "status_counts": dict(sorted(gai_status_counts.items())),
                "trace_artifacts": [
                    {
                        "file_name": artifact.file_name,
                        "uri": artifact.uri,
                        "checksum": artifact.checksum,
                    }
                    for artifact in gai_trace_artifacts
                ],
                "message": (
                    "Live GAI HTTP adapter was called for exploratory evidence only."
                    if self.gai_http_config is not None
                    else "Live GAI HTTP adapter is not configured."
                ),
            }
        else:
            gai_status = {
                "status": "unavailable",
                "provider": None,
                "formal_eligible": False,
                "live_provider": False,
                "decision_count": 0,
                "status_counts": {},
                "message": "GAI API is not available; no fake GAI decision was produced.",
            }
        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_id": self.config.run_id,
            "rule": {
                "status": "available" if self.config.include_rule else "disabled",
                "policy_id": self.config.decision_policy_id,
                "decision_count": rule_decision_count,
            },
            "gai": gai_status,
            "same_input_contract": {
                "observation_checksum": self.config.m5_observation_checksum,
                "topology_checksum": self.config.m3_topology_spec_checksum,
                "capacity_checksum": self.config.m3_topology_spec_checksum,
            },
        }

    def _validate_decisions(
        self,
        *,
        topology: TopologySpec,
        scenarios: list[ScenarioRecord],
        decisions: list[ExploratoryDecisionResult],
        ground_truth_checksum: str,
        scenario_by_id: dict[str, ScenarioRecord] | None = None,
        node_by_id: dict[str, TopologyNode] | None = None,
        edge_lookup: dict[tuple[str, str], TopologyEdge] | None = None,
        reverse_directed_lookup: set[tuple[str, str]] | None = None,
    ) -> list[ExploratoryValidationResult]:
        scenario_by_id = scenario_by_id or {
            scenario.scenario_id: scenario for scenario in scenarios
        }
        node_by_id = node_by_id or {node.node_id: node for node in topology.nodes}
        edge_lookup = edge_lookup or _edge_lookup(topology)
        reverse_directed_lookup = reverse_directed_lookup or _reverse_directed_lookup(topology)
        validations: list[ExploratoryValidationResult] = []
        for decision in decisions:
            scenario = scenario_by_id.get(decision.scenario_id)
            if scenario is None:
                raise DomainValidationError(
                    f"M7 cannot find M4 ground truth for scenario {decision.scenario_id}."
                )
            population = {
                entry.node_id: entry.ground_truth_population
                for entry in scenario.zone_counts
            }
            occupancy_ratio = {
                entry.node_id: entry.occupancy_ratio
                for entry in scenario.zone_counts
            }
            violations: list[ValidationViolation] = []
            outgoing: dict[str, int] = defaultdict(int)
            incoming: dict[str, int] = defaultdict(int)
            seen_actions: set[tuple[str, str]] = set()
            if decision.status != "parsed":
                violations.append(
                    ValidationViolation(
                        code=_decision_status_violation_code(decision.status),
                        message_zh_tw="decision output 無法解析或不可用，不能視為有效疏散建議。",
                    )
                )
            for action in decision.actions:
                if (action.from_node, action.to_node) in seen_actions:
                    violations.append(
                        ValidationViolation(
                            code="DUPLICATE_ACTION",
                            message_zh_tw="同一來源與目的地出現重複 action。",
                            action_id=action.action_id,
                        )
                    )
                seen_actions.add((action.from_node, action.to_node))
                if action.from_node not in node_by_id:
                    violations.append(
                        ValidationViolation(
                            code="UNKNOWN_SOURCE_NODE",
                            message_zh_tw="action 來源節點不存在。",
                            action_id=action.action_id,
                            node_id=action.from_node,
                        )
                    )
                    continue
                if action.to_node not in node_by_id:
                    violations.append(
                        ValidationViolation(
                            code="UNKNOWN_TARGET_NODE",
                            message_zh_tw="action 目的節點不存在。",
                            action_id=action.action_id,
                            node_id=action.to_node,
                        )
                    )
                    continue
                edge = edge_lookup.get((action.from_node, action.to_node))
                if edge is None:
                    code = (
                        "INVALID_DIRECTION"
                        if (action.from_node, action.to_node) in reverse_directed_lookup
                        else "INVALID_EDGE"
                    )
                    violations.append(
                        ValidationViolation(
                            code=code,
                            message_zh_tw="來源與目的節點之間沒有合法拓樸邊或方向。",
                            action_id=action.action_id,
                        )
                    )
                elif edge.edge_capacity is not None and action.count > edge.edge_capacity:
                    violations.append(
                        ValidationViolation(
                            code="EDGE_CAPACITY_EXCEEDED",
                            message_zh_tw="移動人數超過邊容量。",
                            edge_id=edge.edge_id,
                            action_id=action.action_id,
                        )
                    )
                outgoing[action.from_node] += action.count
                incoming[action.to_node] += action.count

            for source, out_count in outgoing.items():
                if out_count > population.get(source, 0):
                    violations.append(
                        ValidationViolation(
                            code="SOURCE_UNDERFLOW",
                            message_zh_tw="來源移出人數超過 M4 Ground Truth 人數。",
                            node_id=source,
                        )
                    )

            post_total = 0
            original_total = sum(population.values())
            for node_id, node in node_by_id.items():
                post_occupancy = population.get(node_id, 0) + incoming[node_id] - outgoing[node_id]
                post_total += post_occupancy
                if post_occupancy > node.capacity:
                    violations.append(
                        ValidationViolation(
                            code="CAPACITY_EXCEEDED",
                            message_zh_tw=(
                                f"移動後人數超過區域容量 {post_occupancy - node.capacity} 人。"
                            ),
                            node_id=node_id,
                            capacity=node.capacity,
                            post_occupancy=post_occupancy,
                            exceeded_by=post_occupancy - node.capacity,
                        )
                    )
            if post_total != original_total:
                violations.append(
                    ValidationViolation(
                        code="FLOW_CONSERVATION_FAILED",
                        message_zh_tw="移動前後總人數不一致。",
                    )
                )

            expected = sorted(
                [
                    node_id
                    for node_id, ratio in occupancy_ratio.items()
                    if ratio >= self.config.high_source_occupancy_threshold
                    and node_id in node_by_id
                    and not node_by_id[node_id].is_exit
                    and not node_by_id[node_id].is_sink
                ],
                key=_node_sort_key,
            )
            recommended = sorted(
                {action.from_node for action in decision.actions},
                key=_node_sort_key,
            )
            tp = len(set(expected) & set(recommended))
            fp = len(set(recommended) - set(expected))
            fn = len(set(expected) - set(recommended))
            fatal = any(
                violation.code
                in {
                    "UNKNOWN_SOURCE_NODE",
                    "UNKNOWN_TARGET_NODE",
                    "INVALID_EDGE",
                    "INVALID_DIRECTION",
                    "NON_POSITIVE_FLOW",
                    "SOURCE_UNDERFLOW",
                    "CAPACITY_EXCEEDED",
                    "EDGE_CAPACITY_EXCEEDED",
                    "FLOW_CONSERVATION_FAILED",
                    "DECISION_OUTPUT_INVALID",
                    "DECISION_OUTPUT_UNAVAILABLE",
                    "DECISION_OUTPUT_TIMEOUT",
                    "DECISION_OUTPUT_ERROR",
                }
                for violation in violations
            )
            legality_score = 0.0 if fatal else 1.0
            priority_score = _safe_ratio(tp, tp + fn, zero_when_empty=1.0)
            source_target_counts: dict[str, set[str]] = defaultdict(set)
            for action in decision.actions:
                source_target_counts[action.from_node].add(action.to_node)
            economy_ok = all(
                len(targets) <= self.config.max_targets_per_source
                for targets in source_target_counts.values()
            )
            economy_score = 1.0 if economy_ok else 0.0
            weighted = 0.50 * legality_score + 0.35 * priority_score + 0.15 * economy_score
            action_consistency = 0.0 if fatal and legality_score == 0.0 else weighted
            validations.append(
                ExploratoryValidationResult(
                    validation_id=f"VAL-{decision.decision_id}",
                    decision_id=decision.decision_id,
                    interface_type=decision.interface_type,
                    trial_id=decision.trial_id,
                    scenario_id=decision.scenario_id,
                    error_realization_id=decision.error_realization_id,
                    condition_id=decision.condition_id,
                    dataset_id=decision.dataset_id,
                    model_id=decision.model_id,
                    paradigm=decision.paradigm,
                    split=decision.split,
                    valid=not violations,
                    validator_policy_id=self.config.validator_policy_id,
                    validator_policy_version=self.config.validator_policy_version,
                    ground_truth_checksum=ground_truth_checksum,
                    observation_checksum=decision.observation_checksum,
                    violations=violations,
                    legality_score=legality_score,
                    priority_score=priority_score,
                    economy_score=economy_score,
                    fatal_legality_gate=fatal,
                    action_consistency_score=action_consistency,
                    expected_high_sources=expected,
                    recommended_sources=recommended,
                    risk_tp=tp,
                    risk_fp=fp,
                    risk_fn=fn,
                )
            )
        return validations

    def _validation_summary(
        self,
        *,
        validations: list[ExploratoryValidationResult],
    ) -> JsonObject:
        violation_counts: dict[str, int] = defaultdict(int)
        condition_counts: dict[str, int] = defaultdict(int)
        for result in validations:
            condition_counts[result.condition_id or "unscoped"] += 1
            for violation in result.violations:
                violation_counts[violation.code] += 1
        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_id": self.config.run_id,
            "m6_m7_only": True,
            "ground_truth_source_stage_id": "M4",
            "ground_truth_checksum": self.config.m4_scenario_gt_checksum,
            "observation_source_stage_id": "M5",
            "observation_checksum": self.config.m5_observation_checksum,
            "result_count": len(validations),
            "valid_count": sum(1 for item in validations if item.valid),
            "invalid_count": sum(1 for item in validations if not item.valid),
            "condition_counts": dict(sorted(condition_counts.items())),
            "violation_counts": dict(sorted(violation_counts.items())),
            "metrics_not_computed": ["R_deploy", "Delta_R", "integrated_reliability"],
        }

    def _validation_summary_from_stats(self, *, stats: M6M7StreamStats) -> JsonObject:
        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_id": self.config.run_id,
            "m6_m7_only": True,
            "ground_truth_source_stage_id": "M4",
            "ground_truth_checksum": self.config.m4_scenario_gt_checksum,
            "observation_source_stage_id": "M5",
            "observation_checksum": self.config.m5_observation_checksum,
            "result_count": stats.validation_count,
            "valid_count": stats.valid_count,
            "invalid_count": stats.invalid_count,
            "condition_counts": dict(sorted(stats.condition_counts.items())),
            "violation_counts": dict(sorted(stats.violation_counts.items())),
            "condition_rollups": [
                {
                    **rollup,
                    "violation_counts": dict(sorted(rollup["violation_counts"].items())),
                }
                for _, rollup in sorted(stats.condition_rollups.items())
            ],
            "metrics_not_computed": ["R_deploy", "Delta_R", "integrated_reliability"],
        }

    def _accumulate_validation_stats(
        self,
        stats: M6M7StreamStats,
        validation: ExploratoryValidationResult,
    ) -> None:
        stats.validation_count += 1
        stats.condition_counts[validation.condition_id or "unscoped"] += 1
        if validation.valid:
            stats.valid_count += 1
        else:
            stats.invalid_count += 1
        key = (
            validation.interface_type,
            validation.condition_id or "unscoped",
            validation.dataset_id or "unscoped",
            validation.model_id or "unscoped",
            validation.paradigm or "unscoped",
            validation.split or "unscoped",
        )
        rollup = stats.condition_rollups.setdefault(
            key,
            {
                "interface_type": validation.interface_type,
                "condition_id": validation.condition_id or "unscoped",
                "dataset_id": validation.dataset_id or "unscoped",
                "model_id": validation.model_id or "unscoped",
                "paradigm": validation.paradigm or "unscoped",
                "split": validation.split or "unscoped",
                "result_count": 0,
                "valid_count": 0,
                "invalid_count": 0,
                "action_consistency_sum": 0.0,
                "action_consistency_count": 0,
                "risk_tp": 0,
                "risk_fp": 0,
                "risk_fn": 0,
                "violation_counts": defaultdict(int),
            },
        )
        rollup["result_count"] += 1
        rollup["valid_count"] += 1 if validation.valid else 0
        rollup["invalid_count"] += 0 if validation.valid else 1
        rollup["action_consistency_sum"] += validation.action_consistency_score
        rollup["action_consistency_count"] += 1
        rollup["risk_tp"] += validation.risk_tp
        rollup["risk_fp"] += validation.risk_fp
        rollup["risk_fn"] += validation.risk_fn
        for violation in validation.violations:
            stats.violation_counts[violation.code] += 1
            rollup["violation_counts"][violation.code] += 1

    def _quality_report(
        self,
        *,
        bindings: JsonObject,
        request_count: int,
        decision_count: int,
        validation_count: int,
        validation_summary: JsonObject,
    ) -> str:
        lines = [
            "# M6/M7 Exploratory Decision Validation Quality Report",
            "",
            "Status: exploratory M6/M7 only.",
            "",
            "This run validates decisions produced from M5 perturbed observation against M4 "
            "scenario_gt. It does not compute M8/M9 metrics, R_deploy or Delta_R.",
            "",
            "## Inputs",
            "",
        ]
        for key in ("m5_observation", "m5_error_realizations", "m3", "m4"):
            item = cast(JsonObject, bindings["required_inputs"][key])
            lines.append(f"- {key}: `{item['uri']}` `{item['checksum']}`")
        lines.extend(
            [
                "",
                "## Counts",
                "",
                f"- Decision requests: {request_count}",
                f"- Decision results: {decision_count}",
                f"- Validation results: {validation_count}",
                f"- Invalid validation results: {validation_summary['invalid_count']}",
                "",
                "## Truth Source",
                "",
                "- M6 observation source: M5 perturbed_observation_population.",
                "- M7 validation truth source: M4 scenario_gt only.",
                "",
                "## Explicit Limits",
                "",
            ]
        )
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
            "determinism": {
                "rule_decision_order": [
                    "scenario_id",
                    "trial_id",
                    "occupancy_ratio_desc",
                    "node_id",
                    "edge_hops",
                    "edge_travel_cost",
                    "target_node_id",
                ],
                "uses_randomness": False,
            },
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

    def _publish_gai_trace_artifacts(self) -> list[ArtifactRecord]:
        if not self.gai_calls:
            return []
        request_artifact = self._publish_bytes(
            stage_id="M6",
            file_name="gai_decision_requests.jsonl",
            purpose="Canonical live GAI request payloads without secrets or ground truth fields.",
            content=_jsonl_bytes([call.request_payload for call in self.gai_calls]),
            media_type="application/jsonl",
            schema_name="M6GaiDecisionRequests",
            row_count=len(self.gai_calls),
        )
        raw_artifact = self._publish_bytes(
            stage_id="M6",
            file_name="gai_decision_raw_responses.jsonl",
            purpose="Raw live GAI provider responses or terminal transport errors.",
            content=_jsonl_bytes([call.raw_response_payload for call in self.gai_calls]),
            media_type="application/jsonl",
            schema_name="M6GaiDecisionRawResponses",
            row_count=len(self.gai_calls),
        )
        parsed_artifact = self._publish_bytes(
            stage_id="M6",
            file_name="gai_decision_parsed_responses.jsonl",
            purpose="Parsed canonical GAI decisions and invalid-output status records.",
            content=_jsonl_bytes([call.parsed_response_payload for call in self.gai_calls]),
            media_type="application/jsonl",
            schema_name="M6GaiDecisionParsedResponses",
            row_count=len(self.gai_calls),
        )
        return [request_artifact, raw_artifact, parsed_artifact]
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

    def _publish_file(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        source_path: Path,
        media_type: str,
        schema_name: str,
        row_count: int | None,
    ) -> ArtifactRecord:
        staged = self.artifact_store.stage_file(
            relative_path=f"runs/{self.config.run_id}/{stage_id}/{file_name}",
            source_path=source_path,
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

    def _temp_artifact_path(self, stage_id: str, file_name: str) -> Path:
        safe_run_id = _safe_token(self.config.run_id)
        path = self.artifact_root / "tmp" / "build" / safe_run_id / stage_id / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def _stage_statuses() -> list[StageStatusRecord]:
    output = [StageStatusRecord(stage_id="M0", status="succeeded")]
    for stage_id in ("M1", "M2", "M3", "M4", "M5"):
        output.append(
            StageStatusRecord(
                stage_id=stage_id,
                status="not_required",
                reason="M6/M7 exploratory run consumes published upstream artifacts.",
            )
        )
    output.append(StageStatusRecord(stage_id="M6", status="succeeded"))
    output.append(StageStatusRecord(stage_id="M7", status="succeeded"))
    for stage_id in ("M8", "M9"):
        output.append(
            StageStatusRecord(
                stage_id=stage_id,
                status="not_required",
                reason="Integrated metrics and reporting are not enabled.",
            )
        )
    return output


def _limitations() -> list[str]:
    return [
        "This profile is exploratory M6/M7 decision validation only.",
        "M6 consumes a published M5 perturbed observation; it does not rerun M5.",
        "M7 validates with M4 scenario_gt only and must not treat M5 observation as truth.",
        "Rule adapter is executable; GAI is unavailable unless explicitly set to mock.",
        "No M8/M9 metrics, R_deploy, Delta_R or integrated reliability are produced.",
        "Formal run purpose remains blocked.",
    ]


def _allowed_decision_action_schema() -> JsonObject:
    return {
        "type": "object",
        "required": ["actions"],
        "additionalProperties": True,
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["from_node", "to_node", "count"],
                    "additionalProperties": True,
                    "properties": {
                        "action_id": {"type": "string"},
                        "from_node": {"type": "string"},
                        "to_node": {"type": "string"},
                        "count": {"type": "integer", "minimum": 1},
                    },
                },
            },
            "input_tokens": {"type": "integer", "minimum": 0},
            "output_tokens": {"type": "integer", "minimum": 0},
        },
    }


def _iter_perturbed_observation_jsonl(path: str) -> Iterator[PerturbedObservationRecord]:
    found = False
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            data = json.loads(line)
            if not isinstance(data, dict):
                raise DomainValidationError("M5 observation JSONL must contain JSON objects.")
            found = True
            yield PerturbedObservationRecord.model_validate(data)
    if not found:
        raise DomainValidationError("M5 observation JSONL must contain at least one record.")


def _read_perturbed_observation_jsonl(path: str) -> list[PerturbedObservationRecord]:
    records: list[PerturbedObservationRecord] = []
    for record in _iter_perturbed_observation_jsonl(path):
        records.append(record)
    if not records:
        raise DomainValidationError("M5 observation JSONL must contain at least one record.")
    return records


def _read_topology_spec(path: str) -> TopologySpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DomainValidationError("M3 topology_spec.json must contain a JSON object.")
    return TopologySpec.model_validate(data)


def _read_scenario_jsonl(path: str) -> list[ScenarioRecord]:
    scenarios: list[ScenarioRecord] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        data = json.loads(line)
        if not isinstance(data, dict):
            raise DomainValidationError("M4 scenario_gt.jsonl must contain JSON objects.")
        scenarios.append(ScenarioRecord.model_validate(data))
    if not scenarios:
        raise DomainValidationError("M4 scenario_gt.jsonl must contain at least one scenario.")
    return scenarios


def _artifact_uri_to_path(artifact_root: Path, uri: str) -> Path:
    if not uri.startswith("artifact://"):
        raise DomainValidationError(f"Unsupported artifact URI for M6/M7 input: {uri}")
    relative = uri.removeprefix("artifact://")
    if Path(relative).is_absolute():
        raise DomainValidationError("Artifact URI must be relative to artifact root.")
    target = (artifact_root / "published" / relative).resolve()
    try:
        target.relative_to((artifact_root / "published").resolve())
    except ValueError as exc:
        raise DomainValidationError(f"Artifact URI path traversal rejected: {uri}") from exc
    return target


def _edge_candidates(topology: TopologySpec) -> dict[str, list[TopologyEdge]]:
    candidates: dict[str, list[TopologyEdge]] = defaultdict(list)
    for edge in topology.edges:
        if not edge.enabled:
            continue
        candidates[edge.source].append(edge)
        if not edge.directed:
            candidates[edge.target].append(
                TopologyEdge(
                    edge_id=edge.edge_id,
                    source=edge.target,
                    target=edge.source,
                    directed=edge.directed,
                    enabled=edge.enabled,
                    hops=edge.hops,
                    travel_cost=edge.travel_cost,
                    edge_capacity=edge.edge_capacity,
                    edge_type=edge.edge_type,
                )
            )
    for source, edges in candidates.items():
        candidates[source] = sorted(
            edges,
            key=lambda edge: (edge.hops, edge.travel_cost, _node_sort_key(edge.target)),
        )
    return candidates


def _edge_lookup(topology: TopologySpec) -> dict[tuple[str, str], TopologyEdge]:
    lookup: dict[tuple[str, str], TopologyEdge] = {}
    for edge in topology.edges:
        if not edge.enabled:
            continue
        lookup[(edge.source, edge.target)] = edge
        if not edge.directed:
            lookup[(edge.target, edge.source)] = edge
    return lookup


def _reverse_directed_lookup(topology: TopologySpec) -> set[tuple[str, str]]:
    lookup: set[tuple[str, str]] = set()
    for edge in topology.edges:
        if edge.enabled and edge.directed:
            lookup.add((edge.target, edge.source))
    return lookup


def _decision_status_violation_code(status: str) -> str:
    if status == "invalid_output":
        return "DECISION_OUTPUT_INVALID"
    if status == "unavailable":
        return "DECISION_OUTPUT_UNAVAILABLE"
    if status == "timeout":
        return "DECISION_OUTPUT_TIMEOUT"
    return "DECISION_OUTPUT_ERROR"

def _safe_ratio(numerator: int, denominator: int, *, zero_when_empty: float = 0.0) -> float:
    if denominator == 0:
        return zero_when_empty
    return numerator / denominator


def _node_sort_key(node_id: str) -> tuple[str, int]:
    prefix = "".join(ch for ch in node_id if not ch.isdigit())
    digits = "".join(ch for ch in node_id if ch.isdigit())
    return prefix, int(digits) if digits else 0


def _safe_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    token = "-".join(part for part in token.split("-") if part)
    return token or "unknown"


def _write_results_object_prefix(handle: Any, metadata: JsonObject) -> None:
    handle.write(_json_bytes(metadata)[:-1] + b',"results":[')


def _write_json_array_item(handle: Any, payload: JsonObject, *, first: bool) -> bool:
    if not first:
        handle.write(b",")
    handle.write(_json_bytes(payload))
    return False


def _write_results_object_suffix(handle: Any) -> None:
    handle.write(b"]}")


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _jsonl_bytes(rows: list[JsonObject]) -> bytes:
    return b"".join(_json_bytes(row) + b"\n" for row in rows)


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _checksum_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
