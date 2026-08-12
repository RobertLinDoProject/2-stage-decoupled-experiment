from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter

from two_stage.settings import get_settings

router = APIRouter(prefix="/integrated-results")


@router.get("/overview")
def overview(run_id: str | None = None) -> dict[str, object]:
    if not run_id:
        return {
            "run_id": None,
            "resolved_run_id": None,
            "metrics_available": False,
            "message": "Select or submit an integrated exploratory run to inspect M8/M9 results.",
            "metric_definition_version": None,
            "condition_results": [],
            "unavailable_metrics": [],
            "artifacts": [],
            "limitations": [],
        }

    settings = get_settings()
    artifact_root = Path(settings.local_artifact_root) / "published" / "runs"
    resolved_root = _resolve_integrated_root(artifact_root, run_id)
    metrics_path = resolved_root / "M8" / "integrated_metrics.json"
    summary_path = resolved_root / "M9" / "run_summary.json"

    if not metrics_path.exists():
        return {
            "run_id": run_id,
            "resolved_run_id": resolved_root.name,
            "metrics_available": False,
            "message": "M8 integrated_metrics.json is not available for this run.",
            "metric_definition_version": None,
            "condition_results": [],
            "unavailable_metrics": [],
            "artifacts": _artifacts_from_summary(summary_path),
            "limitations": _limitations_from_summary(summary_path),
        }

    payload = _read_json(metrics_path)
    metrics = [item for item in payload.get("metrics", []) if isinstance(item, dict)]
    return {
        "run_id": run_id,
        "resolved_run_id": resolved_root.name,
        "metrics_available": True,
        "message": None,
        "metric_definition_version": payload.get("metric_definition_version"),
        "pipeline_profile": payload.get("pipeline_profile"),
        "condition_results": _condition_results(metrics),
        "unavailable_metrics": _unavailable_metrics(metrics),
        "artifacts": _artifacts_from_summary(summary_path),
        "limitations": _limitations_from_summary(summary_path),
    }


def _resolve_integrated_root(artifact_root: Path, run_id: str) -> Path:
    direct = artifact_root / run_id
    if (direct / "M8" / "integrated_metrics.json").exists():
        return direct
    child = artifact_root / f"{run_id}-m8-m9"
    if (child / "M8" / "integrated_metrics.json").exists():
        return child
    return direct


def _condition_results(metrics: list[dict[str, Any]]) -> list[dict[str, object]]:
    by_condition: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        if metric.get("availability") != "available":
            continue
        filters_payload = metric.get("filters")
        filters = cast(dict[str, Any], filters_payload) if isinstance(filters_payload, dict) else {}
        condition_id = str(filters.get("condition_id", "run"))
        if condition_id == "unavailable":
            continue
        result = by_condition.setdefault(
            condition_id,
            {
                "condition_id": condition_id,
                "dataset_id": filters.get("dataset_id"),
                "model_id": filters.get("model_id"),
                "paradigm": filters.get("paradigm"),
                "split": filters.get("split"),
                "interface_type": filters.get("interface_type"),
                "n_trials": metric.get("n_trials"),
                "metrics": {},
            },
        )
        metric_id = str(metric.get("metric_id"))
        result["metrics"][metric_id] = metric.get("value")
        result["n_trials"] = _max_numeric(result.get("n_trials"), metric.get("n_trials"))
    return sorted(
        by_condition.values(),
        key=lambda item: (
            str(item.get("paradigm") or ""),
            str(item.get("model_id") or ""),
            str(item.get("condition_id") or ""),
        ),
    )


def _unavailable_metrics(metrics: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for metric in metrics:
        if metric.get("availability") != "unavailable":
            continue
        filters_payload = metric.get("filters")
        filters = cast(dict[str, Any], filters_payload) if isinstance(filters_payload, dict) else {}
        rows.append(
            {
                "metric_id": metric.get("metric_id"),
                "interface_type": filters.get("interface_type"),
                "reason": metric.get("unavailable_reason"),
            }
        )
    return rows


def _max_numeric(left: object, right: object) -> object:
    if not isinstance(left, int | float):
        return right
    if not isinstance(right, int | float):
        return left
    return max(left, right)


def _artifacts_from_summary(path: Path) -> list[dict[str, object]]:
    summary = _read_json(path)
    artifacts = summary.get("artifacts", [])
    return artifacts if isinstance(artifacts, list) else []


def _limitations_from_summary(path: Path) -> list[str]:
    summary = _read_json(path)
    limitations = summary.get("limitations", [])
    if not isinstance(limitations, list):
        return []
    return [str(item) for item in limitations]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}
