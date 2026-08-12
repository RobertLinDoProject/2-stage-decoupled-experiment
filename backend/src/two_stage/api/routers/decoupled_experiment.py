from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from two_stage.application.use_cases.decoupled_2_stage_experiment import (
    COMPARISON_PROFILE_ID,
    RULE_SOURCE_HUMAN,
    RULE_SOURCE_AI,
    Decoupled2StageExperimentUseCase,
    ExperimentFailure,
)
from two_stage.application.services.topology_flow_preview import PreviewServiceError, TopologyFlowPreviewService
from two_stage.application.services.perception_error_boundary import BoundaryServiceError, PerceptionErrorBoundaryService
from two_stage.settings import get_settings
from two_stage.application.services.run_execution import run_execution_manager

router = APIRouter(prefix="/decoupled-2-stage-experiment")


def _topology_flow_preview_service() -> TopologyFlowPreviewService:
    return TopologyFlowPreviewService(get_settings().local_artifact_root)


def _raise_preview_error(exc: PreviewServiceError) -> None:
    detail = {"code": exc.code, "message": exc.message}
    detail.update(exc.details)
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


class RunRequest(BaseModel):
    run_purpose: Literal["development", "exploratory", "formal"] = "exploratory"
    root_seed: int = Field(default=114, ge=0)
    split: str = Field(default="test", min_length=1)
    trial_count_per_condition: int = Field(default=30, ge=1, le=500)
    scenarios_per_regime: int = Field(default=8, ge=1, le=300)
    risk_f_beta: float = Field(default=2.0, gt=0)
    risk_threshold: float = Field(default=0.82, gt=0, le=1)
    scenario_alpha: float = Field(default=2.0, gt=0)
    scenario_beta: float = Field(default=2.0, gt=0)
    rho: float = Field(default=0.55, gt=0, lt=1)
    hotspot_selection: str = Field(default="top_capacity_quartile", min_length=1)
    rule_source_ids: list[str] = Field(default_factory=lambda: [RULE_SOURCE_HUMAN])
    selected_topology_ids: list[str] | None = None
    selected_model_ids: list[str] | None = None
    selected_regimes: list[str] | None = None
    selected_interfaces: list[str] | None = None
    gai_provider: Literal["ollama", "openai"] = "ollama"


class BoundaryAnalysisRequest(BaseModel):
    rule_source_id: str = Field(min_length=1)
    topology_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    regime: str = Field(min_length=1)
    decision_interface: str = Field(default="rule_based", min_length=1)


@router.get("/metadata")
def metadata() -> dict[str, Any]:
    service = Decoupled2StageExperimentUseCase(get_settings())
    return service.metadata()


def _settings_for_provider(provider: str | None):
    settings = get_settings()
    selected = (provider or settings.gai_provider_name).strip().lower()
    if selected == "openai":
        return replace(
            settings,
            gai_provider_name="openai",
            gai_provider_endpoint=settings.openai_api_endpoint,
            gai_provider_api_key=settings.openai_api_key,
            gai_provider_model=settings.openai_model,
        )
    return replace(settings, gai_provider_name="ollama")


