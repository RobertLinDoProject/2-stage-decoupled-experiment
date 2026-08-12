from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import cast

from two_stage.application.dto.preflight import (
    PreflightCheck,
    PreflightIssue,
    ResearchPreflightReport,
    TechnicalPreflightReport,
)
from two_stage.application.dto.stage2 import (
    ArtifactRecord,
    DecisionAction,
    DecisionResult,
    InterfaceType,
    MetricResult,
    ObservationManifest,
    PopulationEntry,
    Regime,
    ScenarioManifest,
    ScenarioRecord,
    Stage2PipelineConfig,
    Stage2RunSummary,
    StageStatusRecord,
    TopologyEdge,
    TopologyNode,
    TopologySpec,
    TopologyValidationReport,
    ValidationIssue,
    ValidationResult,
    ValidationViolation,
)
from two_stage.domain.entities.artifact import ArtifactPayload
from two_stage.domain.enums import (
    CheckStatus,
    IssueSeverity,
    PolicyStatus,
    PreflightStatus,
    RunPurpose,
)
from two_stage.domain.errors import DomainValidationError
from two_stage.infrastructure.artifact_store.local import LocalArtifactStore

JsonObject = dict[str, object]

NOT_REQUIRED_STAGES = ("M1", "M2", "M5")
TOPOLOGY_REQUIRED_FILES = (
    "topology_nodes.csv",
    "topology_edges.csv",
    "topology_rules.json",
    "topology_spec.json",
    "topology_manifest.json",
    "quality_report.md",
)

TOPOLOGY_TRIPLET_PROFILE_ID = "project_multi_topology_json_triplet_v0_1"



