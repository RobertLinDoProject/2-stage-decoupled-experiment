from __future__ import annotations

import json
import unittest

from two_stage.domain.ports.decision_interface import DecisionRequestPayload
from two_stage.infrastructure.decision_adapters.gai import (
    GaiHttpAdapterConfig,
    HttpJsonResponse,
    OllamaActionDecisionAdapter,
)


class FakeTransport:
    def __init__(self, responses: list[HttpJsonResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post_json(self, *, endpoint: str, payload: dict[str, object], headers: dict[str, str], timeout_ms: int) -> HttpJsonResponse:
        self.calls.append({"endpoint": endpoint, "payload": payload, "headers": headers, "timeout_ms": timeout_ms})
        return self.responses.pop(0)


def _request() -> DecisionRequestPayload:
    step = {
        "action_id": "A-0001",
        "from_node": "11",
        "source_visible_population": 900,
        "remaining_required_move_count": 600,
        "candidates": [
            {"target_id": "8", "target_type": "zone", "external_exit": False, "max_count": 600, "total_cost": 2},
            {"target_id": "E1", "target_type": "exit", "external_exit": True, "max_count": 600, "total_cost": 4},
        ],
        "seed": 114,
    }
    return DecisionRequestPayload(
        experiment_id="comparison_v1",
        run_id="run-1",
        request_id="request-1",
        trial_id="trial-1",
        scenario_id="fcu_medium_000",
        error_realization_id="",
        observed_population=tuple(),
        topology={},
        capacities={},
        allowed_action_schema={},
        decision_policy_version="capacity_aware_multi_source_rule_based@1.0.0",
        input_checksum="sha256:step",
        m6_decision_context={"action_step": step},
    )


def _body(action: dict[str, object]) -> str:
    return json.dumps({
        "message": {"role": "assistant", "content": json.dumps(action)},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 120,
        "eval_count": 20,
    })


class OllamaActionAdapterTests(unittest.TestCase):
    def _adapter(self, transport: FakeTransport, *, retries: int = 0) -> OllamaActionDecisionAdapter:
        return OllamaActionDecisionAdapter(
            config=GaiHttpAdapterConfig(
                endpoint="http://host.docker.internal:11434/api/chat",
                provider="ollama",
                model="mistral:7b-instruct-v0.3-q4_K_M",
                prompt_template_version="m6_ollama_action_v1",
                timeout_ms=120000,
                max_retries=retries,
                max_output_tokens=64,
                num_ctx=2048,
                keep_alive="5m",
                seed=114,
            ),
            transport=transport,
        )

    def test_parses_one_canonical_action_and_preserves_checksums(self) -> None:
        transport = FakeTransport([HttpJsonResponse(200, {}, _body({
            "action_id": "A-0001",
            "from_node": "11",
            "to_node": "8",
            "count": 600,
        }))])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(len(call.result.actions), 1)
        self.assertEqual(call.result.actions[0].count, 600)
        self.assertEqual(call.result.provider_metadata.provider, "ollama")
        self.assertIsNotNone(call.result.provider_metadata.request_checksum)
        self.assertIsNotNone(call.result.provider_metadata.response_checksum)
        provider_request = json.dumps(transport.calls[0]["payload"], ensure_ascii=False)
        self.assertIn("m6_ollama_action_v1", json.dumps(call.request_payload, ensure_ascii=False))
        self.assertNotIn("scenario_gt", provider_request)
        self.assertNotIn("ground_truth_population", provider_request)
        self.assertNotIn("R_ideal", provider_request)

    def test_extra_fields_are_invalid_without_fallback(self) -> None:
        transport = FakeTransport([HttpJsonResponse(200, {}, _body({
            "action_id": "A-0001",
            "from_node": "11",
            "to_node": "8",
            "count": 600,
            "explanation": "not allowed",
        }))])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "invalid_output")
        self.assertEqual(call.result.actions, ())
        self.assertEqual(call.result.provider_metadata.error_code, "OLLAMA_ACTION_SCHEMA_INVALID")

    def test_transport_retry_preserves_input_checksum(self) -> None:
        transport = FakeTransport([
            HttpJsonResponse(503, {}, "busy"),
            HttpJsonResponse(200, {}, _body({
                "action_id": "A-0001",
                "from_node": "11",
                "to_node": "8",
                "count": 600,
            })),
        ])
        call = self._adapter(transport, retries=1).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(call.result.input_checksum, "sha256:step")
        self.assertEqual(call.result.provider_metadata.retry_count, 1)


if __name__ == "__main__":
    unittest.main()
