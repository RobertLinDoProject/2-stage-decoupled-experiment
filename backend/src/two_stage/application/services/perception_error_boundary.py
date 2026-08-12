from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar

import pyarrow.parquet as pq

from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    FRAMEWORK_WITH,
    FRAMEWORK_WITHOUT,
    REGIMES,
    RULE_SOURCE_HUMAN,
    Decoupled2StageExperimentUseCase,
)
from two_stage.settings import Settings


class BoundaryServiceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class PerceptionErrorBoundaryService:
    """Read-only, deterministic counterfactual analysis over published Run artifacts."""

    SCHEMA_VERSION = "perception_error_boundary_v1"
    TOLERANCE_SCHEMA_VERSION = "perception_error_tolerance_boundary_v1"
    TOLERANCE_ANALYSIS_VERSION = "fixed_lambda_grid_v1"
    LAMBDA_GRID = tuple(round(index / 20, 2) for index in range(21))
    TOLERANCE_TARGETS = (("R_deploy > 0", "positive"), ("R_deploy >= 0.50", 0.50), ("R_deploy >= 0.80", 0.80), ("R_deploy >= 0.95", 0.95))
    ANALYSIS_VERSION = "integer_observation_transition_v1"
    TARGETS = (("R_deploy > 0", 1 / 10_000), ("R_deploy >= 0.5", 0.5), ("R_deploy >= 0.8", 0.8), ("R_deploy >= 1.0", 1.0))
    CACHE_SIZE = 8
    READ_MODEL_CACHE_SIZE = 32
    _CACHE: ClassVar[OrderedDict[tuple[str, str, str, str, str, str], dict[str, Any]]] = OrderedDict()
    _CAPABILITY_CACHE: ClassVar[OrderedDict[tuple[str, str, str, str, str, str], dict[str, Any]]] = OrderedDict()
    _EXISTING_ANALYSIS_CACHE: ClassVar[OrderedDict[tuple[str, str, str, str, str, str], dict[str, Any]]] = OrderedDict()

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.use_case = Decoupled2StageExperimentUseCase(settings)
        self.storage_root = Path(settings.local_artifact_root) / "published" / "runs"

    def boundary_capability(
        self,
        run_id: str,
        *,
        rule_source_id: str,
        topology_id: str,
        model_id: str,
        regime: str,
        decision_interface: str,
    ) -> dict[str, Any]:
        run_root = self.storage_root / run_id
        self._require_published_run(run_root)
        self._require_succeeded_run(run_root)
        regime = regime.upper()
        if regime not in REGIMES:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Unsupported regime: {regime}", 400)
        if decision_interface not in {"rule_based", "gai", "gai_reserved"}:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Unsupported decision interface: {decision_interface}", 400)
        cache_key = self._selection_cache_key(run_id, rule_source_id, topology_id, model_id, regime, decision_interface)
        cached = self._CAPABILITY_CACHE.get(cache_key)
        if cached is not None:
            self._CAPABILITY_CACHE.move_to_end(cache_key)
            return deepcopy(cached)
        config = self._read_json(run_root / "run_progress.json").get("config", {})
        scenarios = self._load_scenarios(run_root, topology_id, regime)
        m5_rows = self._load_parquet(run_root / "M5" / "observation_trials.parquet")
        m7_rows = self._load_parquet(run_root / "M7" / "decision_validation_trials.parquet")
        trials = self._select_trials(m5_rows, topology_id, model_id, regime, max(1, int(config.get("scenarios_per_regime", 8))))
        selected_m7 = self._select_m7_rows(m7_rows, topology_id, model_id, regime, rule_source_id, decision_interface, "deployment")
        has_residuals = bool(trials) and any(self._population_float(row.get("sampled_residuals")) for row in trials)
        response = {
            "schema_version": self.TOLERANCE_SCHEMA_VERSION,
            "run_id": run_id,
            "source_run_status": self._effective_run_status(run_root),
            "condition": {
                "rule_source_id": rule_source_id,
                "topology_id": topology_id,
                "model_id": model_id,
                "regime": regime,
                "decision_interface": decision_interface,
                "framework_mode": FRAMEWORK_WITH,
            },
            "existing_run_analysis": {
                "available": bool(trials and selected_m7),
                "level": "A" if trials and selected_m7 and has_residuals else "unavailable",
                "label": "Observed Estimate",
            },
            "boundary_sweep": {
                "available": bool(decision_interface == "rule_based" and trials and scenarios and has_residuals),
                "supported_interface": "rule_based",
                "lambda_min": 0.0,
                "lambda_max": 1.0,
                "lambda_step": 0.05,
                "lambda_points": list(self.LAMBDA_GRID),
                "reuse_scenarios": True,
                "reuse_residuals": True,
                "reason": None if decision_interface == "rule_based" and trials and scenarios and has_residuals else self._boundary_unavailable_reason(decision_interface, trials, scenarios, has_residuals),
            },
            "source_checksums": self._source_checksums(run_root),
            "formal_artifacts_modified": False,
        }
        self._read_model_cache_put(self._CAPABILITY_CACHE, cache_key, response)
        return deepcopy(response)

    def existing_run_analysis(
        self,
        run_id: str,
        *,
        rule_source_id: str,
        topology_id: str,
        model_id: str,
        regime: str,
        decision_interface: str,
    ) -> dict[str, Any]:
        """Fast, read-only estimate from retained M5 and M7 trial facts."""
        run_root = self.storage_root / run_id
        self._require_published_run(run_root)
        self._require_succeeded_run(run_root)
        regime = regime.upper()
        if regime not in REGIMES:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Unsupported regime: {regime}", 400)
        cache_key = self._selection_cache_key(run_id, rule_source_id, topology_id, model_id, regime, decision_interface)
        cached = self._EXISTING_ANALYSIS_CACHE.get(cache_key)
        if cached is not None:
            self._EXISTING_ANALYSIS_CACHE.move_to_end(cache_key)
            return deepcopy(cached)
        config = self._read_json(run_root / "run_progress.json").get("config", {})
        risk_threshold = float(config.get("risk_threshold", 0.82))
        decision_topology = self._load_topology(run_root, rule_source_id, topology_id)
        scenarios = self._load_scenarios(run_root, topology_id, regime)
        m5_rows = self._load_parquet(run_root / "M5" / "observation_trials.parquet")
        m7_rows = self._load_parquet(run_root / "M7" / "decision_validation_trials.parquet")
        m8_rows = self._load_parquet(run_root / "M8" / "decoupled_2_stage_metrics.parquet")
        trials = self._select_trials(m5_rows, topology_id, model_id, regime, max(1, int(config.get("scenarios_per_regime", 8))))
        if not trials:
            raise BoundaryServiceError("BOUNDARY_INPUT_INSUFFICIENT", "Published Run does not contain deployment residual trials for this selection.", details={"topology_id": topology_id, "model_id": model_id, "regime": regime})
        selected_m7 = self._select_m7_rows(m7_rows, topology_id, model_id, regime, rule_source_id, decision_interface, "deployment")
        if not selected_m7:
            if decision_interface != "rule_based":
                response = self._unavailable_gai_response(self._tolerance_context(run_id, rule_source_id, topology_id, model_id, regime, decision_interface, config), self._analytical_boundary(decision_topology, scenarios, risk_threshold), None, None, run_root, trials)
                self._read_model_cache_put(self._EXISTING_ANALYSIS_CACHE, cache_key, response)
                return deepcopy(response)
            raise BoundaryServiceError("BOUNDARY_INPUT_INSUFFICIENT", "Published Run does not contain M7 deployment facts for this selection.")
        scenario_by_id = {str(row["scenario_id"]): row for row in scenarios}
        m7_by_index = self._m7_by_trial_index(selected_m7)
        trial_results: list[dict[str, Any]] = []
        for row in trials:
            index = int(row["trial_index"])
            scenario = scenario_by_id.get(str(row.get("scenario_id")))
            if scenario is None:
                raise BoundaryServiceError("SOURCE_LINEAGE_MISMATCH", f"M4 scenario is missing: {row.get('scenario_id')}")
            truth = self._population(scenario.get("scenario_gt_population"))
            residuals = self._population_float(row.get("sampled_residuals"))
            observed = self._population(row.get("observation_population"))
            effective_residual = {node_id: int(observed.get(node_id, 0)) - int(truth.get(node_id, 0)) for node_id in sorted(set(truth) | set(observed))}
            m7 = m7_by_index.get(index)
            if m7 is None:
                raise BoundaryServiceError("SOURCE_LINEAGE_MISMATCH", f"M7 deployment trial is missing: {index}")
            trial_results.append({
                "trial_id": row.get("trial_id"),
                "trial_index": index,
                "scenario_id": row.get("scenario_id"),
                "scenario_checksum": scenario.get("scenario_checksum"),
                "valid": bool(float(m7.get("valid", 0))),
                "signed_error_mean": self._round(sum(effective_residual.values()) / max(len(effective_residual), 1)),
                "mae": self._round(self._mean_abs(effective_residual.values())),
                "residuals": effective_residual,
                "violation_reasons": self._parse_json_cell(m7.get("violation_reasons"), []),
                "observation_checksum": row.get("observation_checksum"),
                "m7_result_checksum": self._object_checksum(m7),
            })
        valid_rows = [item for item in trial_results if item["valid"]]
        failed_rows = [item for item in trial_results if not item["valid"]]
        all_errors = [abs(float(value)) for item in trial_results for value in item["residuals"].values()]
        successful_errors = [abs(float(value)) for item in valid_rows for value in item["residuals"].values()]
        failed_errors = [abs(float(value)) for item in failed_rows for value in item["residuals"].values()]
        ideal_metric = self._select_metric(m8_rows, rule_source_id, topology_id, model_id, regime, decision_interface, FRAMEWORK_WITHOUT, "ideal")
        deployment_metric = self._select_metric(m8_rows, rule_source_id, topology_id, model_id, regime, decision_interface, FRAMEWORK_WITH, "deployment")
        deployment_value = self._number(deployment_metric.get("r_deploy")) if deployment_metric else self._round(len(valid_rows) / len(trial_results))
        ideal_value = self._number(ideal_metric.get("r_ideal")) if ideal_metric else None
        errors = self._error_summary(all_errors, trial_results)
        observed_interval = {
            "max_error_among_successful_trials": self._round(max(successful_errors)) if successful_errors else None,
            "min_error_among_failed_trials": self._round(min(failed_errors)) if failed_errors else None,
            "status": "observed_interval" if successful_errors and failed_errors and max(successful_errors) < min(failed_errors) else "overlap_or_insufficient",
        }
        reason_counts = self._reason_counts(trial_results)
        interpretation = "Observed error interval is not a precise critical boundary." if observed_interval["status"] != "observed_interval" else "This interval describes the errors observed in this Run; it is not a universal model accuracy threshold."
        context = self._tolerance_context(run_id, rule_source_id, topology_id, model_id, regime, decision_interface, config)
        response = {
            "schema_version": self.TOLERANCE_SCHEMA_VERSION,
            "analysis_type": "perception_error_tolerance_boundary",
            "analysis_mode": "EXISTING_RUN_ANALYSIS",
            "analysis_label": "Observed Estimate",
            "formal_artifacts_modified": False,
            "context": context,
            "observed_estimate": {
                "status": "available",
                "r_deploy": deployment_value,
                "r_ideal": ideal_value,
                "valid_trial_count": len(valid_rows),
                "executed_trial_count": len(trial_results),
                "failure_trial_count": len(failed_rows),
                "error_summary": errors,
                "observed_error_interval": observed_interval,
                "violation_reason_counts": reason_counts,
                "trial_results": trial_results,
                "interpretation": interpretation,
            },
            "analytical_boundary": self._analytical_boundary(decision_topology, scenarios, risk_threshold),
            "boundary_sweep": {"status": "not_started", "available": decision_interface == "rule_based"},
            "source_checksums": self._source_checksums(run_root),
            "interpretation": {"status": "available", "message": interpretation, "ideal_r": ideal_value, "deployment_r": deployment_value},
        }
        self._read_model_cache_put(self._EXISTING_ANALYSIS_CACHE, cache_key, response)
        return deepcopy(response)

    def create_boundary_job(self, run_id: str, request: dict[str, Any]) -> dict[str, Any]:
        run_root = self.storage_root / run_id
        self._require_published_run(run_root)
        selection = self._selection_from_request(request)
        capability = self.boundary_capability(run_id, **selection)
        sweep = capability["boundary_sweep"]
        if not sweep.get("available"):
            raise BoundaryServiceError("BOUNDARY_NOT_SUPPORTED", str(sweep.get("reason") or "Boundary sweep is unavailable."), 409)
        import uuid
        job_id = f"boundary-{uuid.uuid4().hex[:12]}"
        job_root = run_root / "boundary_analysis" / job_id
        job_root.mkdir(parents=True, exist_ok=False)
        config = {
            "job_id": job_id,
            "source_run_id": run_id,
            "analysis_mode": "BOUNDARY_SWEEP",
            **selection,
            "lambda_min": 0.0,
            "lambda_max": 1.0,
            "lambda_step": 0.05,
            "lambda_points": list(self.LAMBDA_GRID),
            "reuse_scenarios": True,
            "reuse_residuals": True,
            "requested_at": self._now_iso(),
            "source_checksums": capability["source_checksums"],
        }
        self._write_json(job_root / "boundary_config.json", config)
        self._write_json(job_root / "source_lineage.json", {"source_run_id": run_id, "source_checksums": capability["source_checksums"], "formal_artifacts_modified": False})
        self._write_job(job_root, {"job_id": job_id, "source_run_id": run_id, "status": "QUEUED", "config": config, "completed_lambda_count": 0, "total_lambda_count": len(self.LAMBDA_GRID), "message": "Boundary Sweep queued."})
        return {"job_id": job_id, "source_run_id": run_id, "status": "QUEUED", "config": config}

    def run_boundary_job(self, settings: Settings, run_id: str, job_id: str, cancel_check: Any) -> None:
        run_root = self.storage_root / run_id
        job_root = run_root / "boundary_analysis" / job_id
        config = self._read_json(job_root / "boundary_config.json")
        self._write_job(job_root, {**self._read_job(job_root), "status": "PREFLIGHT", "message": "Boundary inputs frozen."})
        selection = {key: config[key] for key in ("rule_source_id", "topology_id", "model_id", "regime", "decision_interface")}
        context_payload = self.existing_run_analysis(run_id, **selection)
        if selection["decision_interface"] != "rule_based":
            raise BoundaryServiceError("BOUNDARY_NOT_SUPPORTED", "Computed Boundary Sweep currently supports Rule-based only.")
        data = self._sweep_inputs(run_root, config)
        curve: list[dict[str, Any]] = []
        total = len(self.LAMBDA_GRID)
        for index, lambda_value in enumerate(self.LAMBDA_GRID):
            if cancel_check():
                self._write_job(job_root, {**self._read_job(job_root), "status": "CANCELLED", "message": "Boundary Sweep cancelled."})
                return
            cache_path = job_root / f"lambda_{lambda_value:.2f}.json"
            if cache_path.is_file():
                result = self._read_json(cache_path)
            else:
                self._write_job(job_root, {**self._read_job(job_root), "status": "RUNNING_LAMBDA_SWEEP", "current_lambda": lambda_value, "completed_lambda_count": index, "total_lambda_count": total})
                result = self._evaluate_lambda(lambda_value, data)
                self._write_json(cache_path, result)
            curve.append(result)
            self._write_curve_csv(job_root / "lambda_curve.csv", curve)
            self._write_job(job_root, {**self._read_job(job_root), "status": "RUNNING_LAMBDA_SWEEP", "current_lambda": lambda_value, "completed_lambda_count": index + 1, "total_lambda_count": total, "completed_trial_count": sum(int(row.get("executed_trial_count", 0)) for row in curve)})
        self._write_job(job_root, {**self._read_job(job_root), "status": "AGGREGATING", "message": "Aggregating reliability targets."})
        focus_curve, focus_meta = self._build_focus_curve(curve, data)
        analysis_curve = self._merge_lambda_curves(curve, focus_curve)
        audit = self._monotonicity_audit(curve)
        targets = self._target_results(analysis_curve, context_payload.get("observed_estimate", {}).get("r_ideal"))
        observed_summary = {key: value for key, value in (context_payload.get("observed_estimate") or {}).items() if key != "trial_results"}
        curve_summary = [{key: value for key, value in row.items() if key != "trial_results"} for row in curve]
        focus_curve_summary = [{key: value for key, value in row.items() if key != "trial_results"} for row in focus_curve]
        summary = {**context_payload, "analysis_mode": "BOUNDARY_SWEEP", "analysis_label": "Computed Boundary", "observed_estimate": observed_summary, "boundary_sweep": {"status": "available", "lambda_curve": curve_summary, "focus_lambda_curve": focus_curve_summary, "focus": focus_meta, "targets": targets, "monotonicity": audit, "source_checksums": config["source_checksums"], "formal_artifacts_modified": False}}
        self._write_json(job_root / "boundary_targets.json", {"targets": targets})
        self._write_json(job_root / "monotonicity_audit.json", audit)
        self._write_json(job_root / "boundary_summary.json", summary)
        self._write_curve_csv(job_root / "boundary_focus_curve.csv", focus_curve)
        self._write_jsonl(job_root / "trial_results.jsonl", [trial for row in analysis_curve for trial in row.get("trial_results", [])])
        self._write_text(job_root / "boundary_report.md", self.markdown(summary, title="Perception Error Tolerance Boundary"))
        self._write_job(job_root, {**self._read_job(job_root), "status": "SUCCEEDED", "message": "Boundary Sweep completed.", "completed_lambda_count": total, "summary_path": "boundary_summary.json"})

    def _build_focus_curve(self, curve: list[dict[str, Any]], data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        zero_rows = [
            row for row in curve
            if float(row.get("lambda", 0.0)) >= 0.0
            and row.get("r_deploy") is not None
            and float(row["r_deploy"]) <= 0.0
        ]
        if not zero_rows:
            return [], {
                "status": "NO_ZERO_OBSERVED",
                "first_zero_lambda": None,
                "lambda_min": 0.0,
                "lambda_max": None,
                "lambda_step": 0.01,
                "display_mode": "FULL_CURVE",
            }

        coarse_first_zero = min(float(row["lambda"]) for row in zero_rows)
        point_count = int(round(coarse_first_zero * 100.0))
        focus_points = [round(index / 100.0, 2) for index in range(point_count + 1)]
        by_lambda = {round(float(row["lambda"]), 2): row for row in curve}
        focus_curve: list[dict[str, Any]] = []
        first_zero: float | None = None
        for lambda_value in focus_points:
            row = by_lambda.get(lambda_value)
            if row is None:
                row = self._evaluate_lambda(lambda_value, data)
                by_lambda[lambda_value] = row
            focus_curve.append(row)
            if row.get("r_deploy") is not None and float(row["r_deploy"]) <= 0.0:
                first_zero = lambda_value
                break

        # A coarse zero should always be encountered by the fine scan. Keep a
        # deterministic fallback for malformed/legacy rows rather than
        # returning an unbounded display curve.
        if first_zero is None:
            first_zero = coarse_first_zero
        return focus_curve, {
            "status": "BASELINE_FAILED" if first_zero == 0.0 else "FIRST_ZERO_REACHED",
            "first_zero_lambda": self._round(first_zero),
            "lambda_min": 0.0,
            "lambda_max": self._round(first_zero),
            "lambda_step": 0.01,
            "display_mode": "FOCUS_TO_FIRST_ZERO",
            "complete_curve_retained": True,
        }

    @staticmethod
    def _merge_lambda_curves(*curves: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[float, dict[str, Any]] = {}
        for curve in curves:
            for row in curve:
                merged[round(float(row["lambda"]), 2)] = row
        return [merged[key] for key in sorted(merged)]

    def get_boundary_job(self, run_id: str, job_id: str) -> dict[str, Any]:
        job_root = self.storage_root / run_id / "boundary_analysis" / job_id
        if not job_root.is_dir():
            raise BoundaryServiceError("BOUNDARY_JOB_NOT_FOUND", f"Boundary job not found: {job_id}", 404)
        return self._read_job(job_root)

    def get_boundary_job_file(self, run_id: str, job_id: str, name: str) -> dict[str, Any]:
        job_root = self.storage_root / run_id / "boundary_analysis" / job_id
        allowed = {"boundary_summary.json", "boundary_targets.json", "monotonicity_audit.json"}
        if name not in allowed or not (job_root / name).is_file():
            raise BoundaryServiceError("BOUNDARY_JOB_FILE_NOT_FOUND", f"Boundary result is not available: {name}", 404)
        return self._read_json(job_root / name)

    def get_boundary_curve(self, run_id: str, job_id: str) -> list[dict[str, Any]]:
        job_root = self.storage_root / run_id / "boundary_analysis" / job_id
        path = job_root / "lambda_curve.csv"
        if not path.is_file():
            return []
        import ast
        import csv
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows: list[dict[str, Any]] = []
            for row in csv.DictReader(handle):
                normalized: dict[str, Any] = dict(row)
                for key in ("lambda", "r_deploy"):
                    if normalized.get(key) not in (None, ""):
                        normalized[key] = float(normalized[key])
                for key in ("valid_trial_count", "executed_trial_count", "violation_trial_count"):
                    if normalized.get(key) not in (None, ""):
                        normalized[key] = int(normalized[key])
                raw_reasons = normalized.get("violation_reason_counts")
                if raw_reasons in (None, ""):
                    normalized["violation_reason_counts"] = {}
                else:
                    try:
                        parsed_reasons = ast.literal_eval(raw_reasons)
                        normalized["violation_reason_counts"] = parsed_reasons if isinstance(parsed_reasons, dict) else {}
                    except (SyntaxError, ValueError):
                        normalized["violation_reason_counts"] = {}
                rows.append(normalized)
            return rows

    def get_boundary_trials(self, run_id: str, job_id: str) -> list[dict[str, Any]]:
        path = self.storage_root / run_id / "boundary_analysis" / job_id / "trial_results.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _selection_from_request(self, request: dict[str, Any]) -> dict[str, Any]:
        required = ("rule_source_id", "topology_id", "model_id", "regime", "decision_interface")
        missing = [key for key in required if not str(request.get(key, "")).strip()]
        if missing:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Missing boundary selection: {', '.join(missing)}", 400)
        return {
            "rule_source_id": str(request["rule_source_id"]),
            "topology_id": str(request["topology_id"]),
            "model_id": str(request["model_id"]),
            "regime": str(request["regime"]).upper(),
            "decision_interface": str(request["decision_interface"]),
        }

    def _boundary_unavailable_reason(self, decision_interface: str, trials: list[dict[str, Any]], scenarios: list[dict[str, Any]], has_residuals: bool) -> str:
        if decision_interface != "rule_based":
            return "Computed Boundary unavailable — GAI replay is not enabled."
        if not scenarios:
            return "BOUNDARY_INPUT_INSUFFICIENT: M4 scenarios are missing."
        if not trials:
            return "BOUNDARY_INPUT_INSUFFICIENT: M5 deployment trials are missing."
        if not has_residuals:
            return "RESIDUAL_SOURCE_MISSING: sampled empirical residuals are missing."
        return "Boundary Sweep is unavailable for this selection."

    def _tolerance_context(self, run_id: str, rule_source_id: str, topology_id: str, model_id: str, regime: str, decision_interface: str, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "topology_id": topology_id,
            "model_id": model_id,
            "regime": regime,
            "rule_source_id": rule_source_id,
            "decision_interface": decision_interface,
            "framework_mode": FRAMEWORK_WITH,
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
            "risk_threshold": float(config.get("risk_threshold", 0.82)),
            "decision_policy_version": config.get("decision_policy_version"),
            "rounding_policy": config.get("rounding", "round_half_up"),
            "negative_handling": config.get("negative_handling", "floor_at_zero"),
            "m7_truth_source": "M4/scenario_gt.jsonl",
        }

    def _source_checksums(self, run_root: Path) -> dict[str, str]:
        paths = {
            "m4_scenario_gt": run_root / "M4" / "scenario_gt.jsonl",
            "m5_observation_trials": run_root / "M5" / "observation_trials.parquet",
            "m7_validation_trials": run_root / "M7" / "decision_validation_trials.parquet",
            "m8_metrics": run_root / "M8" / "decoupled_2_stage_metrics.parquet",
        }
        return {name: self._sha256(path) for name, path in paths.items() if path.is_file()}

    def _effective_run_status(self, run_root: Path) -> str:
        summary_path = run_root / "M9" / "run_summary.json"
        if summary_path.is_file():
            summary_status = str(self._read_json(summary_path).get("status", "")).upper()
            if summary_status:
                return summary_status
        progress_path = run_root / "run_progress.json"
        return str(self._read_json(progress_path).get("status", "UNKNOWN")).upper() if progress_path.is_file() else "UNKNOWN"

    def _sweep_inputs(self, run_root: Path, config: dict[str, Any]) -> dict[str, Any]:
        run_config = self._read_json(run_root / "run_progress.json").get("config", {})
        regime = str(config["regime"]).upper()
        scenarios = self._load_scenarios(run_root, config["topology_id"], regime)
        m5_rows = self._load_parquet(run_root / "M5" / "observation_trials.parquet")
        m5_trials = self._select_trials(m5_rows, config["topology_id"], config["model_id"], regime, max(1, int(run_config.get("scenarios_per_regime", 8))))
        decision_topology = self._load_topology(run_root, config["rule_source_id"], config["topology_id"])
        validation_topology = self._load_topology(run_root, RULE_SOURCE_HUMAN, config["topology_id"])
        scenario_by_id = {str(row["scenario_id"]): row for row in scenarios}
        return {
            "run_config": run_config,
            "config": config,
            "trials": m5_trials,
            "scenario_by_id": scenario_by_id,
            "decision_topology": decision_topology,
            "validation_topology": validation_topology,
            "risk_threshold": float(run_config.get("risk_threshold", 0.82)),
        }

    def _evaluate_lambda(self, lambda_value: float, data: dict[str, Any]) -> dict[str, Any]:
        trial_results: list[dict[str, Any]] = []
        all_errors: list[float] = []
        for row in data["trials"]:
            scenario = data["scenario_by_id"].get(str(row.get("scenario_id")))
            if scenario is None:
                raise BoundaryServiceError("SOURCE_LINEAGE_MISMATCH", f"M4 scenario is missing: {row.get('scenario_id')}")
            truth = self._population(scenario.get("scenario_gt_population"))
            residuals = self._population_float(row.get("sampled_residuals"))
            observed = self._scaled_observation(truth, residuals, lambda_value)
            effective_residuals = {node_id: int(observed.get(node_id, 0)) - int(truth.get(node_id, 0)) for node_id in sorted(set(truth) | set(observed))}
            all_errors.extend(abs(float(value)) for value in effective_residuals.values())
            actions = self.use_case._decide_actions(data["decision_topology"], observed, data["risk_threshold"])
            validation = self.use_case._validate_actions(data["validation_topology"], truth, actions)
            reasons = validation.get("violation_reasons", [])
            trial_results.append({
                "trial_id": row.get("trial_id"),
                "trial_index": int(row.get("trial_index", 0)),
                "scenario_id": row.get("scenario_id"),
                "scenario_checksum": scenario.get("scenario_checksum"),
                "lambda": self._round(lambda_value),
                "decision_input_checksum": self._object_checksum(observed),
                "m6_output_checksum": self._object_checksum(actions),
                "m7_result_checksum": self._object_checksum(validation),
                "valid": bool(validation.get("valid")),
                "violation_reasons": reasons,
                "violation_codes": sorted({str(reason.get("code", "unknown")) for reason in reasons}),
                "effective_residual": effective_residuals,
                "observed_population": observed,
            })
        valid_count = sum(1 for row in trial_results if row["valid"])
        reason_counts = self._reason_counts(trial_results)
        return {
            "lambda": self._round(lambda_value),
            "label": "ideal_input" if lambda_value == 0 else ("formal_m5_observation" if lambda_value == 1 else "counterfactual"),
            "valid_trial_count": valid_count,
            "executed_trial_count": len(trial_results),
            "r_deploy": self._round(valid_count / len(trial_results)) if trial_results else None,
            "violation_trial_count": len(trial_results) - valid_count,
            "violation_reason_counts": reason_counts,
            "error_summary": self._error_summary(all_errors, trial_results),
            "trial_results": trial_results,
        }

    def _error_summary(self, all_errors: list[float], trial_results: list[dict[str, Any]]) -> dict[str, Any]:
        signed_values = [float(value) for row in trial_results for value in row.get("effective_residual", row.get("residuals", {})).values()]
        absolute = [abs(value) for value in signed_values]
        if not absolute:
            return {"signed_mean": 0.0, "mae": 0.0, "rmse": 0.0, "std": 0.0, "p90_absolute_error": 0.0, "max_absolute_error": 0.0, "underestimate_rate": 0.0, "overestimate_rate": 0.0}
        mean = sum(signed_values) / len(signed_values)
        variance = sum((value - mean) ** 2 for value in signed_values) / len(signed_values)
        sorted_abs = sorted(absolute)
        p90_index = min(len(sorted_abs) - 1, max(0, math.ceil(len(sorted_abs) * 0.9) - 1))
        return {
            "signed_mean": self._round(mean),
            "mae": self._round(sum(absolute) / len(absolute)),
            "rmse": self._round(math.sqrt(sum(value * value for value in signed_values) / len(signed_values))),
            "std": self._round(math.sqrt(variance)),
            "p90_absolute_error": self._round(sorted_abs[p90_index]),
            "max_absolute_error": self._round(max(absolute)),
            "underestimate_rate": self._round(sum(1 for value in signed_values if value < 0) / len(signed_values)),
            "overestimate_rate": self._round(sum(1 for value in signed_values if value > 0) / len(signed_values)),
        }

    def _reason_counts(self, trial_results: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in trial_results:
            for reason in row.get("violation_reasons", []):
                code = str(reason.get("code", "unknown"))
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    def _monotonicity_audit(self, curve: list[dict[str, Any]]) -> dict[str, Any]:
        values = [float(row["r_deploy"]) for row in curve if row.get("r_deploy") is not None]
        upward_jumps = [values[index] - values[index - 1] for index in range(1, len(values)) if values[index] > values[index - 1] + 1e-9]
        return {
            "curve_is_monotonic_nonincreasing": not upward_jumps,
            "warning_code": "NON_MONOTONIC_RELIABILITY_CURVE" if upward_jumps else None,
            "largest_upward_jump": self._round(max(upward_jumps)) if upward_jumps else 0.0,
        }

    def _target_results(self, curve: list[dict[str, Any]], ideal_value: float | None) -> list[dict[str, Any]]:
        if ideal_value is not None and ideal_value <= 0:
            return [{"target": label, "status": "BASELINE_FAILED", "safe_critical_lambda": None, "required_error_reduction_pct": None} for label, _ in self.TOLERANCE_TARGETS]
        results: list[dict[str, Any]] = []
        for label, threshold in self.TOLERANCE_TARGETS:
            required = (1 / max(int(curve[0].get("executed_trial_count", 1)), 1)) if threshold == "positive" and curve else 1.0
            eligible = [row for row in curve if row.get("r_deploy") is not None and float(row["r_deploy"]) >= (required if threshold == "positive" else float(threshold))]
            prefix: list[dict[str, Any]] = []
            for row in curve:
                if row.get("r_deploy") is None or float(row["r_deploy"]) < (required if threshold == "positive" else float(threshold)):
                    break
                prefix.append(row)
            safe = float(prefix[-1]["lambda"]) if prefix else None
            status = "NOT_REACHED" if safe is None else ("ABOVE_SEARCH_RANGE" if safe >= 1.0 and len(prefix) == len(curve) else "REACHED")
            results.append({
                "target": label,
                "threshold": required if threshold == "positive" else threshold,
                "safe_critical_lambda": self._round(safe),
                "critical_lambda": self._round(max((float(row["lambda"]) for row in eligible), default=None)),
                "required_error_reduction_pct": self._round(max(0.0, 1.0 - safe) * 100.0) if safe is not None else None,
                "status": status,
            })
        return results

    def _write_job(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_json(path / "boundary_job.json", {**payload, "updated_at": self._now_iso()})

    def _read_job(self, path: Path) -> dict[str, Any]:
        return self._read_json(path / "boundary_job.json")

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    def _write_text(self, path: Path, value: str) -> None:
        path.write_text(value, encoding="utf-8")

    def _write_curve_csv(self, path: Path, curve: list[dict[str, Any]]) -> None:
        import csv
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["lambda", "r_deploy", "valid_trial_count", "executed_trial_count", "violation_trial_count", "violation_reason_counts"])
            writer.writeheader()
            for row in curve:
                writer.writerow({key: row.get(key) for key in writer.fieldnames})

    def _parse_json_cell(self, value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def _mean_abs(self, values: Any) -> float:
        numbers = [abs(float(value)) for value in values]
        return sum(numbers) / len(numbers) if numbers else 0.0

    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def analyze(
        self,
        run_id: str,
        *,
        rule_source_id: str,
        topology_id: str,
        model_id: str,
        regime: str,
        decision_interface: str,
    ) -> dict[str, Any]:
        run_root = self.storage_root / run_id
        self._require_published_run(run_root)
        regime = regime.upper()
        if regime not in REGIMES:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Unsupported regime: {regime}", 400)
        if decision_interface not in {"rule_based", "gai", "gai_reserved"}:
            raise BoundaryServiceError("INVALID_BOUNDARY_SELECTION", f"Unsupported decision interface: {decision_interface}", 400)
        cache_key = (run_id, rule_source_id, topology_id, model_id, regime, decision_interface)
        cached = self._CACHE.get(cache_key)
        if cached is not None:
            self._CACHE.move_to_end(cache_key)
            return deepcopy(cached)

        config = self._read_json(run_root / "run_progress.json").get("config", {})
        risk_threshold = float(config.get("risk_threshold", 0.82))
        human_topology = self._load_topology(run_root, RULE_SOURCE_HUMAN, topology_id)
        decision_topology = self._load_topology(run_root, rule_source_id, topology_id)
        scenarios = self._load_scenarios(run_root, topology_id, regime)
        m5_rows = self._load_parquet(run_root / "M5" / "observation_trials.parquet")
        m7_rows = self._load_parquet(run_root / "M7" / "decision_validation_trials.parquet")
        m8_rows = self._load_parquet(run_root / "M8" / "decoupled_2_stage_metrics.parquet")
        trials = self._select_trials(
            m5_rows,
            topology_id,
            model_id,
            regime,
            max(1, int(config.get("scenarios_per_regime", 8))),
        )
        if not trials:
            raise BoundaryServiceError(
                "BOUNDARY_ARTIFACT_INCOMPLETE",
                "Published Run does not contain M5 deployment residuals for this selection.",
                details={"topology_id": topology_id, "model_id": model_id, "regime": regime},
            )

        ideal_metric = self._select_metric(m8_rows, rule_source_id, topology_id, model_id, regime, decision_interface, FRAMEWORK_WITHOUT, "ideal")
        deployment_metric = self._select_metric(m8_rows, rule_source_id, topology_id, model_id, regime, decision_interface, FRAMEWORK_WITH, "deployment")
        ideal_value = self._number(ideal_metric.get("r_ideal")) if ideal_metric else None
        published_deploy = self._number(deployment_metric.get("r_deploy")) if deployment_metric else None

        analytical = self._analytical_boundary(decision_topology, scenarios, risk_threshold)
        context = {
            "run_id": run_id,
            "topology_id": topology_id,
            "model_id": model_id,
            "regime": regime,
            "rule_source_id": rule_source_id,
            "decision_interface": decision_interface,
            "validation_rule_source_id": RULE_SOURCE_HUMAN,
            "risk_threshold": risk_threshold,
            "decision_policy_version": config.get("decision_policy_version"),
            "m7_truth_source": "M4/scenario_gt.jsonl",
        }

        if decision_interface != "rule_based":
            response = self._unavailable_gai_response(context, analytical, ideal_value, published_deploy, run_root, trials)
            self._cache_put(cache_key, response)
            return deepcopy(response)

        scenario_by_id = {str(row["scenario_id"]): row for row in scenarios}
        selected_m7_deployment = self._select_m7_rows(m7_rows, topology_id, model_id, regime, rule_source_id, decision_interface, "deployment")
        selected_m7_ideal = self._select_m7_rows(m7_rows, topology_id, model_id, regime, rule_source_id, decision_interface, "ideal")
        trial_sensitivities = self._build_trial_sensitivities(
            trials,
            scenario_by_id,
            decision_topology,
            human_topology,
            risk_threshold,
        )
        all_alpha_points = sorted({point for item in trial_sensitivities for point in item["alphas"]})
        all_evaluations = [self._aggregate_at(alpha, trial_sensitivities) for alpha in all_alpha_points]
        empirical = self._empirical_boundary(all_evaluations, ideal_value, published_deploy, selected_m7_deployment, selected_m7_ideal)
        display_alphas = sorted({0.0, 0.25, 0.5, 0.75, 1.0, *[
            float(item["max_alpha"])
            for item in empirical.get("target_thresholds", [])
            if item.get("max_alpha") is not None
        ]})
        display_evaluations = [self._aggregate_at(alpha, trial_sensitivities) for alpha in display_alphas]
        empirical["alpha_points"] = display_evaluations
        empirical["evaluated_transition_count"] = len(all_alpha_points)
        empirical["trial_transition_counts"] = [
            {"trial_id": item["trial_id"], "transition_count": len(item["alphas"])}
            for item in trial_sensitivities
        ]
        response = {
            "schema_version": self.SCHEMA_VERSION,
            "analysis_type": "counterfactual_sensitivity",
            "analysis_version": self.ANALYSIS_VERSION,
            "formal_artifacts_modified": False,
            "context": context,
            "analytical_boundary": analytical,
            "empirical_boundary": empirical,
            "critical_evidence": self._critical_evidence(all_evaluations),
            "source_checksums": {
                "m4_scenario_gt": self._sha256(run_root / "M4" / "scenario_gt.jsonl"),
                "m5_observation_trials": self._sha256(run_root / "M5" / "observation_trials.parquet"),
                "m7_validation_trials": self._sha256(run_root / "M7" / "decision_validation_trials.parquet"),
                "m8_metrics": self._sha256(run_root / "M8" / "decoupled_2_stage_metrics.parquet"),
            },
            "interpretation": self._interpretation(ideal_value, published_deploy, empirical),
        }
        response["analysis_checksum"] = self._object_checksum(response)
        self._cache_put(cache_key, response)
        return deepcopy(response)

    @classmethod
    def _cache_put(cls, key: tuple[str, str, str, str, str, str], payload: dict[str, Any]) -> None:
        cls._CACHE[key] = deepcopy(payload)
        cls._CACHE.move_to_end(key)
        while len(cls._CACHE) > cls.CACHE_SIZE:
            cls._CACHE.popitem(last=False)

    @classmethod
    def _read_model_cache_put(
        cls,
        cache: OrderedDict[tuple[str, str, str, str, str, str], dict[str, Any]],
        key: tuple[str, str, str, str, str, str],
        payload: dict[str, Any],
    ) -> None:
        cache[key] = deepcopy(payload)
        cache.move_to_end(key)
        while len(cache) > cls.READ_MODEL_CACHE_SIZE:
            cache.popitem(last=False)

    @staticmethod
    def _selection_cache_key(
        run_id: str,
        rule_source_id: str,
        topology_id: str,
        model_id: str,
        regime: str,
        decision_interface: str,
    ) -> tuple[str, str, str, str, str, str]:
        return (run_id, rule_source_id, topology_id, model_id, regime.upper(), decision_interface)

    def _build_trial_sensitivities(
        self,
        trials: list[dict[str, Any]],
        scenario_by_id: dict[str, dict[str, Any]],
        decision_topology: dict[str, Any],
        validation_topology: dict[str, Any],
        risk_threshold: float,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for row in trials:
            scenario = scenario_by_id.get(str(row.get("scenario_id")))
            if scenario is None:
                raise BoundaryServiceError("BOUNDARY_ARTIFACT_INCOMPLETE", f"M4 scenario is missing: {row.get('scenario_id')}")
            truth = self._population(scenario.get("scenario_gt_population"))
            residuals = self._population_float(row.get("sampled_residuals"))
            relevant_nodes: set[str] = set()
            for source_id in decision_topology.get("source_nodes", []):
                capacity = max(int(decision_topology["capacity_by_node"].get(source_id, 1)), 1)
                endpoint_max = max(
                    float(truth.get(source_id, 0)),
                    float(truth.get(source_id, 0)) + float(residuals.get(source_id, 0.0)),
                )
                if endpoint_max / capacity >= risk_threshold:
                    relevant_nodes.add(str(source_id))
                    relevant_nodes.update(str(node_id) for node_id in decision_topology.get("adjacency", {}).get(source_id, []))
            alphas = self._alpha_points(
                [{**row, "scenario_gt_population": truth}],
                relevant_nodes=relevant_nodes,
            )
            states: list[dict[str, Any]] = []
            for alpha in alphas:
                observed = self._scaled_observation(truth, residuals, alpha)
                actions = self.use_case._decide_actions(decision_topology, observed, risk_threshold)
                validation = self.use_case._validate_actions(validation_topology, truth, actions)
                reason_counts: dict[str, int] = {}
                for reason in validation.get("violation_reasons", []):
                    code = str(reason.get("code", "unknown"))
                    reason_counts[code] = reason_counts.get(code, 0) + 1
                states.append({
                    "valid": bool(validation["valid"]),
                    "violation_reason_counts": dict(sorted(reason_counts.items())),
                    "evidence": {
                        "trial_id": row.get("trial_id"),
                        "trial_index": row.get("trial_index"),
                        "scenario_id": row.get("scenario_id"),
                        "observed_population_checksum": self._object_checksum(observed),
                        "action_count": len(actions),
                        "valid": bool(validation["valid"]),
                        "violation_reasons": validation.get("violation_reasons", []),
                    },
                })
            results.append({"trial_id": row.get("trial_id"), "alphas": alphas, "states": states})
        return results

    def _aggregate_at(self, alpha: float, trial_sensitivities: list[dict[str, Any]]) -> dict[str, Any]:
        valid_count = 0
        violation_counts: dict[str, int] = {}
        trial_evidence: list[dict[str, Any]] = []
        for item in trial_sensitivities:
            index = min(bisect_right(item["alphas"], alpha) - 1, len(item["states"]) - 1)
            state = item["states"][max(index, 0)]
            valid_count += int(state["valid"])
            for code, count in state["violation_reason_counts"].items():
                violation_counts[code] = violation_counts.get(code, 0) + int(count)
            trial_evidence.append(state["evidence"])
        executed = len(trial_sensitivities)
        return {
            "alpha": self._round(alpha),
            "label": "ideal_input" if alpha == 0 else ("formal_m5_observation" if alpha == 1 else "counterfactual"),
            "valid_trial_count": valid_count,
            "executed_trial_count": executed,
            "r_deploy": self._round(valid_count / executed) if executed else None,
            "violation_trial_count": executed - valid_count,
            "violation_reason_counts": dict(sorted(violation_counts.items())),
            "trial_evidence": trial_evidence,
        }

    def markdown(self, payload: dict[str, Any], title: str = "Perception Error Boundary") -> str:
        context = payload["context"]
        empirical = payload.get("empirical_boundary") or payload.get("boundary_sweep") or {}
        observed = payload.get("observed_estimate") or {}
        lines = [
            f"# {title}",
            "",
            "> COUNTERFACTUAL ANALYSIS — NOT FORMAL M8/M9 RESULT",
            "",
            f"- Run: `{context['run_id']}`",
            f"- Condition: `{context['topology_id']} × {context['model_id']} × {context['regime']}`",
            f"- Rule source: `{context['rule_source_id']}`",
            f"- Decision interface: `{context['decision_interface']}`",
            "- α=0: ideal ground-truth input; α=1: published M5 observation.",
            "",
            "## Interpretation",
            "",
            str(payload.get("interpretation", {}).get("message", "")),
            "",
            "## Observed Estimate",
            "",
            f"- R_deploy: {self._display_number(observed.get('r_deploy'))}",
            f"- Valid / executed: {observed.get('valid_trial_count', 'unavailable')} / {observed.get('executed_trial_count', 'unavailable')}",
            "",
            "## Reliability Boundary",
            "",
            "| Target | Max residual scale | Required residual reduction | Status |",
            "| --- | ---: | ---: | --- |",
        ]
        for target in empirical.get("target_thresholds", empirical.get("targets", [])):
            alpha = target.get("safe_critical_lambda", target.get("max_alpha"))
            reduction = target.get("required_error_reduction_pct")
            if reduction is not None and reduction > 1:
                reduction = reduction / 100.0
            lines.append(f"| {target['target']} | {self._display_number(alpha)} | {self._display_percent(reduction)} | {target['status']} |")
        focus = empirical.get("focus") or {}
        if focus:
            lines.extend(["", "## Focus Curve", "", f"- Status: `{focus.get('status')}`", f"- First zero lambda: `{self._display_number(focus.get('first_zero_lambda'))}`", "- Focus step: `0.01`", "- The complete coarse curve is retained separately."])
        lines.extend(["", "## Analytical Boundary", ""])
        lines.extend([
            "| Source | Ground truth | Capacity | First high-risk count | Signed boundary |",
            "| --- | ---: | ---: | ---: | ---: |",
        ])
        for row in payload.get("analytical_boundary", {}).get("sources", []):
            lines.append(f"| {row['source_id']} | {row['ground_truth_population']} | {row['capacity']} | {row['first_high_risk_count']} | {row['signed_error_boundary']} |")
        lines.extend(["", "## Sensitivity Points", "", "| α | R_deploy | Valid / executed | Main violations |", "| ---: | ---: | ---: | --- |"])
        for row in empirical.get("alpha_points", empirical.get("lambda_curve", [])):
            reasons = ", ".join(f"{key}={value}" for key, value in row.get("violation_reason_counts", {}).items()) or "none"
            lambda_value = row.get("lambda", row.get("alpha"))
            lines.append(f"| {float(lambda_value):.6f} | {self._display_number(row.get('r_deploy'))} | {row.get('valid_trial_count')} / {row.get('executed_trial_count')} | {reasons} |")
        return "\n".join(lines) + "\n"

    def _unavailable_gai_response(self, context: dict[str, Any], analytical: dict[str, Any], ideal: float | None, deployment: float | None, run_root: Path, trials: list[dict[str, Any]]) -> dict[str, Any]:
        reason = "Empirical boundary unavailable — GAI replay is required"
        response = {
            "schema_version": self.SCHEMA_VERSION,
            "analysis_type": "counterfactual_sensitivity",
            "analysis_version": self.ANALYSIS_VERSION,
            "formal_artifacts_modified": False,
            "context": context,
            "analytical_boundary": analytical,
            "empirical_boundary": {
                "status": "unavailable",
                "unavailable_reason": reason,
                "actual_alpha": 1.0,
                "target_thresholds": [],
                "alpha_points": [],
            },
            "critical_evidence": [],
            "source_checksums": {
                "m4_scenario_gt": self._sha256(run_root / "M4" / "scenario_gt.jsonl"),
                "m5_observation_trials": self._sha256(run_root / "M5" / "observation_trials.parquet"),
            },
            "interpretation": {
                "status": "unavailable",
                "message": reason + ". The service does not call GAI or substitute Rule-based actions.",
                "ideal_r": ideal,
                "deployment_r": deployment,
            },
        }
        response["analysis_checksum"] = self._object_checksum(response)
        return response

    def _empirical_boundary(
        self,
        evaluations: list[dict[str, Any]],
        ideal: float | None,
        published_deploy: float | None,
        selected_m7_deployment: list[dict[str, Any]],
        selected_m7_ideal: list[dict[str, Any]],
    ) -> dict[str, Any]:
        alpha_one = next((row for row in evaluations if abs(row["alpha"] - 1.0) < 1e-9), None)
        alpha_zero = next((row for row in evaluations if abs(row["alpha"]) < 1e-9), None)
        consistency: list[str] = []
        if ideal is not None and alpha_zero is not None and abs(float(alpha_zero["r_deploy"]) - float(ideal)) > 1e-6:
            consistency.append("alpha_zero_does_not_match_published_ideal")
        if published_deploy is not None and alpha_one is not None and abs(float(alpha_one["r_deploy"]) - float(published_deploy)) > 1e-6:
            consistency.append("alpha_one_does_not_match_published_deployment")
        m7_deployment_by_trial = self._m7_by_trial_index(selected_m7_deployment)
        m7_ideal_by_trial = self._m7_by_trial_index(selected_m7_ideal)
        if alpha_one is not None:
            consistency.extend(self._compare_m7_outcome(alpha_one, m7_deployment_by_trial, "alpha_one"))
        if alpha_zero is not None:
            consistency.extend(self._compare_m7_outcome(alpha_zero, m7_ideal_by_trial, "alpha_zero"))
        target_rows: list[dict[str, Any]] = []
        for label, threshold in self.TARGETS:
            matches = [row for row in evaluations if float(row["r_deploy"]) >= threshold]
            max_alpha = max((float(row["alpha"]) for row in matches), default=None)
            target_rows.append({
                "target": label,
                "threshold": threshold,
                "max_alpha": self._round(max_alpha) if max_alpha is not None else None,
                "required_residual_reduction": self._round(1.0 - max_alpha) if max_alpha is not None else None,
                "status": "reached" if max_alpha is not None else "not_reached",
            })
        status = "consistency_error" if consistency else ("baseline_failed" if ideal is not None and ideal <= 0 else "available")
        return {
            "status": status,
            "actual_alpha": 1.0,
            "max_alpha_for_positive_r": target_rows[0]["max_alpha"],
            "required_residual_reduction_for_positive_r": target_rows[0]["required_residual_reduction"],
            "target_thresholds": target_rows,
            "consistency_issues": consistency,
            "alpha_points": evaluations,
            "formal_m7_deployment_row_count": len(selected_m7_deployment),
            "formal_m7_ideal_row_count": len(selected_m7_ideal),
        }

    def _m7_by_trial_index(self, rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            value = row.get("trial_index")
            if value is not None:
                result[int(value)] = row
        return result

    def _compare_m7_outcome(self, evaluation: dict[str, Any], formal_rows: dict[int, dict[str, Any]], label: str) -> list[str]:
        issues: list[str] = []
        evidence = {int(item["trial_index"]): item for item in evaluation.get("trial_evidence", []) if item.get("trial_index") is not None}
        if len(formal_rows) != len(evidence):
            issues.append(f"{label}_m7_trial_count_mismatch")
        for trial_index, expected in evidence.items():
            formal = formal_rows.get(trial_index)
            if formal is None:
                issues.append(f"{label}_m7_trial_missing:{trial_index}")
                continue
            if bool(float(formal.get("valid", 0))) != bool(expected.get("valid")):
                issues.append(f"{label}_m7_valid_mismatch:{trial_index}")
        return issues

    def _interpretation(self, ideal: float | None, deployment: float | None, empirical: dict[str, Any]) -> dict[str, Any]:
        if ideal is None:
            return {"status": "unavailable", "message": "Published M8 ideal result is missing; no perception boundary is reported."}
        if ideal <= 0:
            return {"status": "baseline_failed", "message": "Ideal baseline failed — perception boundary is not interpretable. Correct population input already failed M7, so the result cannot be attributed to perception residual alone."}
        target = empirical.get("max_alpha_for_positive_r")
        if target is None:
            message = "No evaluated residual scale produced R_deploy > 0. The selected condition remains invalid across the available empirical sensitivity range."
        else:
            reduction = float(empirical.get("required_residual_reduction_for_positive_r") or 0.0)
            message = f"At the published residual scale, R_deploy is {self._display_number(deployment)}. To obtain R_deploy > 0, the residual magnitude must be reduced by at least {reduction:.1%} in this condition. This is a conditional sensitivity result, not a general perception accuracy threshold."
        return {"status": empirical.get("status", "available"), "message": message, "ideal_r": ideal, "deployment_r": deployment}

    def _analytical_boundary(self, topology: dict[str, Any], scenarios: list[dict[str, Any]], risk_threshold: float) -> dict[str, Any]:
        sources = topology.get("source_nodes", [])
        representative = scenarios[0].get("scenario_gt_population", {}) if scenarios else {}
        rows: list[dict[str, Any]] = []
        for source_id in sources:
            capacity = max(int(topology["capacity_by_node"].get(source_id, 1)), 1)
            ground_truth = int(representative.get(source_id, 0))
            first_high = int(math.ceil(risk_threshold * capacity))
            rows.append({
                "source_id": str(source_id),
                "ground_truth_population": ground_truth,
                "capacity": capacity,
                "risk_threshold": risk_threshold,
                "first_high_risk_count": first_high,
                "last_non_high_count": first_high - 1,
                "signed_error_boundary": first_high - 1 - ground_truth,
                "requested_move_formula": "round_half_up(observed_population - capacity * 0.70), clamped to [1, observed_population]",
                "requested_move_transition_points": self._move_transition_points(ground_truth, capacity),
            })
        return {
            "status": "available" if rows else "unavailable",
            "risk_rule": "observed_population / capacity >= risk_threshold",
            "sources": rows,
            "note": "Analytical boundaries describe M6 threshold transitions; they do not guarantee M7 validity.",
        }

    def _move_transition_points(self, ground_truth: int, capacity: int) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        start = max(0, ground_truth - 2)
        end = ground_truth + max(2, int(capacity * 0.35) + 2)
        previous: int | None = None
        for observed in range(start, end + 1):
            requested = min(max(1, self.use_case._round_half_up(observed - capacity * 0.70)), observed)
            if requested != previous:
                values.append({"observed_population": observed, "requested_move_count": requested})
                previous = requested
        return values

    def _alpha_points(self, trials: list[dict[str, Any]], relevant_nodes: set[str] | None = None) -> list[float]:
        points = {0.0, 1.0}
        for row in trials:
            truth = self._population(row.get("scenario_gt_population", {}))
            residuals = self._population_float(row.get("sampled_residuals"))
            for node_id, residual in residuals.items():
                if relevant_nodes is not None and node_id not in relevant_nodes:
                    continue
                if not residual:
                    continue
                start = int(truth.get(node_id, 0))
                end = start + residual
                low = math.floor(min(start, end)) - 2
                high = math.ceil(max(start, end)) + 2
                for integer_value in range(low, high + 1):
                    alpha = (integer_value + 0.5 - start) / residual
                    if 0.0 < alpha < 1.0 and math.isfinite(alpha):
                        points.add(round(alpha, 12))
        return sorted(points)

    def _critical_evidence(self, evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        examples: dict[str, list[dict[str, Any]]] = {}
        for row in evaluations:
            for evidence in row.get("trial_evidence", []):
                for reason in evidence.get("violation_reasons", []):
                    code = str(reason.get("code", "unknown"))
                    counts[code] = counts.get(code, 0) + 1
                    if len(examples.setdefault(code, [])) < 3:
                        examples[code].append({
                            "trial_id": evidence.get("trial_id"),
                            "scenario_id": evidence.get("scenario_id"),
                            "node_id": reason.get("node_id"),
                            "source_id": reason.get("source_id"),
                            "target_id": reason.get("target_id"),
                            "capacity": reason.get("capacity"),
                            "post_population": reason.get("post_population"),
                            "message": reason.get("message"),
                            "original_code": reason.get("code"),
                        })
        return [
            {"reason_code": code, "count": count, "examples": examples.get(code, [])}
            for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]

    def _select_trials(
        self,
        rows: list[dict[str, Any]],
        topology_id: str,
        model_id: str,
        regime: str,
        scenarios_per_regime: int,
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for row in rows:
            if not (
                str(row.get("topology_id")) == topology_id
                and str(row.get("model_id")) == model_id
                and str(row.get("ground_truth_regime", "")).upper() == regime
                and str(row.get("trial_type")) == "deployment"
            ):
                continue
            trial_index = self._trial_index(row)
            selected.append({
                **row,
                "trial_index": trial_index,
                "scenario_id": f"{topology_id}_{regime.lower()}_{trial_index % scenarios_per_regime:03d}",
            })
        return sorted(selected, key=lambda row: int(row.get("trial_index", 0)))

    def _trial_index(self, row: dict[str, Any]) -> int:
        value = row.get("trial_index")
        if value is not None:
            return int(value)
        pair_key = str(row.get("pair_key", ""))
        parts = pair_key.split("::")
        for part in reversed(parts):
            if part.isdigit():
                return int(part)
        trial_id = str(row.get("trial_id", ""))
        parts = trial_id.split("::")
        for part in reversed(parts):
            if part.isdigit():
                return int(part)
        raise BoundaryServiceError("BOUNDARY_ARTIFACT_INCOMPLETE", "M5 trial row has no deterministic trial index.")

    def _select_m7_rows(self, rows: list[dict[str, Any]], topology_id: str, model_id: str, regime: str, rule_source_id: str, interface: str, trial_type: str) -> list[dict[str, Any]]:
        return [row for row in rows if str(row.get("topology_id")) == topology_id and str(row.get("model_id")) == model_id and str(row.get("ground_truth_regime", "")).upper() == regime and str(row.get("rule_source_id")) == rule_source_id and str(row.get("decision_interface")) == interface and str(row.get("trial_type")) == trial_type]

    def _select_metric(self, rows: list[dict[str, Any]], rule_source_id: str, topology_id: str, model_id: str, regime: str, interface: str, framework: str, trial_type: str) -> dict[str, Any] | None:
        for row in rows:
            if str(row.get("rule_source_id")) == rule_source_id and str(row.get("topology_id")) == topology_id and str(row.get("model_id")) == model_id and str(row.get("ground_truth_regime", "")).upper() == regime and str(row.get("decision_interface")) == interface and str(row.get("framework_condition")) == framework and str(row.get("trial_type")) == trial_type:
                return row
        return None

    def _load_topology(self, run_root: Path, rule_source_id: str, topology_id: str) -> dict[str, Any]:
        path = run_root / "M3" / rule_source_id / topology_id / "topology_spec.json"
        if not path.is_file():
            raise BoundaryServiceError("BOUNDARY_ARTIFACT_INCOMPLETE", f"Topology artifact is missing: {path}")
        return self._read_json(path)

    def _load_scenarios(self, run_root: Path, topology_id: str, regime: str) -> list[dict[str, Any]]:
        path = run_root / "M4" / "scenario_gt.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in rows if str(row.get("topology_id")) == topology_id and str(row.get("ground_truth_regime", "")).upper() == regime]

    def _load_parquet(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            raise BoundaryServiceError("BOUNDARY_ARTIFACT_INCOMPLETE", f"Artifact is missing: {path}")
        return [dict(row) for row in pq.read_table(path).to_pylist()]

    def _population(self, value: Any) -> dict[str, int]:
        if isinstance(value, str):
            value = json.loads(value)
        return {str(key): int(float(item)) for key, item in dict(value or {}).items()}

    def _population_float(self, value: Any) -> dict[str, float]:
        if isinstance(value, str):
            value = json.loads(value)
        return {str(key): float(item) for key, item in dict(value or {}).items()}

    def _scaled_observation(self, truth: dict[str, int], residuals: dict[str, float], alpha: float) -> dict[str, int]:
        return {node_id: max(0, self.use_case._round_half_up(float(value) + float(residuals.get(node_id, 0.0)) * alpha)) for node_id, value in sorted(truth.items())}

    def _require_published_run(self, run_root: Path) -> None:
        if not run_root.is_dir():
            raise BoundaryServiceError("RUN_NOT_FOUND", f"Published Run not found: {run_root.name}", 404)
        if not (run_root / "M4" / "scenario_gt.jsonl").is_file() or not (run_root / "M5" / "observation_trials.parquet").is_file() or not (run_root / "M7" / "decision_validation_trials.parquet").is_file():
            raise BoundaryServiceError("BOUNDARY_ARTIFACT_INCOMPLETE", "Run does not contain the M4, M5 and M7 artifacts required for Boundary analysis.")

    def _require_succeeded_run(self, run_root: Path) -> None:
        summary_path = run_root / "M9" / "run_summary.json"
        if summary_path.is_file() and str(self._read_json(summary_path).get("status", "")).upper() == "SUCCEEDED":
            return
        progress_path = run_root / "run_progress.json"
        if progress_path.is_file():
            status = str(self._read_json(progress_path).get("status", "")).upper()
            if status and status != "SUCCEEDED":
                raise BoundaryServiceError("BOUNDARY_SOURCE_RUN_NOT_SUCCEEDED", f"Boundary analysis requires a SUCCEEDED source Run; current status is {status}.", 409)

    def _read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _object_checksum(self, payload: Any) -> str:
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _round(self, value: float | None) -> float | None:
        return None if value is None else round(float(value), 6)

    def _number(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _display_number(self, value: Any) -> str:
        return "unavailable" if value is None else f"{float(value):.6f}"

    def _display_percent(self, value: Any) -> str:
        return "unavailable" if value is None else f"{float(value):.1%}"
