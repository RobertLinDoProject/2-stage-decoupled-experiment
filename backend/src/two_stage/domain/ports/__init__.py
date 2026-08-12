from __future__ import annotations

from typing import BinaryIO, Protocol

from two_stage.domain.entities.artifact import (
    ArtifactPayload,
    ArtifactRef,
    ArtifactVerification,
    StagedArtifactRef,
)
from two_stage.domain.entities.policy import PolicyDefinition, PolicyRef
from two_stage.domain.enums import RunPurpose


class PolicyRegistryPort(Protocol):
    def get(self, ref: PolicyRef) -> PolicyDefinition:
        ...

    def assert_allowed(
        self,
        ref: PolicyRef,
        run_purpose: RunPurpose,
        *,
        exploratory_draft_override: bool = False,
    ) -> None:
        ...


class ArtifactStorePort(Protocol):
    def stage(self, payload: ArtifactPayload) -> StagedArtifactRef:
        ...

    def verify(self, ref: StagedArtifactRef) -> ArtifactVerification:
        ...

    def publish(self, ref: StagedArtifactRef) -> ArtifactRef:
        ...

    def abandon(self, ref: StagedArtifactRef, reason: str) -> None:
        ...

    def get(self, ref: ArtifactRef) -> BinaryIO:
        ...

    def exists(self, ref: ArtifactRef) -> bool:
        ...

    def checksum(self, ref: ArtifactRef) -> str:
        ...
