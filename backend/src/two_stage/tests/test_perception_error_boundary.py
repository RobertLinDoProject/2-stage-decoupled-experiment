from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from two_stage.application.services.perception_error_boundary import PerceptionErrorBoundaryService
from two_stage.application.use_cases.decoupled_2_stage_experiment import Decoupled2StageExperimentUseCase
from two_stage.settings import Settings


class PerceptionErrorBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        settings = Settings(
            app_env="test",
            local_artifact_root=self.temp.name,
            current_project_data_root=str(Path(__file__).resolve().parents[4] / "data"),
            current_project_config_root=str(Path(__file__).resolve().parents[4] / "configs"),
            live_gai_provider_enabled=False,
        )
        self.service = PerceptionErrorBoundaryService(settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_alpha_zero_and_one_observation_mapping(self) -> None:
        truth = {"A": 100, "B": 10}
        residuals = {"A": -2.6, "B": 1.4}
        self.assertEqual(self.service._scaled_observation(truth, residuals, 0.0), truth)
        self.assertEqual(self.service._scaled_observation(truth, residuals, 1.0), {"A": 97, "B": 11})

    def test_alpha_points_are_deterministic_rounding_transitions(self) -> None:
        trial = {
            "scenario_gt_population": {"A": 100},
            "sampled_residuals": {"A": -2.6},
        }
        first = self.service._alpha_points([trial])
        second = self.service._alpha_points([trial])
        self.assertEqual(first, second)
        self.assertEqual(first[0], 0.0)
        self.assertEqual(first[-1], 1.0)
        self.assertIn(round(0.5 / 2.6, 12), first)

    def test_analytical_high_risk_boundary(self) -> None:
        topology = {
            "source_nodes": ["A"],
            "capacity_by_node": {"A": 100},
        }
        boundary = self.service._analytical_boundary(
            topology,
            [{"scenario_gt_population": {"A": 90}}],
            0.82,
        )
        source = boundary["sources"][0]
        self.assertEqual(source["first_high_risk_count"], 82)
        self.assertEqual(source["last_non_high_count"], 81)
        self.assertEqual(source["signed_error_boundary"], -9)

    def test_gai_selection_is_unavailable_without_replay(self) -> None:
        run_root = Path(self.temp.name)
        for relative in (
            "M4/scenario_gt.jsonl",
            "M5/observation_trials.parquet",
        ):
            path = run_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        response = self.service._unavailable_gai_response(
            {
                "run_id": "run",
                "rule_source_id": "human_manual_v1",
                "topology_id": "fcu",
                "model_id": "model",
                "regime": "HIGH",
                "decision_interface": "gai",
            },
            {"status": "available", "sources": []},
            1.0,
            0.0,
            run_root,
            [],
        )
        self.assertEqual(response["empirical_boundary"]["status"], "unavailable")
        self.assertIn("GAI replay", response["empirical_boundary"]["unavailable_reason"])
        self.assertFalse(response["formal_artifacts_modified"])

    def test_tolerance_lambda_grid_is_fixed_and_inclusive(self) -> None:
        self.assertEqual(self.service.LAMBDA_GRID[0], 0.0)
        self.assertEqual(self.service.LAMBDA_GRID[-1], 1.0)
        self.assertEqual(len(self.service.LAMBDA_GRID), 21)
        self.assertEqual(self.service.LAMBDA_GRID[1] - self.service.LAMBDA_GRID[0], 0.05)

    def test_error_summary_uses_effective_rounded_residuals(self) -> None:
        summary = self.service._error_summary(
            [1.0, -2.0, 3.0],
            [
                {"effective_residual": {"A": 1, "B": -2}},
                {"effective_residual": {"A": 3}},
            ],
        )
        self.assertEqual(summary["signed_mean"], 0.666667)
        self.assertEqual(summary["mae"], 2.0)
        self.assertEqual(summary["max_absolute_error"], 3.0)
        self.assertEqual(summary["underestimate_rate"], 0.333333)

    def test_safe_boundary_requires_every_prior_lambda_to_pass(self) -> None:
        curve = [
            {"lambda": 0.0, "r_deploy": 1.0, "executed_trial_count": 10},
            {"lambda": 0.05, "r_deploy": 0.9, "executed_trial_count": 10},
            {"lambda": 0.10, "r_deploy": 0.4, "executed_trial_count": 10},
            {"lambda": 0.15, "r_deploy": 0.8, "executed_trial_count": 10},
        ]
        rows = self.service._target_results(curve, 1.0)
        half = next(row for row in rows if row["target"] == "R_deploy >= 0.50")
        self.assertEqual(half["safe_critical_lambda"], 0.05)
        self.assertEqual(half["status"], "REACHED")

    def test_target_above_search_range_and_non_monotonic_audit(self) -> None:
        curve = [
            {"lambda": 0.0, "r_deploy": 1.0, "executed_trial_count": 10},
            {"lambda": 0.05, "r_deploy": 1.0, "executed_trial_count": 10},
            {"lambda": 1.0, "r_deploy": 1.0, "executed_trial_count": 10},
        ]
        rows = self.service._target_results(curve, 1.0)
        target = next(row for row in rows if row["target"] == "R_deploy >= 0.95")
        self.assertEqual(target["status"], "ABOVE_SEARCH_RANGE")
        audit = self.service._monotonicity_audit([
            {"r_deploy": 1.0},
            {"r_deploy": 0.4},
            {"r_deploy": 0.7},
        ])
        self.assertEqual(audit["warning_code"], "NON_MONOTONIC_RELIABILITY_CURVE")

    def test_curve_endpoint_normalizes_csv_values(self) -> None:
        job_root = Path(self.temp.name) / "published" / "runs" / "run" / "boundary_analysis" / "job"
        job_root.mkdir(parents=True, exist_ok=True)
        (job_root / "lambda_curve.csv").write_text(
            "lambda,r_deploy,valid_trial_count,executed_trial_count,violation_trial_count,violation_reason_counts\n"
            "0.05,0.5,5,10,5,{'post_state_capacity': 7}\n",
            encoding="utf-8",
        )
        row = self.service.get_boundary_curve("run", "job")[0]
        self.assertEqual(row["lambda"], 0.05)
        self.assertEqual(row["r_deploy"], 0.5)
        self.assertEqual(row["valid_trial_count"], 5)
        self.assertEqual(row["violation_reason_counts"], {"post_state_capacity": 7})

    def test_focus_curve_stops_at_first_zero_and_keeps_fine_grid(self) -> None:
        curve = [
            {"lambda": 0.0, "r_deploy": 1.0},
            {"lambda": 0.05, "r_deploy": 0.0},
        ]
        def evaluate(lambda_value: float, _data: dict) -> dict:
            return {"lambda": lambda_value, "r_deploy": 0.0 if lambda_value >= 0.02 else 1.0}

        with patch.object(self.service, "_evaluate_lambda", side_effect=evaluate):
            focus, meta = self.service._build_focus_curve(curve, {})
        self.assertEqual([row["lambda"] for row in focus], [0.0, 0.01, 0.02])
        self.assertEqual(meta["status"], "FIRST_ZERO_REACHED")
        self.assertEqual(meta["first_zero_lambda"], 0.02)
        self.assertTrue(meta["complete_curve_retained"])


if __name__ == "__main__":
    unittest.main()
