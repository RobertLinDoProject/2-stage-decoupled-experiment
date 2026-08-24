from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from two_stage.api.routers.decoupled_experiment import RunRequest
from two_stage.application.use_cases.decoupled_2_stage_experiment import Decoupled2StageExperimentUseCase
from two_stage.settings import Settings


ROOT = Path(__file__).resolve().parents[4]


class ScenarioConfigurationLimitTests(unittest.TestCase):
    def test_api_accepts_three_hundred_scenarios_and_rejects_three_hundred_one(self) -> None:
        request = RunRequest(scenarios_per_regime=300, trial_count_per_condition=300)
        self.assertEqual(request.scenarios_per_regime, 300)
        with self.assertRaises(ValidationError):
            RunRequest(scenarios_per_regime=301)

    def test_resolved_run_config_uses_three_hundred_scenario_upper_bound(self) -> None:
        use_case = Decoupled2StageExperimentUseCase(Settings(
            app_env="test",
            local_artifact_root=str(ROOT / "storage"),
            current_project_data_root=str(ROOT / "data"),
            current_project_config_root=str(ROOT / "configs"),
            live_gai_provider_enabled=False,
        ))
        config = use_case._resolve_run_config(
            root_seed=114,
            split="test",
            trial_count=300,
            scenario_count=300,
            risk_f_beta=2.0,
            risk_threshold=0.82,
            scenario_alpha=2.0,
            scenario_beta=2.0,
            rho=0.55,
            hotspot_selection="top_capacity_quartile",
        )
        self.assertEqual(config.scenarios_per_regime, 300)
        with self.assertRaises(ValueError):
            use_case._resolve_run_config(
                root_seed=114,
                split="test",
                trial_count=300,
                scenario_count=301,
                risk_f_beta=2.0,
                risk_threshold=0.82,
                scenario_alpha=2.0,
                scenario_beta=2.0,
                rho=0.55,
                hotspot_selection="top_capacity_quartile",
            )


if __name__ == "__main__":
    unittest.main()
