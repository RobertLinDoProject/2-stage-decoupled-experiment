from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from two_stage.application.dto.m5 import (
    M5ErrorRealizationRow,
    M5ExploratoryPipelineConfig,
    M5ExploratoryRunSummary,
    M5ObservedPopulationEntry,
    M5PerturbedObservationRecord,
)
from two_stage.application.dto.stage1 import ErrorSampleRow
from two_stage.application.dto.stage2 import (
    ArtifactRecord,
    ScenarioRecord,
    StageStatusRecord,
    TopologySpec,
)
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.enums import RunPurpose
from two_stage.domain.errors import DomainValidationError
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore

JsonObject = dict[str, Any]
ConditionKey = tuple[str, str, str, str]
PoolKey = tuple[str, ...]

NOT_REQUIRED_STAGES = ("M1", "M2", "M3", "M4", "M6", "M7", "M8", "M9")
SUPPORTED_ASSIGNMENT_STRATEGIES = {
    "nodewise_regime_conditioned_v0_1",
    "zonewise_regime_conditioned_v0_1",
}
PARQUET_BATCH_SIZE = 5_000


@dataclass(slots=True)
class M5AssignmentStats:
    row_count: int = 0
    capacity_flags: int = 0
    invalid_count: int = 0
    adjusted_count: int = 0


