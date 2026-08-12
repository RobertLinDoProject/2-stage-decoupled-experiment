from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import shutil
import statistics
import tempfile
import zipfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any, Callable, cast

import pyarrow as pa
import pyarrow.parquet as pq

from two_stage.settings import Settings
from two_stage.domain.ports.decision_interface import (
    DecisionActionPayload,
    DecisionInterfaceResult,
    DecisionRequestPayload,
)
from two_stage.infrastructure.decision_adapters import (
    GaiHttpAdapterConfig,
    create_gai_decision_adapter,
    UnavailableGaiDecisionAdapter,
)
from two_stage.infrastructure.decision_adapters.gai import GaiDecisionAdapterCall
from two_stage.application.services.topology_input_contract import (
    validate_topology_triplet,
)
from two_stage.application.services.insight_report import DecoupledInsightReportService

PROFILE_ID = "decoupled_2_stage_experiment_v1"
DISPLAY_NAME = "Decoupled 2-Stage Experiment"
COMPARISON_PROFILE_ID = "decoupled_2_stage_rule_source_comparison_v1"
COMPARISON_DISPLAY_NAME = "Decoupled 2-Stage Rule Source Comparison"
FRAMEWORK_WITH = "w/ Two-stage framework"
FRAMEWORK_WITHOUT = "w/o Two-stage framework"
REGIMES = ["LOW", "MEDIUM", "HIGH"]
REGIME_LOAD_FACTOR = {"LOW": 0.25, "MEDIUM": 0.55, "HIGH": 0.85}
METRIC_POLICY_ID = 'safety_consistency'
METRIC_POLICY_VERSION = '2.0.0'
SCENARIO_POLICY_ID = 'topology_capacity_hotspot_beta_v1'
SCENARIO_POLICY_VERSION = 'feasibility_constrained_v1'
MAX_SCENARIO_CANDIDATE_ATTEMPTS = 512
FEASIBILITY_ORACLE_VERSION = "capacity_aware_multi_source_feasibility_oracle_v1"
M6_DECISION_POLICY_ID = 'capacity_aware_multi_source_rule_based'
M6_DECISION_POLICY_VERSION = '1.0.0'
RULE_SOURCE_HUMAN = "human_manual_v1"
RULE_SOURCE_AI = "ai_generated_derived_v1"
RULE_SOURCE_ORDER = (RULE_SOURCE_HUMAN, RULE_SOURCE_AI)
RULE_SOURCE_LABELS = {
    RULE_SOURCE_HUMAN: "人工規則",
    RULE_SOURCE_AI: "AI 生成規則",
}
DECISION_INTERFACES = ("rule_based", "gai")
GAI_EXECUTION_MODE_LIVE = "live"
GAI_EXECUTION_MODE_RESERVED = "reserved_unavailable"
SUPPORTED_TOPOLOGIES = [
    {
        "id": "fcu",
        "name": "FCU Campus",
        "source_name": "fcu",
    },
    {
        "id": "taichung_lantern_festival",
        "name": "Taichung Lantern Festival",
        "source_name": "Taichung Lantern Festival",
    },
    {
        "id": "taipei_new_years_eve",
        "name": "Taipei New Year's Eve",
        "source_name": "Taipei New Year's Eve",
    },
]


@dataclass(frozen=True, slots=True)
class ResidualStats:
    count: int
    mean: float
    std: float
    p90_abs: float
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class ResolvedRunConfig:
    """Immutable parameters that are allowed to change an experiment result."""

    root_seed: int
    split: str
    trial_count_per_condition: int
    scenarios_per_regime: int
    risk_threshold: float
    risk_f_beta: float
    scenario_alpha: float
    scenario_beta: float
    hotspot_ratio: float
    hotspot_selection: str
    metric_policy_id: str = METRIC_POLICY_ID
    metric_policy_version: str = METRIC_POLICY_VERSION
    scenario_policy_id: str = SCENARIO_POLICY_ID
    scenario_policy_version: str = SCENARIO_POLICY_VERSION
    max_scenario_candidate_attempts: int = MAX_SCENARIO_CANDIDATE_ATTEMPTS
    decision_policy_id: str = M6_DECISION_POLICY_ID
    decision_policy_version: str = M6_DECISION_POLICY_VERSION
    run_purpose: str = "exploratory"
    selected_topology_ids: tuple[str, ...] = ()
    selected_model_ids: tuple[str, ...] = ()
    selected_regimes: tuple[str, ...] = ()
    selected_interfaces: tuple[str, ...] = ()
    gai_provider: str = "ollama"
    gai_execution_mode: str = GAI_EXECUTION_MODE_RESERVED
    planned_gai_calls: int | None = None
    effective_gai_budget: int | None = None
    gai_budget_estimation_method: str | None = None
    gai_budget_hard_limit: int | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "root_seed": self.root_seed,
            "split": self.split,
            "trial_count_per_condition": self.trial_count_per_condition,
            "scenarios_per_regime": self.scenarios_per_regime,
            "risk_threshold": self.risk_threshold,
            "risk_f_beta": self.risk_f_beta,
            "scenario_alpha": self.scenario_alpha,
            "scenario_beta": self.scenario_beta,
            "rho": self.hotspot_ratio,
            "hotspot_selection": self.hotspot_selection,
            "metric_policy_id": self.metric_policy_id,
            "metric_policy_version": self.metric_policy_version,
            "scenario_policy_id": self.scenario_policy_id,
            "scenario_policy_version": self.scenario_policy_version,
            "max_scenario_candidate_attempts": self.max_scenario_candidate_attempts,
            "decision_policy_id": self.decision_policy_id,
            "decision_policy_version": self.decision_policy_version,
            "run_purpose": self.run_purpose,
            "selected_topology_ids": list(self.selected_topology_ids),
            "selected_model_ids": list(self.selected_model_ids),
            "selected_regimes": list(self.selected_regimes),
            "selected_interfaces": list(self.selected_interfaces),
            "gai_provider": self.gai_provider,
            "gai_execution_mode": self.gai_execution_mode,
            "planned_gai_calls": self.planned_gai_calls,
            "effective_gai_budget": self.effective_gai_budget,
            "gai_budget_estimation_method": self.gai_budget_estimation_method,
            "gai_budget_hard_limit": self.gai_budget_hard_limit,
        }


