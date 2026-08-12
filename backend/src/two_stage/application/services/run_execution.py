from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    COMPARISON_PROFILE_ID,
    RULE_SOURCE_HUMAN,
    Decoupled2StageExperimentUseCase,
    ExperimentFailure,
)
from two_stage.settings import Settings


@dataclass
class RunJob:
    run_id: str
    cancel_event: threading.Event
    future: Future[Any]


class RunExecutionManager:
    """Serial background executor for long local-model experiment runs.

    The experiment use case remains the single orchestration path. This class
    only owns HTTP lifetime, cooperative cancellation and run-id handoff.
    """

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="decoupled-run")
        self._jobs: dict[str, RunJob] = {}
        self._boundary_jobs: dict[str, RunJob] = {}
        self._lock = threading.Lock()

    def _is_active(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
        return job is not None and not job.future.done()

    def reconcile_stale_run(self, settings: Settings, payload: dict[str, Any], *, stale_after_seconds: int = 10) -> dict[str, Any]:
        """Expose interrupted worker state instead of leaving a stale RUNNING status."""
        run_id = str(payload.get("run_id", ""))
        status = str(payload.get("status", ""))
        if not run_id or status not in {"RUNNING", "QUEUED", "PREFLIGHT", "FREEZING_INPUTS"} or self._is_active(run_id):
            return payload

        updated_at = str(payload.get("updated_at", ""))
        try:
            last_update = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if datetime.now(UTC) - last_update < timedelta(seconds=stale_after_seconds):
                return payload
        except ValueError:
            return payload

        service = Decoupled2StageExperimentUseCase(settings)
        interrupted = {
            **payload,
            "status": "INTERRUPTED_RESUMABLE",
            "message": "API worker no longer owns this Run. The Run is resumable and no new result was published.",
            "interrupted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        service._write_json(service._run_root(run_id) / "run_progress.json", interrupted)
        return interrupted

    def submit_boundary(self, settings: Settings, run_id: str, request: dict[str, Any]) -> dict[str, Any]:
        """Queue a read-only Boundary Sweep on the same serial worker."""
        from two_stage.application.services.perception_error_boundary import PerceptionErrorBoundaryService

        service = PerceptionErrorBoundaryService(settings)
        created = service.create_boundary_job(run_id, request)
        job_id = str(created["job_id"])
        cancel_event = threading.Event()
        future = self._executor.submit(self._execute_boundary, settings, run_id, job_id, cancel_event)
        with self._lock:
            self._boundary_jobs[job_id] = RunJob(run_id=run_id, cancel_event=cancel_event, future=future)
        return created

    def cancel_boundary(self, job_id: str) -> bool:
        with self._lock:
            job = self._boundary_jobs.get(job_id)
        if job is None or job.future.done():
            return False
        job.cancel_event.set()
        return True

    def resume_boundary(self, settings: Settings, run_id: str, job_id: str) -> bool:
        from two_stage.application.services.perception_error_boundary import PerceptionErrorBoundaryService

        with self._lock:
            current = self._boundary_jobs.get(job_id)
            if current is not None and not current.future.done():
                return False
        service = PerceptionErrorBoundaryService(settings)
        job_root = service.storage_root / run_id / "boundary_analysis" / job_id
        if not job_root.is_dir():
            return False
        cancel_event = threading.Event()
        future = self._executor.submit(self._execute_boundary, settings, run_id, job_id, cancel_event)
        with self._lock:
            self._boundary_jobs[job_id] = RunJob(run_id=run_id, cancel_event=cancel_event, future=future)
        return True

    def _execute_boundary(self, settings: Settings, run_id: str, job_id: str, cancel_event: threading.Event) -> None:
        from two_stage.application.services.perception_error_boundary import BoundaryServiceError, PerceptionErrorBoundaryService

        service = PerceptionErrorBoundaryService(settings)
        try:
            service.run_boundary_job(settings, run_id, job_id, cancel_event.is_set)
        except BoundaryServiceError as exc:
            job_root = service.storage_root / run_id / "boundary_analysis" / job_id
            if job_root.is_dir():
                service._write_job(job_root, {**service._read_job(job_root), "status": "FAILED", "failure_code": exc.code, "message": exc.message, "failure_details": exc.details})
        except Exception as exc:
            job_root = service.storage_root / run_id / "boundary_analysis" / job_id
            if job_root.is_dir():
                service._write_job(job_root, {**service._read_job(job_root), "status": "FAILED", "failure_code": "BOUNDARY_INTERNAL_ERROR", "message": str(exc)})

    def submit(self, settings: Settings, request: dict[str, Any]) -> str:
        service = Decoupled2StageExperimentUseCase(settings)
        budget_report = service.ensure_gai_budget_sufficient_for_request(request)
        if budget_report is not None:
            request = {
                **request,
                "planned_gai_calls": int(budget_report.get("planned_calls", 0) or 0),
                "effective_gai_budget": int(budget_report.get("effective_budget", budget_report.get("budget_max_requests_per_run", 0)) or 0),
                "gai_budget_estimation_method": str(budget_report.get("estimation_method") or "m6_context_candidate_capacity_upper_bound"),
                "gai_budget_hard_limit": int(budget_report.get("budget_hard_limit", settings.gai_budget_hard_limit) or settings.gai_budget_hard_limit),
            }
        comparison = (
            tuple(request.get("rule_source_ids") or (RULE_SOURCE_HUMAN,)) != (RULE_SOURCE_HUMAN,)
            or tuple(request.get("selected_interfaces") or ("rule_based",)) != ("rule_based",)
        )
        split = str(request.get("split", "test"))
        if comparison:
            split = f"{split}-rule-source-comparison"
        run_id = service._make_run_id(
            int(request.get("root_seed", 114)),
            int(request.get("trial_count_per_condition", 30)),
            int(request.get("scenarios_per_regime", 8)),
            split,
        )
        cancel_event = threading.Event()
        run_root = service._run_root(run_id)
        run_root.mkdir(parents=True, exist_ok=False)
        service._write_progress(
            run_root,
            status="RUNNING",
            stage_id="QUEUED",
            message="Run queued for background execution.",
            config={**request, "profile_id": COMPARISON_PROFILE_ID if comparison else "decoupled_2_stage_experiment_v1"},
        )
        future = self._executor.submit(self._execute, settings, request, run_id, cancel_event)
        job = RunJob(run_id=run_id, cancel_event=cancel_event, future=future)
        with self._lock:
            self._jobs[run_id] = job
        return run_id

    def cancel(self, run_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(run_id)
        if job is None or job.future.done():
            return False
        job.cancel_event.set()
        return True

    def resume(self, settings: Settings, run_id: str, request: dict[str, Any]) -> bool:
        """Resume using the frozen request and same run id after a checkpoint stop.

        Stage files are deterministically regenerated into the same run root;
        published M8/M9 outputs are still written only after completion.
        """
        with self._lock:
            existing = self._jobs.get(run_id)
            if existing is not None and not existing.future.done():
                return False
        service = Decoupled2StageExperimentUseCase(settings)
        budget_report = service.ensure_gai_budget_sufficient_for_request(request)
        if budget_report is not None:
            request = {
                **request,
                "planned_gai_calls": int(budget_report.get("planned_calls", 0) or 0),
                "effective_gai_budget": int(budget_report.get("effective_budget", budget_report.get("budget_max_requests_per_run", 0)) or 0),
                "gai_budget_estimation_method": str(budget_report.get("estimation_method") or "m6_context_candidate_capacity_upper_bound"),
                "gai_budget_hard_limit": int(budget_report.get("budget_hard_limit", settings.gai_budget_hard_limit) or settings.gai_budget_hard_limit),
            }
        cancel_event = threading.Event()
        future = self._executor.submit(self._execute, settings, request, run_id, cancel_event)
        with self._lock:
            self._jobs[run_id] = RunJob(run_id=run_id, cancel_event=cancel_event, future=future)
        return True

    def _execute(
        self,
        settings: Settings,
        request: dict[str, Any],
        run_id: str,
        cancel_event: threading.Event,
    ) -> None:
        service = Decoupled2StageExperimentUseCase(settings)
        try:
            service.run(
                run_purpose=str(request.get("run_purpose", "exploratory")),
                trial_count_per_condition=int(request.get("trial_count_per_condition", 30)),
                scenarios_per_regime=int(request.get("scenarios_per_regime", 8)),
                root_seed=int(request.get("root_seed", 114)),
                split=str(request.get("split", "test")),
                risk_f_beta=float(request.get("risk_f_beta", 2.0)),
                risk_threshold=float(request.get("risk_threshold", 0.82)),
                scenario_alpha=float(request.get("scenario_alpha", 2.0)),
                scenario_beta=float(request.get("scenario_beta", 2.0)),
                rho=float(request.get("rho", 0.55)),
                hotspot_selection=str(request.get("hotspot_selection", "top_capacity_quartile")),
                rule_source_ids=list(request.get("rule_source_ids") or [RULE_SOURCE_HUMAN]),
                selected_topology_ids=request.get("selected_topology_ids"),
                selected_model_ids=request.get("selected_model_ids"),
                selected_regimes=request.get("selected_regimes"),
                selected_interfaces=request.get("selected_interfaces"),
                gai_provider=request.get("gai_provider"),
                planned_gai_calls=request.get("planned_gai_calls"),
                effective_gai_budget=request.get("effective_gai_budget"),
                gai_budget_estimation_method=request.get("gai_budget_estimation_method"),
                gai_budget_hard_limit=request.get("gai_budget_hard_limit"),
                run_id=run_id,
                cancel_check=cancel_event.is_set,
                enforce_gai_budget=False,
            )
        except ExperimentFailure:
            return
        except Exception as exc:  # Persist an API-visible terminal state.
            progress_path = service._run_root(run_id) / "run_progress.json"
            service._write_json(progress_path, {
                "run_id": run_id,
                "status": "FAILED",
                "stage_id": "UNKNOWN",
                "message": str(exc),
                "updated_at": service._now(),
                "config": request,
                "gai": service._gai_runtime_payload(service._run_root(run_id)),
            })


run_execution_manager = RunExecutionManager()