class Stage2TopologyIdealRunner:
    """Executes the Stage II-only ideal-input baseline without perception or M5."""

    def __init__(
        self,
        *,
        source_root: str | Path,
        artifact_root: str | Path,
        config: Stage2PipelineConfig,
    ) -> None:
        self.source_root = Path(source_root)
        self.artifact_store = LocalArtifactStore(artifact_root)
        self.config = config
        self.artifacts: list[ArtifactRecord] = []
        self.rules: JsonObject = {}

    def run(self) -> Stage2RunSummary:
        package_root = self._topology_package_root()
        source_checksums = self._source_checksums(package_root)

        topology = self._load_topology(package_root)
        topology_report = self._validate_topology(topology, source_checksums)
        if not topology_report.valid:
            raise DomainValidationError("Topology validation failed for Stage II baseline.")

        technical_preflight = self._technical_preflight(package_root, topology_report)
        research_preflight = self._research_preflight()
        if technical_preflight.status == PreflightStatus.FAILED:
            raise DomainValidationError("Technical preflight failed.")
        if research_preflight.status == PreflightStatus.FAILED:
            raise DomainValidationError("Research preflight failed.")

        self._publish_json(
            stage_id="M0",
            file_name="pipeline_profile.json",
            purpose="Stage II topology ideal baseline pipeline profile.",
            payload=self.config.model_dump(mode="json"),
            schema_name="Stage2PipelineConfig",
        )
        self._publish_json(
            stage_id="M0",
            file_name="technical_preflight_report.json",
            purpose="Technical Preflight report for topology-only baseline.",
            payload=technical_preflight.model_dump(mode="json"),
            schema_name="PreflightReport",
        )
        self._publish_json(
            stage_id="M0",
            file_name="research_preflight_report.json",
            purpose="Research Preflight report for topology-only baseline.",
            payload=research_preflight.model_dump(mode="json"),
            schema_name="PreflightReport",
        )

        topology_artifact = self._publish_json(
            stage_id="M3",
            file_name="topology_spec.json",
            purpose="Canonical topology and capacity runtime input for M4/M6/M7.",
            payload=topology.model_dump(mode="json"),
            schema_name="TopologySpec",
            row_count=len(topology.nodes),
        )
        topology_report_artifact = self._publish_json(
            stage_id="M3",
            file_name="topology_validation_report.json",
            purpose="Topology/capacity validation report with issue navigation metadata.",
            payload=topology_report.model_dump(mode="json"),
            schema_name="TopologyValidationReport",
            row_count=len(topology_report.issues),
        )
        self._publish_json(
            stage_id="M3",
            file_name="topology_manifest.json",
            purpose="Canonical topology materialization manifest and checksums.",
            payload={
                "pipeline_profile": self.config.pipeline_profile,
                "topology_artifact_uri": topology_artifact.uri,
                "validation_report_uri": topology_report_artifact.uri,
                "source_profile_id": self.config.topology_profile_id,
                "source_root_env": "CURRENT_PROJECT_DATA_ROOT",
                "source_relative_package": self.config.topology_package_path,
                "topology_source_id": self.config.topology_source_id or topology.topology_id,
                "source_checksums": source_checksums,
            },
            schema_name="ArtifactManifest",
        )

        scenarios = self._generate_scenarios(topology)
        scenario_bytes = _jsonl_bytes([scenario.model_dump(mode="json") for scenario in scenarios])
        scenario_checksum = _checksum_bytes(scenario_bytes)
        scenario_artifact = self._publish_bytes(
            stage_id="M4",
            file_name="scenario_gt.jsonl",
            purpose="Ground-truth Stage II scenario population d*.",
            content=scenario_bytes,
            media_type="application/x-ndjson",
            schema_name="ScenarioGroundTruth",
            row_count=len(scenarios),
        )
        scenario_manifest = ScenarioManifest(
            scenario_policy_id=self.config.scenario_policy_id,
            scenario_policy_version=self.config.scenario_policy_version,
            scenario_policy_status=self.config.scenario_policy_status,
            root_seed=self.config.root_seed,
            scenario_count=len(scenarios),
            total_population=self.config.total_population,
            scenario_gt_checksum=scenario_checksum,
            topology_checksum=topology_artifact.checksum,
            invariants={
                "total_population_exact": all(
                    sum(entry.ground_truth_population for entry in scenario.zone_counts)
                    == self.config.total_population
                    for scenario in scenarios
                ),
                "capacity_respected": all(
                    entry.ground_truth_population <= entry.capacity
                    for scenario in scenarios
                    for entry in scenario.zone_counts
                ),
                "seed_reproducible": scenarios == self._generate_scenarios(topology),
            },
        )
        self._publish_json(
            stage_id="M4",
            file_name="scenario_manifest.json",
            purpose="Scenario policy, seed and invariant manifest.",
            payload=scenario_manifest.model_dump(mode="json"),
            schema_name="ScenarioManifest",
        )

        observation_bytes, observation_manifest = BuildIdealObservationUseCase().build(
            scenario_bytes=scenario_bytes,
            scenario_checksum=scenario_artifact.checksum,
        )
        observation_artifact = self._publish_bytes(
            stage_id="M6",
            file_name="observation_population.jsonl",
            purpose="Ideal observation population; bytes equal M4 scenario_gt.",
            content=observation_bytes,
            media_type="application/x-ndjson",
            schema_name="ObservationPopulation",
            row_count=len(scenarios),
        )
        self._publish_json(
            stage_id="M6",
            file_name="observation_manifest.json",
            purpose="Ideal observation manifest; no error realization is created.",
            payload=observation_manifest.model_dump(mode="json"),
            schema_name="ObservationManifest",
        )

        decisions = self._build_decisions(
            topology=topology,
            scenarios=scenarios,
            observation_checksum=observation_artifact.checksum,
            topology_checksum=topology_artifact.checksum,
        )
        self._publish_json(
            stage_id="M6",
            file_name="decision_results.json",
            purpose="Rule and mock GAI decisions using the same ideal observation.",
            payload={"decisions": [decision.model_dump(mode="json") for decision in decisions]},
            schema_name="DecisionResults",
            row_count=len(decisions),
        )

        validations = self._validate_decisions(
            topology=topology,
            scenarios=scenarios,
            decisions=decisions,
            ground_truth_checksum=scenario_artifact.checksum,
        )
        self._publish_json(
            stage_id="M7",
            file_name="validation_results.json",
            purpose="External validator results using M4 scenario_gt as ground truth.",
            payload={"validations": [result.model_dump(mode="json") for result in validations]},
            schema_name="ValidationResults",
            row_count=len(validations),
        )

        metrics = self._calculate_metrics(validations, decisions)
        self._publish_json(
            stage_id="M8",
            file_name="metrics.json",
            purpose="Stage II ideal-input metrics; perception and Delta R unavailable.",
            payload={"metrics": [metric.model_dump(mode="json") for metric in metrics]},
            schema_name="Stage2IdealMetrics",
            row_count=len(metrics),
        )

        report_markdown = self._render_report(
            topology=topology,
            topology_report=topology_report,
            scenario_artifact=scenario_artifact,
            observation_artifact=observation_artifact,
            metrics=metrics,
        )
        self._publish_bytes(
            stage_id="M9",
            file_name="report.md",
            purpose="Frozen Stage II-only baseline report.",
            content=report_markdown.encode("utf-8"),
            media_type="text/markdown",
            schema_name="Stage2FrozenReport",
            row_count=None,
        )
        self._publish_json(
            stage_id="M9",
            file_name="reproducibility_manifest.json",
            purpose="Reproducibility manifest for Stage II ideal baseline.",
            payload=self._reproducibility_manifest(
                topology_checksum=topology_artifact.checksum,
                scenario_checksum=scenario_artifact.checksum,
                observation_checksum=observation_artifact.checksum,
            ),
            schema_name="ReproducibilityManifest",
        )
        self._publish_bytes(
            stage_id="M9",
            file_name="delivery_manifest.csv",
            purpose="Delivery manifest listing published Stage II artifacts.",
            content=self._delivery_manifest_csv().encode("utf-8-sig"),
            media_type="text/csv",
            schema_name="DeliveryManifest",
            row_count=len(self.artifacts),
        )

        summary = Stage2RunSummary(
            run_id=self.config.run_id,
            pipeline_profile=self.config.pipeline_profile,
            run_purpose=self.config.run_purpose,
            status="succeeded",
            stage_statuses=self._stage_statuses(),
            m4_scenario_gt_checksum=scenario_artifact.checksum,
            m6_observation_checksum=observation_artifact.checksum,
            limitations=[
                "Stage II-only ideal-input baseline; perception benchmark is not required.",
                "M1, M2 and M5 are NOT_REQUIRED for this pipeline profile.",
                "No synthetic perception noise is used.",
                "No epsilon=0 error injection artifact is created.",
                "R_deploy, Delta R and perception error propagation are unavailable.",
                "Scenario policy is experimental, so this run is exploratory, not formal.",
                "Mock GAI adapter is for adapter-contract comparison only; "
                "live GAI is not invoked.",
            ],
            artifacts=self.artifacts.copy(),
            generated_at=self.config.created_at,
        )
        summary_artifact = self._publish_json(
            stage_id="M9",
            file_name="run_summary.json",
            purpose="Run status summary with conditional DAG statuses.",
            payload=summary.model_dump(mode="json"),
            schema_name="Stage2RunSummary",
        )
        summary.artifacts.append(summary_artifact)
        return summary

    def _topology_package_root(self) -> Path:
        package_root = self.source_root / self.config.topology_package_path
        if not package_root.exists():
            raise DomainValidationError(
                "Formal topology/capacity package is missing. "
                "Set CURRENT_PROJECT_DATA_ROOT to the project data root."
            )
        return package_root

    def _uses_topology_triplet_profile(self) -> bool:
        return self.config.topology_profile_id == TOPOLOGY_TRIPLET_PROFILE_ID

    def _source_checksums(self, package_root: Path) -> dict[str, str]:
        if self._uses_topology_triplet_profile():
            files = _find_topology_triplet_files(package_root, self.config.topology_source_id)
            return {
                f"{role}:{file_path.name}": _checksum_file(file_path)
                for role, file_path in files.items()
            }

        checksums: dict[str, str] = {}
        for file_name in TOPOLOGY_REQUIRED_FILES:
            path = package_root / file_name
            if not path.exists():
                raise DomainValidationError(f"Required topology package file missing: {file_name}")
            checksums[file_name] = _checksum_file(path)
        return checksums

    def _load_topology(self, package_root: Path) -> TopologySpec:
        if self._uses_topology_triplet_profile():
            return self._load_topology_triplet(package_root)

        spec_path = package_root / self.config.topology_runtime_input
        manifest_path = package_root / "topology_manifest.json"
        raw = _read_json_object(spec_path)
        manifest = _read_json_object(manifest_path)
        rules_raw = raw.get("rules", {})
        self.rules = cast(JsonObject, rules_raw if isinstance(rules_raw, dict) else {})

        nodes: list[TopologyNode] = []
        node_items = cast(list[object], raw.get("nodes", []))
        for item in node_items:
            node_raw = cast(JsonObject, item)
            node_type = str(node_raw.get("node_type", "zone"))
            nodes.append(
                TopologyNode(
                    node_id=str(node_raw["node_id"]),
                    node_name=str(node_raw.get("node_name", node_raw["node_id"])),
                    node_type=node_type,
                    capacity=_int_value(node_raw.get("capacity"), default=0),
                    is_exit=_bool_value(node_raw.get("is_exit", node_type == "exit")),
                    is_sink=_bool_value(node_raw.get("is_sink", node_type == "sink")),
                    enabled=_bool_value(node_raw.get("enabled", True)),
                    cluster_id=_optional_str(node_raw.get("cluster_id", node_raw.get("cluster"))),
                    x=_optional_float(node_raw.get("x")),
                    y=_optional_float(node_raw.get("y")),
                )
            )

        edges: list[TopologyEdge] = []
        edge_items = cast(list[object], raw.get("edges", []))
        for item in edge_items:
            edge_raw = cast(JsonObject, item)
            source = edge_raw.get("from_node_id", edge_raw.get("source"))
            target = edge_raw.get("to_node_id", edge_raw.get("target"))
            if source is None or target is None:
                raise DomainValidationError("Topology edge is missing source/target.")
            edges.append(
                TopologyEdge(
                    edge_id=str(edge_raw["edge_id"]),
                    source=str(source),
                    target=str(target),
                    directed=_bool_value(
                        edge_raw.get("is_directed", edge_raw.get("directed", True))
                    ),
                    enabled=_bool_value(edge_raw.get("enabled", True)),
                    hops=int(cast(int | float | str, edge_raw.get("hops", 1))),
                    travel_cost=float(cast(int | float | str, edge_raw.get("travel_cost", 1.0))),
                    edge_capacity=_optional_int(edge_raw.get("edge_capacity")),
                    edge_type=_optional_str(edge_raw.get("edge_type")),
                )
            )

        graph_directionality = _optional_str(
            raw.get(
                "graph_directionality",
                self.rules.get("graph_directionality", manifest.get("graph_directionality")),
            )
        )
        adjacency_semantics = _optional_str(
            raw.get(
                "adjacency_semantics",
                self.rules.get("adjacency_semantics", manifest.get("adjacency_semantics")),
            )
        )
        edge_cost_directionality = _optional_str(
            raw.get(
                "edge_cost_directionality",
                self.rules.get(
                    "edge_cost_directionality",
                    manifest.get("edge_cost_directionality"),
                ),
            )
        )
        return TopologySpec(
            schema_version=str(raw.get("schema_version", "0.1.0")),
            topology_id=str(
                raw.get("topology_id", manifest.get("topology_id", "unknown_topology"))
            ),
            topology_version=str(
                raw.get("topology_version", manifest.get("topology_version", "unknown_version"))
            ),
            coordinate_system=str(
                raw.get(
                    "coordinate_system",
                    manifest.get("coordinate_system", "unspecified_project_map"),
                )
            ),
            origin=_optional_str(raw.get("origin")),
            x_unit=_optional_str(raw.get("x_unit")),
            y_unit=_optional_str(raw.get("y_unit")),
            background_image_ref=_optional_str(raw.get("background_image_ref")),
            background_width=_optional_int(raw.get("background_width")),
            background_height=_optional_int(raw.get("background_height")),
            map_version=_optional_str(raw.get("map_version")),
            graph_directionality=graph_directionality,
            adjacency_semantics=adjacency_semantics,
            edge_cost_directionality=edge_cost_directionality,
            rules=self.rules,
            nodes=nodes,
            edges=edges,
        )

    def _load_topology_triplet(self, package_root: Path) -> TopologySpec:
        files = _find_topology_triplet_files(package_root, self.config.topology_source_id)
        map_rows = _read_json_array(files["map"])
        neighbor_rows = _read_json_array(files["neighbors"])
        rules = _read_json_object(files["rule"])
        self.rules = dict(rules)

        exit_ids = {
            str(value)
            for value in cast(list[object], rules.get("external_exits", []))
        }
        topology_id = str(
            rules.get("topology_id")
            or self.config.topology_source_id
            or files["neighbors"].name.removesuffix("_neighbors.json")
        )
        graph_directionality = str(rules.get("graph_directionality", "undirected")).lower()
        directed = graph_directionality == "directed"
        adjacency_semantics = str(
            rules.get(
                "adjacency_semantics",
                "symmetric" if graph_directionality == "undirected" else "directed",
            )
        ).lower()
        edge_cost_directionality = str(
            rules.get(
                "edge_cost_directionality",
                "symmetric" if graph_directionality == "undirected" else "directed",
            )
        ).lower()
        preserve_directional_cost = edge_cost_directionality == "directed"

        map_capacity_by_id: dict[str, int] = {}
        for item in map_rows:
            if isinstance(item, dict) and item.get("id") is not None:
                map_capacity_by_id[str(item["id"])] = _int_value(
                    item.get("max_occupancy"),
                    default=0,
                )

        nodes: list[TopologyNode] = []
        for item in neighbor_rows:
            if not isinstance(item, dict):
                raise DomainValidationError("Topology neighbors rows must be JSON objects.")
            node_id_raw = item.get("id")
            if node_id_raw is None:
                raise DomainValidationError("Topology neighbor row is missing id.")
            node_id = str(node_id_raw)
            node_type = "exit" if node_id in exit_ids else "zone"
            capacity = _int_value(
                item.get("max_occupancy", map_capacity_by_id.get(node_id)),
                default=0,
            )
            nodes.append(
                TopologyNode(
                    node_id=node_id,
                    node_name=f"{topology_id} {node_id}",
                    node_type=node_type,
                    capacity=capacity,
                    is_exit=node_id in exit_ids,
                    is_sink=node_id in exit_ids,
                    enabled=True,
                )
            )

        edges: list[TopologyEdge] = []
        seen_edge_keys: set[tuple[str, str, bool]] = set()
        for item in neighbor_rows:
            if not isinstance(item, dict):
                continue
            source = str(item.get("id"))
            neighbors = item.get("neighbors", [])
            if not isinstance(neighbors, list):
                raise DomainValidationError("Topology neighbor list must be an array.")
            for neighbor in neighbors:
                if not isinstance(neighbor, dict) or neighbor.get("id") is None:
                    raise DomainValidationError("Topology neighbor entry is missing id.")
                target = str(neighbor["id"])
                edge_is_directional = directed or preserve_directional_cost
                edge_key = (source, target, edge_is_directional)
                if not edge_is_directional:
                    first, second = sorted((source, target), key=_node_sort_key)
                    edge_key = (first, second, edge_is_directional)
                if edge_key in seen_edge_keys:
                    continue
                seen_edge_keys.add(edge_key)
                travel_cost = _optional_float(neighbor.get("cost"))
                if travel_cost is None:
                    travel_cost = _optional_float(neighbor.get("hops"))
                if travel_cost is None:
                    travel_cost = 1.0
                edges.append(
                    TopologyEdge(
                        edge_id=f"E-{source}-{target}",
                        source=source,
                        target=target,
                        directed=edge_is_directional,
                        enabled=True,
                        hops=max(1, int(round(travel_cost))),
                        travel_cost=travel_cost,
                        edge_type="triplet_neighbor",
                    )
                )

        return TopologySpec(
            schema_version=str(rules.get("schema_version", "0.1.0")),
            topology_id=_slugify_topology_id(topology_id),
            topology_version="current_project_triplet_v0_1",
            coordinate_system="unspecified_project_map",
            map_version=files["map"].name,
            graph_directionality=graph_directionality,
            adjacency_semantics=adjacency_semantics,
            edge_cost_directionality=edge_cost_directionality,
            rules=self.rules,
            nodes=nodes,
            edges=edges,
        )

    def _validate_topology(
        self,
        topology: TopologySpec,
        source_checksums: dict[str, str],
    ) -> TopologyValidationReport:
        issues: list[ValidationIssue] = []
        node_ids: set[str] = set()
        duplicate_nodes: set[str] = set()
        for node in topology.nodes:
            if node.node_id in node_ids:
                duplicate_nodes.add(node.node_id)
            node_ids.add(node.node_id)
            if node.capacity < 0:
                issues.append(
                    ValidationIssue(
                        code="NEGATIVE_CAPACITY",
                        severity="error",
                        message="Capacity must be non-negative.",
                        node_id=node.node_id,
                    )
                )

        for node_id in sorted(duplicate_nodes):
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_NODE_ID",
                    severity="error",
                    message="Node ID must be unique.",
                    node_id=node_id,
                )
            )

        edge_keys: set[tuple[str, str, bool]] = set()
        adjacency: dict[str, list[str]] = defaultdict(list)
        incoming_count: dict[str, int] = defaultdict(int)
        outgoing_count: dict[str, int] = defaultdict(int)
        for edge in topology.edges:
            if edge.source not in node_ids:
                issues.append(
                    ValidationIssue(
                        code="EDGE_SOURCE_NOT_FOUND",
                        severity="error",
                        message="Edge source node does not exist.",
                        edge_id=edge.edge_id,
                        context={"source": edge.source},
                    )
                )
                continue
            if edge.target not in node_ids:
                issues.append(
                    ValidationIssue(
                        code="EDGE_TARGET_NOT_FOUND",
                        severity="error",
                        message="Edge target node does not exist.",
                        edge_id=edge.edge_id,
                        context={"target": edge.target},
                    )
                )
                continue
            if edge.source == edge.target:
                issues.append(
                    ValidationIssue(
                        code="SELF_LOOP",
                        severity="warning",
                        message="Self-loop edge is allowed but requires review.",
                        edge_id=edge.edge_id,
                    )
                )
            edge_key = (edge.source, edge.target, edge.directed)
            if edge_key in edge_keys:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_EDGE",
                        severity="warning",
                        message="Duplicate edge detected.",
                        edge_id=edge.edge_id,
                    )
                )
            edge_keys.add(edge_key)
            if edge.enabled:
                adjacency[edge.source].append(edge.target)
                outgoing_count[edge.source] += 1
                incoming_count[edge.target] += 1
                if not edge.directed:
                    adjacency[edge.target].append(edge.source)
                    outgoing_count[edge.target] += 1
                    incoming_count[edge.source] += 1

        if (
            topology.graph_directionality == "undirected"
            and topology.adjacency_semantics == "symmetric"
            and topology.edge_cost_directionality == "directed"
        ):
            edge_pairs = {
                (edge.source, edge.target)
                for edge in topology.edges
                if edge.enabled
            }
            for source, target in sorted(edge_pairs):
                if (target, source) not in edge_pairs:
                    issues.append(
                        ValidationIssue(
                            code="ASYMMETRIC_ADJACENCY",
                            severity="error",
                            message="Symmetric adjacency requires both directed edge records.",
                            edge_id=f"E-{source}-{target}",
                            context={"source": source, "target": target},
                        )
                    )

        exit_ids = {
            node.node_id
            for node in topology.nodes
            if node.enabled and (node.is_exit or node.is_sink or node.node_type in {"exit", "sink"})
        }
        if not exit_ids:
            issues.append(
                ValidationIssue(
                    code="NO_EXIT_OR_SINK",
                    severity="error",
                    message="Topology must define at least one exit or sink.",
                )
            )
        for node in topology.nodes:
            if not node.enabled:
                continue
            if outgoing_count[node.node_id] == 0 and incoming_count[node.node_id] == 0:
                issues.append(
                    ValidationIssue(
                        code="ISOLATED_NODE",
                        severity="warning",
                        message="Node has no enabled incident edge.",
                        node_id=node.node_id,
                    )
                )
            if node.node_id not in exit_ids and not _can_reach_any(
                node.node_id, exit_ids, adjacency
            ):
                issues.append(
                    ValidationIssue(
                        code="EXIT_UNREACHABLE",
                        severity="warning",
                        message="Node cannot reach any exit/sink using enabled directed edges.",
                        node_id=node.node_id,
                    )
                )
        if topology.coordinate_system == "unspecified_project_map":
            issues.append(
                ValidationIssue(
                    code="COORDINATE_SYSTEM_UNSPECIFIED",
                    severity="warning",
                    message=(
                        "Project topology has provisional coordinate metadata; "
                        "UI must not infer map units."
                    ),
                )
            )

        has_error = any(issue.severity == "error" for issue in issues)
        return TopologyValidationReport(
            profile_id=self.config.topology_profile_id,
            topology_id=topology.topology_id,
            topology_version=topology.topology_version,
            valid=not has_error,
            node_count=len(topology.nodes),
            edge_count=len(topology.edges),
            capacity_total=sum(node.capacity for node in topology.nodes),
            source_checksums=source_checksums,
            issues=issues,
        )

    def _technical_preflight(
        self,
        package_root: Path,
        topology_report: TopologyValidationReport,
    ) -> TechnicalPreflightReport:
        checks = [
            PreflightCheck(
                check_id="pipeline_profile",
                status=CheckStatus.PASSED,
                message=f"pipeline_profile = {self.config.pipeline_profile}",
            ),
            PreflightCheck(
                check_id="topology_package_present",
                status=CheckStatus.PASSED if package_root.exists() else CheckStatus.FAILED,
                message="Campus topology/capacity package is present.",
            ),
            PreflightCheck(
                check_id="topology_validation",
                status=CheckStatus.PASSED if topology_report.valid else CheckStatus.FAILED,
                message=f"Topology validation valid = {topology_report.valid}.",
                details={"issue_count": len(topology_report.issues)},
            ),
            PreflightCheck(
                check_id="perception_sources",
                status=CheckStatus.SKIPPED,
                message="Perception benchmark/residual sources are not required for this profile.",
            ),
            PreflightCheck(
                check_id="m5_error_realizations",
                status=CheckStatus.SKIPPED,
                message="M5 is not required; ideal observation is built from M4 scenario_gt.",
            ),
        ]
        issues = [
            PreflightIssue(
                code=issue.code,
                severity=IssueSeverity.WARNING
                if issue.severity == "warning"
                else IssueSeverity.ERROR,
                message=issue.message,
                blocking=issue.severity == "error",
                context={
                    "node_id": issue.node_id,
                    "edge_id": issue.edge_id,
                },
            )
            for issue in topology_report.issues
        ]
        return TechnicalPreflightReport(
            report_id=f"TPF-{self.config.run_id}",
            run_purpose=self.config.run_purpose,
            status=_preflight_status(issues),
            checks=checks,
            issues=issues,
        )

    def _research_preflight(self) -> ResearchPreflightReport:
        checks: list[PreflightCheck] = []
        issues: list[PreflightIssue] = []
        policy_allowed = _policy_allowed(
            self.config.scenario_policy_status,
            self.config.run_purpose,
            exploratory_draft_override=False,
        )
        checks.append(
            PreflightCheck(
                check_id=f"policy:{self.config.scenario_policy_id}",
                status=CheckStatus.PASSED if policy_allowed else CheckStatus.FAILED,
                message=(
                    f"ScenarioGenerator policy is {self.config.scenario_policy_status.value}; "
                    f"run_purpose = {self.config.run_purpose.value}."
                ),
            )
        )
        if not policy_allowed:
            issues.append(
                PreflightIssue(
                    code="SCENARIO_POLICY_NOT_ALLOWED_FOR_RUN_PURPOSE",
                    severity=IssueSeverity.ERROR,
                    message="Scenario policy is not allowed for the selected run purpose.",
                    blocking=True,
                )
            )
        if self.config.run_purpose == RunPurpose.FORMAL and self.config.include_mock_gai:
            issues.append(
                PreflightIssue(
                    code="MOCK_GAI_PROVIDER_FORBIDDEN",
                    severity=IssueSeverity.ERROR,
                    message="Mock GAI adapter cannot be used for formal output.",
                    blocking=True,
                )
            )
        checks.append(
            PreflightCheck(
                check_id="perception_not_required",
                status=CheckStatus.PASSED,
                message="Missing perception benchmark does not block Stage II ideal baseline.",
            )
        )
        checks.append(
            PreflightCheck(
                check_id="delta_r_unavailable",
                status=CheckStatus.PASSED,
                message="R_deploy and Delta R will be marked unavailable, not zero.",
            )
        )
        return ResearchPreflightReport(
            report_id=f"RPF-{self.config.run_id}",
            run_purpose=self.config.run_purpose,
            status=_preflight_status(issues),
            checks=checks,
            issues=issues,
        )

    def _generate_scenarios(self, topology: TopologySpec) -> list[ScenarioRecord]:
        eligible_nodes = [
            node
            for node in topology.nodes
            if node.enabled and not node.is_exit and not node.is_sink and node.capacity > 0
        ]
        if not eligible_nodes:
            raise DomainValidationError("No population-eligible topology nodes.")
        if self.config.total_population > sum(node.capacity for node in eligible_nodes):
            raise DomainValidationError("Scenario total population exceeds eligible capacity.")

        high_node_ids = self._resolve_high_nodes(eligible_nodes)
        high_nodes = [node for node in eligible_nodes if node.node_id in high_node_ids]
        remaining_nodes = [node for node in eligible_nodes if node.node_id not in high_node_ids]
        high_capacity = sum(node.capacity for node in high_nodes)
        high_total = min(
            round(self.config.total_population * self.config.high_population_ratio),
            high_capacity,
        )
        remaining_total = self.config.total_population - high_total
        if remaining_total > sum(node.capacity for node in remaining_nodes):
            spill = remaining_total - sum(node.capacity for node in remaining_nodes)
            high_total += spill
            remaining_total -= spill

        scenarios: list[ScenarioRecord] = []
        for scenario_index in range(self.config.scenario_count):
            scenario_id = f"SCN-{scenario_index + 1:04d}"
            seed = _stable_child_seed(self.config.root_seed, "M4", scenario_id)
            high_alloc = self._allocate_population(high_total, high_nodes, seed)
            rest_alloc = self._allocate_population(remaining_total, remaining_nodes, seed + 17)
            population_by_node = {node.node_id: 0 for node in topology.nodes}
            population_by_node.update(high_alloc)
            population_by_node.update(rest_alloc)
            entries = [
                PopulationEntry(
                    node_id=node.node_id,
                    ground_truth_population=population_by_node[node.node_id],
                    capacity=node.capacity,
                    occupancy_ratio=(
                        population_by_node[node.node_id] / node.capacity
                        if node.capacity > 0
                        else 0.0
                    ),
                    regime=_regime(
                        population_by_node[node.node_id] / node.capacity
                        if node.capacity > 0
                        else 0.0
                    ),
                )
                for node in sorted(topology.nodes, key=lambda value: _node_sort_key(value.node_id))
            ]
            scenarios.append(
                ScenarioRecord(
                    scenario_id=scenario_id,
                    scenario_seed=seed,
                    total_population=self.config.total_population,
                    high_population_node_ids=high_node_ids,
                    zone_counts=entries,
                )
            )
        return scenarios

    def _resolve_high_nodes(self, eligible_nodes: list[TopologyNode]) -> list[str]:
        if self.config.high_population_node_ids:
            eligible_ids = {node.node_id for node in eligible_nodes}
            missing = sorted(set(self.config.high_population_node_ids) - eligible_ids)
            if missing:
                raise DomainValidationError(f"High population nodes are not eligible: {missing}")
            return sorted(self.config.high_population_node_ids, key=_node_sort_key)
        top_nodes = sorted(
            eligible_nodes,
            key=lambda node: (-node.capacity, _node_sort_key(node.node_id)),
        )
        return [node.node_id for node in top_nodes[: min(3, len(top_nodes))]]

    def _allocate_population(
        self,
        total: int,
        nodes: list[TopologyNode],
        seed: int,
    ) -> dict[str, int]:
        if total == 0:
            return {node.node_id: 0 for node in nodes}
        capacity_by_node = {node.node_id: node.capacity for node in nodes}
        if total > sum(capacity_by_node.values()):
            raise DomainValidationError("Population allocation exceeds group capacity.")
        rng = random.Random(seed)
        allocation = {node.node_id: 0 for node in nodes}
        sorted_ids = sorted(capacity_by_node, key=_node_sort_key)
        for _ in range(total):
            remaining_capacity = sum(capacity_by_node[node_id] for node_id in sorted_ids)
            pick = rng.randrange(remaining_capacity)
            cursor = 0
            selected = sorted_ids[-1]
            for node_id in sorted_ids:
                cursor += capacity_by_node[node_id]
                if pick < cursor:
                    selected = node_id
                    break
            allocation[selected] += 1
            capacity_by_node[selected] -= 1
            if capacity_by_node[selected] == 0:
                sorted_ids.remove(selected)
        return allocation

    def _build_decisions(
        self,
        *,
        topology: TopologySpec,
        scenarios: list[ScenarioRecord],
        observation_checksum: str,
        topology_checksum: str,
    ) -> list[DecisionResult]:
        decisions: list[DecisionResult] = []
        interface_types: list[tuple[str, str | None]] = [("rule", None)]
        if self.config.include_mock_gai:
            interface_types.append(("gai", "mock_gai_adapter_v1"))
        capacity_checksum = topology_checksum
        for scenario in scenarios:
            actions = self._rule_actions(topology, scenario)
            input_checksum = _checksum_text(
                "|".join(
                    [
                        self.config.run_id,
                        scenario.scenario_id,
                        observation_checksum,
                        topology_checksum,
                    ]
                )
            )
            for interface_type, provider in interface_types:
                typed_interface = cast(InterfaceType, interface_type)
                decisions.append(
                    DecisionResult(
                        decision_id=f"DEC-{typed_interface.upper()}-{scenario.scenario_id}",
                        interface_type=typed_interface,
                        scenario_id=scenario.scenario_id,
                        actions=actions,
                        input_checksum=input_checksum,
                        topology_checksum=topology_checksum,
                        capacity_checksum=capacity_checksum,
                        observation_checksum=observation_checksum,
                        provider=provider,
                        policy_id=(
                            self.config.decision_policy_id
                            if interface_type == "rule"
                            else "mock_gai_capacity_relief_adapter_v1"
                        ),
                        policy_version=self.config.decision_policy_version,
                    )
                )
        return decisions

    def _rule_actions(
        self,
        topology: TopologySpec,
        scenario: ScenarioRecord,
    ) -> list[DecisionAction]:
        node_by_id = {node.node_id: node for node in topology.nodes}
        population = {
            entry.node_id: entry.ground_truth_population
            for entry in scenario.zone_counts
        }
        incoming_reserved: dict[str, int] = defaultdict(int)
        forbidden_destinations = {
            str(value)
            for value in cast(list[object], self.rules.get("forbidden_destinations", []))
        }
        actions: list[DecisionAction] = []
        high_entries = sorted(
            [
                entry
                for entry in scenario.zone_counts
                if entry.occupancy_ratio >= self.config.high_source_occupancy_threshold
                and entry.node_id in node_by_id
                and not node_by_id[entry.node_id].is_exit
                and not node_by_id[entry.node_id].is_sink
            ],
            key=lambda entry: (-entry.occupancy_ratio, _node_sort_key(entry.node_id)),
        )
        edge_candidates = self._edge_candidates(topology)
        for entry in high_entries:
            source_node = node_by_id[entry.node_id]
            desired_after = int(source_node.capacity * 0.70)
            excess = max(0, entry.ground_truth_population - desired_after)
            if excess == 0:
                continue
            for edge in edge_candidates.get(entry.node_id, []):
                target = node_by_id[edge.target]
                if target.node_id in forbidden_destinations:
                    continue
                target_limit = int(target.capacity * self.config.target_occupancy_limit)
                current_target_population = population.get(target.node_id, 0)
                margin = (
                    target_limit - current_target_population - incoming_reserved[target.node_id]
                )
                if margin <= 0:
                    continue
                move_count = min(excess, margin)
                if move_count <= 0:
                    continue
                action = DecisionAction(
                    action_id=f"A-{scenario.scenario_id}-{len(actions) + 1:03d}",
                    from_node=entry.node_id,
                    to_node=target.node_id,
                    count=move_count,
                )
                actions.append(action)
                incoming_reserved[target.node_id] += move_count
                break
        return actions

    def _edge_candidates(self, topology: TopologySpec) -> dict[str, list[TopologyEdge]]:
        candidates: dict[str, list[TopologyEdge]] = defaultdict(list)
        for edge in topology.edges:
            if not edge.enabled:
                continue
            candidates[edge.source].append(edge)
            if not edge.directed:
                candidates[edge.target].append(
                    TopologyEdge(
                        edge_id=edge.edge_id,
                        source=edge.target,
                        target=edge.source,
                        directed=edge.directed,
                        enabled=edge.enabled,
                        hops=edge.hops,
                        travel_cost=edge.travel_cost,
                        edge_capacity=edge.edge_capacity,
                        edge_type=edge.edge_type,
                    )
                )
        for source, edges in candidates.items():
            candidates[source] = sorted(
                edges,
                key=lambda edge: (edge.hops, edge.travel_cost, _node_sort_key(edge.target)),
            )
        return candidates

    def _validate_decisions(
        self,
        *,
        topology: TopologySpec,
        scenarios: list[ScenarioRecord],
        decisions: list[DecisionResult],
        ground_truth_checksum: str,
    ) -> list[ValidationResult]:
        scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios}
        node_by_id = {node.node_id: node for node in topology.nodes}
        edge_lookup = self._edge_lookup(topology)
        validations: list[ValidationResult] = []
        for decision in decisions:
            scenario = scenario_by_id[decision.scenario_id]
            population = {
                entry.node_id: entry.ground_truth_population
                for entry in scenario.zone_counts
            }
            occupancy_ratio = {
                entry.node_id: entry.occupancy_ratio
                for entry in scenario.zone_counts
            }
            violations: list[ValidationViolation] = []
            outgoing: dict[str, int] = defaultdict(int)
            incoming: dict[str, int] = defaultdict(int)
            seen_actions: set[tuple[str, str]] = set()
            for action in decision.actions:
                if (action.from_node, action.to_node) in seen_actions:
                    violations.append(
                        ValidationViolation(
                            code="DUPLICATE_ACTION",
                            message_zh_tw="同一來源與目的地出現重複 action。",
                            action_id=action.action_id,
                        )
                    )
                seen_actions.add((action.from_node, action.to_node))
                if action.from_node not in node_by_id:
                    violations.append(
                        ValidationViolation(
                            code="UNKNOWN_SOURCE_NODE",
                            message_zh_tw="action 來源節點不存在。",
                            action_id=action.action_id,
                            node_id=action.from_node,
                        )
                    )
                    continue
                if action.to_node not in node_by_id:
                    violations.append(
                        ValidationViolation(
                            code="UNKNOWN_TARGET_NODE",
                            message_zh_tw="action 目的節點不存在。",
                            action_id=action.action_id,
                            node_id=action.to_node,
                        )
                    )
                    continue
                edge = edge_lookup.get((action.from_node, action.to_node))
                if edge is None:
                    violations.append(
                        ValidationViolation(
                            code="INVALID_EDGE",
                            message_zh_tw="來源與目的節點之間沒有合法拓樸邊。",
                            action_id=action.action_id,
                        )
                    )
                elif edge.edge_capacity is not None and action.count > edge.edge_capacity:
                    violations.append(
                        ValidationViolation(
                            code="EDGE_CAPACITY_EXCEEDED",
                            message_zh_tw="移動人數超過邊容量。",
                            edge_id=edge.edge_id,
                            action_id=action.action_id,
                        )
                    )
                outgoing[action.from_node] += action.count
                incoming[action.to_node] += action.count

            for source, out_count in outgoing.items():
                if out_count > population.get(source, 0):
                    violations.append(
                        ValidationViolation(
                            code="SOURCE_UNDERFLOW",
                            message_zh_tw="來源移出人數超過 Ground Truth 人數。",
                            node_id=source,
                        )
                    )

            post_total = 0
            original_total = sum(population.values())
            for node_id, node in node_by_id.items():
                post_occupancy = population.get(node_id, 0) + incoming[node_id] - outgoing[node_id]
                post_total += post_occupancy
                if post_occupancy > node.capacity:
                    violations.append(
                        ValidationViolation(
                            code="CAPACITY_EXCEEDED",
                            message_zh_tw=(
                                f"移動後人數超過區域容量 {post_occupancy - node.capacity} 人。"
                            ),
                            node_id=node_id,
                            capacity=node.capacity,
                            post_occupancy=post_occupancy,
                            exceeded_by=post_occupancy - node.capacity,
                        )
                    )
            if post_total != original_total:
                violations.append(
                    ValidationViolation(
                        code="FLOW_CONSERVATION_FAILED",
                        message_zh_tw="移動前後總人數不一致。",
                    )
                )

            expected = sorted(
                [
                    node_id
                    for node_id, ratio in occupancy_ratio.items()
                    if ratio >= self.config.high_source_occupancy_threshold
                    and node_id in node_by_id
                    and not node_by_id[node_id].is_exit
                    and not node_by_id[node_id].is_sink
                ],
                key=_node_sort_key,
            )
            recommended = sorted(
                {action.from_node for action in decision.actions},
                key=_node_sort_key,
            )
            tp = len(set(expected) & set(recommended))
            fp = len(set(recommended) - set(expected))
            fn = len(set(expected) - set(recommended))
            fatal = any(
                violation.code
                in {
                    "UNKNOWN_SOURCE_NODE",
                    "UNKNOWN_TARGET_NODE",
                    "INVALID_EDGE",
                    "INVALID_DIRECTION",
                    "NON_POSITIVE_FLOW",
                    "SOURCE_UNDERFLOW",
                    "CAPACITY_EXCEEDED",
                    "FLOW_CONSERVATION_FAILED",
                }
                for violation in violations
            )
            legality_score = 0.0 if fatal else 1.0
            priority_score = _safe_ratio(tp, tp + fn, zero_when_empty=1.0)
            source_target_counts: dict[str, set[str]] = defaultdict(set)
            for action in decision.actions:
                source_target_counts[action.from_node].add(action.to_node)
            economy_ok = all(
                len(targets) <= self.config.max_targets_per_source
                for targets in source_target_counts.values()
            )
            economy_score = 1.0 if economy_ok else 0.0
            weighted = 0.50 * legality_score + 0.35 * priority_score + 0.15 * economy_score
            action_consistency = 0.0 if fatal and legality_score == 0.0 else weighted
            validations.append(
                ValidationResult(
                    validation_id=f"VAL-{decision.decision_id}",
                    decision_id=decision.decision_id,
                    interface_type=decision.interface_type,
                    scenario_id=decision.scenario_id,
                    valid=not violations,
                    validator_policy_id=self.config.validator_policy_id,
                    validator_policy_version=self.config.validator_policy_version,
                    ground_truth_checksum=ground_truth_checksum,
                    observation_checksum=decision.observation_checksum,
                    violations=violations,
                    legality_score=legality_score,
                    priority_score=priority_score,
                    economy_score=economy_score,
                    fatal_legality_gate=fatal,
                    action_consistency_score=action_consistency,
                    expected_high_sources=expected,
                    recommended_sources=recommended,
                    risk_tp=tp,
                    risk_fp=fp,
                    risk_fn=fn,
                )
            )
        return validations

    def _edge_lookup(self, topology: TopologySpec) -> dict[tuple[str, str], TopologyEdge]:
        lookup: dict[tuple[str, str], TopologyEdge] = {}
        for edge in topology.edges:
            if not edge.enabled:
                continue
            lookup[(edge.source, edge.target)] = edge
            if not edge.directed:
                lookup[(edge.target, edge.source)] = edge
        return lookup

    def _calculate_metrics(
        self,
        validations: list[ValidationResult],
        decisions: list[DecisionResult],
    ) -> list[MetricResult]:
        metrics: list[MetricResult] = []
        definition_ref = "docs/research_decisions/metric_definitions.md"
        for interface_type in ("rule", "gai"):
            scoped_validations = [
                validation
                for validation in validations
                if validation.interface_type == interface_type
            ]
            scoped_decisions = [
                decision for decision in decisions if decision.interface_type == interface_type
            ]
            if not scoped_decisions:
                continue
            denominator = float(len(scoped_decisions))
            invalid_count = float(
                sum(1 for decision in scoped_decisions if decision.status == "invalid_output")
            )
            violation_count = float(sum(1 for result in scoped_validations if result.violations))
            valid_count = float(sum(1 for result in scoped_validations if result.valid))
            capacity_violations = float(
                sum(
                    1
                    for result in scoped_validations
                    if any(violation.code == "CAPACITY_EXCEEDED" for violation in result.violations)
                )
            )
            topology_violations = float(
                sum(
                    1
                    for result in scoped_validations
                    if any(
                        violation.code
                        in {"INVALID_EDGE", "INVALID_DIRECTION", "UNKNOWN_TARGET_NODE"}
                        for violation in result.violations
                    )
                )
            )
            action_values = [result.action_consistency_score for result in scoped_validations]
            tp = sum(result.risk_tp for result in scoped_validations)
            fp = sum(result.risk_fp for result in scoped_validations)
            fn = sum(result.risk_fn for result in scoped_validations)
            risk_precision = _risk_precision(tp, fp, fn)
            risk_recall = _risk_recall(tp, fp, fn)
            risk_f_beta = _risk_f_beta(risk_precision, risk_recall, beta=2.0)
            filters = {"interface_type": interface_type, "trial_type": "ideal"}
            metrics.extend(
                [
                    _ratio_metric(
                        "valid_rate",
                        valid_count,
                        denominator,
                        filters,
                        self.config.metric_definition_version,
                        definition_ref,
                    ),
                    _ratio_metric(
                        "invalid_output_rate",
                        invalid_count,
                        denominator,
                        filters,
                        self.config.metric_definition_version,
                        definition_ref,
                    ),
                    _ratio_metric(
                        "rule_violation_rate",
                        violation_count,
                        denominator,
                        filters,
                        self.config.metric_definition_version,
                        definition_ref,
                    ),
                    _ratio_metric(
                        "capacity_violation_rate",
                        capacity_violations,
                        denominator,
                        filters,
                        self.config.metric_definition_version,
                        definition_ref,
                    ),
                    _ratio_metric(
                        "topology_violation_rate",
                        topology_violations,
                        denominator,
                        filters,
                        self.config.metric_definition_version,
                        definition_ref,
                    ),
                    MetricResult(
                        metric_id="action_consistency_score",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=sum(action_values) / len(action_values) if action_values else None,
                        formula_inputs={
                            "mean_legality_score": _mean(
                                [result.legality_score for result in scoped_validations]
                            ),
                            "mean_priority_score": _mean(
                                [result.priority_score for result in scoped_validations]
                            ),
                            "mean_economy_score": _mean(
                                [result.economy_score for result in scoped_validations]
                            ),
                            "fatal_legality_gate_applied": True,
                        },
                        aggregation="interface_macro",
                        n_trials=len(scoped_validations),
                        filters=filters,
                        definition_ref=definition_ref,
                    ),
                    MetricResult(
                        metric_id="risk_f_beta",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=risk_f_beta,
                        formula_inputs={
                            "risk_precision": risk_precision,
                            "risk_recall": risk_recall,
                            "beta": 2.0,
                            "tp": tp,
                            "fp": fp,
                            "fn": fn,
                        },
                        aggregation="interface_micro",
                        n_trials=len(scoped_validations),
                        filters=filters,
                        definition_ref=definition_ref,
                    ),
                    MetricResult(
                        metric_id="ideal_reliability",
                        metric_version=self.config.metric_definition_version,
                        availability="available",
                        value=sum(action_values) / len(action_values) if action_values else None,
                        formula_inputs={
                            "source_metric": "action_consistency_score",
                            "trial_type": "ideal",
                        },
                        aggregation="interface_macro",
                        n_trials=len(scoped_validations),
                        filters=filters,
                        definition_ref=definition_ref,
                    ),
                ]
            )

        for metric_id, reason in {
            "perception_mae": "Perception benchmark is unavailable and not required.",
            "perception_rmse": "Perception benchmark is unavailable and not required.",
            "perception_regime_confusion": "Perception benchmark is unavailable and not required.",
            "perturbed_R_deploy": (
                "M5 perturbed observation is not part of Stage II ideal baseline."
            ),
            "Delta_R": "Delta R requires both ideal and perturbed deployment trials.",
            "perception_error_propagation": "Perception residual propagation is outside this run.",
        }.items():
            metrics.append(
                MetricResult(
                    metric_id=metric_id,
                    metric_version=self.config.metric_definition_version,
                    availability="unavailable",
                    value=None,
                    definition_ref=definition_ref,
                    unavailable_reason=reason,
                )
            )
        return metrics

    def _render_report(
        self,
        *,
        topology: TopologySpec,
        topology_report: TopologyValidationReport,
        scenario_artifact: ArtifactRecord,
        observation_artifact: ArtifactRecord,
        metrics: list[MetricResult],
    ) -> str:
        available_metrics = [metric for metric in metrics if metric.availability == "available"]
        unavailable_metrics = [metric for metric in metrics if metric.availability == "unavailable"]
        lines = [
            "# Stage II Topology-Aware Ideal Baseline Report",
            "",
            f"- Run ID: `{self.config.run_id}`",
            f"- Pipeline profile: `{self.config.pipeline_profile}`",
            f"- Run purpose: `{self.config.run_purpose.value}`",
            "- Scope: Stage II-only ideal input baseline.",
            "- Perception benchmark, empirical residuals and M5 error injection are not required.",
            "- This report does not claim perception error propagation, "
            "perturbed R_deploy or Delta R.",
            "",
            "## Conditional DAG",
            "",
            "- Required and succeeded: M0, M3, M4, M6, M7, M8, M9.",
            "- Not required: M1, M2, M5.",
            "",
            "## Topology / Capacity",
            "",
            f"- Topology: `{topology.topology_id}` version `{topology.topology_version}`",
            f"- Nodes: {len(topology.nodes)}",
            f"- Edges: {len(topology.edges)}",
            f"- Total capacity: {sum(node.capacity for node in topology.nodes)}",
            f"- Validation issues: {len(topology_report.issues)}",
            "",
            "## Ideal Observation",
            "",
            f"- M4 scenario_gt checksum: `{scenario_artifact.checksum}`",
            f"- M6 observation checksum: `{observation_artifact.checksum}`",
            "- Observation bytes equal M4 scenario_gt bytes.",
            "- `trial_type = ideal`, `input_mode = ground_truth`, `error_realization_id = null`.",
            "",
            "## Metrics",
            "",
        ]
        for metric in available_metrics:
            lines.append(
                f"- `{metric.metric_id}` {metric.filters}: {metric.value} "
                f"(n={metric.n_trials}, aggregation={metric.aggregation})"
            )
        lines.extend(["", "## Unavailable Metrics", ""])
        for metric in unavailable_metrics:
            lines.append(f"- `{metric.metric_id}`: unavailable. {metric.unavailable_reason}")
        lines.extend(
            [
                "",
                "## Limitations",
                "",
                "- ScenarioGenerator policy is experimental, so this report is exploratory.",
                "- Mock GAI adapter is not a live provider and is forbidden for formal output.",
                "- Missing perception data is shown as not required, not as system failure.",
                "",
            ]
        )
        return "\n".join(lines)

    def _reproducibility_manifest(
        self,
        *,
        topology_checksum: str,
        scenario_checksum: str,
        observation_checksum: str,
    ) -> JsonObject:
        return {
            "run_id": self.config.run_id,
            "pipeline_profile": self.config.pipeline_profile,
            "root_seed": self.config.root_seed,
            "scenario_policy": {
                "policy_id": self.config.scenario_policy_id,
                "version": self.config.scenario_policy_version,
                "status": self.config.scenario_policy_status.value,
            },
            "decision_policy": {
                "policy_id": self.config.decision_policy_id,
                "version": self.config.decision_policy_version,
            },
            "validator_policy": {
                "policy_id": self.config.validator_policy_id,
                "version": self.config.validator_policy_version,
            },
            "topology_input": {
                "profile_id": self.config.topology_profile_id,
                "package_path": self.config.topology_package_path,
                "source_id": self.config.topology_source_id,
            },
            "hashes": {
                "topology": topology_checksum,
                "scenario_gt": scenario_checksum,
                "observation": observation_checksum,
            },
            "stage2_only": True,
            "m1_m2_m5": "not_required",
        }

    def _stage_statuses(self) -> list[StageStatusRecord]:
        records: list[StageStatusRecord] = []
        artifacts_by_stage: dict[str, list[str]] = defaultdict(list)
        for artifact in self.artifacts:
            artifacts_by_stage[artifact.stage_id].append(artifact.uri)
        for stage_id in ("M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"):
            if stage_id in NOT_REQUIRED_STAGES:
                reason = (
                    "Perception benchmark/residual is not required."
                    if stage_id in {"M1", "M2"}
                    else "Ideal observation uses M4 scenario_gt; no error injection stage is run."
                )
                records.append(
                    StageStatusRecord(stage_id=stage_id, status="not_required", reason=reason)
                )
            else:
                records.append(
                    StageStatusRecord(
                        stage_id=stage_id,
                        status="succeeded",
                        active_artifacts=artifacts_by_stage.get(stage_id, []),
                    )
                )
        return records

    def _delivery_manifest_csv(self) -> str:
        rows = [
            [
                "file_name",
                "purpose",
                "format",
                "stage",
                "schema_version",
                "row_count",
                "checksum",
            ]
        ]
        for artifact in self.artifacts:
            rows.append(
                [
                    artifact.file_name,
                    artifact.purpose,
                    artifact.media_type,
                    artifact.stage_id,
                    artifact.schema_version,
                    "" if artifact.row_count is None else str(artifact.row_count),
                    artifact.checksum,
                ]
            )
        return "\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows) + "\n"

    def _publish_json(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        payload: object,
        schema_name: str,
        row_count: int | None = None,
    ) -> ArtifactRecord:
        return self._publish_bytes(
            stage_id=stage_id,
            file_name=file_name,
            purpose=purpose,
            content=_json_bytes(payload),
            media_type="application/json",
            schema_name=schema_name,
            row_count=row_count,
        )

    def _publish_bytes(
        self,
        *,
        stage_id: str,
        file_name: str,
        purpose: str,
        content: bytes,
        media_type: str,
        schema_name: str,
        row_count: int | None,
    ) -> ArtifactRecord:
        staged = self.artifact_store.stage(
            ArtifactPayload(
                relative_path=f"runs/{self.config.run_id}/{stage_id}/{file_name}",
                content=content,
                media_type=media_type,
                artifact_type=schema_name,
                schema_name=schema_name,
                schema_version="1.0.0",
            )
        )
        self.artifact_store.verify(staged)
        published = self.artifact_store.publish(staged)
        record = ArtifactRecord(
            artifact_id=published.artifact_id,
            stage_id=stage_id,
            file_name=file_name,
            purpose=purpose,
            uri=published.uri,
            absolute_path=published.absolute_path,
            checksum=published.checksum,
            media_type=media_type,
            schema_name=schema_name,
            schema_version="1.0.0",
            row_count=row_count,
            byte_size=published.byte_size,
        )
        self.artifacts.append(record)
        return record


