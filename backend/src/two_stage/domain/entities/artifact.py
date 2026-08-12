from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class ArtifactPayload:
    relative_path: str
    content: bytes
    media_type: str
    artifact_type: str
    schema_name: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class StagedArtifactRef:
    staged_id: str
    relative_path: str
    absolute_path: str
    uri: str


@dataclass(frozen=True, slots=True)
class ArtifactVerification:
    staged_id: str
    checksum: str
    byte_size: int
    verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    uri: str
    absolute_path: str
    checksum: str
    byte_size: int
    published_at: datetime = field(default_factory=lambda: datetime.now(UTC))
