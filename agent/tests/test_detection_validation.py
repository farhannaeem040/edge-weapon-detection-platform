"""Unit tests for the pure detection-payload validator (IP-07 T-83, FS-05 §5).

Every test is fully offline: no socket, no ``pyds``, no filesystem — ``validate_detection`` is a
pure function over a raw dict, exactly as the Bridge boundary (ADR-005) requires it to be
testable without real DeepStream hardware.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from uuid import UUID

import pytest

from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.detection.validation import (
    DetectionRejection,
    DetectionRejectionReason,
    validate_detection,
)

UTC_NOW = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)
CLASS_NAMES = {0: "gun", 1: "knife"}
FIXED_EVENT_ID = UUID("3f9c8e2a-6b1d-4c9a-9f2e-8a1b2c3d4e5f")


def _payload(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "class_id": 0,
        "confidence": 0.91,
        "source_id": 0,
        "frame_number": 12345,
        "frame_width": 640,
        "frame_height": 640,
        "bbox_left": 210.0,
        "bbox_top": 130.0,
        "bbox_width": 95.0,
        "bbox_height": 70.0,
    }
    fields.update(overrides)
    return fields


def _validate(payload: dict[str, object], **kwargs: object) -> DetectionEvent | DetectionRejection:
    defaults: dict[str, object] = {
        "device_id": "158a7f4a-8b21-44d4-b466-383804e5e515",
        "camera_id": "camera1",
        "class_names": CLASS_NAMES,
        "min_confidence": 0.5,
        "now": UTC_NOW,
        "event_id_factory": lambda: FIXED_EVENT_ID,
    }
    defaults.update(kwargs)
    return validate_detection(payload, **defaults)  # type: ignore[arg-type]


# --- Valid events -----------------------------------------------------------------------------


def test_valid_gun_event_is_accepted() -> None:
    result = _validate(_payload(class_id=0))

    assert isinstance(result, DetectionEvent)
    assert result.class_name == "gun"
    assert result.event_id == FIXED_EVENT_ID
    assert result.device_id == "158a7f4a-8b21-44d4-b466-383804e5e515"
    assert result.camera_id == "camera1"
    assert result.detected_at_utc == UTC_NOW


def test_valid_knife_event_is_accepted() -> None:
    result = _validate(_payload(class_id=1))

    assert isinstance(result, DetectionEvent)
    assert result.class_name == "knife"


# --- Class resolution -----------------------------------------------------------------------------


def test_unknown_class_is_rejected() -> None:
    result = _validate(_payload(class_id=99))

    assert result == DetectionRejection(DetectionRejectionReason.UNKNOWN_CLASS, field="class_id")


# --- Confidence -------------------------------------------------------------------------------


def test_confidence_below_threshold_is_rejected() -> None:
    result = _validate(_payload(confidence=0.4), min_confidence=0.5)

    assert result == DetectionRejection(
        DetectionRejectionReason.CONFIDENCE_BELOW_THRESHOLD, field="confidence"
    )


def test_confidence_above_one_is_rejected() -> None:
    result = _validate(_payload(confidence=1.5))

    assert result == DetectionRejection(
        DetectionRejectionReason.CONFIDENCE_OUT_OF_RANGE, field="confidence"
    )


def test_confidence_below_zero_is_rejected() -> None:
    result = _validate(_payload(confidence=-0.1))

    assert result == DetectionRejection(
        DetectionRejectionReason.CONFIDENCE_OUT_OF_RANGE, field="confidence"
    )


def test_confidence_at_exact_threshold_is_accepted() -> None:
    result = _validate(_payload(confidence=0.5), min_confidence=0.5)

    assert isinstance(result, DetectionEvent)


# --- NaN / infinity ------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_confidence_is_rejected(value: float) -> None:
    result = _validate(_payload(confidence=value))

    assert result == DetectionRejection(
        DetectionRejectionReason.NON_FINITE_VALUE, field="confidence"
    )


@pytest.mark.parametrize("field", ["bbox_left", "bbox_top", "bbox_width", "bbox_height"])
def test_non_finite_bbox_value_is_rejected(field: str) -> None:
    result = _validate(_payload(**{field: math.nan}))

    assert result == DetectionRejection(DetectionRejectionReason.NON_FINITE_VALUE, field=field)


# --- Negative values --------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["bbox_left", "bbox_top", "bbox_width", "bbox_height"])
def test_negative_bbox_value_is_rejected(field: str) -> None:
    result = _validate(_payload(**{field: -1.0}))

    assert result == DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field=field)


@pytest.mark.parametrize("field", ["source_id", "frame_number", "frame_width", "frame_height"])
def test_negative_integer_field_is_rejected(field: str) -> None:
    result = _validate(_payload(**{field: -1}))

    assert result == DetectionRejection(DetectionRejectionReason.NEGATIVE_VALUE, field=field)


def test_zero_frame_width_is_rejected() -> None:
    result = _validate(_payload(frame_width=0))

    assert result == DetectionRejection(
        DetectionRejectionReason.NEGATIVE_VALUE, field="frame_width"
    )


# --- Bounding-box clamping --------------------------------------------------------------------


def test_bbox_overflow_is_clamped_not_rejected() -> None:
    result = _validate(
        _payload(bbox_left=600.0, bbox_width=100.0, frame_width=640, bbox_top=0.0, bbox_height=50.0)
    )

    assert isinstance(result, DetectionEvent)
    assert result.bbox_left == 600.0
    assert result.bbox_width == 40.0  # clamped: 640 - 600


def test_bbox_overflow_on_height_is_clamped() -> None:
    result = _validate(
        _payload(
            bbox_top=600.0, bbox_height=100.0, frame_height=640, bbox_left=0.0, bbox_width=50.0
        )
    )

    assert isinstance(result, DetectionEvent)
    assert result.bbox_height == 40.0  # clamped: 640 - 600


# --- Malformed fields -------------------------------------------------------------------------


def test_missing_field_is_rejected_as_malformed() -> None:
    payload = _payload()
    del payload["confidence"]

    result = _validate(payload)

    assert result == DetectionRejection(
        DetectionRejectionReason.MALFORMED_FIELD, field="confidence"
    )


def test_wrong_type_field_is_rejected_as_malformed() -> None:
    result = _validate(_payload(class_id="0"))

    assert result == DetectionRejection(DetectionRejectionReason.MALFORMED_FIELD, field="class_id")


def test_boolean_is_not_mistaken_for_integer() -> None:
    result = _validate(_payload(class_id=True))

    assert result == DetectionRejection(DetectionRejectionReason.MALFORMED_FIELD, field="class_id")


# --- Timestamp / clock -------------------------------------------------------------------------


def test_naive_now_raises() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _validate(_payload(), now=datetime(2026, 7, 24, 18, 30, 0))


def test_result_uses_injected_now_not_wall_clock() -> None:
    fixed = datetime(2020, 1, 1, tzinfo=timezone.utc)

    result = _validate(_payload(), now=fixed)

    assert isinstance(result, DetectionEvent)
    assert result.detected_at_utc == fixed


# --- No I/O ------------------------------------------------------------------------------------


def test_validate_detection_is_pure_no_module_state() -> None:
    # Calling twice with identical input produces equal (not merely same-typed) results, modulo the
    # injected event_id_factory/now — proving no hidden mutable state influences the outcome.
    first = _validate(_payload())
    second = _validate(_payload())

    assert first == second
