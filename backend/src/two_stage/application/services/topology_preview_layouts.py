"""Visualization-only layouts derived from the three topology reference drawings.

These coordinates are not part of the canonical topology contract. They are used
only by the topology flow preview to place the existing M3 nodes on a readable
canvas that resembles the supplied site drawings.
"""

from __future__ import annotations

import math
from typing import Any


BASE_LAYOUT_VERSION = "ppt_reference_v1"
LAYOUT_VERSION = "ppt_reference_spread_v2"
LAYOUT_MIN_GAP = 1.0
LAYOUT_MAX_DISPLACEMENT = 10.0
LAYOUT_ITERATIONS = 40
CANVAS_PADDING = 8.0
NODE_RADII = {"exit": 3.1, "zone": 3.6}
# Keep a small numerical buffer so the published coordinates remain outside
# the visual collision threshold after repeated floating-point adjustments.
RELAXATION_EPSILON = 1e-3


def _positions(**items: tuple[float, float]) -> dict[str, dict[str, float]]:
    return {node_id: {"x": x, "y": y} for node_id, (x, y) in items.items()}


TOPOLOGY_PREVIEW_LAYOUTS: dict[str, dict[str, Any]] = {
    "fcu": {
        "source": "3 topologies drawing origin.pptx",
        "aspect_ratio": 1.1915,
        "positions": _positions(
            E1=(0.383, 0.029), E2=(0.034, 0.721), E3=(0.075, 0.945), E4=(0.744, 0.856),
            P1=(0.034, 0.500), P2=(0.744, 0.205), P3=(0.178, 0.856), P4=(0.372, 0.945),
            **{
                "1": (0.076, 0.166), "2": (0.140, 0.144), "3": (0.308, 0.126),
                "4": (0.482, 0.115), "5": (0.638, 0.101), "6": (0.606, 0.161),
                "7": (0.159, 0.271), "8": (0.304, 0.281), "9": (0.541, 0.292),
                "10": (0.154, 0.418), "11": (0.289, 0.420), "12": (0.531, 0.404),
                "13": (0.432, 0.460), "14": (0.541, 0.461), "15": (0.638, 0.462),
                "16": (0.825, 0.551), "17": (0.137, 0.546), "18": (0.305, 0.541),
                "19": (0.438, 0.547), "20": (0.589, 0.540), "21": (0.167, 0.640),
                "22": (0.314, 0.642), "23": (0.441, 0.640), "24": (0.595, 0.639),
                "25": (0.178, 0.789), "26": (0.288, 0.791), "27": (0.383, 0.793),
                "28": (0.482, 0.777), "29": (0.623, 0.779), "30": (0.372, 0.870),
                "31": (0.501, 0.870), "32": (0.619, 0.868),
            },
        ),
    },
    "taichung_lantern_festival": {
        "source": "3 topologies drawing origin.pptx",
        "aspect_ratio": 0.7895,
        "positions": _positions(
            E1=(0.198, 0.680), E2=(0.198, 0.345), E3=(0.204, 0.088),
            E4=(0.704, 0.106), E5=(0.704, 0.910),
            **{
                "1": (0.310, 0.720), "2": (0.430, 0.700), "3": (0.365, 0.475),
                "4": (0.250, 0.620), "5": (0.250, 0.305), "6": (0.350, 0.175),
                "7": (0.535, 0.355), "8": (0.520, 0.820), "9": (0.470, 0.890),
            },
        ),
    },
    "taipei_new_years_eve": {
        "source": "3 topologies drawing origin.pptx",
        "aspect_ratio": 1.3021,
        "positions": _positions(
            **{
                "E1": (0.456, 0.620), "E2": (0.846, 0.620), "E3": (0.693, 0.118),
                "E4": (0.410, 0.118), "E5": (0.303, 0.118), "E6": (0.067, 0.118),
                "1": (0.456, 0.415), "2": (0.530, 0.760), "3": (0.424, 0.880),
                "4": (0.915, 0.735), "5": (0.196, 0.112),
            },
        ),
    },
}


def _natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for item in value.split("-"):
        parts.append((0, int(item)) if item.isdigit() else (1, item))
    return tuple(parts)


