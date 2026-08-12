from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from two_stage.application.dto.stage1 import Stage1PipelineConfig
from two_stage.application.use_cases.perception_benchmark import (
    PerceptionInputAuditor,
    _read_perception_parquet,
)
from two_stage.settings import get_settings

router = APIRouter(prefix="/stage1-perception-benchmark")


@router.get("/profile")
def profile() -> dict[str, object]:
    return {
        "pipeline_profile": "stage1_perception_benchmark_v1",
        "display_name": "Stage I Perception Benchmark",
        "default_run_purpose": "formal",
        "required_stages": ["M0", "M1", "M2", "M8", "M9"],
        "not_required_stages": {
            "M3": "Topology is not required for Stage I perception benchmark.",
            "M4": "Scenario generation is not required.",
            "M5": "Perception error injection is forbidden for this profile.",
            "M6": "Decision input construction is not required.",
            "M7": "Topology decision validation is not required.",
        },
        "metrics_scope": "perception_only",
        "not_applicable_metrics": ["Decision", "Validator", "R_ideal", "R_deploy", "Delta_R"],
    }


@router.get("/audit")
def audit() -> dict[str, object]:
    settings = get_settings()
    config = Stage1PipelineConfig(run_id="RUN-AUDIT-PREVIEW")
    result = PerceptionInputAuditor().audit(
        source_root=Path(settings.current_project_data_root),
        config=config,
    )
    return result.model_dump(mode="json")


@router.get("/overview")
def overview(
    run_id: str | None = None,
    dataset_id: str | None = None,
    model_id: str | None = None,
    paradigm: str | None = None,
    split: str | None = None,
    sample_id: str | None = None,
    sample_key: str | None = None,
) -> dict[str, object]:
    settings = get_settings()
    if run_id is None:
        return {
            "run_id": None,
            "canonical_available": False,
            "message": "Select or submit a Stage I run to inspect canonical perception results.",
            "filters": {},
            "metrics": [],
            "samples": [],
            "selected_sample": None,
        }

    artifact_root = Path(settings.local_artifact_root) / "published" / "runs" / run_id
    canonical_path = artifact_root / "M1" / "perception_results.parquet"
    metrics_path = artifact_root / "M8" / "metrics.json"
    quality_path = artifact_root / "M0" / "perception_data_quality_report.json"
    if not canonical_path.exists():
        return {
            "run_id": run_id,
            "canonical_available": False,
            "message": "M1 canonical perception_results.parquet is not available for this run.",
            "filters": {},
            "metrics": _read_json_payload(metrics_path).get("metrics", []),
            "samples": [],
            "selected_sample": None,
            "quality": _read_json_payload(quality_path),
        }

    rows = [row.model_dump(mode="json") for row in _read_perception_parquet(str(canonical_path))]
    filtered = [
        row for row in rows
        if _matches(row, "dataset_id", dataset_id)
        and _matches(row, "model_id", model_id)
        and _matches(row, "paradigm", paradigm)
        and _matches(row, "split", split)
    ]
    selected = next(
        (
            row for row in filtered
            if _canonical_sample_key(row) == sample_key
            or (sample_key is None and row["sample_id"] == sample_id)
        ),
        None,
    )
    if selected is None and filtered:
        selected = filtered[0]
    return {
        "run_id": run_id,
        "canonical_available": True,
        "filters": {
            "dataset_id": sorted({str(row["dataset_id"]) for row in rows}),
            "model_id": sorted({str(row["model_id"]) for row in rows}),
            "paradigm": sorted({str(row["paradigm"]) for row in rows}),
            "split": sorted({str(row["split"]) for row in rows}),
        },
        "metrics": _read_json_payload(metrics_path).get("metrics", []),
        "quality": _read_json_payload(quality_path),
        "samples": [_sample_view(row) for row in filtered[:500]],
        "selected_sample": _sample_view(selected) if selected is not None else None,
        "row_count": len(rows),
        "filtered_count": len(filtered),
        "error_semantics": {
            "positive": "overestimate",
            "negative": "underestimate",
            "formula": "predicted_count - ground_truth_count",
        },
    }


def _matches(row: dict[str, Any], key: str, expected: str | None) -> bool:
    return expected in {None, "", "all"} or str(row.get(key)) == expected


def _sample_view(row: dict[str, Any]) -> dict[str, object]:
    error = float(row["error"])
    if error > 0:
        direction = "overestimate"
    elif error < 0:
        direction = "underestimate"
    else:
        direction = "exact"
    return {**row, "sample_key": _canonical_sample_key(row), "error_direction": direction}


def _canonical_sample_key(row: dict[str, Any]) -> str:
    return "::".join(
        [
            str(row["dataset_id"]),
            str(row["model_id"]),
            str(row["split"]),
            str(row["sample_id"]),
        ]
    )


def _read_json_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
