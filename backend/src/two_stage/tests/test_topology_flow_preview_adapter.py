from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

from two_stage.application.use_cases.decoupled_2_stage_experiment import Decoupled2StageExperimentUseCase
from two_stage.settings import Settings


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "build_topology_flow_preview_data.py"
SPEC = importlib.util.spec_from_file_location("topology_flow_preview_adapter", SCRIPT)
assert SPEC and SPEC.loader
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class TopologyFlowPreviewAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = tempfile.TemporaryDirectory()
        use_case = Decoupled2StageExperimentUseCase(Settings(
            app_env="test",
            local_artifact_root=cls.fixture.name,
            current_project_data_root=str(ROOT / "data"),
            current_project_config_root=str(ROOT / "configs"),
            live_gai_provider_enabled=False,
        ))
        result = use_case.run(trial_count_per_condition=30, scenarios_per_regime=8, root_seed=114)
        cls.run_root = Path(cls.fixture.name) / "published" / "runs" / result["run_id"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.cleanup()

    def test_formal_run_builds_paired_read_model_from_m3_to_m7(self) -> None:
        payload = ADAPTER.build_preview(self.run_root, "fcu__yolov8_det_v1", "MEDIUM", 0)
        metadata = payload["metadata"]
        without = payload["branches"]["without_two_stage"]
        with_branch = payload["branches"]["with_two_stage"]

        self.assertEqual(payload["schema_version"], "topology_flow_violation_preview_v1")
        self.assertFalse(metadata["demo"])
        self.assertEqual(without["decision_input_type"], "scenario_gt")
        self.assertEqual(with_branch["decision_input_type"], "observation")
        self.assertEqual(without["validation"]["truth_source_stage_id"], "M4")
        self.assertEqual(with_branch["validation"]["truth_source_stage_id"], "M4")
        self.assertEqual(without["validation"]["truth_checksum"], metadata["scenario_gt_checksum"])
        self.assertEqual(with_branch["validation"]["truth_checksum"], metadata["scenario_gt_checksum"])
        self.assertEqual(payload["topology"]["topology_checksum"], metadata["topology_checksum"])
        self.assertEqual(payload["topology"]["capacity_checksum"], metadata["capacity_checksum"])
        self.assertTrue(payload["topology"]["nodes"])
        self.assertTrue(payload["topology"]["edges"])
        self.assertTrue(all("x" in node and "y" in node for node in payload["topology"]["nodes"]))

    def test_capacity_display_comes_from_m7_reason(self) -> None:
        payload = ADAPTER.build_preview(self.run_root, "fcu__yolov8_det_v1", "MEDIUM", 0)
        violations = payload["branches"]["with_two_stage"]["validation"]["violations"]
        capacity = [item for item in violations if item["rule_code"] == "CAPACITY_CONSTRAINT"]
        self.assertTrue(capacity)
        self.assertTrue(all(item["original_code"] == "post_state_capacity" for item in capacity))
        self.assertTrue(all("violation_margin" in item for item in capacity))

    def test_gai_without_provider_is_unavailable(self) -> None:
        payload = ADAPTER.build_preview(self.run_root, "fcu__yolov8_det_v1", "MEDIUM", 0, interface="gai")
        for branch in payload["branches"].values():
            self.assertEqual(branch["availability"], "unavailable")
            self.assertEqual(branch["actions"], [])
            self.assertIsNone(branch["validation"]["valid"])

    def test_unknown_reason_is_preserved_as_warning_code(self) -> None:
        result = ADAPTER.normalize_reason({"code": "future_rule", "message": "kept"}, [])
        self.assertEqual(result["rule_code"], "UNKNOWN_RULE_CODE")
        self.assertEqual(result["original_code"], "future_rule")
        self.assertEqual(result["message"], "kept")

    def test_reason_evidence_fields_are_passed_through_without_recalculation(self) -> None:
        reason = {
            "code": "source_underflow",
            "source_id": "7",
            "target_id": "E2",
            "action_ids": ["action-1"],
            "outgoing": 120,
            "visible_population": 100,
            "truth": 100,
            "expected": 100,
            "actual": 120,
            "message": "outgoing exceeds visible population",
        }
        result = ADAPTER.normalize_reason(reason, [])

        self.assertEqual(result["rule_code"], "SOURCE_UNDERFLOW")
        self.assertEqual(result["action_ids"], ["action-1"])
        self.assertEqual(result["outgoing"], 120)
        self.assertEqual(result["visible_population"], 100)
        self.assertEqual(result["truth"], 100)
        self.assertEqual(result["actual"], 120)

    def test_grid_layout_is_deterministic(self) -> None:
        nodes = [{"id": "10"}, {"id": "2"}, {"id": "1"}, {"id": "E1"}]
        first = ADAPTER.normalized_grid(nodes)
        second = ADAPTER.normalized_grid(nodes)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"1", "2", "10", "E1"})

    def test_reference_layout_covers_all_formal_topology_nodes(self) -> None:
        expected = {
            "fcu": {str(index) for index in range(1, 33)} | {"P1", "P2", "P3", "P4", "E1", "E2", "E3", "E4"},
            "taichung_lantern_festival": {str(index) for index in range(1, 10)} | {"E1", "E2", "E3", "E4", "E5"},
            "taipei_new_years_eve": {str(index) for index in range(1, 6)} | {"E1", "E2", "E3", "E4", "E5", "E6"},
        }
        for topology_id, node_ids in expected.items():
            layout = ADAPTER.preview_layout(topology_id)
            self.assertIsNotNone(layout)
            self.assertEqual(set(layout["positions"]), node_ids)
            self.assertEqual(layout["version"], "ppt_reference_spread_v2")
            self.assertEqual(layout["base_version"], "ppt_reference_v1")
            self.assertGreater(layout["aspect_ratio"], 0)
            self.assertTrue(all(0 <= value <= 1 for item in layout["positions"].values() for value in item.values()))

    def test_spread_layout_is_deterministic_and_keeps_anchors(self) -> None:
        for topology_id in ("fcu", "taichung_lantern_festival", "taipei_new_years_eve"):
            first = ADAPTER.preview_layout(topology_id)
            second = ADAPTER.preview_layout(topology_id)
            self.assertEqual(first, second)
            assert first is not None
            for node_id, base in first["base_positions"].items():
                if node_id.startswith(("E", "P")):
                    self.assertEqual(first["positions"][node_id], base)

    def test_spread_layout_has_no_display_circle_overlap(self) -> None:
        for topology_id in ("fcu", "taichung_lantern_festival", "taipei_new_years_eve"):
            layout = ADAPTER.preview_layout(topology_id)
            assert layout is not None
            height = 100 / layout["aspect_ratio"]
            positions = {
                node_id: (
                    8 + 84 * point["x"],
                    8 + (height - 16) * point["y"],
                )
                for node_id, point in layout["positions"].items()
            }
            ids = list(positions)
            for index, first_id in enumerate(ids):
                for second_id in ids[index + 1:]:
                    distance = math.dist(positions[first_id], positions[second_id])
                    first_radius = 3.1 if first_id.startswith("E") else 3.6
                    second_radius = 3.1 if second_id.startswith("E") else 3.6
                    self.assertGreaterEqual(
                        distance,
                        first_radius + second_radius + layout["min_gap"] - 1e-6,
                        f"{topology_id}: {first_id} overlaps {second_id}",
                    )

    def test_spread_layout_bounds_general_node_displacement(self) -> None:
        for topology_id in ("fcu", "taichung_lantern_festival", "taipei_new_years_eve"):
            layout = ADAPTER.preview_layout(topology_id)
            assert layout is not None
            height = 100 / layout["aspect_ratio"]
            for node_id, base in layout["base_positions"].items():
                current = layout["positions"][node_id]
                base_point = (8 + 84 * base["x"], 8 + (height - 16) * base["y"])
                current_point = (8 + 84 * current["x"], 8 + (height - 16) * current["y"])
                displacement = math.dist(base_point, current_point)
                if not node_id.startswith(("E", "P")):
                    self.assertLessEqual(displacement, 10 + 1e-6, f"{topology_id}: {node_id} moved too far")

    def test_formal_topology_uses_reference_layout_without_changing_edges(self) -> None:
        payload = ADAPTER.build_preview(self.run_root, "fcu__yolov8_det_v1", "MEDIUM", 0)
        topology = payload["topology"]
        canonical = json.loads((self.run_root / "M3" / "fcu" / "topology_spec.json").read_text(encoding="utf-8"))
        self.assertEqual(topology["layout_status"], "ppt_reference_spread")
        self.assertEqual(topology["layout_version"], "ppt_reference_spread_v2")
        self.assertEqual(topology["layout_base_version"], "ppt_reference_v1")
        self.assertEqual(topology["layout_source"], "3 topologies drawing origin.pptx")
        self.assertEqual(topology["layout_min_gap"], 1.0)
        self.assertEqual(topology["canvas_aspect_ratio"], 1.1915)
        self.assertEqual(len(topology["nodes"]), 40)
        expected_pairs = {
            tuple(sorted((str(edge["source_id"]), str(edge["target_id"]))))
            for edge in canonical["edges"]
            if edge["source_id"] != edge["target_id"]
        }
        actual_pairs = {tuple(sorted((edge["source"], edge["target"]))) for edge in topology["edges"]}
        self.assertEqual(actual_pairs, expected_pairs)
        self.assertEqual(topology["topology_checksum"], canonical["topology_checksum"])

    def test_unknown_topology_layout_falls_back_to_grid(self) -> None:
        self.assertIsNone(ADAPTER.preview_layout("unknown"))


if __name__ == "__main__":
    unittest.main()