class M5ControlledPerturbationRunner:
    """Builds exploratory controlled perturbed observations from published artifacts."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        config: M5ExploratoryPipelineConfig,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.config = config
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.artifacts: list[ArtifactRecord] = []

    def run(self) -> M5ExploratoryRunSummary:
        self._reject_out_of_scope_run()

        bindings = self._load_and_validate_bindings()
        binding_artifact = self._publish_json(
            stage_id="M0",
            file_name="m5_input_binding_manifest.json",
            purpose="M5 exploratory upstream artifact binding and preflight result.",
            payload=bindings,
            schema_name="M5InputBindingManifest",
            row_count=len(bindings["required_inputs"]),
        )

        error_samples = _read_error_samples_parquet(bindings["required_inputs"]["m2"]["path"])
        topology = _read_topology_spec(bindings["required_inputs"]["m3"]["path"])
        scenarios = _read_scenario_jsonl(bindings["required_inputs"]["m4"]["path"])
        eligible_node_ids = _population_eligible_node_ids(topology)
        if not eligible_node_ids:
            raise DomainValidationError(
                "M5 requires at least one population-eligible topology node."
            )

        filtered_samples = self._filter_error_samples(error_samples)
        pools, eligible_conditions, skipped_conditions = self._build_residual_pools(
            filtered_samples,
            scenarios=scenarios,
            eligible_node_ids=eligible_node_ids,
        )
        assignment_path = self._temp_artifact_path("M5", "error_realizations.parquet")
        assignment_stats = self._write_assignments_parquet(
            target_path=assignment_path,
            assignments=self._iter_assignments(
                pools=pools,
                conditions=eligible_conditions,
                scenarios=scenarios,
                eligible_node_ids=eligible_node_ids,
            ),
        )
        error_artifact = self._publish_file(
            stage_id="M5",
            file_name="error_realizations.parquet",
            purpose="Exploratory residual assignments from M2 empirical residual samples.",
            source_path=assignment_path,
            media_type="application/vnd.apache.parquet",
            schema_name="M5ErrorRealizations",
            row_count=assignment_stats.row_count,
        )

        observation_path = self._temp_artifact_path("M5", "perturbed_observation_population.jsonl")
        observation_count = self._write_observations_jsonl(
            target_path=observation_path,
            assignments=self._iter_assignments(
                pools=pools,
                conditions=eligible_conditions,
                scenarios=scenarios,
                eligible_node_ids=eligible_node_ids,
            ),
            error_realization_artifact_id=error_artifact.artifact_id,
        )
        observation_artifact = self._publish_file(
            stage_id="M5",
            file_name="perturbed_observation_population.jsonl",
            purpose="Exploratory controlled perturbed observation for future M6 input.",
            source_path=observation_path,
            media_type="application/jsonl",
            schema_name="M5PerturbedObservationPopulation",
            row_count=observation_count,
        )

        quality_report = self._quality_report(
            bindings=bindings,
            source_sample_count=len(error_samples),
            filtered_sample_count=len(filtered_samples),
            eligible_conditions=eligible_conditions,
            skipped_conditions=skipped_conditions,
            assignment_stats=assignment_stats,
            observation_count=observation_count,
        )
        quality_artifact = self._publish_bytes(
            stage_id="M5",
            file_name="m5_quality_report.md",
            purpose="Exploratory M5 quality report and limitations.",
            content=quality_report.encode("utf-8"),
            media_type="text/markdown",
            schema_name="M5QualityReport",
            row_count=None,
        )

        sampling_manifest = self._sampling_manifest(
            bindings=bindings,
            filtered_sample_count=len(filtered_samples),
            pools=pools,
            eligible_conditions=eligible_conditions,
            skipped_conditions=skipped_conditions,
            assignment_row_count=assignment_stats.row_count,
            error_artifact=error_artifact,
            observation_artifact=observation_artifact,
        )
        manifest_artifact = self._publish_json(
            stage_id="M5",
            file_name="m5_sampling_manifest.json",
            purpose="Seed, filters, policy choices and output checksums for M5 exploratory run.",
            payload=sampling_manifest,
            schema_name="M5SamplingManifest",
            row_count=None,
        )

        self._publish_json(
            stage_id="M5",
            file_name="m5_reproducibility_manifest.json",
            purpose="Replay metadata for M5 exploratory controlled perturbation.",
            payload=self._reproducibility_manifest(
                bindings=bindings,
                produced_artifacts=[
                    binding_artifact,
                    error_artifact,
                    observation_artifact,
                    quality_artifact,
                    manifest_artifact,
                ],
            ),
            schema_name="M5ReproducibilityManifest",
            row_count=None,
        )

        summary_payload = {
            "run_id": self.config.run_id,
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "m5_only": True,
            "integrated_profile_enabled": False,
            "formal_output_enabled": False,
            "controlled_error_trials": observation_count,
            "assignment_row_count": assignment_stats.row_count,
            "condition_count": len(eligible_conditions),
            "skipped_condition_count": len(skipped_conditions),
            "m5_error_realizations_checksum": error_artifact.checksum,
            "m5_observation_checksum": observation_artifact.checksum,
            "limitations": _limitations(),
        }
        self._publish_json(
            stage_id="M5",
            file_name="m5_exploratory_run_summary.json",
            purpose="Machine-readable M5 exploratory run summary.",
            payload=summary_payload,
            schema_name="M5ExploratoryRunSummary",
            row_count=None,
        )

        return M5ExploratoryRunSummary(
            run_id=self.config.run_id,
            pipeline_profile=self.config.pipeline_profile,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=_stage_statuses(),
            m5_error_realizations_checksum=error_artifact.checksum,
            m5_observation_checksum=observation_artifact.checksum,
            controlled_error_trials=observation_count,
            assignment_row_count=assignment_stats.row_count,
            condition_count=len(eligible_conditions),
            skipped_condition_count=len(skipped_conditions),
            limitations=_limitations(),
            artifacts=self.artifacts,
        )

    def _reject_out_of_scope_run(self) -> None:
        if self.config.run_purpose == RunPurpose.FORMAL:
            raise DomainValidationError("M5 exploratory perturbation blocks formal runs.")
        if self.config.formal_output_enabled:
            raise DomainValidationError("M5 exploratory run cannot enable formal output.")
        if self.config.integrated_profile_enabled:
            raise DomainValidationError("two_stage_integrated_v1 remains disabled.")
        if not self.config.exploratory_policy_override:
            raise DomainValidationError(
                "M5 exploratory run requires explicit exploratory override."
            )
        if self.config.residual_assignment_strategy not in SUPPORTED_ASSIGNMENT_STRATEGIES:
            raise DomainValidationError(
                "Only nodewise or zonewise regime-conditioned assignment is executable in "
                "the exploratory M5 runner."
            )

    def _load_and_validate_bindings(self) -> JsonObject:
        required: dict[str, JsonObject] = {
            "m2": {
                "role": "empirical_residual_pool",
                "stage_id": "M2",
                "uri": self.config.m2_error_samples_uri,
                "checksum": self.config.m2_error_samples_checksum,
                "artifact_id": self.config.m2_error_samples_artifact_id,
                "file_name": "error_samples.parquet",
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
                "role": "deployment_ground_truth",
                "stage_id": "M4",
                "uri": self.config.m4_scenario_gt_uri,
                "checksum": self.config.m4_scenario_gt_checksum,
                "artifact_id": self.config.m4_scenario_gt_artifact_id,
                "file_name": "scenario_gt.jsonl",
            },
        }
        for item in required.values():
            uri = str(item["uri"])
            if "/M6/" in uri or uri.endswith("/observation_population.jsonl"):
                raise DomainValidationError("M5 must not use M6 ideal observation as input.")
            path = _artifact_uri_to_path(self.artifact_root, uri)
            if not path.exists():
                raise DomainValidationError(f"Published input artifact is missing: {uri}")
            checksum = _checksum_file(path)
            if checksum != item["checksum"]:
                raise DomainValidationError(
                    f"Checksum mismatch for {uri}: expected {item['checksum']}, got {checksum}"
                )
            item["path"] = str(path)
            item["byte_size"] = path.stat().st_size

        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "exploratory_only": True,
            "required_inputs": required,
            "prohibited_inputs_checked": [
                "raw_perception_package",
                "stage1_m1_perception_results_direct_read",
                "stage2_m6_ideal_observation",
                "synthetic_perception_noise",
                "epsilon_zero_fake_baseline",
            ],
            "policy_snapshot": {
                "paper_aligned_policy_id": self.config.paper_aligned_policy_id,
                "snapshot_id": self.config.m5_policy_bundle_snapshot_id,
                "snapshot_hash": self.config.m5_policy_bundle_snapshot_hash,
                "formal_output_enabled": False,
                "integrated_profile_enabled": False,
            },
        }

    def _filter_error_samples(self, rows: list[ErrorSampleRow]) -> list[ErrorSampleRow]:
        filtered = [
            row
            for row in rows
            if _matches_optional(row.dataset_id, self.config.residual_dataset_id)
            and _matches_optional(row.model_id, self.config.residual_model_id)
            and _matches_optional(row.paradigm, self.config.residual_paradigm)
            and row.split == self.config.residual_split
        ]
        if not filtered:
            raise DomainValidationError(
                "No empirical residual samples match the selected M5 residual pool filters."
            )
        return sorted(
            filtered,
            key=lambda row: (
                row.dataset_id,
                row.model_id,
                row.paradigm,
                row.split,
                row.ground_truth_regime,
                row.sample_id,
                row.error,
            ),
        )

    def _build_residual_pools(
        self,
        rows: list[ErrorSampleRow],
        *,
        scenarios: list[ScenarioRecord],
        eligible_node_ids: set[str],
    ) -> tuple[dict[PoolKey, list[ErrorSampleRow]], list[ConditionKey], list[JsonObject]]:
        pools: dict[PoolKey, list[ErrorSampleRow]] = defaultdict(list)
        all_conditions: set[ConditionKey] = set()
        for row in rows:
            condition = _condition_key(row)
            all_conditions.add(condition)
            pools[self._pool_key_for_sample(row)].append(row)

        required_suffixes = self._required_pool_suffixes(
            scenarios=scenarios,
            eligible_node_ids=eligible_node_ids,
        )
        eligible_conditions: list[ConditionKey] = []
        skipped_conditions: list[JsonObject] = []
        for condition in sorted(all_conditions):
            missing_or_sparse: list[JsonObject] = []
            for suffix in required_suffixes:
                key = (*condition, *suffix)
                pool_size = len(pools.get(key, []))
                if pool_size < self.config.minimum_pool_size:
                    missing_or_sparse.append(
                        {
                            "pool_key": "|".join(key),
                            "available_samples": pool_size,
                            "minimum_pool_size": self.config.minimum_pool_size,
                        }
                    )
            if missing_or_sparse:
                skipped = {
                    "condition_id": _condition_id(condition),
                    "condition_label": _condition_label(condition),
                    "dataset_id": condition[0],
                    "model_id": condition[1],
                    "paradigm": condition[2],
                    "split": condition[3],
                    "reason": "insufficient_empirical_residual_pool",
                    "missing_or_sparse_pools": missing_or_sparse,
                }
                if self.config.insufficient_pool_policy == "fail_run":
                    raise DomainValidationError(
                        "M5 residual condition has insufficient empirical samples: "
                        f"{skipped['condition_label']}."
                    )
                skipped_conditions.append(skipped)
                continue
            eligible_conditions.append(condition)

        if not eligible_conditions:
            raise DomainValidationError(
                "No M5 residual condition has enough empirical samples for the selected "
                "scenario regimes."
            )
        return dict(pools), eligible_conditions, skipped_conditions

    def _required_pool_suffixes(
        self,
        *,
        scenarios: list[ScenarioRecord],
        eligible_node_ids: set[str],
    ) -> set[PoolKey]:
        regimes = {
            entry.regime
            for scenario in scenarios
            for entry in scenario.zone_counts
            if entry.node_id in eligible_node_ids
        }
        if not regimes:
            raise DomainValidationError(
                "M5 requires scenario_gt rows for population-eligible topology nodes."
            )
        return {self._pool_suffix(regime) for regime in regimes}

    def _pool_key_for_sample(self, row: ErrorSampleRow) -> PoolKey:
        return (*_condition_key(row), *self._pool_suffix(row.ground_truth_regime))

    def _pool_key_for_entry(self, condition: ConditionKey, regime: str) -> PoolKey:
        return (*condition, *self._pool_suffix(regime))

    def _pool_suffix(self, regime: str) -> PoolKey:
        strategy = self.config.residual_grouping_strategy
        if strategy == "regime_conditioned_pool_lookup_v0_1":
            return ("regime", regime)
        if strategy == "split_level_pool_lookup_v0_1":
            return ("split", self.config.residual_split)
        if strategy == "pooled_residual_lookup_v0_1":
            return ("pooled",)
        raise DomainValidationError(f"Unsupported M5 residual grouping strategy: {strategy}")

    def _iter_assignments(
        self,
        *,
        pools: dict[PoolKey, list[ErrorSampleRow]],
        conditions: list[ConditionKey],
        scenarios: list[ScenarioRecord],
        eligible_node_ids: set[str],
    ) -> Iterator[M5ErrorRealizationRow]:
        without_replacement_orders = self._without_replacement_orders(pools)
        without_replacement_cursors: dict[PoolKey, int] = defaultdict(int)

        for condition in sorted(conditions):
            condition_id = _condition_id(condition)
            condition_label = _condition_label(condition)
            dataset_id, model_id, paradigm, split = condition
            for scenario in sorted(scenarios, key=lambda item: item.scenario_id):
                entries = [
                    entry
                    for entry in sorted(scenario.zone_counts, key=lambda item: item.node_id)
                    if entry.node_id in eligible_node_ids
                ]
                for trial_index in range(self.config.trial_count):
                    trial_id = (
                        f"{scenario.scenario_id}-{condition_id}-TRIAL-{trial_index + 1:04d}"
                    )
                    for entry in entries:
                        key = self._pool_key_for_entry(condition, entry.regime)
                        pool = pools.get(key)
                        if not pool:
                            raise DomainValidationError(
                                "No M2 residual samples available for "
                                f"{condition_label} / regime {entry.regime}."
                            )
                        seed = _stable_seed(
                            self.config.root_seed,
                            dataset_id,
                            model_id,
                            paradigm,
                            split,
                            scenario.scenario_id,
                            str(trial_index),
                            entry.node_id,
                            entry.regime,
                            self.config.residual_grouping_strategy,
                        )
                        residual = self._select_residual_sample(
                            key=key,
                            pool=pool,
                            seed=seed,
                            without_replacement_orders=without_replacement_orders,
                            without_replacement_cursors=without_replacement_cursors,
                        )
                        raw_observed = float(entry.ground_truth_population) + residual.error
                        observed, reason, invalid = self._adjust_observation(
                            raw_observed=raw_observed,
                            capacity=entry.capacity,
                        )
                        exceeded_by = max(0.0, observed - float(entry.capacity))
                        yield M5ErrorRealizationRow(
                            error_realization_id=_stable_id(
                                "ER",
                                condition_id,
                                scenario.scenario_id,
                                str(trial_index),
                                entry.node_id,
                                str(seed),
                            ),
                            trial_id=trial_id,
                            trial_index=trial_index,
                            condition_id=condition_id,
                            condition_label=condition_label,
                            dataset_id=dataset_id,
                            model_id=model_id,
                            paradigm=cast(Any, paradigm),
                            split=split,
                            scenario_id=scenario.scenario_id,
                            node_id=entry.node_id,
                            ground_truth_population=float(entry.ground_truth_population),
                            ground_truth_regime=entry.regime,
                            residual_error=residual.error,
                            raw_observed_population=raw_observed,
                            observed_population=observed,
                            adjustment_reason=reason,
                            invalid_observation=invalid,
                            capacity=entry.capacity,
                            capacity_exceeded=exceeded_by > 0,
                            capacity_exceeded_by=exceeded_by,
                            residual_source_sample_id=residual.sample_id,
                            residual_source_pool_id=residual.pool_id,
                            residual_source_artifact_id=(
                                self.config.m2_error_samples_artifact_id or ""
                            ),
                            scenario_gt_artifact_id=(
                                self.config.m4_scenario_gt_artifact_id or ""
                            ),
                            topology_artifact_id=(
                                self.config.m3_topology_spec_artifact_id or ""
                            ),
                            policy_ref=self._policy_ref(),
                            seed=seed,
                        )

    def _without_replacement_orders(
        self,
        pools: dict[PoolKey, list[ErrorSampleRow]],
    ) -> dict[PoolKey, list[ErrorSampleRow]]:
        orders: dict[PoolKey, list[ErrorSampleRow]] = {}
        for key, pool in pools.items():
            ordered = list(pool)
            rng = random.Random(_stable_seed(self.config.root_seed, "without", "|".join(key)))
            rng.shuffle(ordered)
            orders[key] = ordered
        return orders

    def _select_residual_sample(
        self,
        *,
        key: PoolKey,
        pool: list[ErrorSampleRow],
        seed: int,
        without_replacement_orders: dict[PoolKey, list[ErrorSampleRow]],
        without_replacement_cursors: dict[PoolKey, int],
    ) -> ErrorSampleRow:
        if self.config.sampling_replacement == "with_replacement":
            return pool[random.Random(seed).randrange(len(pool))]
        cursor = without_replacement_cursors[key]
        order = without_replacement_orders[key]
        if cursor >= len(order):
            raise DomainValidationError(
                f"M5 residual pool {key} is exhausted with "
                "sampling_replacement=without_replacement."
            )
        without_replacement_cursors[key] = cursor + 1
        return order[cursor]

    def _adjust_observation(
        self,
        *,
        raw_observed: float,
        capacity: int,
    ) -> tuple[float, str, bool]:
        invalid = False
        value = _round_value(raw_observed, self.config.rounding)
        reason = "none"
        if value < 0:
            if self.config.negative_handling == "fail_trial":
                raise DomainValidationError("M5 produced a negative observed population.")
            if self.config.negative_handling == "mark_invalid":
                invalid = True
                reason = "negative_marked_invalid"
            if self.config.negative_handling == "lower_bound_zero":
                reason = "lower_bound_zero"
            value = 0.0
        if value > capacity:
            if self.config.capacity_handling == "fail_trial":
                raise DomainValidationError("M5 observed population exceeds capacity.")
            if self.config.capacity_handling == "clip_to_capacity":
                reason = "clip_to_capacity" if reason == "none" else f"{reason}+clip_to_capacity"
                value = float(capacity)
            elif self.config.capacity_handling == "flag_only":
                reason = "capacity_flagged" if reason == "none" else f"{reason}+capacity_flagged"
        if (
            self.config.observation_adjustment_family == "reject_invalid_observation_v0_1"
            and invalid
        ):
            raise DomainValidationError("M5 rejected an invalid observation by policy.")
        return value, reason, invalid

    def _write_assignments_parquet(
        self,
        *,
        target_path: Path,
        assignments: Iterator[M5ErrorRealizationRow],
    ) -> M5AssignmentStats:
        pa = _pyarrow()
        pq = _pyarrow_parquet()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        writer: Any | None = None
        batch: list[JsonObject] = []
        stats = M5AssignmentStats()
        try:
            for row in assignments:
                stats.row_count += 1
                if row.capacity_exceeded:
                    stats.capacity_flags += 1
                if row.invalid_observation:
                    stats.invalid_count += 1
                if row.adjustment_reason != "none":
                    stats.adjusted_count += 1
                batch.append(_ordered_json_row(row.model_dump(mode="json")))
                if len(batch) >= PARQUET_BATCH_SIZE:
                    writer = _write_parquet_batch(
                        pa=pa,
                        pq=pq,
                        writer=writer,
                        target_path=target_path,
                        rows=batch,
                    )
                    batch = []
            if batch:
                writer = _write_parquet_batch(
                    pa=pa,
                    pq=pq,
                    writer=writer,
                    target_path=target_path,
                    rows=batch,
                )
            if writer is None:
                pq.write_table(pa.table({}), target_path, compression="zstd", version="2.6")
        finally:
            if writer is not None:
                writer.close()
        return stats

    def _write_observations_jsonl(
        self,
        *,
        target_path: Path,
        assignments: Iterator[M5ErrorRealizationRow],
        error_realization_artifact_id: str,
    ) -> int:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        current_key: tuple[str, str, str] | None = None
        current_rows: list[M5ErrorRealizationRow] = []
        record_count = 0
        with target_path.open("wb") as handle:
            for row in assignments:
                key = (row.condition_id, row.trial_id, row.scenario_id)
                if current_key is not None and key != current_key:
                    record = self._observation_record_from_assignment_rows(
                        rows=current_rows,
                        error_realization_artifact_id=error_realization_artifact_id,
                    )
                    handle.write(_json_bytes(record.model_dump(mode="json")) + b"\n")
                    record_count += 1
                    current_rows = []
                current_key = key
                current_rows.append(row)
            if current_rows:
                record = self._observation_record_from_assignment_rows(
                    rows=current_rows,
                    error_realization_artifact_id=error_realization_artifact_id,
                )
                handle.write(_json_bytes(record.model_dump(mode="json")) + b"\n")
                record_count += 1
        return record_count

    def _observation_record_from_assignment_rows(
        self,
        *,
        rows: list[M5ErrorRealizationRow],
        error_realization_artifact_id: str,
    ) -> M5PerturbedObservationRecord:
        first = rows[0]
        realization_id = _stable_id(
            "ERZ",
            first.condition_id,
            first.trial_id,
            first.scenario_id,
            str(self.config.root_seed),
        )
        return M5PerturbedObservationRecord(
            trial_id=first.trial_id,
            scenario_id=first.scenario_id,
            error_realization_id=realization_id,
            condition_id=first.condition_id,
            condition_label=first.condition_label,
            dataset_id=first.dataset_id,
            model_id=first.model_id,
            paradigm=first.paradigm,
            split=first.split,
            observed_population=[
                M5ObservedPopulationEntry(
                    node_id=row.node_id,
                    condition_id=row.condition_id,
                    dataset_id=row.dataset_id,
                    model_id=row.model_id,
                    paradigm=row.paradigm,
                    split=row.split,
                    ground_truth_population=row.ground_truth_population,
                    observed_population=row.observed_population,
                    raw_observed_population=row.raw_observed_population,
                    residual_error=row.residual_error,
                    capacity=row.capacity,
                    occupancy_ratio=(
                        row.observed_population / row.capacity if row.capacity > 0 else 0.0
                    ),
                    ground_truth_regime=row.ground_truth_regime,
                    adjustment_reason=row.adjustment_reason,
                    invalid_observation=row.invalid_observation,
                    capacity_exceeded=row.capacity_exceeded,
                    residual_source_sample_id=row.residual_source_sample_id,
                    residual_source_pool_id=row.residual_source_pool_id,
                )
                for row in sorted(rows, key=lambda item: item.node_id)
            ],
            scenario_gt_artifact_id=self.config.m4_scenario_gt_artifact_id,
            error_realization_artifact_id=error_realization_artifact_id,
            topology_artifact_id=self.config.m3_topology_spec_artifact_id,
        )

    def _build_observations(
        self,
        *,
        assignments: list[M5ErrorRealizationRow],
        error_realization_artifact_id: str,
    ) -> list[M5PerturbedObservationRecord]:
        grouped: dict[tuple[str, str, str], list[M5ErrorRealizationRow]] = defaultdict(list)
        for row in assignments:
            grouped[(row.condition_id, row.trial_id, row.scenario_id)].append(row)

        records: list[M5PerturbedObservationRecord] = []
        for (condition_id, trial_id, scenario_id), rows in sorted(grouped.items()):
            first = rows[0]
            realization_id = _stable_id(
                "ERZ",
                condition_id,
                trial_id,
                scenario_id,
                str(self.config.root_seed),
            )
            records.append(
                M5PerturbedObservationRecord(
                    trial_id=trial_id,
                    scenario_id=scenario_id,
                    error_realization_id=realization_id,
                    condition_id=condition_id,
                    condition_label=first.condition_label,
                    dataset_id=first.dataset_id,
                    model_id=first.model_id,
                    paradigm=first.paradigm,
                    split=first.split,
                    observed_population=[
                        M5ObservedPopulationEntry(
                            node_id=row.node_id,
                            condition_id=row.condition_id,
                            dataset_id=row.dataset_id,
                            model_id=row.model_id,
                            paradigm=row.paradigm,
                            split=row.split,
                            ground_truth_population=row.ground_truth_population,
                            observed_population=row.observed_population,
                            raw_observed_population=row.raw_observed_population,
                            residual_error=row.residual_error,
                            capacity=row.capacity,
                            occupancy_ratio=(
                                row.observed_population / row.capacity
                                if row.capacity > 0
                                else 0.0
                            ),
                            ground_truth_regime=row.ground_truth_regime,
                            adjustment_reason=row.adjustment_reason,
                            invalid_observation=row.invalid_observation,
                            capacity_exceeded=row.capacity_exceeded,
                            residual_source_sample_id=row.residual_source_sample_id,
                            residual_source_pool_id=row.residual_source_pool_id,
                        )
                        for row in sorted(rows, key=lambda item: item.node_id)
                    ],
                    scenario_gt_artifact_id=self.config.m4_scenario_gt_artifact_id,
                    error_realization_artifact_id=error_realization_artifact_id,
                    topology_artifact_id=self.config.m3_topology_spec_artifact_id,
                )
            )
        return records

    def _quality_report(
        self,
        *,
        bindings: JsonObject,
        source_sample_count: int,
        filtered_sample_count: int,
        eligible_conditions: list[ConditionKey],
        skipped_conditions: list[JsonObject],
        assignment_stats: M5AssignmentStats,
        observation_count: int,
    ) -> str:
        lines = [
            "# M5 Exploratory Controlled Perturbation Quality Report",
            "",
            "Status: exploratory only.",
            "",
            "This run builds controlled perturbed observations for inspection. It does not "
            "produce deployment reliability, R_deploy or Delta_R.",
            "",
            "## Inputs",
            "",
        ]
        for key in ("m2", "m3", "m4"):
            item = cast(JsonObject, bindings["required_inputs"][key])
            lines.append(f"- {key}: `{item['uri']}` `{item['checksum']}`")
        lines.extend(
            [
                "",
                "## Counts",
                "",
                f"- Source residual samples: {source_sample_count}",
                f"- Filtered residual samples: {filtered_sample_count}",
                f"- Eligible residual conditions: {len(eligible_conditions)}",
                f"- Skipped residual conditions: {len(skipped_conditions)}",
                f"- Controlled trials: {observation_count}",
                f"- Assignment rows: {assignment_stats.row_count}",
                f"- Adjusted observations: {assignment_stats.adjusted_count}",
                f"- Invalid observations flagged: {assignment_stats.invalid_count}",
                f"- Capacity flags: {assignment_stats.capacity_flags}",
                "",
                "## Explicit Limits",
                "",
            ]
        )
        if skipped_conditions:
            lines.extend(["", "## Skipped Conditions", ""])
            for item in skipped_conditions:
                lines.append(
                    f"- `{item['condition_id']}` {item['condition_label']}: "
                    f"{item['reason']}"
                )
        lines.extend(["", "## Paper-Aligned Exploratory Policy", ""])
        lines.extend(
            [
                f"- Policy id: `{self.config.paper_aligned_policy_id}`",
                "- All matching models are evaluated as separate residual conditions.",
                "- Density and Detection are never merged into the same residual pool.",
                "- Raw observed population is preserved before decision-input adjustment.",
                "- GAI unavailable means GAI metrics stay unavailable; no fake GAI output is made.",
            ]
        )
        lines.extend(f"- {item}" for item in _limitations())
        return "\n".join(lines) + "\n"

    def _sampling_manifest(
        self,
        *,
        bindings: JsonObject,
        filtered_sample_count: int,
        pools: dict[PoolKey, list[ErrorSampleRow]],
        eligible_conditions: list[ConditionKey],
        skipped_conditions: list[JsonObject],
        assignment_row_count: int,
        error_artifact: ArtifactRecord,
        observation_artifact: ArtifactRecord,
    ) -> JsonObject:
        return {
            "schema_version": "1.0.0",
            "pipeline_profile": self.config.pipeline_profile,
            "run_purpose": self.config.run_purpose.value,
            "exploratory_only": True,
            "paper_aligned_policy_id": self.config.paper_aligned_policy_id,
            "root_seed": self.config.root_seed,
            "trial_count": self.config.trial_count,
            "minimum_pool_size": self.config.minimum_pool_size,
            "insufficient_pool_policy": self.config.insufficient_pool_policy,
            "filters": {
                "dataset_id": self.config.residual_dataset_id,
                "model_id": self.config.residual_model_id,
                "paradigm": self.config.residual_paradigm,
                "split": self.config.residual_split,
            },
            "policy_choices": {
                "residual_pool_strategy": self.config.residual_pool_strategy,
                "residual_grouping_strategy": self.config.residual_grouping_strategy,
                "residual_assignment_strategy": self.config.residual_assignment_strategy,
                "assignment_unit": self.config.assignment_unit,
                "observation_adjustment_family": self.config.observation_adjustment_family,
                "sampling_replacement": self.config.sampling_replacement,
                "negative_handling": self.config.negative_handling,
                "rounding": self.config.rounding,
                "capacity_handling": self.config.capacity_handling,
                "exploratory_aggregation": self.config.exploratory_aggregation,
            },
            "input_bindings": bindings["required_inputs"],
            "condition_scope": {
                "unit": "dataset_id + model_id + paradigm + split",
                "density_detection_separate": True,
                "all_matching_models_included": (
                    self.config.residual_model_id in {None, "", "auto"}
                ),
            },
            "eligible_conditions": [
                {
                    "condition_id": _condition_id(condition),
                    "condition_label": _condition_label(condition),
                    "dataset_id": condition[0],
                    "model_id": condition[1],
                    "paradigm": condition[2],
                    "split": condition[3],
                }
                for condition in eligible_conditions
            ],
            "skipped_conditions": skipped_conditions,
            "pool_counts": {"|".join(key): len(pool) for key, pool in sorted(pools.items())},
            "filtered_sample_count": filtered_sample_count,
            "eligible_condition_count": len(eligible_conditions),
            "skipped_condition_count": len(skipped_conditions),
            "assignment_row_count": assignment_row_count,
            "outputs": {
                "error_realizations": {
                    "artifact_id": error_artifact.artifact_id,
                    "uri": error_artifact.uri,
                    "checksum": error_artifact.checksum,
                    "row_count": error_artifact.row_count,
                },
                "perturbed_observation_population": {
                    "artifact_id": observation_artifact.artifact_id,
                    "uri": observation_artifact.uri,
                    "checksum": observation_artifact.checksum,
                    "row_count": observation_artifact.row_count,
                },
            },
            "forbidden_behavior": {
                "synthetic_perception_noise": False,
                "epsilon_zero_fake_baseline": False,
                "formal_reliability_claim": False,
                "mix_density_detection_pool": False,
                "fake_gai_api_result": False,
            },
        }

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
            "determinism": {
                "root_seed": self.config.root_seed,
                "stable_seed_inputs": [
                    "root_seed",
                    "dataset_id",
                    "model_id",
                    "paradigm",
                    "split",
                    "scenario_id",
                    "trial_index",
                    "node_id",
                    "ground_truth_regime",
                    "residual_grouping_strategy",
                ],
                "worker_order_independent": True,
            },
            "input_bindings": bindings["required_inputs"],
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

    def _policy_ref(self) -> str:
        parts = [
            self.config.paper_aligned_policy_id,
            self.config.m5_policy_bundle_snapshot_id or "open_exploratory_policy",
            self.config.m5_policy_bundle_snapshot_hash or "unapproved_for_formal",
            self.config.residual_pool_strategy,
            self.config.residual_grouping_strategy,
            self.config.residual_assignment_strategy,
            self.config.observation_adjustment_family,
        ]
        return "|".join(parts)

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
    for stage_id in ("M1", "M2", "M3", "M4"):
        output.append(
            StageStatusRecord(
                stage_id=stage_id,
                status="not_required",
                reason="M5 exploratory run consumes published upstream artifacts.",
            )
        )
    output.append(StageStatusRecord(stage_id="M5", status="succeeded"))
    for stage_id in ("M6", "M7", "M8", "M9"):
        output.append(
            StageStatusRecord(
                stage_id=stage_id,
                status="not_required",
                reason="Integrated decision validation and reporting are not enabled.",
            )
        )
    return output


def _limitations() -> list[str]:
    return [
        "M5 output is exploratory controlled perturbation only.",
        "M5 reads published M2 error_samples, M3 topology_spec and M4 scenario_gt only.",
        "No raw perception files, synthetic perception noise or epsilon-zero fake baseline "
        "are used.",
        "M5 publishes observation artifacts only; downstream runners compute decisions "
        "and metrics.",
        "Standalone M5 runs do not compute R_deploy or Delta_R.",
        "Formal run purpose remains blocked until formal research approval.",
    ]


def _matches_optional(value: str, expected: str | None) -> bool:
    if expected is None or expected == "" or expected == "auto":
        return True
    return value == expected


def _condition_key(row: ErrorSampleRow) -> ConditionKey:
    return (row.dataset_id, row.model_id, row.paradigm, row.split)


def _condition_id(condition: ConditionKey) -> str:
    dataset_id, model_id, paradigm, split = condition
    return "COND-" + "-".join(
        _safe_token(part) for part in (paradigm, dataset_id, model_id, split)
    )


def _condition_label(condition: ConditionKey) -> str:
    dataset_id, model_id, paradigm, split = condition
    return f"{paradigm}/{dataset_id}/{model_id}/{split}"


def _safe_token(value: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    token = "-".join(part for part in token.split("-") if part)
    return token or "unknown"


def _round_value(value: float, policy: str) -> float:
    if policy == "nearest_integer":
        return float(math.floor(value + 0.5))
    if policy == "floor":
        return float(math.floor(value))
    if policy == "ceil":
        return float(math.ceil(value))
    if policy == "none":
        return float(value)
    raise DomainValidationError(f"Unsupported M5 rounding policy: {policy}")


def _population_eligible_node_ids(topology: TopologySpec) -> set[str]:
    return {
        node.node_id
        for node in topology.nodes
        if node.enabled and not node.is_exit and not node.is_sink and node.capacity > 0
    }


def _read_error_samples_parquet(path: str) -> list[ErrorSampleRow]:
    pq = _pyarrow_parquet()
    table = pq.read_table(path)
    rows: list[ErrorSampleRow] = []
    for row in table.to_pylist():
        rows.append(ErrorSampleRow.model_validate(row))
    if not rows:
        raise DomainValidationError("M2 error_samples.parquet must contain at least one row.")
    return rows


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
        raise DomainValidationError(f"Unsupported artifact URI for M5 input: {uri}")
    relative = uri.removeprefix("artifact://")
    if Path(relative).is_absolute():
        raise DomainValidationError("M5 artifact URI must be relative to artifact root.")
    target = (artifact_root / "published" / relative).resolve()
    try:
        target.relative_to((artifact_root / "published").resolve())
    except ValueError as exc:
        raise DomainValidationError(f"M5 artifact URI path traversal rejected: {uri}") from exc
    return target


def _parquet_bytes(rows: list[JsonObject]) -> bytes:
    pa = _pyarrow()
    pq = _pyarrow_parquet()
    sink = pa.BufferOutputStream()
    if not rows:
        table = pa.table({})
    else:
        ordered = [{key: row.get(key) for key in sorted(rows[0])} for row in rows]
        table = pa.Table.from_pylist(ordered)
    pq.write_table(table, sink, compression="zstd", version="2.6")
    return cast(bytes, sink.getvalue().to_pybytes())


def _write_parquet_batch(
    *,
    pa: Any,
    pq: Any,
    writer: Any | None,
    target_path: Path,
    rows: list[JsonObject],
) -> Any:
    table = pa.Table.from_pylist(rows)
    if writer is None:
        writer = pq.ParquetWriter(
            target_path,
            table.schema,
            compression="zstd",
            version="2.6",
        )
    writer.write_table(table)
    return writer


def _ordered_json_row(row: JsonObject) -> JsonObject:
    return {key: row.get(key) for key in sorted(row)}


def _pyarrow() -> Any:
    try:
        import pyarrow as pa  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError("pyarrow is required to write M5 Parquet artifacts.") from exc
    return pa


def _pyarrow_parquet() -> Any:
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ModuleNotFoundError as exc:
        raise DomainValidationError("pyarrow.parquet is required for M5 artifacts.") from exc
    return pq


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


def _stable_seed(root_seed: int, *parts: str) -> int:
    digest = hashlib.sha256("|".join([str(root_seed), *parts]).encode("utf-8")).hexdigest()
    return int(digest[:16], 16) % (2**31 - 1)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:16]}"
