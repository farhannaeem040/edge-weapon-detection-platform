"""Unit tests for the detection event domain model (IP-07 T-83, FS-05 §5).

Exercises :class:`DetectionEvent`'s own constructor-time invariants directly, independent of
:func:`~weapon_detection_agent.detection.validation.validate_detection` (covered separately in
``test_detection_validation.py``) — proving the model cannot be constructed in an inconsistent state
even if a future caller bypasses the validator.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from weapon_detection_agent.detection.models import DetectionEvent

UTC_NOW = datetime(2026, 7, 24, 18, 30, 0, tzinfo=timezone.utc)


def _valid_event(**overrides: object) -> DetectionEvent:
    fields: dict[str, object] = {
        "event_id": uuid4(),
        "device_id": "158a7f4a-8b21-44d4-b466-383804e5e515",
        "camera_id": "camera1",
        "source_id": 0,
        "class_id": 0,
        "class_name": "gun",
        "confidence": 0.91,
        "frame_number": 12345,
        "detected_at_utc": UTC_NOW,
        "frame_width": 640,
        "frame_height": 640,
        "bbox_left": 210.0,
        "bbox_top": 130.0,
        "bbox_width": 95.0,
        "bbox_height": 70.0,
    }
    fields.update(overrides)
    return DetectionEvent(**fields)  # type: ignore[arg-type]


def test_valid_gun_event_constructs() -> None:
    event = _valid_event(class_id=0, class_name="gun")

    assert event.class_name == "gun"
    assert event.confidence == 0.91


def test_valid_knife_event_constructs() -> None:
    event = _valid_event(class_id=1, class_name="knife")

    assert event.class_name == "knife"


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _valid_event(detected_at_utc=datetime(2026, 7, 24, 18, 30, 0))


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 2.0, -1.0])
def test_out_of_range_confidence_is_rejected(confidence: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        _valid_event(confidence=confidence)


def test_negative_source_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="source_id"):
        _valid_event(source_id=-1)


def test_negative_frame_number_is_rejected() -> None:
    with pytest.raises(ValueError, match="frame_number"):
        _valid_event(frame_number=-1)


@pytest.mark.parametrize(("field", "value"), [("frame_width", 0), ("frame_height", -1)])
def test_non_positive_frame_dimensions_are_rejected(field: str, value: int) -> None:
    with pytest.raises(ValueError, match="frame_width and frame_height"):
        _valid_event(**{field: value})


@pytest.mark.parametrize("field", ["bbox_left", "bbox_top", "bbox_width", "bbox_height"])
def test_negative_bbox_components_are_rejected(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        _valid_event(**{field: -1.0})


def test_bbox_exceeding_frame_width_is_rejected() -> None:
    with pytest.raises(ValueError, match="frame width"):
        _valid_event(bbox_left=600.0, bbox_width=100.0, frame_width=640)


def test_bbox_exceeding_frame_height_is_rejected() -> None:
    with pytest.raises(ValueError, match="frame height"):
        _valid_event(bbox_top=600.0, bbox_height=100.0, frame_height=640)


def test_bbox_exactly_at_frame_boundary_is_accepted() -> None:
    event = _valid_event(bbox_left=540.0, bbox_width=100.0, frame_width=640)

    assert event.bbox_left + event.bbox_width == 640


def test_is_frozen() -> None:
    event = _valid_event()

    with pytest.raises(AttributeError):
        event.confidence = 0.5  # type: ignore[misc]

    # dataclasses.replace still works for constructing a modified copy (not in-place mutation).
    replaced = replace(event, confidence=0.5)
    assert replaced.confidence == 0.5
    assert event.confidence == 0.91
