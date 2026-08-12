from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from two_stage.domain.enums import PolicyStatus


@dataclass(frozen=True, slots=True)
class PolicyRef:
    policy_id: str
    version: str


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    policy_id: str
    policy_type: str
    version: str
    status: PolicyStatus
    implementation_id: str
    approved_for_formal_run: bool
    definition_ref: str
    config_schema_ref: str | None = None
    checksum: str | None = None
    implementation_version: str = "git:unknown"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ref(self) -> PolicyRef:
        return PolicyRef(policy_id=self.policy_id, version=self.version)
