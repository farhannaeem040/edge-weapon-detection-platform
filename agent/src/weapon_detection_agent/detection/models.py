"""The detection event domain model (IP-07 T-83, FS-05 §5).

``DetectionEvent`` mirrors one accepted, validated detection — the Agent-side result of
:mod:`weapon_detection_agent.detection.validation` turning a raw wire payload from the DeepStream
Bridge into a trustworthy record. It follows the same conventions as
:mod:`weapon_detection_agent.persistence.models`: a frozen, snake_case dataclass, a timezone-aware
UTC ``datetime`` for the detection timestamp, and constructor-time invariant enforcement so an
inconsistent instance can never exist, regardless of which code path constructs one.

Identity and attribution fields (``event_id``, ``device_id``, ``camera_id``, ``detected_at_utc``)
are always Agent-generated — never trusted from the wire (FS-05 §5) — so this model carries no
field the Bridge is allowed to forge. The Bridge publishes only raw inference facts (``class_id``,
``confidence``, ``source_id``, frame number/dimensions, bounding box, schema version); everything
else here is the Agent's own addition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class DetectionEvent:
    """One validated, accepted weapon detection, ready for deduplication and persistence.

    ``confidence`` is a probability in ``[0.0, 1.0]``; ``frame_width``/``frame_height`` and the
    ``bbox_*`` pixel coordinates are non-negative and finite, with the bounding box clamped to the
    frame boundary by the validator before construction (FS-05 §5) — this constructor re-asserts
    the invariants defensively (mirrors
    :class:`~weapon_detection_agent.persistence.models.DeviceIdentity`) so a caller that bypasses
    the validator still cannot build an inconsistent instance.
    """

    event_id: UUID
    device_id: str
    camera_id: str
    source_id: int
    class_id: int
    class_name: str
    confidence: float
    frame_number: int
    detected_at_utc: datetime
    frame_width: int
    frame_height: int
    bbox_left: float
    bbox_top: float
    bbox_width: float
    bbox_height: float

    # Populated only when this instance is read back from storage (IP-08 T-104,
    # `DetectionEventRepository.list_pending`) — the moment `DetectionEventRepository.insert`
    # actually assigns `CreatedAtUtc`, from its own injected clock, not from the event object. Never
    # set by `validate_detection` (FS-05 §5) or read by `insert`; `None` for every not-yet-persisted
    # event. `BackendSyncClient` (FS-06 §7.3) requires it be present on any event it is asked to
    # send — a `None` there is a caller bug, not a wire-trust concern.
    created_at_utc: datetime | None = None

    def __post_init__(self) -> None:
        if self.detected_at_utc.tzinfo is None:
            raise ValueError("detected_at_utc must be timezone-aware")
        if self.created_at_utc is not None and self.created_at_utc.tzinfo is None:
            raise ValueError("created_at_utc must be timezone-aware")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0.0, 1.0]")
        if self.source_id < 0:
            raise ValueError("source_id must be non-negative")
        if self.frame_number < 0:
            raise ValueError("frame_number must be non-negative")
        if self.frame_width <= 0 or self.frame_height <= 0:
            raise ValueError("frame_width and frame_height must be positive")
        for name, value in (
            ("bbox_left", self.bbox_left),
            ("bbox_top", self.bbox_top),
            ("bbox_width", self.bbox_width),
            ("bbox_height", self.bbox_height),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.bbox_left + self.bbox_width > self.frame_width:
            raise ValueError("bounding box must not exceed the frame width")
        if self.bbox_top + self.bbox_height > self.frame_height:
            raise ValueError("bounding box must not exceed the frame height")