@router.get("/gai/preflight")
def gai_preflight(provider: Literal["ollama", "openai"] | None = Query(default=None)) -> dict[str, Any]:
    """Read-only provider check; never generates an M6 action."""
    settings = _settings_for_provider(provider)
    service = Decoupled2StageExperimentUseCase(settings)
    if service._gai_status() != "configured":
        return {
            "status": "UNAVAILABLE",
            "provider": settings.gai_provider_name,
            "model": settings.gai_provider_model,
            "planned_calls": 0,
            "calls_sent": 0,
            "message": "GAI provider is not configured; no generation request was sent.",
        }
    endpoint = settings.gai_provider_endpoint or ""
    parsed = urlparse(endpoint)
    if settings.gai_provider_name == "ollama":
        if parsed.hostname not in {"localhost", "127.0.0.1", "host.docker.internal"}:
            return {"status": "FAILED", "provider": "ollama", "model": settings.gai_provider_model, "planned_calls": 0, "calls_sent": 0, "message": "Ollama endpoint must be local."}
        version_url = f"{parsed.scheme}://{parsed.netloc}/api/version"
        tags_url = f"{parsed.scheme}://{parsed.netloc}/api/tags"
        try:
            with urllib.request.urlopen(version_url, timeout=5) as response:
                version = json.loads(response.read().decode("utf-8"))
            with urllib.request.urlopen(tags_url, timeout=5) as response:
                tags = json.loads(response.read().decode("utf-8"))
            available_models = [str(item.get("name")) for item in tags.get("models", []) if isinstance(item, dict)]
            model_available = settings.gai_provider_model in available_models
            return {
                "status": "PASSED" if model_available else "FAILED",
                "provider": "ollama",
                "model": settings.gai_provider_model,
                "model_available": model_available,
                "available_models": available_models,
                "ollama_version": version.get("version"),
                "planned_calls": 0,
                "calls_sent": 0,
                "message": "Read-only Ollama version/model check completed; no action was generated.",
            }
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            return {"status": "FAILED", "provider": "ollama", "model": settings.gai_provider_model, "planned_calls": 0, "calls_sent": 0, "message": f"Ollama preflight failed: {exc}"}
    if settings.gai_provider_name == "openai":
        if not settings.openai_api_key:
            return {"status": "FAILED", "provider": "openai", "model": settings.openai_model, "planned_calls": 0, "calls_sent": 0, "message": "OPENAI_API_KEY is not configured on the API server."}
        endpoint = settings.openai_api_endpoint.rstrip("/")
        models_url = endpoint.rsplit("/responses", 1)[0] + "/models/" + urllib.parse.quote(settings.openai_model, safe="")
        request = urllib.request.Request(
            models_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Accept": "application/json"},
            method="GET",
        )
        last_error: Exception | None = None
        for attempt_no in range(2):
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                model_id = str(payload.get("id", "")) if isinstance(payload, dict) else ""
                available = model_id == settings.openai_model
                return {
                    "status": "PASSED" if available else "FAILED",
                    "provider": "openai",
                    "model": settings.openai_model,
                    "model_available": available,
                    "planned_calls": 0,
                    "calls_sent": 0,
                    "message": "Read-only OpenAI model access check completed; no action was generated.",
                }
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt_no == 0:
                    time.sleep(0.5)
        return {
            "status": "FAILED",
            "provider": "openai",
            "model": settings.openai_model,
            "planned_calls": 0,
            "calls_sent": 0,
            "message": f"OpenAI preflight failed after retry: {last_error.__class__.__name__ if last_error else 'UnknownError'}.",
        }
    return {
        "status": "CONFIGURED",
        "provider": settings.gai_provider_name,
        "model": settings.gai_provider_model,
        "planned_calls": 0,
        "calls_sent": 0,
        "message": "Provider configuration is present; this preflight does not generate an action.",
    }


@router.get("/runs")
def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    settings = get_settings()
    service = Decoupled2StageExperimentUseCase(settings)
    return [run_execution_manager.reconcile_stale_run(settings, row) for row in service.list_runs(limit=limit)]


@router.post("/preflight")
def preflight(request: RunRequest) -> dict[str, Any]:
    service = Decoupled2StageExperimentUseCase(get_settings())
    return service.preflight(
        run_purpose=request.run_purpose,
        trial_count_per_condition=request.trial_count_per_condition,
        scenarios_per_regime=request.scenarios_per_regime,
        root_seed=request.root_seed,
        split=request.split,
        risk_f_beta=request.risk_f_beta,
        risk_threshold=request.risk_threshold,
        scenario_alpha=request.scenario_alpha,
        scenario_beta=request.scenario_beta,
        rho=request.rho,
        hotspot_selection=request.hotspot_selection,
        rule_source_ids=request.rule_source_ids,
        selected_topology_ids=request.selected_topology_ids,
        selected_model_ids=request.selected_model_ids,
        selected_regimes=request.selected_regimes,
        selected_interfaces=request.selected_interfaces,
        gai_provider=request.gai_provider,
    )


