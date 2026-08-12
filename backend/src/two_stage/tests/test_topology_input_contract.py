from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from two_stage.application.services.topology_input_contract import (
    rebuild_nearby_zone,
    validate_topology_triplet,
)
from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    Decoupled2StageExperimentUseCase,
)
from two_stage.application.dto.stage2 import Stage2PipelineConfig
from two_stage.application.use_cases.stage2_topology_ideal import (
    Stage2TopologyIdealRunner,
    TOPOLOGY_TRIPLET_PROFILE_ID,
)


class TopologyInputContractTests(unittest.TestCase):
    def _rules(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "topology_id": "fixture",
            "graph_directionality": "undirected",
            "adjacency_semantics": "symmetric",
            "edge_cost_directionality": "directed",
            "max_total_cost": 3,
            "external_exits": ["E1"],
        }

    def _rows(self) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        maps = [
            {"id": "A", "max_occupancy": 100, "nearby_zone": []},
            {"id": "B", "max_occupancy": 100, "nearby_zone": []},
            {"id": "E1", "max_occupancy": 1000, "nearby_zone": []},
        ]
        neighbors = [
            {
                "id": "A",
                "max_occupancy": 100,
                "traversal_cost": 0,
                "neighbors": [{"id": "B", "cost": 10}],
            },
            {
                "id": "B",
                "max_occupancy": 100,
                "traversal_cost": 0,
                "neighbors": [
                    {"id": "A", "cost": 1},
                    {"id": "E1", "cost": 1},
                ],
            },
            {
                "id": "E1",
                "max_occupancy": 1000,
                "traversal_cost": 0,
                "neighbors": [{"id": "B", "cost": 1}],
            },
        ]
        return maps, neighbors

    def test_asymmetric_costs_are_allowed_when_adjacency_is_symmetric(self) -> None:
        maps, neighbors = self._rows()
        report = validate_topology_triplet(maps, neighbors, self._rules())
        self.assertEqual(report["status"], "PASSED")
        self.assertEqual(report["asymmetric_cost_pairs"], [{
            "source_id": "A",
            "target_id": "B",
            "forward_cost": 10,
            "reverse_cost": 1,
        }])

    def test_missing_reverse_adjacency_fails(self) -> None:
        maps, neighbors = self._rows()
        neighbors[2]["neighbors"] = []
        report = validate_topology_triplet(maps, neighbors, self._rules())
        self.assertEqual(report["status"], "FAILED")
        self.assertTrue(any(issue["code"] == "ASYMMETRIC_ADJACENCY" for issue in report["issues"]))

    def test_nearby_zone_uses_directional_costs_and_rule_exits(self) -> None:
        maps, neighbors = self._rows()
        rebuilt = rebuild_nearby_zone(maps, neighbors, self._rules())
        by_id = {str(row["id"]): row for row in rebuilt}
        self.assertEqual(by_id["A"]["nearby_zone"], [])
        self.assertEqual(by_id["B"]["nearby_zone"], [{"id": "A", "hops": 1}, {"id": "E1", "hops": 1}])

    def test_m6_ranking_uses_source_to_target_cost(self) -> None:
        use_case = Decoupled2StageExperimentUseCase.__new__(Decoupled2StageExperimentUseCase)
        topology = {
            "rules": {"priority_rule": "ascending_total_cost"},
            "adjacency": {"A": ["B"], "B": ["A"]},
            "edges": [
                {"source_id": "A", "target_id": "B", "edge_cost": 10, "traversal_cost": 0},
                {"source_id": "B", "target_id": "A", "edge_cost": 1, "traversal_cost": 0},
            ],
        }
        self.assertEqual(use_case._ranked_targets("A", topology)[0]["total_cost"], 10)
        self.assertEqual(use_case._ranked_targets("B", topology)[0]["total_cost"], 1)

    def test_m7_validation_uses_adjacency_not_cost_symmetry(self) -> None:
        use_case = Decoupled2StageExperimentUseCase.__new__(Decoupled2StageExperimentUseCase)
        topology = {
            "capacity_by_node": {"A": 100, "B": 100, "E1": 1000},
            "adjacency": {"A": ["B"], "B": ["A", "E1"], "E1": ["B"]},
            "external_exits": ["E1"],
            "source_nodes": ["A", "B"],
            "rules": {"allowed_node_types_as_destination": ["zone", "exit"]},
            "edges": [
                {"source_id": "A", "target_id": "B", "edge_cost": 10, "traversal_cost": 0},
                {"source_id": "B", "target_id": "A", "edge_cost": 1, "traversal_cost": 0},
            ],
        }
        result = use_case._validate_actions(
            topology,
            {"A": 10, "B": 0, "E1": 0},
            [{"source_id": "A", "target_id": "B", "move_count": 1}],
        )
        self.assertTrue(result["valid"])
        self.assertFalse(result["topology_violation"])

    def test_materialized_repository_inputs_keep_directional_cost_metadata(self) -> None:
        root = Path(__file__).resolve().parents[4]
        data_root = root / "Data" / "Topology資料"
        manifest = json.loads((data_root / "topology_input_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["graph_directionality"], "undirected")
        self.assertEqual(manifest["adjacency_semantics"], "symmetric")
        self.assertEqual(manifest["edge_cost_directionality"], "directed")
        for rule_path in data_root.glob("*_rule.json"):
            rules = json.loads(rule_path.read_text(encoding="utf-8"))
            self.assertEqual(rules["adjacency_semantics"], "symmetric")
            self.assertEqual(rules["edge_cost_directionality"], "directed")

    def test_materialized_formal_cost_pairs_are_preserved(self) -> None:
        root = Path(__file__).resolve().parents[4]
        data_root = root / "Data" / "Topology資料"

        def costs(file_name: str) -> dict[tuple[str, str], int]:
            rows = json.loads((data_root / file_name).read_text(encoding="utf-8"))
            return {
                (str(row["id"]), str(neighbor["id"])): int(neighbor["cost"])
                for row in rows
                for neighbor in row["neighbors"]
            }

        fcu = costs("fcu_neighbors.json")
        taipei = costs("Taipei New Year's Eve_neighbors.json")
        self.assertEqual(fcu[("4", "6")], 10)
        self.assertEqual(fcu[("6", "4")], 1)
        self.assertEqual(taipei[("2", "4")], 2)
        self.assertEqual(taipei[("4", "2")], 1)

    def test_stage2_triplet_loader_preserves_both_directional_edges(self) -> None:
        root = Path(__file__).resolve().parents[4]
        data_root = root / "Data" / "Topology資料"
        with tempfile.TemporaryDirectory() as artifact_root:
            config = Stage2PipelineConfig(
                run_id="directional-cost-loader-test",
                topology_profile_id=TOPOLOGY_TRIPLET_PROFILE_ID,
                topology_package_path=".",
                topology_source_id="fcu",
            )
            runner = Stage2TopologyIdealRunner(
                source_root=data_root,
                artifact_root=artifact_root,
                config=config,
            )
            topology = runner._load_topology(runner._topology_package_root())
            costs = {
                (edge.source, edge.target): edge.travel_cost
                for edge in topology.edges
            }
            self.assertEqual(topology.graph_directionality, "undirected")
            self.assertEqual(topology.adjacency_semantics, "symmetric")
            self.assertEqual(topology.edge_cost_directionality, "directed")
            self.assertEqual(costs[("4", "6")], 10.0)
            self.assertEqual(costs[("6", "4")], 1.0)


if __name__ == "__main__":
    unittest.main()
