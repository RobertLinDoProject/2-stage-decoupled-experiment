from __future__ import annotations

from fastapi import APIRouter

from two_stage.application.dto.preflight import (
    ResearchPreflightReport,
    ResearchReadiness,
    TechnicalPreflightReport,
    TechnicalReadiness,
)
from two_stage.application.services.preflight import (
    evaluate_research_preflight,
    evaluate_technical_preflight,
)
from two_stage.domain.enums import RunPurpose

router = APIRouter()


@router.post("/preflight/technical", response_model=TechnicalPreflightReport)
def technical_preflight(readiness: TechnicalReadiness) -> TechnicalPreflightReport:
    return evaluate_technical_preflight(readiness, run_purpose=RunPurpose.DEVELOPMENT)


@router.post("/preflight/research", response_model=ResearchPreflightReport)
def research_preflight(readiness: ResearchReadiness) -> ResearchPreflightReport:
    return evaluate_research_preflight(readiness)
