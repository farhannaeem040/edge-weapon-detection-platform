"""Typed outcomes for the Backend snapshot upload client (IP-10 T-144, FS-08 §9/§11).

Mirrors :mod:`weapon_detection_agent.sync.models`'s Result/Failure split exactly:
:class:`SnapshotUploadResult` is a response the Backend actually answered (accepted or duplicate —
both mean "the worker may mark this row uploaded and delete the local file"); :class:`
SnapshotUploadFailure` means "nothing was durably stored Backend-side for this attempt; the row
stays pending" — network/timeout/5xx/401/malformed-response/409-conflict/local-file-problem.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SnapshotUploadOutcome(str, Enum):
    """The Backend's per-request verdict (FS-08 §9) — both mean "safe to mark uploaded"."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class SnapshotUploadFailureReason(str, Enum):
    """Why an upload attempt produced no durable Backend-side result (FS-08 §9/§11).

    A ``str`` enum so ``.value`` is the exact, safe diagnostic string recorded as
    ``SnapshotOutbox.LastErrorCategory`` and logged — never raw response content.
    """

    TIMEOUT = "timeout"
    TRANSPORT_FAILURE = "transport_failure"
    SERVER_FAILURE = "server_failure"
    UNAUTHORIZED = "unauthorized"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED_STATUS = "unexpected_status"
    # A named integrity issue (FS-08 §9): the Backend already holds a different snapshot for this
    # Alert. Never retried in a tight loop — the worker's ordinary backoff paces the next attempt
    # exactly like any other failure, and the row is never silently marked uploaded.
    CONFLICT = "conflict"
    # The local JPEG referenced by the row no longer exists at upload time (FS-08 §11's "missing
    # local file ... does not block later rows"). Recorded so a later row is never starved by one
    # bad row's repeated failure.
    LOCAL_FILE_MISSING = "local_file_missing"
    # The local file's current size/SHA-256 no longer matches the row's recorded metadata (FS-08
    # §11's "verify local file still exists + size/SHA-256 match" step) — the file was modified or
    # truncated after capture; never uploaded as-is.
    LOCAL_FILE_MODIFIED = "local_file_modified"


@dataclass(frozen=True)
class SnapshotUploadResult:
    """The Backend accepted or already held this exact snapshot (FS-08 §9)."""

    outcome: SnapshotUploadOutcome


@dataclass(frozen=True)
class SnapshotUploadFailure:
    """The upload attempt produced no durable Backend-side result (FS-08 §9/§11).

    ``status_code`` is the raw HTTP status when one was received (``None`` for a timeout/transport
    failure or a local-file problem detected before any request was sent).
    """

    reason: SnapshotUploadFailureReason
    status_code: int | None = None
