from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from two_stage.domain.entities.artifact import (
    ArtifactPayload,
    ArtifactRef,
    ArtifactVerification,
    StagedArtifactRef,
)
from two_stage.domain.errors import ArtifactNotFoundError, ArtifactPublicationError


class LocalArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.tmp_root = self.root / "tmp"
        self.published_root = self.root / "published"
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self.published_root.mkdir(parents=True, exist_ok=True)

    def stage(self, payload: ArtifactPayload) -> StagedArtifactRef:
        staged_id = f"STAGED-{uuid4().hex[:16]}"
        relative_path = f"{staged_id}/{payload.relative_path}"
        target = self._safe_join(self.tmp_root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload.content)
        return StagedArtifactRef(
            staged_id=staged_id,
            relative_path=relative_path,
            absolute_path=str(target),
            uri=f"artifact-tmp://{relative_path}",
        )

    def stage_file(self, *, relative_path: str, source_path: str | Path) -> StagedArtifactRef:
        staged_id = f"STAGED-{uuid4().hex[:16]}"
        staged_relative_path = f"{staged_id}/{relative_path}"
        source = Path(source_path)
        if not source.exists():
            raise ArtifactNotFoundError(f"Source artifact file not found: {source}")
        target = self._safe_join(self.tmp_root, staged_relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as source_handle, target.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
        return StagedArtifactRef(
            staged_id=staged_id,
            relative_path=staged_relative_path,
            absolute_path=str(target),
            uri=f"artifact-tmp://{staged_relative_path}",
        )

    def verify(self, ref: StagedArtifactRef) -> ArtifactVerification:
        path = Path(ref.absolute_path)
        if not path.exists():
            raise ArtifactNotFoundError(f"Staged artifact not found: {ref.uri}")
        checksum = self._checksum_path(path)
        return ArtifactVerification(
            staged_id=ref.staged_id,
            checksum=checksum,
            byte_size=path.stat().st_size,
        )

    def publish(self, ref: StagedArtifactRef) -> ArtifactRef:
        staged_path = Path(ref.absolute_path)
        if not staged_path.exists():
            raise ArtifactNotFoundError(f"Staged artifact not found: {ref.uri}")

        verification = self.verify(ref)
        published_relative = ref.relative_path.split("/", 1)[1]
        target = self._safe_join(self.published_root, published_relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        artifact_id = self._artifact_id(published_relative, verification.checksum)
        if target.exists():
            existing_checksum = self._checksum_path(target)
            if existing_checksum != verification.checksum:
                raise ArtifactPublicationError(
                    f"Published artifact already exists with different content: {target}"
                )
            staged_path.unlink()
            return ArtifactRef(
                artifact_id=artifact_id,
                uri=f"artifact://{published_relative}",
                absolute_path=str(target),
                checksum=existing_checksum,
                byte_size=target.stat().st_size,
            )

        staged_path.replace(target)
        return ArtifactRef(
            artifact_id=artifact_id,
            uri=f"artifact://{published_relative}",
            absolute_path=str(target),
            checksum=verification.checksum,
            byte_size=verification.byte_size,
        )

    def abandon(self, ref: StagedArtifactRef, reason: str) -> None:
        staged_path = Path(ref.absolute_path)
        metadata_path = staged_path.with_suffix(staged_path.suffix + ".abandoned.json")
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps({"staged_id": ref.staged_id, "reason": reason}, indent=2),
            encoding="utf-8",
        )

    def exists(self, ref: ArtifactRef) -> bool:
        return Path(ref.absolute_path).exists()

    def checksum(self, ref: ArtifactRef) -> str:
        path = Path(ref.absolute_path)
        if not path.exists():
            raise ArtifactNotFoundError(f"Published artifact not found: {ref.uri}")
        return self._checksum_path(path)

    def _safe_join(self, root: Path, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise ArtifactPublicationError("Artifact relative path must not be absolute.")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError as exc:
            raise ArtifactPublicationError(f"Path traversal rejected: {relative_path}") from exc
        return target

    @staticmethod
    def _checksum_path(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _artifact_id(relative_path: str, checksum: str) -> str:
        digest = hashlib.sha256(f"{relative_path}|{checksum}".encode()).hexdigest()
        return f"ART-{digest[:16]}"