class BuildIdealObservationUseCase:
    """Builds the ideal observation view from M4 scenario_gt without M5."""

    def build(
        self,
        *,
        scenario_bytes: bytes,
        scenario_checksum: str,
    ) -> tuple[bytes, ObservationManifest]:
        observation_checksum = _checksum_bytes(scenario_bytes)
        if observation_checksum != scenario_checksum:
            raise DomainValidationError("Ideal observation checksum must equal M4 scenario_gt.")
        return scenario_bytes, ObservationManifest(
            scenario_gt_checksum=scenario_checksum,
            observation_checksum=observation_checksum,
        )


def _preflight_status(issues: list[PreflightIssue]) -> PreflightStatus:
    if any(issue.blocking for issue in issues):
        return PreflightStatus.FAILED
    if issues:
        return PreflightStatus.WARNING
    return PreflightStatus.PASSED


def _policy_allowed(
    status: PolicyStatus,
    run_purpose: RunPurpose,
    *,
    exploratory_draft_override: bool,
) -> bool:
    if status == PolicyStatus.DEPRECATED:
        return False
    if run_purpose == RunPurpose.DEVELOPMENT:
        return status in {PolicyStatus.DRAFT, PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED}
    if run_purpose == RunPurpose.EXPLORATORY:
        return status in {PolicyStatus.EXPERIMENTAL, PolicyStatus.APPROVED} or (
            status == PolicyStatus.DRAFT and exploratory_draft_override
        )
    return status == PolicyStatus.APPROVED


