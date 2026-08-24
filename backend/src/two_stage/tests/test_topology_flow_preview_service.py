from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from two_stage.application.use_cases.decoupled_2_stage_experiment import Decoupled2StageExperimentUseCase
from two_stage.application.services.topology_flow_preview import (
    PreviewServiceError,
    TopologyFlowPreviewService,
)
from two_stage.settings import Settings


ROOT = Path(__file__).resolve().parents[4]
class TopologyFlowPreviewServiceTests(unittest.TestCase):
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
        cls.run_id = result["run_id"]
        cls.run_root = Path(cls.fixture.name) / "published" / "runs" / cls.run_id
        cls.service = TopologyFlowPreviewService(cls.fixture.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture.cleanup()

    def test_list_runs_only_returns_complete_succeeded_runs(self) -> None:
        runs = self.service.list_runs(limit=100)
        self.assertTrue(runs)
        self.assertTrue(all(item["status"] == "SUCCEEDED" for item in runs))
        self.assertTrue(all(item["preview_available"] for item in runs))
        self.assertTrue(all(item["profile_id"] == "decoupled_2_stage_experiment_v1" for item in runs))

    def test_options_expose_topology_model_and_derived_trial_scenarios(self) -> None:
        options = self.service.options(self.run_id)
        self.assertEqual(options["schema_version"], "topology_flow_violation_preview_options_v1")
        self.assertEqual(len(options["conditions"]), 15)
        self.assertEqual({item["topology_id"] for item in options["topologies"]}, {"fcu", "taichung_lantern_festival", "taipei_new_years_eve"})
        self.assertEqual({item["id"] for item in options["interfaces"]}, {"rule_based", "gai_reserved"})
        low_fcu = next(item for item in options["trial_options"] if item["topology_id"] == "fcu" and item["regime"] == "LOW")
        self.assertEqual(len(low_fcu["trials"]), 30)
        self.assertEqual(low_fcu["trials"][0]["scenario_id"], "fcu_low_000")

    def test_preview_preserves_paired_truth_sources_and_is_deterministic(self) -> None:
        first = self.service.preview(
            self.run_id,
            topology_id="fcu",
            model_id="yolov8_det_v1",
            regime="MEDIUM",
            trial_index=0,
        )
        second = self.service.preview(
            self.run_id,
            topology_id="fcu",
            model_id="yolov8_det_v1",
            regime="MEDIUM",
            trial_index=0,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["branches"]["without_two_stage"]["decision_input_type"], "scenario_gt")
        self.assertEqual(first["branches"]["with_two_stage"]["decision_input_type"], "observation")
        self.assertEqual(first["branches"]["without_two_stage"]["validation"]["truth_source_stage_id"], "M4")
        self.assertEqual(first["branches"]["with_two_stage"]["validation"]["truth_source_stage_id"], "M4")
        self.assertEqual(first["metadata"]["scenario_gt_checksum"], first["branches"]["with_two_stage"]["validation"]["truth_checksum"])

    def test_gai_reserved_is_unavailable_without_zero_or_fake_actions(self) -> None:
        payload = self.service.preview(
            self.run_id,
            topology_id="fcu",
            model_id="yolov8_det_v1",
            regime="LOW",
            trial_index=0,
            interface="gai_reserved",
        )
        self.assertEqual(payload["metadata"]["interface_type"], "gai_reserved")
        for branch in payload["branches"].values():
            self.assertEqual(branch["availability"], "unavailable")
            self.assertEqual(branch["actions"], [])
            self.assertIsNone(branch["validation"]["valid"])

    def test_invalid_selection_has_stable_error_code(self) -> None:
        with self.assertRaises(PreviewServiceError) as context:
            self.service.preview(
                self.run_id,
                topology_id="fcu",
                model_id="not_a_model",
                regime="LOW",
                trial_index=0,
            )
        self.assertEqual(context.exception.code, "INVALID_PREVIEW_SELECTION")
        self.assertEqual(context.exception.status_code, 400)

    def test_formal_artifacts_are_not_modified(self) -> None:
        paths = [
            self.run_root / "M4" / "scenario_gt.jsonl",
            self.run_root / "M5" / "observation_trials.csv",
            self.run_root / "M6" / "action_trials.csv",
            self.run_root / "M7" / "decision_validation_trials.csv",
        ]
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        self.service.options(self.run_id)
        self.service.preview(
            self.run_id,
            topology_id="fcu",
            model_id="yolov8_det_v1",
            regime="HIGH",
            trial_index=3,
        )
        after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
