from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from two_stage.settings import get_settings

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "two-stage-api",
        "phase": "phase1-scaffold",
    }


@router.get("/ready")
def ready() -> dict[str, object]:
    settings = get_settings()
    artifact_root = Path(settings.local_artifact_root)
    return {
        "status": "ok" if artifact_root.exists() else "warning",
        "checks": {
            "artifact_root_exists": artifact_root.exists(),
            "current_project_data_root_configured": bool(settings.current_project_data_root),
            "current_project_config_root_configured": bool(settings.current_project_config_root),
        },
    }