def discover_topology_triplet_options(package_root: Path) -> list[JsonObject]:
    if not package_root.exists():
        raise DomainValidationError(
            "Formal topology/capacity package is missing. "
            "Set CURRENT_PROJECT_DATA_ROOT to the project data root."
        )

    options: list[JsonObject] = []
    for prefix in _available_triplet_prefixes(package_root):
        files = _find_topology_triplet_files(package_root, prefix)
        rules = _read_json_object(files["rule"])
        neighbor_rows = _read_json_array(files["neighbors"])
        capacity_total = 0
        direct_edge_count = 0
        for item in neighbor_rows:
            if not isinstance(item, dict):
                continue
            capacity_total += _int_value(item.get("max_occupancy"), default=0)
            neighbors = item.get("neighbors", [])
            if isinstance(neighbors, list):
                direct_edge_count += len(neighbors)
        options.append(
            {
                "source_id": prefix,
                "display_name": str(rules.get("topology_id", prefix)),
                "topology_id": _slugify_topology_id(str(rules.get("topology_id", prefix))),
                "topology_profile_id": TOPOLOGY_TRIPLET_PROFILE_ID,
                "node_count": len(neighbor_rows),
                "raw_directed_neighbor_count": direct_edge_count,
                "capacity_total": capacity_total,
                "graph_directionality": str(rules.get("graph_directionality", "unknown")),
                "external_exits": [
                    str(value)
                    for value in cast(list[object], rules.get("external_exits", []))
                ],
                "source_files": {role: file_path.name for role, file_path in files.items()},
                "source_checksums": {
                    role: _checksum_file(file_path)
                    for role, file_path in files.items()
                },
            }
        )
    return options


