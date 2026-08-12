from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


class DecoupledInsightReportService:
    """Build deterministic, evidence-linked M9 insight files.

    M8 remains the source of published metrics. M7 and M6 are read only for
    diagnostics and lineage; this service never replaces or rewrites metrics.
    """

    REPORT_NAME = "insight_report.md"
    SUMMARY_NAME = "insight_summary.json"
    SCHEMA_VERSION = "decoupled_2_stage_insight_v1"

    def generate(
        self,
        *,
        run_root: Path,
        profile_id: str,
        config: dict[str, Any],
        all_tables_path: Path,
        metrics_path: Path,
    ) -> dict[str, Any]:
        m8_rows = self._read_csv(metrics_path)
        m9_rows = self._read_csv(all_tables_path)
        m7_rows = self._read_csv_if_present(run_root / "M7" / "decision_validation_trials.csv")
        trace_stats = self._read_trace_stats(run_root / "M6" / "gai_decision_trace.jsonl")
        paired_rows = self._paired_rows(m8_rows)
        summary = {
            "schema_version": self.SCHEMA_VERSION,
            "run_id": run_root.name,
            "profile_id": profile_id,
            "source_artifacts": self._source_artifacts(run_root, all_tables_path, metrics_path),
            "scope": self._scope(m8_rows, config),
            "publication_checks": {
                "m8_metric_row_count": len(m8_rows),
                "m9_all_table_row_count": len(m9_rows),
                "m8_m9_row_count_match": len(m8_rows) == len(m9_rows),
                "m7_trial_row_count": len(m7_rows),
                "m7_truth_lineage": self._m7_truth_lineage(m7_rows),
            },
            "availability": self._availability(m8_rows),
            "paired_comparisons": paired_rows,
            "regime_trends": self._regime_trends(paired_rows),
            "violation_evidence": self._violation_summary(m7_rows),
            "gai_execution": trace_stats,
            "interpretation_rules": [
                "M8 canonical metrics are authoritative for R_ideal, R_deploy, and Delta_R.",
                "M7 evidence explains terminal failures; it does not replace M8 aggregation.",
                "GAI invalid_output and decision_infeasible are model outcomes; unavailable is an execution-status outcome.",
                "The report describes this Run and does not claim causality or generalization beyond its configured scope.",
            ],
        }
        report_path = run_root / "M9" / self.REPORT_NAME
        summary_path = run_root / "M9" / self.SUMMARY_NAME
        report_path.write_text(self._render_markdown(summary), encoding="utf-8")
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {
            "report_path": report_path,
            "summary_path": summary_path,
            "summary": summary,
        }

    def _paired_rows(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], dict[str, dict[str, str]]] = defaultdict(dict)
        for row in rows:
            trial_type = row.get("trial_type", "")
            if trial_type not in {"ideal", "deployment"}:
                continue
            key = (
                row.get("condition_id", ""),
                row.get("ground_truth_regime", ""),
                row.get("rule_source_id", "human_manual_v1"),
                row.get("decision_interface", ""),
            )
            grouped[key][trial_type] = row

        result: list[dict[str, Any]] = []
        for key in sorted(grouped):
            ideal = grouped[key].get("ideal")
            deployment = grouped[key].get("deployment")
            values = {
                "condition_id": key[0],
                "ground_truth_regime": key[1],
                "rule_source_id": key[2],
                "rule_source_label": (ideal or deployment or {}).get("rule_source_label", key[2]),
                "decision_interface": key[3],
                "topology_id": (ideal or deployment or {}).get("topology_id", ""),
                "topology_name": (ideal or deployment or {}).get("topology_name", ""),
                "model_id": (ideal or deployment or {}).get("model_id", ""),
                "model_name": (ideal or deployment or {}).get("model_name", ""),
                "paradigm": (ideal or deployment or {}).get("paradigm", ""),
            }
            if ideal is None or deployment is None:
                values.update({
                    "status": "incomplete",
                    "interpretation": "缺少 ideal 或 deployment 配對列，不能解讀 paired reliability。",
                    "r_ideal": None,
                    "r_deploy": None,
                    "delta_r": None,
                    "ideal_executed_trials": 0,
                    "deployment_executed_trials": 0,
                    "ideal_outcome": None,
                    "deployment_outcome": None,
                })
                result.append(values)
                continue

            r_ideal = self._float(ideal.get("r_ideal"))
            r_deploy = self._float(deployment.get("r_deploy"))
            delta_r = self._float(ideal.get("delta_r"))
            available = ideal.get("availability") == "available" and deployment.get("availability") == "available"
            consistency = self._same_metrics(
                (r_ideal, self._float(deployment.get("r_ideal"))),
                (r_deploy, self._float(ideal.get("r_deploy"))),
                (delta_r, self._float(deployment.get("delta_r"))),
            )
            status = "available" if available and consistency and all(value is not None for value in (r_ideal, r_deploy, delta_r)) else "unavailable"
            if not consistency:
                interpretation = "配對列的 M8 reliability 值不一致，停止解讀並保留 consistency error。"
            elif status != "available":
                interpretation = "沒有完整可用的 paired terminal outcome，不能解讀可靠度落差。"
            elif self._near_zero(r_ideal) and self._near_zero(r_deploy):
                interpretation = "Ideal baseline failed；Delta_R 沒有可解讀的額外 headroom。"
            elif r_ideal is not None and 0 < r_ideal < 1:
                interpretation = "Ideal baseline partial；Delta_R 是不完整 ideal baseline 下的條件性比較。"
            else:
                interpretation = "可在同一組 scenario/trial 內比較 ideal 與 deployment。"
            values.update({
                "status": status,
                "consistency": "pass" if consistency else "fail",
                "interpretation": interpretation,
                "r_ideal": r_ideal,
                "r_deploy": r_deploy,
                "delta_r": delta_r,
                "ideal_executed_trials": self._int(ideal.get("executed_trial_count")),
                "deployment_executed_trials": self._int(deployment.get("executed_trial_count")),
                "ideal_outcome": ideal.get("execution_outcome_status"),
                "deployment_outcome": deployment.get("execution_outcome_status"),
            })
            result.append(values)
        return result

    def _regime_trends(self, paired_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in paired_rows:
            grouped[(row["condition_id"], row["rule_source_id"], row["decision_interface"], row["model_id"])].append(row)
        result: list[dict[str, Any]] = []
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        for key in sorted(grouped):
            rows = sorted(grouped[key], key=lambda row: order.get(row["ground_truth_regime"], 99))
            deploy = [row["r_deploy"] for row in rows]
            delta = [row["delta_r"] for row in rows]
            result.append({
                "condition_id": key[0],
                "rule_source_id": key[1],
                "decision_interface": key[2],
                "model_id": key[3],
                "regimes": [row["ground_truth_regime"] for row in rows],
                "r_deploy": deploy,
                "delta_r": delta,
                "r_deploy_pattern": self._pattern(deploy, decreasing=True),
                "delta_r_pattern": self._pattern(delta, decreasing=False),
            })
        return result

    def _violation_summary(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        fields = {
            "invalid_output": "invalid_output",
            "m6_contract_violation": "m6_contract_violation",
            "m6_decision_infeasible": "m6_decision_infeasible",
            "topology_violation": "topology_violation",
            "capacity_violation": "capacity_violation",
            "source_underflow_violation": "source_underflow_violation",
            "flow_conservation_violation": "flow_conservation_violation",
            "rule_violation": "rule_violation",
        }
        totals = Counter()
        reasons = Counter()
        by_condition: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(lambda: {
            "trial_count": 0,
            "valid_count": 0,
            "failure_count": 0,
            "counts": Counter(),
            "reason_counts": Counter(),
        })
        for row in rows:
            key = (
                row.get("condition_id", ""),
                row.get("ground_truth_regime", ""),
                row.get("decision_interface", ""),
                row.get("trial_type", ""),
            )
            bucket = by_condition[key]
            bucket["trial_count"] += 1
            valid = self._bool(row.get("valid"))
            bucket["valid_count"] += int(valid)
            bucket["failure_count"] += int(not valid)
            for name, field in fields.items():
                if self._bool(row.get(field)):
                    totals[name] += 1
                    bucket["counts"][name] += 1
            for reason in self._json_list(row.get("violation_reasons")):
                code = str(reason.get("code") if isinstance(reason, dict) else reason)
                if code:
                    reasons[code] += 1
                    bucket["reason_counts"][code] += 1
        grouped_rows = []
        for key in sorted(by_condition):
            bucket = by_condition[key]
            grouped_rows.append({
                "condition_id": key[0],
                "ground_truth_regime": key[1],
                "decision_interface": key[2],
                "trial_type": key[3],
                "trial_count": bucket["trial_count"],
                "valid_count": bucket["valid_count"],
                "failure_count": bucket["failure_count"],
                "violation_counts": dict(sorted(bucket["counts"].items())),
                "reason_counts": dict(sorted(bucket["reason_counts"].items())),
            })
        return {
            "m7_trial_count": len(rows),
            "failure_trial_count": sum(1 for row in rows if not self._bool(row.get("valid"))),
            "violation_trial_counts": dict(sorted(totals.items())),
            "reason_counts": dict(sorted(reasons.items())),
            "by_condition": grouped_rows,
        }

    def _read_trace_stats(self, path: Path) -> dict[str, Any]:
        status_counts: Counter[str] = Counter()
        outcome_counts: Counter[str] = Counter()
        providers: set[str] = set()
        models: set[str] = set()
        latencies: list[float] = []
        external_calls = 0
        failed_requests = 0
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        status_counts["malformed_trace_row"] += 1
                        continue
                    status_counts[str(row.get("status") or "unknown")] += 1
                    outcome_counts[str(row.get("decision_output_status") or row.get("status") or "unknown")] += 1
                    if row.get("external_call_attempted") is True:
                        external_calls += 1
                    if row.get("error_code") or row.get("status") in {"failed", "invalid_output", "decision_infeasible"}:
                        failed_requests += 1
                    if row.get("provider"):
                        providers.add(str(row["provider"]))
                    if row.get("model"):
                        models.add(str(row["model"]))
                    parsed_response = row.get("parsed_response")
                    provider_metadata = parsed_response.get("provider_metadata", {}) if isinstance(parsed_response, dict) else {}
                    latency = self._float(row.get("latency_ms") or provider_metadata.get("latency_ms"))
                    if latency is not None:
                        latencies.append(latency)
        return {
            "trace_row_count": sum(status_counts.values()),
            "status_counts": dict(sorted(status_counts.items())),
            "decision_output_status_counts": dict(sorted(outcome_counts.items())),
            "external_call_count": external_calls,
            "failed_request_count": failed_requests,
            "providers": sorted(providers),
            "models": sorted(models),
            "latency_ms": {
                "sample_count": len(latencies),
                "mean": round(sum(latencies) / len(latencies), 3) if latencies else None,
                "max": max(latencies) if latencies else None,
            },
        }

    def _m7_truth_lineage(self, rows: list[dict[str, str]]) -> dict[str, Any]:
        truth_source_count = sum(row.get("validation_truth_source_stage_id") == "M4" for row in rows)
        checksum_matches = sum(
            bool(row.get("scenario_checksum"))
            and row.get("scenario_checksum") == row.get("validation_truth_checksum")
            for row in rows
        )
        return {
            "truth_source_m4_count": truth_source_count,
            "truth_checksum_match_count": checksum_matches,
            "all_truth_source_m4": truth_source_count == len(rows) if rows else False,
            "all_truth_checksum_match": checksum_matches == len(rows) if rows else False,
        }

    def _availability(self, rows: list[dict[str, str]]) -> dict[str, int]:
        return dict(sorted(Counter(row.get("availability", "unknown") for row in rows).items()))

    def _scope(self, rows: list[dict[str, str]], config: dict[str, Any]) -> dict[str, Any]:
        def unique(field: str) -> list[str]:
            return sorted({row.get(field, "") for row in rows if row.get(field, "")})

        return {
            "trial_metric_row_count": len(rows),
            "conditions": unique("condition_id"),
            "topologies": unique("topology_id"),
            "models": unique("model_id"),
            "regimes": unique("ground_truth_regime"),
            "rule_sources": unique("rule_source_id"),
            "decision_interfaces": unique("decision_interface"),
            "run_purpose": config.get("run_purpose"),
            "trial_count_per_condition": config.get("trial_count_per_condition"),
        }

    def _source_artifacts(self, run_root: Path, all_tables_path: Path, metrics_path: Path) -> dict[str, Any]:
        paths = {
            "m8_metrics": metrics_path,
            "m9_all_tables": all_tables_path,
            "m7_validation_trials": run_root / "M7" / "decision_validation_trials.csv",
            "m6_gai_trace": run_root / "M6" / "gai_decision_trace.jsonl",
        }
        return {
            name: {
                "path": str(path.relative_to(run_root)).replace("\\", "/"),
                "exists": path.is_file(),
                "checksum": self._sha256(path) if path.is_file() else None,
            }
            for name, path in paths.items()
        }

    def _render_markdown(self, summary: dict[str, Any]) -> str:
        scope = summary["scope"]
        lines = [
            "# Decoupled 2-Stage Insight Report",
            "",
            "本報告由固定程式根據本次 Run 的 M8、M7 與 M6 artifact 產生。它用白話整理結果與證據，不重新計算或改寫 M8 指標，也不自動宣稱因果關係。",
            "",
            "## 1. 本次 Run 做了什麼",
            "",
            f"- Run：`{summary['run_id']}`",
            f"- Profile：`{summary['profile_id']}`",
            f"- Run purpose：`{scope.get('run_purpose') or '未提供'}`",
            f"- 條件數：`{len(scope['conditions'])}`；M8 rows：`{scope['trial_metric_row_count']}`",
            f"- Topology：{', '.join(scope['topologies']) or '未提供'}",
            f"- Model：{', '.join(scope['models']) or '未提供'}",
            f"- Regime：{', '.join(scope['regimes']) or '未提供'}",
            "",
            "## 2. 結果是否完整",
            "",
            f"- M8 與 M9 全表 row count：`{summary['publication_checks']['m8_metric_row_count']}` / `{summary['publication_checks']['m9_all_table_row_count']}`，結果：`{'PASS' if summary['publication_checks']['m8_m9_row_count_match'] else 'WARN'}`",
            f"- M7 trial rows：`{summary['publication_checks']['m7_trial_row_count']}`",
            f"- M7 truth 來源為 M4：`{'PASS' if summary['publication_checks']['m7_truth_lineage']['all_truth_source_m4'] else 'WARN'}`",
            f"- M7 truth checksum 與 scenario checksum 一致：`{'PASS' if summary['publication_checks']['m7_truth_lineage']['all_truth_checksum_match'] else 'WARN'}`",
            f"- Availability：`{self._inline_json(summary['availability'])}`",
            "",
            "## 3. Paired reliability 怎麼看",
            "",
            "每一列固定配對同一個 topology、model、rule source、決策方式與 regime；`R_ideal` 是正確人數輸入下的 M7 通過率，`R_deploy` 是含 perception residual 的 deployment 通過率，`Delta_R` 直接引用 M8 的 `R_ideal - R_deploy`。",
            "",
            "| Rule source | 決策方式 | Topology | Model | Regime | R_ideal | R_deploy | Delta_R | 狀態 |",
            "|---|---|---|---|---|---:|---:|---:|---|",
        ]
        for row in summary["paired_comparisons"]:
            lines.append(
                f"| {row['rule_source_label']} | {row['decision_interface']} | {row['topology_name']} | {row['model_name']} | {row['ground_truth_regime']} | {self._format(row['r_ideal'])} | {self._format(row['r_deploy'])} | {self._format(row['delta_r'])} | {row['status']} |"
            )
        lines += [
            "",
            "## 4. Regime 趨勢",
            "",
            "下表只是本次 Run 的描述性排序。`R_deploy non-increasing` 表示 LOW 到 HIGH 沒有上升；它不是程式強制的規則，也不單獨代表因果關係。",
            "",
            "| Condition | Rule source | 決策方式 | R_deploy 趨勢 | Delta_R 趨勢 |",
            "|---|---|---|---|---|",
        ]
        for row in summary["regime_trends"]:
            lines.append(f"| {row['condition_id']} | {row['rule_source_id']} | {row['decision_interface']} | {row['r_deploy_pattern']} | {row['delta_r_pattern']} |")
        lines += [
            "",
            "## 5. M7 違規證據",
            "",
            f"- M7 failure trials：`{summary['violation_evidence']['failure_trial_count']}` / `{summary['violation_evidence']['m7_trial_count']}`",
            f"- 違規 trial 類型統計：`{self._inline_json(summary['violation_evidence']['violation_trial_counts'])}`",
            f"- M7 原始 reason code：`{self._inline_json(summary['violation_evidence']['reason_counts'])}`",
            "",
            "這些 counts 來自 M7 trial-level evidence，用來解釋失敗位置與原因；正式 reliability 數值仍以 M8 為準。",
            "",
            "## 6. GAI 執行狀態",
            "",
            f"- Trace rows：`{summary['gai_execution']['trace_row_count']}`；外部呼叫：`{summary['gai_execution']['external_call_count']}`",
            f"- Status：`{self._inline_json(summary['gai_execution']['status_counts'])}`",
            f"- Decision outcome：`{self._inline_json(summary['gai_execution']['decision_output_status_counts'])}`",
            f"- Failed requests：`{summary['gai_execution']['failed_request_count']}`",
            f"- Provider / model：`{', '.join(summary['gai_execution']['providers']) or '未提供'}` / `{', '.join(summary['gai_execution']['models']) or '未提供'}`",
            f"- Latency：`{self._inline_json(summary['gai_execution']['latency_ms'])}`",
            "",
            "GAI 的 `invalid_output` 與 `decision_infeasible` 是模型決策結果；provider 或 transport `unavailable` 則代表沒有可用執行結果，兩者不可混為一談。",
            "",
            "## 7. 研究限制",
            "",
            "- 本報告只描述本次設定、資料、topology、model、regime 與 trials 的結果。",
            "- `Delta_R = 0` 不自動代表 perception 沒有影響；若 `R_ideal = 0`，必須先看 M7 failure evidence。",
            "- 非單調趨勢不自動是 bug；需要結合 residual pool 與 M7 violation evidence 判讀。",
            "- 本報告不宣稱跨資料集泛化、因果關係或 deployment reliability 已被完整證明。",
            "",
            "## 8. 可追溯來源",
            "",
        ]
        for name, artifact in summary["source_artifacts"].items():
            lines.append(f"- `{name}`：`{artifact['path']}`；checksum：`{artifact['checksum'] or 'MISSING'}`")
        lines.append("")
        return "\n".join(lines)

    def _pattern(self, values: list[float | None], *, decreasing: bool) -> str:
        if len(values) < 2 or any(value is None for value in values):
            return "unavailable"
        numbers = [float(value) for value in values if value is not None]
        if decreasing:
            if all(numbers[index] > numbers[index + 1] + 1e-6 for index in range(len(numbers) - 1)):
                return "R_deploy strictly decreasing"
            if all(numbers[index] >= numbers[index + 1] - 1e-6 for index in range(len(numbers) - 1)):
                return "R_deploy non-increasing"
        else:
            if all(numbers[index] < numbers[index + 1] - 1e-6 for index in range(len(numbers) - 1)):
                return "Delta_R strictly increasing"
            if all(numbers[index] <= numbers[index + 1] + 1e-6 for index in range(len(numbers) - 1)):
                return "Delta_R non-decreasing"
        return "non-monotonic"

    def _same_metrics(self, *pairs: tuple[float | None, float | None]) -> bool:
        return all(
            left is None and right is None or left is not None and right is not None and abs(left - right) <= 1e-6
            for left, right in pairs
        )

    def _read_csv(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _read_csv_if_present(self, path: Path) -> list[dict[str, str]]:
        return self._read_csv(path) if path.is_file() else []

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return rows

    def _json_list(self, value: str | None) -> list[Any]:
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    def _float(self, value: Any) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _int(self, value: Any) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "1.0", "true", "yes"}

    def _near_zero(self, value: float | None) -> bool:
        return value is not None and abs(value) <= 1e-6

    def _format(self, value: float | None) -> str:
        if value is None:
            return "unavailable"
        return f"{value:.6f}".rstrip("0").rstrip(".")

    def _inline_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _sha256(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