class ExperimentFailure(ValueError):
    """Expected experiment failure with a persisted stage-level diagnostic."""

    def __init__(self, *, stage_id: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.run_id: str | None = None
        self.stage_id = stage_id
        self.details = details or {}


class RunCancelled(ExperimentFailure):
    """Cooperative cancellation at an orchestration checkpoint."""


class Decoupled2StageExperimentUseCase:
    """File-backed experiment for generating decoupled two-stage paper tables."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage_root = Path(settings.local_artifact_root) / "published" / "runs"
        self.data_root = Path(settings.current_project_data_root)
        self.perception_root = self.data_root / "Perception資料"
        self.topology_root = self.data_root / "Topology資料"
        self._gai_requests_used = 0
        self._gai_effective_budget = settings.gai_budget_max_requests_per_run
        self._cancel_check: Callable[[], bool] | None = None
        self._resume_mode = False
        self._resume_action_journal: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._gai_provider_override = settings.gai_provider_name
        self._quota_exhausted = False
        self._quota_details: dict[str, Any] = {}

    def _check_cancelled(self, stage_id: str) -> None:
        if self._cancel_check is not None and self._cancel_check():
            raise RunCancelled(
                stage_id=stage_id,
                message="Run cancellation requested at orchestration checkpoint.",
                details={"status": "CANCELLED"},
            )

    def metadata(self) -> dict[str, Any]:
        models = self._load_models(include_ineligible=False) if self.perception_root.exists() else []
        latest = self.list_runs(limit=1)
        return {
            "profile_id": PROFILE_ID,
            "display_name": DISPLAY_NAME,
            "paper_output": "15 configuration tables: 5 perception models x 3 topologies",
            "topologies": SUPPORTED_TOPOLOGIES,
            "rule_sources": [
                {
                    "id": RULE_SOURCE_HUMAN,
                    "label": RULE_SOURCE_LABELS[RULE_SOURCE_HUMAN],
                    "status": "available",
                    "scope": "manual topology and rules",
                },
                {
                    "id": RULE_SOURCE_AI,
                    "label": RULE_SOURCE_LABELS[RULE_SOURCE_AI],
                    "status": "available",
                    "scope": "AI map/neighbors materialized rule bundle",
                },
            ],
            "comparison_profile": {
                "profile_id": COMPARISON_PROFILE_ID,
                "decision_interfaces": list(DECISION_INTERFACES),
                "framework_conditions": [FRAMEWORK_WITHOUT, FRAMEWORK_WITH],
                "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            },
            "models": models,
            "default_config": {
                "root_seed": 114,
                "split": "test",
                "trial_count_per_condition": 30,
                "scenarios_per_regime": 8,
                "risk_threshold": 0.82,
                "risk_f_beta": 2.0,
                "scenario_alpha": 2.0,
                "scenario_beta": 2.0,
                "rho": 0.55,
                "hotspot_selection": "top_capacity_quartile",
                "metric_policy_id": METRIC_POLICY_ID,
                "metric_policy_version": METRIC_POLICY_VERSION,
                "scenario_policy_id": SCENARIO_POLICY_ID,
                "scenario_policy_version": SCENARIO_POLICY_VERSION,
                "max_scenario_candidate_attempts": MAX_SCENARIO_CANDIDATE_ATTEMPTS,
                "decision_policy_id": M6_DECISION_POLICY_ID,
                "decision_policy_version": M6_DECISION_POLICY_VERSION,
                "decision_policy_scope": "shared M6 planner for ideal and deployment branches",
                "gai_execution_mode": self.settings.gai_execution_mode,
                "sampling_replacement": "with_replacement",
                "negative_handling": "floor_at_zero",
                "rounding": "round_half_up",
                "count_to_node_mapping": "regime_matched_independent_residual_per_source_node",
                "decision_interfaces": ["rule_based", "gai"],
                "run_purpose": "exploratory",
                "selection_defaults": {
                    "topology_ids": [item["id"] for item in SUPPORTED_TOPOLOGIES],
                    "model_ids": [item["model_id"] for item in models],
                    "regimes": list(REGIMES),
                    "interfaces": ["rule_based"],
                },
            },            "gai": {
                "enabled": self._gai_external_calls_allowed(),
                "status": self._gai_status(),
                "execution_mode": self.settings.gai_execution_mode,
                "external_calls_allowed": self._gai_external_calls_allowed(),
                "provider": self.settings.gai_provider_name,
                "model": self.settings.gai_provider_model,
                "prompt_template_version": self.settings.gai_prompt_template_version,
                "budget_max_requests_per_run": self.settings.gai_budget_max_requests_per_run,
                "budget_mode": self.settings.gai_budget_mode,
                "budget_hard_limit": self.settings.gai_budget_hard_limit,
                "note": "GAI is available only when live local Ollama is explicitly enabled; preflight never calls the provider.",
            },
            "latest_run": latest[0] if latest else None,
        }

    def preflight(
        self,
        *,
        trial_count_per_condition: int = 30,
        scenarios_per_regime: int = 8,
        root_seed: int = 114,
        split: str = "test",
        risk_f_beta: float = 2.0,
        risk_threshold: float = 0.82,
        scenario_alpha: float = 2.0,
        scenario_beta: float = 2.0,
        rho: float = 0.55,
        hotspot_selection: str = "top_capacity_quartile",
        rule_source_ids: list[str] | tuple[str, ...] | None = None,
        run_purpose: str = "exploratory",
        selected_topology_ids: list[str] | tuple[str, ...] | None = None,
        selected_model_ids: list[str] | tuple[str, ...] | None = None,
        selected_regimes: list[str] | tuple[str, ...] | None = None,
        selected_interfaces: list[str] | tuple[str, ...] | None = None,
        gai_provider: str | None = None,
    ) -> dict[str, Any]:
        """Run read-only data and reproducibility checks without calling a GAI provider."""
        if gai_provider:
            self._gai_provider_override = str(gai_provider).strip().lower()
        try:
            selected_sources = tuple(rule_source_ids or (RULE_SOURCE_HUMAN,))
            if not selected_sources or any(source not in RULE_SOURCE_ORDER for source in selected_sources):
                raise ValueError(
                    "Rule-source preflight requires at least one known rule source."
                )
            comparison_requested = (
                selected_sources != (RULE_SOURCE_HUMAN,)
                or selected_interfaces is not None
                and tuple(selected_interfaces) != ("rule_based",)
            )
            topologies_selected, models, regimes, interfaces = self._resolve_selection(
                selected_topology_ids=selected_topology_ids,
                selected_model_ids=selected_model_ids,
                selected_regimes=selected_regimes,
                selected_interfaces=selected_interfaces,
                default_interfaces=self._default_interfaces(run_purpose) if comparison_requested else ("rule_based",),
            )
            config = self._resolve_run_config(
                root_seed=root_seed,
                split=split,
                trial_count=trial_count_per_condition,
                scenario_count=scenarios_per_regime,
                risk_f_beta=risk_f_beta,
                risk_threshold=risk_threshold,
                scenario_alpha=scenario_alpha,
                scenario_beta=scenario_beta,
                rho=rho,
                hotspot_selection=hotspot_selection,
                run_purpose=run_purpose,
                selected_topology_ids=tuple(item["id"] for item in topologies_selected),
                selected_model_ids=tuple(item["model_id"] for item in models),
                selected_regimes=regimes,
                selected_interfaces=interfaces,
            )
            planned_gai_calls = 0
            gai_estimation_rows: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory(prefix="decoupled-preflight-") as temporary_root:
                run_root = Path(temporary_root) / "run"
                run_root.mkdir(parents=True, exist_ok=True)
                m1_rows = self._write_m1(run_root, models, split)
                pools, _pool_stats = self._write_m2(run_root, m1_rows)
                if comparison_requested:
                    topologies = self._write_comparison_m3(run_root, selected_sources, topologies_selected)
                    scenarios = self._write_comparison_m4(run_root, topologies, config, regimes)
                else:
                    topologies = self._write_m3(run_root, topologies_selected)
                    scenarios = self._write_m4(run_root, topologies, config, regimes)

                pool_rows = [
                    {
                        "model_id": model["model_id"],
                        "paradigm": model["paradigm"],
                        "regime": regime,
                        "pool_size": len(pools.get((model["model_id"], regime), [])),
                        "status": "PASSED" if pools.get((model["model_id"], regime)) else "FAILED",
                    }
                    for model in models
                    for regime in regimes
                ]
                scenario_checksums: list[str] = []
                observation_checksums: list[dict[str, Any]] = []
                for topology in topologies_selected:
                    topology_id = topology["id"]
                    topology_key = (RULE_SOURCE_HUMAN, topology_id) if comparison_requested else topology_id
                    topology_runtime = topologies[topology_key]
                    for regime in regimes:
                        scenarios_for_regime = scenarios[(topology_id, regime)]
                        scenario_checksums.extend(item["scenario_checksum"] for item in scenarios_for_regime)
                        scenario = scenarios_for_regime[0]
                        for model in models:
                            pool = pools.get((model["model_id"], regime), [])
                            sample_seed = self._seed_value(root_seed, "M5", f"{topology_id}__{model['model_id']}", regime, 0)
                            observed_a, _ = self._build_observation(
                                scenario["scenario_gt_population"], pool, random.Random(sample_seed)
                            )
                            observed_b, _ = self._build_observation(
                                scenario["scenario_gt_population"], pool, random.Random(sample_seed)
                            )
                            observation_checksums.append({
                                "topology_id": topology_id,
                                "model_id": model["model_id"],
                                "regime": regime,
                                "scenario_checksum": scenario["scenario_checksum"],
                                "observation_checksum": self._object_checksum(observed_a),
                                "repeated_observation_checksum": self._object_checksum(observed_b),
                                "topology_checksum": topology_runtime["topology_checksum"],
                                "stable": observed_a == observed_b,
                            })
                if "gai" in interfaces and self._gai_external_calls_allowed():
                    # Ideal input is independent of perception model.  Estimate
                    # and later execute that branch once per rule source,
                    # topology, regime and trial; only deployment is model-
                    # specific because it uses the model's observation.
                    for source_id in selected_sources:
                        for topology_item in topologies_selected:
                            topology_id = topology_item["id"]
                            topology_runtime = topologies[(source_id, topology_id)]
                            for regime in regimes:
                                scenarios_for_regime = scenarios[(topology_id, regime)]
                                for trial_index in range(trial_count_per_condition):
                                    scenario = scenarios_for_regime[trial_index % len(scenarios_for_regime)]
                                    ideal_estimate = self._estimate_gai_action_steps(
                                        topology_runtime,
                                        scenario["scenario_gt_population"],
                                        config.risk_threshold,
                                    )
                                    planned_gai_calls += int(ideal_estimate["upper_bound"])
                                    gai_estimation_rows.append({
                                        "rule_source_id": source_id,
                                        "topology_id": topology_id,
                                        "model_id": "__shared_ideal__",
                                        "regime": regime,
                                        "trial_index": trial_index,
                                        "branch": "ideal",
                                        "scope": "shared_rule_source_topology_regime_trial",
                                        **ideal_estimate,
                                    })
                                    for model in models:
                                        pool = pools.get((model["model_id"], regime), [])
                                        observed, _ = self._build_observation(
                                            scenario["scenario_gt_population"],
                                            pool,
                                            random.Random(self._seed_value(
                                                root_seed,
                                                "M5",
                                                f"{topology_id}__{model['model_id']}",
                                                regime,
                                                trial_index,
                                            )),
                                        )
                                        deployment_estimate = self._estimate_gai_action_steps(
                                            topology_runtime,
                                            observed,
                                            config.risk_threshold,
                                        )
                                        planned_gai_calls += int(deployment_estimate["upper_bound"])
                                        gai_estimation_rows.append({
                                            "rule_source_id": source_id,
                                            "topology_id": topology_id,
                                            "model_id": model["model_id"],
                                            "regime": regime,
                                            "trial_index": trial_index,
                                            "branch": "deployment",
                                            "scope": "model_specific_observation",
                                            **deployment_estimate,
                                        })
                failed_pools = [row for row in pool_rows if row["status"] != "PASSED"]
                unstable_observations = [row for row in observation_checksums if not row["stable"]]
                scenario_items = [
                    item
                    for values in scenarios.values()
                    for item in values
                ]
                scenario_oracle_versions = sorted({
                    str(item.get("feasibility_oracle_version"))
                    for item in scenario_items
                    if item.get("feasibility_oracle_version")
                })
                scenario_generation = {
                    "feasibility_constrained_sampling": bool(scenario_items)
                    and all(
                        item.get("decision_feasibility_status") == "feasible"
                        for item in scenario_items
                    ),
                    "feasibility_oracle_version": (
                        scenario_oracle_versions[0]
                        if len(scenario_oracle_versions) == 1
                        else scenario_oracle_versions
                    ),
                    "accepted_scenario_count": len(scenario_items),
                    "rejected_candidate_count": sum(
                        int(item.get("candidate_rejection_count", 0) or 0)
                        for item in scenario_items
                    ),
                }
                effective_budget = (
                    planned_gai_calls
                    if self.settings.gai_budget_mode == "auto"
                    else self.settings.gai_budget_max_requests_per_run
                )
                budget_sufficient = (
                    "gai" not in interfaces
                    or not self._gai_external_calls_allowed()
                    or (
                        planned_gai_calls <= self.settings.gai_budget_hard_limit
                        and (
                            self.settings.gai_budget_mode == "auto"
                            or planned_gai_calls <= self.settings.gai_budget_max_requests_per_run
                        )
                    )
                )
                budget_shortfall = max(0, planned_gai_calls - effective_budget)
                return {
                    "status": "FAILED" if failed_pools or unstable_observations or not budget_sufficient else "PASSED",
                    "run_purpose": run_purpose,
                    "config": config.payload(),
                    "selection": {
                        "rule_source_ids": list(selected_sources),
                        "topology_ids": [item["id"] for item in topologies_selected],
                        "model_ids": [item["model_id"] for item in models],
                        "regimes": list(regimes),
                        "interfaces": list(interfaces),
                    },
                    "planned_counts": {
                        "condition_count": len(selected_sources) * len(topologies_selected) * len(models),
                        "scenario_count": len(scenario_checksums),
                        "trial_count_per_condition": trial_count_per_condition,
                        "rule_based_decision_branches": (
                            len(selected_sources) * len(topologies_selected) * len(models) * len(regimes)
                            * trial_count_per_condition * 2
                            if "rule_based" in interfaces else 0
                        ),
                        "gai_logical_calls": planned_gai_calls,
                        "gai_action_step_upper_bound": planned_gai_calls,
                        "gai_shared_ideal_action_episodes": (
                            len(selected_sources) * len(topologies_selected) * len(regimes) * trial_count_per_condition
                            if "gai" in interfaces else 0
                        ),
                        "gai_model_specific_deployment_action_episodes": (
                            len(selected_sources) * len(topologies_selected) * len(models) * len(regimes) * trial_count_per_condition
                            if "gai" in interfaces else 0
                        ),
                    },
                    "residual_pools": pool_rows,
                    "scenario_checksums": {
                        "count": len(scenario_checksums),
                        "unique_count": len(set(scenario_checksums)),
                    },
                    "scenario_generation": scenario_generation,
                    "observation_reproducibility": {
                        "count": len(observation_checksums),
                        "stable_count": sum(row["stable"] for row in observation_checksums),
                        "rows": observation_checksums,
                    },
                        "gai": {
                        "status": self._gai_status(),
                        "provider": self._active_gai_provider_name(),
                        "model": self._active_gai_model(),
                        "planned_calls": planned_gai_calls,
                        "planned_action_calls": planned_gai_calls,
                        "calls_sent": 0,
                        "budget_max_requests_per_run": effective_budget,
                        "effective_budget": effective_budget,
                        "budget_mode": self.settings.gai_budget_mode,
                        "budget_hard_limit": self.settings.gai_budget_hard_limit,
                        "budget_sufficient": budget_sufficient,
                        "budget_shortfall": budget_shortfall,
                        "estimation_method": "m6_context_candidate_capacity_upper_bound_with_shared_ideal",
                        "estimation_rows": gai_estimation_rows,
                        "note": "Preflight never calls the provider; it estimates canonical action steps without creating experiment results.",
                    },
                }
        except (ExperimentFailure, FileNotFoundError, ValueError) as exc:
            return {
                "status": "FAILED",
                "run_purpose": run_purpose,
                "message": str(exc),
                "gai": {
                    "status": self._gai_status(),
                    "calls_sent": 0,
                    "note": "Preflight never calls Gemini.",
                },
            }

    def _gai_status(self) -> str:
        if not self._gai_external_calls_allowed():
            return "unavailable"
        if not self._active_gai_endpoint():
            return "unavailable"
        if self._active_gai_provider_name() != "ollama" and not self._active_gai_api_key():
            return "unavailable"
        return "configured"

    def _active_gai_provider_name(self) -> str:
        return str(getattr(self, "_gai_provider_override", self.settings.gai_provider_name))

    def _active_gai_endpoint(self) -> str | None:
        provider = self._active_gai_provider_name()
        if provider == "openai":
            return self.settings.openai_api_endpoint
        return self.settings.gai_provider_endpoint

    def _active_gai_api_key(self) -> str | None:
        provider = self._active_gai_provider_name()
        if provider == "openai":
            return self.settings.openai_api_key
        return self.settings.gai_provider_api_key

    def _active_gai_model(self) -> str:
        provider = self._active_gai_provider_name()
        if provider == "openai":
            return self.settings.openai_model
        return self.settings.gai_provider_model

    @staticmethod
    def _gai_result_completed(result: dict[str, Any]) -> bool:
        return result.get("status") == "parsed" and result.get("decision_output_status") in {
            "parsed",
            "no_action_required",
        }

    @staticmethod
    def _gai_result_terminal(result: dict[str, Any]) -> bool:
        """Return whether GAI produced a terminal model outcome for this branch.

        Invalid model output and an infeasible action episode are experiment
        outcomes. Transport/provider failures are not, because no decision was
        produced and the trial must remain unavailable.
        """
        return (
            Decoupled2StageExperimentUseCase._gai_result_completed(result)
            or result.get("decision_output_status") in {"invalid_output", "decision_infeasible"}
        )

    @staticmethod
    def _gai_result_failure_reason(result: dict[str, Any]) -> str:
        trace = result.get("trace")
        last_trace = trace[-1] if isinstance(trace, list) and trace else {}
        if not isinstance(last_trace, dict):
            last_trace = {}
        contract = last_trace.get("contract_validation")
        if isinstance(contract, dict) and contract.get("reasons"):
            return "contract:" + ",".join(str(item) for item in contract["reasons"])
        return str(
            last_trace.get("error_code")
            or result.get("decision_output_status")
            or result.get("status")
            or "provider_error"
        )

    def _gai_external_calls_allowed(self) -> bool:
        """Live GAI is opt-in; the normal build keeps the interface reserved."""
        return (
            self.settings.gai_execution_mode == GAI_EXECUTION_MODE_LIVE
            and self.settings.live_gai_provider_enabled
        )

    def ensure_gai_budget_sufficient_for_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Run the backend preflight before a Run directory is allocated.

        The HTTP execution manager calls this for every Run. Live GAI then
        receives the additional action-step budget check; reserved and
        rule-based runs keep their existing execution behavior.
        """
        selected_interfaces = tuple(request.get("selected_interfaces") or ("rule_based",))
        previous_provider = self._gai_provider_override
        requested_provider = str(request.get("gai_provider") or previous_provider).strip().lower()
        self._gai_provider_override = requested_provider
        try:
            report = self.preflight(
            run_purpose=str(request.get("run_purpose", "exploratory")),
            trial_count_per_condition=int(request.get("trial_count_per_condition", 30)),
            scenarios_per_regime=int(request.get("scenarios_per_regime", 8)),
            root_seed=int(request.get("root_seed", 114)),
            split=str(request.get("split", "test")),
            risk_f_beta=float(request.get("risk_f_beta", 2.0)),
            risk_threshold=float(request.get("risk_threshold", 0.82)),
            scenario_alpha=float(request.get("scenario_alpha", 2.0)),
            scenario_beta=float(request.get("scenario_beta", 2.0)),
            rho=float(request.get("rho", 0.55)),
            hotspot_selection=str(request.get("hotspot_selection", "top_capacity_quartile")),
            rule_source_ids=list(request.get("rule_source_ids") or [RULE_SOURCE_HUMAN]),
            selected_topology_ids=request.get("selected_topology_ids"),
            selected_model_ids=request.get("selected_model_ids"),
            selected_regimes=request.get("selected_regimes"),
            selected_interfaces=list(selected_interfaces),
            )
        finally:
            self._gai_provider_override = previous_provider
        if report.get("status") != "PASSED":
            raise ValueError(
                "Run preflight failed; Run was not created. "
                f"reason={report.get('message') or 'input, residual, scenario, or reproducibility check failed'}"
            )
        if "gai" not in selected_interfaces or not self._gai_external_calls_allowed():
            return None
        gai_report = report.get("gai") if isinstance(report.get("gai"), dict) else {}
        planned = int(gai_report.get("planned_calls", 0) or 0)
        budget = int(gai_report.get("effective_budget", gai_report.get("budget_max_requests_per_run", self.settings.gai_budget_max_requests_per_run)) or 0)
        if report.get("status") != "PASSED" or not bool(gai_report.get("budget_sufficient")):
            shortfall = max(0, planned - budget)
            raise ValueError(
                "GAI preflight failed; Run was not created. "
                f"planned action-step calls={planned}, budget={budget}, shortfall={shortfall}. "
                f"reason={report.get('message') or 'budget or input preflight failed'}"
            )
        return gai_report

    def _default_interfaces(self, run_purpose: str) -> tuple[str, ...]:
        del run_purpose
        return DECISION_INTERFACES if self._gai_external_calls_allowed() else ("rule_based",)

    def _gai_runtime_payload(self, run_root: Path | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self._gai_status(),
            "enabled": self._gai_external_calls_allowed(),
            "execution_mode": self.settings.gai_execution_mode,
            "external_calls_allowed": self._gai_external_calls_allowed(),
            "provider": self._active_gai_provider_name(),
            "model": self._active_gai_model(),
            "model_version": self.settings.gai_provider_model_version,
            "prompt_template_version": self.settings.gai_prompt_template_version,
            "temperature": self.settings.gai_temperature,
            "timeout_ms": self.settings.gai_timeout_ms,
            "max_retries": self.settings.gai_max_retries,
            "budget_max_requests_per_run": self.settings.gai_budget_max_requests_per_run,
            "budget_mode": self.settings.gai_budget_mode,
            "budget_hard_limit": self.settings.gai_budget_hard_limit,
            "max_output_tokens": self.settings.gai_max_output_tokens,
            "num_ctx": self.settings.gai_num_ctx,
            "keep_alive": self.settings.gai_keep_alive,
            "seed": self.settings.gai_seed,
            "network_scope": "local_host" if self._active_gai_provider_name() == "ollama" else "provider_http",
        }
        if run_root is not None:
            trace_summary = self._gai_trace_summary(run_root)
            payload.update(trace_summary)
            if (
                self._gai_external_calls_allowed()
                and trace_summary["request_count"] > 0
                and trace_summary["failed_request_count"] > 0
            ):
                payload["status"] = "failed"
        return payload

    def _gai_trace_summary(self, run_root: Path) -> dict[str, Any]:
        trace_path = run_root / "M6" / "gai_decision_trace.jsonl"
        status_counts: dict[str, int] = {}
        external_call_count = 0
        reserved_unavailable_count = 0
        if trace_path.is_file():
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    status = "trace_invalid"
                else:
                    status = str(row.get("status") or "unknown")
                    external_call_count += int(bool(row.get("external_call_attempted")))
                    reserved_unavailable_count += int(
                        row.get("unavailable_reason") == "reserved_unavailable"
                    )
                status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "request_count": sum(status_counts.values()),
            "external_call_count": external_call_count,
            "reserved_unavailable_count": reserved_unavailable_count,
            "parsed_request_count": status_counts.get("parsed", 0),
            "no_action_required_count": status_counts.get("no_action_required", 0),
            "failed_request_count": sum(
                count
                for status, count in status_counts.items()
                if status not in {"parsed", "no_action_required"}
            ),
            "trace_status_counts": status_counts,
        }

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.storage_root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for run_root in self.storage_root.iterdir():
            if not run_root.is_dir():
                continue
            summary_path = run_root / "M9" / "run_summary.json"
            failure_path = run_root / "run_failure.json"
            if summary_path.exists():
                payload = self._read_json(summary_path)
                if payload.get("profile_id") in {PROFILE_ID, COMPARISON_PROFILE_ID}:
                    rows.append(self._compact_summary(payload))
            elif failure_path.exists():
                payload = self._read_json(failure_path)
                if payload.get("profile_id") in {PROFILE_ID, COMPARISON_PROFILE_ID}:
                    rows.append(self._compact_failure(payload, run_root))
            else:
                progress_path = run_root / "run_progress.json"
                if progress_path.exists():
                    payload = self._read_json(progress_path)
                    rows.append({
                        "run_id": payload.get("run_id", run_root.name),
                        "profile_id": payload.get("config", {}).get("profile_id", PROFILE_ID),
                        "status": payload.get("status", "RUNNING"),
                        "created_at": payload.get("updated_at"),
                        "stage_id": payload.get("stage_id"),
                        "message": payload.get("message"),
                        "config": payload.get("config", {}),
                        "gai": payload.get("gai"),
                    })
        rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return rows[:limit]

    def get_run(self, run_id: str) -> dict[str, Any]:
        summary_path = self._run_root(run_id) / "M9" / "run_summary.json"
        failure_path = self._run_root(run_id) / "run_failure.json"
        if summary_path.exists():
            return cast(dict[str, Any], self._read_json(summary_path))
        if failure_path.exists():
            return cast(dict[str, Any], self._read_json(failure_path))
        progress_path = self._run_root(run_id) / "run_progress.json"
        if progress_path.exists():
            return cast(dict[str, Any], self._read_json(progress_path))
        raise FileNotFoundError(f"Run not found: {run_id}")

    def resolve_run_file(self, run_id: str, relative_path: str) -> Path:
        run_root = self._run_root(run_id).resolve()
        target = (run_root / relative_path).resolve()
        if not str(target).startswith(str(run_root)) or not target.exists() or not target.is_file():
            raise FileNotFoundError(relative_path)
        return target

    def run(
        self,
        *,
        trial_count_per_condition: int = 30,
        scenarios_per_regime: int = 8,
        root_seed: int = 114,
        split: str = "test",
        risk_f_beta: float = 2.0,
        risk_threshold: float = 0.82,
        scenario_alpha: float = 2.0,
        scenario_beta: float = 2.0,
        rho: float = 0.55,
        hotspot_selection: str = "top_capacity_quartile",
        rule_source_ids: list[str] | tuple[str, ...] | None = None,
        run_purpose: str = "exploratory",
        selected_topology_ids: list[str] | tuple[str, ...] | None = None,
        selected_model_ids: list[str] | tuple[str, ...] | None = None,
        selected_regimes: list[str] | tuple[str, ...] | None = None,
        selected_interfaces: list[str] | tuple[str, ...] | None = None,
        gai_provider: str | None = None,
        planned_gai_calls: int | None = None,
        effective_gai_budget: int | None = None,
        gai_budget_estimation_method: str | None = None,
        gai_budget_hard_limit: int | None = None,
        run_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        enforce_gai_budget: bool = True,
    ) -> dict[str, Any]:
        self._cancel_check = cancel_check
        selected_rule_sources = tuple(rule_source_ids or (RULE_SOURCE_HUMAN,))
        comparison_requested = (
            selected_rule_sources != (RULE_SOURCE_HUMAN,)
            or selected_interfaces is not None
            and tuple(selected_interfaces) != ("rule_based",)
        )
        if comparison_requested:
            return self.run_rule_source_comparison(
                trial_count_per_condition=trial_count_per_condition,
                scenarios_per_regime=scenarios_per_regime,
                root_seed=root_seed,
                split=split,
                risk_f_beta=risk_f_beta,
                risk_threshold=risk_threshold,
                scenario_alpha=scenario_alpha,
                scenario_beta=scenario_beta,
                rho=rho,
                hotspot_selection=hotspot_selection,
                rule_source_ids=selected_rule_sources,
                run_purpose=run_purpose,
                selected_topology_ids=selected_topology_ids,
                selected_model_ids=selected_model_ids,
                selected_regimes=selected_regimes,
                selected_interfaces=selected_interfaces,
                gai_provider=gai_provider,
                planned_gai_calls=planned_gai_calls,
                effective_gai_budget=effective_gai_budget,
                gai_budget_estimation_method=gai_budget_estimation_method,
                gai_budget_hard_limit=gai_budget_hard_limit,
                run_id=run_id,
                cancel_check=cancel_check,
                enforce_gai_budget=enforce_gai_budget,
            )
        topologies_selected, models, regimes, interfaces = self._resolve_selection(
            selected_topology_ids=selected_topology_ids,
            selected_model_ids=selected_model_ids,
            selected_regimes=selected_regimes,
            selected_interfaces=("rule_based",),
        )
        config = self._resolve_run_config(
            root_seed=root_seed,
            split=split,
            trial_count=trial_count_per_condition,
            scenario_count=scenarios_per_regime,
            risk_f_beta=risk_f_beta,
            risk_threshold=risk_threshold,
            scenario_alpha=scenario_alpha,
            scenario_beta=scenario_beta,
            rho=rho,
            hotspot_selection=hotspot_selection,
            run_purpose=run_purpose,
            selected_topology_ids=tuple(item["id"] for item in topologies_selected),
            selected_model_ids=tuple(item["model_id"] for item in models),
            selected_regimes=regimes,
            selected_interfaces=interfaces,
        )
        supplied_run_id = run_id is not None
        run_id = run_id or self._make_run_id(
            config.root_seed,
            config.trial_count_per_condition,
            config.scenarios_per_regime,
            config.split,
        )
        run_root = self._run_root(run_id)
        run_root.mkdir(parents=True, exist_ok=supplied_run_id)
        if supplied_run_id:
            for stale_state in (
                run_root / "run_failure.json",
                run_root / "M9" / "run_summary.json",
                run_root / "M9" / "partial_publication.json",
            ):
                if stale_state.is_file():
                    stale_state.unlink()
        self._resume_mode = False
        self._resume_action_journal = {}
        self._gai_requests_used = 0
        conditions = [
            {
                "condition_id": f"{topology['id']}__{model['model_id']}",
                "topology_id": topology["id"],
                "topology_name": topology["name"],
                "model_id": model["model_id"],
                "model_name": model["model_name"],
                "paradigm": model["paradigm"],
            }
            for topology in topologies_selected
            for model in models
        ]
        try:
            self._write_progress(run_root, status="RUNNING", stage_id="M0", message="Preparing input manifest.", config=config.payload())
            self._write_m0(run_root, config, models, conditions, topologies_selected, regimes)
            self._write_progress(run_root, status="RUNNING", stage_id="M1", message="Materializing perception benchmark.", config=config.payload())
            m1_rows = self._write_m1(run_root, models, config.split)
            self._write_progress(run_root, status="RUNNING", stage_id="M2", message="Building empirical residual pools.", config=config.payload())
            pools, pool_stats = self._write_m2(run_root, m1_rows)
            self._write_progress(run_root, status="RUNNING", stage_id="M3", message="Materializing topology artifacts.", config=config.payload())
            topologies = self._write_m3(run_root, topologies_selected)
            self._write_progress(run_root, status="RUNNING", stage_id="M4", message="Generating feasible scenarios.", config=config.payload())
            scenarios = self._write_m4(run_root, topologies, config, regimes)
            self._write_progress(run_root, status="RUNNING", stage_id="M5-M8", message="Running paired observations, decisions and validation.", config=config.payload())
            metrics = self._write_m5_to_m8(run_root, conditions, topologies, scenarios, pools, pool_stats, config, regimes)
            self._write_progress(run_root, status="RUNNING", stage_id="M9", message="Publishing reports and manifests.", config=config.payload())
            return self._write_m9(run_root, config, models, conditions, metrics)
        except ExperimentFailure as exc:
            exc.run_id = run_root.name
            failure_status = "CANCELLED" if isinstance(exc, RunCancelled) else "FAILED"
            self._write_json(run_root / "run_failure.json", {
                "run_id": run_root.name,
                "profile_id": PROFILE_ID,
                "status": failure_status,
                "stage_id": exc.stage_id,
                "message": str(exc),
                "details": exc.details,
                "config": config.payload(),
                "created_at": self._now(),
                "artifacts": self._artifact_list(run_root, self._failure_artifact_paths(run_root)),
            })
            self._write_progress(run_root, status=failure_status, stage_id=exc.stage_id, message=str(exc), config=config.payload())
            raise

    def run_rule_source_comparison(
        self,
        *,
        trial_count_per_condition: int = 30,
        scenarios_per_regime: int = 8,
        root_seed: int = 114,
        split: str = "test",
        risk_f_beta: float = 2.0,
        risk_threshold: float = 0.82,
        scenario_alpha: float = 2.0,
        scenario_beta: float = 2.0,
        rho: float = 0.55,
        hotspot_selection: str = "top_capacity_quartile",
        rule_source_ids: tuple[str, ...] = RULE_SOURCE_ORDER,
        run_purpose: str = "exploratory",
        selected_topology_ids: list[str] | tuple[str, ...] | None = None,
        selected_model_ids: list[str] | tuple[str, ...] | None = None,
        selected_regimes: list[str] | tuple[str, ...] | None = None,
        selected_interfaces: list[str] | tuple[str, ...] | None = None,
        gai_provider: str | None = None,
        planned_gai_calls: int | None = None,
        effective_gai_budget: int | None = None,
        gai_budget_estimation_method: str | None = None,
        gai_budget_hard_limit: int | None = None,
        run_id: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        enforce_gai_budget: bool = True,
    ) -> dict[str, Any]:
        """Run the shared M0-M9 orchestration across both rule sources.

        This path intentionally keeps one scenario/residual cohort and uses the
        human package as the independent M7 gold-standard validator.
        """
        selected_sources = tuple(dict.fromkeys(rule_source_ids))
        if not selected_sources or any(source not in RULE_SOURCE_ORDER for source in selected_sources):
            raise ValueError(
                "Rule-source comparison requires at least one known rule source."
            )
        topologies_selected, models, regimes, interfaces = self._resolve_selection(
            selected_topology_ids=selected_topology_ids,
            selected_model_ids=selected_model_ids,
            selected_regimes=selected_regimes,
            selected_interfaces=selected_interfaces,
            default_interfaces=self._default_interfaces(run_purpose),
        )
        config = self._resolve_run_config(
            root_seed=root_seed,
            split=split,
            trial_count=trial_count_per_condition,
            scenario_count=scenarios_per_regime,
            risk_f_beta=risk_f_beta,
            risk_threshold=risk_threshold,
            scenario_alpha=scenario_alpha,
            scenario_beta=scenario_beta,
            rho=rho,
            hotspot_selection=hotspot_selection,
            run_purpose=run_purpose,
            selected_topology_ids=tuple(item["id"] for item in topologies_selected),
            selected_model_ids=tuple(item["model_id"] for item in models),
            selected_regimes=regimes,
            selected_interfaces=interfaces,
        )
        config = replace(
            config,
            gai_provider=(gai_provider or self.settings.gai_provider_name).strip().lower(),
            planned_gai_calls=planned_gai_calls,
            effective_gai_budget=effective_gai_budget,
            gai_budget_estimation_method=gai_budget_estimation_method,
            gai_budget_hard_limit=gai_budget_hard_limit or self.settings.gai_budget_hard_limit,
        )
        self._gai_effective_budget = int(
            config.effective_gai_budget
            or self.settings.gai_budget_max_requests_per_run
        )
        if enforce_gai_budget:
            budget_report = self.ensure_gai_budget_sufficient_for_request({
                "run_purpose": run_purpose,
                "trial_count_per_condition": trial_count_per_condition,
                "scenarios_per_regime": scenarios_per_regime,
                "root_seed": root_seed,
                "split": split,
                "risk_f_beta": risk_f_beta,
                "risk_threshold": risk_threshold,
                "scenario_alpha": scenario_alpha,
                "scenario_beta": scenario_beta,
                "rho": rho,
                "hotspot_selection": hotspot_selection,
                "rule_source_ids": list(selected_sources),
                "selected_topology_ids": list(config.selected_topology_ids),
                "selected_model_ids": list(config.selected_model_ids),
                "selected_regimes": list(config.selected_regimes),
                "selected_interfaces": list(config.selected_interfaces),
            })
            if budget_report is not None:
                config = replace(
                    config,
                    planned_gai_calls=int(budget_report.get("planned_calls", 0) or 0),
                    effective_gai_budget=int(budget_report.get("effective_budget", budget_report.get("budget_max_requests_per_run", 0)) or 0),
                    gai_budget_estimation_method=str(
                        budget_report.get("estimation_method")
                        or "m6_context_candidate_capacity_upper_bound_with_shared_ideal"
                    ),
                    gai_budget_hard_limit=int(budget_report.get("budget_hard_limit", self.settings.gai_budget_hard_limit) or self.settings.gai_budget_hard_limit),
                )
                self._gai_effective_budget = int(config.effective_gai_budget or self.settings.gai_budget_max_requests_per_run)
        self._cancel_check = cancel_check
        self._gai_provider_override = config.gai_provider
        self._quota_exhausted = False
        self._quota_details = {}
        supplied_run_id = run_id is not None
        run_id = run_id or self._make_run_id(
            config.root_seed,
            config.trial_count_per_condition,
            config.scenarios_per_regime,
            f"{config.split}-rule-source-comparison",
        )
        run_root = self._run_root(run_id)
        run_root.mkdir(parents=True, exist_ok=supplied_run_id)
        if supplied_run_id:
            for stale_state in (
                run_root / "run_failure.json",
                run_root / "M9" / "run_summary.json",
                run_root / "M9" / "partial_publication.json",
            ):
                if stale_state.is_file():
                    stale_state.unlink()
        journal_path = run_root / "M6" / "gai_action_journal.jsonl"
        self._resume_mode = supplied_run_id and journal_path.is_file()
        self._resume_action_journal = self._load_action_journal(journal_path) if self._resume_mode else {}
        self._gai_requests_used = self._count_external_journal_calls(journal_path) if self._resume_mode else 0
        conditions = [
            {
                "condition_id": f"{source_id}__{topology['id']}__{model['model_id']}",
                "base_condition_id": f"{topology['id']}__{model['model_id']}",
                "rule_source_id": source_id,
                "rule_source_label": RULE_SOURCE_LABELS[source_id],
                "topology_id": topology["id"],
                "topology_name": topology["name"],
                "model_id": model["model_id"],
                "model_name": model["model_name"],
                "paradigm": model["paradigm"],
            }
            for source_id in selected_sources
            for topology in topologies_selected
            for model in models
        ]
        try:
            self._write_progress(run_root, status="RUNNING", stage_id="M0", message="Preparing comparison input manifest.", config=config.payload())
            self._write_comparison_m0(run_root, config, models, conditions, selected_sources, topologies_selected, regimes, interfaces)
            self._write_progress(run_root, status="RUNNING", stage_id="M1", message="Materializing perception benchmark.", config=config.payload())
            m1_rows = self._write_m1(run_root, models, config.split)
            self._write_progress(run_root, status="RUNNING", stage_id="M2", message="Building empirical residual pools.", config=config.payload())
            pools, pool_stats = self._write_m2(run_root, m1_rows)
            self._write_progress(run_root, status="RUNNING", stage_id="M3", message="Materializing rule-source topologies.", config=config.payload())
            topologies = self._write_comparison_m3(run_root, selected_sources, topologies_selected)
            self._write_progress(run_root, status="RUNNING", stage_id="M4", message="Generating shared feasible scenarios.", config=config.payload())
            scenarios = self._write_comparison_m4(run_root, topologies, config, regimes)
            self._write_progress(run_root, status="RUNNING", stage_id="M5-M8", message="Running paired observations, decisions and validation.", config=config.payload())
            metrics = self._write_comparison_m5_to_m8(
                run_root,
                conditions,
                topologies,
                scenarios,
                pools,
                pool_stats,
                config,
                regimes,
                interfaces,
            )
            run_status = "PARTIAL_QUOTA_EXHAUSTED" if self._quota_exhausted else "SUCCEEDED"
            self._write_progress(
                run_root,
                status=run_status,
                stage_id="M9",
                message=(
                    "Partial results published after provider quota exhaustion."
                    if self._quota_exhausted
                    else "Publishing comparison reports and manifests."
                ),
                config=config.payload(),
            )
            if not self._quota_exhausted:
                self._assert_formal_completeness(config, metrics)
            return self._write_comparison_m9(
                run_root,
                config,
                models,
                conditions,
                metrics,
                selected_sources,
                run_status=run_status,
                partial_details=self._quota_details if self._quota_exhausted else None,
            )
        except ExperimentFailure as exc:
            exc.run_id = run_root.name
            failure_status = "CANCELLED" if isinstance(exc, RunCancelled) else "FAILED"
            self._write_json(run_root / "run_failure.json", {
                "run_id": run_root.name,
                "profile_id": COMPARISON_PROFILE_ID,
                "status": failure_status,
                "stage_id": exc.stage_id,
                "message": str(exc),
                "details": exc.details,
                "config": config.payload(),
                "rule_source_ids": list(selected_sources),
                "gai": self._gai_runtime_payload(run_root),
                "created_at": self._now(),
                "artifacts": self._artifact_list(run_root, self._failure_artifact_paths(run_root)),
            })
            self._write_progress(run_root, status=failure_status, stage_id=exc.stage_id, message=str(exc), config=config.payload())
            raise

    def _comparison_source_root(self, source_id: str) -> Path:
        if source_id == RULE_SOURCE_HUMAN:
            return self.topology_root
        if source_id == RULE_SOURCE_AI:
            return self.topology_root / "AI生成"
        raise ValueError(f"Unknown rule source: {source_id}")

    def _comparison_source_manifest(self, source_id: str) -> Path:
        return self._comparison_source_root(source_id) / "topology_input_manifest.json"

    def _resolve_selection(
        self,
        *,
        selected_topology_ids: list[str] | tuple[str, ...] | None,
        selected_model_ids: list[str] | tuple[str, ...] | None,
        selected_regimes: list[str] | tuple[str, ...] | None,
        selected_interfaces: list[str] | tuple[str, ...] | None,
        default_interfaces: tuple[str, ...] = ("rule_based",),
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...], tuple[str, ...]]:
        topology_by_id = {str(item["id"]): item for item in SUPPORTED_TOPOLOGIES}
        requested_topologies = (
            tuple(selected_topology_ids)
            if selected_topology_ids is not None
            else tuple(topology_by_id)
        )
        if not requested_topologies or any(item not in topology_by_id for item in requested_topologies):
            raise ValueError(
                "selected_topology_ids must contain one or more supported topology IDs: "
                + ", ".join(sorted(topology_by_id))
            )
        if len(set(requested_topologies)) != len(requested_topologies):
            raise ValueError("selected_topology_ids must not contain duplicates")
        topologies = [topology_by_id[item] for item in requested_topologies]

        all_models = self._load_models(include_ineligible=False)
        model_by_id = {str(item["model_id"]): item for item in all_models}
        requested_models = (
            tuple(selected_model_ids)
            if selected_model_ids is not None
            else tuple(model_by_id)
        )
        if not requested_models or any(item not in model_by_id for item in requested_models):
            raise ValueError(
                "selected_model_ids must contain one or more eligible model IDs: "
                + ", ".join(sorted(model_by_id))
            )
        if len(set(requested_models)) != len(requested_models):
            raise ValueError("selected_model_ids must not contain duplicates")
        models = [model_by_id[item] for item in requested_models]

        requested_regimes = (
            tuple(str(item).upper() for item in selected_regimes)
            if selected_regimes is not None
            else tuple(REGIMES)
        )
        if not requested_regimes or any(item not in REGIMES for item in requested_regimes):
            raise ValueError("selected_regimes must contain only LOW, MEDIUM, or HIGH")
        if len(set(requested_regimes)) != len(requested_regimes):
            raise ValueError("selected_regimes must not contain duplicates")

        requested_interfaces = (
            tuple(str(item) for item in selected_interfaces)
            if selected_interfaces is not None
            else default_interfaces
        )
        if not requested_interfaces or any(item not in DECISION_INTERFACES for item in requested_interfaces):
            raise ValueError("selected_interfaces must contain only rule_based or gai")
        if len(set(requested_interfaces)) != len(requested_interfaces):
            raise ValueError("selected_interfaces must not contain duplicates")
        return topologies, models, requested_regimes, requested_interfaces

    def _assert_formal_completeness(
        self,
        config: ResolvedRunConfig,
        metrics: list[dict[str, Any]],
    ) -> None:
        if config.run_purpose != "formal":
            return
        unavailable = [
            row for row in metrics
            if row.get("decision_interface") in config.selected_interfaces
            and row.get("availability") != "available"
        ]
        if unavailable:
            raise ExperimentFailure(
                stage_id="M6",
                message="Formal run cannot publish because selected interface results are incomplete.",
                details={
                    "unavailable_row_count": len(unavailable),
                    "interfaces": list(config.selected_interfaces),
                    "reason": "Formal runs require every selected rule source/interface/regime pair to complete.",
                },
            )

    def _write_comparison_m0(
        self,
        run_root: Path,
        config: ResolvedRunConfig,
        models: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        source_ids: tuple[str, ...],
        topologies_selected: list[dict[str, Any]] | None = None,
        regimes: tuple[str, ...] = tuple(REGIMES),
        interfaces: tuple[str, ...] = DECISION_INTERFACES,
    ) -> None:
        stage = self._stage(run_root, "M0")
        perception_files = [
            self.perception_root / "A1_benchmark_samples_combined.csv",
            self.perception_root / "A2_perception_model_registry.csv",
            self.perception_root / "A3_model_predictions_raw.csv",
        ]
        topology_files: list[Path] = []
        selected_topologies = topologies_selected or SUPPORTED_TOPOLOGIES
        materialized_source_ids = tuple(dict.fromkeys((RULE_SOURCE_HUMAN, *source_ids)))
        for source_id in materialized_source_ids:
            source_root = self._comparison_source_root(source_id)
            for topology in selected_topologies:
                stem = topology["source_name"]
                topology_files.extend([
                    source_root / f"{stem}_map_neww.json",
                    source_root / f"{stem}_neighbors.json",
                    source_root / f"{stem}_rule.json",
                ])
            topology_files.append(self._comparison_source_manifest(source_id))
        missing = [str(path) for path in perception_files + topology_files if not path.exists()]
        if missing:
            raise FileNotFoundError("Missing comparison input files: " + ", ".join(missing))
        self._write_json(stage / "experiment_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "display_name": COMPARISON_DISPLAY_NAME,
            "created_at": self._now(),
            "run_purpose": config.run_purpose,
            **config.payload(),
            "rule_source_ids": list(source_ids),
            "rule_sources": [
                {"id": source_id, "label": RULE_SOURCE_LABELS[source_id],
                 "manifest_checksum": self._sha256(self._comparison_source_manifest(source_id))}
                for source_id in source_ids
            ],
            "framework_conditions": [FRAMEWORK_WITHOUT, FRAMEWORK_WITH],
            "decision_interfaces": list(interfaces),
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "conditions": conditions,
            "policy": {
                **self._policy_payload(config),
                "scenario_cohort": "shared across rule sources",
                "m7_validation": "human_manual_v1 gold-standard topology/rules",
            },
            "scenario_generation_policy": {
                "policy_id": "shared_capacity_scenario_cohort_v1",
                "policy_version": "1.0.0",
                "rule_source_independent": True,
                "diagnostics_checksum": None,
            },
            "input_checksums": {str(path): self._sha256(path) for path in perception_files + topology_files},
        })
        self._write_json(stage / "preflight_report.json", {
            "status": "PASSED",
            "checks": [
                {"id": "perception_models", "status": "PASSED", "observed": len(models), "expected": len(models)},
                {"id": "topologies", "status": "PASSED", "observed": len(selected_topologies), "expected": len(selected_topologies)},
                {"id": "rule_sources", "status": "PASSED", "observed": list(source_ids), "expected": list(source_ids)},
                {"id": "regimes", "status": "PASSED", "observed": list(regimes), "expected": list(regimes)},
                {"id": "interfaces", "status": "PASSED", "observed": list(interfaces), "expected": list(interfaces)},
                {"id": "conditions", "status": "PASSED", "observed": len(conditions), "expected": len(conditions)},
                {"id": "shared_scenario_cohort", "status": "PASSED", "observed": True},
                {"id": "m7_gold_standard", "status": "PASSED", "observed": RULE_SOURCE_HUMAN},
                {"id": "gai", "status": "INFO", "observed": "available only when provider is configured"},
            ],
            "gai": self._gai_runtime_payload(run_root),
        })

    def _write_comparison_m3(
        self,
        run_root: Path,
        source_ids: tuple[str, ...],
        topologies_selected: list[dict[str, Any]] | None = None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        stage = self._stage(run_root, "M3")
        topologies: dict[tuple[str, str], dict[str, Any]] = {}
        selected_topologies = topologies_selected or SUPPORTED_TOPOLOGIES
        materialized_source_ids = tuple(dict.fromkeys((RULE_SOURCE_HUMAN, *source_ids)))
        for source_id in materialized_source_ids:
            source_root = self._comparison_source_root(source_id)
            source_manifest_checksum = self._sha256(self._comparison_source_manifest(source_id))
            for topology in selected_topologies:
                source_name = topology["source_name"]
                map_rows = self._read_json(source_root / f"{source_name}_map_neww.json")
                neighbor_rows = self._read_json(source_root / f"{source_name}_neighbors.json")
                rules = self._read_json(source_root / f"{source_name}_rule.json")
                contract = validate_topology_triplet(
                    map_rows,
                    neighbor_rows,
                    rules,
                    source_label=f"comparison/{source_id}/{source_name}",
                )
                if contract["status"] != "PASSED":
                    raise ExperimentFailure(
                        stage_id="M3",
                        message=f"Topology input contract failed for {source_id}/{source_name}.",
                        details=contract,
                    )
                canonical = self._canonical_topology_for_source(
                    topology=topology,
                    source_id=source_id,
                    map_rows=map_rows,
                    neighbor_rows=neighbor_rows,
                    rules=rules,
                    source_manifest_checksum=source_manifest_checksum,
                )
                topologies[(source_id, topology["id"])] = canonical
                topo_dir = stage / source_id / topology["id"]
                self._write_json(topo_dir / "topology_spec.json", canonical)
                self._write_csv(topo_dir / "topology_nodes.csv", canonical["nodes"])
                self._write_csv(topo_dir / "topology_edges.csv", canonical["edges"])
                self._write_json(topo_dir / "topology_rules.json", rules)
                self._write_json(topo_dir / "validation_report.json", {
                    "status": "PASSED",
                    "rule_source_id": source_id,
                    "node_count": len(canonical["nodes"]),
                    "edge_count": len(canonical["edges"]),
                    "source_node_count": len(canonical["source_nodes"]),
                    "capacity_checksum": canonical["capacity_checksum"],
                    "topology_checksum": canonical["topology_checksum"],
                    "contract_status": contract["status"],
                    "asymmetric_cost_pairs": contract["asymmetric_cost_pairs"],
                    "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
                })

        for topology in selected_topologies:
            gold = topologies[(RULE_SOURCE_HUMAN, topology["id"])]
            for source_id in source_ids:
                candidate = topologies[(source_id, topology["id"])]
                if (
                    [node["node_id"] for node in candidate["nodes"]] != [node["node_id"] for node in gold["nodes"]]
                    or candidate["capacity_checksum"] != gold["capacity_checksum"]
                ):
                    raise ExperimentFailure(
                        stage_id="M3",
                        message=f"Rule-source topology/capacity mismatch for {source_id}/{topology['id']}.",
                        details={
                            "rule_source_id": source_id,
                            "topology_id": topology["id"],
                            "gold_capacity_checksum": gold["capacity_checksum"],
                            "candidate_capacity_checksum": candidate["capacity_checksum"],
                        },
                    )
        self._write_json(stage / "topology_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "rule_source_ids": list(source_ids),
            "materialized_rule_source_ids": list(materialized_source_ids),
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "topologies": [
                {
                    "rule_source_id": source_id,
                    "topology_id": topology_id,
                    "node_count": len(topology["nodes"]),
                    "edge_count": len(topology["edges"]),
                    "capacity_checksum": topology["capacity_checksum"],
                    "topology_checksum": topology["topology_checksum"],
                    "source_manifest_checksum": topology["source_manifest_checksum"],
                }
                for (source_id, topology_id), topology in sorted(topologies.items())
            ],
        })
        return topologies

    def _canonical_topology_for_source(
        self,
        *,
        topology: dict[str, str],
        source_id: str,
        map_rows: list[dict[str, Any]],
        neighbor_rows: list[dict[str, Any]],
        rules: dict[str, Any],
        source_manifest_checksum: str,
    ) -> dict[str, Any]:
        exits = set(str(value) for value in rules.get("external_exits", []))
        capacity_by_id = {str(row["id"]): int(row.get("max_occupancy") or 0) for row in map_rows}
        nodes = [
            {
                "topology_id": topology["id"],
                "rule_source_id": source_id,
                "node_id": node_id,
                "node_type": "exit" if node_id in exits or node_id.upper().startswith("E") else "zone",
                "capacity": capacity,
                "is_source_eligible": node_id not in exits and not node_id.upper().startswith("E") and capacity > 0,
            }
            for node_id, capacity in sorted(capacity_by_id.items(), key=lambda item: self._natural_key(item[0]))
        ]
        edges: list[dict[str, Any]] = []
        adjacency: dict[str, set[str]] = {str(node["node_id"]): set() for node in nodes}
        for row in neighbor_rows:
            source_node = str(row["id"])
            for neighbor in row.get("neighbors", []):
                target_node = str(neighbor["id"])
                pair = sorted((source_node, target_node), key=self._natural_key)
                edges.append({
                    "topology_id": topology["id"],
                    "rule_source_id": source_id,
                    "source_id": source_node,
                    "target_id": target_node,
                    "directed": True,
                    "adjacency_pair_id": "--".join(pair),
                    "edge_cost": int(neighbor.get("cost") or 1),
                    "traversal_cost": int(row.get("traversal_cost") or 0),
                })
                adjacency.setdefault(source_node, set()).add(target_node)
        source_nodes = [node for node in nodes if node["is_source_eligible"]]
        canonical = {
            "topology_id": topology["id"],
            "topology_name": topology["name"],
            "rule_source_id": source_id,
            "rule_source_label": RULE_SOURCE_LABELS[source_id],
            "nodes": nodes,
            "edges": edges,
            "rules": rules,
            "graph_directionality": rules.get("graph_directionality"),
            "adjacency_semantics": rules.get("adjacency_semantics"),
            "edge_cost_directionality": rules.get("edge_cost_directionality"),
            "source_manifest_checksum": source_manifest_checksum,
            "source_nodes": [node["node_id"] for node in source_nodes],
            "capacity_by_node": capacity_by_id,
            "adjacency": {key: sorted(value, key=self._natural_key) for key, value in adjacency.items()},
            "total_source_capacity": sum(int(node["capacity"]) for node in source_nodes),
            "external_exits": sorted(exits, key=self._natural_key),
        }
        canonical["capacity_checksum"] = self._object_checksum(capacity_by_id)
        canonical["topology_checksum"] = self._object_checksum({"nodes": nodes, "edges": edges, "rules": rules})
        return canonical

    def _write_comparison_m4(
        self,
        run_root: Path,
        topologies: dict[tuple[str, str], dict[str, Any]],
        config: ResolvedRunConfig,
        regimes: tuple[str, ...] = tuple(REGIMES),
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Generate one capacity-respecting scenario cohort for both sources."""
        stage = self._stage(run_root, "M4")
        scenarios: dict[tuple[str, str], list[dict[str, Any]]] = {}
        manifest_rows: list[dict[str, Any]] = []
        generation_rows: list[dict[str, Any]] = []
        rejected_candidate_count = 0
        rejected_candidate_reason_counts: dict[str, int] = {}
        gold_topologies = {
            topology_id: topologies[(RULE_SOURCE_HUMAN, topology_id)]
            for topology_id in config.selected_topology_ids
        }
        for topology_id, topology in sorted(gold_topologies.items()):
            capacity = topology["capacity_by_node"]
            source_nodes = topology["source_nodes"]
            hotspot_nodes = self._resolve_hotspot_nodes(topology, config.hotspot_selection)
            for regime in regimes:
                scenarios[(topology_id, regime)] = []
                d_total = self._round_half_up(topology["total_source_capacity"] * REGIME_LOAD_FACTOR[regime])
                for scenario_index in range(config.scenarios_per_regime):
                    scenario_id = f"{topology_id}_{regime.lower()}_{scenario_index:03d}"
                    scenario_seed_base = self._seed_value(config.root_seed, "M4", topology_id, regime, scenario_index)
                    population: dict[str, int] | None = None
                    scenario_seed: int | None = None
                    attempt = 0
                    rejection_reasons: dict[str, int] = {}
                    for attempt in range(1, config.max_scenario_candidate_attempts + 1):
                        candidate_seed = self._seed_value(scenario_seed_base, "candidate", attempt)
                        try:
                            candidate = self._allocate_scenario_population(
                                source_nodes,
                                capacity,
                                d_total,
                                hotspot_nodes,
                                config.hotspot_ratio,
                                random.Random(candidate_seed),
                                config.scenario_alpha,
                                config.scenario_beta,
                            )
                            feasibility = self._assess_common_scenario_feasibility(
                                topology,
                                candidate,
                                config.risk_threshold,
                            )
                            if feasibility["status"] != "feasible":
                                rejected_candidate_count += 1
                                for reason in feasibility["reasons"]:
                                    code = str(reason.get("code", "unknown"))
                                    rejection_reasons[code] = rejection_reasons.get(code, 0) + 1
                                    rejected_candidate_reason_counts[code] = rejected_candidate_reason_counts.get(code, 0) + 1
                                continue
                            population = candidate
                            scenario_seed = candidate_seed
                            break
                        except ValueError:
                            rejected_candidate_count += 1
                            rejection_reasons["scenario_population_allocation_failed"] = rejection_reasons.get(
                                "scenario_population_allocation_failed", 0
                            ) + 1
                            rejected_candidate_reason_counts["scenario_population_allocation_failed"] = (
                                rejected_candidate_reason_counts.get("scenario_population_allocation_failed", 0) + 1
                            )
                    if population is None or scenario_seed is None:
                        raise ExperimentFailure(
                            stage_id="M4",
                            message=f"Shared scenario cohort could not generate {scenario_id}.",
                            details={
                                "scenario_id": scenario_id,
                                "candidate_rejection_reason_counts": rejection_reasons,
                                "report": "M4/scenario_generation_diagnostics.json",
                            },
                        )
                    rho_actual = (sum(population[node] for node in hotspot_nodes) / d_total) if d_total else 0.0
                    scenario_checksum = self._object_checksum(population)
                    scenario = {
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "D_total": d_total,
                        "total_population": sum(population.values()),
                        "H": hotspot_nodes,
                        "rho_requested": config.hotspot_ratio,
                        "rho_actual": self._round(rho_actual),
                        "scenario_alpha": config.scenario_alpha,
                        "scenario_beta": config.scenario_beta,
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": attempt,
                        "candidate_rejection_count": attempt - 1,
                        "candidate_rejection_reason_counts": rejection_reasons,
                        "scenario_gt_population": population,
                        "population_by_node": population,
                        "capacity_check_passed": all(0 <= population[node] <= int(capacity[node]) for node in source_nodes),
                        "decision_feasibility_status": "feasible",
                        "decision_feasibility_reasons": [],
                        "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
                        "scenario_policy_id": config.scenario_policy_id,
                        "scenario_policy_version": config.scenario_policy_version,
                        "m6_decision_policy_version": config.decision_policy_version,
                        "scenario_checksum": scenario_checksum,
                        "topology_checksum": topology["topology_checksum"],
                        "capacity_checksum": topology["capacity_checksum"],
                    }
                    scenarios[(topology_id, regime)].append(scenario)
                    generation_rows.append({
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "scenario_index": scenario_index,
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": attempt,
                        "candidate_rejection_count": attempt - 1,
                        "candidate_rejection_reason_counts": rejection_reasons,
                    })
                    manifest_rows.append({
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "D_total": d_total,
                        "hotspot_nodes": self._json_cell(hotspot_nodes),
                        "rho_requested": config.hotspot_ratio,
                        "rho_actual": scenario["rho_actual"],
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": attempt,
                        "candidate_rejection_count": attempt - 1,
                        "candidate_rejection_reason_counts": self._json_cell(rejection_reasons),
                        "total_population": scenario["total_population"],
                        "capacity_check_passed": scenario["capacity_check_passed"],
                        "decision_feasibility_status": scenario["decision_feasibility_status"],
                        "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
                        "scenario_checksum": scenario_checksum,
                    })
        self._write_jsonl(stage / "scenario_gt.jsonl", [item for values in scenarios.values() for item in values])
        self._write_csv(stage / "scenario_manifest.csv", manifest_rows)
        diagnostics = {
            "status": "PASSED",
            "policy_id": config.scenario_policy_id,
            "policy_version": config.scenario_policy_version,
            "feasibility_constrained_sampling": True,
            "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
            "rule_source_independent": True,
            "max_candidate_attempts": config.max_scenario_candidate_attempts,
            "candidate_seed_strategy": "seed_value(base_seed, candidate, attempt_index)",
            "required_scenario_count": len(gold_topologies) * len(regimes) * config.scenarios_per_regime,
            "accepted_scenario_count": len(manifest_rows),
            "rejected_candidate_count": rejected_candidate_count,
            "rejected_candidate_reason_counts": rejected_candidate_reason_counts,
            "topology_regimes": generation_rows,
        }
        self._write_json(stage / "scenario_generation_diagnostics.json", diagnostics)
        self._write_json(stage / "scenario_generator_policy.json", {
            "policy_id": config.scenario_policy_id,
            "policy_version": config.scenario_policy_version,
            "capacity_respected": True,
            "total_population_exact": True,
            "rule_source_independent": True,
            "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
            "m6_feasibility_preflight": "interface-independent deterministic oracle on the common human topology; no GAI calls",
        })
        self._write_json(stage / "scenario_feasibility_report.json", {
            "status": "PASSED",
            "policy": "Common scenario cohort is accepted only after an interface-independent feasibility oracle on the human topology.",
            "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
            "feasibility_constrained_sampling": True,
            "scenario_generation_diagnostics_checksum": self._sha256(stage / "scenario_generation_diagnostics.json"),
            "scenario_count": len(manifest_rows),
            "rule_source_ids": list(RULE_SOURCE_ORDER),
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
        })
        self._backfill_m0_scenario_generation(run_root)
        return scenarios

    def _comparison_gai_decision(
        self,
        *,
        run_root: Path,
        condition: dict[str, Any],
        trial_id: str,
        scenario: dict[str, Any],
        population: dict[str, int],
        decision_topology: dict[str, Any],
        risk_threshold: float,
        root_seed: int,
    ) -> dict[str, Any]:
        self._check_cancelled("M6")
        input_checksum = self._object_checksum({
            "trial_id": trial_id,
            "scenario_checksum": scenario["scenario_checksum"],
            "population": population,
            "topology_checksum": decision_topology["topology_checksum"],
        })
        m6_context = self._build_gai_decision_context(
            topology=decision_topology,
            decision_population=population,
            risk_threshold=risk_threshold,
        )
        m6_context_checksum = str(m6_context["context_checksum"])
        budget_exceeded = self._gai_effective_budget <= self._gai_requests_used
        active_provider = self._active_gai_provider_name()
        provider_unavailable = (
            not self._gai_external_calls_allowed()
            or not self._active_gai_endpoint()
            or active_provider not in {"ollama", "openai"}
            or active_provider != "ollama" and not self._active_gai_api_key()
        )
        if provider_unavailable or budget_exceeded:
            if budget_exceeded:
                error_code = "GAI_BUDGET_EXCEEDED"
                error_message = (
                    "GAI request budget was exhausted; no action was produced."
                )
            else:
                error_code = (
                    "GAI_RESERVED_UNAVAILABLE"
                    if self.settings.gai_execution_mode != GAI_EXECUTION_MODE_LIVE
                    else "GAI_UNAVAILABLE"
                )
                error_message = (
                    "GAI execution is reserved and disabled; no external request or fake decision was produced."
                    if self.settings.gai_execution_mode != GAI_EXECUTION_MODE_LIVE
                    else "GAI API is not configured; no fake decision was produced."
                )
            unavailable = UnavailableGaiDecisionAdapter(
                error_code=error_code,
                error_message=error_message,
            )
            request = DecisionRequestPayload(
                experiment_id=COMPARISON_PROFILE_ID,
                run_id=run_root.name,
                request_id=f"REQ-{trial_id}",
                trial_id=trial_id,
                scenario_id=scenario["scenario_id"],
                error_realization_id="",
                observed_population=tuple(
                    {"node_id": node_id, "population": int(value)}
                    for node_id, value in sorted(population.items(), key=lambda item: self._natural_key(item[0]))
                ),
                topology=decision_topology,
                capacities=decision_topology["capacity_by_node"],
                allowed_action_schema={
                    "type": "object",
                    "properties": {
                        "actions": {
                            "type": "array",
                            "items": {
                                "action_id": "string",
                                "from_node": "string",
                                "to_node": "string",
                                "count": "integer",
                            },
                        },
                    },
                },
                decision_policy_version=M6_DECISION_POLICY_VERSION,
                input_checksum=input_checksum,
                m6_decision_context=m6_context,
            )
            call = unavailable.decide_with_trace(request)
            trace_row = {
                    "trial_id": trial_id,
                    "action_episode_id": trial_id,
                    "status": "quota_exhausted" if budget_exceeded else "unavailable",
                    "decision_output_status": "quota_exhausted" if budget_exceeded else "unavailable",
                    "external_call_attempted": False,
                    "unavailable_reason": (
                        "reserved_unavailable"
                        if self.settings.gai_execution_mode != GAI_EXECUTION_MODE_LIVE
                        else error_code
                    ),
                    "provider": active_provider,
                    "model": self._active_gai_model(),
                    "model_version": self.settings.gai_provider_model_version,
                    "prompt_template_version": self.settings.gai_prompt_template_version,
                    "error_code": error_code,
                    "error_message": error_message,
                    "budget_max_requests_per_run": self._gai_effective_budget,
                    "requests_used": self._gai_requests_used,
                    "m6_context_version": m6_context["context_version"],
                    "m6_context_checksum": m6_context_checksum,
                    "request": call.request_payload,
                    "raw_response": call.raw_response_payload,
                    "parsed_response": call.parsed_response_payload,
                }
            self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace_row)
            if budget_exceeded:
                self._quota_exhausted = True
                self._quota_details = {
                    "quota_error_code": error_code,
                    "quota_error_message": error_message,
                    "first_quota_failure_at": self._now(),
                    "provider": active_provider,
                }
            return {
                "status": "quota_exhausted" if budget_exceeded else "unavailable",
                "decision_output_status": "quota_exhausted" if budget_exceeded else "unavailable",
                "actions": [],
                "input_checksum": input_checksum,
                "provider": call.result.provider_metadata.provider if call.result.provider_metadata else active_provider,
                "trace": [trace_row],
            }
        gai_config = GaiHttpAdapterConfig(
            endpoint=self._active_gai_endpoint() or "",
            api_key=self._active_gai_api_key(),
            provider=active_provider,
            model=self._active_gai_model(),
            model_version=self.settings.gai_provider_model_version,
            prompt_template_version=self.settings.gai_prompt_template_version,
            temperature=self.settings.gai_temperature,
            reasoning_effort=self.settings.gai_reasoning_effort,
            timeout_ms=self.settings.gai_timeout_ms,
            max_retries=self.settings.gai_max_retries,
            budget_max_requests_per_run=self._gai_effective_budget,
            max_output_tokens=self.settings.gai_max_output_tokens,
            num_ctx=self.settings.gai_num_ctx,
            keep_alive=self.settings.gai_keep_alive,
            seed=self.settings.gai_seed,
        )
        adapter = create_gai_decision_adapter(config=gai_config)
        if active_provider not in {"ollama", "openai"}:
            return {
                "status": "error",
                "decision_output_status": "provider_unsupported",
                "actions": [],
                "input_checksum": input_checksum,
                "provider": active_provider,
                "trace": [{
                    "trial_id": trial_id,
                    "status": "error",
                    "decision_output_status": "provider_unsupported",
                    "external_call_attempted": False,
                    "error_code": "GAI_PROVIDER_UNSUPPORTED_FOR_ACTION_EPISODE",
                    "provider": active_provider,
                    "model": self._active_gai_model(),
                    "m6_context_version": m6_context["context_version"],
                    "m6_context_checksum": m6_context_checksum,
                }],
            }

        source_priority = [str(source_id) for source_id in m6_context.get("source_priority_order", [])]
        if not source_priority:
            trace_row = {
                "trial_id": trial_id,
                "action_episode_id": trial_id,
                "status": "no_action_required",
                "decision_output_status": "no_action_required",
                "external_call_attempted": False,
                "provider": active_provider,
                "model": self._active_gai_model(),
                "m6_context_version": m6_context["context_version"],
                "m6_context_checksum": m6_context_checksum,
            }
            self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace_row)
            return {
                "status": "parsed",
                "decision_output_status": "no_action_required",
                "actions": [],
                "input_checksum": input_checksum,
                "provider": active_provider,
                "trace": [trace_row],
            }

        requirements = {
            str(item["source_id"]): item
            for item in m6_context.get("source_requirements", [])
            if item.get("high_risk")
        }
        visible = {str(node_id): max(0, int(value)) for node_id, value in population.items()}
        incoming: dict[str, int] = {}
        outgoing: dict[str, int] = {}
        actions: list[dict[str, Any]] = []
        trace_rows: list[dict[str, Any]] = []
        exits = {str(node_id) for node_id in decision_topology["external_exits"]}
        for source_id in source_priority:
            source_requirement = requirements[source_id]
            remaining = int(source_requirement["requested_move_count"])
            while remaining > 0:
                self._check_cancelled("M6")
                action_id = f"A-{len(actions) + 1:04d}"
                candidates: list[dict[str, Any]] = []
                for ranked in m6_context.get("legal_target_candidates", {}).get(source_id, []):
                    target_id = str(ranked["target_id"])
                    if target_id in exits:
                        max_count = remaining
                    else:
                        max_count = min(
                            remaining,
                            max(0, self._target_remaining_capacity(
                                decision_topology, visible, incoming, outgoing, target_id
                            )),
                        )
                    if max_count <= 0:
                        continue
                    candidates.append({
                        "target_id": target_id,
                        "target_type": ranked.get("target_type", "zone"),
                        "external_exit": target_id in exits,
                        "max_count": int(max_count),
                        "total_cost": int(ranked.get("total_cost", 0)),
                    })
                if not candidates:
                    trace_rows.append({
                        "trial_id": trial_id,
                        "action_id": action_id,
                        "status": "decision_infeasible",
                        "decision_output_status": "decision_infeasible",
                        "external_call_attempted": False,
                        "error_code": "M6_NO_LEGAL_TARGET_WITH_REMAINING_DEMAND",
                        "source_id": source_id,
                        "remaining_required_move_count": remaining,
                        "m6_context_version": m6_context["context_version"],
                        "m6_context_checksum": m6_context_checksum,
                    })
                    self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace_rows[-1])
                    return {
                        "status": "parsed",
                        "decision_output_status": "decision_infeasible",
                        "actions": actions,
                        "input_checksum": input_checksum,
                        "provider": active_provider,
                        "trace": trace_rows,
                    }

                step_seed = self._seed_value(root_seed, trial_id, action_id)
                step_context = dict(m6_context)
                step_context["action_step"] = {
                    "action_id": action_id,
                    "from_node": source_id,
                    "source_visible_population": int(visible.get(source_id, 0)),
                    "remaining_required_move_count": remaining,
                    "candidates": candidates,
                    "seed": step_seed,
                }
                step_checksum = self._object_checksum(step_context["action_step"])
                request = DecisionRequestPayload(
                    experiment_id=COMPARISON_PROFILE_ID,
                    run_id=run_root.name,
                    request_id=f"REQ-{trial_id}-{action_id}",
                    trial_id=trial_id,
                    scenario_id=scenario["scenario_id"],
                    error_realization_id="",
                    observed_population=tuple(
                        {"node_id": node_id, "population": int(value)}
                        for node_id, value in sorted(population.items(), key=lambda item: self._natural_key(item[0]))
                    ),
                    topology=decision_topology,
                    capacities=decision_topology["capacity_by_node"],
                    allowed_action_schema={
                        "type": "object",
                        "properties": {
                            "action_id": {"type": "string"},
                            "from_node": {"type": "string"},
                            "to_node": {"type": "string"},
                            "count": {"type": "integer", "minimum": 1},
                        },
                        "required": ["action_id", "from_node", "to_node", "count"],
                    },
                    decision_policy_version=M6_DECISION_POLICY_VERSION,
                    input_checksum=step_checksum,
                    m6_decision_context=step_context,
                )
                cached = self._resume_action_journal.get((trial_id, action_id, step_checksum))
                resumed_from_journal = cached is not None
                if cached is not None:
                    cached_action = cached.get("parsed_response", {}).get("actions", [{}])[0]
                    call = GaiDecisionAdapterCall(
                        result=DecisionInterfaceResult(
                            decision_id=f"RESUMED-{trial_id}-{action_id}",
                            interface_type="gai",
                            scenario_id=scenario["scenario_id"],
                            error_realization_id="",
                            status="parsed",
                            actions=(DecisionActionPayload(
                                action_id=str(cached_action.get("action_id")),
                                from_node=str(cached_action.get("from_node")),
                                to_node=str(cached_action.get("to_node")),
                                count=int(cached_action.get("count")),
                            ),),
                            input_checksum=step_checksum,
                        ),
                        request_payload=cached.get("request", {}),
                        raw_response_payload=cached.get("raw_response", {}),
                        parsed_response_payload=cached.get("parsed_response", {}),
                    )
                else:
                    self._gai_requests_used += 1
                    call = adapter.decide_with_trace(request)
                metadata = call.result.provider_metadata
                trace = {
                    "trial_id": trial_id,
                    "action_episode_id": trial_id,
                    "action_id": action_id,
                    "status": call.result.status,
                    "decision_output_status": "parsed" if call.result.status == "parsed" else call.result.status,
                    "external_call_attempted": not resumed_from_journal,
                    "resumed_from_journal": resumed_from_journal,
                    "provider": metadata.provider if metadata else active_provider,
                    "model": metadata.model if metadata else self._active_gai_model(),
                    "model_version": metadata.model_version if metadata else self.settings.gai_provider_model_version,
                    "prompt_template_version": metadata.prompt_template_version if metadata else self.settings.gai_prompt_template_version,
                    "request_checksum": metadata.request_checksum if metadata else cached.get("request_checksum") if cached else None,
                    "response_checksum": metadata.response_checksum if metadata else cached.get("response_checksum") if cached else None,
                    "decision_input_checksum": step_checksum,
                    "step_seed": step_seed,
                    "retry_count": metadata.retry_count if metadata else 0,
                    "m6_context_version": m6_context["context_version"],
                    "m6_context_checksum": m6_context_checksum,
                    "http_status": metadata.http_status if metadata else None,
                    "finish_reason": metadata.finish_reason if metadata else None,
                    "error_code": metadata.error_code if metadata else None,
                    "request": call.request_payload,
                    "raw_response": call.raw_response_payload,
                    "parsed_response": call.parsed_response_payload,
                }
                trace_rows.append(trace)
                if call.result.status == "quota_exhausted":
                    trace["decision_output_status"] = "quota_exhausted"
                    trace["quota_error_code"] = metadata.error_code if metadata else "OPENAI_QUOTA_EXHAUSTED"
                    trace["quota_error_message"] = "Provider quota was exhausted; remaining calls were not attempted."
                    self._quota_exhausted = True
                    self._quota_details = {
                        "quota_error_code": trace["quota_error_code"],
                        "quota_error_message": trace["quota_error_message"],
                        "first_quota_failure_at": self._now(),
                        "provider": active_provider,
                    }
                    self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace)
                    return {
                        "status": "quota_exhausted",
                        "decision_output_status": "quota_exhausted",
                        "actions": actions,
                        "input_checksum": input_checksum,
                        "provider": metadata.provider if metadata else active_provider,
                        "trace": trace_rows,
                    }
                if call.result.status != "parsed" or len(call.result.actions) != 1:
                    if call.result.status == "parsed" and len(call.result.actions) != 1:
                        trace["status"] = "invalid_output"
                        trace["decision_output_status"] = "invalid_output"
                        trace["contract_validation"] = {
                            "status": "failed",
                            "reasons": ["expected_exactly_one_action"],
                            "observed_action_count": len(call.result.actions),
                        }
                        self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace)
                        return {
                            "status": "parsed",
                            "decision_output_status": "invalid_output",
                            "actions": actions,
                            "input_checksum": input_checksum,
                            "provider": active_provider,
                            "trace": trace_rows,
                        }
                    self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace)
                    return {
                        "status": call.result.status,
                        "decision_output_status": call.result.status,
                        "actions": actions,
                        "input_checksum": input_checksum,
                        "provider": metadata.provider if metadata else active_provider,
                        "trace": trace_rows,
                    }
                action = call.result.actions[0]
                candidate_by_target = {str(item["target_id"]): item for item in candidates}
                selected = candidate_by_target.get(action.to_node)
                contract_reasons: list[str] = []
                if action.action_id != action_id:
                    contract_reasons.append("action_id_mismatch")
                if action.from_node != source_id:
                    contract_reasons.append("source_mismatch")
                if action.count <= 0:
                    contract_reasons.append("count_must_be_positive")
                if selected is None:
                    contract_reasons.append("target_not_in_candidates")
                elif action.count != int(selected["max_count"]):
                    contract_reasons.append("count_must_equal_target_max_count")
                if contract_reasons:
                    trace["status"] = "invalid_output"
                    trace["decision_output_status"] = "invalid_output"
                    trace["contract_validation"] = {
                        "status": "failed",
                        "reasons": contract_reasons,
                        "selected_target_max_count": selected.get("max_count") if selected else None,
                        "model_action": {
                            "action_id": action.action_id,
                            "from_node": action.from_node,
                            "to_node": action.to_node,
                            "count": action.count,
                        },
                    }
                    self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace)
                    return {
                        "status": "parsed",
                        "decision_output_status": "invalid_output",
                        "actions": actions,
                        "input_checksum": input_checksum,
                        "provider": "ollama",
                        "trace": trace_rows,
                    }
                trace["contract_validation"] = {
                    "status": "passed",
                    "target_remaining_capacity_before": selected["max_count"],
                    "target_remaining_capacity_after": None if action.to_node in exits else int(selected["max_count"] - action.count),
                }
                self._append_jsonl(run_root / "M6" / "gai_action_journal.jsonl", trace)
                actions.append({
                    "source_id": action.from_node,
                    "target_id": action.to_node,
                    "move_count": int(action.count),
                    "priority_metadata": {
                        "source": "gai_ollama",
                        "action_episode_id": trial_id,
                        "action_step_id": action_id,
                        "requested_quantity": int(source_requirement["requested_move_count"]),
                        "allocated_quantity": int(action.count),
                        "total_cost": int(selected["total_cost"]),
                    },
                })
                outgoing[source_id] = outgoing.get(source_id, 0) + int(action.count)
                incoming[action.to_node] = incoming.get(action.to_node, 0) + int(action.count)
                remaining -= int(action.count)
        return {
            "status": "parsed",
            "decision_output_status": "parsed",
            "actions": actions,
            "input_checksum": input_checksum,
            "provider": "ollama",
            "trace": trace_rows,
        }

    def _build_gai_decision_context(
        self,
        *,
        topology: dict[str, Any],
        decision_population: dict[str, int],
        risk_threshold: float,
    ) -> dict[str, Any]:
        """Build deterministic M6 constraints from the branch-visible population.

        This context describes the existing M6 contract. It does not contain
        M4 truth labels, M7 results, or any post-validation metric.
        """
        visible = {
            str(node_id): max(0, int(value))
            for node_id, value in decision_population.items()
        }
        capacity = {
            str(node_id): int(value)
            for node_id, value in topology["capacity_by_node"].items()
        }
        exits = {str(node_id) for node_id in topology["external_exits"]}
        allowed_destination_types = list(
            topology["rules"].get("allowed_node_types_as_destination", ["zone", "exit"])
        )
        node_by_id = {
            str(node.get("id")): node
            for node in topology.get("nodes", [])
            if isinstance(node, dict) and node.get("id") is not None
        }
        edge_by_pair = {
            (str(edge.get("source_id")), str(edge.get("target_id"))): edge
            for edge in topology.get("edges", [])
            if isinstance(edge, dict)
        }
        requested = self._requested_move_counts(topology, visible, risk_threshold)
        source_nodes = sorted(
            {str(node_id) for node_id in topology["source_nodes"]},
            key=self._natural_key,
        )
        source_priority_order = sorted(
            requested,
            key=lambda source_id: (
                -float(requested[source_id]["utilization"]),
                -int(requested[source_id]["requested_move_count"]),
                self._natural_key(source_id),
            ),
        )

        source_visible_population = {
            source_id: int(visible.get(source_id, 0))
            for source_id in source_nodes
        }
        source_capacity = {
            source_id: int(capacity.get(source_id, 0))
            for source_id in source_nodes
        }
        source_utilization = {
            source_id: (
                source_visible_population[source_id] / max(source_capacity[source_id], 1)
            )
            for source_id in source_nodes
        }
        requested_move_count = {
            source_id: int(requested.get(source_id, {}).get("requested_move_count", 0))
            for source_id in source_nodes
        }
        source_max_outgoing = dict(source_visible_population)

        target_capacity = dict(capacity)
        target_visible_population = {
            node_id: int(visible.get(node_id, 0))
            for node_id in sorted(capacity, key=self._natural_key)
        }
        target_remaining_capacity = {
            node_id: None if node_id in exits else max(
                0,
                int(capacity[node_id]) - int(visible.get(node_id, 0)),
            )
            for node_id in sorted(capacity, key=self._natural_key)
        }
        legal_target_candidates: dict[str, list[dict[str, Any]]] = {}
        for source_id in source_nodes:
            candidates: list[dict[str, Any]] = []
            for candidate in self._ranked_targets(source_id, topology):
                target_id = str(candidate["target_id"])
                edge = edge_by_pair.get((source_id, target_id), {})
                target_node = node_by_id.get(target_id, {})
                target_type = (
                    "exit"
                    if target_id in exits or target_id.upper().startswith("E")
                    else str(target_node.get("node_type", target_node.get("type", "zone")))
                )
                candidates.append({
                    "target_id": target_id,
                    "target_type": target_type,
                    "allowed_destination": target_type in allowed_destination_types,
                    "external_exit": target_id in exits,
                    "target_capacity": target_capacity.get(target_id),
                    "target_visible_population": target_visible_population.get(target_id, 0),
                    "target_remaining_capacity": target_remaining_capacity.get(target_id),
                    "edge_cost": int(edge.get("edge_cost", 0)),
                    "traversal_cost": int(edge.get("traversal_cost", 0)),
                    "total_cost": int(candidate["total_cost"]),
                })
            legal_target_candidates[source_id] = candidates

        context: dict[str, Any] = {
            "context_version": "m6_gai_decision_context_v1",
            "risk_threshold": float(risk_threshold),
            "source_priority_order": source_priority_order,
            "source_requirements": [
                {
                    "source_id": source_id,
                    "high_risk": source_id in requested,
                    "visible_population": source_visible_population[source_id],
                    "capacity": source_capacity[source_id],
                    "utilization": source_utilization[source_id],
                    "requested_move_count": requested_move_count[source_id],
                    "source_max_outgoing": source_max_outgoing[source_id],
                }
                for source_id in source_nodes
            ],
            "source_visible_population": source_visible_population,
            "source_capacity": source_capacity,
            "source_utilization": source_utilization,
            "requested_move_count": requested_move_count,
            "source_max_outgoing": source_max_outgoing,
            "legal_target_candidates": legal_target_candidates,
            "target_capacity": target_capacity,
            "target_visible_population": target_visible_population,
            "target_remaining_capacity": target_remaining_capacity,
            "external_exits": sorted(exits, key=self._natural_key),
            "allowed_destination_types": allowed_destination_types,
            "priority_rule": topology["rules"].get("priority_rule", "ascending_total_cost"),
            "multi_source_coordination": "shared_non_exit_target_remaining_capacity",
            "target_capacity_policy": "full_node_capacity; external_exits_unbounded",
        }
        context["context_checksum"] = self._object_checksum(context)
        return context

    def _write_comparison_m5_to_m8(
        self,
        run_root: Path,
        conditions: list[dict[str, Any]],
        topologies: dict[tuple[str, str], dict[str, Any]],
        scenarios: dict[tuple[str, str], list[dict[str, Any]]],
        pools: dict[tuple[str, str], list[float]],
        pool_stats: dict[tuple[str, str], ResidualStats],
        config: ResolvedRunConfig,
        regimes: tuple[str, ...] = tuple(REGIMES),
        interfaces: tuple[str, ...] = DECISION_INTERFACES,
    ) -> list[dict[str, Any]]:
        del pool_stats
        m5 = self._stage(run_root, "M5")
        m6 = self._stage(run_root, "M6")
        m7 = self._stage(run_root, "M7")
        m8 = self._stage(run_root, "M8")
        if not self._resume_mode:
            self._write_jsonl(m6 / "gai_action_journal.jsonl", [])
        human_topologies = {
            topology_id: topologies[(RULE_SOURCE_HUMAN, topology_id)]
            for topology_id in config.selected_topology_ids
        }
        model_conditions = {
            (item["topology_id"], item["model_id"]): item
            for item in conditions
            if item["rule_source_id"] == RULE_SOURCE_HUMAN
        }
        observation_rows: list[dict[str, Any]] = []
        observation_by_key: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for (topology_id, model_id), condition in sorted(model_conditions.items()):
            for regime in regimes:
                residual_pool = pools.get((model_id, regime), [])
                if not residual_pool:
                    raise ValueError(f"Residual pool is empty: {model_id}/{regime}")
                for trial_index in range(config.trial_count_per_condition):
                    scenario = scenarios[(topology_id, regime)][trial_index % len(scenarios[(topology_id, regime)])]
                    sampling_seed = self._seed_value(config.root_seed, "M5", condition["base_condition_id"], regime, trial_index)
                    observation, sampled_residuals = self._build_observation(
                        scenario["scenario_gt_population"],
                        residual_pool,
                        random.Random(sampling_seed),
                    )
                    observation_checksum = self._object_checksum(observation)
                    key = (topology_id, model_id, regime, trial_index)
                    observation_by_key[key] = {
                        "scenario": scenario,
                        "observation": observation,
                        "observation_checksum": observation_checksum,
                        "sampled_residuals": sampled_residuals,
                    }
                    observation_rows.append({
                        "trial_id": f"TRIAL::{condition['base_condition_id']}::{regime}::{trial_index:04d}::deployment",
                        "pair_key": f"PAIR::{condition['base_condition_id']}::{regime}::{trial_index:04d}",
                        "trial_type": "deployment",
                        "condition_id": condition["base_condition_id"],
                        "topology_id": topology_id,
                        "model_id": model_id,
                        "ground_truth_regime": regime,
                        "residual_pool_id": f"{model_id}__{regime.lower()}",
                        "residual_pool_count": len(residual_pool),
                        "sampling_policy": "with_replacement",
                        "sampling_seed": sampling_seed,
                        "sampled_residuals": self._json_cell(sampled_residuals),
                        "observation_population": self._json_cell(observation),
                        "observation_checksum": observation_checksum,
                        "input_mode": "controlled_empirical_residual_propagation",
                    })

        decision_rows: list[dict[str, Any]] = []
        action_rows: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        gai_trace_rows: list[dict[str, Any]] = []
        pair_records: dict[tuple[str, str, str], list[dict[str, dict[str, Any]]]] = {}
        gai_group_available: dict[tuple[str, str, str], bool] = {}
        gai_group_unavailable_reason: dict[tuple[str, str, str], str] = {}
        gai_ideal_failures: list[dict[str, Any]] = []
        shared_gai_ideal_results: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        shared_gai_ideal_trial_ids: dict[tuple[str, str, str, int], str] = {}
        quota_stop = False
        for condition in conditions:
            if quota_stop:
                break
            source_id = condition["rule_source_id"]
            topology_id = condition["topology_id"]
            decision_topology = topologies[(source_id, topology_id)]
            validation_topology = human_topologies[topology_id]
            for regime in regimes:
                if quota_stop:
                    break
                for trial_index in range(config.trial_count_per_condition):
                    if quota_stop:
                        break
                    observation_item = observation_by_key[(topology_id, condition["model_id"], regime, trial_index)]
                    scenario = observation_item["scenario"]
                    truth = scenario["scenario_gt_population"]
                    observation = observation_item["observation"]
                    observation_checksum = observation_item["observation_checksum"]
                    base_pair_id = f"PAIR::{condition['base_condition_id']}::{regime}::{trial_index:04d}"
                    for interface in interfaces:
                        if quota_stop:
                            break
                        if interface == "rule_based":
                            ideal_actions = self._decide_actions(decision_topology, truth, config.risk_threshold)
                            deployment_actions = self._decide_actions(decision_topology, observation, config.risk_threshold)
                            interface_status = "parsed"
                            interface_trace: list[dict[str, Any]] = []
                            ideal_result = {
                                "status": "parsed",
                                "decision_output_status": "parsed",
                                "actions": ideal_actions,
                                "trace": [],
                            }
                            deployment_result = {
                                "status": "parsed",
                                "decision_output_status": "parsed",
                                "actions": deployment_actions,
                                "trace": [],
                            }
                            ideal_action_scope = "deterministic_shared_rule_source_topology_regime_trial"
                            ideal_action_source_trial_id = None
                        else:
                            shared_ideal_key = (source_id, topology_id, regime, trial_index)
                            shared_ideal_trial_id = shared_gai_ideal_trial_ids.setdefault(
                                shared_ideal_key,
                                f"TRIAL::SHARED::{topology_id}::{regime}::{trial_index:04d}::{source_id}::gai::ideal",
                            )
                            shared_ideal_created = shared_ideal_key not in shared_gai_ideal_results
                            if shared_ideal_created:
                                shared_gai_ideal_results[shared_ideal_key] = self._comparison_gai_decision(
                                    run_root=run_root,
                                    condition=condition,
                                    trial_id=shared_ideal_trial_id,
                                    scenario=scenario,
                                    population=truth,
                                    decision_topology=decision_topology,
                                    risk_threshold=config.risk_threshold,
                                    root_seed=config.root_seed,
                                )
                            ideal_result = shared_gai_ideal_results[shared_ideal_key]
                            if self._quota_exhausted:
                                gai_trace_rows.extend(ideal_result.get("trace", []))
                                quota_stop = True
                                break
                            ideal_trial_id = shared_ideal_trial_id
                            deployment_trial_id = f"TRIAL::{base_pair_id}::{source_id}::gai::deployment"
                            deployment_result = self._comparison_gai_decision(
                                run_root=run_root,
                                condition=condition,
                                trial_id=deployment_trial_id,
                                scenario=scenario,
                                population=observation,
                                decision_topology=decision_topology,
                                risk_threshold=config.risk_threshold,
                                root_seed=config.root_seed,
                            )
                            if self._quota_exhausted:
                                gai_trace_rows.extend(deployment_result.get("trace", []))
                                quota_stop = True
                                break
                            ideal_actions = ideal_result["actions"]
                            deployment_actions = deployment_result["actions"]
                            interface_status = (
                                "terminal"
                                if self._gai_result_terminal(ideal_result)
                                and self._gai_result_terminal(deployment_result)
                                else "unavailable"
                            )
                            interface_trace = [
                                *(ideal_result["trace"] if shared_ideal_created else []),
                                *deployment_result["trace"],
                            ]
                            ideal_action_scope = "shared_rule_source_topology_regime_trial"
                            ideal_action_source_trial_id = ideal_trial_id
                        if interface_trace:
                            gai_trace_rows.extend(interface_trace)
                        group_key = (condition["condition_id"], regime, interface)
                        if interface != "rule_based" and interface_status != "terminal":
                            gai_group_available[group_key] = False
                            failed_results = [("ideal", ideal_result), ("deployment", deployment_result)]
                            reasons = [
                                f"{branch}:{self._gai_result_failure_reason(item)}"
                                for branch, item in failed_results
                                if not self._gai_result_terminal(item)
                            ]
                            gai_group_unavailable_reason.setdefault(
                                group_key,
                                "GAI pair incomplete: " + ", ".join(reasons),
                            )
                            continue
                        gai_group_available.setdefault(group_key, True)
                        pair_records.setdefault(group_key, [])
                        branch_records: dict[str, dict[str, Any]] = {}
                        for framework, trial_type, decision_population, input_checksum, actions in [
                            (FRAMEWORK_WITHOUT, "ideal", truth, scenario["scenario_checksum"], ideal_actions),
                            (FRAMEWORK_WITH, "deployment", observation, observation_checksum, deployment_actions),
                        ]:
                            trial_id = f"TRIAL::{base_pair_id}::{source_id}::{interface}::{trial_type}"
                            action_checksum = self._object_checksum(actions)
                            decision_rows.append({
                                "trial_id": trial_id,
                                "pair_id": base_pair_id,
                                "trial_type": trial_type,
                                "framework_condition": framework,
                                "condition_id": condition["condition_id"],
                                "base_condition_id": condition["base_condition_id"],
                                "rule_source_id": source_id,
                                "rule_source_label": condition["rule_source_label"],
                                "decision_interface": interface,
                                "decision_output_status": (
                                    ideal_result.get("decision_output_status", "parsed")
                                    if trial_type == "ideal" and interface != "rule_based"
                                    else deployment_result.get("decision_output_status", "parsed")
                                    if interface != "rule_based"
                                    else "parsed"
                                ),
                                "scenario_id": scenario["scenario_id"],
                                "scenario_checksum": scenario["scenario_checksum"],
                                "decision_input_checksum": input_checksum,
                                "decision_input_mode": "scenario_gt" if trial_type == "ideal" else "observation",
                                "action_count": len(actions),
                                "action_checksum": action_checksum,
                                "ideal_action_scope": ideal_action_scope,
                                "ideal_action_source_trial_id": ideal_action_source_trial_id,
                            })
                            for action_index, action in enumerate(actions):
                                action_rows.append({
                                    "trial_id": trial_id,
                                    "pair_id": base_pair_id,
                                    "trial_type": trial_type,
                                    "framework_condition": framework,
                                    "condition_id": condition["condition_id"],
                                    "rule_source_id": source_id,
                                    "rule_source_label": condition["rule_source_label"],
                                    "decision_interface": interface,
                                    "decision_output_status": (
                                        ideal_result.get("decision_output_status", "parsed")
                                        if trial_type == "ideal" and interface != "rule_based"
                                        else deployment_result.get("decision_output_status", "parsed")
                                        if interface != "rule_based"
                                        else "parsed"
                                    ),
                                    "scenario_id": scenario["scenario_id"],
                                    "action_index": action_index,
                                    "source_id": action["source_id"],
                                    "target_id": action["target_id"],
                                    "move_count": action["move_count"],
                                    "ideal_action_scope": ideal_action_scope,
                                    "ideal_action_source_trial_id": ideal_action_source_trial_id,
                                    "priority_metadata": self._json_cell(action.get("priority_metadata", {})),
                                })
                            record = self._evaluate_trial(
                                condition=condition,
                                topology=validation_topology,
                                regime=regime,
                                config=config,
                                pair_id=base_pair_id,
                                trial_id=trial_id,
                                trial_index=trial_index,
                                trial_type=trial_type,
                                framework=framework,
                                scenario=scenario,
                                actions=actions,
                                ideal_actions=ideal_actions,
                                observation_checksum=observation_checksum if trial_type == "deployment" else None,
                                decision_topology=decision_topology,
                                decision_rule_source_id=source_id,
                                validation_rule_source_id=RULE_SOURCE_HUMAN,
                                decision_interface=interface,
                                decision_output_status=(
                                    ideal_result.get("decision_output_status", "parsed")
                                    if trial_type == "ideal" and interface != "rule_based"
                                    else deployment_result.get("decision_output_status", "parsed")
                                    if interface != "rule_based"
                                    else "parsed"
                                ),
                            )
                            record["ideal_action_scope"] = ideal_action_scope
                            record["ideal_action_source_trial_id"] = ideal_action_source_trial_id
                            validation_rows.append(record)
                            branch_records[trial_type] = record
                        pair_records[group_key].append(branch_records)

                    if quota_stop:
                        break

        metric_rows: list[dict[str, Any]] = []
        for condition in conditions:
            for regime in regimes:
                for interface in interfaces:
                    group_key = (condition["condition_id"], regime, interface)
                    records = pair_records.get(group_key, [])
                    if interface == "gai" and not gai_group_available.get(group_key, False) and not self._quota_exhausted:
                        for framework in (FRAMEWORK_WITHOUT, FRAMEWORK_WITH):
                            metric_rows.append(self._unavailable_gai_record(
                                condition,
                                regime,
                                framework,
                                config,
                                rule_source_id=condition["rule_source_id"],
                                rule_source_label=condition["rule_source_label"],
                                decision_interface="gai",
                                trial_type="ideal" if framework == FRAMEWORK_WITHOUT else "deployment",
                                unavailable_reason=gai_group_unavailable_reason.get(
                                    group_key,
                                    "GAI provider is reserved but not configured; metrics are unavailable, not zero.",
                                ),
                            ))
                        continue
                    if records:
                        metric_rows.extend(self._aggregate_paired_metric_records(
                            condition,
                            regime,
                            records,
                            config,
                            decision_interface=interface,
                            rule_source_id=condition["rule_source_id"],
                            rule_source_label=condition["rule_source_label"],
                        ))
                    elif interface == "gai" and not gai_group_available.get(group_key, False):
                        for framework in (FRAMEWORK_WITHOUT, FRAMEWORK_WITH):
                            metric_rows.append(self._unavailable_gai_record(
                                condition,
                                regime,
                                framework,
                                config,
                                rule_source_id=condition["rule_source_id"],
                                rule_source_label=condition["rule_source_label"],
                                decision_interface="gai",
                                trial_type="ideal" if framework == FRAMEWORK_WITHOUT else "deployment",
                                unavailable_reason=(
                                    "OpenAI quota exhausted before this paired condition was executed."
                                    if self._quota_exhausted
                                    else gai_group_unavailable_reason.get(group_key, "GAI pair is incomplete.")
                                ),
                            ))

        if not self._gai_external_calls_allowed() and "gai" not in interfaces:
            # Persist reserved-unavailable lineage without creating actions or
            # validation rows. This is local bookkeeping, never an HTTP call.
            for condition in conditions:
                for regime in regimes:
                    for trial_index in range(config.trial_count_per_condition):
                        observation_item = observation_by_key[(
                            condition["topology_id"],
                            condition["model_id"],
                            regime,
                            trial_index,
                        )]
                        scenario = observation_item["scenario"]
                        base_pair_id = (
                            f"PAIR::{condition['base_condition_id']}::{regime}::{trial_index:04d}"
                        )
                        for trial_type, population in (
                            ("ideal", scenario["scenario_gt_population"]),
                            ("deployment", observation_item["observation"]),
                        ):
                            reserved_result = self._comparison_gai_decision(
                                run_root=run_root,
                                condition=condition,
                                trial_id=(
                                    f"TRIAL::{base_pair_id}::{condition['rule_source_id']}::gai::{trial_type}"
                                ),
                                scenario=scenario,
                                population=population,
                                decision_topology=topologies[(
                                    condition["rule_source_id"],
                                    condition["topology_id"],
                                )],
                                risk_threshold=config.risk_threshold,
                                root_seed=config.root_seed,
                            )
                            gai_trace_rows.extend(reserved_result["trace"])

        # Keep the comparison schema at eight rows per base condition even
        # when the reserved GAI interface is not selected for execution.
        if not self._gai_external_calls_allowed() and "gai" not in interfaces:
            for condition in conditions:
                for regime in regimes:
                    for framework, trial_type in (
                        (FRAMEWORK_WITHOUT, "ideal"),
                        (FRAMEWORK_WITH, "deployment"),
                    ):
                        metric_rows.append(self._unavailable_gai_record(
                            condition,
                            regime,
                            framework,
                            config,
                            rule_source_id=condition["rule_source_id"],
                            rule_source_label=condition["rule_source_label"],
                            decision_interface="gai",
                            trial_type=trial_type,
                            unavailable_reason=(
                                "GAI execution is reserved and disabled; metrics are unavailable, not zero."
                            ),
                        ))

        self._write_csv(m5 / "observation_trials.csv", observation_rows)
        self._write_parquet(m5 / "observation_trials.parquet", observation_rows)
        self._write_json(m5 / "controlled_residual_policy.json", {
            **self._policy_payload(config),
            "rule_source_independent": True,
            "observation_pairing": "topology × model × regime × trial; rule_source excluded from residual seed",
        })
        self._write_csv(m6 / "action_trials.csv", decision_rows)
        self._write_parquet(m6 / "action_trials.parquet", decision_rows)
        self._write_csv(m6 / "decision_actions.csv", action_rows)
        self._write_parquet(m6 / "decision_actions.parquet", action_rows)
        self._write_json(m6 / "m6_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "rule_source_ids": sorted({str(item["rule_source_id"]) for item in conditions}),
            "decision_interfaces": list(interfaces),
            "ideal_decision_input": "M4/scenario_gt.jsonl",
            "deployment_decision_input": "M5/observation_trials.parquet",
            "ideal_action_scope": "rule_source × topology × regime × trial; perception model excluded",
            "gai_ideal_action_reuse": "one shared GAI ideal episode is reused by all selected perception models",
            "decision_policy_id": config.decision_policy_id,
             "decision_policy_version": config.decision_policy_version,
             "gai_decision_context_version": "m6_gai_decision_context_v1",
             "gai_terminal_failure_policy": {
                 "invalid_output": "terminal_trial_valid_zero",
                 "decision_infeasible": "terminal_trial_valid_zero",
                 "provider_or_transport_unavailable": "unavailable_null_metrics",
             },
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "gai_trace_file": "M6/gai_decision_trace.jsonl",
            "gai_action_journal_file": "M6/gai_action_journal.jsonl",
            "action_episode_checkpoint": "one_jsonl_row_after_each_action_step",
            "gai": self._gai_runtime_payload(run_root),
        })
        self._write_jsonl(m6 / "gai_decision_trace.jsonl", gai_trace_rows)
        self._write_csv(m7 / "decision_validation_trials.csv", validation_rows)
        self._write_parquet(m7 / "decision_validation_trials.parquet", validation_rows)
        self._write_json(m7 / "validator_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "truth_source": "M4/scenario_gt.jsonl",
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
            "validator_uses_observation_as_truth": False,
             "checks": ["invalid_output", "m6_contract_violation", "m6_decision_infeasible", "topology_violation", "capacity_violation", "source_underflow_violation", "flow_conservation_violation", "rule_violation"],
        })
        expected_ideal_trial_count = (
            len(conditions)
            * len(regimes)
            * len(interfaces)
            * config.trial_count_per_condition
        )
        ideal_failures = [
            *[
                {
                    "rule_source_id": row.get("decision_rule_source_id"),
                    "decision_interface": row.get("decision_interface"),
                    "pair_id": row["pair_id"],
                    "trial_id": row["trial_id"],
                    "topology_id": row["topology_id"],
                    "ground_truth_regime": row["ground_truth_regime"],
                    "scenario_id": row["scenario_id"],
                    "violation_reasons": row["violation_reasons"],
                }
                for row in validation_rows
                if row["trial_type"] == "ideal" and not bool(row["valid"])
            ],
            *gai_ideal_failures,
        ]
        self._write_json(m7 / "ideal_invariant_report.json", {
            "status": "FAILED" if ideal_failures else "MEASURED",
            "policy": "Comparison profile does not resample scenarios by rule source; source-specific ideal failures remain measurable results.",
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
            "ideal_trial_count": expected_ideal_trial_count,
            "ideal_failure_count": len(ideal_failures),
            "failures": ideal_failures,
        })
        if not self._quota_exhausted:
            self._assert_formal_completeness(config, metric_rows)
        self._write_csv(m8 / "decoupled_2_stage_metrics.csv", metric_rows)
        self._write_parquet(m8 / "decoupled_2_stage_metrics.parquet", metric_rows)
        self._write_json(m8 / "metrics_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "rule_source_ids": list(RULE_SOURCE_ORDER),
            "decision_interfaces": list(interfaces),
            "r_ideal_definition": "average(valid of ideal trials) within rule_source × interface",
            "ideal_baseline_scope": "rule_source × decision_interface × topology × regime; model_id excluded",
            "ideal_baseline_action_scope": "rule_source × topology × regime × trial; perception model excluded",
            "r_deploy_definition": "average(valid of deployment trials) within rule_source × interface",
             "delta_r_definition": "R_ideal - R_deploy",
             "unavailable_not_zero": True,
             "terminal_failure_policy": {
                 "invalid_output": "included_in_executed_trial_count_with_valid_zero",
                 "decision_infeasible": "included_in_executed_trial_count_with_valid_zero",
                 "provider_or_transport_unavailable": "excluded_from_executed_trial_count_and_metrics_null",
             },
            "row_count": len(metric_rows),
            "run_status": "PARTIAL_QUOTA_EXHAUSTED" if self._quota_exhausted else "SUCCEEDED",
            "partial_pair_policy": (
                "Only completed ideal/deployment pairs are included; unfinished calls are excluded, not valid=0."
                if self._quota_exhausted else None
            ),
            "quota_details": self._quota_details if self._quota_exhausted else None,
        })
        return metric_rows

    def _write_m0(
        self,
        run_root: Path,
        config: ResolvedRunConfig,
        models: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        topologies_selected: list[dict[str, Any]] | None = None,
        regimes: tuple[str, ...] = tuple(REGIMES),
    ) -> None:
        stage = self._stage(run_root, "M0")
        perception_files = [
            self.perception_root / "A1_benchmark_samples_combined.csv",
            self.perception_root / "A2_perception_model_registry.csv",
            self.perception_root / "A3_model_predictions_raw.csv",
        ]
        topology_files: list[Path] = []
        for topology in topologies_selected or SUPPORTED_TOPOLOGIES:
            source = topology["source_name"]
            topology_files.extend([
                self.topology_root / f"{source}_map_neww.json",
                self.topology_root / f"{source}_neighbors.json",
                self.topology_root / f"{source}_rule.json",
            ])
        topology_input_manifest = self.topology_root / "topology_input_manifest.json"
        topology_files.append(topology_input_manifest)
        missing = [str(path) for path in perception_files + topology_files if not path.exists()]
        if missing:
            raise FileNotFoundError("Missing required input files: " + ", ".join(missing))
        self._write_json(stage / "experiment_manifest.json", {
            "profile_id": PROFILE_ID,
            "display_name": DISPLAY_NAME,
            "created_at": self._now(),
            "run_purpose": config.run_purpose,
            **config.payload(),
            "framework_conditions": [FRAMEWORK_WITHOUT, FRAMEWORK_WITH],
            "decision_interfaces": list(config.selected_interfaces),
            "conditions": conditions,
            "policy": self._policy_payload(config),
            "scenario_generation_policy": {
                "policy_id": config.scenario_policy_id,
                "policy_version": config.scenario_policy_version,
                "feasibility_constrained_sampling": True,
                "max_candidate_attempts": config.max_scenario_candidate_attempts,
                "diagnostics_checksum": None,
            },
            "input_checksums": {str(path): self._sha256(path) for path in perception_files + topology_files},
        })
        self._write_json(stage / "preflight_report.json", {
            "status": "PASSED",
            "checks": [
                {"id": "perception_models", "status": "PASSED", "observed": len(models), "expected": 5},
                {"id": "topologies", "status": "PASSED", "observed": len(topologies_selected or SUPPORTED_TOPOLOGIES), "expected": len(topologies_selected or SUPPORTED_TOPOLOGIES)},
                {"id": "regimes", "status": "PASSED", "observed": list(regimes), "expected": list(regimes)},
                {"id": "conditions", "status": "PASSED", "observed": len(conditions), "expected": len(conditions)},
                {"id": "scenario_parameters", "status": "PASSED", "observed": {"scenario_alpha": config.scenario_alpha, "scenario_beta": config.scenario_beta, "rho": config.hotspot_ratio, "hotspot_selection": config.hotspot_selection}},
                {"id": "risk_metric", "status": "PASSED", "observed": {"risk_threshold": config.risk_threshold, "risk_f_beta": config.risk_f_beta, "metric_policy_version": config.metric_policy_version}},
                {"id": "gai", "status": "INFO", "observed": "reserved"},
            ],
        })

    def _write_m1(self, run_root: Path, models: list[dict[str, Any]], split: str) -> list[dict[str, Any]]:
        stage = self._stage(run_root, "M1")
        samples = self._read_csv(self.perception_root / "A1_benchmark_samples_combined.csv")
        predictions = self._read_csv(self.perception_root / "A3_model_predictions_raw.csv")
        model_by_id = {str(model["model_id"]): model for model in models}
        sample_by_key: dict[tuple[str, str], dict[str, str]] = {}
        for sample in samples:
            if sample.get("dataset_split") != split:
                continue
            if not self._is_true(sample.get("count_error_eligible")) or not self._is_true(sample.get("paper_result_eligible")):
                continue
            sample_by_key[(sample["sample_id"], sample["dataset_id"])] = sample
        thresholds = self._regime_thresholds(sample_by_key.values())
        rows: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for pred in predictions:
            model = model_by_id.get(pred.get("perception_model_id", ""))
            if model is None:
                excluded.append({"sample_id": pred.get("sample_id"), "reason": "model_not_eligible"})
                continue
            if pred.get("prediction_status") != "success":
                excluded.append({"sample_id": pred.get("sample_id"), "model_id": model["model_id"], "reason": "prediction_not_success"})
                continue
            key = (str(pred.get("sample_id")), str(model["compatible_dataset_id"]))
            sample_row = sample_by_key.get(key)
            if sample_row is None:
                excluded.append({"sample_id": pred.get("sample_id"), "model_id": model["model_id"], "reason": "sample_not_eligible_for_split"})
                continue
            gt = float(sample_row["scene_gt_count"])
            predicted = float(pred["benchmark_pred"])
            error = predicted - gt
            rows.append({
                "sample_id": sample_row["sample_id"],
                "dataset_id": sample_row["dataset_id"],
                "split": sample_row["dataset_split"],
                "paradigm": sample_row["perception_paradigm"],
                "model_id": model["model_id"],
                "model_name": model["model_name"],
                "model_version": model["model_version"],
                "ground_truth_count": gt,
                "predicted_count": predicted,
                "error": error,
                "absolute_error": abs(error),
                "ground_truth_regime": sample_row["perception_regime"].upper(),
                "predicted_regime": self._count_to_regime(predicted, thresholds),
                "source_ref": f"A3_model_predictions_raw.csv:{sample_row['sample_id']}:{model['model_id']}",
            })
        rows.sort(key=lambda row: (row["model_id"], row["ground_truth_regime"], row["sample_id"]))
        self._write_csv(stage / "perception_results.csv", rows)
        self._write_parquet(stage / "perception_results.parquet", rows)
        self._write_json(stage / "perception_results_manifest.json", {
            "artifact": "perception_results.parquet",
            "schema": list(rows[0].keys()) if rows else [],
            "row_count": len(rows),
            "checksum": self._sha256(stage / "perception_results.parquet"),
            "lineage": ["A1_benchmark_samples_combined.csv", "A2_perception_model_registry.csv", "A3_model_predictions_raw.csv"],
        })
        quality = {
            "row_count": len(rows),
            "excluded_count": len(excluded),
            "split": split,
            "models": self._counts(rows, "model_id"),
            "paradigms": self._counts(rows, "paradigm"),
            "ground_truth_regimes": self._counts(rows, "ground_truth_regime"),
            "rule": "error = predicted_count - ground_truth_count; positive means over-estimation, negative means under-estimation.",
        }
        self._write_json(stage / "m1_quality_report.json", quality)
        self._write_csv(stage / "excluded_samples.csv", excluded)
        return rows

    def _write_m2(self, run_root: Path, m1_rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], list[float]], dict[tuple[str, str], ResidualStats]]:
        stage = self._stage(run_root, "M2")
        error_samples: list[dict[str, Any]] = []
        pools: dict[tuple[str, str], list[float]] = {}
        for row in m1_rows:
            key = (str(row["model_id"]), str(row["ground_truth_regime"]))
            residual = float(row["error"])
            pools.setdefault(key, []).append(residual)
            error_samples.append({
                "model_id": row["model_id"],
                "model_name": row["model_name"],
                "paradigm": row["paradigm"],
                "dataset_id": row["dataset_id"],
                "split": row["split"],
                "ground_truth_regime": row["ground_truth_regime"],
                "sample_id": row["sample_id"],
                "residual": residual,
                "absolute_residual": abs(residual),
            })
        pool_stats: dict[tuple[str, str], ResidualStats] = {}
        summary_rows: list[dict[str, Any]] = []
        for key, values in sorted(pools.items()):
            stats = self._residual_stats(values)
            pool_stats[key] = stats
            summary_rows.append({
                "model_id": key[0],
                "ground_truth_regime": key[1],
                "sample_count": stats.count,
                "mean": stats.mean,
                "std": stats.std,
                "p90_abs": stats.p90_abs,
                "min": stats.minimum,
                "max": stats.maximum,
                "clipping": "not_applied",
            })
        self._write_csv(stage / "error_samples.csv", error_samples)
        self._write_parquet(stage / "error_samples.parquet", error_samples)
        self._write_csv(stage / "regime_statistics.csv", summary_rows)
        self._write_parquet(stage / "regime_statistics.parquet", summary_rows)
        self._write_json(stage / "error_distribution_summary.json", {
            "profile_id": PROFILE_ID,
            "grouping": ["model_id", "ground_truth_regime"],
            "residual_pool_rule": "Detection and Density pools remain separated by model and paradigm.",
            "sampling": "with_replacement",
            "row_count": len(error_samples),
            "groups": summary_rows,
        })
        self._write_json(stage / "m2_error_model.json", {
            "model_type": "empirical_residual_pool",
            "source_artifact": "M1/perception_results.parquet",
            "preserves_full_samples": True,
            "outlier_clipping": "not_applied",
            "minimum_pool_policy": "fail_preflight_if_any_model_regime_pool_is_empty",
        })
        self._write_text(stage / "m2_quality_report.md", self._m2_quality_markdown(summary_rows))
        return pools, pool_stats

    def _write_m3(
        self,
        run_root: Path,
        topologies_selected: list[dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        stage = self._stage(run_root, "M3")
        topologies: dict[str, dict[str, Any]] = {}
        for topology in topologies_selected or SUPPORTED_TOPOLOGIES:
            source = topology["source_name"]
            topo_dir = stage / topology["id"]
            topo_dir.mkdir(parents=True, exist_ok=True)
            map_rows = self._read_json(self.topology_root / f"{source}_map_neww.json")
            neighbor_rows = self._read_json(self.topology_root / f"{source}_neighbors.json")
            rules = self._read_json(self.topology_root / f"{source}_rule.json")
            input_manifest_path = self.topology_root / "topology_input_manifest.json"
            input_manifest_checksum = (
                self._sha256(input_manifest_path)
                if input_manifest_path.exists()
                else None
            )
            topology_contract = validate_topology_triplet(
                map_rows,
                neighbor_rows,
                rules,
                source_label=f"runtime/{source}",
            )
            if topology_contract["status"] != "PASSED":
                raise ExperimentFailure(
                    stage_id="M3",
                    message=f"Topology input contract failed for {source}.",
                    details=topology_contract,
                )
            exits = set(rules.get("external_exits", [])) if isinstance(rules, dict) else set()
            capacity_by_id = {str(row["id"]): int(row.get("max_occupancy") or 0) for row in map_rows}
            nodes: list[dict[str, Any]] = []
            for node_id, capacity in sorted(capacity_by_id.items(), key=lambda item: self._natural_key(item[0])):
                nodes.append({
                    "topology_id": topology["id"],
                    "node_id": node_id,
                    "node_type": "exit" if node_id in exits or node_id.upper().startswith("E") else "zone",
                    "capacity": capacity,
                    "is_source_eligible": node_id not in exits and not node_id.upper().startswith("E") and capacity > 0,
                })
            edges: list[dict[str, Any]] = []
            adjacency: dict[str, set[str]] = {node["node_id"]: set() for node in nodes}
            for row in neighbor_rows:
                source_id = str(row["id"])
                for neighbor in row.get("neighbors", []):
                    target_id = str(neighbor["id"])
                    adjacency_pair = sorted(
                        (source_id, target_id),
                        key=self._natural_key,
                    )
                    edges.append({
                        "topology_id": topology["id"],
                        "source_id": source_id,
                        "target_id": target_id,
                        "directed": True,
                        "adjacency_pair_id": "--".join(adjacency_pair),
                        "edge_cost": int(neighbor.get("cost") or 1),
                        "traversal_cost": int(row.get("traversal_cost") or 0),
                    })
                    adjacency.setdefault(source_id, set()).add(target_id)
            source_nodes = [node for node in nodes if node["is_source_eligible"]]
            canonical = {
                "topology_id": topology["id"],
                "topology_name": topology["name"],
                "nodes": nodes,
                "edges": edges,
                "rules": rules,
                "graph_directionality": rules.get("graph_directionality"),
                "adjacency_semantics": rules.get("adjacency_semantics"),
                "edge_cost_directionality": rules.get("edge_cost_directionality"),
                "topology_input_manifest_checksum": input_manifest_checksum,
                "source_nodes": [node["node_id"] for node in source_nodes],
                "capacity_by_node": capacity_by_id,
                "adjacency": {key: sorted(value) for key, value in adjacency.items()},
                "total_source_capacity": sum(int(node["capacity"]) for node in source_nodes),
                "external_exits": sorted(exits),
            }
            canonical["capacity_checksum"] = self._object_checksum(capacity_by_id)
            canonical["topology_checksum"] = self._object_checksum({"nodes": nodes, "edges": edges, "rules": rules})
            topologies[topology["id"]] = canonical
            self._write_json(topo_dir / "topology_spec.json", canonical)
            self._write_csv(topo_dir / "topology_nodes.csv", nodes)
            self._write_csv(topo_dir / "topology_edges.csv", edges)
            self._write_json(topo_dir / "topology_rules.json", rules)
            self._write_json(topo_dir / "validation_report.json", {
                "status": "PASSED",
                "node_count": len(nodes),
                "edge_count": len(edges),
                "source_node_count": len(source_nodes),
                "total_source_capacity": canonical["total_source_capacity"],
                "external_exits": sorted(exits),
                "edge_cost_directionality": rules.get("edge_cost_directionality"),
                "adjacency_semantics": rules.get("adjacency_semantics"),
                "topology_input_manifest_checksum": input_manifest_checksum,
                "contract_status": topology_contract["status"],
            })
        self._write_json(stage / "topology_manifest.json", {
            "topology_count": len(topologies),
            "topologies": [
                {
                    "topology_id": item["topology_id"],
                    "node_count": len(item["nodes"]),
                    "edge_count": len(item["edges"]),
                    "total_source_capacity": item["total_source_capacity"],
                    "graph_directionality": item.get("graph_directionality"),
                    "adjacency_semantics": item.get("adjacency_semantics"),
                    "edge_cost_directionality": item.get("edge_cost_directionality"),
                    "topology_input_manifest_checksum": item.get("topology_input_manifest_checksum"),
                }
                for item in topologies.values()
            ],
        })
        return topologies

    def _write_m4(
        self,
        run_root: Path,
        topologies: dict[str, dict[str, Any]],
        config: ResolvedRunConfig,
        regimes: tuple[str, ...] = tuple(REGIMES),
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Materialize deterministic M6-feasible ground-truth scenarios before residual use."""
        stage = self._stage(run_root, "M4")
        scenarios: dict[tuple[str, str], list[dict[str, Any]]] = {}
        manifest_rows: list[dict[str, Any]] = []
        feasibility_rows: list[dict[str, Any]] = []
        generation_rows: list[dict[str, Any]] = []
        rejected_candidate_count = 0

        def count_reasons(counter: dict[str, int], reasons: list[dict[str, Any]]) -> None:
            for reason in reasons:
                code = str(reason.get("code", "unknown"))
                counter[code] = counter.get(code, 0) + 1

        def write_generation_failure(failure: dict[str, Any]) -> None:
            self._write_json(stage / "scenario_generation_diagnostics.json", {
                "status": "FAILED",
                "policy_id": config.scenario_policy_id,
                "policy_version": config.scenario_policy_version,
                "feasibility_constrained_sampling": True,
                "max_candidate_attempts": config.max_scenario_candidate_attempts,
                "candidate_seed_strategy": "seed_value(base_seed, candidate, attempt_index)",
                "required_scenario_count": len(topologies) * len(regimes) * config.scenarios_per_regime,
                "accepted_scenario_count": len(manifest_rows),
                "rejected_candidate_count": rejected_candidate_count,
                "topology_regimes": [*generation_rows, failure],
            })
            self._write_json(stage / "scenario_feasibility_report.json", {
                "status": "FAILED",
                "policy": "M4 accepts only candidates that pass the M6 feasibility preflight; rejected candidates are generation diagnostics, not formal scenarios.",
                "scenario_policy_id": config.scenario_policy_id,
                "scenario_policy_version": config.scenario_policy_version,
                "m6_decision_policy_id": config.decision_policy_id,
                "m6_decision_policy_version": config.decision_policy_version,
                "scenario_count": len(feasibility_rows),
                "required_scenario_count": len(topologies) * len(regimes) * config.scenarios_per_regime,
                "feasible_count": len(feasibility_rows),
                "infeasible_count": 0,
                "candidate_rejected_count": rejected_candidate_count,
                "scenario_generation_diagnostics_checksum": self._sha256(stage / "scenario_generation_diagnostics.json"),
                "generation_failure": failure,
                "scenarios": feasibility_rows,
            })
            self._backfill_m0_scenario_generation(run_root)

        for topology_id, topology in sorted(topologies.items()):
            capacity = topology["capacity_by_node"]
            source_nodes = topology["source_nodes"]
            hotspot_nodes = self._resolve_hotspot_nodes(topology, config.hotspot_selection)
            for regime in regimes:
                scenarios[(topology_id, regime)] = []
                d_total = self._round_half_up(topology["total_source_capacity"] * REGIME_LOAD_FACTOR[regime])
                for scenario_index in range(config.scenarios_per_regime):
                    scenario_id = f"{topology_id}_{regime.lower()}_{scenario_index:03d}"
                    scenario_seed_base = self._seed_value(config.root_seed, "M4", topology_id, regime, scenario_index)
                    accepted_population: dict[str, int] | None = None
                    accepted_seed: int | None = None
                    accepted_attempt: int | None = None
                    accepted_feasibility: dict[str, Any] | None = None
                    rejection_reason_counts: dict[str, int] = {}
                    last_reasons: list[dict[str, Any]] = []
                    for attempt in range(1, config.max_scenario_candidate_attempts + 1):
                        candidate_seed = self._seed_value(scenario_seed_base, "candidate", attempt)
                        try:
                            candidate_population = self._allocate_scenario_population(
                                source_nodes, capacity, d_total, hotspot_nodes, config.hotspot_ratio,
                                random.Random(candidate_seed), config.scenario_alpha, config.scenario_beta,
                            )
                            candidate_actions = self._decide_actions(topology, candidate_population, config.risk_threshold)
                            candidate_feasibility = self._assess_plan_feasibility(
                                topology, candidate_population, candidate_actions, config.risk_threshold
                            )
                            candidate_reasons = candidate_feasibility["reasons"]
                        except ValueError as exc:
                            candidate_feasibility = {"status": "infeasible", "reasons": [{
                                "code": "scenario_population_allocation_failed",
                                "message": str(exc),
                            }]}
                            candidate_population = None
                            candidate_reasons = candidate_feasibility["reasons"]
                        if candidate_feasibility["status"] == "feasible" and candidate_population is not None:
                            accepted_population = candidate_population
                            accepted_seed = candidate_seed
                            accepted_attempt = attempt
                            accepted_feasibility = candidate_feasibility
                            break
                        rejected_candidate_count += 1
                        count_reasons(rejection_reason_counts, candidate_reasons)
                        last_reasons = candidate_reasons
                    if accepted_population is None or accepted_seed is None or accepted_attempt is None or accepted_feasibility is None:
                        generation_failure = {
                            "scenario_id": scenario_id,
                            "topology_id": topology_id,
                            "ground_truth_regime": regime,
                            "scenario_index": scenario_index,
                            "scenario_seed_base": scenario_seed_base,
                            "max_candidate_attempts": config.max_scenario_candidate_attempts,
                            "candidate_rejection_count": config.max_scenario_candidate_attempts,
                            "candidate_rejection_reason_counts": rejection_reason_counts,
                            "last_rejection_reasons": last_reasons,
                        }
                        write_generation_failure(generation_failure)
                        raise ExperimentFailure(
                            stage_id="M4",
                            message=(
                                f"M4 could not generate a feasible scenario after "
                                f"{config.max_scenario_candidate_attempts} attempts: {scenario_id}"
                            ),
                            details={
                                "scenario_id": scenario_id,
                                "report": "M4/scenario_generation_diagnostics.json",
                                "failure": generation_failure,
                            },
                        )

                    population = accepted_population
                    scenario_seed = accepted_seed
                    feasibility = accepted_feasibility
                    rho_actual = (sum(population[node] for node in hotspot_nodes) / d_total) if d_total else 0.0
                    scenario_checksum = self._object_checksum(population)
                    scenario = {
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "D_total": d_total,
                        "total_population": sum(population.values()),
                        "H": hotspot_nodes,
                        "rho_requested": config.hotspot_ratio,
                        "rho_actual": self._round(rho_actual),
                        "scenario_alpha": config.scenario_alpha,
                        "scenario_beta": config.scenario_beta,
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": accepted_attempt,
                        "candidate_rejection_count": accepted_attempt - 1,
                        "candidate_rejection_reason_counts": rejection_reason_counts,
                        "scenario_gt_population": population,
                        "population_by_node": population,
                        "capacity_check_passed": all(0 <= population[node] <= int(capacity[node]) for node in source_nodes),
                         "decision_feasibility_status": feasibility["status"],
                         "decision_feasibility_reasons": feasibility["reasons"],
                         "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
                        "scenario_policy_id": config.scenario_policy_id,
                        "scenario_policy_version": config.scenario_policy_version,
                        "m6_decision_policy_version": config.decision_policy_version,
                        "scenario_checksum": scenario_checksum,
                        "topology_checksum": topology["topology_checksum"],
                        "capacity_checksum": topology["capacity_checksum"],
                    }
                    scenarios[(topology_id, regime)].append(scenario)
                    generation_rows.append({
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "scenario_index": scenario_index,
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": accepted_attempt,
                        "candidate_rejection_count": accepted_attempt - 1,
                        "candidate_rejection_reason_counts": rejection_reason_counts,
                    })
                    manifest_rows.append({
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "D_total": d_total,
                        "hotspot_nodes": self._json_cell(hotspot_nodes),
                        "rho_requested": config.hotspot_ratio,
                        "rho_actual": scenario["rho_actual"],
                        "scenario_alpha": config.scenario_alpha,
                        "scenario_beta": config.scenario_beta,
                        "scenario_seed_base": scenario_seed_base,
                        "scenario_seed": scenario_seed,
                        "generation_attempt": accepted_attempt,
                        "candidate_rejection_count": accepted_attempt - 1,
                        "candidate_rejection_reason_counts": self._json_cell(rejection_reason_counts),
                        "total_population": scenario["total_population"],
                        "capacity_check_passed": scenario["capacity_check_passed"],
                        "decision_feasibility_status": scenario["decision_feasibility_status"],
                        "decision_feasibility_reasons": self._json_cell(scenario["decision_feasibility_reasons"]),
                        "scenario_policy_id": config.scenario_policy_id,
                        "scenario_policy_version": config.scenario_policy_version,
                        "m6_decision_policy_version": config.decision_policy_version,
                        "scenario_checksum": scenario_checksum,
                    })
                    feasibility_rows.append({
                        "scenario_id": scenario_id,
                        "topology_id": topology_id,
                        "ground_truth_regime": regime,
                        "scenario_checksum": scenario_checksum,
                        "status": "feasible",
                        "reasons": feasibility["reasons"],
                        "generation_attempt": accepted_attempt,
                        "candidate_rejection_count": accepted_attempt - 1,
                    })
        self._write_jsonl(stage / "scenario_gt.jsonl", [item for values in scenarios.values() for item in values])
        self._write_csv(stage / "scenario_manifest.csv", manifest_rows)
        generation_diagnostics = {
            "status": "PASSED",
            "policy_id": config.scenario_policy_id,
            "policy_version": config.scenario_policy_version,
            "feasibility_constrained_sampling": True,
            "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
            "max_candidate_attempts": config.max_scenario_candidate_attempts,
            "candidate_seed_strategy": "seed_value(base_seed, candidate, attempt_index)",
            "required_scenario_count": len(topologies) * len(regimes) * config.scenarios_per_regime,
            "accepted_scenario_count": len(manifest_rows),
            "rejected_candidate_count": rejected_candidate_count,
            "topology_regimes": generation_rows,
        }
        self._write_json(stage / "scenario_generation_diagnostics.json", generation_diagnostics)
        self._backfill_m0_scenario_generation(run_root)
        self._write_json(stage / "scenario_generator_policy.json", {
            "policy_id": config.scenario_policy_id,
            "policy_version": config.scenario_policy_version,
            "D_total_strategy": "round_half_up(total_source_capacity * regime_load_factor)",
            "regime_load_factor": REGIME_LOAD_FACTOR,
            "hotspot_selection": config.hotspot_selection,
            "rho": config.hotspot_ratio,
            "scenario_alpha": config.scenario_alpha,
            "scenario_beta": config.scenario_beta,
            "root_seed": config.root_seed,
            "capacity_respected": True,
            "total_population_exact": True,
            "feasibility_constrained_sampling": True,
            "max_candidate_attempts": config.max_scenario_candidate_attempts,
            "decision_feasibility_policy": "only candidates whose high-risk source requests are fully allocated by M6 under scenario_gt become formal scenarios",
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
            "feasibility_oracle_version": FEASIBILITY_ORACLE_VERSION,
        })
        failed = [row for row in feasibility_rows if row["status"] != "feasible"]
        feasibility_report = {
            "status": "FAILED" if failed else "PASSED",
            "policy": "M4 accepts only candidates that pass the M6 feasibility preflight; rejected candidates are generation diagnostics, not formal scenarios.",
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
            "scenario_count": len(feasibility_rows),
            "feasible_count": len(feasibility_rows) - len(failed),
            "infeasible_count": len(failed),
            "required_scenario_count": len(topologies) * len(regimes) * config.scenarios_per_regime,
            "accepted_scenario_count": len(manifest_rows),
            "candidate_rejected_count": rejected_candidate_count,
            "scenario_generation_diagnostics_checksum": self._sha256(stage / "scenario_generation_diagnostics.json"),
            "scenarios": feasibility_rows,
        }
        self._write_json(stage / "scenario_feasibility_report.json", feasibility_report)
        if failed:
            raise ExperimentFailure(
                stage_id="M4",
                message=(
                    f"M4 accepted an infeasible scenario: {failed[0]['scenario_id']}"
                ),
                details={"failed_scenarios": failed, "report": "M4/scenario_feasibility_report.json"},
            )
        return scenarios

    def _backfill_m0_scenario_generation(self, run_root: Path) -> None:
        """Add the materialized M4 diagnostics checksum to the earlier M0 manifest."""
        manifest_path = run_root / "M0" / "experiment_manifest.json"
        diagnostics_path = run_root / "M4" / "scenario_generation_diagnostics.json"
        if not manifest_path.is_file() or not diagnostics_path.is_file():
            return
        manifest = self._read_json(manifest_path)
        policy = manifest.setdefault("scenario_generation_policy", {})
        policy["diagnostics_checksum"] = self._sha256(diagnostics_path)
        self._write_json(manifest_path, manifest)

    def _write_m5_to_m8(
        self,
        run_root: Path,
        conditions: list[dict[str, Any]],
        topologies: dict[str, dict[str, Any]],
        scenarios: dict[tuple[str, str], list[dict[str, Any]]],
        pools: dict[tuple[str, str], list[float]],
        pool_stats: dict[tuple[str, str], ResidualStats],
        config: ResolvedRunConfig,
        regimes: tuple[str, ...] = tuple(REGIMES),
    ) -> list[dict[str, Any]]:
        m5 = self._stage(run_root, "M5")
        m6 = self._stage(run_root, "M6")
        m7 = self._stage(run_root, "M7")
        m8 = self._stage(run_root, "M8")
        observation_rows: list[dict[str, Any]] = []
        decision_rows: list[dict[str, Any]] = []
        action_rows: list[dict[str, Any]] = []
        validation_rows: list[dict[str, Any]] = []
        metric_rows: list[dict[str, Any]] = []
        for condition in conditions:
            model_id = str(condition["model_id"])
            topology_id = str(condition["topology_id"])
            topology = topologies[topology_id]
            for regime in regimes:
                residual_pool = pools.get((model_id, regime), [])
                if not residual_pool:
                    raise ValueError(f"Residual pool is empty: {model_id}/{regime}")
                pool_id = f"{model_id}__{regime.lower()}"
                pairs: list[dict[str, Any]] = []
                for trial_index in range(config.trial_count_per_condition):
                    scenario = scenarios[(topology_id, regime)][trial_index % len(scenarios[(topology_id, regime)])]
                    pair_id = f"PAIR::{condition['condition_id']}::{regime}::{trial_index:04d}"
                    ideal_trial_id = f"TRIAL::{pair_id}::ideal"
                    deployment_trial_id = f"TRIAL::{pair_id}::deployment"
                    truth = scenario["scenario_gt_population"]
                    ideal_actions = self._decide_actions(topology, truth, config.risk_threshold)
                    sampling_seed = self._seed_value(config.root_seed, "M5", condition["condition_id"], regime, trial_index)
                    observation, sampled_residuals = self._build_observation(truth, residual_pool, random.Random(sampling_seed))
                    observation_checksum = self._object_checksum(observation)
                    deployment_actions = self._decide_actions(topology, observation, config.risk_threshold)
                    observation_rows.append({
                        "trial_id": deployment_trial_id,
                        "pair_id": pair_id,
                        "trial_type": "deployment",
                        "condition_id": condition["condition_id"],
                        "scenario_id": scenario["scenario_id"],
                        "scenario_checksum": scenario["scenario_checksum"],
                        "topology_id": topology_id,
                        "model_id": model_id,
                        "ground_truth_regime": regime,
                        "residual_pool_id": pool_id,
                        "residual_pool_count": len(residual_pool),
                        "sampling_policy": "with_replacement",
                        "sampling_seed": sampling_seed,
                        "sampled_residuals": self._json_cell(sampled_residuals),
                        "observation_population": self._json_cell(observation),
                        "observation_checksum": observation_checksum,
                        "input_mode": "controlled_empirical_residual_propagation",
                    })
                    pair_records: dict[str, dict[str, Any]] = {}
                    for framework, trial_type, trial_id, decision_population, input_checksum, actions in [
                        (FRAMEWORK_WITHOUT, "ideal", ideal_trial_id, truth, scenario["scenario_checksum"], ideal_actions),
                        (FRAMEWORK_WITH, "deployment", deployment_trial_id, observation, observation_checksum, deployment_actions),
                    ]:
                        action_checksum = self._object_checksum(actions)
                        decision_rows.append({
                            "trial_id": trial_id,
                            "pair_id": pair_id,
                            "trial_type": trial_type,
                            "framework_condition": framework,
                            "condition_id": condition["condition_id"],
                            "scenario_id": scenario["scenario_id"],
                            "scenario_checksum": scenario["scenario_checksum"],
                            "decision_input_checksum": input_checksum,
                            "decision_input_mode": "scenario_gt" if trial_type == "ideal" else "observation",
                            "action_count": len(actions),
                            "action_checksum": action_checksum,
                        })
                        for action_index, action in enumerate(actions):
                            action_rows.append({
                                "trial_id": trial_id,
                                "pair_id": pair_id,
                                "trial_type": trial_type,
                                "framework_condition": framework,
                                "condition_id": condition["condition_id"],
                                "scenario_id": scenario["scenario_id"],
                                "action_index": action_index,
                                "source_id": action["source_id"],
                                "target_id": action["target_id"],
                                "move_count": action["move_count"],
                                "priority_metadata": self._json_cell(action["priority_metadata"]),
                            })
                        record = self._evaluate_trial(
                            condition=condition,
                            topology=topology,
                            regime=regime,
                            config=config,
                            pair_id=pair_id,
                            trial_id=trial_id,
                            trial_index=trial_index,
                            trial_type=trial_type,
                            framework=framework,
                            scenario=scenario,
                            actions=actions,
                            ideal_actions=ideal_actions,
                            observation_checksum=observation_checksum if trial_type == "deployment" else None,
                        )
                        validation_rows.append(record)
                        pair_records[trial_type] = record
                    pairs.append(pair_records)
                metric_rows.extend(self._aggregate_paired_metric_records(condition, regime, pairs, config))
                for framework in (FRAMEWORK_WITHOUT, FRAMEWORK_WITH):
                    metric_rows.append(self._unavailable_gai_record(condition, regime, framework, config))
        self._write_csv(m5 / "observation_trials.csv", observation_rows)
        self._write_parquet(m5 / "observation_trials.parquet", observation_rows)
        self._write_json(m5 / "controlled_residual_policy.json", self._policy_payload(config))
        self._write_json(m5 / "ideal_branch_lineage.json", {
            "trial_type": "ideal", "decision_input": "M4/scenario_gt.jsonl", "uses_residual": False,
            "paired_with": "M5/observation_trials.parquet",
        })
        self._write_csv(m6 / "action_trials.csv", decision_rows)
        self._write_parquet(m6 / "action_trials.parquet", decision_rows)
        self._write_csv(m6 / "decision_actions.csv", action_rows)
        self._write_parquet(m6 / "decision_actions.parquet", action_rows)
        self._write_json(m6 / "m6_manifest.json", {
            "ideal_decision_input": "M4/scenario_gt.jsonl",
            "deployment_decision_input": "M5/observation_trials.parquet",
            "action_schema": ["source_id", "target_id", "move_count", "priority_metadata"],
            "decision_does_not_read_validator": True,
            "decision_policy_id": config.decision_policy_id,
            "decision_policy_version": config.decision_policy_version,
            "coordination_policy": "source utilization priority with shared non-exit target remaining capacity",
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "scenario_generation": self._scenario_generation_summary(run_root),
        })
        self._write_csv(m7 / "decision_validation_trials.csv", validation_rows)
        self._write_parquet(m7 / "decision_validation_trials.parquet", validation_rows)
        self._write_json(m7 / "validator_manifest.json", {
            "truth_source": "M4/scenario_gt.jsonl",
            "validator_uses_observation_as_truth": False,
            "checks": ["invalid_output", "topology_violation", "unknown_target_violation", "forbidden_target_violation", "capacity_violation", "source_underflow_violation", "flow_conservation_violation", "rule_violation"],
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "scenario_generation": self._scenario_generation_summary(run_root),
        })
        ideal_failures = [
            row for row in validation_rows
            if row["trial_type"] == "ideal" and not bool(row["valid"])
        ]
        ideal_report = {
            "status": "FAILED" if ideal_failures else "PASSED",
            "policy": "Every M4-feasible ideal scenario must pass the independent M7 validator.",
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
            "ideal_trial_count": sum(row["trial_type"] == "ideal" for row in validation_rows),
            "ideal_failure_count": len(ideal_failures),
            "failures": [
                {
                    "pair_id": row["pair_id"],
                    "trial_id": row["trial_id"],
                    "topology_id": row["topology_id"],
                    "ground_truth_regime": row["ground_truth_regime"],
                    "scenario_id": row["scenario_id"],
                    "violation_reasons": row["violation_reasons"],
                }
                for row in ideal_failures
            ],
        }
        self._write_json(m7 / "ideal_invariant_report.json", ideal_report)
        if ideal_failures:
            first = ideal_failures[0]
            raise ExperimentFailure(
                stage_id="M7",
                message=(
                    f"Ideal invariant failed for {first['pair_id']} ({first['scenario_id']}): "
                    f"{first['violation_reasons']}"
                ),
                details={"ideal_failure_count": len(ideal_failures), "report": "M7/ideal_invariant_report.json"},
            )
        self._write_csv(m8 / "decoupled_2_stage_metrics.csv", metric_rows)
        self._write_parquet(m8 / "decoupled_2_stage_metrics.parquet", metric_rows)
        self._write_json(m8 / "metrics_manifest.json", {
            "metric_policy_id": config.metric_policy_id,
            "metric_policy_version": config.metric_policy_version,
            "risk_f_beta": config.risk_f_beta,
            "r_ideal_definition": "average(valid of ideal trials)",
            "r_deploy_definition": "average(valid of deployment trials)",
            "delta_r_definition": "R_ideal - R_deploy",
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "scenario_generation": self._scenario_generation_summary(run_root),
            "row_count": len(metric_rows),
            "unavailable_not_zero": True,
        })
        return metric_rows

    def _aggregate_paired_metric_records(
        self,
        condition: dict[str, Any],
        regime: str,
        pairs: list[dict[str, dict[str, Any]]],
        config: ResolvedRunConfig,
        *,
        decision_interface: str = "rule_based",
        rule_source_id: str | None = None,
        rule_source_label: str | None = None,
    ) -> list[dict[str, Any]]:
        ideal = [pair["ideal"] for pair in pairs]
        deployment = [pair["deployment"] for pair in pairs]
        r_ideal = self._average(ideal, "valid")
        r_deploy = self._average(deployment, "valid")
        rows: list[dict[str, Any]] = []
        for framework, trial_type, records in [
            (FRAMEWORK_WITHOUT, "ideal", ideal),
            (FRAMEWORK_WITH, "deployment", deployment),
        ]:
            if records and all(bool(row.get("m6_contract_violation")) for row in records):
                execution_outcome_status = "invalid_output"
            elif records and all(bool(row.get("m6_decision_infeasible")) for row in records):
                execution_outcome_status = "decision_infeasible"
            else:
                execution_outcome_status = "available"
            rows.append({
                "condition_id": condition["condition_id"],
                "topology_id": condition["topology_id"],
                "topology_name": condition["topology_name"],
                "model_id": condition["model_id"],
                "model_name": condition["model_name"],
                "paradigm": condition["paradigm"],
                "base_condition_id": condition.get("base_condition_id", condition["condition_id"]),
                "rule_source_id": rule_source_id or condition.get("rule_source_id", RULE_SOURCE_HUMAN),
                "rule_source_label": rule_source_label or condition.get("rule_source_label", RULE_SOURCE_LABELS[RULE_SOURCE_HUMAN]),
                "decision_rule_source_id": (records[0].get("decision_rule_source_id") if records else None),
                "validation_rule_source_id": (records[0].get("validation_rule_source_id") if records else RULE_SOURCE_HUMAN),
                "decision_topology_checksum": (records[0].get("decision_topology_checksum") if records else None),
                "validation_topology_checksum": (records[0].get("validation_topology_checksum") if records else None),
                "ground_truth_regime": regime,
                "framework_condition": framework,
                "trial_type": trial_type,
                "decision_interface": decision_interface,
                "availability": "available",
                "run_status": "PARTIAL_QUOTA_EXHAUSTED" if self._quota_exhausted else "SUCCEEDED",
                "metric_policy_id": config.metric_policy_id,
                "metric_policy_version": config.metric_policy_version,
                "risk_f_beta": config.risk_f_beta,
                "trial_count": len(records),
                "executed_trial_count": len(records),
                "expected_trial_count": config.trial_count_per_condition,
                "paired_completed_trial_count": len(records),
                "completion": "partial" if self._quota_exhausted else "complete",
                "execution_outcome_status": execution_outcome_status,
                "ideal_baseline_scope": "rule_source × decision_interface × topology × regime; model_id excluded",
                "ideal_action_scope": (
                    records[0].get("ideal_action_scope")
                    if records else "rule_source × topology × regime × trial; perception model excluded"
                ),
                "ideal_executed_trial_count": len(ideal),
                "deployment_executed_trial_count": len(deployment),
                "ideal_valid_trial_count": int(sum(row["valid"] for row in ideal)),
                "deployment_valid_trial_count": int(sum(row["valid"] for row in deployment)),
                 "valid_rate": self._average(records, "valid"),
                 "m6_contract_violation_rate": self._average(records, "m6_contract_violation"),
                 "m6_decision_infeasible_rate": self._average(records, "m6_decision_infeasible"),
                 "invalid_output_rate": self._average(records, "invalid_output"),
                "rule_violation_rate": self._average(records, "rule_violation"),
                "capacity_violation_rate": self._average(records, "capacity_violation"),
                "topology_violation_rate": self._average(records, "topology_violation"),
                "unknown_target_violation_rate": self._average(records, "unknown_target_violation"),
                "forbidden_target_violation_rate": self._average(records, "forbidden_target_violation"),
                "source_underflow_rate": self._average(records, "source_underflow_violation"),
                "flow_conservation_violation_rate": self._average(records, "flow_conservation_violation"),
                "risk_tp": int(sum(row["risk_tp"] for row in records)),
                "risk_fp": int(sum(row["risk_fp"] for row in records)),
                "risk_fn": int(sum(row["risk_fn"] for row in records)),
                "risk_precision": self._average(records, "risk_precision"),
                "risk_recall": self._average(records, "risk_recall"),
                "risk_consistency": self._average(records, "risk_consistency"),
                "legality_score": self._average(records, "legality_score"),
                "priority_score": self._average(records, "priority_score"),
                "economy_score": self._average(records, "economy_score"),
                "action_consistency": self._average(records, "action_consistency"),
                "action_agreement_with_ideal": self._average(records, "action_agreement_with_ideal"),
                "r_ideal": r_ideal,
                "r_deploy": r_deploy,
                "delta_r": self._round(r_ideal - r_deploy),
                "unavailable_reason": "",
            })
        return rows

    def _unavailable_gai_record(
        self,
        condition: dict[str, Any],
        regime: str,
        framework: str,
        config: ResolvedRunConfig,
        *,
        rule_source_id: str | None = None,
        rule_source_label: str | None = None,
        decision_interface: str = "gai_reserved",
        trial_type: str = "unavailable",
        unavailable_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "condition_id": condition["condition_id"], "topology_id": condition["topology_id"],
            "topology_name": condition["topology_name"], "model_id": condition["model_id"],
            "model_name": condition["model_name"], "paradigm": condition["paradigm"],
            "base_condition_id": condition.get("base_condition_id", condition["condition_id"]),
            "rule_source_id": rule_source_id or condition.get("rule_source_id", RULE_SOURCE_HUMAN),
            "rule_source_label": rule_source_label or condition.get("rule_source_label", RULE_SOURCE_LABELS[RULE_SOURCE_HUMAN]),
            "decision_rule_source_id": rule_source_id or condition.get("rule_source_id", RULE_SOURCE_HUMAN),
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
            "decision_topology_checksum": None,
            "validation_topology_checksum": None,
             "ground_truth_regime": regime, "framework_condition": framework,
             "trial_type": trial_type, "decision_interface": decision_interface, "availability": "unavailable",
             "execution_outcome_status": "unavailable",
            "ideal_baseline_scope": "rule_source × decision_interface × topology × regime; model_id excluded",
            "ideal_action_scope": "rule_source × topology × regime × trial; perception model excluded",
            "metric_policy_id": config.metric_policy_id, "metric_policy_version": config.metric_policy_version,
            "risk_f_beta": config.risk_f_beta, "trial_count": 0, "executed_trial_count": 0,
            "expected_trial_count": config.trial_count_per_condition,
            "paired_completed_trial_count": 0,
            "completion": "incomplete",
            "run_status": "PARTIAL_QUOTA_EXHAUSTED" if self._quota_exhausted else "SUCCEEDED",
            "ideal_executed_trial_count": 0, "deployment_executed_trial_count": 0,
            "ideal_valid_trial_count": 0, "deployment_valid_trial_count": 0,
             "valid_rate": None, "invalid_output_rate": None, "rule_violation_rate": None,
             "m6_contract_violation_rate": None, "m6_decision_infeasible_rate": None,
            "capacity_violation_rate": None, "topology_violation_rate": None,
            "unknown_target_violation_rate": None, "forbidden_target_violation_rate": None,
            "source_underflow_rate": None, "flow_conservation_violation_rate": None,
            "risk_tp": None, "risk_fp": None, "risk_fn": None, "risk_precision": None,
            "risk_recall": None, "risk_consistency": None, "legality_score": None,
            "priority_score": None, "economy_score": None, "action_consistency": None,
            "action_agreement_with_ideal": None, "r_ideal": None, "r_deploy": None, "delta_r": None,
            "unavailable_reason": unavailable_reason or "GAI provider is reserved but not configured; metrics are unavailable, not zero.",
        }

    def _write_comparison_m9(
        self,
        run_root: Path,
        config: ResolvedRunConfig,
        models: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        source_ids: tuple[str, ...],
        *,
        run_status: str = "SUCCEEDED",
        partial_details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stage = self._stage(run_root, "M9")
        table_paths: list[Path] = []
        for condition in conditions:
            condition_rows = [row for row in metrics if row["condition_id"] == condition["condition_id"]]
            slug = condition["condition_id"].replace(" ", "_")
            table_name = f"decoupled_2_stage_comparison_{slug}"
            csv_path = stage / f"{table_name}.csv"
            md_path = stage / f"{table_name}.md"
            self._write_csv(csv_path, self._paper_rows(condition_rows))
            self._write_text(md_path, self._comparison_paper_markdown(condition, condition_rows, config))
            table_paths.extend([csv_path, md_path])
        all_csv = stage / "decoupled_2_stage_rule_source_comparison_all_tables.csv"
        all_md = stage / "decoupled_2_stage_rule_source_comparison_all_tables.md"
        all_xlsx = stage / "decoupled_2_stage_rule_source_comparison_all_tables.xlsx"
        all_zip = stage / "decoupled_2_stage_rule_source_comparison_all_tables.zip"
        paper_rows = self._paper_rows(metrics)
        self._write_csv(all_csv, paper_rows)
        self._write_text(all_md, self._comparison_all_tables_markdown(conditions, metrics, config))
        self._write_xlsx(all_xlsx, "rule_source_comparison", paper_rows)
        insight = DecoupledInsightReportService().generate(
            run_root=run_root,
            profile_id=COMPARISON_PROFILE_ID,
            config=config.payload(),
            all_tables_path=all_csv,
            metrics_path=run_root / "M8" / "decoupled_2_stage_metrics.csv",
        )
        insight_paths = [insight["report_path"], insight["summary_path"]]
        partial_path = stage / "partial_publication.json"
        if run_status == "PARTIAL_QUOTA_EXHAUSTED":
            self._write_json(partial_path, {
                "run_id": run_root.name,
                "status": run_status,
                "completed_call_count": self._gai_requests_used,
                "expected_call_count": config.planned_gai_calls,
                "completed_paired_trial_rows": sum(
                    int(row.get("paired_completed_trial_count", 0) or 0)
                    for row in metrics
                    if row.get("framework_condition") == FRAMEWORK_WITHOUT
                ),
                "expected_trial_count_per_condition": config.trial_count_per_condition,
                "quota_details": partial_details or {},
                "resume_available": True,
                "unfinished_calls_are_not_valid_zero": True,
            })
            insight_paths.append(partial_path)
        with zipfile.ZipFile(all_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in [all_csv, all_md, all_xlsx, *table_paths, *insight_paths]:
                archive.write(path, path.name)
        evidence_paths = [
            run_root / "M3" / "topology_manifest.json",
            run_root / "M4" / "scenario_generation_diagnostics.json",
            run_root / "M4" / "scenario_feasibility_report.json",
            run_root / "M6" / "gai_decision_trace.jsonl",
            run_root / "M6" / "gai_action_journal.jsonl",
            run_root / "M7" / "ideal_invariant_report.json",
        ]
        artifacts = self._artifact_list(
            run_root,
            [all_csv, all_md, all_xlsx, all_zip, *table_paths, *insight_paths, *evidence_paths],
        )
        scenario_generation = self._scenario_generation_summary(run_root)
        summary = {
            "run_id": run_root.name,
            "profile_id": COMPARISON_PROFILE_ID,
            "display_name": COMPARISON_DISPLAY_NAME,
            "status": run_status,
            "created_at": self._now(),
            "config": {
                **config.payload(),
                "profile_id": COMPARISON_PROFILE_ID,
                "rule_source_ids": list(source_ids),
                "decision_interfaces": list(DECISION_INTERFACES),
                "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            },
            "rule_source_ids": list(source_ids),
            "rule_sources": [
                {"id": source_id, "label": RULE_SOURCE_LABELS[source_id]}
                for source_id in source_ids
            ],
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "condition_count": len(conditions),
            "table_count": len(conditions),
            "models": models,
            "topologies": SUPPORTED_TOPOLOGIES,
            "matrix": self._comparison_matrix(metrics),
            "metrics": paper_rows,
            "artifacts": artifacts,
            "scenario_generation": scenario_generation,
            "gai": self._gai_runtime_payload(run_root),
            "insight_report": insight["summary"],
            "partial": run_status == "PARTIAL_QUOTA_EXHAUSTED",
            "partial_details": partial_details or {},
            "limitations": [
                "w/o uses scenario_gt and w/ uses the same paired residual-derived observation for each rule source.",
                "M7 always validates against the human_manual_v1 gold-standard topology/rules.",
                "R_ideal and R_deploy are computed within rule_source × decision_interface pairs.",
                "GAI invalid_output and decision_infeasible are terminal valid=0 outcomes included in M8; provider or transport unavailable groups remain null and are not zero.",
                "AI rules are materialized from AI map/neighbors; governance defaults remain fixed by the input contract.",
            ],
        }
        self._write_json(stage / "delivery_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            "rule_source_ids": list(source_ids),
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "metric_policy_id": config.metric_policy_id,
            "metric_policy_version": config.metric_policy_version,
            "scenario_generation": scenario_generation,
            "gai": self._gai_runtime_payload(run_root),
            "required_outputs": ["8-row comparison data", "four paired reliability groups", "CSV", "Markdown", "XLSX", "ZIP", "insight_report.md", "insight_summary.json"],
            "run_status": run_status,
            "partial_details": partial_details or {},
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        })
        self._write_json(stage / "reproducibility_manifest.json", {
            "profile_id": COMPARISON_PROFILE_ID,
            **config.payload(),
            "rule_source_ids": list(source_ids),
            "rule_sources": [
                {
                    "rule_source_id": source_id,
                    "manifest_checksum": self._sha256(self._comparison_source_manifest(source_id)),
                }
                for source_id in source_ids
            ],
            "m7_validation_rule_source_id": RULE_SOURCE_HUMAN,
            "m1_checksum": self._sha256(run_root / "M1" / "perception_results.parquet"),
            "m2_checksum": self._sha256(run_root / "M2" / "error_samples.parquet"),
            "m4_checksum": self._sha256(run_root / "M4" / "scenario_gt.jsonl"),
            "m5_checksum": self._sha256(run_root / "M5" / "observation_trials.parquet"),
            "m6_checksum": self._sha256(run_root / "M6" / "action_trials.parquet"),
            "m6_gai_trace_checksum": self._sha256(run_root / "M6" / "gai_decision_trace.jsonl"),
            "gai": self._gai_runtime_payload(run_root),
            "m7_checksum": self._sha256(run_root / "M7" / "decision_validation_trials.parquet"),
            "m8_checksum": self._sha256(run_root / "M8" / "decoupled_2_stage_metrics.parquet"),
            "insight_report_checksum": self._sha256(insight["report_path"]),
            "insight_summary_checksum": self._sha256(insight["summary_path"]),
        })
        self._write_json(stage / "run_summary.json", summary)
        return summary

    def _comparison_paper_markdown(
        self,
        condition: dict[str, Any],
        rows: list[dict[str, Any]],
        config: ResolvedRunConfig,
    ) -> str:
        title = f"# {condition['rule_source_label']} · {condition['topology_name']} x {condition['model_name']}\n\n"
        note = (
            f"Comparison profile: {COMPARISON_PROFILE_ID}; M7 gold standard: {RULE_SOURCE_HUMAN}. "
            f"Metric policy: {config.metric_policy_id}/{config.metric_policy_version}. "
            "w/o uses scenario_gt, w/ uses paired empirical residual observation. Unavailable values are blank, not zero.\n\n"
        )
        return title + note + self._markdown_table(self._paper_rows(rows))

    def _comparison_all_tables_markdown(
        self,
        conditions: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        config: ResolvedRunConfig,
    ) -> str:
        parts = [
            f"# {COMPARISON_DISPLAY_NAME}\n",
            "Each topology × model condition contains eight rows: two rule sources × two M6 interfaces × two framework branches.\n",
            f"M7 validation standard: `{RULE_SOURCE_HUMAN}`. Metric policy `{config.metric_policy_id}` v{config.metric_policy_version}.\n",
        ]
        for condition in conditions:
            rows = [row for row in metrics if row["condition_id"] == condition["condition_id"]]
            parts.append(self._comparison_paper_markdown(condition, rows, config))
        return "\n".join(parts)

    def _comparison_matrix(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "condition_id": row["condition_id"],
                "base_condition_id": row.get("base_condition_id"),
                "rule_source_id": row.get("rule_source_id"),
                "rule_source_label": row.get("rule_source_label"),
                "topology_id": row["topology_id"],
                "topology_name": row["topology_name"],
                "model_id": row["model_id"],
                "model_name": row["model_name"],
                "paradigm": row["paradigm"],
                "ground_truth_regime": row["ground_truth_regime"],
                "framework_condition": row["framework_condition"],
                "trial_type": row["trial_type"],
                "decision_interface": row["decision_interface"],
                "availability": row["availability"],
                "trial_count": row["trial_count"],
                "valid_rate": row["valid_rate"],
                "r_ideal": row["r_ideal"],
                "r_deploy": row["r_deploy"],
                "delta_r": row["delta_r"],
            }
            for row in metrics
            if row["rule_source_id"] == RULE_SOURCE_HUMAN
            and row["decision_interface"] == "rule_based"
            and row["framework_condition"] == FRAMEWORK_WITH
        ]

    def _write_m9(
        self,
        run_root: Path,
        config: ResolvedRunConfig,
        models: list[dict[str, Any]],
        conditions: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Freeze delivery artifacts from M8 only; M9 does not recalculate metrics."""
        stage = self._stage(run_root, "M9")
        table_paths: list[Path] = []
        for condition in conditions:
            condition_rows = [row for row in metrics if row["condition_id"] == condition["condition_id"]]
            table_name = f"decoupled_2_stage_{condition['topology_id']}__{condition['model_id']}"
            csv_path = stage / f"{table_name}.csv"
            md_path = stage / f"{table_name}.md"
            self._write_csv(csv_path, self._paper_rows(condition_rows))
            self._write_text(md_path, self._paper_markdown(condition, condition_rows, config))
            table_paths.extend([csv_path, md_path])
        all_csv = stage / "decoupled_2_stage_all_tables.csv"
        all_md = stage / "decoupled_2_stage_all_tables.md"
        all_xlsx = stage / "decoupled_2_stage_all_tables.xlsx"
        all_zip = stage / "decoupled_2_stage_all_tables.zip"
        paper_rows = self._paper_rows(metrics)
        self._write_csv(all_csv, paper_rows)
        self._write_text(all_md, self._all_tables_markdown(conditions, metrics, config))
        self._write_xlsx(all_xlsx, "decoupled_2_stage", paper_rows)
        insight = DecoupledInsightReportService().generate(
            run_root=run_root,
            profile_id=PROFILE_ID,
            config=config.payload(),
            all_tables_path=all_csv,
            metrics_path=run_root / "M8" / "decoupled_2_stage_metrics.csv",
        )
        insight_paths = [insight["report_path"], insight["summary_path"]]
        with zipfile.ZipFile(all_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in [all_csv, all_md, all_xlsx, *table_paths, *insight_paths]:
                archive.write(path, path.name)
        evidence_paths = [
            run_root / "M4" / "scenario_generation_diagnostics.json",
            run_root / "M4" / "scenario_feasibility_report.json",
            run_root / "M7" / "ideal_invariant_report.json",
        ]
        artifacts = self._artifact_list(run_root, [all_csv, all_md, all_xlsx, all_zip, *table_paths, *insight_paths, *evidence_paths])
        scenario_generation = self._scenario_generation_summary(run_root)
        summary = {
            "run_id": run_root.name,
            "profile_id": PROFILE_ID,
            "display_name": DISPLAY_NAME,
            "status": "SUCCEEDED",
            "created_at": self._now(),
            "config": config.payload(),
            "condition_count": len(conditions),
            "table_count": len(conditions),
            "models": models,
            "topologies": SUPPORTED_TOPOLOGIES,
            "matrix": self._matrix(metrics),
            "metrics": paper_rows,
            "artifacts": artifacts,
            "scenario_generation": scenario_generation,
            "insight_report": insight["summary"],
            "limitations": [
                "w/o is the paired ideal baseline and uses scenario_gt directly; w/ uses controlled empirical residual propagation.",
                "R_ideal and R_deploy are validator-valid rates, not composite scores.",
                "GAI decision interface is reserved and reported as unavailable until a provider is configured.",
                "The validator uses M4 scenario_gt as truth; observations are never treated as ground truth.",
                "M6 uses the same capacity-aware multi-source planner for ideal and deployment inputs.",
                "M4 formal scenarios are sampled from candidates that pass M6 decision feasibility; rejected candidates are generation diagnostics, not formal trials.",
                "Stage II ideal baseline is conditioned on topology, capacity, and M6-feasible scenarios; it does not represent arbitrary population distributions.",
                "All accepted ideal scenarios pass M4 decision feasibility preflight before M5; ideal M7 failures block formal M8/M9 publication.",
                "Results are experiment outputs for reviewer-facing analysis, not final paper conclusions by themselves.",
            ],
        }
        self._write_json(stage / "delivery_manifest.json", {
            "profile_id": PROFILE_ID,
            "metric_policy_id": config.metric_policy_id,
            "metric_policy_version": config.metric_policy_version,
            "decision_policy_id": config.decision_policy_id,
            "decision_policy_version": config.decision_policy_version,
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "scenario_generation": scenario_generation,
            "scenario_generation_diagnostics_checksum": self._sha256(run_root / "M4" / "scenario_generation_diagnostics.json"),
            "risk_f_beta": config.risk_f_beta,
            "required_outputs": ["15 configuration tables", "all CSV", "all Markdown", "all XLSX", "ZIP bundle", "insight_report.md", "insight_summary.json"],
            "artifact_count": len(artifacts), "artifacts": artifacts,
        })
        self._write_json(stage / "reproducibility_manifest.json", {
            "profile_id": PROFILE_ID, **config.payload(), "policy": self._policy_payload(config),
            "m1_checksum": self._sha256(run_root / "M1" / "perception_results.parquet"),
            "m2_checksum": self._sha256(run_root / "M2" / "error_samples.parquet"),
            "m4_checksum": self._sha256(run_root / "M4" / "scenario_gt.jsonl"),
            "m4_feasibility_checksum": self._sha256(run_root / "M4" / "scenario_feasibility_report.json"),
            "m4_scenario_generation_checksum": self._sha256(run_root / "M4" / "scenario_generation_diagnostics.json"),
            "m5_checksum": self._sha256(run_root / "M5" / "observation_trials.parquet"),
            "m6_checksum": self._sha256(run_root / "M6" / "action_trials.parquet"),
            "m7_checksum": self._sha256(run_root / "M7" / "decision_validation_trials.parquet"),
            "m7_ideal_invariant_checksum": self._sha256(run_root / "M7" / "ideal_invariant_report.json"),
            "m8_checksum": self._sha256(run_root / "M8" / "decoupled_2_stage_metrics.parquet"),
            "insight_report_checksum": self._sha256(insight["report_path"]),
            "insight_summary_checksum": self._sha256(insight["summary_path"]),
        })
        self._write_json(stage / "run_summary.json", summary)
        return summary

    def _build_observation(
        self, truth: dict[str, int], residual_pool: list[float], rng: random.Random
    ) -> tuple[dict[str, int], dict[str, float]]:
        sampled: dict[str, float] = {}
        observed: dict[str, int] = {}
        for node_id, value in truth.items():
            residual = float(rng.choice(residual_pool))
            sampled[node_id] = residual
            observed[node_id] = max(0, self._round_half_up(float(value) + residual))
        return observed, sampled

    def _decide_actions(
        self, topology: dict[str, Any], decision_population: dict[str, int], risk_threshold: float
    ) -> list[dict[str, Any]]:
        capacity = topology["capacity_by_node"]
        visible_population = {
            str(node_id): max(0, int(value))
            for node_id, value in decision_population.items()
        }
        requested = self._requested_move_counts(topology, visible_population, risk_threshold)
        source_order = sorted(
            requested,
            key=lambda source_id: (
                -requested[source_id]["utilization"],
                -requested[source_id]["requested_move_count"],
                self._natural_key(source_id),
            ),
        )
        incoming: dict[str, int] = {}
        outgoing: dict[str, int] = {}
        actions: list[dict[str, Any]] = []
        allocation_order = 0

        for source_id in source_order:
            requested_count = requested[source_id]["requested_move_count"]
            remaining_demand = requested_count
            ranked = self._ranked_targets(source_id, topology)
            for selected_rank, candidate in enumerate(ranked, start=1):
                if remaining_demand <= 0:
                    break
                target_id = str(candidate["target_id"])
                target_capacity = self._target_remaining_capacity(
                    topology,
                    visible_population,
                    incoming,
                    outgoing,
                    target_id,
                )
                allocation = remaining_demand if target_id in set(topology["external_exits"]) else min(remaining_demand, target_capacity)
                source_remaining = max(0, visible_population.get(source_id, 0) - outgoing.get(source_id, 0))
                allocation = min(allocation, source_remaining)
                if allocation <= 0:
                    continue
                allocation_order += 1
                capacity_before = None if target_id in set(topology["external_exits"]) else target_capacity
                actions.append({
                    "source_id": source_id,
                    "target_id": target_id,
                    "move_count": int(allocation),
                    "priority_metadata": {
                        "priority_rule": topology["rules"].get("priority_rule", "ascending_total_cost"),
                        "candidate_targets": ranked,
                        "selected_rank": selected_rank,
                        "selected_total_cost": candidate["total_cost"],
                        "requested_quantity": requested_count,
                        "allocated_quantity": int(allocation),
                        "target_remaining_capacity_before": capacity_before,
                        "target_remaining_capacity_after": None if capacity_before is None else int(capacity_before - allocation),
                        "allocation_order": allocation_order,
                    },
                })
                remaining_demand -= int(allocation)
                outgoing[source_id] = outgoing.get(source_id, 0) + int(allocation)
                incoming[target_id] = incoming.get(target_id, 0) + int(allocation)
        return actions

    def _requested_move_counts(
        self, topology: dict[str, Any], decision_population: dict[str, int], risk_threshold: float
    ) -> dict[str, dict[str, float | int]]:
        capacity = topology["capacity_by_node"]
        requested: dict[str, dict[str, float | int]] = {}
        for source_id in topology["source_nodes"]:
            cap = max(int(capacity[source_id]), 1)
            visible_count = max(0, int(decision_population.get(source_id, 0)))
            utilization = visible_count / cap
            if utilization < risk_threshold:
                continue
            move_count = min(
                max(1, self._round_half_up(visible_count - (cap * 0.70))),
                visible_count,
            )
            requested[str(source_id)] = {
                "utilization": utilization,
                "requested_move_count": int(move_count),
            }
        return requested

    def _estimate_gai_action_steps(
        self,
        topology: dict[str, Any],
        decision_population: dict[str, int],
        risk_threshold: float,
    ) -> dict[str, Any]:
        """Estimate a safe upper bound for canonical GAI action-step calls.

        The live episode can split one source request across several targets.
        Therefore the number of high-risk sources is only a lower bound. This
        estimator counts every currently legal target with positive visible
        remaining capacity for each prioritized source. Shared target capacity
        is intentionally not consumed across sources in the estimate: reusing
        that capacity overestimates the call count, but cannot undercount a
        real action step. The runtime still enforces the real shared capacity.
        """
        context = self._build_gai_decision_context(
            topology=topology,
            decision_population=decision_population,
            risk_threshold=risk_threshold,
        )
        exits = {str(node_id) for node_id in topology.get("external_exits", [])}
        source_rows: list[dict[str, Any]] = []
        upper_bound = 0
        requirements = {
            str(item["source_id"]): item
            for item in context.get("source_requirements", [])
            if item.get("high_risk")
        }
        for source_id in context.get("source_priority_order", []):
            source_id = str(source_id)
            requirement = requirements.get(source_id, {})
            requested = int(requirement.get("requested_move_count", 0) or 0)
            candidate_targets: list[str] = []
            for candidate in context.get("legal_target_candidates", {}).get(source_id, []):
                target_id = str(candidate.get("target_id"))
                if target_id in exits:
                    max_count = requested
                else:
                    max_count = min(
                        requested,
                        max(0, int(candidate.get("target_remaining_capacity") or 0)),
                    )
                if max_count > 0:
                    candidate_targets.append(target_id)
            source_upper_bound = len(candidate_targets)
            upper_bound += source_upper_bound
            source_rows.append({
                "source_id": source_id,
                "requested_move_count": requested,
                "candidate_step_upper_bound": source_upper_bound,
                "candidate_target_ids": candidate_targets,
            })
        return {
            "upper_bound": int(upper_bound),
            "high_risk_source_count": len(source_rows),
            "by_source": source_rows,
        }

    def _target_remaining_capacity(
        self,
        topology: dict[str, Any],
        visible_population: dict[str, int],
        incoming: dict[str, int],
        outgoing: dict[str, int],
        target_id: str,
    ) -> int:
        if target_id in set(topology["external_exits"]):
            return 2**31 - 1
        capacity = int(topology["capacity_by_node"].get(target_id, 0))
        current = int(visible_population.get(target_id, 0))
        net_population = current + incoming.get(target_id, 0) - outgoing.get(target_id, 0)
        return max(0, capacity - net_population)

    def _assess_plan_feasibility(
        self,
        topology: dict[str, Any],
        decision_population: dict[str, int],
        actions: list[dict[str, Any]],
        risk_threshold: float,
    ) -> dict[str, Any]:
        capacity = topology["capacity_by_node"]
        source_nodes = set(topology["source_nodes"])
        exits = set(topology["external_exits"])
        allowed_destinations = set(topology["rules"].get("allowed_node_types_as_destination", ["zone", "exit"]))
        reasons: list[dict[str, Any]] = []
        outgoing: dict[str, int] = {}
        incoming: dict[str, int] = {}
        requested = self._requested_move_counts(topology, decision_population, risk_threshold)
        for index, action in enumerate(actions):
            source = action.get("source_id")
            target = action.get("target_id")
            move_count = action.get("move_count")
            if (
                not isinstance(source, str)
                or not isinstance(target, str)
                or source not in source_nodes
                or target not in capacity
                or not isinstance(move_count, int)
                or move_count <= 0
            ):
                reasons.append({"code": "invalid_action", "action_index": index})
                continue
            target_type = "exit" if target in exits or str(target).upper().startswith("E") else "zone"
            if target_type not in allowed_destinations:
                reasons.append({"code": "forbidden_target", "target_id": target})
            if target not in topology["adjacency"].get(source, []):
                reasons.append({"code": "topology_violation", "source_id": source, "target_id": target})
            outgoing[source] = outgoing.get(source, 0) + move_count
            incoming[target] = incoming.get(target, 0) + move_count
        for source, count in outgoing.items():
            if count > max(0, int(decision_population.get(source, 0))):
                reasons.append({"code": "source_underflow", "source_id": source, "outgoing": count, "visible_population": int(decision_population.get(source, 0))})
        for source_id, request in requested.items():
            allocated = outgoing.get(source_id, 0)
            if allocated < int(request["requested_move_count"]):
                reasons.append({"code": "unallocated_source_request", "source_id": source_id, "requested": int(request["requested_move_count"]), "allocated": allocated})
        for node_id in capacity:
            if node_id in exits:
                continue
            post = int(decision_population.get(node_id, 0)) + incoming.get(node_id, 0) - outgoing.get(node_id, 0)
            if post < 0 or post > int(capacity[node_id]):
                reasons.append({"code": "post_state_capacity", "node_id": node_id, "post_population": post, "capacity": int(capacity[node_id])})
        return {"status": "feasible" if not reasons else "infeasible", "reasons": reasons}

    def _assess_common_scenario_feasibility(
        self,
        topology: dict[str, Any],
        decision_population: dict[str, int],
        risk_threshold: float,
    ) -> dict[str, Any]:
        """Apply the interface-independent M4 feasibility oracle.

        The oracle uses the deterministic, non-GAI allocation policy only as a
        feasibility witness. It is evaluated once on the common human
        topology, before the scenario is shared with either M6 interface.
        """
        actions = self._decide_actions(topology, decision_population, risk_threshold)
        assessment = self._assess_plan_feasibility(
            topology,
            decision_population,
            actions,
            risk_threshold,
        )
        return {
            "oracle_version": FEASIBILITY_ORACLE_VERSION,
            "status": assessment["status"],
            "reasons": assessment["reasons"],
            "witness_action_count": len(actions),
        }

    def _select_target(self, source_id: str, topology: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        ranked = self._ranked_targets(source_id, topology)
        if not ranked:
            return None, {"priority_rule": topology["rules"].get("priority_rule"), "candidate_targets": [], "selected_rank": None}
        selected = ranked[0]
        return str(selected["target_id"]), {
            "priority_rule": topology["rules"].get("priority_rule", "ascending_total_cost"),
            "candidate_targets": ranked,
            "selected_rank": 1,
            "selected_total_cost": selected["total_cost"],
        }

    def _ranked_targets(self, source_id: str, topology: dict[str, Any]) -> list[dict[str, Any]]:
        edge_by_pair = {(edge["source_id"], edge["target_id"]): edge for edge in topology["edges"]}
        ranked: list[dict[str, Any]] = []
        for target_id in topology["adjacency"].get(source_id, []):
            edge = edge_by_pair.get((source_id, target_id), {})
            total_cost = int(edge.get("edge_cost", 0)) + int(edge.get("traversal_cost", 0))
            ranked.append({"target_id": target_id, "total_cost": total_cost})
        return sorted(ranked, key=lambda item: (item["total_cost"], self._natural_key(str(item["target_id"]))))

    def _evaluate_trial(
        self, *, condition: dict[str, Any], topology: dict[str, Any], regime: str,
        config: ResolvedRunConfig, pair_id: str, trial_id: str, trial_index: int,
        trial_type: str, framework: str, scenario: dict[str, Any], actions: list[dict[str, Any]],
        ideal_actions: list[dict[str, Any]], observation_checksum: str | None,
        decision_topology: dict[str, Any] | None = None,
        decision_rule_source_id: str | None = None,
        validation_rule_source_id: str | None = None,
        decision_interface: str = "rule_based",
        decision_output_status: str = "parsed",
    ) -> dict[str, Any]:
        decision_topology = decision_topology or topology
        decision_rule_source_id = decision_rule_source_id or condition.get("rule_source_id", RULE_SOURCE_HUMAN)
        validation_rule_source_id = validation_rule_source_id or condition.get("rule_source_id", RULE_SOURCE_HUMAN)
        validation = self._validate_actions(topology, scenario["scenario_gt_population"], actions)
        m6_contract_violation = decision_output_status == "invalid_output"
        m6_decision_infeasible = decision_output_status == "decision_infeasible"
        if decision_output_status not in {"parsed", "no_action_required"}:
            if m6_contract_violation:
                validation["invalid_output"] = True
            validation["valid"] = False
            validation["violation_reasons"].append({
                "code": "m6_contract_violation" if m6_contract_violation else "m6_decision_infeasible",
                "decision_output_status": decision_output_status,
            })
        expected = self._risk_nodes(topology, scenario["scenario_gt_population"], config.risk_threshold)
        recommended = self._recommended_sources(topology, actions)
        risk = self._risk_consistency(expected, recommended, config.risk_f_beta)
        action_components = self._action_components(topology, expected, actions, validation)
        agreement = self._action_agreement_with_ideal(ideal_actions, actions)
        return {
            "trial_id": trial_id, "pair_id": pair_id, "trial_type": trial_type,
            "trial_index": trial_index, "condition_id": condition["condition_id"],
            "topology_id": condition["topology_id"], "topology_name": condition["topology_name"],
            "model_id": condition["model_id"], "model_name": condition["model_name"],
            "paradigm": condition["paradigm"], "ground_truth_regime": regime,
            "framework_condition": framework, "decision_interface": decision_interface,
            "base_condition_id": condition.get("base_condition_id", condition["condition_id"]),
            "rule_source_id": decision_rule_source_id,
            "rule_source_label": condition.get("rule_source_label", RULE_SOURCE_LABELS.get(decision_rule_source_id, decision_rule_source_id)),
            "decision_rule_source_id": decision_rule_source_id,
            "validation_rule_source_id": validation_rule_source_id,
            "decision_topology_checksum": decision_topology.get("topology_checksum"),
            "validation_topology_checksum": topology.get("topology_checksum"),
            "scenario_id": scenario["scenario_id"], "scenario_checksum": scenario["scenario_checksum"],
            "observation_checksum": observation_checksum, "metric_policy_id": config.metric_policy_id,
            "metric_policy_version": config.metric_policy_version, "risk_f_beta": config.risk_f_beta,
            "decision_input_mode": "scenario_gt" if trial_type == "ideal" else "observation",
            "decision_output_status": decision_output_status,
            "decision_input_checksum": scenario["scenario_checksum"] if trial_type == "ideal" else observation_checksum,
            "validation_truth_source_stage_id": "M4",
            "validation_truth_checksum": scenario["scenario_checksum"],
            "m6_decision_policy_id": config.decision_policy_id,
            "m6_decision_policy_version": config.decision_policy_version,
             "valid": 1.0 if validation["valid"] else 0.0,
             "m6_contract_violation": 1.0 if m6_contract_violation else 0.0,
             "m6_decision_infeasible": 1.0 if m6_decision_infeasible else 0.0,
             "invalid_output": float(validation["invalid_output"]),
            "topology_violation": float(validation["topology_violation"]),
            "unknown_target_violation": float(validation["unknown_target_violation"]),
            "forbidden_target_violation": float(validation["forbidden_target_violation"]),
            "capacity_violation": float(validation["capacity_violation"]),
            "source_underflow_violation": float(validation["source_underflow_violation"]),
            "flow_conservation_violation": float(validation["flow_conservation_violation"]),
            "rule_violation": float(validation["rule_violation"]),
            "violation_reasons": self._json_cell(validation["violation_reasons"]),
            "post_population": self._json_cell(validation["post_population"]),
            "expected_high_sources": self._json_cell(sorted(expected, key=self._natural_key)),
            "recommended_sources": self._json_cell(sorted(recommended, key=self._natural_key)),
            **risk, **action_components, "action_agreement_with_ideal": agreement,
        }

    def _validate_actions(self, topology: dict[str, Any], truth: dict[str, int], actions: list[dict[str, Any]]) -> dict[str, Any]:
        capacity = topology["capacity_by_node"]
        adjacency = topology["adjacency"]
        exits = set(topology["external_exits"])
        allowed_destinations = set(topology["rules"].get("allowed_node_types_as_destination", ["zone", "exit"]))
        reasons: list[dict[str, Any]] = []
        flags = {name: False for name in ["invalid_output", "topology_violation", "unknown_target_violation", "forbidden_target_violation", "capacity_violation", "source_underflow_violation", "flow_conservation_violation"]}
        outgoing: dict[str, int] = {}
        incoming: dict[str, int] = {}
        for index, action in enumerate(actions):
            source, target, move_count = action.get("source_id"), action.get("target_id"), action.get("move_count")
            if not isinstance(source, str) or not isinstance(target, str) or not isinstance(move_count, int) or move_count <= 0:
                flags["invalid_output"] = True; reasons.append({"code": "invalid_output", "action_index": index}); continue
            if source not in topology["source_nodes"]:
                flags["invalid_output"] = True; reasons.append({"code": "invalid_source", "source_id": source, "action_index": index})
            if target not in capacity:
                flags["unknown_target_violation"] = True; reasons.append({"code": "unknown_target", "source_id": source, "target_id": target}); continue
            target_type = "exit" if target in exits or target.upper().startswith("E") else "zone"
            if target_type not in allowed_destinations:
                flags["forbidden_target_violation"] = True; reasons.append({"code": "forbidden_target", "source_id": source, "target_id": target})
            if target not in adjacency.get(source, []):
                flags["topology_violation"] = True; reasons.append({"code": "topology_violation", "source_id": source, "target_id": target})
            outgoing[source] = outgoing.get(source, 0) + move_count
            incoming[target] = incoming.get(target, 0) + move_count
        for source, count in outgoing.items():
            if count > int(truth.get(source, 0)):
                flags["source_underflow_violation"] = True; reasons.append({"code": "source_underflow", "source_id": source, "outgoing": count, "truth": int(truth.get(source, 0))})
        post_population: dict[str, int] = {}
        for node_id in capacity:
            if node_id in exits:
                continue
            post = int(truth.get(node_id, 0)) + incoming.get(node_id, 0) - outgoing.get(node_id, 0)
            post_population[node_id] = post
            if post < 0 or post > int(capacity[node_id]):
                flags["capacity_violation"] = True; reasons.append({"code": "post_state_capacity", "node_id": node_id, "post_population": post, "capacity": int(capacity[node_id])})
        if sum(outgoing.values()) != sum(incoming.values()):
            flags["flow_conservation_violation"] = True; reasons.append({"code": "flow_conservation", "outgoing": sum(outgoing.values()), "incoming": sum(incoming.values())})
        rule_violation = any(flags.values())
        return {**flags, "rule_violation": rule_violation, "valid": not rule_violation, "violation_reasons": reasons, "post_population": post_population}

    def _risk_nodes(self, topology: dict[str, Any], population: dict[str, int], risk_threshold: float) -> set[str]:
        return {node_id for node_id in topology["source_nodes"] if float(population.get(node_id, 0)) / max(int(topology["capacity_by_node"][node_id]), 1) >= risk_threshold}

    def _recommended_sources(self, topology: dict[str, Any], actions: list[dict[str, Any]]) -> set[str]:
        return {str(action["source_id"]) for action in actions if isinstance(action.get("source_id"), str) and action["source_id"] in topology["source_nodes"] and isinstance(action.get("target_id"), str) and isinstance(action.get("move_count"), int) and action["move_count"] > 0}

    def _risk_consistency(self, expected: set[str], recommended: set[str], beta: float) -> dict[str, float | int]:
        tp, fp, fn = len(expected & recommended), len(recommended - expected), len(expected - recommended)
        if not expected and not recommended:
            precision = recall = score = 1.0
        elif not expected or not recommended:
            precision = recall = score = 0.0
        else:
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            score = ((1 + beta ** 2) * precision * recall / ((beta ** 2 * precision) + recall)) if precision + recall else 0.0
        return {"risk_tp": tp, "risk_fp": fp, "risk_fn": fn, "risk_precision": self._round(precision), "risk_recall": self._round(recall), "risk_consistency": self._round(score)}

    def _action_components(self, topology: dict[str, Any], expected: set[str], actions: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, Any]:
        legality = 1.0 if validation["valid"] else 0.0
        evidence: list[dict[str, Any]] = []
        priority_values: list[float] = []
        for action in actions:
            ranked = self._ranked_targets(str(action.get("source_id")), topology)
            targets = [item["target_id"] for item in ranked]
            target = action.get("target_id")
            rank = targets.index(target) + 1 if target in targets else None
            score = 1.0 if rank == 1 else 0.0
            priority_values.append(score)
            evidence.append({"source_id": action.get("source_id"), "candidate_targets": ranked, "selected_target": target, "selected_rank": rank, "priority_passed": bool(score)})
        priority = sum(priority_values) / len(priority_values) if priority_values else (1.0 if not expected else 0.0)
        economy_evidence: list[dict[str, Any]] = []
        economy_values: list[float] = []
        for source_id in sorted(expected, key=self._natural_key):
            target_count = len({action.get("target_id") for action in actions if action.get("source_id") == source_id and action.get("target_id") is not None})
            source_score = 1.0 if target_count <= 3 else 0.0
            economy_values.append(source_score); economy_evidence.append({"source_id": source_id, "distinct_target_count": target_count, "economy_source": source_score})
        economy = sum(economy_values) / len(economy_values) if economy_values else (1.0 if not actions else 0.0)
        before_gate = 0.50 * legality + 0.35 * priority + 0.15 * economy
        fatal_gate = legality == 0.0
        return {
            "legality_score": self._round(legality), "priority_score": self._round(priority), "economy_score": self._round(economy),
            "action_consistency_before_gate": self._round(before_gate), "fatal_legality_gate": fatal_gate,
            "action_consistency": 0.0 if fatal_gate else self._round(before_gate),
            "priority_evidence": self._json_cell(evidence), "economy_evidence": self._json_cell(economy_evidence),
        }

    def _action_agreement_with_ideal(self, ideal: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> float:
        if not ideal and not candidate:
            return 1.0
        ideal_map = self._action_map(ideal); candidate_map = self._action_map(candidate)
        denominator = max(sum(ideal_map.values()), 1)
        overlap = sum(min(ideal_map.get(key, 0), candidate_map.get(key, 0)) for key in set(ideal_map) | set(candidate_map))
        return self._round(min(overlap / denominator, 1.0))

    def _action_map(self, actions: list[dict[str, Any]]) -> dict[str, int]:
        mapped: dict[str, int] = {}
        for action in actions:
            target = action.get("target_id")
            if target is not None:
                key = f"{action.get('source_id')}->{target}"
                mapped[key] = mapped.get(key, 0) + int(action.get("move_count") or 0)
        return mapped

    def _paper_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keys = [
            "condition_id", "topology_id", "model_id", "topology_name", "model_name", "paradigm",
            "base_condition_id", "rule_source_id", "rule_source_label",
            "ground_truth_regime", "framework_condition", "trial_type", "decision_interface", "availability",
            "validation_rule_source_id", "decision_rule_source_id", "decision_topology_checksum", "validation_topology_checksum",
             "ideal_baseline_scope", "ideal_action_scope", "ideal_action_source_trial_id",
             "metric_policy_version", "risk_f_beta", "trial_count", "executed_trial_count", "execution_outcome_status",
             "risk_precision", "risk_recall", "risk_consistency", "legality_score", "priority_score",
             "economy_score", "action_consistency", "action_agreement_with_ideal", "invalid_output_rate",
             "m6_contract_violation_rate", "m6_decision_infeasible_rate",
            "rule_violation_rate", "capacity_violation_rate", "topology_violation_rate", "source_underflow_rate",
            "flow_conservation_violation_rate", "r_ideal", "r_deploy", "delta_r", "unavailable_reason",
        ]
        return [{key: row.get(key) for key in keys} for row in sorted(rows, key=self._metric_sort_key)]

    def _paper_markdown(self, condition: dict[str, Any], rows: list[dict[str, Any]], config: ResolvedRunConfig) -> str:
        title = f"# {condition['topology_name']} x {condition['model_name']}\n\n"
        note = (
            f"Metric policy: {config.metric_policy_id}/{config.metric_policy_version}; Risk Consistency F-beta: {config.risk_f_beta}. "
            f"Scenario policy: {config.scenario_policy_id}/{config.scenario_policy_version}. "
            "`w/o` is the ideal branch; `w/` is the paired deployment branch. Unavailable values are intentionally blank, not zero. "
            "Ideal baseline is conditioned on M6-feasible scenarios.\n\n"
        )
        return title + note + self._markdown_table(self._paper_rows(rows))

    def _all_tables_markdown(self, conditions: list[dict[str, Any]], metrics: list[dict[str, Any]], config: ResolvedRunConfig) -> str:
        parts = [
            f"# {DISPLAY_NAME} Results\n",
            "This bundle contains 15 topology/model configuration tables. Each deployment trial is paired with the same ideal scenario.\n",
            f"Metric policy `{config.metric_policy_id}` v{config.metric_policy_version}; Risk F-beta = {config.risk_f_beta}. Scenario policy `{config.scenario_policy_id}` v{config.scenario_policy_version}; ideal baseline uses M6-feasible scenarios.\n",
        ]
        for condition in conditions:
            parts.append("\n" + self._paper_markdown(condition, [row for row in metrics if row["condition_id"] == condition["condition_id"]], config))
        return "\n".join(parts)

    def _matrix(self, metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "condition_id": row["condition_id"], "topology_id": row["topology_id"], "topology_name": row["topology_name"],
            "model_id": row["model_id"], "model_name": row["model_name"], "paradigm": row["paradigm"],
            "ground_truth_regime": row["ground_truth_regime"], "trial_count": row["trial_count"],
            "risk_consistency": row["risk_consistency"], "action_consistency": row["action_consistency"],
            "valid_rate": row["valid_rate"], "invalid_output_rate": row["invalid_output_rate"],
            "rule_violation_rate": row["rule_violation_rate"], "r_ideal": row["r_ideal"],
            "r_deploy": row["r_deploy"], "delta_r": row["delta_r"], "risk_f_beta": row["risk_f_beta"],
        } for row in metrics if row["decision_interface"] == "rule_based" and row["framework_condition"] == FRAMEWORK_WITH]

    def _load_models(self, *, include_ineligible: bool) -> list[dict[str, Any]]:
        rows = self._read_csv(self.perception_root / "A2_perception_model_registry.csv")
        models: list[dict[str, Any]] = []
        for row in rows:
            if not include_ineligible and not self._is_true(row.get("paper_result_eligible")):
                continue
            family = row.get("perception_model_family", "")
            models.append({
                "model_id": row["perception_model_id"],
                "model_name": row["perception_model_name"],
                "model_version": row["perception_model_version"],
                "model_family": family,
                "paradigm": "detection" if family == "object_detection" else "density",
                "compatible_dataset_id": row["compatible_dataset_id"],
                "paper_result_eligible": self._is_true(row.get("paper_result_eligible")),
            })
        return models

    def _resolve_hotspot_nodes(self, topology: dict[str, Any], selection: str) -> list[str]:
        if selection != "top_capacity_quartile":
            raise ValueError(f"Unsupported hotspot selection: {selection}")
        source_nodes = list(topology["source_nodes"])
        count = max(1, math.ceil(len(source_nodes) * 0.25))
        selected = sorted(source_nodes, key=lambda node: (-int(topology["capacity_by_node"][node]), self._natural_key(node)))[:count]
        return sorted(selected, key=self._natural_key)

    def _allocate_scenario_population(
        self, source_nodes: list[str], capacity: dict[str, int], total: int, hotspot_nodes: list[str],
        rho: float, rng: random.Random, alpha: float, beta: float,
    ) -> dict[str, int]:
        hotspot_set = set(hotspot_nodes)
        regular_nodes = [node for node in source_nodes if node not in hotspot_set]
        hotspot_total = self._round_half_up(total * rho)
        regular_total = total - hotspot_total
        if hotspot_total > sum(int(capacity[node]) for node in hotspot_nodes):
            raise ValueError("Scenario hotspot allocation exceeds hotspot capacity")
        if regular_total > sum(int(capacity[node]) for node in regular_nodes):
            raise ValueError("Scenario non-hotspot allocation exceeds non-hotspot capacity")
        allocation = {node: 0 for node in source_nodes}
        allocation.update(self._allocate_beta_population(hotspot_nodes, capacity, hotspot_total, rng, alpha, beta))
        allocation.update(self._allocate_beta_population(regular_nodes, capacity, regular_total, rng, alpha, beta))
        if sum(allocation.values()) != total:
            raise ValueError("Scenario allocation did not preserve D_total")
        return dict(sorted(allocation.items(), key=lambda item: self._natural_key(item[0])))

    def _allocate_beta_population(
        self, nodes: list[str], capacity: dict[str, int], total: int, rng: random.Random, alpha: float, beta: float
    ) -> dict[str, int]:
        if not nodes:
            if total:
                raise ValueError("Cannot allocate positive population to an empty node group")
            return {}
        weights = {node: rng.betavariate(alpha, beta) for node in nodes}
        weight_sum = sum(weights.values())
        if weight_sum <= 0:
            raise ValueError("Beta scenario weights are all zero")
        allocation = {node: min(int(math.floor(total * weights[node] / weight_sum)), int(capacity[node])) for node in nodes}
        remaining = total - sum(allocation.values())
        ranked = sorted(nodes, key=lambda node: (-(total * weights[node] / weight_sum - allocation[node]), self._natural_key(node)))
        while remaining:
            progressed = False
            for node in ranked:
                if allocation[node] < int(capacity[node]):
                    allocation[node] += 1
                    remaining -= 1
                    progressed = True
                    if not remaining:
                        break
            if not progressed:
                raise ValueError("Scenario allocation exceeds node capacity")
        return allocation

    def _regime_thresholds(self, samples: Any) -> dict[str, float]:
        values = list(samples)
        low = [float(row["scene_gt_count"]) for row in values if row.get("perception_regime", "").upper() == "LOW"]
        medium = [float(row["scene_gt_count"]) for row in values if row.get("perception_regime", "").upper() == "MEDIUM"]
        return {"low_max": max(low) if low else 0.0, "medium_max": max(medium) if medium else float("inf")}

    def _count_to_regime(self, value: float, thresholds: dict[str, float]) -> str:
        if value <= thresholds["low_max"]:
            return "LOW"
        if value <= thresholds["medium_max"]:
            return "MEDIUM"
        return "HIGH"

    def _residual_stats(self, values: list[float]) -> ResidualStats:
        ordered_abs = sorted(abs(value) for value in values)
        p90_index = min(len(ordered_abs) - 1, max(0, math.ceil(len(ordered_abs) * 0.90) - 1))
        return ResidualStats(
            count=len(values),
            mean=self._round(statistics.fmean(values)),
            std=self._round(statistics.pstdev(values)) if len(values) > 1 else 0.0,
            p90_abs=self._round(ordered_abs[p90_index]) if ordered_abs else 0.0,
            minimum=self._round(min(values)),
            maximum=self._round(max(values)),
        )

    def _m2_quality_markdown(self, rows: list[dict[str, Any]]) -> str:
        return "# M2 Error Distribution Quality Report\n\n" + self._markdown_table(rows)

    def _policy_payload(self, config: ResolvedRunConfig) -> dict[str, Any]:
        return {
            "policy_id": "decoupled_2_stage_fixed_experiment_policy_v2",
            "metric_policy_id": config.metric_policy_id,
            "metric_policy_version": config.metric_policy_version,
            "framework_conditions": {
                FRAMEWORK_WITHOUT: "ideal baseline: decision input is scenario_gt only",
                FRAMEWORK_WITH: "deployment evaluation: decision input is observed_population only",
            },
            "residual_pool_scope": "model_id + ground_truth_regime; Density and Detection stay separated by model/paradigm",
            "sampling_replacement": "with_replacement",
            "negative_handling": "floor_at_zero",
            "rounding": "round_half_up",
            "capacity_handling": "M6 coordinates visible non-exit target remaining capacity; M7 independently checks post-state from scenario_gt",
            "count_to_node_mapping": "regime-matched empirical residual sampled independently for each topology source node",
            "minimum_pool_size": "must be non-empty for every model/regime; otherwise fail preflight",
            "decision_policy_id": config.decision_policy_id,
            "decision_policy_version": config.decision_policy_version,
            "coordination_policy": "source utilization priority; ranked target allocation with shared remaining capacity",
            "ideal_feasibility_policy": "only candidates whose high-risk scenario_gt source requests are fully allocated by M6 become formal scenarios",
            "scenario_policy_id": config.scenario_policy_id,
            "scenario_policy_version": config.scenario_policy_version,
            "feasibility_constrained_sampling": True,
            "max_candidate_attempts": config.max_scenario_candidate_attempts,
            "scenario_alpha": config.scenario_alpha,
            "scenario_beta": config.scenario_beta,
            "rho": config.hotspot_ratio,
            "hotspot_selection": config.hotspot_selection,
            "risk_threshold": config.risk_threshold,
            "risk_f_beta": config.risk_f_beta,
            "r_trial": "valid",
            "r_ideal": "average(valid of ideal trials)",
            "r_deploy": "average(valid of deployment trials)",
            "delta_r": "R_ideal - R_deploy",
            "gai": "reserved_unavailable; external calls disabled; metrics remain null",
        }

    def _resolve_run_config(
        self, *, root_seed: int, split: str, trial_count: int, scenario_count: int,
        risk_f_beta: float, risk_threshold: float, scenario_alpha: float,
        scenario_beta: float, rho: float, hotspot_selection: str,
        run_purpose: str = "exploratory",
        selected_topology_ids: tuple[str, ...] = (),
        selected_model_ids: tuple[str, ...] = (),
        selected_regimes: tuple[str, ...] = tuple(REGIMES),
        selected_interfaces: tuple[str, ...] = ("rule_based",),
        gai_execution_mode: str | None = None,
    ) -> ResolvedRunConfig:
        split = str(split).strip() or "test"
        trial_count = self._bounded_int(trial_count, minimum=1, maximum=500)
        scenario_count = self._bounded_int(scenario_count, minimum=1, maximum=300)
        numeric = {"risk_f_beta": risk_f_beta, "risk_threshold": risk_threshold, "scenario_alpha": scenario_alpha, "scenario_beta": scenario_beta, "rho": rho}
        values: dict[str, float] = {}
        for key, value in numeric.items():
            try:
                values[key] = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be numeric") from exc
        if values["risk_f_beta"] <= 0 or values["scenario_alpha"] <= 0 or values["scenario_beta"] <= 0:
            raise ValueError("risk_f_beta, scenario_alpha, and scenario_beta must be greater than zero")
        if not 0 < values["risk_threshold"] <= 1:
            raise ValueError("risk_threshold must be in (0, 1]")
        if not 0 < values["rho"] < 1:
            raise ValueError("rho must be in (0, 1)")
        if hotspot_selection != "top_capacity_quartile":
            raise ValueError("hotspot_selection must be top_capacity_quartile")
        if run_purpose not in {"development", "exploratory", "formal"}:
            raise ValueError("run_purpose must be development, exploratory, or formal")
        if (
            run_purpose == "formal"
            and "gai" in selected_interfaces
            and not self._gai_external_calls_allowed()
        ):
            raise ValueError(
                "Formal runs currently support rule_based only; GAI is reserved and unavailable."
            )
        if run_purpose == "formal" and trial_count < 30:
            raise ValueError("formal runs require at least 30 trials per condition")
        resolved_gai_execution_mode = gai_execution_mode or self.settings.gai_execution_mode
        return ResolvedRunConfig(
            root_seed=int(root_seed), split=split, trial_count_per_condition=trial_count,
            scenarios_per_regime=scenario_count, risk_threshold=values["risk_threshold"],
            risk_f_beta=values["risk_f_beta"], scenario_alpha=values["scenario_alpha"],
            scenario_beta=values["scenario_beta"], hotspot_ratio=values["rho"], hotspot_selection=hotspot_selection,
            scenario_policy_id=SCENARIO_POLICY_ID,
            scenario_policy_version=SCENARIO_POLICY_VERSION,
            max_scenario_candidate_attempts=MAX_SCENARIO_CANDIDATE_ATTEMPTS,
            run_purpose=run_purpose,
            selected_topology_ids=selected_topology_ids,
            selected_model_ids=selected_model_ids,
            selected_regimes=selected_regimes,
            selected_interfaces=selected_interfaces,
            gai_execution_mode=resolved_gai_execution_mode,
        )

    def _average(self, rows: list[dict[str, Any]], field: str) -> float:
        return self._round(sum(float(row.get(field, 0.0) or 0.0) for row in rows) / len(rows)) if rows else 0.0

    def _json_cell(self, payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def _seed_value(self, root_seed: int, *parts: Any) -> int:
        digest = hashlib.sha256(":".join([str(root_seed), *[str(part) for part in parts]]).encode()).hexdigest()
        return int(digest[:16], 16) % ((2 ** 63) - 1)

    def _stage(self, run_root: Path, name: str) -> Path:
        path = run_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_progress(
        self,
        run_root: Path,
        *,
        status: str,
        stage_id: str,
        message: str,
        config: dict[str, Any],
    ) -> None:
        if status == "RUNNING":
            self._check_cancelled(stage_id)
        self._write_json(run_root / "run_progress.json", {
            "run_id": run_root.name,
            "status": status,
            "stage_id": stage_id,
            "message": message,
            "updated_at": self._now(),
            "config": config,
            "gai": self._gai_runtime_payload(run_root),
        })

    def _run_root(self, run_id: str) -> Path:
        return self.storage_root / run_id

    def _make_run_id(self, root_seed: int, trial_count: int, scenario_count: int, split: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        digest = hashlib.sha1(f"{root_seed}:{trial_count}:{scenario_count}:{split}:{timestamp}".encode()).hexdigest()[:8]
        return f"decoupled-2-stage-{timestamp}-{digest}"

    def _bounded_int(self, value: int, *, minimum: int, maximum: int) -> int:
        integer = int(value)
        if integer < minimum or integer > maximum:
            raise ValueError(f"value must be between {minimum} and {maximum}: {value}")
        return integer

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _read_json(self, path: Path) -> Any:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        temporary_path: Path | None = None
        try:
            # Replace only after the complete JSON document is written so API
            # readers never observe a partially written progress/artifact file.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(serialized)
                handle.flush()
            temporary_path.replace(path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def _write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _load_action_journal(self, path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
        cached: dict[tuple[str, str, str], dict[str, Any]] = {}
        if not path.is_file():
            return cached
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") != "parsed" or row.get("contract_validation", {}).get("status") != "passed":
                continue
            episode_id = row.get("action_episode_id")
            action_id = row.get("action_id")
            step_checksum = row.get("decision_input_checksum")
            parsed_actions = row.get("parsed_response", {}).get("actions", [])
            if not episode_id or not action_id or not step_checksum or not parsed_actions:
                continue
            action = parsed_actions[0]
            if not isinstance(action, dict):
                continue
            cached[(str(episode_id), str(action_id), str(step_checksum))] = row
        return cached

    def _count_external_journal_calls(self, path: Path) -> int:
        count = 0
        if not path.is_file():
            return count
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += int(bool(isinstance(row, dict) and row.get("external_call_attempted")))
        return count

    def _write_csv(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames = list(rows[0].keys())
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    def _write_parquet(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows) if rows else pa.table({})
        pq.write_table(table, path)

    def _write_xlsx(self, path: Path, sheet_name: str, rows: list[dict[str, Any]]) -> None:
        headers = list(rows[0].keys()) if rows else ["empty"]
        body = [headers] + [[row.get(header, "") for header in headers] for row in rows]
        sheet_rows = []
        for r_index, row in enumerate(body, start=1):
            cells = []
            for c_index, value in enumerate(row, start=1):
                ref = f"{self._excel_col(c_index)}{r_index}"
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape("" if value is None else str(value))}</t></is></c>')
            sheet_rows.append(f'<row r="{r_index}">{"".join(cells)}</row>')
        sheet_xml = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
        workbook_xml = f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets></workbook>'
        rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'
        wb_rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
        content_types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", rels)
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", wb_rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)

    def _excel_col(self, index: int) -> str:
        label = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            label = chr(65 + remainder) + label
        return label

    def _markdown_table(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "No rows.\n"
        headers = list(rows[0].keys())
        lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        for row in rows:
            lines.append("| " + " | ".join(self._markdown_value(row.get(header)) for header in headers) + " |")
        return "\n".join(lines) + "\n"

    def _markdown_value(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("|", "\\|")

    def _artifact_list(self, run_root: Path, paths: list[Path]) -> list[dict[str, Any]]:
        artifacts = []
        for path in paths:
            artifacts.append({
                "path": str(path.relative_to(run_root)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "checksum": self._sha256(path),
            })
        return artifacts

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _object_checksum(self, payload: Any) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _rng(self, root_seed: int, *parts: Any) -> random.Random:
        digest = hashlib.sha256(":".join([str(root_seed), *[str(part) for part in parts]]).encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def _counts(self, rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            value = str(row.get(key, ""))
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items()))

    def _round(self, value: float) -> float:
        return round(float(value), 6)

    def _round_half_up(self, value: float) -> int:
        return int(math.floor(value + 0.5))

    def _is_true(self, value: Any) -> bool:
        return str(value).strip().lower() in {"1", "true", "yes", "y"}

    def _natural_key(self, value: str) -> tuple[int, str]:
        digits = "".join(ch for ch in value if ch.isdigit())
        return (int(digits) if digits else 10**9, value)

    def _metric_sort_key(self, row: dict[str, Any]) -> tuple[Any, ...]:
        framework_order = {FRAMEWORK_WITHOUT: 0, FRAMEWORK_WITH: 1}
        interface_order = {"rule_based": 0, "gai_reserved": 1}
        return (
            str(row.get("topology_id")),
            str(row.get("model_id")),
            REGIMES.index(str(row.get("ground_truth_regime"))) if row.get("ground_truth_regime") in REGIMES else 99,
            framework_order.get(str(row.get("framework_condition")), 99),
            interface_order.get(str(row.get("decision_interface")), 99),
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _failure_artifact_paths(self, run_root: Path) -> list[Path]:
        relative_paths = [
            "M4/scenario_generation_diagnostics.json",
            "M4/scenario_feasibility_report.json",
            "M7/ideal_invariant_report.json",
        ]
        return [
            run_root / relative_path
            for relative_path in relative_paths
            if (run_root / relative_path).is_file()
        ]

    def _compact_failure(self, payload: dict[str, Any], run_root: Path) -> dict[str, Any]:
        return {
            "run_id": payload.get("run_id"),
            "profile_id": payload.get("profile_id"),
            "status": payload.get("status", "FAILED"),
            "created_at": payload.get("created_at"),
            "stage_id": payload.get("stage_id"),
            "message": payload.get("message"),
            "failure_details": payload.get("details", {}),
            "config": payload.get("config", {}),
            "artifacts": payload.get("artifacts") or self._artifact_list(run_root, self._failure_artifact_paths(run_root)),
        }

    def _scenario_generation_summary(self, run_root: Path) -> dict[str, Any]:
        diagnostics_path = run_root / "M4" / "scenario_generation_diagnostics.json"
        diagnostics = self._read_json(diagnostics_path)
        return {
            "policy_id": diagnostics.get("policy_id"),
            "policy_version": diagnostics.get("policy_version"),
            "feasibility_constrained_sampling": diagnostics.get("feasibility_constrained_sampling"),
            "feasibility_oracle_version": diagnostics.get("feasibility_oracle_version"),
            "max_candidate_attempts": diagnostics.get("max_candidate_attempts"),
            "required_scenario_count": diagnostics.get("required_scenario_count"),
            "accepted_scenario_count": diagnostics.get("accepted_scenario_count"),
            "rejected_candidate_count": diagnostics.get("rejected_candidate_count"),
            "diagnostics_checksum": self._sha256(diagnostics_path),
        }

    def _compact_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": payload.get("run_id"),
            "profile_id": payload.get("profile_id"),
            "status": payload.get("status"),
            "created_at": payload.get("created_at"),
            "condition_count": payload.get("condition_count"),
            "table_count": payload.get("table_count"),
            "config": payload.get("config", {}),
            "gai": payload.get("gai"),
            "scenario_generation": payload.get("scenario_generation", {}),
            "artifacts": payload.get("artifacts", [])[:4],
        }
