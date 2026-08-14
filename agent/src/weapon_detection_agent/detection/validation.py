"""Pure validation of a raw DeepStream Bridge detection payload (IP-07 T-83, FS-05 §5).

:func:`validate_detection` is the single place a raw wire payload (already framed and
schema-version-checked by the transport layer, T-86) is turned into a trustworthy
:class:`~weapon_detection_agent.detection.models.DetectionEvent` or a safe, typed
:class:`DetectionRejection`. It performs no I/O, imports no ``pyds``/socket/database code, and never
raises on malformed input — every failure path is a returned :class:`DetectionRejection`, never an
exception, so a caller (T-86's ingest handler) can log and move on to the next message without a
``try``/``except`` around business logic.

Per FS-05 §5, identity/attribution fields the Bridge never sends — ``event_id``, ``device_id``,
``camera_id``, ``detected_at_utc`` — are supplied by the caller (Agent-side), not read from
``payload``. ``class_name`` is resolved here from the caller-supplied label map (the active
profile's ``labels.txt``), never trusted verbatim from the wire, so an unconfigured/unresolvable
class is rejected rather than silently accepted under an attacker-chosen name.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from weapon_detection_agent.detection.models import DetectionEvent

# The raw wire payload's required fields (FS-05 §5) — everything the Bridge is responsible for
# sending. Identity/attribution fields are deliberately absent: they are never read from `payload`
# even if present (a malicious/buggy Bridge payload cannot forge them).
#
# `frame_width`/`frame_height` moved to _OPTIONAL_INT_FIELDS (IP-10 T-139, FS-08 §4.3): the Bridge's
# v2 wire message (`build_detection_message`) deliberately omits them — FS-08 §4.3's own example
# payload has no frame dimensions at all, confirmed against the real Bridge's wire-message builder.
# A v1 payload (and any v2 payload that does carry them) still validates them exactly as before when
# present; when absent, :func:`validate_detection` derives the smallest frame that contains the
# reported bounding box (see the derivation below) so `DetectionEvent`'s own "bbox never exceeds
# frame" invariant still holds by construction, and logs this as a documented deviation.
_REQUIRED_INT_FIELDS = ("class_id", "source_id", "frame_number")
_OPTIONAL_INT_FIELDS = ("frame_width", "frame_height")
_REQUIRED_FLOAT_FIELDS = ("confidence", "bbox_left", "bbox_top", "bbox_width", "bbox_height")


class DetectionRejectionReason(str, Enum):
    """Why a raw payload was not turned into a :class:`DetectionEvent` (FS-05 §5/§8).

    A ``str`` enum so its ``value`` is the exact, safe diagnostic string logged at
    ``detection_event_rejected`` (FS-05 §8) — never raw payload content, only the rule that was
    broken.
    """

    MALFORMED_FIELD = "malformed_field"
    NON_FINITE_VALUE = "non_finite_value"
    NEGATIVE_VALUE = "negative_value"
    UNKNOWN_CLASS = "unknown_class"
    CONFIDENCE_OUT_OF_RANGE = "confidence_out_of_range"
    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    # FS-11 §9: a Bridge-reported source_id that does not resolve to a Camera in the currently
    # applied configuration generation (server-driven Camera configuration mode only).
    UNRESOLVED_CAMERA_SOURCE = "unresolved_camera_source"


@dataclass(frozen=True)
class DetectionRejection:
    """A safe, structured rejection outcome — never carries raw payload content.

    ``field`` names the offending field when known (e.g. ``"confidence"``); it is a field *name*,
    never a field *value*, so it cannot leak payload data through a log line.
    """

    reason: DetectionRejectionReason
    field: str | None = None


def normalize_v2_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Translate a v2 wire message (FS-08 §4.3: camelCase, nested ``boundingBox``) into the flat
    snake_case shape :func:`validate_detection` expects (the same shape a v1 message already uses).

    This is the one place schema-version-specific wire-shape translation belongs — every rule below
    it in :func:`validate_detection` runs identically regardless of which schema version produced
    the now-normalized fields. A missing/malformed ``boundingBox`` (not a ``dict``, or missing a
    sub-field) degrades gracefully: the corresponding flat key is simply left absent, which
    :func:`validate_detection` already rejects as ``malformed_field`` — the same outcome a
    genuinely-missing v1 field produces, no special-cased error path needed here.
    """
    bbox = payload.get("boundingBox")
    bbox = bbox if isinstance(bbox, dict) else {}

    normalized: dict[str, Any] = dict(payload)
    for camel, snake in (
        ("classId", "class_id"),
        ("sourceId", "source_id"),
        ("frameNumber", "frame_number"),
    ):
        if camel in payload:
            normalized[snake] = payload[camel]
    for snake, value in (
        ("bbox_left", bbox.get("left")),
        ("bbox_top", bbox.get("top")),
        ("bbox_width", bbox.get("width")),
        ("bbox_height", bbox.get("height")),
    ):
        if value is not None:
            normalized[snake] = value
    return normalized


