from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from two_stage.api.routers import (
    decoupled_experiment,
    health,
    integrated,
    preflight,
    stage1,
    stage2,
)
from two_stage.domain.errors import DomainValidationError, ExperimentStateError
from two_stage.settings import get_settings


async def domain_validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc), "error_type": exc.__class__.__name__},
    )


async def experiment_state_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={"detail": str(exc), "error_type": exc.__class__.__name__},
    )


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Decoupled 2-Stage Experiment API",
        version="0.3.0-decoupled-2-stage",
        description="Lean API for generating decoupled two-stage experiment tables from formal A/B data.",
    )
    app.add_exception_handler(DomainValidationError, domain_validation_error_handler)
    app.add_exception_handler(ExperimentStateError, experiment_state_error_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allow_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(decoupled_experiment.router, prefix="/api/v1", tags=["decoupled-2-stage-experiment"])
    app.include_router(preflight.router, prefix="/api/v1", tags=["preflight"])
    app.include_router(stage1.router, prefix="/api/v1", tags=["stage1-perception-benchmark"])
    app.include_router(stage2.router, prefix="/api/v1", tags=["stage2-topology-ideal"])
    app.include_router(integrated.router, prefix="/api/v1", tags=["integrated-results"])
    return app


app = create_app()
