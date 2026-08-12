from __future__ import annotations

import heapq
from typing import Any


ADJACENCY_SEMANTICS = "symmetric"
EDGE_COST_DIRECTIONALITY = "directed"


class TopologyInputContractError(ValueError):
    """Raised when a topology triplet cannot be used as a runtime input."""


def validate_topology_triplet(
    map_rows: object,
    neighbor_rows: object,
    rules: object,
    *,
    source_label: str = "topology triplet",
) -> dict[str, Any]:
    """Validate undirected adjacency with direction-dependent edge costs.

    ``graph_directionality`` describes adjacency, while
    ``edge_cost_directionality`` describes the cost attached to each explicit
    source -> target record.  Both directions must be present, but their costs
    are intentionally allowed to differ.
    """

    issues: list[dict[str, Any]] = []
    if not isinstance(map_rows, list):
        issues.append({"code": "MAP_NOT_ARRAY", "message": "map JSON must be an array."})
        map_rows = []
    if not isinstance(neighbor_rows, list):
        issues.append({"code": "NEIGHBORS_NOT_ARRAY", "message": "neighbors JSON must be an array."})
        neighbor_rows = []
    if not isinstance(rules, dict):
        issues.append({"code": "RULES_NOT_OBJECT", "message": "rule JSON must be an object."})
        rules = {}

    map_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(map_rows):
        if not isinstance(row, dict):
            issues.append({"code": "MAP_ROW_NOT_OBJECT", "index": index})
            continue
        node_id = str(row.get("id", "")).strip()
        if not node_id:
            issues.append({"code": "MAP_NODE_ID_MISSING", "index": index})
            continue
        if node_id in map_by_id:
            issues.append({"code": "DUPLICATE_NODE_ID", "node_id": node_id})
            continue
        capacity = row.get("max_occupancy")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
            issues.append({"code": "INVALID_CAPACITY", "node_id": node_id})
        nearby = row.get("nearby_zone", [])
        if not isinstance(nearby, list):
            issues.append({"code": "NEARBY_ZONE_NOT_ARRAY", "node_id": node_id})
        map_by_id[node_id] = row

    neighbor_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(neighbor_rows):
        if not isinstance(row, dict):
            issues.append({"code": "NEIGHBOR_ROW_NOT_OBJECT", "index": index})
            continue
        node_id = str(row.get("id", "")).strip()
        if not node_id:
            issues.append({"code": "NEIGHBOR_NODE_ID_MISSING", "index": index})
            continue
        if node_id in neighbor_by_id:
            issues.append({"code": "DUPLICATE_NEIGHBOR_NODE_ID", "node_id": node_id})
            continue
        capacity = row.get("max_occupancy")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
            issues.append({"code": "INVALID_NEIGHBOR_CAPACITY", "node_id": node_id})
        traversal_cost = row.get("traversal_cost", 0)
        if not isinstance(traversal_cost, int) or isinstance(traversal_cost, bool) or traversal_cost < 0:
            issues.append({"code": "INVALID_TRAVERSAL_COST", "node_id": node_id})
        neighbors = row.get("neighbors", [])
        if not isinstance(neighbors, list):
            issues.append({"code": "NEIGHBORS_FIELD_NOT_ARRAY", "node_id": node_id})
        neighbor_by_id[node_id] = row

    if set(map_by_id) != set(neighbor_by_id):
        issues.append({
            "code": "MAP_NEIGHBOR_NODE_SET_MISMATCH",
            "map_only": sorted(set(map_by_id) - set(neighbor_by_id)),
            "neighbors_only": sorted(set(neighbor_by_id) - set(map_by_id)),
        })

    for node_id in sorted(set(map_by_id) & set(neighbor_by_id)):
        map_capacity = map_by_id[node_id].get("max_occupancy")
        neighbor_capacity = neighbor_by_id[node_id].get("max_occupancy")
        if map_capacity != neighbor_capacity:
            issues.append({
                "code": "CAPACITY_MISMATCH",
                "node_id": node_id,
                "map_capacity": map_capacity,
                "neighbor_capacity": neighbor_capacity,
            })

    graph: dict[str, dict[str, int]] = {node_id: {} for node_id in neighbor_by_id}
    for source_id, row in neighbor_by_id.items():
        neighbors = row.get("neighbors", [])
        if not isinstance(neighbors, list):
            continue
        for neighbor in neighbors:
            if not isinstance(neighbor, dict):
                issues.append({"code": "NEIGHBOR_ENTRY_NOT_OBJECT", "source_id": source_id})
                continue
            target_id = str(neighbor.get("id", "")).strip()
            cost = neighbor.get("cost")
            if target_id not in neighbor_by_id:
                issues.append({
                    "code": "EDGE_TARGET_NOT_FOUND",
                    "source_id": source_id,
                    "target_id": target_id,
                })
            if target_id == source_id:
                issues.append({"code": "SELF_LOOP", "node_id": source_id})
            if target_id in graph[source_id]:
                issues.append({
                    "code": "DUPLICATE_EDGE",
                    "source_id": source_id,
                    "target_id": target_id,
                })
            if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
                issues.append({
                    "code": "INVALID_EDGE_COST",
                    "source_id": source_id,
                    "target_id": target_id,
                })
                continue
            graph[source_id][target_id] = cost

    graph_directionality = str(rules.get("graph_directionality", "")).lower()
    adjacency_semantics = str(rules.get("adjacency_semantics", "")).lower()
    cost_directionality = str(rules.get("edge_cost_directionality", "")).lower()
    if graph_directionality != "undirected":
        issues.append({"code": "GRAPH_DIRECTIONALITY_NOT_UNDIRECTED", "value": graph_directionality})
    if adjacency_semantics != ADJACENCY_SEMANTICS:
        issues.append({"code": "ADJACENCY_SEMANTICS_INVALID", "value": adjacency_semantics})
    if cost_directionality != EDGE_COST_DIRECTIONALITY:
        issues.append({"code": "EDGE_COST_DIRECTIONALITY_INVALID", "value": cost_directionality})

    exits = rules.get("external_exits", [])
    if not isinstance(exits, list):
        issues.append({"code": "EXTERNAL_EXITS_NOT_ARRAY"})
        exits = []
    missing_exits = sorted(str(exit_id) for exit_id in exits if str(exit_id) not in neighbor_by_id)
    if missing_exits:
        issues.append({"code": "EXTERNAL_EXIT_NOT_FOUND", "node_ids": missing_exits})

    missing_reverse: list[dict[str, str]] = []
    for source_id, targets in graph.items():
        for target_id in targets:
            if source_id not in graph.get(target_id, {}):
                missing_reverse.append({"source_id": source_id, "target_id": target_id})
    if missing_reverse:
        issues.append({"code": "ASYMMETRIC_ADJACENCY", "edges": missing_reverse})

    asymmetric_cost_pairs = [
        {
            "source_id": source_id,
            "target_id": target_id,
            "forward_cost": graph[source_id][target_id],
            "reverse_cost": graph[target_id][source_id],
        }
        for source_id, targets in graph.items()
        for target_id in targets
        if source_id in graph.get(target_id, {})
        and graph[source_id][target_id] != graph[target_id][source_id]
        and source_id < target_id
    ]

    return {
        "status": "PASSED" if not issues else "FAILED",
        "source_label": source_label,
        "node_count": len(neighbor_by_id),
        "directed_edge_count": sum(len(targets) for targets in graph.values()),
        "external_exits": sorted(str(exit_id) for exit_id in exits),
        "asymmetric_cost_pairs": asymmetric_cost_pairs,
        "issues": issues,
    }


