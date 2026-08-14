"""Typed outcomes for the Backend detection-event sync client (IP-08 T-105, FS-06 §7.3/§4.2).

Two disjoint shapes, mirroring :mod:`weapon_detection_agent.validation.models`'s
Valid/Indeterminate split: :class:`SyncBatchResult` is a **response the Backend actually
answered** — per-event outcomes for exactly the ``EventId``\\ s it named — while
:class:`SyncBatchFailure` means "nothing in this batch was acknowledged; treat every event as
still pending" (network/timeout/5xx/401/malformed-response, FS-06 §9). The worker never has to
guess which shape it received; it is the return type itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID


class SyncFailureReason(str, Enum):
    """Why a sync batch produced no per-event outcomes at all (FS-06 §9).

    A ``str`` enum so ``.value`` is the exact, safe diagnostic string logged — never raw response
    content, only the category of failure.
    """

    TIMEOUT = "timeout"
    TRANSPORT_FAILURE = "transport_failure"
    SERVER_FAILURE = "server_failure"
    UNAUTHORIZED = "unauthorized"
    INVALID_RESPONSE = "invalid_response"
    UNEXPECTED_STATUS = "unexpected_status"


@dataclass(frozen=True)
class SyncEventQuotaInfo:
    """The safe quota context the Backend returns alongside a ``quota_exceeded`` outcome (FS-09 §5).

    Diagnostic/logging only — the Agent's own retry/terminal-state decision never depends on these
    values, only on the outcome name itself. No internal database identifier or count is ever
    carried here, mirroring the Backend's own wire contract.
    """

    maximum: int
    local_date: str


@dataclass(frozen=True)
class SyncBatchResult:
    """The Backend's per-event verdicts for a submitted batch (FS-06 §4.2; FS-09 §5 adds
    ``quota_exceeded``).

    ``accepted``/``duplicate`` are the ``EventId``\\ s the worker may mark ``'delivered'``.
    ``rejected`` maps an ``EventId`` to its Backend-supplied ``errorCode`` — that row stays
    ``'pending'`` forever unless the rejection reason resolves (FS-06 §11, no dead-letter queue in
    this increment). ``quota_exceeded`` maps an ``EventId`` to the Backend's quota context (FS-09
    §5) — the worker marks these ``'suppressed_by_quota'`` (a successful, terminal policy outcome,
    never ``'delivered'`` and never retried, FS-09 §6). Any ``EventId`` sent but **not** present in
    any of the four collections (missing, malformed, or duplicated in the response) is implicitly
    left pending too — this type carries no explicit "unknown" bucket because the worker only ever
    acts on what it recognizes.
    """

    accepted: frozenset[UUID]
    duplicate: frozenset[UUID]
    rejected: Mapping[UUID, str]
    quota_exceeded: Mapping[UUID, SyncEventQuotaInfo] = field(default_factory=dict)

    # The Backend's per-event ``alertId`` (IP-10 T-143, FS-08 §11 — additive, this feature's only
    # change to this dataclass). Present only for entries the Backend's response actually named an
    # ``alertId`` for (an accepted or duplicate item; a response omitting it, e.g. a not-yet-updated
    # Backend, simply yields an empty mapping — never a hard failure). Consumed only by the optional
    # snapshot-association side effect; ``DetectionEventSyncWorker``'s own delivered-marking logic
    # never reads this field.
    alert_ids: Mapping[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncBatchFailure:
    """The whole batch produced no trustworthy per-event outcome (FS-06 §9).

    Every event in the attempted batch stays ``'pending'`` — the worker never calls
    ``mark_delivered_many`` for any event in a batch that produced this result. ``status_code`` is
    the raw HTTP status when one was received (``None`` for a timeout/transport failure that never
    got a response).
    """

    reason: SyncFailureReason
    status_code: int | None = None
