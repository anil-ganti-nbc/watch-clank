"""Atomic, content-addressed snapshot storage service."""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SnapshotStorageError(Exception):
    """Base error for snapshot storage operations."""


class PayloadTooLargeError(SnapshotStorageError):
    """Raised when payload exceeds configured maximum size."""


class SnapshotNotFoundError(SnapshotStorageError):
    """Raised when a snapshot file is missing."""


class SnapshotCorruptedError(SnapshotStorageError):
    """Raised when a snapshot file cannot be read or is corrupted."""


class SnapshotStorageService:
    """Content-addressed, atomic snapshot storage.

    - SHA-256 content hashing
    - Atomic writes via temp file + rename
    - Deduplication by content hash
    - Optional gzip compression for text/JSON
    - Paths independent of process CWD
    """

    SCHEMA_VERSION = "1"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.root = self.settings.resolved_snapshot_root
        self.max_bytes = self.settings.snapshot_max_payload_bytes
        self.compression = self.settings.snapshot_compression

    def _hash_payload(self, payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def _subdir_for_hash(self, content_hash: str) -> Path:
        # Two-level sharding: ab/cd/<hash>
        return self.root / content_hash[:2] / content_hash[2:4]

    def _filepath_for_hash(
        self, content_hash: str, compression: str | None
    ) -> Path:
        subdir = self._subdir_for_hash(content_hash)
        suffix = ".gz" if compression == "gzip" else ".bin"
        return subdir / f"{content_hash}{suffix}"

    def store(
        self,
        payload: bytes,
        *,
        source_url: str,
        content_type: str | None = None,
        collector_id: str,
        collector_version: str,
        encoding: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store payload atomically with deduplication.

        Returns metadata dict suitable for creating a RawSnapshot row.
        Does not write to the database.
        """
        if len(payload) > self.max_bytes:
            raise PayloadTooLargeError(
                f"Payload size {len(payload)} exceeds maximum {self.max_bytes}"
            )

        content_hash = self._hash_payload(payload)
        use_compression = (
            self.compression == "gzip"
            and content_type
            and (
                content_type.startswith("text/")
                or "json" in content_type
                or "html" in content_type
                or "xml" in content_type
            )
        )
        compression_type = "gzip" if use_compression else None

        target = self._filepath_for_hash(content_hash, compression_type)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            logger.debug(
                "snapshot_deduplicated",
                content_hash=content_hash,
                filepath=str(target),
            )
            return {
                "content_hash": content_hash,
                "filepath": str(target.relative_to(self.root)),
                "content_type": content_type,
                "compression_type": compression_type,
                "byte_size": len(payload),
                "schema_version": self.SCHEMA_VERSION,
                "collector_id": collector_id,
                "collector_version": collector_version,
                "encoding": encoding,
                "extra_metadata": extra_metadata or {},
                "source_url": source_url,
                "fetched_at": datetime.now(UTC),
                "reused": True,
            }

        # Atomic write: write to temp then rename
        fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".tmp_")
        try:
            with os.fdopen(fd, "wb") as f:
                if use_compression:
                    with gzip.GzipFile(fileobj=f, mode="wb") as gz:
                        gz.write(payload)
                else:
                    f.write(payload)
            os.replace(tmp_path, target)
        except Exception:
            import contextlib
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        logger.info(
            "snapshot_stored",
            content_hash=content_hash,
            filepath=str(target),
            byte_size=len(payload),
            compressed=use_compression,
        )

        return {
            "content_hash": content_hash,
            "filepath": str(target.relative_to(self.root)),
            "content_type": content_type,
            "compression_type": compression_type,
            "byte_size": len(payload),
            "schema_version": self.SCHEMA_VERSION,
            "collector_id": collector_id,
            "collector_version": collector_version,
            "encoding": encoding,
            "extra_metadata": extra_metadata or {},
            "source_url": source_url,
            "fetched_at": datetime.now(UTC),
            "reused": False,
        }

    def read(self, relative_filepath: str, compression_type: str | None = None) -> bytes:
        """Read a snapshot by relative filepath. Handles missing/corrupt files gracefully."""
        path = self.root / relative_filepath
        if not path.exists():
            raise SnapshotNotFoundError(f"Snapshot file not found: {relative_filepath}")

        try:
            if compression_type == "gzip" or relative_filepath.endswith(".gz"):
                with gzip.open(path, "rb") as f:
                    return f.read()
            else:
                return path.read_bytes()
        except (OSError, gzip.BadGzipFile, EOFError) as exc:
            raise SnapshotCorruptedError(
                f"Failed to read snapshot {relative_filepath}: {exc}"
            ) from exc

    def exists(self, content_hash: str, compression: str | None = None) -> bool:
        return self._filepath_for_hash(content_hash, compression).exists()

    def resolve_path(self, relative_filepath: str) -> Path:
        return self.root / relative_filepath
