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


TOPOLOGY_STEMS = (
    "fcu",
    "Taichung Lantern Festival",
    "Taipei New Year's Eve",
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def materialize(source_root: Path, destination_root: Path) -> dict[str, Any]:
    destination_root.mkdir(parents=True, exist_ok=True)
    topology_records: list[dict[str, Any]] = []

    for stem in TOPOLOGY_STEMS:
        map_path = source_root / f"{stem}_map_neww.json"
        neighbors_path = source_root / f"{stem}_neighbors.json"
        rules_path = source_root / f"{stem}_rule.json"
        source_files = (map_path, neighbors_path, rules_path)
        missing = [str(path) for path in source_files if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Missing topology input files: {missing}")

        map_rows = _read_json(map_path)
        neighbor_rows = _read_json(neighbors_path)
        rules = dict(_read_json(rules_path))
        rules["adjacency_semantics"] = ADJACENCY_SEMANTICS
        rules["edge_cost_directionality"] = EDGE_COST_DIRECTIONALITY
        rules["topology_input_materialization"] = "human_0803_directional_cost_v1"

        report = validate_topology_triplet(
            map_rows,
            neighbor_rows,
            rules,
            source_label=f"human_0803/{stem}",
        )
        if report["status"] != "PASSED":
            raise ValueError(json.dumps(report, ensure_ascii=False, indent=2))

        if not isinstance(map_rows, list) or not isinstance(neighbor_rows, list):
            raise TypeError("Validated topology rows must be arrays.")
        rebuilt_map = rebuild_nearby_zone(map_rows, neighbor_rows, rules)

        output_map = destination_root / map_path.name
        output_neighbors = destination_root / neighbors_path.name
        output_rules = destination_root / rules_path.name
        _write_json(output_map, rebuilt_map)
        _write_json(output_neighbors, neighbor_rows)
        _write_json(output_rules, rules)
        topology_records.append({
            "topology_id": str(rules.get("topology_id", stem)),
            "source_files": {
                map_path.name: _sha256(map_path),
                neighbors_path.name: _sha256(neighbors_path),
                rules_path.name: _sha256(rules_path),
            },
            "materialized_files": {
                output_map.name: _sha256(output_map),
                output_neighbors.name: _sha256(output_neighbors),
                output_rules.name: _sha256(output_rules),
            },
            "validation": report,
            "nearby_zone_policy": {
                "algorithm": "directional_dijkstra",
                "max_total_cost": int(rules.get("max_total_cost", 3)),
                "external_exits": sorted(str(value) for value in rules.get("external_exits", [])),
            },
        })

    manifest = {
        "manifest_version": "topology_input_manifest_v1",
        "materialization_id": "human_0803_directional_cost_v1",
        "source_label": "X:/瀏覽器下載/拓樸0803/拓樸0803/人工",
        "graph_directionality": "undirected",
        "adjacency_semantics": ADJACENCY_SEMANTICS,
        "edge_cost_directionality": EDGE_COST_DIRECTIONALITY,
        "topologies": topology_records,
    }
    _write_json(destination_root / "topology_input_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize human topology triplets with directional edge costs.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    manifest = materialize(args.source, args.destination)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
