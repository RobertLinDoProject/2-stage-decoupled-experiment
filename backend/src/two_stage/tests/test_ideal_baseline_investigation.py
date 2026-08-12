from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    Decoupled2StageExperimentUseCase,
)
from two_stage.settings import Settings


ROOT = Path(__file__).resolve().parents[4]
STORAGE_ROOT = ROOT / "storage" / "published" / "runs"


def latest_formal_run() -> Path:
    candidates = []
    for run_root in STORAGE_ROOT.iterdir():
        summary_path = run_root / "M9" / "run_summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        config = summary.get("config", {})
        if (
            config.get("root_seed") == 114
            and config.get("trial_count_per_condition") == 30
            and config.get("scenarios_per_regime") == 8
            and config.get("split") == "test"
            and config.get("metric_policy_version") == "2.0.0"
        ):
            candidates.append(run_root)
    if not candidates:
        raise AssertionError("No formal run is available for diagnostics tests")
    return max(candidates, key=lambda path: (path / "M9" / "run_summary.json").stat().st_mtime)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class IdealBaselineInvestigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = tempfile.TemporaryDirectory()
        cls.use_case = Decoupled2StageExperimentUseCase(Settings(
            app_env="test",
            local_artifact_root=cls.fixture.name,
            current_project_data_root=str(ROOT / "Data"),
            current_project_config_root=str(ROOT / "configs"),
            live_gai_provider_enabled=False,
        ))
        result = cls.use_case.run(
            trial_count_per_condition=30,
            scenarios_per_regime=8,
            root_seed=114,
        )
        cls.run_root = Path(cls.fixture.name) / "published" / "runs" / result["run_id"]
        cls.validation = rows(cls.run_root / "M7" / "decision_validation_trials.csv")
        cls.decisions = rows(cls.run_root / "M6" / "action_trials.csv")
        cls.decision_by_trial = {row["trial_id"]: row for row in cls.decisions}
        cls.observations = rows(cls.run_root / "M5" / "observation_trials.csv")
        cls.metrics = rows(cls.run_root / "M8" / "decoupled_2_stage_metrics.csv")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.cleanup()

    def test_ideal_residual_isolation(self) -> None:
        ideal_decisions = [row for row in self.decisions if row["trial_type"] == "ideal"]
        self.assertEqual(len(ideal_decisions), 1350)
        self.assertTrue(all(row["decision_input_mode"] == "scenario_gt" for row in ideal_decisions))
        self.assertTrue(all(row["decision_input_checksum"] == row["scenario_checksum"] for row in ideal_decisions))
        self.assertFalse(any(row["trial_type"] == "ideal" for row in self.observations))

    def test_cross_model_ideal_consistency(self) -> None:
        ideal = [row for row in self.validation if row["trial_type"] == "ideal"]
        grouped: dict[tuple[str, str, str], set[tuple[str, str, str]]]
        grouped = {}
        for row in ideal:
            key = (row["topology_id"], row["ground_truth_regime"], row["scenario_id"])
            decision = self.decision_by_trial[row["trial_id"]]
            grouped.setdefault(key, set()).add((decision["action_checksum"], row["valid"], row["rule_violation"]))
        self.assertTrue(all(len(values) == 1 for values in grouped.values()))

    def test_repeated_scenario_is_deterministic(self) -> None:
        model_id = sorted({row["model_id"] for row in self.validation})[0]
        ideal = [row for row in self.validation if row["trial_type"] == "ideal" and row["model_id"] == model_id]
        grouped: dict[str, set[tuple[str, str, str]]] = {}
        for row in ideal:
            decision = self.decision_by_trial[row["trial_id"]]
            grouped.setdefault(row["scenario_id"], set()).add((decision["action_checksum"], row["valid"], row["rule_violation"]))
        self.assertEqual({len(values) for values in grouped.values()}, {1})
        usage_counts = {scenario_id: sum(row["scenario_id"] == scenario_id for row in ideal) for scenario_id in grouped}
        self.assertEqual(set(usage_counts.values()), {3, 4})

    def test_reliability_aggregation_known_four_of_thirty(self) -> None:
        self.assertEqual(self.use_case._average([{"valid": 1}, {"valid": 1}, {"valid": 1}, {"valid": 1}] + [{"valid": 0}] * 26, "valid"), 0.133333)
        ideal_rows = [row for row in self.metrics if row["trial_type"] == "ideal" and row["decision_interface"] == "rule_based"]
        self.assertTrue(ideal_rows)
        self.assertTrue(all(float(row["r_ideal"]) == 1.0 for row in ideal_rows))

    def test_m4_feasibility_constrained_generation_is_deterministic(self) -> None:
        config = self.use_case._resolve_run_config(
            root_seed=114, split="test", trial_count=1, scenario_count=1,
            risk_f_beta=2.0, risk_threshold=0.82, scenario_alpha=2.0,
            scenario_beta=2.0, rho=0.55, hotspot_selection="top_capacity_quartile",
        )
        generated: list[tuple[str, str, dict[str, object]]] = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temporary_root:
                run_root = Path(temporary_root)
                topologies = self.use_case._write_m3(run_root)
                scenarios = self.use_case._write_m4(run_root, topologies, config)
                diagnostics = json.loads((run_root / "M4" / "scenario_generation_diagnostics.json").read_text(encoding="utf-8"))
                scenario_rows = [scenario for values in scenarios.values() for scenario in values]
                self.assertEqual(len(scenario_rows), 9)
                self.assertEqual(diagnostics["accepted_scenario_count"], 9)
                self.assertEqual(diagnostics["required_scenario_count"], 9)
                self.assertEqual(diagnostics["accepted_scenario_count"] + 0, len(scenario_rows))
                self.assertTrue(all(row["decision_feasibility_status"] == "feasible" for row in scenario_rows))
                self.assertTrue(all(row["candidate_rejection_count"] >= 0 for row in scenario_rows))
                generated.append((
                    (run_root / "M4" / "scenario_gt.jsonl").read_text(encoding="utf-8"),
                    (run_root / "M4" / "scenario_generation_diagnostics.json").read_text(encoding="utf-8"),
                    diagnostics,
                ))
        self.assertEqual(generated[0][0], generated[1][0])
        self.assertEqual(generated[0][1], generated[1][1])

    def test_m4_candidate_rejections_are_not_formal_scenarios(self) -> None:
        config = self.use_case._resolve_run_config(
            root_seed=114, split="test", trial_count=1, scenario_count=1,
            risk_f_beta=2.0, risk_threshold=0.82, scenario_alpha=2.0,
            scenario_beta=2.0, rho=0.55, hotspot_selection="top_capacity_quartile",
        )
        with tempfile.TemporaryDirectory() as temporary_root:
            run_root = Path(temporary_root)
            topologies = self.use_case._write_m3(run_root)
            scenarios = self.use_case._write_m4(run_root, topologies, config)
            diagnostics = json.loads((run_root / "M4" / "scenario_generation_diagnostics.json").read_text(encoding="utf-8"))
            formal_ids = {scenario["scenario_id"] for values in scenarios.values() for scenario in values}
            generated_ids = {row["scenario_id"] for row in diagnostics["topology_regimes"]}
            self.assertEqual(formal_ids, generated_ids)
            self.assertEqual(
                diagnostics["rejected_candidate_count"],
                sum(int(row["candidate_rejection_count"]) for row in diagnostics["topology_regimes"]),
            )
            self.assertEqual(len((run_root / "M4" / "scenario_gt.jsonl").read_text(encoding="utf-8").splitlines()), 9)

    def test_validator_post_state_detects_multi_source_capacity(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "B": 10, "C": 10},
            "source_nodes": ["A", "B", "C"],
            "adjacency": {"A": ["B"], "B": [], "C": ["B"]},
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"]},
        }
        result = self.use_case._validate_actions(
            topology,
            {"A": 10, "B": 0, "C": 10},
            [
                {"source_id": "A", "target_id": "B", "move_count": 6},
                {"source_id": "C", "target_id": "B", "move_count": 6},
            ],
        )
        self.assertTrue(result["capacity_violation"])
        self.assertEqual(result["post_population"]["B"], 12)
        self.assertFalse(result["source_underflow_violation"])
        self.assertFalse(result["flow_conservation_violation"])

    def test_validator_checks_non_source_target_capacity(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "X": 5},
            "source_nodes": ["A"],
            "adjacency": {"A": ["X"]},
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"]},
        }
        result = self.use_case._validate_actions(
            topology,
            {"A": 10, "X": 0},
            [{"source_id": "A", "target_id": "X", "move_count": 6}],
        )
        self.assertTrue(result["capacity_violation"])
        self.assertEqual(result["post_population"]["X"], 6)

    def test_m6_coordinates_shared_capacity_and_spills_by_priority(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "B": 4, "C": 10, "D": 20},
            "source_nodes": ["A", "B", "C", "D"],
            "adjacency": {"A": ["B", "D"], "B": [], "C": ["B", "D"], "D": []},
            "edges": [
                {"source_id": "A", "target_id": "B", "edge_cost": 1, "traversal_cost": 0},
                {"source_id": "A", "target_id": "D", "edge_cost": 3, "traversal_cost": 0},
                {"source_id": "C", "target_id": "B", "edge_cost": 1, "traversal_cost": 0},
                {"source_id": "C", "target_id": "D", "edge_cost": 3, "traversal_cost": 0},
            ],
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"], "priority_rule": "ascending_total_cost"},
        }
        actions = self.use_case._decide_actions(topology, {"A": 10, "B": 0, "C": 10, "D": 0}, 0.82)
        allocations = {(row["source_id"], row["target_id"]): row["move_count"] for row in actions}
        self.assertEqual(allocations[("A", "B")], 3)
        self.assertEqual(allocations[("C", "B")], 1)
        self.assertEqual(allocations[("C", "D")], 2)
        self.assertEqual(sum(row["move_count"] for row in actions if row["target_id"] == "B"), 4)
        self.assertTrue(all("requested_quantity" in row["priority_metadata"] for row in actions))
        self.assertTrue(all("allocation_order" in row["priority_metadata"] for row in actions))
        repeated = self.use_case._decide_actions(topology, {"A": 10, "B": 0, "C": 10, "D": 0}, 0.82)
        self.assertEqual(self.use_case._object_checksum(actions), self.use_case._object_checksum(repeated))

    def test_m4_feasibility_rejects_unallocated_ideal_request(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "B": 1},
            "source_nodes": ["A", "B"],
            "adjacency": {"A": ["B"], "B": []},
            "edges": [{"source_id": "A", "target_id": "B", "edge_cost": 1, "traversal_cost": 0}],
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"]},
        }
        population = {"A": 10, "B": 0}
        actions = self.use_case._decide_actions(topology, population, 0.82)
        feasibility = self.use_case._assess_plan_feasibility(topology, population, actions, 0.82)
        self.assertEqual(feasibility["status"], "infeasible")
        self.assertTrue(any(reason["code"] == "unallocated_source_request" for reason in feasibility["reasons"]))

    def test_feasible_ideal_plan_passes_independent_validator(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "B": 3},
            "source_nodes": ["A", "B"],
            "adjacency": {"A": ["B"], "B": []},
            "edges": [{"source_id": "A", "target_id": "B", "edge_cost": 1, "traversal_cost": 0}],
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"]},
        }
        population = {"A": 10, "B": 0}
        actions = self.use_case._decide_actions(topology, population, 0.82)
        feasibility = self.use_case._assess_plan_feasibility(topology, population, actions, 0.82)
        validation = self.use_case._validate_actions(topology, population, actions)
        self.assertEqual(feasibility["status"], "feasible")
        self.assertTrue(validation["valid"])

    def test_m7_records_observation_input_but_m4_truth(self) -> None:
        topology = {
            "capacity_by_node": {"A": 10, "B": 20},
            "source_nodes": ["A", "B"],
            "adjacency": {"A": ["B"], "B": []},
            "edges": [{"source_id": "A", "target_id": "B", "edge_cost": 1, "traversal_cost": 0}],
            "external_exits": [],
            "rules": {"allowed_node_types_as_destination": ["zone"]},
        }
        condition = {"condition_id": "test__model", "topology_id": "test", "topology_name": "Test", "model_id": "model", "model_name": "Model", "paradigm": "density"}
        scenario = {"scenario_id": "scenario-1", "scenario_checksum": "gt-checksum", "scenario_gt_population": {"A": 10, "B": 0}}
        config = self.use_case._resolve_run_config(
            root_seed=114, split="test", trial_count=1, scenario_count=1,
            risk_f_beta=2.0, risk_threshold=0.82, scenario_alpha=2.0,
            scenario_beta=2.0, rho=0.55, hotspot_selection="top_capacity_quartile",
        )
        record = self.use_case._evaluate_trial(
            condition=condition,
            topology=topology,
            regime="HIGH",
            config=config,
            pair_id="pair-1",
            trial_id="trial-1",
            trial_index=0,
            trial_type="deployment",
            framework="w/ Two-stage framework",
            scenario=scenario,
            actions=[{"source_id": "A", "target_id": "B", "move_count": 11, "priority_metadata": {}}],
            ideal_actions=[],
            observation_checksum="observation-checksum",
        )
        self.assertEqual(record["decision_input_mode"], "observation")
        self.assertEqual(record["decision_input_checksum"], "observation-checksum")
        self.assertEqual(record["validation_truth_source_stage_id"], "M4")
        self.assertEqual(record["validation_truth_checksum"], "gt-checksum")
        self.assertTrue(record["source_underflow_violation"])

    def test_ui_baseline_source_keeps_consistency_guard(self) -> None:
        source = (ROOT / "frontend" / "src" / "app" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("baselineIssues", source)
        self.assertIn("sameMetric(reference.r_ideal, row.r_ideal)", source)
        self.assertIn("topology × regime", source)


if __name__ == "__main__":
    unittest.main()