def _radius(node_id: str) -> float:
    return NODE_RADII["exit"] if str(node_id).startswith("E") else NODE_RADII["zone"]


def _spread_positions(layout: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Relax only ordinary nodes while keeping the reference anchors fixed."""

    aspect_ratio = float(layout["aspect_ratio"])
    width = 100.0
    height = width / aspect_ratio
    base_positions = layout["positions"]
    points = {
        node_id: [
            CANVAS_PADDING + (width - 2 * CANVAS_PADDING) * float(position["x"]),
            CANVAS_PADDING + (height - 2 * CANVAS_PADDING) * float(position["y"]),
        ]
        for node_id, position in base_positions.items()
    }
    reference_points = {node_id: list(point) for node_id, point in points.items()}
    locked = {node_id for node_id in points if str(node_id).startswith(("E", "P"))}
    node_ids = sorted(points, key=_natural_key)

    for _ in range(LAYOUT_ITERATIONS):
        changed = 0.0
        for index, first_id in enumerate(node_ids):
            for second_id in node_ids[index + 1:]:
                first = points[first_id]
                second = points[second_id]
                dx = second[0] - first[0]
                dy = second[1] - first[1]
                distance = math.hypot(dx, dy)
                if distance < 1e-9:
                    dx, dy, distance = 1.0, 0.0, 1.0
                required = (
                    _radius(first_id)
                    + _radius(second_id)
                    + LAYOUT_MIN_GAP
                    + RELAXATION_EPSILON
                )
                if distance >= required:
                    continue

                amount = required - distance
                unit_x, unit_y = dx / distance, dy / distance
                if first_id in locked and second_id in locked:
                    continue
                if first_id in locked:
                    moves = ((second_id, amount * unit_x, amount * unit_y),)
                elif second_id in locked:
                    moves = ((first_id, -amount * unit_x, -amount * unit_y),)
                else:
                    moves = (
                        (first_id, -amount * unit_x / 2, -amount * unit_y / 2),
                        (second_id, amount * unit_x / 2, amount * unit_y / 2),
                    )
                for node_id, move_x, move_y in moves:
                    points[node_id][0] += move_x
                    points[node_id][1] += move_y
                    changed += abs(move_x) + abs(move_y)

        for node_id in node_ids:
            if node_id in locked:
                continue
            start_x, start_y = reference_points[node_id]
            delta_x = points[node_id][0] - start_x
            delta_y = points[node_id][1] - start_y
            displacement = math.hypot(delta_x, delta_y)
            if displacement > LAYOUT_MAX_DISPLACEMENT:
                scale = LAYOUT_MAX_DISPLACEMENT / displacement
                points[node_id][0] = start_x + delta_x * scale
                points[node_id][1] = start_y + delta_y * scale
            radius = _radius(node_id)
            points[node_id][0] = min(
                max(CANVAS_PADDING + radius, points[node_id][0]),
                width - CANVAS_PADDING - radius,
            )
            points[node_id][1] = min(
                max(CANVAS_PADDING + radius, points[node_id][1]),
                height - CANVAS_PADDING - radius,
            )
        if changed < 1e-6:
            break

    return {
        node_id: (
            dict(base_positions[node_id])
            if node_id in locked
            else {
                "x": (point[0] - CANVAS_PADDING) / (width - 2 * CANVAS_PADDING),
                "y": (point[1] - CANVAS_PADDING) / (height - 2 * CANVAS_PADDING),
            }
        )
        for node_id, point in points.items()
    }


def preview_layout(topology_id: str) -> dict[str, Any] | None:
    """Return a defensive copy of a known preview layout."""

    layout = TOPOLOGY_PREVIEW_LAYOUTS.get(str(topology_id))
    if layout is None:
        return None
    return {
        "source": layout["source"],
        "base_version": BASE_LAYOUT_VERSION,
        "version": LAYOUT_VERSION,
        "min_gap": LAYOUT_MIN_GAP,
        "max_displacement": LAYOUT_MAX_DISPLACEMENT,
        "aspect_ratio": float(layout["aspect_ratio"]),
        "base_positions": {node_id: dict(position) for node_id, position in layout["positions"].items()},
        "positions": _spread_positions(layout),
    }