def rebuild_nearby_zone(
    map_rows: list[dict[str, Any]],
    neighbor_rows: list[dict[str, Any]],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rebuild cost-limited nearby_zone with direction-specific edge costs."""

    node_order = [str(row["id"]) for row in neighbor_rows]
    order_index = {node_id: index for index, node_id in enumerate(node_order)}
    row_by_id = {str(row["id"]): row for row in neighbor_rows}
    graph = {
        source_id: [
            (str(neighbor["id"]), int(neighbor["cost"]))
            for neighbor in row.get("neighbors", [])
        ]
        for source_id, row in row_by_id.items()
    }
    exits = {str(value) for value in rules.get("external_exits", [])}
    max_total_cost = int(rules.get("max_total_cost", 3))
    rebuilt_by_id: dict[str, list[dict[str, Any]]] = {}

    for start_id in node_order:
        distances = {node_id: float("inf") for node_id in node_order}
        distances[start_id] = 0
        queue: list[tuple[int, int, str]] = [(0, order_index[start_id], start_id)]
        while queue:
            current_cost, _, current_id = heapq.heappop(queue)
            if current_cost != distances[current_id] or current_cost > max_total_cost:
                continue
            if current_id in exits:
                continue
            traversal_cost = 0 if current_id == start_id else int(row_by_id[current_id].get("traversal_cost", 0))
            for target_id, edge_cost in graph[current_id]:
                next_cost = current_cost + traversal_cost + edge_cost
                if next_cost > max_total_cost or next_cost >= distances[target_id]:
                    continue
                distances[target_id] = next_cost
                heapq.heappush(queue, (next_cost, order_index[target_id], target_id))
        nearby = [
            {"id": target_id, "hops": int(distance)}
            for target_id, distance in distances.items()
            if target_id != start_id and 1 <= distance <= max_total_cost
        ]
        nearby.sort(key=lambda item: (item["hops"], order_index[item["id"]]))
        rebuilt_by_id[start_id] = nearby

    output: list[dict[str, Any]] = []
    for row in map_rows:
        copied = dict(row)
        copied["nearby_zone"] = rebuilt_by_id[str(row["id"])]
        output.append(copied)
    return output
