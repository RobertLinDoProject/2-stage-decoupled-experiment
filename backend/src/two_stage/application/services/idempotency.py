from __future__ import annotations

import hashlib


def stable_idempotency_key(*parts: str) -> str:
    normalized = "\x1f".join(parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def run_creation_key(snapshot_id: str, run_purpose: str, client_request_id: str) -> str:
    return stable_idempotency_key("run_creation", snapshot_id, run_purpose, client_request_id)


def stage_output_key(
    run_id: str,
    stage_id: str,
    stage_version: str,
    config_hash: str,
    ordered_input_checksums: list[str],
) -> str:
    return stable_idempotency_key(
        "stage_output",
        run_id,
        stage_id,
        stage_version,
        config_hash,
        *ordered_input_checksums,
    )


def decision_attempt_key(
    run_id: str,
    scenario_id: str,
    realization_id: str,
    interface_type: str,
    input_mode: str,
) -> str:
    return stable_idempotency_key(
        "decision_attempt",
        run_id,
        scenario_id,
        realization_id,
        interface_type,
        input_mode,
    )
