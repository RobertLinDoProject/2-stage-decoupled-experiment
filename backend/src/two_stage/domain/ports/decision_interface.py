from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

JsonDict = dict[str, Any]
DecisionInterfaceType = Literal["rule", "gai"]
DecisionStatus = Literal[
    "parsed",
    "invalid_output",
    "unavailable",
    "timeout",
    "error",
    "quota_exhausted",
    "rate_limited",
]


@dataclass(frozen=True, slots=True)
class DecisionActionPayload:
    action_id: str
    from_node: str
    to_node: str
    count: int


@dataclass(frozen=True, slots=True)
class DecisionRequestPayload:
    experiment_id: str
    run_id: str
    request_id: str
    trial_id: str
    scenario_id: str
    error_realization_id: str
    observed_population: tuple[JsonDict, ...]
    topology: JsonDict
    capacities: JsonDict
    allowed_action_schema: JsonDict
    decision_policy_version: str
    input_checksum: str
    m6_decision_context: JsonDict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GaiInvocationMetadata:
    provider: str
    model: str
    model_version: str | None = None
    prompt_template_version: str | None = None
    temperature: float | None = None
    seed: int | None = None
    request_checksum: str | None = None
    raw_response_ref: str | None = None
    parsed_response_ref: str | None = None
    parse_repair_applied: bool = False
    retry_count: int = 0
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    timeout_ms: int | None = None
    response_checksum: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    safety_block_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionInterfaceResult:
    decision_id: str
    interface_type: DecisionInterfaceType
    scenario_id: str
    error_realization_id: str
    status: DecisionStatus
    actions: tuple[DecisionActionPayload, ...] = field(default_factory=tuple)
    raw_output_ref: str | None = None
    input_checksum: str = ""
    provider_metadata: GaiInvocationMetadata | None = None


class DecisionInterfacePort(Protocol):
    interface_type: DecisionInterfaceType
    policy_id: str
    policy_version: str

    def decide(self, request: DecisionRequestPayload) -> DecisionInterfaceResult:
        ...