@router.post("/runs")
def create_run(request: RunRequest) -> JSONResponse:
    try:
        if "gai" in (request.selected_interfaces or []):
            provider = gai_preflight(request.gai_provider)
            if provider.get("status") != "PASSED":
                raise HTTPException(
                    status_code=400,
                    detail=f"GAI provider preflight failed; Run was not created. {provider.get('message', 'Ollama provider is unavailable.')}",
                )
        run_id = run_execution_manager.submit(get_settings(), request.model_dump())
        return JSONResponse(
            status_code=202,
            content={
                "run_id": run_id,
                "status": "RUNNING",
                "message": "Run accepted for background execution.",
            },
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    if not run_execution_manager.cancel(run_id):
        raise HTTPException(status_code=409, detail="Run is not currently running or cannot be cancelled.")
    return {"run_id": run_id, "status": "CANCEL_REQUESTED"}


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: str) -> JSONResponse:
    settings = get_settings()
    service = Decoupled2StageExperimentUseCase(settings)
    try:
        current = run_execution_manager.reconcile_stale_run(settings, service.get_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if current.get("status") not in {"FAILED", "CANCELLED", "INTERRUPTED_RESUMABLE", "PARTIAL_QUOTA_EXHAUSTED"}:
        raise HTTPException(status_code=409, detail="Only an interrupted, cancelled, or quota-partial Run can be resumed.")
    config = dict(current.get("config") or {})
    if not config:
        raise HTTPException(status_code=409, detail="Run has no frozen configuration for resume.")
    try:
        resumed = run_execution_manager.resume(settings, run_id, config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not resumed:
        raise HTTPException(status_code=409, detail="Run is already executing.")
    return JSONResponse(
        status_code=202,
        content={"run_id": run_id, "status": "RUNNING", "message": "Run resume accepted."},
    )


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    settings = get_settings()
    service = Decoupled2StageExperimentUseCase(settings)
    try:
        return run_execution_manager.reconcile_stale_run(settings, service.get_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runs/{run_id}/perception-error-boundary", response_model=None)
def get_perception_error_boundary(
    run_id: str,
    rule_source_id: str = Query(default=RULE_SOURCE_HUMAN, min_length=1),
    topology_id: str = Query(..., min_length=1),
    model_id: str = Query(..., min_length=1),
    regime: str = Query(..., min_length=1),
    decision_interface: str = Query(default="rule_based", min_length=1),
    format: str = Query(default="json", pattern="^(json|md)$"),
) -> JSONResponse | PlainTextResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        payload = service.existing_run_analysis(
            run_id,
            rule_source_id=rule_source_id,
            topology_id=topology_id,
            model_id=model_id,
            regime=regime,
            decision_interface=decision_interface,
        )
    except BoundaryServiceError as exc:
        detail = {"code": exc.code, "message": exc.message}
        detail.update(exc.details)
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    if format == "md":
        return PlainTextResponse(
            service.markdown(payload),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="perception-error-boundary-{run_id}.md"'},
        )
    return JSONResponse(content=payload)


@router.get("/runs/{run_id}/boundary-capability")
def get_boundary_capability(
    run_id: str,
    rule_source_id: str = Query(default=RULE_SOURCE_HUMAN, min_length=1),
    topology_id: str = Query(..., min_length=1),
    model_id: str = Query(..., min_length=1),
    regime: str = Query(..., min_length=1),
    decision_interface: str = Query(default="rule_based", min_length=1),
) -> JSONResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        return JSONResponse(content=service.boundary_capability(run_id, rule_source_id=rule_source_id, topology_id=topology_id, model_id=model_id, regime=regime, decision_interface=decision_interface))
    except BoundaryServiceError as exc:
        detail = {"code": exc.code, "message": exc.message, **exc.details}
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc


@router.post("/runs/{run_id}/boundary-analysis")
def create_boundary_analysis(run_id: str, request: BoundaryAnalysisRequest) -> JSONResponse:
    try:
        created = run_execution_manager.submit_boundary(get_settings(), run_id, request.model_dump())
    except BoundaryServiceError as exc:
        detail = {"code": exc.code, "message": exc.message, **exc.details}
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    return JSONResponse(status_code=202, content=created)


@router.get("/boundary-analysis/{job_id}")
def get_boundary_analysis(job_id: str, run_id: str = Query(..., min_length=1)) -> JSONResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        return JSONResponse(content=service.get_boundary_job(run_id, job_id))
    except BoundaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


@router.get("/boundary-analysis/{job_id}/curve")
def get_boundary_analysis_curve(job_id: str, run_id: str = Query(..., min_length=1)) -> JSONResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        return JSONResponse(content={"job_id": job_id, "curve": service.get_boundary_curve(run_id, job_id)})
    except BoundaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


@router.get("/boundary-analysis/{job_id}/summary")
def get_boundary_analysis_summary(job_id: str, run_id: str = Query(..., min_length=1)) -> JSONResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        return JSONResponse(content=service.get_boundary_job_file(run_id, job_id, "boundary_summary.json"))
    except BoundaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


@router.get("/boundary-analysis/{job_id}/trials")
def get_boundary_analysis_trials(job_id: str, run_id: str = Query(..., min_length=1)) -> JSONResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    try:
        return JSONResponse(content={"job_id": job_id, "trials": service.get_boundary_trials(run_id, job_id)})
    except BoundaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


@router.get("/boundary-analysis/{job_id}/download", response_model=None)
def download_boundary_analysis(job_id: str, run_id: str = Query(..., min_length=1), format: str = Query(default="md", pattern="^(json|md)$")) -> JSONResponse | PlainTextResponse:
    service = PerceptionErrorBoundaryService(get_settings())
    job_root = service.storage_root / run_id / "boundary_analysis" / job_id
    try:
        if format == "md":
            path = job_root / "boundary_report.md"
            if not path.is_file():
                raise BoundaryServiceError("BOUNDARY_JOB_FILE_NOT_FOUND", "Boundary Markdown is not available.", 404)
            return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="boundary-{job_id}.md"'})
        return JSONResponse(content=service.get_boundary_job_file(run_id, job_id, "boundary_summary.json"), headers={"Content-Disposition": f'attachment; filename="boundary-{job_id}.json"'})
    except BoundaryServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message, **exc.details}) from exc


@router.post("/boundary-analysis/{job_id}/cancel")
def cancel_boundary_analysis(job_id: str) -> JSONResponse:
    if not run_execution_manager.cancel_boundary(job_id):
        raise HTTPException(status_code=409, detail={"code": "BOUNDARY_NOT_RUNNING", "message": "Boundary job is not running."})
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "CANCEL_REQUESTED"})


