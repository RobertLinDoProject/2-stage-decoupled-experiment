"""CLI wrapper for the shared topology flow preview read-model service."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from two_stage.application.services.topology_flow_preview import (  # noqa: E402
    DEFAULT_RUN_ID,
    SCHEMA_VERSION,
    build_preview,
    normalize_reason,
    normalized_grid,
)
from two_stage.application.services.topology_preview_layouts import preview_layout  # noqa: E402, F401


def main() -> int:
    parser = argparse.ArgumentParser(description="Build topology flow preview read model")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--condition-id", default="fcu__yolov8_det_v1")
    parser.add_argument("--regime", choices=("LOW", "MEDIUM", "HIGH"), default="MEDIUM")
    parser.add_argument("--trial-index", type=int, default=0)
    parser.add_argument("--interface", choices=("rule_based", "gai", "gai_reserved"), default="rule_based")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    run_root = source_root / "storage" / "published" / "runs" / args.run_id
    if not run_root.is_dir():
        raise SystemExit(f"Published run not found: {run_root}")

    output_dir = args.output_dir or source_root / "prototype_data"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_preview(run_root, args.condition_id, args.regime, args.trial_index, args.interface)
    output = output_dir / f"topology-flow-preview-{args.condition_id}-{args.regime.lower()}-{args.trial_index:04d}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "scenario_id": payload["metadata"]["scenario_id"],
        "pair_id": payload["metadata"]["pair_id"],
        "schema_version": SCHEMA_VERSION,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
