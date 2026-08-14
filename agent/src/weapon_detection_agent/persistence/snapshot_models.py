"""The ``SnapshotOutbox`` domain model (IP-10 T-141/T-142, FS-08 §6).

Mirrors :mod:`weapon_detection_agent.detection.models`'s ``DetectionEvent`` conventions: a frozen,
snake_case dataclass, timezone-aware UTC ``datetime`` fields, constructor-time invariant
enforcement.
``local_path`` is a filesystem path only — the JPEG bytes themselves are never held here or written
to SQLite (ADR-004/ADR-011).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from uuid import UUID


class CaptureStatus(str, Enum):
    """The two ``SnapshotOutbox.CaptureStatus`` values the schema CHECK permits (FS-08 §6)."""

    CAPTURED = "captured"
    CAPTURE_FAILED = "capture_failed"


class UploadStatus(str, Enum):
    """The ``SnapshotOutbox.UploadStatus`` values the schema CHECK permits (FS-08 §6; FS-09 §10).

    ``permanent_failure`` is deliberately not a member (FS-08 §6) — a stuck row stays ``PENDING``.
    ``SUPPRESSED_BY_QUOTA`` (FS-09 §10) is a terminal state set for a row whose ``DetectionEvent``
    was suppressed by the Branch daily Alert quota — it is never selected for upload again.
    """

    PENDING = "pending"
    UPLOADED = "uploaded"
    SUPPRESSED_BY_QUOTA = "suppressed_by_quota"


@dataclass(frozen=True)
class SnapshotOutboxRecord:
    """One ``SnapshotOutbox`` row — a captured snapshot's local metadata and upload lifecycle."""

    event_id: UUID
    local_path: Path
    capture_status: CaptureStatus
    upload_status: UploadStatus
    backend_alert_id: str | None
    content_type: str
    size_bytes: int
    sha256: str
    captured_at_utc: datetime
    uploaded_at_utc: datetime | None = None
    attempt_count: int = 0
    last_attempt_at_utc: datetime | None = None
    last_error_category: str | None = None

    def __post_init__(self) -> None:
        if self.captured_at_utc.tzinfo is None:
            raise ValueError("captured_at_utc must be timezone-aware")
        if self.uploaded_at_utc is not None and self.uploaded_at_utc.tzinfo is None:
            raise ValueError("uploaded_at_utc must be timezone-aware")
        if self.last_attempt_at_utc is not None and self.last_attempt_at_utc.tzinfo is None:
            raise ValueError("last_attempt_at_utc must be timezone-aware")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
