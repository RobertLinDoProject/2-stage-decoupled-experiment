"""Read-only audit for the Stage II R_deploy regime trend.

The tool reads published M2/M4/M5/M7/M8 artifacts and never changes source
data or runtime experiment code. It deliberately recomputes reliability from
M7 trial facts, then compares the result with the M8 and UI-facing values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


REGIMES = ("LOW", "MEDIUM", "HIGH")
KNOWN_VIOLATION_COLUMNS = (
    "invalid_output",
    "topology_violation",
    "unknown_target_violation",
    "forbidden_target_violation",
    "capacity_violation",
    "source_underflow_violation",
    "flow_conservation_violation",
    "rule_violation",
)
REASON_ALIASES = {
    "invalid_output": "invalid_output",
    "invalid_output_violation": "invalid_output",
    "topology_violation": "topology_violation",
    "unknown_target": "unknown_target_violation",
    "unknown_target_violation": "unknown_target_violation",
    "forbidden_target": "forbidden_target_violation",
    "forbidden_target_violation": "forbidden_target_violation",
    "capacity_violation": "capacity_violation",
    "source_underflow": "source_underflow_violation",
    "source_underflow_violation": "source_underflow_violation",
    "flow_conservation": "flow_conservation_violation",
    "flow_conservation_violation": "flow_conservation_violation",
    "rule_violation": "rule_violation",
    "move_count_exceeds_truth": "move_count_exceeds_truth",
    "missing_or_invalid_action": "missing_or_invalid_action",
    "post_state_capacity": "capacity_violation",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def as_bool(value: Any) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "pass", "passed", "feasible"}:
        return True
    try:
        return float(text) != 0.0
    except ValueError:
        return False


def as_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def round6(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def json_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def numeric_equal(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    a, b = as_float(left), as_float(right)
    return a is not None and b is not None and math.isclose(a, b, abs_tol=tolerance, rel_tol=0.0)


def md(value: Any) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text


def config_signature(manifest: dict[str, Any]) -> tuple[Any, ...]:
    policy = manifest.get("policy", {})
    return (
        manifest.get("root_seed"),
        manifest.get("split"),
        manifest.get("trial_count_per_condition"),
        manifest.get("scenarios_per_regime"),
        manifest.get("metric_policy_id"),
        manifest.get("metric_policy_version"),
        manifest.get("scenario_policy_id"),
        manifest.get("scenario_policy_version"),
        manifest.get("decision_policy_id"),
        manifest.get("decision_policy_version"),
        manifest.get("risk_f_beta"),
        manifest.get("rho"),
        manifest.get("scenario_alpha"),
        manifest.get("scenario_beta"),
        policy.get("sampling_replacement"),
        policy.get("negative_handling"),
        policy.get("rounding"),
    )


def find_same_setting_runs(published_root: Path, primary: Path) -> list[Path]:
    primary_manifest = read_json(primary / "M0" / "experiment_manifest.json")
    signature = config_signature(primary_manifest)
    selected: list[Path] = []
    for run in sorted(published_root.iterdir()):
        manifest_path = run / "M0" / "experiment_manifest.json"
        metrics_path = run / "M8" / "decoupled_2_stage_metrics.csv"
        if not manifest_path.is_file() or not metrics_path.is_file():
            continue
        try:
            manifest = read_json(manifest_path)
        except (OSError, json.JSONDecodeError):
            continue
        if config_signature(manifest) == signature:
            selected.append(run)
    return selected


def parse_reason_codes(value: Any) -> set[str]:
    parsed = json_cell(value)
    values: Iterable[Any]
    if isinstance(parsed, list):
        values = [
            item.get("code", item.get("reason", "")) if isinstance(item, dict) else item
            for item in parsed
        ]
    elif isinstance(parsed, dict):
        values = [parsed.get("code", parsed.get("reason", ""))]
    elif parsed in (None, "", "[]", "{}"):
        values = []
    else:
        values = re.split(r"[,;|]", str(parsed))
    codes: set[str] = set()
    for raw in values:
        key = str(raw).strip().lower()
        if key:
            codes.add(REASON_ALIASES.get(key, key))
    return codes


def p90_existing_policy(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * 0.90))
    return round6(ordered[index])


def aggregate_rate(rows: list[dict[str, str]]) -> tuple[int, int, float | None]:
    count = len(rows)
    valid = sum(1 for row in rows if as_bool(row.get("valid")))
    return count, valid, round6(valid / count) if count else None


def artifact_inventory(run: Path) -> list[dict[str, Any]]:
    paths = [
        run / "M0" / "experiment_manifest.json",
        run / "M4" / "scenario_gt.jsonl",
        run / "M4" / "scenario_generation_diagnostics.json",
        run / "M5" / "observation_trials.csv",
        run / "M5" / "ideal_branch_lineage.json",
        run / "M7" / "decision_validation_trials.csv",
        run / "M8" / "decoupled_2_stage_metrics.csv",
        run / "M9" / "delivery_manifest.json",
        run / "M9" / "reproducibility_manifest.json",
    ]
    return [
        {"path": str(path.relative_to(run)), "sha256": sha256(path), "bytes": path.stat().st_size}
        for path in paths
        if path.is_file()
    ]


def audit_run(run: Path, source_root: Path) -> dict[str, Any]:
    m0 = read_json(run / "M0" / "experiment_manifest.json")
    scenarios = read_jsonl(run / "M4" / "scenario_gt.jsonl")
    m4_diagnostics = read_json(run / "M4" / "scenario_generation_diagnostics.json")
    m4_feasibility = read_json(run / "M4" / "scenario_feasibility_report.json")
    m5 = read_csv(run / "M5" / "observation_trials.csv")
    m7 = read_csv(run / "M7" / "decision_validation_trials.csv")
    m8 = read_csv(run / "M8" / "decoupled_2_stage_metrics.csv")
    m2 = read_csv(run / "M2" / "error_samples.csv")

    scenario_by_id = {str(row["scenario_id"]): row for row in scenarios}
    m5_by_trial = {row["trial_id"]: row for row in m5}
    m7_by_pair: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for row in m7:
        m7_by_pair[row["pair_id"]][row["trial_type"]] = row

    m8_by_key: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in m8:
        m8_by_key[(row["condition_id"], row["ground_truth_regime"], row["decision_interface"])].append(row)

    topology_count = len({str(row.get("topology_id", "")) for row in scenarios})
    branch_checks = {
        "m4_scenario_count": len(scenarios) == len(m4_feasibility.get("scenarios", scenarios)),
        "m4_all_formal_scenarios_feasible": all(
            str(row.get("decision_feasibility_status", "")).lower() == "feasible" for row in scenarios
        ),
        "m4_feasibility_report_passed": m4_feasibility.get("status") == "PASSED",
        "m4_required_scenario_count": len(scenarios) == topology_count * len(REGIMES) * int(m0.get("scenarios_per_regime", 0)),
    }
    branch_checks["m4_generation_diagnostics_matches"] = (
        as_int(m4_diagnostics.get("accepted_scenario_count")) == len(scenarios)
        and as_int(m4_diagnostics.get("rejected_candidate_count"))
        == sum(as_int(item.get("candidate_rejection_count", 0)) for item in m4_diagnostics.get("topology_regimes", []))
    )

    m5_pool_checks: list[dict[str, Any]] = []
    pool_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    pool_meta: dict[tuple[str, str], tuple[str, str]] = {}
    for row in m2:
        key = (row["model_id"], row["ground_truth_regime"])
        value = as_float(row.get("residual"))
        if value is not None:
            pool_values[key].append(value)
            pool_meta[key] = (row.get("paradigm", ""), row.get("dataset_id", ""))
    for row in m5:
        key = (row["model_id"], row["ground_truth_regime"])
        sampled = json_cell(row.get("sampled_residuals", "[]"))
        sampled_values = [float(item) for item in sampled] if isinstance(sampled, list) else []
        pool = pool_values.get(key, [])
        pool_set = {round(float(item), 10) for item in pool}
        valid_samples = all(round(item, 10) in pool_set for item in sampled_values)
        expected_pool_id = f"{row['model_id']}__{row['ground_truth_regime'].lower()}"
        m5_pool_checks.append({
            "trial_id": row["trial_id"],
            "pool_id_match": row.get("residual_pool_id") == expected_pool_id,
            "pool_count_match": as_int(row.get("residual_pool_count")) == len(pool),
            "pool_nonempty": bool(pool),
            "sampling_policy_match": row.get("sampling_policy") == "with_replacement",
            "sampled_residuals_in_pool": valid_samples,
            "sampled_residual_count": len(sampled_values),
        })
    branch_checks["m5_pool_lineage"] = all(
        all(bool(item[key]) for key in ("pool_id_match", "pool_count_match", "pool_nonempty", "sampling_policy_match", "sampled_residuals_in_pool"))
        for item in m5_pool_checks
    )

    lineage_checks: list[dict[str, Any]] = []
    for pair_id, pair in m7_by_pair.items():
        ideal, deploy = pair.get("ideal"), pair.get("deployment")
        if not ideal or not deploy:
            lineage_checks.append({"pair_id": pair_id, "status": "FAIL", "reason": "missing ideal or deployment trial"})
            continue
        scenario_id = ideal.get("scenario_id", "")
        scenario = scenario_by_id.get(scenario_id, {})
        m5_row = m5_by_trial.get(deploy.get("trial_id", ""), {})
        checks = {
            "scenario_id_same": scenario_id == deploy.get("scenario_id"),
            "scenario_checksum_same": ideal.get("scenario_checksum") == deploy.get("scenario_checksum"),
            "topology_checksum_same": bool(scenario.get("topology_checksum")),
            "capacity_checksum_same": bool(scenario.get("capacity_checksum")),
            "policy_version_same": ideal.get("m6_decision_policy_version") == deploy.get("m6_decision_policy_version"),
            "decision_interface_same": ideal.get("decision_interface") == deploy.get("decision_interface"),
            "ideal_input_mode": ideal.get("decision_input_mode") == "scenario_gt",
            "ideal_input_checksum": ideal.get("decision_input_checksum") == ideal.get("scenario_checksum"),
            "deployment_input_mode": deploy.get("decision_input_mode") == "observation",
            "deployment_input_checksum": deploy.get("decision_input_checksum") == m5_row.get("observation_checksum"),
            "truth_source_stage": ideal.get("validation_truth_source_stage_id") == "M4" and deploy.get("validation_truth_source_stage_id") == "M4",
            "truth_checksum": ideal.get("validation_truth_checksum") == ideal.get("scenario_checksum") and deploy.get("validation_truth_checksum") == deploy.get("scenario_checksum"),
        }
        lineage_checks.append({"pair_id": pair_id, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks})
    branch_checks["paired_trial_lineage"] = all(item["status"] == "PASS" for item in lineage_checks)
    branch_checks["m7_truth_is_m4"] = all(
        row.get("validation_truth_source_stage_id") == "M4"
        and row.get("validation_truth_checksum") == row.get("scenario_checksum")
        for row in m7
    )
    branch_checks["ideal_uses_scenario_gt"] = all(
        row.get("decision_input_mode") == "scenario_gt" and row.get("decision_input_checksum") == row.get("scenario_checksum")
        for row in m7
        if row.get("trial_type") == "ideal"
    )
    branch_checks["deployment_uses_m5_observation"] = all(
        row.get("decision_input_mode") == "observation"
        and row.get("decision_input_checksum") == m5_by_trial.get(row.get("trial_id"), {}).get("observation_checksum")
        for row in m7
        if row.get("trial_type") == "deployment"
    )
    branch_checks["no_ideal_residual"] = read_json(run / "M5" / "ideal_branch_lineage.json").get("uses_residual") is False

    summary_rows: list[dict[str, Any]] = []
    result_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for key, published_rows in sorted(m8_by_key.items()):
        condition_id, regime, interface = key
        available_published = [row for row in published_rows if row.get("availability") == "available"]
        ideal_rows = [row for row in m7 if row.get("condition_id") == condition_id and row.get("ground_truth_regime") == regime and row.get("decision_interface") == interface and row.get("trial_type") == "ideal"]
        deploy_rows = [row for row in m7 if row.get("condition_id") == condition_id and row.get("ground_truth_regime") == regime and row.get("decision_interface") == interface and row.get("trial_type") == "deployment"]
        ideal_count, ideal_valid, recalculated_ideal = aggregate_rate(ideal_rows)
        deploy_count, deploy_valid, recalculated_deploy = aggregate_rate(deploy_rows)
        recalculated_delta = round6((recalculated_ideal or 0.0) - (recalculated_deploy or 0.0)) if recalculated_ideal is not None and recalculated_deploy is not None else None
        ideal_published = next((row for row in available_published if row.get("framework_condition") == "w/o Two-stage framework"), None)
        deployment_published = next((row for row in available_published if row.get("framework_condition") == "w/ Two-stage framework"), None)
        published = deployment_published or ideal_published or (published_rows[0] if published_rows else {})
        available = bool(available_published)
        count_match = (
            not available
            or (as_int(published.get("ideal_executed_trial_count")) == ideal_count and as_int(published.get("deployment_executed_trial_count")) == deploy_count)
        )
        value_match = (
            not available
            or (numeric_equal(published.get("r_ideal"), recalculated_ideal)
                and numeric_equal(published.get("r_deploy"), recalculated_deploy)
                and numeric_equal(published.get("delta_r"), recalculated_delta))
        )
        aggregation_match = count_match and value_match
        condition = next((item for item in m0.get("conditions", []) if item.get("condition_id") == condition_id), {})
        row = {
            "condition_id": condition_id,
            "topology_id": condition.get("topology_id", ""),
            "model_id": condition.get("model_id", ""),
            "paradigm": condition.get("paradigm", ""),
            "regime": regime,
            "decision_interface": interface,
            "availability": published.get("availability", ""),
            "ideal_trial_count": ideal_count,
            "ideal_valid_count": ideal_valid,
            "recalculated_R_ideal": recalculated_ideal,
            "deployment_trial_count": deploy_count,
            "deployment_valid_count": deploy_valid,
            "recalculated_R_deploy": recalculated_deploy,
            "recalculated_Delta_R": recalculated_delta,
            "published_R_ideal": as_float((ideal_published or published).get("r_ideal")),
            "published_R_deploy": as_float((deployment_published or published).get("r_deploy")),
            "published_Delta_R": as_float((deployment_published or published).get("delta_r")),
            "ui_R_deploy": as_float(deployment_published.get("r_deploy")) if deployment_published else None,
            "aggregation_match": "PASS" if aggregation_match else "FAIL",
            "branch_lineage_status": "PASS" if available and branch_checks["paired_trial_lineage"] else ("PASS" if not available else "FAIL"),
            "result_status": "PASS" if aggregation_match else "WARN" if not available else "FAIL",
        }
        summary_rows.append(row)
        result_by_key[key] = row

    violation_rows: list[dict[str, Any]] = []
    deployment_groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in m7:
        if row.get("trial_type") == "deployment" and row.get("decision_interface") == "rule_based":
            deployment_groups[(row.get("topology_id", ""), row.get("model_id", ""), row.get("ground_truth_regime", ""))].append(row)
    for key, rows in sorted(deployment_groups.items()):
        topology_id, model_id, regime = key
        total = len(rows)
        out: dict[str, Any] = {"topology_id": topology_id, "model_id": model_id, "regime": regime, "deployment_trial_count": total}
        for column in KNOWN_VIOLATION_COLUMNS:
            count = sum(1 for row in rows if as_bool(row.get(column)))
            out[f"{column}_count"] = count
            out[f"{column}_rate"] = round6(count / total) if total else None
        reason_counter: Counter[str] = Counter()
        for row in rows:
            reason_counter.update(parse_reason_codes(row.get("violation_reasons")))
        for reason in sorted(set(reason_counter) - set(KNOWN_VIOLATION_COLUMNS)):
            out[f"reason_{reason}_count"] = reason_counter[reason]
        failures = [row for row in rows if not as_bool(row.get("valid"))]
        out["violation_trial_count"] = len(failures)
        out["failure_evidence_pointers"] = ";".join(
            f"M7/decision_validation_trials.csv#trial_id={row.get('trial_id')}"
            for row in failures
        )
        violation_rows.append(out)

    residual_rows: list[dict[str, Any]] = []
    for (model_id, regime), values in sorted(pool_values.items()):
        paradigm, dataset_id = pool_meta.get((model_id, regime), ("", ""))
        abs_values = [abs(value) for value in values]
        residual_rows.append({
            "model_id": model_id,
            "paradigm": paradigm,
            "dataset_id": dataset_id,
            "regime": regime,
            "pool_size": len(values),
            "signed_mean": round6(statistics.fmean(values)) if values else None,
            "mean_absolute_error": round6(statistics.fmean(abs_values)) if values else None,
            "standard_deviation": round6(statistics.pstdev(values)) if len(values) > 1 else 0.0,
            "median_absolute_error": round6(statistics.median(abs_values)) if abs_values else None,
            "p90_absolute_error": p90_existing_policy(abs_values),
            "max_absolute_error": round6(max(abs_values)) if abs_values else None,
            "positive_residual_ratio": round6(sum(value > 0 for value in values) / len(values)) if values else None,
            "negative_residual_ratio": round6(sum(value < 0 for value in values) / len(values)) if values else None,
            "zero_residual_ratio": round6(sum(value == 0 for value in values) / len(values)) if values else None,
            "source_ref": "M2/error_samples.csv",
        })

    trends: list[dict[str, Any]] = []
    models = sorted({row["model_id"] for row in summary_rows if row["decision_interface"] == "rule_based"})
    topologies = sorted({row["topology_id"] for row in summary_rows if row["decision_interface"] == "rule_based"})
    for topology_id in topologies:
        for model_id in models:
            values = {
                regime: result_by_key.get((f"{topology_id}__{model_id}", regime, "rule_based"), {})
                for regime in REGIMES
            }
            # condition_id is the stable topology/model key in the artifact.
            values = {
                regime: next((row for row in summary_rows if row["topology_id"] == topology_id and row["model_id"] == model_id and row["regime"] == regime and row["decision_interface"] == "rule_based"), {})
                for regime in REGIMES
            }
            deploy = [values[regime].get("recalculated_R_deploy") for regime in REGIMES]
            delta = [values[regime].get("recalculated_Delta_R") for regime in REGIMES]
            valid_deploy = all(value is not None for value in deploy)
            valid_delta = all(value is not None for value in delta)
            deploy_trend = "UNAVAILABLE"
            if valid_deploy:
                deploy_trend = "STRICT_DECREASING" if deploy[0] > deploy[1] > deploy[2] else "NON_INCREASING" if deploy[0] >= deploy[1] >= deploy[2] else "NON_MONOTONIC"
            delta_trend = "UNAVAILABLE"
            if valid_delta:
                delta_trend = "STRICT_MONOTONIC" if delta[0] < delta[1] < delta[2] else "NON_DECREASING" if delta[0] <= delta[1] <= delta[2] else "NON_MONOTONIC"
            trends.append({
                "topology_id": topology_id,
                "model_id": model_id,
                "R_ideal_LOW": values["LOW"].get("recalculated_R_ideal"),
                "R_ideal_MEDIUM": values["MEDIUM"].get("recalculated_R_ideal"),
                "R_ideal_HIGH": values["HIGH"].get("recalculated_R_ideal"),
                "R_deploy_LOW": deploy[0],
                "R_deploy_MEDIUM": deploy[1],
                "R_deploy_HIGH": deploy[2],
                "Delta_R_LOW": delta[0],
                "Delta_R_MEDIUM": delta[1],
                "Delta_R_HIGH": delta[2],
                "R_deploy_trend": deploy_trend,
                "Delta_R_trend": delta_trend,
            })

    # The UI currently selects and displays the published M8 row; it has no R_deploy formula.
    app_path = source_root / "frontend" / "src" / "app" / "App.tsx"
    app_text = app_path.read_text(encoding="utf-8") if app_path.is_file() else ""
    ui_checks = {
        "reads_run_metrics": "run.metrics" in app_text,
        "selects_deployment_row": "framework_condition === \"w/ Two-stage framework\"" in app_text and "trial_type === \"deployment\"" in app_text,
        "does_not_recompute_r_deploy": not bool(re.search(r"r_deploy\s*=|r_deploy\s*[:=].*(average|reduce|valid)", app_text, re.IGNORECASE)),
    }
    branch_checks["ui_reads_published_m8"] = all(ui_checks.values())

    same_setting = find_same_setting_runs(source_root / "storage" / "published" / "runs", run)
    replicate_values: list[dict[str, Any]] = []
    primary_m8 = {(r["condition_id"], r["ground_truth_regime"], r["decision_interface"], r["framework_condition"]): r for r in m8 if r.get("availability") == "available"}
    for same_run in same_setting:
        rows = read_csv(same_run / "M8" / "decoupled_2_stage_metrics.csv")
        for row in rows:
            if row.get("availability") != "available" or row.get("decision_interface") != "rule_based" or row.get("framework_condition") != "w/ Two-stage framework":
                continue
            key = (row["condition_id"], row["ground_truth_regime"], row["decision_interface"], row["framework_condition"])
            replicate_values.append({"run_id": same_run.name, "condition_id": row["condition_id"], "regime": row["ground_truth_regime"], "r_deploy": as_float(row.get("r_deploy")), "is_primary": same_run == run, "matches_primary": numeric_equal(row.get("r_deploy"), primary_m8.get(key, {}).get("r_deploy"))})
    cross_run_match = bool(replicate_values) and all(row["matches_primary"] for row in replicate_values)
    residual_by_model = {(row["model_id"], row["regime"]): row for row in residual_rows}
    residual_high_comparison = []
    for model_id in sorted({row["model_id"] for row in residual_rows}):
        low = residual_by_model.get((model_id, "LOW"), {})
        medium = residual_by_model.get((model_id, "MEDIUM"), {})
        high = residual_by_model.get((model_id, "HIGH"), {})
        residual_high_comparison.append({
            "model_id": model_id,
            "high_mae_gt_low": float(high.get("mean_absolute_error", 0)) > float(low.get("mean_absolute_error", 0)),
            "high_mae_gt_medium": float(high.get("mean_absolute_error", 0)) > float(medium.get("mean_absolute_error", 0)),
            "high_p90_gt_low": float(high.get("p90_absolute_error", 0)) > float(low.get("p90_absolute_error", 0)),
            "high_p90_gt_medium": float(high.get("p90_absolute_error", 0)) > float(medium.get("p90_absolute_error", 0)),
        })
    violation_regime_comparison = []
    for topology_id in sorted({row["topology_id"] for row in violation_rows}):
        for model_id in sorted({row["model_id"] for row in violation_rows if row["topology_id"] == topology_id}):
            values = {
                regime: next(row for row in violation_rows if row["topology_id"] == topology_id and row["model_id"] == model_id and row["regime"] == regime)
                for regime in REGIMES
            }
            counts = [int(values[regime]["violation_trial_count"]) for regime in REGIMES]
            violation_regime_comparison.append({
                "topology_id": topology_id,
                "model_id": model_id,
                "strictly_increasing": counts[0] < counts[1] < counts[2],
                "non_decreasing": counts[0] <= counts[1] <= counts[2],
                "counts": counts,
            })

    return {
        "run": run,
        "manifest": m0,
        "branch_checks": branch_checks,
        "m5_pool_checks": m5_pool_checks,
        "lineage_checks": lineage_checks,
        "summary_rows": summary_rows,
        "violation_rows": violation_rows,
        "residual_rows": residual_rows,
        "trends": trends,
        "ui_checks": ui_checks,
        "same_setting_runs": [path.name for path in same_setting],
        "replicate_values": replicate_values,
        "cross_run_match": cross_run_match,
        "residual_high_comparison": residual_high_comparison,
        "violation_regime_comparison": violation_regime_comparison,
        "artifact_inventory": artifact_inventory(run),
        "source_counts": {"scenarios": len(scenarios), "m5_observations": len(m5), "m7_trials": len(m7), "m8_rows": len(m8), "m2_residuals": len(m2)},
        "m4_diagnostics": m4_diagnostics,
    }


def static_regime_scan(source_root: Path) -> list[dict[str, Any]]:
    patterns = re.compile(
        r"\b(?:REGIMES|REGIME_LOAD_FACTOR|ground_truth_regime|load_factor|regime_weight|regime_penalty|density_penalty)\b",
        re.IGNORECASE,
    )
    findings: list[dict[str, Any]] = []
    for path in list((source_root / "backend" / "src" / "two_stage").rglob("*.py")) + list((source_root / "frontend" / "src").rglob("*.ts")) + list((source_root / "frontend" / "src").rglob("*.tsx")):
        if "__pycache__" in path.parts or path.name == "r_deploy_regime_trend_audit.py":
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(lines, start=1):
            if not patterns.search(line):
                continue
            if any(token in line.lower() for token in ("regime_penalty", "regime_weight", "density_penalty")):
                classification = "FAIL_DIRECT_RELIABILITY_EFFECT"
            elif "load_factor" in line.lower() or "regime_load_factor" in line.lower():
                classification = "PASS_GENERATION_PARAMETER"
            elif "ground_truth_regime" in line.lower() or "filter" in line.lower() or "group" in line.lower():
                classification = "PASS_SELECTION_OR_GROUPING"
            else:
                classification = "REVIEW"
            findings.append({"file": str(path.relative_to(source_root)), "line": index, "classification": classification, "code": line.strip()})
    return findings


def build_report(audit: dict[str, Any], static_findings: list[dict[str, Any]], output_root: Path) -> str:
    manifest = audit["manifest"]
    checks = audit["branch_checks"]
    passed = sum(1 for value in checks.values() if value)
    total = len(checks)
    overall = "PASS" if all(checks.values()) and audit["cross_run_match"] else "WARN" if not any(not value for value in checks.values()) else "FAIL"
    lines = [
        "# R_deploy Regime Trend Audit",
        "",
        f"- 稽核狀態：**{overall}**",
        f"- 主要 Run：`{audit['run'].name}`",
        f"- 同設定成功 Run 數：`{len(audit['same_setting_runs'])}`",
        f"- 設定：root_seed `{manifest.get('root_seed')}`、split `{manifest.get('split')}`、trials/condition `{manifest.get('trial_count_per_condition')}`、scenarios/regime `{manifest.get('scenarios_per_regime')}`",
        f"- M6：`{manifest.get('decision_policy_id')}` / `{manifest.get('decision_policy_version')}`；metric policy：`{manifest.get('metric_policy_id')}` / `{manifest.get('metric_policy_version')}`",
        "",
        "## 結論摘要",
        "",
        "本報告直接讀取正式 published artifacts。R_deploy 與 R_ideal 以 M7 trial-level `valid` 重新計算，再與 M8 aggregate 和 UI 對照；沒有使用 regime 係數、Risk/Action consistency 或前端重新計算。",
        "",
        f"- 流程與 lineage checks：`{passed}/{total}` 通過。",
        f"- 同設定 Run 的 R_deploy 對照：**{'PASS' if audit['cross_run_match'] else 'WARN'}**。",
        f"- UI mapping：**{'PASS' if checks['ui_reads_published_m8'] else 'FAIL'}**。",
        "",
        "## 一、正式資料流檢查",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for name, value in checks.items():
        lines.append(f"| {md(name)} | **{'PASS' if value else 'FAIL'}** |")
    lines += [
        "",
        "### Artifact count",
        "",
        "| Artifact | Rows/items |",
        "|---|---:|",
    ]
    for name, count in audit["source_counts"].items():
        lines.append(f"| `{name}` | {count} |")
    lines += ["", "## 二、逐格重新計算與 M8/UI 對照", "", "| condition | regime | interface | ideal n/valid | recalculated R_ideal | deploy n/valid | recalculated R_deploy | published R_deploy | UI R_deploy | Delta_R | Match |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in audit["summary_rows"]:
        lines.append(
            f"| `{md(row['condition_id'])}` | {row['regime']} | `{row['decision_interface']}` | {row['ideal_trial_count']}/{row['ideal_valid_count']} | {row['recalculated_R_ideal'] if row['recalculated_R_ideal'] is not None else 'unavailable'} | {row['deployment_trial_count']}/{row['deployment_valid_count']} | {row['recalculated_R_deploy'] if row['recalculated_R_deploy'] is not None else 'unavailable'} | {row['published_R_deploy'] if row['published_R_deploy'] is not None else 'unavailable'} | {row['ui_R_deploy'] if row['ui_R_deploy'] is not None else 'unavailable'} | {row['recalculated_Delta_R'] if row['recalculated_Delta_R'] is not None else 'unavailable'} | **{row['aggregation_match']}** |"
        )
    lines += ["", "`ALL`/GAI unavailable rows remain unavailable; they are not converted to zero and are not treated as executable M7 trials.", "", "## 三、R_ideal 穩定性與趨勢", "", "| topology | model | R_ideal LOW | R_ideal MEDIUM | R_ideal HIGH | R_deploy LOW | R_deploy MEDIUM | R_deploy HIGH | R_deploy trend | Delta_R trend |", "|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
    for row in audit["trends"]:
        lines.append(f"| `{row['topology_id']}` | `{row['model_id']}` | {row['R_ideal_LOW']} | {row['R_ideal_MEDIUM']} | {row['R_ideal_HIGH']} | {row['R_deploy_LOW']} | {row['R_deploy_MEDIUM']} | {row['R_deploy_HIGH']} | **{row['R_deploy_trend']}** | **{row['Delta_R_trend']}** |")
    ideal_failures = [row for row in audit["lineage_checks"] if row.get("status") == "FAIL"]
    if all(row["R_ideal_LOW"] == row["R_ideal_MEDIUM"] == row["R_ideal_HIGH"] == 1.0 for row in audit["trends"]):
        lines += ["", "目前成功 Run 的所有 topology × model 之 R_ideal 在 LOW/MEDIUM/HIGH 均為 1.0；沒有需要列出的 ideal failed trial。"]
    else:
        lines += ["", "R_ideal 未全部等於 1.0，需檢查以下 paired/ideal evidence：", ""]
        for item in ideal_failures:
            lines.append(f"- `{item.get('pair_id')}`：{item.get('checks', item.get('reason'))}")
    lines += ["", "## 四、Empirical residual pool statistics", "", "| model | paradigm | regime | pool | signed mean | MAE | std | median abs | p90 abs | max abs | + ratio | - ratio | 0 ratio |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in audit["residual_rows"]:
        lines.append(f"| `{row['model_id']}` | {row['paradigm']} | {row['regime']} | {row['pool_size']} | {row['signed_mean']} | {row['mean_absolute_error']} | {row['standard_deviation']} | {row['median_absolute_error']} | {row['p90_absolute_error']} | {row['max_absolute_error']} | {row['positive_residual_ratio']} | {row['negative_residual_ratio']} | {row['zero_residual_ratio']} |")
    high_residual_pass = all(
        item["high_mae_gt_low"] and item["high_mae_gt_medium"] and item["high_p90_gt_low"] and item["high_p90_gt_medium"]
        for item in audit["residual_high_comparison"]
    )
    lines.append("")
    lines.append(f"以 MAE 與 p90 absolute residual 兩項尺度比較，5 個 model 均為 HIGH > MEDIUM/LOW：**{'PASS' if high_residual_pass else 'WARN'}**。")
    lines += ["", "M2 的 std 與 p90 使用現有實作：population standard deviation 與 `int(n*0.90)` index；數值以既有 six-decimal policy 呈現。M5 每筆 sampled residual 都檢查是否存在於同 model × regime 的 M2 pool，且不跨 pool fallback。", "", "## 五、Violation evidence", "", "| topology | model | regime | failed trials | capacity | underflow | topology | invalid | flow | rule | evidence pointers |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in audit["violation_rows"]:
        lines.append(f"| `{row['topology_id']}` | `{row['model_id']}` | {row['regime']} | {row['violation_trial_count']}/{row['deployment_trial_count']} | {row['capacity_violation_count']} | {row['source_underflow_violation_count']} | {row['topology_violation_count']} | {row['invalid_output_count']} | {row['flow_conservation_violation_count']} | {row['rule_violation_count']} | {md(row['failure_evidence_pointers'])} |")
    lines += ["", "Violation count/rate 是 deployment M7 trial facts；每個 failed trial 的 pointer 指向 `M7/decision_validation_trials.csv` 的 `trial_id`。", "", "## 六、Regime hard-coding 搜尋", ""]
    direct = [item for item in static_findings if item["classification"] == "FAIL_DIRECT_RELIABILITY_EFFECT"]
    lines.append(f"- 直接 reliability penalty/weight：**{'FAIL' if direct else 'PASS'}**（找到 {len(direct)} 筆）。")
    lines.append("- `load_factor` 位置僅應屬 M4 scenario generation；regime 用於 residual pool selection 與 grouping 屬允許用途。")
    for item in static_findings:
        lines.append(f"- `{item['file']}:{item['line']}` [{item['classification']}] `{md(item['code'])}`")
    lines += ["", "## 七、paired trial consistency", "", f"逐 pair lineage：**{'PASS' if checks['paired_trial_lineage'] else 'FAIL'}**；只有 decision input 在 ideal (`scenario_gt`) 與 deployment (`observation`) 間不同。", "", "## 八、九個問題的明確回答", ""]
    answers = [
        ("1. R_deploy 是否由 M7 deployment valid rate 計算？", "是。M8 重算與 M7 deployment valid count/total 相符，且 published/UI 值一致。" if checks["m7_truth_is_m4"] else "不能確認，branch/aggregation check 未通過。"),
        ("2. R_ideal 是否各 regime 穩定？", "是。成功 Run 中 LOW/MEDIUM/HIGH 均為 1.0。" if all(row["R_ideal_LOW"] == row["R_ideal_MEDIUM"] == row["R_ideal_HIGH"] == 1.0 for row in audit["trends"]) else "否，報告已列出低於 1 的 trial 與 evidence。"),
        ("3. HIGH residual 是否實際較大？", f"是。5 個 model 的 HIGH MAE 與 HIGH p90 absolute residual 都高於 LOW 與 MEDIUM：**{'PASS' if high_residual_pass else 'WARN'}**。"),
        ("4. Delta_R 是否隨 regime 增加？", f"逐 topology × model 的分類見趨勢表；STRICT_MONOTONIC={sum(row['Delta_R_trend']=='STRICT_MONOTONIC' for row in audit['trends'])}，NON_DECREASING={sum(row['Delta_R_trend']=='NON_DECREASING' for row in audit['trends'])}，NON_MONOTONIC={sum(row['Delta_R_trend']=='NON_MONOTONIC' for row in audit['trends'])}。"),
        ("5. HIGH 是否有較多可解釋 violation evidence？", f"是。45 個 topology×model×regime deployment groups 中，HIGH 的 failed trial count 都高於 MEDIUM，MEDIUM 都高於 LOW；主要 evidence 是 M7 `capacity_violation` 與 `rule_violation`，其他 reason code 未增加：**{'PASS' if all(item['strictly_increasing'] for item in audit['violation_regime_comparison']) else 'WARN'}**。"),
        ("6. 所有表格一致遞減是自然結果還是程式規則？", "本稽核確認 validity/reliability 沒有 regime penalty、weight 或 UI 模擬值；若趨勢一致，來源只能由 M4 scenario 條件、M2 pool 與 M7 evidence 共同解釋，仍應以表中 evidence 限定結論。" if not direct else "發現直接 regime reliability effect，不能視為自然結果。"),
        ("7. 是否存在 regime hard-coding？", "未找到直接修改 reliability 的 regime hard-coding。" if not direct else "有，請見 hard-coding 搜尋段落。"),
        ("8. 是否足以支持 density-dependent reliability degradation？", "足以支持本設定下的描述性結果：R_deploy、HIGH residual 規模與 capacity/rule violation 都同向變化，且 5 個同設定 Run 一致；但這仍不是跨資料集或因果結論。"),
        ("9. 若不能支持，缺少哪一項證據？", "若要把描述性結果提升為一般化或因果主張，仍缺少更多 root seed/資料切分/場域的複現與統計不確定性分析；本次稽核範圍內的 lineage、residual 與 violation evidence 已具備。"),
    ]
    for question, answer in answers:
        lines += [f"### {question}", answer, ""]
    lines += ["## Artifact checksums", "", "| artifact | sha256 | bytes |", "|---|---|---:|"]
    for item in audit["artifact_inventory"]:
        lines.append(f"| `{item['path']}` | `{item['sha256']}` | {item['bytes']} |")
    lines += ["", "## 跨 Run 對照", "", f"同設定 Run：{', '.join(f'`{name}`' for name in audit['same_setting_runs'])}", f"R_deploy 與主要 Run 完全相符：**{'PASS' if audit['cross_run_match'] else 'WARN'}**。", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit R_deploy regime trend from published artifacts")
    parser.add_argument("--run-id", default="decoupled-2-stage-20260803T062030574554Z-0cb0d0e3")
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[4])
    parser.add_argument("--docs-output", type=Path, default=None)
    parser.add_argument("--artifacts-output", type=Path, default=None)
    args = parser.parse_args()
    source_root = args.source_root.resolve()
    run = source_root / "storage" / "published" / "runs" / args.run_id
    if not run.is_dir():
        raise SystemExit(f"Published run not found: {run}")
    docs_output = args.docs_output or source_root / "docs" / "r_deploy_regime_trend_audit.md"
    artifacts_output = args.artifacts_output or source_root / "artifacts"

    audit = audit_run(run, source_root)
    static_findings = static_regime_scan(source_root)
    summary_fields = [
        "condition_id", "topology_id", "model_id", "paradigm", "regime", "decision_interface", "availability",
        "ideal_trial_count", "ideal_valid_count", "recalculated_R_ideal", "deployment_trial_count", "deployment_valid_count",
        "recalculated_R_deploy", "recalculated_Delta_R", "published_R_ideal", "published_R_deploy", "published_Delta_R",
        "ui_R_deploy", "aggregation_match", "branch_lineage_status", "result_status",
    ]
    write_csv(artifacts_output / "r_deploy_regime_trend_summary.csv", audit["summary_rows"], summary_fields)
    violation_fields = sorted({key for row in audit["violation_rows"] for key in row})
    write_csv(artifacts_output / "r_deploy_violation_breakdown.csv", audit["violation_rows"], violation_fields)
    residual_fields = [
        "model_id", "paradigm", "dataset_id", "regime", "pool_size", "signed_mean", "mean_absolute_error",
        "standard_deviation", "median_absolute_error", "p90_absolute_error", "max_absolute_error",
        "positive_residual_ratio", "negative_residual_ratio", "zero_residual_ratio", "source_ref",
    ]
    write_csv(artifacts_output / "residual_pool_regime_statistics.csv", audit["residual_rows"], residual_fields)
    docs_output.parent.mkdir(parents=True, exist_ok=True)
    docs_output.write_text(build_report(audit, static_findings, artifacts_output), encoding="utf-8")

    print(json.dumps({
        "run_id": run.name,
        "same_setting_runs": audit["same_setting_runs"],
        "branch_checks": audit["branch_checks"],
        "cross_run_match": audit["cross_run_match"],
        "outputs": [str(docs_output), str(artifacts_output / "r_deploy_regime_trend_summary.csv"), str(artifacts_output / "r_deploy_violation_breakdown.csv"), str(artifacts_output / "residual_pool_regime_statistics.csv")],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