@router.post("/boundary-analysis/{job_id}/resume")
def resume_boundary_analysis(job_id: str, run_id: str = Query(..., min_length=1)) -> JSONResponse:
    if not run_execution_manager.resume_boundary(get_settings(), run_id, job_id):
        raise HTTPException(status_code=409, detail={"code": "BOUNDARY_NOT_RESUMABLE", "message": "Boundary job cannot be resumed."})
    return JSONResponse(status_code=202, content={"job_id": job_id, "run_id": run_id, "status": "QUEUED"})


@router.get("/runs/{run_id}/files/{relative_path:path}")
def download_file(run_id: str, relative_path: str) -> FileResponse:
    service = Decoupled2StageExperimentUseCase(get_settings())
    try:
        path = service.resolve_run_file(run_id, relative_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, filename=path.name)


@router.get("/topology-flow-preview/runs")
def list_topology_flow_preview_runs(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return _topology_flow_preview_service().list_runs(limit=limit)


@router.get("/topology-flow-preview/runs/{run_id}/options")
def get_topology_flow_preview_options(run_id: str) -> dict[str, Any]:
    try:
        return _topology_flow_preview_service().options(run_id)
    except PreviewServiceError as exc:
        _raise_preview_error(exc)
    raise AssertionError("Preview options handler did not return or raise")


@router.get("/topology-flow-preview/runs/{run_id}/preview")
def get_topology_flow_preview(
    run_id: str,
    topology_id: str = Query(..., min_length=1),
    model_id: str = Query(..., min_length=1),
    regime: str = Query(..., min_length=1),
    trial_index: int = Query(..., ge=0, le=500),
    interface: str = Query(default="rule_based", min_length=1),
    rule_source_id: str = Query(default=RULE_SOURCE_HUMAN, min_length=1),
) -> dict[str, Any]:
    try:
        return _topology_flow_preview_service().preview(
            run_id,
            topology_id=topology_id,
            model_id=model_id,
            regime=regime,
            trial_index=trial_index,
            interface=interface,
            rule_source_id=rule_source_id,
        )
    except PreviewServiceError as exc:
        _raise_preview_error(exc)
    raise AssertionError("Preview handler did not return or raise")