def _available_triplet_prefixes(package_root: Path) -> list[str]:
    prefixes: list[str] = []
    for neighbors_path in package_root.glob("*_neighbors.json"):
        prefix = neighbors_path.name.removesuffix("_neighbors.json")
        try:
            _find_topology_triplet_files(package_root, prefix)
        except DomainValidationError:
            continue
        prefixes.append(prefix)
    return sorted(prefixes, key=lambda value: value.lower())


def _find_topology_triplet_files(package_root: Path, source_id: str | None) -> dict[str, Path]:
    prefix = _select_triplet_prefix(package_root, source_id)
    files = {
        "map": _map_triplet_file(package_root, prefix),
        "neighbors": package_root / f"{prefix}_neighbors.json",
        "rule": package_root / f"{prefix}_rule.json",
    }
    missing = [role for role, file_path in files.items() if not file_path.exists()]
    if missing:
        raise DomainValidationError(
            f"Topology triplet source '{prefix}' is missing required files: {missing}"
        )
    return files


def _select_triplet_prefix(package_root: Path, source_id: str | None) -> str:
    prefixes = _available_triplet_prefixes_without_validation(package_root)
    if not prefixes:
        raise DomainValidationError("No topology JSON triplet sources were found.")
    if source_id:
        for prefix in prefixes:
            if prefix == source_id:
                return prefix
        normalized = source_id.lower()
        for prefix in prefixes:
            if prefix.lower() == normalized or _slugify_topology_id(prefix) == normalized:
                return prefix
        raise DomainValidationError(f"Topology source_id is not available: {source_id}")
    if len(prefixes) == 1:
        return prefixes[0]
    raise DomainValidationError(
        "Multiple topology sources are available; topology_source_id is required."
    )


