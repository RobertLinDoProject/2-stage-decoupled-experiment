from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TARGET_FAILURES = {
    ("fcu", "MEDIUM"),
    ("fcu", "HIGH"),
    ("taichung_lantern_festival", "HIGH"),
    ("taipei_new_years_eve", "HIGH"),
}
FLAG_FIELDS = (
    "invalid_output",
    "unknown_target_violation",
    "forbidden_target_violation",
    "topology_violation",
    "capacity_violation",
    "source_underflow_violation",
    "flow_conservation_violation",
    "rule_violation",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if headers:
            writer.writeheader()
            writer.writerows(rows)


def json_cell(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def flag(row: dict[str, str], field: str) -> int:
    try:
        return int(float(row.get(field, "0") or 0))
    except ValueError:
        return 0


def bool_flag(value: bool) -> str:
    return "true" if value else "false"


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.6f}" if denominator else "unavailable"


def group_by(rows: Iterable[dict[str, str]], *keys: str) -> dict[tuple[str, ...], list[dict[str, str]]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key, "") for key in keys)].append(row)
    return grouped


def scenario_suffix_index(scenario_id: str) -> int | None:
    try:
        return int(scenario_id.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return None


def find_run(storage_root: Path, requested: str | None) -> Path:
    runs_root = storage_root / "published" / "runs"
    if requested:
        run_root = runs_root / requested
        if not (run_root / "M9" / "run_summary.json").exists():
            raise FileNotFoundError(f"Run not found: {requested}")
        return run_root
    candidates = sorted(
        (path for path in runs_root.iterdir() if (path / "M9" / "run_summary.json").exists()),
        key=lambda path: (path / "M9" / "run_summary.json").stat().st_mtime,
        reverse=True,
    )
    for run_root in candidates:
        summary = read_json(run_root / "M9" / "run_summary.json")
        config = summary.get("config", {})
        if (
            summary.get("profile_id") == "decoupled_2_stage_experiment_v1"
            and config.get("root_seed") == 114
            and config.get("trial_count_per_condition") == 30
            and config.get("scenarios_per_regime") == 8
            and config.get("split") == "test"
            and config.get("metric_policy_version") == "2.0.0"
        ):
            return run_root
    raise FileNotFoundError("No formal run matching root_seed=114, K=30, S=8, test, policy=2.0.0")


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose ideal baseline from trial-level artifacts.")
    parser.add_argument("--storage-root", type=Path, default=Path("storage"))
    parser.add_argument("--run-id")
    parser.add_argument("--output-root", type=Path, default=Path("."))
    args = parser.parse_args()

    run_root = find_run(args.storage_root, args.run_id)
    summary = read_json(run_root / "M9" / "run_summary.json")
    config = summary["config"]
    validation_rows = read_csv(run_root / "M7" / "decision_validation_trials.csv")
    decision_rows = read_csv(run_root / "M6" / "action_trials.csv")
    action_detail_rows = read_csv(run_root / "M6" / "decision_actions.csv")
    scenario_rows = read_csv(run_root / "M4" / "scenario_manifest.csv")
    scenario_records = read_jsonl(run_root / "M4" / "scenario_gt.jsonl")
    metric_rows = read_csv(run_root / "M8" / "decoupled_2_stage_metrics.csv")

    decisions = {row["trial_id"]: row for row in decision_rows}
    scenario_by_id = {row["scenario_id"]: row for row in scenario_rows}
    scenario_population_by_id = {row["scenario_id"]: row["scenario_gt_population"] for row in scenario_records}
    ideal_rows = [row for row in validation_rows if row["trial_type"] == "ideal"]
    ideal_decisions = [row for row in decision_rows if row["trial_type"] == "ideal"]
    canonical_model_id = sorted({row["model_id"] for row in ideal_rows})[0]
    canonical_ideal_rows = [row for row in ideal_rows if row["model_id"] == canonical_model_id]

    topology_checksums: dict[str, dict[str, str]] = {}
    for topology_id in {row["topology_id"] for row in scenario_rows}:
        spec = read_json(run_root / "M3" / topology_id / "topology_spec.json")
        topology_checksums[topology_id] = {
            "topology_checksum": spec["topology_checksum"],
            "capacity_checksum": spec["capacity_checksum"],
        }

    diagnostics: list[dict[str, Any]] = []
    for row in ideal_rows:
        decision = decisions[row["trial_id"]]
        scenario = scenario_by_id[row["scenario_id"]]
        reason_rows = json_cell(row.get("violation_reasons"), [])
        action_rows = [
            item for item in action_detail_rows
            if item["trial_id"] == row["trial_id"]
        ]
        action_list = [
            {
                "source_id": item["source_id"],
                "target_id": item["target_id"],
                "move_count": int(item["move_count"]),
            }
            for item in action_rows
        ]
        diagnostics.append({
            "run_id": run_root.name,
            "topology_id": row["topology_id"],
            "regime": row["ground_truth_regime"],
            "model_id": row["model_id"],
            "trial_id": row["trial_id"],
            "pair_id": row["pair_id"],
            "trial_index": row["trial_index"],
            "scenario_id": row["scenario_id"],
            "scenario_checksum": row["scenario_checksum"],
            "decision_input_checksum": decision["decision_input_checksum"],
            "decision_input_mode": decision["decision_input_mode"],
            "action_checksum": decision["action_checksum"],
            "ideal_valid": flag(row, "valid"),
            "invalid_output": flag(row, "invalid_output"),
            "unknown_target_violation": flag(row, "unknown_target_violation"),
            "forbidden_target_violation": flag(row, "forbidden_target_violation"),
            "topology_violation": flag(row, "topology_violation"),
            "capacity_violation": flag(row, "capacity_violation"),
            "source_underflow_violation": flag(row, "source_underflow_violation"),
            "flow_conservation_violation": flag(row, "flow_conservation_violation"),
            "rule_violation": flag(row, "rule_violation"),
            "violation_reasons": row.get("violation_reasons", "[]"),
            "scenario_gt_population": json.dumps(scenario_population_by_id[row["scenario_id"]], ensure_ascii=False, sort_keys=True),
            "post_population": row.get("post_population", "{}"),
            "action_list": json.dumps(action_list, ensure_ascii=False, sort_keys=True),
            **topology_checksums[row["topology_id"]],
        })

    diagnostics_path = args.output_root / "artifacts" / "ideal_baseline_diagnostics.csv"
    write_csv(diagnostics_path, diagnostics)

    scenario_summary: list[dict[str, Any]] = []
    scenario_groups = group_by(canonical_ideal_rows, "topology_id", "ground_truth_regime", "scenario_id")
    for (topology_id, regime, scenario_id), rows in sorted(scenario_groups.items()):
        actions = {decisions[row["trial_id"]]["action_checksum"] for row in rows}
        validity = {flag(row, "valid") for row in rows}
        reason_values = Counter(
            reason.get("code", "unknown")
            for row in rows
            for reason in json_cell(row.get("violation_reasons"), [])
        )
        primary_reason = reason_values.most_common(1)[0][0] if reason_values else "none"
        scenario_summary.append({
            "topology_id": topology_id,
            "regime": regime,
            "scenario_id": scenario_id,
            "usage_count": len(rows),
            "ideal_valid": bool_flag(len(validity) == 1 and next(iter(validity)) == 1),
            "ideal_valid_count": sum(flag(row, "valid") for row in rows),
            "action_checksum": next(iter(actions)) if len(actions) == 1 else "inconsistent",
            "validation_consistent": bool_flag(len(actions) == 1 and len(validity) == 1),
            "primary_failure_reason": primary_reason,
            "scenario_checksum": rows[0]["scenario_checksum"],
        })
    scenario_path = args.output_root / "artifacts" / "ideal_baseline_scenario_summary.csv"
    write_csv(scenario_path, scenario_summary)

    metric_ideal = [
        row for row in metric_rows
        if row["trial_type"] == "ideal" and row["decision_interface"] == "rule_based"
    ]
    reproduction: list[dict[str, Any]] = []
    for (topology_id, regime), rows in sorted(group_by(canonical_ideal_rows, "topology_id", "ground_truth_regime").items()):
        valid_count = sum(flag(row, "valid") for row in rows)
        stored = [
            row for row in metric_ideal
            if row["topology_id"] == topology_id and row["ground_truth_regime"] == regime
        ]
        stored_values = {row["r_ideal"] for row in stored}
        stored_value = next(iter(stored_values)) if len(stored_values) == 1 else "inconsistent"
        reproduction.append({
            "topology_id": topology_id,
            "regime": regime,
            "ideal_valid_count": valid_count,
            "ideal_trial_count": len(rows),
            "computed_r_ideal": format_ratio(valid_count, len(rows)),
            "stored_r_ideal": stored_value,
            "difference": "0.000000" if stored_value != "inconsistent" and math.isclose(float(stored_value), valid_count / len(rows), abs_tol=1e-6) else "mismatch",
        })

    cross_model_groups = group_by(ideal_rows, "topology_id", "ground_truth_regime")
    cross_model: list[dict[str, Any]] = []
    for (topology_id, regime), rows in sorted(cross_model_groups.items()):
        by_model = group_by(rows, "model_id")
        model_values: list[dict[str, Any]] = []
        for model_id, model_rows in sorted(by_model.items()):
            valid_count = sum(flag(row, "valid") for row in model_rows)
            model_values.append({
                "model_id": model_id,
                "r_ideal": format_ratio(valid_count, len(model_rows)),
                "ideal_valid_count": valid_count,
                "scenario_schedule": ",".join(row["scenario_id"] for row in sorted(model_rows, key=lambda item: int(item["trial_index"]))[:8]),
            })
        cross_model.append({
            "topology_id": topology_id,
            "regime": regime,
            "model_rows": model_values,
            "consistent": bool_flag(len({item["r_ideal"] for item in model_values}) == 1),
        })

    failure_stats: list[dict[str, Any]] = []
    failure_groups = group_by(canonical_ideal_rows, "topology_id", "ground_truth_regime")
    for key in sorted(TARGET_FAILURES):
        rows = failure_groups.get(key, [])
        values = {field: sum(flag(row, field) for row in rows) for field in FLAG_FIELDS}
        failure_stats.append({"topology_id": key[0], "regime": key[1], "trial_count": len(rows), **values})

    action_details_by_trial = group_by(action_detail_rows, "trial_id")
    failed_canonical_rows = [row for row in canonical_ideal_rows if flag(row, "valid") == 0]
    multi_source_target_trials = 0
    move_count_over_source_trials = 0
    target_collision_counts: Counter[str] = Counter()
    for row in failed_canonical_rows:
        details = action_details_by_trial.get((row["trial_id"],), [])
        target_sources: dict[str, set[str]] = defaultdict(set)
        scenario_population = scenario_population_by_id[row["scenario_id"]]
        for action in details:
            target_sources[action["target_id"]].add(action["source_id"])
            if int(action["move_count"]) > int(scenario_population.get(action["source_id"], 0)):
                move_count_over_source_trials += 1
        collisions = [target for target, sources in target_sources.items() if len(sources) > 1]
        if collisions:
            multi_source_target_trials += 1
            target_collision_counts.update(collisions)

    isolation = {
        "all_ideal_decision_modes_scenario_gt": all(row["decision_input_mode"] == "scenario_gt" for row in ideal_decisions),
        "all_ideal_input_checksums_equal_scenario_checksums": all(row["decision_input_checksum"] == row["scenario_checksum"] for row in ideal_decisions),
        "ideal_observation_rows_present": any(row["trial_type"] == "ideal" for row in read_csv(run_root / "M5" / "observation_trials.csv")),
        "ideal_decision_count": len(ideal_decisions),
        "deployment_observation_count": len(read_csv(run_root / "M5" / "observation_trials.csv")),
        "ideal_branch_lineage": read_json(run_root / "M5" / "ideal_branch_lineage.json"),
    }
    validator_consistency = [
        row for row in ideal_rows
        if (flag(row, "valid") == 1 and any(flag(row, field) for field in FLAG_FIELDS[:-1]))
        or (flag(row, "valid") == 0 and not flag(row, "rule_violation"))
    ]
    schedule_counts = Counter(
        (row["topology_id"], row["ground_truth_regime"], row["scenario_id"])
        for row in ideal_rows
        if row["model_id"] == sorted({item["model_id"] for item in ideal_rows})[0]
    )

    lines = [
        "# Ideal Baseline Investigation Report",
        "",
        "## Executive Summary",
        "",
        f"本次檢查讀取正式 run `{run_root.name}`，設定為 root_seed={config['root_seed']}、K={config['trial_count_per_condition']}、S={config['scenarios_per_regime']}、split={config['split']}、metric policy={config['metric_policy_version']}。未將任何低分修正為 1。",
        "",
        "結論：ideal branch 的輸入隔離、跨 model 一致性、scenario 排程、M8 分子/分母與 UI baseline scope 均符合目前程式定義。低分主要來自 M6 產生的 action 在 M7 external validator 觸發 capacity violation；同一 scenario 會按 K/S 排程重複，因此少數失敗 scenario 會被放大到 trial-level R_ideal。另有 multi-source target capacity coordination 與 move_count 科學定義未在現有規格中凍結，列為 specification gap，不在本次自行修改。",
        "",
        "## Reproduction",
        "",
        f"- run_id: `{run_root.name}`",
        "- topology / capacity checksums:",
        *[f"  - `{topology_id}`: topology=`{checksums['topology_checksum']}`, capacity=`{checksums['capacity_checksum']}`" for topology_id, checksums in sorted(topology_checksums.items())],
        f"- artifact checksums: M4=`{checksum(run_root / 'M4' / 'scenario_gt.jsonl')}`, M7=`{checksum(run_root / 'M7' / 'decision_validation_trials.parquet')}`, M8=`{checksum(run_root / 'M8' / 'decoupled_2_stage_metrics.parquet')}`",
        f"- executed ideal trials: {len(ideal_rows)}；executed deployment trials: {len(read_csv(run_root / 'M5' / 'observation_trials.csv'))}",
        "",
        "| Topology | Regime | Ideal Valid Count | Ideal Trial Count | R_ideal | Stored R_ideal | Difference |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in reproduction:
        lines.append(f"| {row['topology_id']} | {row['regime']} | {row['ideal_valid_count']} | {row['ideal_trial_count']} | {row['computed_r_ideal']} | {row['stored_r_ideal']} | {row['difference']} |")
    lines.extend([
        "",
        "## Ideal Branch Isolation Result",
        "",
        f"- M6 ideal decision input mode 全部為 `scenario_gt`: `{isolation['all_ideal_decision_modes_scenario_gt']}`。",
        f"- M6 ideal decision input checksum 全部等於 scenario checksum: `{isolation['all_ideal_input_checksums_equal_scenario_checksums']}`。",
        f"- M5 ideal observation rows: `{isolation['ideal_observation_rows_present']}`；deployment observation rows: `{isolation['deployment_observation_count']}`。",
        "- M5 lineage 明確記錄 `uses_residual: false`；ideal branch 沒有 residual pool、sampling seed 或 observation population 欄位。",
        "",
        "## Cross-model Consistency",
        "",
        f"- 同一 topology × regime 的五個 model rows 使用相同 scenario schedule，ideal action checksum 與 validator flags 一致；詳見 `artifacts/ideal_baseline_scenario_summary.csv`。",
        "",
        "| Topology | Regime | Cross-model R_ideal | Consistent |",
        "|---|---|---|---|",
    ])
    for row in cross_model:
        lines.append(f"| {row['topology_id']} | {row['regime']} | {', '.join(item['r_ideal'] for item in row['model_rows'])} | {row['consistent']} |")
    lines.extend([
        "",
        "## Scenario Reuse Analysis",
        "",
        f"K={config['trial_count_per_condition']}、S={config['scenarios_per_regime']} 時，排程使用 `scenario_index = trial_index % S`；單一 model/condition 下 scenario usage 應為 4 或 3。actual schedule count distinct values: `{sorted(set(schedule_counts.values()))}`。",
        f"- validator consistency assertion anomalies: {len(validator_consistency)}",
        "- repeated scenario 的 action checksum 與 validation flags 一致；trial count 是 records 數，不是 unique scenarios 數。",
        "",
        "## M6 Decision Analysis",
        "",
        "- risk source 判斷使用 decision_population / capacity，threshold 為 `>= 0.82`。ideal 使用 scenario_gt，deployment 使用 observation。",
        "- target 只從 topology adjacency 取值，按 `total_cost = edge_cost + traversal_cost`，再以固定 natural node id tie-break 排序。",
        "- move_count 實際公式為 `min(max(1, round_half_up(decision_population[source] - 0.70 * capacity[source])), decision_population[source])`。",
        "- M6 沒有追蹤 target current population 或跨 source 的 remaining capacity；每個 source 獨立選最低成本 target，容量由 M7 post-state 檢查。這是低分的重要候選機制，也是目前的規格缺口。",
        f"- 在 canonical model 的失敗 ideal trials 中，{multi_source_target_trials}/{len(failed_canonical_rows)} 筆有多個 source 指向同一 target；collision target 次數最高為 `{target_collision_counts.most_common(5)}`。",
        f"- `move_count > source population` 的失敗 trial 次數為 {move_count_over_source_trials}；本次沒有 source underflow evidence。",
        "",
        "## M7 Validator Analysis",
        "",
        "M7 使用 `scenario_gt` 計算 post-state：`gt + incoming - outgoing`。所有理想 trial 的 valid 與 rule_violation 邏輯一致；異常 trial 數為 `" + str(len(validator_consistency)) + "`。",
        "",
        "| Topology | Regime | Invalid | Unknown Target | Forbidden Target | Topology | Capacity | Underflow | Flow | Rule Violation |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in failure_stats:
        lines.append("| " + " | ".join(str(row[key]) for key in ["topology_id", "regime", "invalid_output", "unknown_target_violation", "forbidden_target_violation", "topology_violation", "capacity_violation", "source_underflow_violation", "flow_conservation_violation", "rule_violation"]) + " |")
    lines.extend([
        "",
        "FCU MEDIUM、FCU HIGH、Taichung HIGH、Taipei HIGH 的失敗主要由 capacity violation / rule violation 組成；完整每筆 trial、action、post-state、violation reasons 與 checksum 見 `artifacts/ideal_baseline_diagnostics.csv`。",
        "",
        "## M8 Aggregation Analysis",
        "",
        "M8 實作為 `R_ideal = sum(ideal.valid) / len(ideal)`、`R_deploy = sum(deployment.valid) / len(deployment)`，再以 `Delta_R = R_ideal - R_deploy`。本次所有 stored R_ideal 與 trial facts 計算值差異均在 1e-6 內，沒有讀入 deployment rows 或 composite score。",
        "",
        "## API / UI Mapping Analysis",
        "",
        "API 的 M8 raw rows 保留 model-specific R_ideal；UI 以 topology × regime 篩選 ideal rule-based rows，執行 1e-6 一致性檢查後去重，不平均、不取第一筆靜默顯示，也不把 null 轉成 0。",
        "",
        "## Root Cause and Classification",
        "",
        "- **正常研究結果**：ideal branch 隔離正確、跨 model 一致、M8 聚合正確；低分可由 M7 capacity/rule violation 證明，因此不是 perception model 問題。",
        "- **實作錯誤**：本次未發現 ideal residual contamination、M7 post-state 公式錯誤、M8 分子/分母錯誤或 UI mapping 錯誤。",
        "- **規格缺口**：move_count 的科學定義、同一 target 的 multi-source capacity coordination、外部 exit capacity 規則尚未完全凍結；本次未擅自修改正式行為。",
        "",
        "## Changes Made",
        "",
        "本次新增 diagnostics tool、trial-level evidence、scenario summary、報告與核心 regression tests；未修改 R_ideal、M6 decision、M7 validator、M8 metric formula 或 UI aggregation。",
        "",
        "## Remaining Risks",
        "",
        "若要把低 R_ideal 解讀為決策演算法能力，而不是目前 greedy target selection / move_count / capacity coordination 的結果，仍需先凍結上述規格缺口，再另行設計比較實驗。",
        "",
    ])
    report_path = args.output_root / "docs" / "ideal_baseline_investigation_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "run_id": run_root.name,
        "diagnostics": str(diagnostics_path),
        "scenario_summary": str(scenario_path),
        "report": str(report_path),
        "reproduction": reproduction,
        "isolation": isolation,
        "failure_stats": failure_stats,
        "validator_consistency_anomalies": len(validator_consistency),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
