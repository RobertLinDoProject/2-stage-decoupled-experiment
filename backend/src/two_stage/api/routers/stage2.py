from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from two_stage.application.dto.stage2 import Stage2PipelineConfig
from two_stage.application.use_cases.stage2_topology_ideal import (
    TOPOLOGY_TRIPLET_PROFILE_ID,
    Stage2TopologyIdealRunner,
    discover_topology_triplet_options,
)
from two_stage.domain.errors import DomainValidationError
from two_stage.settings import get_settings

router = APIRouter(prefix="/stage2-topology-ideal")

DEFAULT_TRIPLET_PACKAGE_PATH = "Topology\u8cc7\u6599"


@router.get("/profile")
def profile() -> dict[str, object]:
    return {
        "pipeline_profile": "stage2_topology_ideal_v1",
        "display_name": "Stage II Topology-Aware Ideal Baseline",
        "default_run_purpose": "exploratory",
        "required_stages": ["M0", "M3", "M4", "M6", "M7", "M8", "M9"],
        "not_required_stages": {
            "M1": "Perception benchmark is not required.",
            "M2": "Empirical residual distribution is not required.",
            "M5": "Ideal observation uses M4 scenario_gt; no error injection stage runs.",
        },
        "ideal_observation": {
            "trial_type": "ideal",
            "input_mode": "ground_truth",
            "observed_population": "scenario_gt",
            "error_realization_id": None,
        },
        "unavailable_metrics": [
            "perception_mae",
            "perception_rmse",
            "perception_regime_confusion",
            "perturbed_R_deploy",
            "Delta_R",
            "perception_error_propagation",
        ],
    }


@router.get("/topologies")
def topologies(
    topology_package_path: str = Query(default=DEFAULT_TRIPLET_PACKAGE_PATH),
) -> dict[str, object]:
    settings = get_settings()
    package_root = Path(settings.current_project_data_root) / topology_package_path
    try:
        options = discover_topology_triplet_options(package_root)
    except DomainValidationError as exc:
        return {
            "topology_profile_id": TOPOLOGY_TRIPLET_PROFILE_ID,
            "topology_package_path": topology_package_path,
            "source_present": False,
            "topologies": [],
            "issues": [
                {
                    "code": "TOPOLOGY_TRIPLET_SOURCE_NOT_AVAILABLE",
                    "severity": "warning",
                    "message": str(exc),
                }
            ],
        }
    return {
        "topology_profile_id": TOPOLOGY_TRIPLET_PROFILE_ID,
        "topology_package_path": topology_package_path,
        "source_present": True,
        "topologies": options,
        "issues": [],
    }


@router.get("/preview")
def preview(
    topology_profile_id: str = Query(default=TOPOLOGY_TRIPLET_PROFILE_ID),
    topology_package_path: str = Query(default=DEFAULT_TRIPLET_PACKAGE_PATH),
    topology_source_id: str | None = Query(default=None),
) -> dict[str, object]:
    settings = get_settings()
    resolved_topology_source_id = topology_source_id
    if topology_profile_id == TOPOLOGY_TRIPLET_PROFILE_ID and not resolved_topology_source_id:
        try:
            options = discover_topology_triplet_options(
                Path(settings.current_project_data_root) / topology_package_path
            )
            if options:
                resolved_topology_source_id = str(options[0]["source_id"])
        except DomainValidationError:
            resolved_topology_source_id = None

    config = Stage2PipelineConfig(
        run_id="RUN-PREVIEW",
        topology_profile_id=topology_profile_id,
        topology_package_path=topology_package_path,
        topology_source_id=resolved_topology_source_id,
        include_mock_gai=False,
    )
    runner = Stage2TopologyIdealRunner(
        source_root=Path(settings.current_project_data_root),
        artifact_root=Path(settings.local_artifact_root),
        config=config,
    )
    try:
        package_root = runner._topology_package_root()
        checksums = runner._source_checksums(package_root)
        topology = runner._load_topology(package_root)
        report = runner._validate_topology(topology, checksums)
    except DomainValidationError as exc:
        return {
            "pipeline_profile": config.pipeline_profile,
            "source_present": False,
            "perception_required": False,
            "perception_status": "not_required",
            "topology_status": "missing",
            "topology_profile_id": config.topology_profile_id,
            "topology_package_path": config.topology_package_path,
            "topology_source_id": config.topology_source_id,
            "issues": [
                {
                    "code": "TOPOLOGY_SOURCE_NOT_MOUNTED",
                    "severity": "warning",
                    "message": str(exc),
                }
            ],
        }

    external_exits_raw = topology.rules.get("external_exits", [])
    external_exits = (
        [str(value) for value in external_exits_raw]
        if isinstance(external_exits_raw, list)
        else []
    )
    return {
        "pipeline_profile": config.pipeline_profile,
        "source_present": True,
        "perception_required": False,
        "perception_status": "not_required",
        "topology_status": "validated" if report.valid else "invalid",
        "topology_profile_id": config.topology_profile_id,
        "topology_package_path": config.topology_package_path,
        "topology_source_id": config.topology_source_id or topology.topology_id,
        "topology": {
            "topology_id": topology.topology_id,
            "topology_version": topology.topology_version,
            "source_id": config.topology_source_id or topology.topology_id,
            "source_profile_id": config.topology_profile_id,
            "source_relative_package": config.topology_package_path,
            "coordinate_system": topology.coordinate_system,
            "graph_directionality": topology.graph_directionality,
            "external_exits": external_exits,
            "node_count": len(topology.nodes),
            "edge_count": len(topology.edges),
            "capacity_total": sum(node.capacity for node in topology.nodes),
            "nodes": [node.model_dump(mode="json") for node in topology.nodes[:100]],
            "edges": [edge.model_dump(mode="json") for edge in topology.edges[:200]],
        },
        "issues": [issue.model_dump(mode="json") for issue in report.issues],
    }
