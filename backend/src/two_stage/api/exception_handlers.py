from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from two_stage.domain.errors import TwoStageError


async def two_stage_error_handler(_: Request, exc: TwoStageError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": {},
            }
        },
    )