def _available_triplet_prefixes_without_validation(package_root: Path) -> list[str]:
    return sorted(
        [
            path.name.removesuffix("_neighbors.json")
            for path in package_root.glob("*_neighbors.json")
        ],
        key=lambda value: value.lower(),
    )


def _map_triplet_file(package_root: Path, prefix: str) -> Path:
    candidates = [
        package_root / f"{prefix}_map_neww.json",
        package_root / f"{prefix}_map_new.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    globbed = sorted(package_root.glob(f"{prefix}_map*.json"), key=lambda path: path.name)
    if globbed:
        return globbed[0]
    return candidates[0]


def _slugify_topology_id(value: str) -> str:
    normalized = value.strip().lower()
    output = []
    previous_separator = False
    for character in normalized:
        if character.isalnum():
            output.append(character)
            previous_separator = False
        elif not previous_separator:
            output.append("_")
            previous_separator = True
    slug = "".join(output).strip("_")
    return slug or "unknown_topology"


def _read_json_object(path: Path) -> JsonObject:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DomainValidationError(f"Expected JSON object: {path.name}")
    return cast(JsonObject, data)


def _read_json_array(path: Path) -> list[object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise DomainValidationError(f"Expected JSON array: {path.name}")
    return cast(list[object], data)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _jsonl_bytes(rows: list[object]) -> bytes:
    return b"".join(_json_bytes(row) + b"\n" for row in rows)


def _checksum_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _checksum_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _checksum_text(content: str) -> str:
    return _checksum_bytes(content.encode("utf-8"))


def _stable_child_seed(root_seed: int, stage_id: str, key: str) -> int:
    digest = hashlib.sha256(f"{root_seed}|{stage_id}|{key}".encode()).hexdigest()
    return int(digest[:16], 16) % (2**31 - 1)


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(cast(int | float | str, value))


def _int_value(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return default
        return int(float(stripped))
    if isinstance(value, float):
        return int(value)
    if isinstance(value, int):
        return value
    return int(cast(int | float | str, value))


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return _int_value(value, default=0)


def _node_sort_key(node_id: str) -> tuple[int, str]:
    return (int(node_id), node_id) if node_id.isdigit() else (10**9, node_id)


def _can_reach_any(source: str, targets: set[str], adjacency: dict[str, list[str]]) -> bool:
    seen = {source}
    queue = [source]
    while queue:
        current = queue.pop(0)
        if current in targets:
            return True
        for neighbor in adjacency.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def _regime(occupancy_ratio: float) -> Regime:
    if occupancy_ratio >= 0.66:
        return "high"
    if occupancy_ratio >= 0.33:
        return "medium"
    return "low"


def _safe_ratio(numerator: int, denominator: int, *, zero_when_empty: float) -> float:
    if denominator == 0:
        return zero_when_empty
    return numerator / denominator


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _risk_precision(tp: int, fp: int, fn: int) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0
    if tp == 0 and fp == 0 and fn > 0:
        return 0.0
    denominator = tp + fp
    return 0.0 if denominator == 0 else tp / denominator


def _risk_recall(tp: int, fp: int, fn: int) -> float:
    if tp == 0 and fp == 0 and fn == 0:
        return 1.0
    denominator = tp + fn
    return 0.0 if denominator == 0 else tp / denominator


def _risk_f_beta(precision: float, recall: float, *, beta: float) -> float:
    if precision == 0.0 and recall == 0.0:
        return 0.0
    beta_squared = beta * beta
    return (1 + beta_squared) * precision * recall / (beta_squared * precision + recall)


def _ratio_metric(
    metric_id: str,
    numerator: float,
    denominator: float,
    filters: dict[str, str],
    metric_version: str,
    definition_ref: str,
) -> MetricResult:
    return MetricResult(
        metric_id=metric_id,
        metric_version=metric_version,
        availability="available",
        value=numerator / denominator if denominator else None,
        numerator=numerator,
        denominator=denominator,
        aggregation="interface_ratio",
        n_trials=int(denominator),
        filters=filters,
        definition_ref=definition_ref,
    )


def _csv_cell(value: str) -> str:
    output = value.replace('"', '""')
    if output.startswith(("=", "+", "-", "@")):
        output = "'" + output
    if any(char in output for char in [",", '"', "\n"]):
        return f'"{output}"'
    return output
