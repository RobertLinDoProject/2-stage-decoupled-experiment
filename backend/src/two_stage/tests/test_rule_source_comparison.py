from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    COMPARISON_PROFILE_ID,
    RULE_SOURCE_AI,
    RULE_SOURCE_HUMAN,
    Decoupled2StageExperimentUseCase,
)
from two_stage.settings import Settings


ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = Path("data")
AI_ROOT = DATA_ROOT / "Topology資料" / "AI生成"


class RuleSourceComparisonTests(unittest.TestCase):
    def _use_case(self, artifact_root: Path) -> Decoupled2StageExperimentUseCase:
        return Decoupled2StageExperimentUseCase(Settings(
            app_env="test",
            local_artifact_root=str(artifact_root),
            current_project_data_root=str(ROOT / "data"),
            current_project_config_root=str(ROOT / "configs"),
            live_gai_provider_enabled=False,
        ))

    def test_gai_decision_context_is_deterministic_and_uses_existing_m6_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))
            topology = {
                "capacity_by_node": {"1": 100, "2": 100, "E1": 999},
                "source_nodes": ["1", "2"],
                "external_exits": ["E1"],
                "rules": {
                    "allowed_node_types_as_destination": ["zone", "exit"],
                    "priority_rule": "ascending_total_cost",
                },
                "nodes": [
                    {"id": "1", "node_type": "zone"},
                    {"id": "2", "node_type": "zone"},
                    {"id": "E1", "node_type": "exit"},
                ],
                "edges": [
                    {"source_id": "1", "target_id": "2", "edge_cost": 5, "traversal_cost": 2},
                    {"source_id": "1", "target_id": "E1", "edge_cost": 10, "traversal_cost": 2},
                    {"source_id": "2", "target_id": "E1", "edge_cost": 1, "traversal_cost": 3},
                ],
                "adjacency": {"1": ["2", "E1"], "2": ["E1"], "E1": []},
            }
            population = {"1": 90, "2": 10, "E1": 0}
            first = use_case._build_gai_decision_context(
                topology=topology,
                decision_population=population,
                risk_threshold=0.82,
            )
            second = use_case._build_gai_decision_context(
                topology=topology,
                decision_population=population,
                risk_threshold=0.82,
            )

        self.assertEqual(first, second)
        self.assertEqual(first["context_version"], "m6_gai_decision_context_v1")
        self.assertEqual(first["context_checksum"], second["context_checksum"])
        self.assertEqual(first["source_priority_order"], ["1"])
        self.assertEqual(first["requested_move_count"]["1"], 20)
        self.assertEqual(first["source_max_outgoing"]["1"], 90)
        self.assertEqual(
            [candidate["target_id"] for candidate in first["legal_target_candidates"]["1"]],
            ["2", "E1"],
        )
        self.assertEqual(first["legal_target_candidates"]["1"][0]["total_cost"], 7)
        context_text = json.dumps(first, ensure_ascii=False, sort_keys=True)
        for forbidden in ("scenario_gt", "m7_validation", "R_ideal", "R_deploy", "Delta_R"):
            self.assertNotIn(forbidden, context_text)

    def test_quota_exhaustion_publishes_partial_without_zero_filling_unfinished_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))

            def quota_result(**_: object) -> dict[str, object]:
                use_case._quota_exhausted = True
                use_case._quota_details = {
                    "quota_error_code": "OPENAI_QUOTA_EXHAUSTED",
                    "quota_error_message": "test quota",
                }
                return {
                    "status": "quota_exhausted",
                    "decision_output_status": "quota_exhausted",
                    "actions": [],
                    "input_checksum": "sha256:test",
                    "provider": "openai",
                    "trace": [{"status": "quota_exhausted", "external_call_attempted": True}],
                }

            with patch.object(use_case, "_comparison_gai_decision", side_effect=quota_result):
                result = use_case.run(
                    trial_count_per_condition=1,
                    scenarios_per_regime=1,
                    root_seed=901,
                    rule_source_ids=[RULE_SOURCE_HUMAN],
                    selected_interfaces=["gai"],
                    enforce_gai_budget=False,
                )

            self.assertEqual(result["status"], "PARTIAL_QUOTA_EXHAUSTED")
            self.assertTrue(result["partial"])
            self.assertTrue((Path(temporary_root) / "published" / "runs" / result["run_id"] / "M9" / "partial_publication.json").is_file())
            self.assertTrue(all(row["r_ideal"] is None for row in result["metrics"]))
            self.assertTrue(all(row["r_deploy"] is None for row in result["metrics"]))
            self.assertTrue(all(row["executed_trial_count"] == 0 for row in result["metrics"]))

    def test_ai_materialized_packages_pass_contract_and_preserve_source_provenance(self) -> None:
        manifest = json.loads((AI_ROOT / "topology_input_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["rule_source_id"], RULE_SOURCE_AI)
        topology_ids = {str(item["topology_id"]) for item in manifest["topologies"]}
        self.assertEqual(topology_ids, {"fcu", "Taichung Lantern Festival", "Taipei New Year's Eve"})
        self.assertTrue(all(item["validation"]["status"] == "PASSED" for item in manifest["topologies"]))

    def test_comparison_has_eight_rows_per_base_condition_and_unavailable_gai(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=8093,
                rule_source_ids=[RULE_SOURCE_HUMAN, RULE_SOURCE_AI],
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["profile_id"], COMPARISON_PROFILE_ID)
            self.assertEqual(result["condition_count"], 30)
            metrics = result["metrics"]
            grouped: dict[str, list[dict[str, object]]] = {}
            for row in metrics:
                grouped.setdefault(f"{row['base_condition_id']}__{row['ground_truth_regime']}", []).append(row)
            self.assertEqual(len(grouped), 45)
            self.assertTrue(all(len(rows) == 8 for rows in grouped.values()))
            self.assertTrue(all({row["trial_type"] for row in rows} == {"ideal", "deployment"} for rows in grouped.values()))
            self.assertEqual({row["availability"] for row in metrics}, {"available", "unavailable"})
            self.assertEqual(sum(row["availability"] == "available" for row in metrics), 180)
            self.assertEqual(sum(row["availability"] == "unavailable" for row in metrics), 180)
            self.assertEqual({row["validation_rule_source_id"] for row in metrics}, {RULE_SOURCE_HUMAN})
            self.assertEqual({row["rule_source_id"] for row in metrics}, {RULE_SOURCE_HUMAN, RULE_SOURCE_AI})
            self.assertEqual({row["decision_interface"] for row in metrics}, {"rule_based", "gai"})
            self.assertEqual({row["framework_condition"] for row in metrics}, {"w/ Two-stage framework", "w/o Two-stage framework"})
            human_rule_based = [
                row for row in metrics
                if row["rule_source_id"] == RULE_SOURCE_HUMAN
                and row["decision_interface"] == "rule_based"
            ]
            self.assertTrue(all(row["r_ideal"] == 1.0 for row in human_rule_based))
            unavailable = [row for row in metrics if row["availability"] == "unavailable"]
            self.assertTrue(all(row["r_ideal"] is None and row["r_deploy"] is None and row["delta_r"] is None for row in unavailable))
            self.assertEqual(result["gai"]["status"], "unavailable")
            self.assertEqual(result["gai"]["external_call_count"], 0)
            run_root = Path(temporary_root) / "published" / "runs" / result["run_id"]
            insight_report = run_root / "M9" / "insight_report.md"
            insight_summary = run_root / "M9" / "insight_summary.json"
            self.assertTrue(insight_report.is_file())
            self.assertTrue(insight_summary.is_file())
            insight = json.loads(insight_summary.read_text(encoding="utf-8"))
            self.assertEqual(insight["schema_version"], "decoupled_2_stage_insight_v1")
            self.assertEqual(insight["publication_checks"]["m8_metric_row_count"], len(metrics))
            self.assertTrue(insight["publication_checks"]["m8_m9_row_count_match"])
            self.assertIn("# Decoupled 2-Stage Insight Report", insight_report.read_text(encoding="utf-8"))
            self.assertIn("M9/insight_report.md", {artifact["path"] for artifact in result["artifacts"]})

    def test_scenario_and_observation_cohorts_are_shared_across_rule_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=8094,
                rule_source_ids=[RULE_SOURCE_HUMAN, RULE_SOURCE_AI],
            )
            run_root = Path(temporary_root) / "published" / "runs" / result["run_id"]
            scenario_rows = [json.loads(line) for line in (run_root / "M4" / "scenario_gt.jsonl").read_text(encoding="utf-8").splitlines() if line]
            self.assertEqual(len(scenario_rows), 9)
            diagnostics = json.loads((run_root / "M4" / "scenario_generation_diagnostics.json").read_text(encoding="utf-8"))
            self.assertTrue(diagnostics["feasibility_constrained_sampling"])
            self.assertEqual(diagnostics["feasibility_oracle_version"], "capacity_aware_multi_source_feasibility_oracle_v1")
            self.assertTrue(all(row["decision_feasibility_status"] == "feasible" for row in scenario_rows))
            observations = result["metrics"]
            observation_rows = [row for row in observations if row["availability"] == "available"]
            self.assertEqual(len(observation_rows), 180)
            self.assertEqual({row["validation_rule_source_id"] for row in observation_rows}, {RULE_SOURCE_HUMAN})
            m3 = json.loads((run_root / "M3" / "topology_manifest.json").read_text(encoding="utf-8"))
            by_topology = {}
            for row in m3["topologies"]:
                by_topology.setdefault(row["topology_id"], {})[row["rule_source_id"]] = row
            for sources in by_topology.values():
                self.assertEqual(sources[RULE_SOURCE_HUMAN]["capacity_checksum"], sources[RULE_SOURCE_AI]["capacity_checksum"])
                self.assertNotEqual(sources[RULE_SOURCE_HUMAN]["topology_checksum"], sources[RULE_SOURCE_AI]["topology_checksum"])

    def test_m6_artifacts_do_not_create_fake_gai_actions_when_provider_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=8095,
                rule_source_ids=[RULE_SOURCE_HUMAN, RULE_SOURCE_AI],
            )
            run_root = Path(temporary_root) / "published" / "runs" / result["run_id"]
            action_lines = (run_root / "M6" / "action_trials.csv").read_text(encoding="utf-8-sig").splitlines()
            self.assertTrue(action_lines)
            self.assertNotIn(",gai,", "\n".join(action_lines).lower())
            trace_lines = (run_root / "M6" / "gai_decision_trace.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertTrue(trace_lines)
            self.assertTrue(all(json.loads(line)["status"] == "unavailable" for line in trace_lines if line))

    def test_selection_filters_limit_rule_based_pilot_without_gai_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                run_purpose="exploratory",
                trial_count_per_condition=5,
                scenarios_per_regime=8,
                root_seed=8110,
                rule_source_ids=[RULE_SOURCE_HUMAN],
                selected_topology_ids=["fcu"],
                selected_model_ids=["csrnet_den_v1"],
                selected_regimes=["HIGH"],
                selected_interfaces=["rule_based"],
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["condition_count"], 1)
            self.assertEqual(result["config"]["selected_topology_ids"], ["fcu"])
            self.assertEqual(result["config"]["selected_model_ids"], ["csrnet_den_v1"])
            self.assertEqual(result["config"]["selected_regimes"], ["HIGH"])
            self.assertEqual(result["config"]["selected_interfaces"], ["rule_based"])
            available = [row for row in result["metrics"] if row["availability"] == "available"]
            self.assertEqual(len(available), 2)
            self.assertEqual({row["executed_trial_count"] for row in available}, {5})

    def test_gai_smoke_scope_is_two_unavailable_requests_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                run_purpose="exploratory",
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=8111,
                rule_source_ids=[RULE_SOURCE_HUMAN],
                selected_topology_ids=["fcu"],
                selected_model_ids=["csrnet_den_v1"],
                selected_regimes=["LOW"],
                selected_interfaces=["gai"],
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["condition_count"], 1)
            self.assertEqual(result["gai"]["request_count"], 2)
            self.assertEqual(result["gai"]["failed_request_count"], 2)
            self.assertTrue(all(row["availability"] == "unavailable" for row in result["metrics"]))
            self.assertTrue(all(row["r_ideal"] is None for row in result["metrics"]))

    def test_live_gai_ideal_episode_is_shared_across_selected_models(self) -> None:
        calls: list[str] = []

        def fake_gai_decision(use_case, *, trial_id, **kwargs):
            del use_case, kwargs
            calls.append(trial_id)
            return {
                "status": "parsed",
                "decision_output_status": "no_action_required",
                "actions": [],
                "input_checksum": f"input-{trial_id}",
                "provider": "ollama",
                "trace": [{
                    "trial_id": trial_id,
                    "status": "no_action_required",
                    "decision_output_status": "no_action_required",
                    "external_call_attempted": True,
                }],
            }

        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=str(Path(temporary_root)),
                current_project_data_root=str(ROOT / "data"),
                current_project_config_root=str(ROOT / "configs"),
                live_gai_provider_enabled=True,
                gai_execution_mode="live",
                gai_provider_name="ollama",
                gai_provider_endpoint="http://localhost:11434/api/chat",
                gai_provider_model="mistral:7b-instruct-v0.3-q4_K_M",
            )
            use_case = Decoupled2StageExperimentUseCase(settings)
            with patch.object(Decoupled2StageExperimentUseCase, "_comparison_gai_decision", new=fake_gai_decision):
                result = use_case.run(
                    run_purpose="exploratory",
                    trial_count_per_condition=1,
                    scenarios_per_regime=1,
                    root_seed=8118,
                    rule_source_ids=[RULE_SOURCE_HUMAN],
                    selected_topology_ids=["fcu"],
                    selected_model_ids=["csrnet_den_v1", "mcnn_den_v1"],
                    selected_regimes=["LOW"],
                    selected_interfaces=["gai"],
                )

        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(item.endswith("::ideal") for item in calls), 1)
        self.assertEqual(sum(item.endswith("::deployment") for item in calls), 2)
        ideal_rows = [row for row in result["metrics"] if row["trial_type"] == "ideal"]
        self.assertEqual(len(ideal_rows), 2)
        self.assertEqual(len({row["r_ideal"] for row in ideal_rows}), 1)
        self.assertEqual({row["ideal_baseline_scope"] for row in ideal_rows}, {
            "rule_source × decision_interface × topology × regime; model_id excluded"
        })
        self.assertEqual({row["ideal_action_scope"] for row in ideal_rows}, {
            "shared_rule_source_topology_regime_trial"
        })

    def test_gai_invalid_output_is_terminal_valid_zero_and_published_as_metrics(self) -> None:
        def fake_gai_decision(use_case, *, trial_id, **kwargs):
            del use_case, kwargs
            is_ideal = trial_id.endswith("::ideal")
            status = "invalid_output" if is_ideal else "parsed"
            decision_output_status = status
            return {
                "status": "parsed",
                "decision_output_status": decision_output_status,
                "actions": [],
                "input_checksum": "test-input-checksum",
                "provider": "ollama",
                "trace": [{
                    "status": status,
                    "decision_output_status": decision_output_status,
                    "error_code": "TEST_CONTRACT_FAILURE" if is_ideal else None,
                }],
            }

        with tempfile.TemporaryDirectory() as temporary_root:
            with patch.object(Decoupled2StageExperimentUseCase, "_comparison_gai_decision", new=fake_gai_decision):
                result = self._use_case(Path(temporary_root)).run(
                    run_purpose="exploratory",
                    trial_count_per_condition=1,
                    scenarios_per_regime=1,
                    root_seed=8117,
                    rule_source_ids=[RULE_SOURCE_HUMAN],
                    selected_topology_ids=["fcu"],
                    selected_model_ids=["csrnet_den_v1"],
                    selected_regimes=["LOW"],
                    selected_interfaces=["gai"],
                )

        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertTrue(all(row["availability"] == "available" for row in result["metrics"]))
        ideal = next(row for row in result["metrics"] if row["trial_type"] == "ideal")
        deployment = next(row for row in result["metrics"] if row["trial_type"] == "deployment")
        self.assertEqual(ideal["execution_outcome_status"], "invalid_output")
        self.assertEqual(ideal["executed_trial_count"], 1)
        self.assertEqual(ideal["r_ideal"], 0.0)
        self.assertEqual(ideal["m6_contract_violation_rate"], 1.0)
        self.assertEqual(deployment["execution_outcome_status"], "available")
        self.assertEqual(deployment["executed_trial_count"], 1)
        self.assertEqual(deployment["invalid_output_rate"], 0.0)

    def test_gai_decision_infeasible_is_terminal_without_mislabeling_m7_rule_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))
            topology = {
                "capacity_by_node": {"A": 100, "B": 100},
                "source_nodes": ["A", "B"],
                "external_exits": [],
                "adjacency": {"A": [], "B": []},
                "rules": {"allowed_node_types_as_destination": ["zone"]},
            }
            condition = {
                "condition_id": "test__model",
                "topology_id": "test",
                "topology_name": "Test",
                "model_id": "model",
                "model_name": "Model",
                "paradigm": "density",
                "rule_source_id": RULE_SOURCE_HUMAN,
                "rule_source_label": "人工規則",
            }
            scenario = {
                "scenario_id": "scenario-1",
                "scenario_checksum": "gt-checksum",
                "scenario_gt_population": {"A": 90, "B": 0},
            }
            config = use_case._resolve_run_config(
                root_seed=114, split="test", trial_count=1, scenario_count=1,
                risk_f_beta=2.0, risk_threshold=0.82, scenario_alpha=2.0,
                scenario_beta=2.0, rho=0.55, hotspot_selection="top_capacity_quartile",
            )
            record = use_case._evaluate_trial(
                condition=condition,
                topology=topology,
                regime="LOW",
                config=config,
                pair_id="pair-1",
                trial_id="trial-1",
                trial_index=0,
                trial_type="ideal",
                framework="w/o Two-stage framework",
                scenario=scenario,
                actions=[],
                ideal_actions=[],
                observation_checksum=None,
                decision_interface="gai",
                decision_output_status="decision_infeasible",
            )
        self.assertEqual(record["valid"], 0.0)
        self.assertEqual(record["invalid_output"], 0.0)
        self.assertEqual(record["m6_decision_infeasible"], 1.0)
        self.assertEqual(record["m6_contract_violation"], 0.0)
        self.assertEqual(record["rule_violation"], 0.0)
        self.assertIn("m6_decision_infeasible", record["violation_reasons"])

    def test_formal_gai_run_does_not_publish_incomplete_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            with self.assertRaises(ValueError):
                self._use_case(Path(temporary_root)).run(
                    run_purpose="formal",
                    trial_count_per_condition=5,
                    scenarios_per_regime=1,
                    root_seed=8112,
                    rule_source_ids=[RULE_SOURCE_HUMAN],
                    selected_topology_ids=["fcu"],
                    selected_model_ids=["csrnet_den_v1"],
                    selected_regimes=["LOW"],
                    selected_interfaces=["gai"],
                )

    def test_formal_gai_is_rejected_before_run_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))
            with self.assertRaises(ValueError) as raised:
                use_case.run(
                    run_purpose="formal",
                    trial_count_per_condition=30,
                    scenarios_per_regime=1,
                    root_seed=8114,
                    rule_source_ids=[RULE_SOURCE_HUMAN],
                    selected_topology_ids=["fcu"],
                    selected_model_ids=["csrnet_den_v1"],
                    selected_regimes=["LOW"],
                    selected_interfaces=["gai"],
                )
            self.assertIn("rule_based only", str(raised.exception))
            self.assertFalse((Path(temporary_root) / "published" / "runs").exists())

    def test_reserved_gai_does_not_call_provider_even_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=str(Path(temporary_root)),
                current_project_data_root=str(ROOT / "data"),
                current_project_config_root=str(ROOT / "configs"),
                live_gai_provider_enabled=True,
                gai_execution_mode="reserved_unavailable",
                gai_provider_endpoint="https://example.invalid/generateContent",
                gai_provider_api_key="test-secret",
            )
            use_case = Decoupled2StageExperimentUseCase(settings)
            result = use_case.run(
                run_purpose="exploratory",
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=8115,
                rule_source_ids=[RULE_SOURCE_HUMAN],
                selected_topology_ids=["fcu"],
                selected_model_ids=["csrnet_den_v1"],
                selected_regimes=["LOW"],
                selected_interfaces=["gai"],
            )
            self.assertEqual(result["gai"]["status"], "unavailable")
            self.assertFalse(result["gai"]["external_calls_allowed"])
            self.assertEqual(result["gai"]["external_call_count"], 0)
            self.assertEqual(result["gai"]["reserved_unavailable_count"], 2)

    def test_formal_rule_based_comparison_keeps_reserved_gai_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            result = self._use_case(Path(temporary_root)).run(
                run_purpose="formal",
                trial_count_per_condition=30,
                scenarios_per_regime=1,
                root_seed=8116,
                rule_source_ids=[RULE_SOURCE_HUMAN, RULE_SOURCE_AI],
                selected_topology_ids=["fcu"],
                selected_model_ids=["csrnet_den_v1"],
                selected_regimes=["LOW"],
                selected_interfaces=["rule_based"],
            )
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(len(result["metrics"]), 8)
            self.assertEqual(
                {row["decision_interface"] for row in result["metrics"]},
                {"rule_based", "gai"},
            )
            self.assertTrue(all(
                row["availability"] == "unavailable"
                for row in result["metrics"]
                if row["decision_interface"] == "gai"
            ))

    def test_read_only_preflight_checks_pools_and_sends_no_gai_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            report = self._use_case(Path(temporary_root)).preflight(
                run_purpose="exploratory",
                trial_count_per_condition=5,
                scenarios_per_regime=8,
                root_seed=8113,
                rule_source_ids=[RULE_SOURCE_HUMAN],
                selected_interfaces=["rule_based"],
            )
        self.assertEqual(report["status"], "PASSED")
        self.assertEqual(report["gai"]["calls_sent"], 0)
        self.assertEqual(report["planned_counts"]["gai_logical_calls"], 0)
        self.assertEqual(len(report["residual_pools"]), 15)
        self.assertTrue(all(row["status"] == "PASSED" for row in report["residual_pools"]))
        self.assertEqual(
            report["observation_reproducibility"]["count"],
            5 * 3 * 3,
        )

    def test_gai_action_step_bound_counts_target_capacity_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))
            topology = {
                "capacity_by_node": {"S": 100, "A": 10, "B": 10},
                "source_nodes": ["S"],
                "external_exits": [],
                "rules": {"allowed_node_types_as_destination": ["zone"]},
                "nodes": [
                    {"id": "S", "node_type": "zone"},
                    {"id": "A", "node_type": "zone"},
                    {"id": "B", "node_type": "zone"},
                ],
                "edges": [
                    {"source_id": "S", "target_id": "A", "edge_cost": 1, "traversal_cost": 0},
                    {"source_id": "S", "target_id": "B", "edge_cost": 2, "traversal_cost": 0},
                ],
                "adjacency": {"S": ["A", "B"], "A": [], "B": []},
            }
            estimate = use_case._estimate_gai_action_steps(
                topology,
                {"S": 100, "A": 0, "B": 0},
                0.82,
            )

        self.assertEqual(estimate["high_risk_source_count"], 1)
        self.assertEqual(estimate["upper_bound"], 2)
        self.assertGreater(estimate["upper_bound"], estimate["high_risk_source_count"])
        self.assertEqual(estimate["by_source"][0]["candidate_target_ids"], ["A", "B"])

    def test_live_gai_preflight_uses_action_step_upper_bound_and_sends_zero_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=temporary_root,
                current_project_data_root=str(ROOT / "data"),
                current_project_config_root=str(ROOT / "configs"),
                live_gai_provider_enabled=True,
                gai_execution_mode="live",
                gai_provider_name="ollama",
                gai_provider_endpoint="http://host.docker.internal:11434/api/chat",
                gai_provider_model="mistral:7b-instruct-v0.3-q4_K_M",
                gai_budget_max_requests_per_run=1,
            )
            report = Decoupled2StageExperimentUseCase(settings).preflight(
                run_purpose="exploratory",
                trial_count_per_condition=1,
                scenarios_per_regime=1,
                root_seed=114,
                rule_source_ids=[RULE_SOURCE_HUMAN],
                selected_topology_ids=["fcu"],
                selected_model_ids=["csrnet_den_v1"],
                selected_regimes=["LOW"],
                selected_interfaces=["gai"],
            )

        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["gai"]["calls_sent"], 0)
        self.assertFalse(report["gai"]["budget_sufficient"])
        self.assertGreater(report["gai"]["planned_calls"], 1)
        self.assertEqual(
            report["planned_counts"]["gai_action_step_upper_bound"],
            report["gai"]["planned_calls"],
        )

    def test_live_gai_budget_gate_rejects_before_run_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=temporary_root,
                current_project_data_root=str(ROOT / "data"),
                current_project_config_root=str(ROOT / "configs"),
                live_gai_provider_enabled=True,
                gai_execution_mode="live",
                gai_provider_name="ollama",
                gai_provider_endpoint="http://host.docker.internal:11434/api/chat",
                gai_provider_model="mistral:7b-instruct-v0.3-q4_K_M",
                gai_budget_max_requests_per_run=1,
            )
            use_case = Decoupled2StageExperimentUseCase(settings)
            with patch.object(
                use_case,
                "preflight",
                return_value={
                    "status": "FAILED",
                    "gai": {
                        "planned_calls": 4,
                        "budget_max_requests_per_run": 1,
                        "budget_sufficient": False,
                    },
                },
            ):
                with self.assertRaises(ValueError):
                    use_case.run(
                        trial_count_per_condition=1,
                        scenarios_per_regime=1,
                        root_seed=114,
                        rule_source_ids=[RULE_SOURCE_HUMAN],
                        selected_topology_ids=["fcu"],
                        selected_model_ids=["csrnet_den_v1"],
                        selected_regimes=["LOW"],
                        selected_interfaces=["gai"],
                    )
            self.assertEqual(list(Path(temporary_root).iterdir()), [])

    def test_run_progress_json_is_written_as_a_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            use_case = self._use_case(Path(temporary_root))
            run_root = Path(temporary_root) / "run"
            run_root.mkdir()
            use_case._write_progress(
                run_root,
                status="RUNNING",
                stage_id="QUEUED",
                message="Run queued for background execution.",
                config={"profile_id": COMPARISON_PROFILE_ID},
            )

            progress_path = run_root / "run_progress.json"
            payload = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "RUNNING")
            self.assertEqual(payload["stage_id"], "QUEUED")
            self.assertEqual(payload["config"]["profile_id"], COMPARISON_PROFILE_ID)


if __name__ == "__main__":
    unittest.main()
