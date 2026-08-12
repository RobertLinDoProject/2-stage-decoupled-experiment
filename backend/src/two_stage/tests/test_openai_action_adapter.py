import json
import unittest

from two_stage.domain.ports.decision_interface import DecisionRequestPayload
from two_stage.infrastructure.decision_adapters.gai import (
    GaiHttpAdapterConfig,
    HttpJsonResponse,
    OpenAIActionDecisionAdapter,
)


class FakeTransport:
    def __init__(self, responses: list[HttpJsonResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post_json(self, *, endpoint: str, payload: dict[str, object], headers: dict[str, str], timeout_ms: int) -> HttpJsonResponse:
        self.calls.append({"endpoint": endpoint, "payload": payload, "headers": headers, "timeout_ms": timeout_ms})
        return self.responses.pop(0)


def _request() -> DecisionRequestPayload:
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
        m6_decision_context={
            "action_step": {
                "action_id": "A-0001",
                "from_node": "11",
                "source_visible_population": 900,
                "remaining_required_move_count": 600,
                "candidates": [{"target_id": "8", "max_count": 600}],
                "seed": 114,
            }
        },
    )


class OpenAIActionAdapterTests(unittest.TestCase):
    def _adapter(self, transport: FakeTransport, *, retries: int = 0) -> OpenAIActionDecisionAdapter:
        return OpenAIActionDecisionAdapter(
            config=GaiHttpAdapterConfig(
                endpoint="https://api.openai.com/v1/responses",
                api_key="sk-test-secret",
                provider="openai",
                model="gpt-5-nano-2025-08-07",
                prompt_template_version="m6_openai_action_v1",
                timeout_ms=30000,
                max_retries=retries,
                max_output_tokens=64,
                reasoning_effort="low",
                seed=114,
            ),
            transport=transport,
        )

    def test_responses_structured_output_is_parsed_without_truth_fields(self) -> None:
        body = json.dumps({
            "id": "resp_test",
            "status": "completed",
            "output_text": json.dumps({"action_id": "A-0001", "from_node": "11", "to_node": "8", "count": 600}),
            "usage": {"input_tokens": 50, "output_tokens": 20},
        })
        transport = FakeTransport([HttpJsonResponse(200, {}, body)])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(call.result.actions[0].count, 600)
        self.assertEqual(call.result.provider_metadata.provider, "openai")
        self.assertEqual(call.result.provider_metadata.model, "gpt-5-nano-2025-08-07")
        sent = json.dumps(transport.calls[0]["payload"], ensure_ascii=False)
        self.assertNotIn("ground_truth_population", sent)
        self.assertNotIn("scenario_gt", sent)
        self.assertNotIn("R_deploy", sent)
        self.assertNotIn("sk-test-secret", json.dumps(call.raw_response_payload))
        self.assertEqual(transport.calls[0]["payload"]["reasoning"], {"effort": "low"})

    def test_quota_error_is_classified_separately(self) -> None:
        body = json.dumps({"error": {"code": "insufficient_quota", "message": "quota exhausted"}})
        transport = FakeTransport([HttpJsonResponse(429, {}, body)])
        call = self._adapter(transport).decide_with_trace(_request())

        self.assertEqual(call.result.status, "quota_exhausted")
        self.assertEqual(call.result.provider_metadata.error_code, "OPENAI_QUOTA_EXHAUSTED")
        self.assertNotIn("sk-test-secret", json.dumps(call.raw_response_payload))

    def test_rate_limit_retries_without_becoming_quota(self) -> None:
        body = json.dumps({
            "status": "completed",
            "output_text": json.dumps({"action_id": "A-0001", "from_node": "11", "to_node": "8", "count": 600}),
        })
        transport = FakeTransport([HttpJsonResponse(429, {}, json.dumps({"error": {"code": "rate_limit_exceeded"}})), HttpJsonResponse(200, {}, body)])
        call = self._adapter(transport, retries=1).decide_with_trace(_request())

        self.assertEqual(call.result.status, "parsed")
        self.assertEqual(len(transport.calls), 2)


if __name__ == "__main__":
    unittest.main()
