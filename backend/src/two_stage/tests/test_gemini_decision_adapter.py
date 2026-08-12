from __future__ import annotations

import json
import unittest

from two_stage.domain.ports.decision_interface import DecisionRequestPayload
from two_stage.infrastructure.decision_adapters.gai import (
    GaiHttpAdapterConfig,
    GeminiFlashLiteDecisionAdapter,
    HttpJsonResponse,
    UnavailableGaiDecisionAdapter,
)


class FakeTransport:
    def __init__(self, responses: list[HttpJsonResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post_json(self, *, endpoint: str, payload: dict[str, object], headers: dict[str, str], timeout_ms: int) -> HttpJsonResponse:
        self.calls.append({
            "endpoint": endpoint,
            "payload": payload,
            "headers": headers,
            "timeout_ms": timeout_ms,
        })
        return self.responses.pop(0)


def _request() -> DecisionRequestPayload:
    return DecisionRequestPayload(
        experiment_id="comparison_v1",
        run_id="run-1",
        request_id="request-1",
        trial_id="trial-1",
        scenario_id="fcu_low_000",
        error_realization_id="error-1",
        observed_population=(
            {"node_id": "12", "population": 100},
            {"node_id": "13", "population": 80},
        ),
        topology={
            "topology_id": "fcu",
            "nodes": [{"id": "12"}, {"id": "13"}],
            "edges": [{"source": "12", "target": "13"}],
        },
        capacities={"12": 300, "13": 300},
        allowed_action_schema={
            "type": "object",
            "properties": {"actions": {"type": "array"}},
        },
        decision_policy_version="capacity_aware_multi_source_rule_based@1.0.0",
        input_checksum="sha256:input",
        m6_decision_context={
            "context_version": "m6_gai_decision_context_v1",
            "context_checksum": "sha256:context",
            "source_requirements": [],
        },
    )


def _success_body() -> str:
    return json.dumps({
        "candidates": [{
            "content": {
                "parts": [{"text": json.dumps({
                    "actions": [{
                        "action_id": "A-001",
                        "from_node": "12",
                        "to_node": "13",
                        "count": 20,
                    }],
                })}],
            },
            "finishReason": "STOP",
        }],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
    })


class GeminiDecisionAdapterTests(unittest.TestCase):
    def _adapter(self, transport: FakeTransport, *, retries: int = 0) -> GeminiFlashLiteDecisionAdapter:
        return GeminiFlashLiteDecisionAdapter(
            config=GaiHttpAdapterConfig(
                endpoint="https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent",
                api_key="secret-api-key",
                provider="gemini",
                model="gemini-2.5-flash-lite",
                prompt_template_version="m6_gemini_decision_v1",
                timeout_ms=1234,
                max_retries=retries,
                max_output_tokens=256,
            ),
            transport=transport,
        )

    def test_structured_request_uses_gemini_contract_and_parses_actions(self) -> None:
        transport = FakeTransport([HttpJsonResponse(200, {"content-type": "application/json"}, _success_body())])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(call.result.actions[0].from_node, "12")
        self.assertEqual(call.result.actions[0].to_node, "13")
        self.assertEqual(call.result.actions[0].count, 20)
        self.assertEqual(call.result.provider_metadata.provider, "gemini")
        self.assertEqual(call.result.provider_metadata.model, "gemini-2.5-flash-lite")
        self.assertEqual(call.result.provider_metadata.finish_reason, "STOP")
        self.assertEqual(call.result.provider_metadata.input_tokens, 120)
        self.assertEqual(call.result.provider_metadata.output_tokens, 30)

        request_call = transport.calls[0]
        self.assertTrue(str(request_call["endpoint"]).endswith(":generateContent"))
        headers = request_call["headers"]
        self.assertEqual(headers["x-goog-api-key"], "secret-api-key")
        provider_request = json.dumps(request_call["payload"], ensure_ascii=False)
        self.assertNotIn("scenario_gt", provider_request)
        self.assertNotIn("R_ideal", provider_request)
        self.assertIn("m6_decision_context", provider_request)
        self.assertIn("sha256:context", provider_request)
        self.assertIn("responseSchema", provider_request)
        self.assertNotIn("additionalProperties", provider_request)
        self.assertNotIn("secret-api-key", json.dumps(call.request_payload, ensure_ascii=False))
        self.assertIsNotNone(call.result.provider_metadata.response_checksum)

    def test_retry_on_rate_limit_preserves_input_checksum(self) -> None:
        transport = FakeTransport([
            HttpJsonResponse(429, {}, '{"error":"rate limited"}'),
            HttpJsonResponse(200, {}, _success_body()),
        ])
        call = self._adapter(transport, retries=1).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(call.result.input_checksum, "sha256:input")
        self.assertEqual(call.result.provider_metadata.retry_count, 1)

    def test_malformed_action_is_invalid_output_without_fallback(self) -> None:
        body = json.dumps({
            "candidates": [{
                "content": {"parts": [{"text": json.dumps({
                    "actions": [{
                        "action_id": "A-001",
                        "from_node": "12",
                        "to_node": "13",
                        "count": 20,
                        "rule_override": True,
                    }],
                })}]},
            }],
        })
        transport = FakeTransport([HttpJsonResponse(200, {}, body)])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "invalid_output")
        self.assertEqual(call.result.actions, ())
        self.assertEqual(call.result.provider_metadata.error_code, "GEMINI_ACTIONS_INVALID")

    def test_blocked_response_preserves_safety_reason(self) -> None:
        body = json.dumps({"promptFeedback": {"blockReason": "SAFETY"}})
        transport = FakeTransport([HttpJsonResponse(200, {}, body)])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "invalid_output")
        self.assertEqual(call.result.provider_metadata.error_code, "GEMINI_NO_CANDIDATE")
        self.assertEqual(call.result.provider_metadata.safety_block_reason, "SAFETY")

    def test_api_error_does_not_expose_secret_in_trace(self) -> None:
        transport = FakeTransport([HttpJsonResponse(403, {"x": "y"}, '{"error":"secret-api-key"}')])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "error")
        self.assertNotIn("secret-api-key", json.dumps(call.request_payload, ensure_ascii=False))
        self.assertNotIn("secret-api-key", json.dumps(call.raw_response_payload, ensure_ascii=False))
        self.assertEqual(call.result.provider_metadata.http_status, 403)

    def test_unavailable_adapter_preserves_explicit_failure_reason(self) -> None:
        call = UnavailableGaiDecisionAdapter(
            error_code="GAI_BUDGET_EXCEEDED",
            error_message="budget exhausted",
        ).decide_with_trace(_request())

        self.assertEqual(call.result.status, "unavailable")
        self.assertEqual(call.result.provider_metadata.error_code, "GAI_BUDGET_EXCEEDED")
        self.assertEqual(call.result.provider_metadata.error_message, "budget exhausted")
        self.assertEqual(call.raw_response_payload["error_code"], "GAI_BUDGET_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