def validate_detection(
    payload: Mapping[str, Any],
    *,
    device_id: str,
    camera_id: str,
    class_names: Mapping[int, str],
    min_confidence: float,
    now: datetime,
    event_id_factory: Callable[[], UUID] = uuid4,
) -> DetectionEvent | DetectionRejection:
    """Validate, resolve, and clamp a raw Bridge payload into a :class:`DetectionEvent`.

    ``class_names`` maps the deployed profile's class ids to names (from ``labels.txt``) —
    resolution happens here, never on the wire (FS-05 §5). ``now`` is the Agent's own clock
    reading, used as ``detected_at_utc``; it must be timezone-aware. ``event_id_factory`` is an
    injectable UUID source (default :func:`uuid.uuid4`) for deterministic tests, mirroring the DI
    seams used elsewhere in this codebase (e.g.
    :class:`~weapon_detection_agent.deepstream.process_manager.DeepStreamProcessManager`'s
    subprocess factory).

    Returns a :class:`DetectionEvent` when every rule passes (bounding-box overflow is clamped to
    the frame boundary, not rejected — FS-05 §5), otherwise a :class:`DetectionRejection` naming
    the first rule broken. Never raises for malformed ``payload`` content.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    ints: dict[str, int] = {}
    for field in _REQUIRED_INT_FIELDS:
        value = payload.get(field)
        # bool is an int subclass; excluded so a stray True/False is not mistaken for 0/1.
        if isinstance(value, bool) or not isinstance(value, int):
            return DetectionRejection(DetectionRejectionReason.MALFORMED_FIELD, field=field)
        ints[field] = value

    optional_ints: dict[str, int | None] = {}
    for field in _OPTIONAL_INT_FIELDS:
        if field not in payload:
            optional_ints[field] = None
            continue
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int):
            return DetectionRejection(DetectionRejectionReason.MALFORMED_FIELD, field=field)
        optional_ints[field] = value

    floats: dict[str, float] = {}
    for field in _REQUIRED_FLOAT_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return DetectionRejection(DetectionRejectionReason.MALFORMED_FIELD, field=field)
        floats[field] = float(value)
        if not math.isfinite(floats[field]):
            return DetectionRejection(DetectionRejectionReason.NON_FINITE_VALUE, field=field)

    class_id = ints["class_id"]
    source_id = ints["source_id"]
    frame_number = ints["frame_number"]
    frame_width = optional_ints["frame_width"]
    frame_height = optional_ints["frame_height"]

    if source_id < 0:
        return DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field="source_id")
    if frame_number < 0:
        return DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field="frame_number")
    if frame_width is not None and frame_width <= 0:
        return DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field="frame_width")
    if frame_height is not None and frame_height <= 0:
        return DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field="frame_height")

    confidence = floats["confidence"]
    if not 0.0 <= confidence <= 1.0:
        return DetectionRejection(
            DetectionRejectionReason.CONFIDENCE_OUT_OF_RANGE, field="confidence"
        )
    if confidence < min_confidence:
        return DetectionRejection(
            DetectionRejectionReason.CONFIDENCE_BELOW_THRESHOLD, field="confidence"
        )

    bbox_left = floats["bbox_left"]
    bbox_top = floats["bbox_top"]
    bbox_width = floats["bbox_width"]
    bbox_height = floats["bbox_height"]
    for field, value in (
        ("bbox_left", bbox_left),
        ("bbox_top", bbox_top),
        ("bbox_width", bbox_width),
        ("bbox_height", bbox_height),
    ):
        if value < 0:
            return DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field=field)

    class_name = class_names.get(class_id)
    if class_name is None:
        return DetectionRejection(DetectionRejectionReason.UNKNOWN_CLASS, field="class_id")

    # IP-10 T-139, FS-08 §4.3: a v2 message omits frame_width/frame_height entirely (confirmed
    # against the real Bridge's wire-message builder). When absent, derive the smallest frame that
    # contains the reported bounding box — this is a deliberate, documented deviation from the
    # native camera resolution (which a v2 message alone cannot convey), chosen so
    # `DetectionEvent`'s
    # existing "bounding box never exceeds the frame" invariant still holds by construction without
    # a DetectionEvent/SQLite schema change this feature does not otherwise need.
    if frame_width is None:
        frame_width = max(1, math.ceil(bbox_left + bbox_width))
    if frame_height is None:
        frame_height = max(1, math.ceil(bbox_top + bbox_height))

    # Clamp bounding-box overflow to the frame boundary (FS-05 §5) — an edge-of-frame detection is
    # still a real detection, unlike a negative coordinate (rejected above).
    bbox_left = min(bbox_left, float(frame_width))
    bbox_top = min(bbox_top, float(frame_height))
    bbox_width = min(bbox_width, frame_width - bbox_left)
    bbox_height = min(bbox_height, frame_height - bbox_top)

    return DetectionEvent(
        event_id=event_id_factory(),
        device_id=device_id,
        camera_id=camera_id,
        source_id=source_id,
        class_id=class_id,
        class_name=class_name,
        confidence=confidence,
        frame_number=frame_number,
        detected_at_utc=now,
        frame_width=frame_width,
        frame_height=frame_height,
        bbox_left=bbox_left,
        bbox_top=bbox_top,
        bbox_width=bbox_width,
        bbox_height=bbox_height,
    )
