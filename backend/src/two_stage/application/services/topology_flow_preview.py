"""Build a read-only topology flow preview model from published M0-M7 artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

from two_stage.application.services.topology_preview_layouts import preview_layout


SCHEMA_VERSION = "topology_flow_violation_preview_v1"
DEFAULT_RUN_ID = "decoupled-2-stage-20260803T062030574554Z-0cb0d0e3"
KNOWN_REASON_CODES = {
    "post_state_capacity": "CAPACITY_CONSTRAINT",
    "flow_conservation": "FLOW_CONSERVATION",
    "source_underflow": "SOURCE_UNDERFLOW",
    "topology_violation": "TOPOLOGY_CONNECTIVITY",
    "forbidden_target": "TOPOLOGY_CONNECTIVITY",
    "unknown_target": "UNKNOWN_ZONE",
    "invalid_output": "PARSE_ERROR",
}
M7_FLAG_REASON_CODES = {
    "capacity_violation": "CAPACITY_CONSTRAINT",
    "source_underflow_violation": "SOURCE_UNDERFLOW",
    "flow_conservation_violation": "FLOW_CONSERVATION",
    "topology_violation": "TOPOLOGY_CONNECTIVITY",
    "unknown_target_violation": "UNKNOWN_ZONE",
    "forbidden_target_violation": "TOPOLOGY_CONNECTIVITY",
    "invalid_output": "PARSE_ERROR",
}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def json_cell(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def as_bool(value: Any) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "pass", "passed"}:
        return True
    try:
        return float(text) != 0.0
    except ValueError:
        return False


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for item in value.split("-"):
        parts.append((0, int(item)) if item.isdigit() else (1, item))
    return tuple(parts)


def normalized_grid(nodes: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    ordered = sorted(nodes, key=lambda node: natural_key(str(node["id"])))
    columns = max(1, math.ceil(math.sqrt(len(ordered))))
    rows = max(1, math.ceil(len(ordered) / columns))
    positions: dict[str, dict[str, float]] = {}
    for index, node in enumerate(ordered):
        column, row = index % columns, index // columns
        positions[str(node["id"])] = {
            "x": 0.08 + (0.84 * column / max(columns - 1, 1)),
            "y": 0.08 + (0.84 * row / max(rows - 1, 1)),
        }
    return positions


def build_topology(stage_root: Path, topology_id: str, rule_source_id: str = "human_manual_v1") -> dict[str, Any]:
    source_path = stage_root / "M3" / rule_source_id / topology_id / "topology_spec.json"
    topology_path = source_path if source_path.is_file() else stage_root / "M3" / topology_id / "topology_spec.json"
    topology = read_json(topology_path)
    raw_nodes = topology.get("nodes", [])
    nodes = [
        {
            "id": str(node["node_id"]),
            "label": str(node.get("label", f"Zone {node['node_id']}")),
            "capacity": as_int(node.get("capacity")),
            "node_type": str(node.get("node_type", "zone")),
            "is_source_eligible": bool(node.get("is_source_eligible", False)),
        }
        for node in raw_nodes
    ]
    fallback_positions = normalized_grid(nodes)
    layout = preview_layout(topology_id)
    layout_positions = layout["positions"] if layout else {}
    missing_layout_nodes = sorted(
        {node["id"] for node in nodes} - set(layout_positions)
    ) if layout else []
    layout_status = (
        "ppt_reference_spread" if layout and not missing_layout_nodes
        else "ppt_reference_partial" if layout
        else "grid_fallback"
    )
    for node in nodes:
        node_id = node["id"]
        position = layout_positions.get(node_id) or fallback_positions[node_id]
        node["x"] = float(position.get("x", fallback_positions[node_id]["x"]))
        node["y"] = float(position.get("y", fallback_positions[node_id]["y"]))

    raw_edges = topology.get("edges", [])
    edge_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in raw_edges:
        source = str(edge["source_id"])
        target = str(edge["target_id"])
        pair = tuple(sorted((source, target), key=natural_key))
        if source == target:
            continue
        item = edge_by_pair.setdefault(
            pair,
            {
                "source": source,
                "target": target,
                "directed": False,
                "adjacency_pair_id": "--".join(pair),
                "directional_costs": {},
                "traversal_costs": {},
            },
        )
        direction = f"{source}->{target}"
        if edge.get("edge_cost") is not None:
            item["directional_costs"][direction] = as_int(edge.get("edge_cost"))
        if edge.get("traversal_cost") is not None:
            item["traversal_costs"][source] = as_int(edge.get("traversal_cost"))
    edges = list(edge_by_pair.values())
    return {
        "id": topology_id,
        "name": topology.get("topology_name", topology_id),
        "nodes": nodes,
        "edges": edges,
        "rules": topology.get("rules", {}),
        "graph_directionality": topology.get(
            "graph_directionality",
            topology.get("rules", {}).get("graph_directionality"),
        ),
        "adjacency_semantics": topology.get(
            "adjacency_semantics",
            topology.get("rules", {}).get("adjacency_semantics"),
        ),
        "edge_cost_directionality": topology.get(
            "edge_cost_directionality",
            topology.get("rules", {}).get("edge_cost_directionality"),
        ),
        "topology_checksum": topology.get("topology_checksum"),
        "capacity_checksum": topology.get("capacity_checksum"),
        "layout_source": layout["source"] if layout else "deterministic_grid",
        "layout_version": layout["version"] if layout else None,
        "layout_base_version": layout["base_version"] if layout else None,
        "layout_status": layout_status,
        "layout_missing_nodes": missing_layout_nodes,
        "layout_min_gap": layout["min_gap"] if layout else None,
        "canvas_aspect_ratio": layout["aspect_ratio"] if layout else 1.0,
        "layout_fallback": None if layout and not missing_layout_nodes else "grid",
    }


def normalize_reason(reason: dict[str, Any], action_ids: list[str]) -> dict[str, Any]:
    original_code = str(reason.get("code", "UNKNOWN_RULE_CODE"))
    rule_code = KNOWN_REASON_CODES.get(original_code, "UNKNOWN_RULE_CODE")
    item: dict[str, Any] = {
        "rule_code": rule_code,
        "original_code": original_code,
        "action_ids": list(reason.get("action_ids", [])),
        "message": str(reason.get("message", original_code)),
    }
    for key in (
        "node_id",
        "source_id",
        "target_id",
        "capacity",
        "post_population",
        "violation_margin",
        "outgoing",
        "incoming",
        "visible_population",
        "truth",
        "expected",
        "actual",
    ):
        if key in reason:
            item[key] = reason[key]
    if rule_code == "CAPACITY_CONSTRAINT":
        item["zone_id"] = str(reason.get("node_id", ""))
        if "capacity" in reason and "post_population" in reason:
            item["violation_margin"] = as_int(reason["post_population"]) - as_int(reason["capacity"])
        item["message"] = str(reason.get("message", f"Zone {item['zone_id']} exceeds capacity"))
    return item


def validation_payload(row: dict[str, str], action_ids: list[str]) -> dict[str, Any]:
    raw_reasons = json_cell(row.get("violation_reasons"), [])
    reasons = raw_reasons if isinstance(raw_reasons, list) else []
    violations = [normalize_reason(reason, action_ids) for reason in reasons if isinstance(reason, dict)]
    if not violations:
        for flag, code in M7_FLAG_REASON_CODES.items():
            if as_bool(row.get(flag)):
                violations.append({
                    "rule_code": code,
                    "original_code": flag,
                    "action_ids": [],
                    "message": f"M7 reported {flag}",
                })
    return {
        "valid": as_bool(row.get("valid")) if row.get("valid", "") != "" else None,
        "invalid_output": as_bool(row.get("invalid_output")),
        "post_population": json_cell(row.get("post_population"), {}),
        "violations": violations,
        "evidence_source": "M7/decision_validation_trials.csv",
        "truth_source_stage_id": row.get("validation_truth_source_stage_id", "M4"),
        "truth_checksum": row.get("validation_truth_checksum", ""),
    }


def build_actions(rows: list[dict[str, str]], trial_id: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in rows:
        action_index = as_int(row.get("action_index"))
        actions.append({
            "action_id": f"{trial_id}::action::{action_index}",
            "source_id": str(row.get("source_id", "")),
            "target_id": str(row.get("target_id", "")),
            "move_count": as_int(row.get("move_count")),
            "parse_valid": bool(row.get("source_id") and row.get("target_id") and row.get("move_count") is not None),
            "priority_metadata": json_cell(row.get("priority_metadata"), {}),
        })
    return actions


def unavailable_branch(interface: str, truth_checksum: str) -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "unavailable_reason": f"{interface} decision artifact is not available; no synthetic action or validation is created.",
        "decision_input_type": "unavailable",
        "observed_population": {},
        "decision_input_checksum": "",
        "actions": [],
        "validation": {
            "valid": None,
            "invalid_output": False,
            "post_population": {},
            "violations": [],
            "evidence_source": "M7 unavailable",
            "truth_source_stage_id": "M4",
            "truth_checksum": truth_checksum,
        },
    }


def build_preview(
    run_root: Path,
    condition_id: str,
    regime: str,
    trial_index: int,
    interface: str = "rule_based",
) -> dict[str, Any]:
    m0 = read_json(run_root / "M0" / "experiment_manifest.json")
    condition = next((item for item in m0["conditions"] if item["condition_id"] == condition_id), None)
    if condition is None:
        raise ValueError(f"Unknown condition_id: {condition_id}")
    topology_id = str(condition["topology_id"])
    topology = build_topology(run_root, topology_id, str(condition.get("rule_source_id", "human_manual_v1")))
    scenarios = read_jsonl(run_root / "M4" / "scenario_gt.jsonl")
    scenario_candidates = [
        item for item in scenarios
        if item["topology_id"] == topology_id and item["ground_truth_regime"] == regime
    ]
    if not scenario_candidates:
        raise ValueError(f"No scenario found for {topology_id}/{regime}")
    scenario_candidates.sort(key=lambda item: str(item["scenario_id"]))
    scenario = scenario_candidates[trial_index % len(scenario_candidates)]
    m5_rows = read_csv(run_root / "M5" / "observation_trials.csv")
    m6_summary_rows = read_csv(run_root / "M6" / "action_trials.csv")
    m6_action_rows = read_csv(run_root / "M6" / "decision_actions.csv")
    m7_rows = read_csv(run_root / "M7" / "decision_validation_trials.csv")
    base_condition_id = str(condition.get("base_condition_id", condition_id))
    pair_id = f"PAIR::{base_condition_id}::{regime}::{trial_index:04d}"
    rule_source_id = condition.get("rule_source_id")
    if rule_source_id:
        ideal_trial_id = f"TRIAL::{pair_id}::{rule_source_id}::{interface}::ideal"
        deployment_trial_id = f"TRIAL::{pair_id}::{rule_source_id}::{interface}::deployment"
        observation_trial_id = f"TRIAL::{base_condition_id}::{regime}::{trial_index:04d}::deployment"
    else:
        ideal_trial_id = f"TRIAL::{pair_id}::ideal"
        deployment_trial_id = f"TRIAL::{pair_id}::deployment"
        observation_trial_id = deployment_trial_id
    ideal_m7 = next((row for row in m7_rows if row["trial_id"] == ideal_trial_id and row["decision_interface"] == interface), None)
    deployment_m7 = next((row for row in m7_rows if row["trial_id"] == deployment_trial_id and row["decision_interface"] == interface), None)
    m5_row = next((row for row in m5_rows if row["trial_id"] == observation_trial_id), None)
    ideal_m6 = next((row for row in m6_summary_rows if row["trial_id"] == ideal_trial_id), None)
    deployment_m6 = next((row for row in m6_summary_rows if row["trial_id"] == deployment_trial_id), None)

    action_rows_by_trial: dict[str, list[dict[str, str]]] = {}
    for row in m6_action_rows:
        action_rows_by_trial.setdefault(row["trial_id"], []).append(row)
    truth = scenario["scenario_gt_population"]
    ideal_actions = build_actions(action_rows_by_trial.get(ideal_trial_id, []), ideal_trial_id)
    deployment_actions = build_actions(action_rows_by_trial.get(deployment_trial_id, []), deployment_trial_id)
    truth_checksum = str(scenario["scenario_checksum"])

    if interface == "gai_reserved" or ideal_m7 is None or deployment_m7 is None:
        without = unavailable_branch(interface, truth_checksum)
        with_branch = unavailable_branch("gai", truth_checksum)
    else:
        without = {
            "availability": "available",
            "decision_input_type": "scenario_gt",
            "observed_population": truth,
            "decision_input_checksum": ideal_m6.get("decision_input_checksum", "") if ideal_m6 else "",
            "actions": ideal_actions,
            "validation": validation_payload(ideal_m7, [item["action_id"] for item in ideal_actions]),
        }
        with_branch = {
            "availability": "available",
            "decision_input_type": "observation",
            "observed_population": json_cell(m5_row.get("observation_population"), {}) if m5_row else {},
            "decision_input_checksum": deployment_m6.get("decision_input_checksum", "") if deployment_m6 else (m5_row or {}).get("observation_checksum", ""),
            "actions": deployment_actions,
            "validation": validation_payload(deployment_m7, [item["action_id"] for item in deployment_actions]),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "run_id": run_root.name,
            "topology_id": topology_id,
            "topology_name": condition.get("topology_name", topology_id),
            "topology_checksum": topology.get("topology_checksum"),
            "capacity_checksum": topology.get("capacity_checksum"),
            "scenario_id": scenario["scenario_id"],
            "scenario_gt_checksum": truth_checksum,
            "regime": regime,
            "interface_type": interface,
            "trial_id": deployment_trial_id,
            "pair_id": pair_id,
            "trial_index": trial_index,
            "experiment_condition": condition_id,
            "rule_source_id": condition.get("rule_source_id", "human_manual_v1"),
            "rule_source_label": condition.get("rule_source_label", "人工規則"),
            "m7_truth_source_stage_id": "M4",
            "demo": False,
        },
        "topology": topology,
        "scenario_gt": truth,
        "branches": {
            "without_two_stage": without,
            "with_two_stage": with_branch,
        },
    }


PROFILE_ID = "decoupled_2_stage_experiment_v1"
COMPARISON_PROFILE_ID = "decoupled_2_stage_rule_source_comparison_v1"
SUPPORTED_PROFILE_IDS = {PROFILE_ID, COMPARISON_PROFILE_ID}
SUPPORTED_REGIMES = ("LOW", "MEDIUM", "HIGH")
SUPPORTED_INTERFACES = (
    {"id": "rule_based", "availability": "available"},
    {"id": "gai", "availability": "available_if_artifact_exists"},
    {"id": "gai_reserved", "availability": "unavailable"},
)
REQUIRED_ARTIFACTS = (
    "M0/experiment_manifest.json",
    "M4/scenario_gt.jsonl",
    "M5/observation_trials.csv",
    "M6/action_trials.csv",
    "M6/decision_actions.csv",
    "M7/decision_validation_trials.csv",
)


class PreviewServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class TopologyFlowPreviewService:
    """Read-only service for formal topology flow preview data."""

    def __init__(self, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root)
        self.published_root = self.storage_root / "published" / "runs"

    def _run_root(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
            raise PreviewServiceError("RUN_NOT_FOUND", "Run was not found.", 404)
        root = (self.published_root / run_id).resolve()
        try:
            root.relative_to(self.published_root.resolve())
        except ValueError as exc:
            raise PreviewServiceError("RUN_NOT_FOUND", "Run was not found.", 404) from exc
        if not root.is_dir():
            raise PreviewServiceError("RUN_NOT_FOUND", "Run was not found.", 404)
        return root

    def _summary(self, run_root: Path) -> dict[str, Any]:
        summary_path = run_root / "M9" / "run_summary.json"
        if not summary_path.exists():
            raise PreviewServiceError("PREVIEW_ARTIFACT_INCOMPLETE", "Run summary is missing.", 409, {"path": "M9/run_summary.json"})
        summary = read_json(summary_path)
        if summary.get("profile_id") not in SUPPORTED_PROFILE_IDS:
            raise PreviewServiceError("RUN_NOT_FOUND", "Run is not a decoupled two-stage experiment.", 404)
        status = str(summary.get("status", "SUCCEEDED")).upper()
        if status != "SUCCEEDED":
            raise PreviewServiceError("PREVIEW_RUN_NOT_SUCCEEDED", "Only SUCCEEDED runs can be loaded.", 404, {"status": status})
        return summary

    def _manifest(self, run_root: Path) -> dict[str, Any]:
        manifest_path = run_root / "M0" / "experiment_manifest.json"
        if not manifest_path.exists():
            raise PreviewServiceError("PREVIEW_ARTIFACT_INCOMPLETE", "M0 experiment manifest is missing.", 409, {"path": "M0/experiment_manifest.json"})
        return read_json(manifest_path)

    def _missing_artifacts(self, run_root: Path, manifest: dict[str, Any]) -> list[str]:
        required = list(REQUIRED_ARTIFACTS)
        topology_paths = []
        for item in manifest.get("conditions", []):
            source_path = f"M3/{item.get('rule_source_id', 'human_manual_v1')}/{item['topology_id']}/topology_spec.json"
            legacy_path = f"M3/{item['topology_id']}/topology_spec.json"
            topology_paths.append(source_path if (run_root / source_path).is_file() else legacy_path)
        topology_paths = sorted(set(topology_paths))
        required.extend(topology_paths)
        return [relative for relative in required if not (run_root / relative).is_file()]

    def _eligible(self, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        run_root = self._run_root(run_id)
        summary = self._summary(run_root)
        manifest = self._manifest(run_root)
        missing = self._missing_artifacts(run_root, manifest)
        if missing:
            raise PreviewServiceError(
                "PREVIEW_ARTIFACT_INCOMPLETE",
                "Formal preview artifacts are incomplete.",
                409,
                {"missing_paths": missing},
            )
        return run_root, summary, manifest

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self.published_root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for run_root in self.published_root.iterdir():
            if not run_root.is_dir():
                continue
            summary_path = run_root / "M9" / "run_summary.json"
            manifest_path = run_root / "M0" / "experiment_manifest.json"
            if not summary_path.is_file() or not manifest_path.is_file():
                continue
            try:
                summary = read_json(summary_path)
                manifest = read_json(manifest_path)
                if summary.get("profile_id") not in SUPPORTED_PROFILE_IDS:
                    continue
                if str(summary.get("status", "SUCCEEDED")).upper() != "SUCCEEDED":
                    continue
                missing = self._missing_artifacts(run_root, manifest)
                if missing:
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                continue
            rows.append({
                "run_id": run_root.name,
                "created_at": summary.get("created_at"),
                "status": "SUCCEEDED",
                "profile_id": summary.get("profile_id", PROFILE_ID),
                "condition_count": len(manifest.get("conditions", [])),
                "config": summary.get("config", {}),
                "scenario_generation": summary.get("scenario_generation", {}),
                "preview_available": True,
            })
        rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return rows[: max(1, min(int(limit), 100))]

    @staticmethod
    def _conditions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
        conditions = []
        for item in manifest.get("conditions", []):
            conditions.append({
                "condition_id": str(item["condition_id"]),
                "topology_id": str(item["topology_id"]),
                "topology_name": str(item.get("topology_name", item["topology_id"])),
                "model_id": str(item["model_id"]),
                "model_name": str(item.get("model_name", item["model_id"])),
                "paradigm": str(item.get("paradigm", "unknown")),
                "rule_source_id": str(item.get("rule_source_id", "human_manual_v1")),
                "rule_source_label": str(item.get("rule_source_label", "人工規則")),
            })
        return sorted(conditions, key=lambda item: (item["rule_source_id"], item["topology_id"], item["model_id"]))

    def _trial_options(
        self,
        run_root: Path,
        conditions: list[dict[str, Any]],
        scenarios: list[dict[str, Any]],
        m7_rows: list[dict[str, str]],
        rule_source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        source_ids = [rule_source_id] if rule_source_id else sorted({item["rule_source_id"] for item in conditions})
        for source_id in source_ids:
            source_conditions = [item for item in conditions if item["rule_source_id"] == source_id]
            by_topology = sorted({item["topology_id"] for item in source_conditions})
            for topology_id in by_topology:
                candidates = sorted(
                    [
                        item for item in scenarios
                        if str(item.get("topology_id")) == topology_id
                        and str(item.get("ground_truth_regime")) in SUPPORTED_REGIMES
                    ],
                    key=lambda item: str(item["scenario_id"]),
                )
                topology_conditions = [item for item in source_conditions if item["topology_id"] == topology_id]
                for regime in SUPPORTED_REGIMES:
                    index_sets: list[set[int]] = []
                    for condition in topology_conditions:
                        indices = {
                            as_int(row.get("trial_index"))
                            for row in m7_rows
                            if row.get("condition_id") == condition["condition_id"]
                            and row.get("ground_truth_regime") == regime
                            and row.get("decision_interface") in {"rule_based", "gai"}
                            and row.get("trial_type") == "ideal"
                        }
                        index_sets.append(indices)
                    common_indices = set.intersection(*index_sets) if index_sets else set()
                    regime_candidates = [item for item in candidates if str(item.get("ground_truth_regime")) == regime]
                    trials = []
                    if regime_candidates:
                        for trial_index in sorted(common_indices):
                            scenario = regime_candidates[trial_index % len(regime_candidates)]
                            trials.append({
                                "trial_index": trial_index,
                                "scenario_id": scenario["scenario_id"],
                                "scenario_checksum": scenario["scenario_checksum"],
                            })
                    result.append({"rule_source_id": source_id, "topology_id": topology_id, "regime": regime, "trials": trials})
        return result

    def options(self, run_id: str) -> dict[str, Any]:
        run_root, summary, manifest = self._eligible(run_id)
        conditions = self._conditions(manifest)
        scenarios = read_jsonl(run_root / "M4" / "scenario_gt.jsonl")
        m7_rows = read_csv(run_root / "M7" / "decision_validation_trials.csv")
        trial_options = self._trial_options(run_root, conditions, scenarios, m7_rows)
        source_rows = {}
        for condition in conditions:
            source_rows.setdefault(condition["rule_source_id"], condition["rule_source_label"])
        topologies = []
        for topology_id in sorted({item["topology_id"] for item in conditions}):
            topology_condition = next(item for item in conditions if item["topology_id"] == topology_id)
            topologies.append({
                "topology_id": topology_id,
                "topology_name": topology_condition["topology_name"],
            })
        models = []
        seen_models: set[str] = set()
        for condition in conditions:
            if condition["model_id"] in seen_models:
                continue
            seen_models.add(condition["model_id"])
            models.append({
                "model_id": condition["model_id"],
                "model_name": condition["model_name"],
                "paradigm": condition["paradigm"],
            })
        default_topology = topologies[0]["topology_id"] if topologies else None
        default_model = next((item["model_id"] for item in conditions if item["topology_id"] == default_topology), None)
        default_trial = next(
            (
                item["trials"][0]["trial_index"]
                for item in trial_options
                if item["rule_source_id"] == (conditions[0]["rule_source_id"] if conditions else "human_manual_v1")
                and item["topology_id"] == default_topology and item["regime"] == "LOW" and item["trials"]
            ),
            0,
        )
        has_gai_artifacts = any(row.get("decision_interface") == "gai" for row in m7_rows)
        interfaces = [
            {"id": "rule_based", "availability": "available"},
            {
                "id": "gai" if has_gai_artifacts else "gai_reserved",
                "availability": "available" if has_gai_artifacts else "unavailable",
            },
        ]
        return {
            "schema_version": "topology_flow_violation_preview_options_v1",
            "run": {
                "run_id": run_root.name,
                "status": "SUCCEEDED",
                "created_at": summary.get("created_at"),
                "config": summary.get("config", {}),
                "scenario_generation": summary.get("scenario_generation", {}),
            },
            "topologies": topologies,
            "models": models,
            "rule_sources": [
                {"rule_source_id": source_id, "rule_source_label": label}
                for source_id, label in sorted(source_rows.items())
            ],
            "conditions": conditions,
            "trial_options": trial_options,
            "interfaces": interfaces,
            "default_selection": {
                "rule_source_id": conditions[0]["rule_source_id"] if conditions else "human_manual_v1",
                "topology_id": default_topology,
                "model_id": default_model,
                "regime": "LOW",
                "trial_index": default_trial,
                "interface": "rule_based",
            },
        }

    def _condition(self, manifest: dict[str, Any], topology_id: str, model_id: str, rule_source_id: str) -> dict[str, Any]:
        matches = [
            item for item in self._conditions(manifest)
            if item["topology_id"] == topology_id
            and item["model_id"] == model_id
            and item["rule_source_id"] == rule_source_id
        ]
        if len(matches) != 1:
            raise PreviewServiceError(
                "INVALID_PREVIEW_SELECTION",
                "Topology and model do not identify exactly one experiment condition.",
                400,
                {"topology_id": topology_id, "model_id": model_id, "rule_source_id": rule_source_id},
            )
        return matches[0]

    def preview(
        self,
        run_id: str,
        *,
        topology_id: str,
        model_id: str,
        regime: str,
        trial_index: int,
        interface: str = "rule_based",
        rule_source_id: str = "human_manual_v1",
    ) -> dict[str, Any]:
        run_root, _summary, manifest = self._eligible(run_id)
        if regime not in SUPPORTED_REGIMES:
            raise PreviewServiceError("INVALID_PREVIEW_SELECTION", "Unsupported ground-truth regime.", 400, {"regime": regime})
        normalized_interface = interface
        if normalized_interface not in {"rule_based", "gai", "gai_reserved"}:
            raise PreviewServiceError("INVALID_PREVIEW_SELECTION", "Unsupported decision interface.", 400, {"interface": interface})
        condition = self._condition(manifest, topology_id, model_id, rule_source_id)
        scenarios = read_jsonl(run_root / "M4" / "scenario_gt.jsonl")
        m7_rows = read_csv(run_root / "M7" / "decision_validation_trials.csv")
        trial_options = self._trial_options(run_root, self._conditions(manifest), scenarios, m7_rows, rule_source_id)
        allowed = next(
            (
                item["trials"] for item in trial_options
                if item["rule_source_id"] == rule_source_id
                and item["topology_id"] == topology_id and item["regime"] == regime
            ),
            [],
        )
        if not any(int(item["trial_index"]) == int(trial_index) for item in allowed):
            raise PreviewServiceError(
                "INVALID_PREVIEW_SELECTION",
                "Trial is not available for the selected topology and regime.",
                400,
                {"topology_id": topology_id, "regime": regime, "trial_index": trial_index},
            )
        payload = build_preview(run_root, condition["condition_id"], regime, int(trial_index), normalized_interface)
        if normalized_interface in {"rule_based", "gai"}:
            for branch_name in ("without_two_stage", "with_two_stage"):
                if payload["branches"][branch_name]["availability"] != "available":
                    raise PreviewServiceError(
                        "PREVIEW_ARTIFACT_INCOMPLETE",
                        "Selected formal pair does not contain complete rule-based M6/M7 evidence.",
                        409,
                        {"branch": branch_name, "condition_id": condition["condition_id"], "regime": regime, "trial_index": trial_index},
                    )
        payload["result_summary"] = self._result_summary(
            run_root,
            condition_id=condition["condition_id"],
            rule_source_id=rule_source_id,
            topology_id=topology_id,
            model_id=model_id,
            regime=regime,
            interface=normalized_interface,
        )
        return payload

    def _result_summary(
        self,
        run_root: Path,
        *,
        condition_id: str,
        rule_source_id: str,
        topology_id: str,
        model_id: str,
        regime: str,
        interface: str,
    ) -> dict[str, Any]:
        metrics_path = run_root / "M8" / "decoupled_2_stage_metrics.csv"
        if not metrics_path.is_file():
            raise PreviewServiceError(
                "PREVIEW_ARTIFACT_INCOMPLETE",
                "M8 metrics artifact is missing.",
                409,
                {"path": "M8/decoupled_2_stage_metrics.csv"},
            )
        rows = [
            row for row in read_csv(metrics_path)
            if row.get("condition_id") == condition_id
            and str(row.get("rule_source_id") or rule_source_id) == rule_source_id
            and row.get("topology_id") == topology_id
            and row.get("model_id") == model_id
            and row.get("ground_truth_regime") == regime
            and row.get("decision_interface") == interface
        ]
        ideal = next((row for row in rows if row.get("trial_type") == "ideal"), None)
        deployment = next((row for row in rows if row.get("trial_type") == "deployment"), None)
        if ideal is None and deployment is None:
            return {
                "r_ideal": None,
                "r_deploy": None,
                "delta_r": None,
                "ideal_valid_trial_count": None,
                "deployment_valid_trial_count": None,
                "ideal_executed_trial_count": None,
                "deployment_executed_trial_count": None,
                "availability": "unavailable",
                "execution_outcome_status": "unavailable",
            }
        outcome_values = [str(row.get("execution_outcome_status") or row.get("availability") or "unavailable") for row in (ideal, deployment) if row]
        availability = "available" if all(str(row.get("availability")) == "available" for row in (ideal, deployment) if row) and len(outcome_values) == 2 else "unavailable"
        outcome = "available" if availability == "available" else next((item for item in outcome_values if item != "available"), "unavailable")
        return {
            "r_ideal": as_float_or_none(ideal.get("r_ideal") if ideal else None),
            "r_deploy": as_float_or_none(deployment.get("r_deploy") if deployment else None),
            "delta_r": as_float_or_none(deployment.get("delta_r") if deployment else None),
            "ideal_valid_trial_count": as_int(ideal.get("ideal_valid_trial_count")) if ideal else None,
            "deployment_valid_trial_count": as_int(deployment.get("deployment_valid_trial_count")) if deployment else None,
            "ideal_executed_trial_count": as_int(ideal.get("ideal_executed_trial_count")) if ideal else None,
            "deployment_executed_trial_count": as_int(deployment.get("deployment_executed_trial_count")) if deployment else None,
            "availability": availability,
            "execution_outcome_status": outcome,
        }
