from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from two_stage.application.services.run_execution import RunExecutionManager
from two_stage.settings import Settings


class RunExecutionStatusTests(unittest.TestCase):
    def test_stale_worker_run_is_marked_interrupted_and_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=temporary_root,
                current_project_data_root=temporary_root,
                current_project_config_root=temporary_root,
                live_gai_provider_enabled=False,
            )
            run_id = "run-stale-status-test"
            updated_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
            payload = {
                "run_id": run_id,
                "status": "RUNNING",
                "stage_id": "M6",
                "message": "worker was running",
                "updated_at": updated_at,
            }
            manager = RunExecutionManager()

            result = manager.reconcile_stale_run(settings, payload)

            self.assertEqual(result["status"], "INTERRUPTED_RESUMABLE")
            self.assertIn("resumable", result["message"].lower())
            progress_path = Path(temporary_root) / "published" / "runs" / run_id / "run_progress.json"
            self.assertTrue(progress_path.is_file())
            self.assertEqual(json.loads(progress_path.read_text(encoding="utf-8"))["status"], "INTERRUPTED_RESUMABLE")

    def test_recent_run_is_not_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_root:
            settings = Settings(
                app_env="test",
                local_artifact_root=temporary_root,
                current_project_data_root=temporary_root,
                current_project_config_root=temporary_root,
                live_gai_provider_enabled=False,
            )
            payload = {
                "run_id": "run-recent-status-test",
                "status": "RUNNING",
                "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }

            result = RunExecutionManager().reconcile_stale_run(settings, payload)

            self.assertEqual(result["status"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
