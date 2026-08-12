from __future__ import annotations

from collections.abc import Iterable

from two_stage.domain.entities.policy import PolicyDefinition, PolicyRef
from two_stage.domain.enums import RunPurpose
from two_stage.domain.errors import PolicyNotAllowedError
from two_stage.policy.compatibility import is_policy_allowed


class InMemoryPolicyRegistry:
    def __init__(self, definitions: Iterable[PolicyDefinition] = ()) -> None:
        self._definitions: dict[tuple[str, str], PolicyDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: PolicyDefinition) -> None:
        key = (definition.policy_id, definition.version)
        if key in self._definitions:
            raise ValueError(
                f"Duplicate policy definition: {definition.policy_id}@{definition.version}"
            )
        self._definitions[key] = definition

    def get(self, ref: PolicyRef) -> PolicyDefinition:
        try:
            return self._definitions[(ref.policy_id, ref.version)]
        except KeyError as exc:
            raise KeyError(f"Policy not found: {ref.policy_id}@{ref.version}") from exc

    def assert_allowed(
        self,
        ref: PolicyRef,
        run_purpose: RunPurpose,
        *,
        exploratory_draft_override: bool = False,
    ) -> None:
        definition = self.get(ref)
        allowed = is_policy_allowed(
            definition.status,
            run_purpose,
            approved_for_formal_run=definition.approved_for_formal_run,
            exploratory_draft_override=exploratory_draft_override,
        )
        if not allowed:
            message = (
                f"Policy {definition.policy_id}@{definition.version} with status "
                f"{definition.status.value} is not allowed for {run_purpose.value} run."
            )
            raise PolicyNotAllowedError(message)

    def list(self) -> list[PolicyDefinition]:
        return list(self._definitions.values())
