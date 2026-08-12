from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from two_stage.application.services.topology_input_contract import (
    ADJACENCY_SEMANTICS,
    EDGE_COST_DIRECTIONALITY,
    rebuild_nearby_zone,
    validate_topology_triplet,
)


TOPOLOGIES = (
    ("fcu", "fcu_map_GPT.json", "fcu_neighbors_GPT.json", "FCU"),
    (
        "Taichung Lantern Festival",
        "Taichung Lantern Festival_map_GPT.json",
        "Taichung Lantern Festival_neighbors_GPT.json",
        "Taichung Lantern Festival",
    ),
    (
        "Taipei New Year's Eve",
        "Taipei New Year's Eve_map_GPT.json",
        "Taipei New Year's Eve_neighbors_GPT.json",
        "Taipei New Year's Eve",
    ),
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _rule_bundle(topology_id: str, map_rows: list[dict[str, Any]]) -> dict[str, Any]:
    node_ids = [str(row["id"]) for row in map_rows]
    exits = sorted(node_id for node_id in node_ids if node_id.upper().startswith("E"))
    return {
        "schema_version": "1.0",
        "topology_id": topology_id,
        "rule_source_id": "ai_generated_derived_v1",
        "rule_source_label": "AI-generated topology graph materialized rule bundle",
        "rule_source_scope": "graph_adjacency_cost_and_exit_derivation",
        "generated_from": "ai_map_and_neighbors",
        "graph_directionality": "undirected",
        "adjacency_semantics": ADJACENCY_SEMANTICS,
        "edge_cost_directionality": EDGE_COST_DIRECTIONALITY,
        "cost_semantics": "total_cost = edge_cost + traversal_cost",
        "max_total_cost": 3,
        "allowed_node_types_as_source": ["zone"],
        "allowed_node_types_as_destination": ["zone", "exit"],
        "external_exits": exits,
        "exit_behavior": "terminate_search",
        "exit_search_behavior": "stop",
        "priority_rule": "ascending_total_cost",
        "decision_output_mode": "target_only",
        "governance_defaults": [
            "max_total_cost",
            "allowed_node_types_as_source",
            "allowed_node_types_as_destination",
            "exit_behavior",
            "priority_rule",
            "decision_output_mode",
        ],
    }


def materialize(source_root: Path, destination_root: Path) -> dict[str, Any]:
    destination_root.mkdir(parents=True, exist_ok=True)
    topology_records: list[dict[str, Any]] = []

    for topology_id, map_name, neighbors_name, rule_topology_id in TOPOLOGIES:
        map_path = source_root / map_name
        neighbors_path = source_root / neighbors_name
        if not map_path.is_file() or not neighbors_path.is_file():
            raise FileNotFoundError(f"Missing AI topology input: {map_path}, {neighbors_path}")

        map_rows = _read_json(map_path)
        neighbor_rows = _read_json(neighbors_path)
        if not isinstance(map_rows, list) or not isinstance(neighbor_rows, list):
            raise TypeError(f"AI topology rows must be arrays: {topology_id}")
        rules = _rule_bundle(rule_topology_id, map_rows)
        report = validate_topology_triplet(
            map_rows,
            neighbor_rows,
            rules,
            source_label=f"ai_generated/{topology_id}",
        )
        if report["status"] != "PASSED":
            raise ValueError(json.dumps(report, ensure_ascii=False, indent=2))

        rebuilt_map = rebuild_nearby_zone(map_rows, neighbor_rows, rules)
        output_map = destination_root / f"{topology_id}_map_neww.json"
        output_neighbors = destination_root / f"{topology_id}_neighbors.json"
        output_rules = destination_root / f"{topology_id}_rule.json"
        _write_json(output_map, rebuilt_map)
        _write_json(output_neighbors, neighbor_rows)
        _write_json(output_rules, rules)

        topology_records.append({
            "topology_id": topology_id,
            "source_topology_id": rule_topology_id,
            "source_files": {
                map_name: _sha256(map_path),
                neighbors_name: _sha256(neighbors_path),
            },
            "materialized_files": {
                output_map.name: _sha256(output_map),
                output_neighbors.name: _sha256(output_neighbors),
                output_rules.name: _sha256(output_rules),
            },
            "validation": report,
            "nearby_zone_policy": {
                "algorithm": "directional_dijkstra",
                "max_total_cost": int(rules["max_total_cost"]),
                "external_exits": rules["external_exits"],
            },
            "rule_provenance": {
                "rule_source_id": rules["rule_source_id"],
                "generated_from": rules["generated_from"],
                "ai_derived_fields": [
                    "nodes",
                    "capacity",
                    "adjacency",
                    "edge_cost",
                    "traversal_cost",
                    "external_exits",
                ],
                "system_contract_fields": rules["governance_defaults"],
            },
        })

    manifest = {
        "manifest_version": "topology_input_manifest_v1",
        "materialization_id": "ai_generated_derived_v1",
        "source_label": "X:/瀏覽器下載/拓樸0803/拓樸0803/AI生成",
        "rule_source_id": "ai_generated_derived_v1",
        "rule_source_label": "AI-generated topology graph materialized rule bundle",
        "graph_directionality": "undirected",
        "adjacency_semantics": ADJACENCY_SEMANTICS,
        "edge_cost_directionality": EDGE_COST_DIRECTIONALITY,
        "topologies": topology_records,
    }
    _write_json(destination_root / "topology_input_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize AI topology graph data as a validated rule-source package.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(args.source, args.destination), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
