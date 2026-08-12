from __future__ import annotations

from deepstream_bridge.probe import (
    RawDetection,
    extract_detections,
    frame_number_from_buffer,
    handle_buffer,
)
from fakes import FakeBatchMeta, FakeFrameMeta, FakeObjectMeta, FakePydsModule, FakeRectParams

_AGENT_OWNED_FIELDS = {
    "event_id",
    "device_id",
    "camera_id",
    "class_name",
    "detected_at_utc",
    "created_at_utc",
    "delivery_status",
}


def _sequential_message_ids():
    counter = {"n": 0}

    def factory() -> str:
        counter["n"] += 1
        return f"msg-{counter['n']}"

    return factory


def test_extract_single_object_single_frame() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=42,
        source_frame_width=640,
        source_frame_height=640,
        objects=[
            FakeObjectMeta(
                class_id=0, confidence=0.91, rect_params=FakeRectParams(10.0, 20.0, 30.0, 40.0)
            )
        ],
    )
    batch = FakeBatchMeta(frames=[frame])

    detections = extract_detections(FakePydsModule(), batch, message_id_factory=_sequential_message_ids())

    assert len(detections) == 1
    detection = detections[0]
    assert detection.schema_version == 2
    assert detection.message_id == "msg-1"
    assert detection.class_id == 0
    assert detection.confidence == 0.91
    assert detection.source_id == 0
    assert detection.frame_number == 42
    assert detection.frame_width == 640
    assert detection.frame_height == 640
    assert (
        detection.bbox_left,
        detection.bbox_top,
        detection.bbox_width,
        detection.bbox_height,
    ) == (
        10.0,
        20.0,
        30.0,
        40.0,
    )


def test_extract_multiple_objects_in_one_frame() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=1,
        source_frame_width=640,
        source_frame_height=640,
        objects=[
            FakeObjectMeta(class_id=0, confidence=0.6, rect_params=FakeRectParams(0, 0, 10, 10)),
            FakeObjectMeta(class_id=1, confidence=0.7, rect_params=FakeRectParams(1, 1, 11, 11)),
        ],
    )
    batch = FakeBatchMeta(frames=[frame])

    detections = extract_detections(FakePydsModule(), batch)

    assert len(detections) == 2
    assert {d.class_id for d in detections} == {0, 1}


def test_extract_generates_a_distinct_message_id_per_detection() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=1,
        source_frame_width=640,
        source_frame_height=640,
        objects=[
            FakeObjectMeta(class_id=0, confidence=0.6, rect_params=FakeRectParams(0, 0, 10, 10)),
            FakeObjectMeta(class_id=1, confidence=0.7, rect_params=FakeRectParams(1, 1, 11, 11)),
        ],
    )
    batch = FakeBatchMeta(frames=[frame])

    detections = extract_detections(FakePydsModule(), batch)

    message_ids = [d.message_id for d in detections]
    assert len(set(message_ids)) == 2


def test_extract_multiple_frames_in_batch() -> None:
    frame1 = FakeFrameMeta(
        source_id=0,
        frame_num=1,
        source_frame_width=640,
        source_frame_height=640,
        objects=[FakeObjectMeta(0, 0.5, FakeRectParams(0, 0, 1, 1))],
    )
    frame2 = FakeFrameMeta(
        source_id=0,
        frame_num=2,
        source_frame_width=640,
        source_frame_height=640,
        objects=[FakeObjectMeta(1, 0.5, FakeRectParams(0, 0, 1, 1))],
    )
    batch = FakeBatchMeta(frames=[frame1, frame2])

    detections = extract_detections(FakePydsModule(), batch)

    assert [d.frame_number for d in detections] == [1, 2]


def test_extract_frame_with_no_objects_yields_nothing() -> None:
    frame = FakeFrameMeta(
        source_id=0, frame_num=1, source_frame_width=640, source_frame_height=640, objects=[]
    )
    batch = FakeBatchMeta(frames=[frame])

    assert extract_detections(FakePydsModule(), batch) == []


def test_to_payload_has_exact_wire_field_set() -> None:
    detection = RawDetection(
        schema_version=2,
        message_id="msg-1",
        class_id=0,
        confidence=0.9,
        source_id=0,
        frame_number=1,
        frame_width=640,
        frame_height=640,
        bbox_left=1.0,
        bbox_top=2.0,
        bbox_width=3.0,
        bbox_height=4.0,
    )

    payload = detection.to_payload()

    assert set(payload.keys()) == {
        "schema_version",
        "message_id",
        "class_id",
        "confidence",
        "source_id",
        "frame_number",
        "frame_width",
        "frame_height",
        "bbox_left",
        "bbox_top",
        "bbox_width",
        "bbox_height",
    }


def test_to_payload_never_contains_agent_owned_fields() -> None:
    detection = RawDetection(
        schema_version=2,
        message_id="msg-1",
        class_id=0,
        confidence=0.9,
        source_id=0,
        frame_number=1,
        frame_width=640,
        frame_height=640,
        bbox_left=1.0,
        bbox_top=2.0,
        bbox_width=3.0,
        bbox_height=4.0,
    )

    payload = detection.to_payload()

    assert _AGENT_OWNED_FIELDS.isdisjoint(payload.keys())


def test_to_wire_message_matches_fs08_v2_shape() -> None:
    detection = RawDetection(
        schema_version=2,
        message_id="b7e2",
        class_id=0,
        confidence=0.91,
        source_id=0,
        frame_number=12345,
        frame_width=1920,
        frame_height=1080,
        bbox_left=420.0,
        bbox_top=180.0,
        bbox_width=250.0,
        bbox_height=190.0,
    )

    message = detection.to_wire_message()

    assert message == {
        "schemaVersion": 2,
        "messageId": "b7e2",
        "sourceId": 0,
        "frameNumber": 12345,
        "classId": 0,
        "confidence": 0.91,
        "boundingBox": {"left": 420.0, "top": 180.0, "width": 250.0, "height": 190.0},
    }


def test_handle_buffer_enqueues_each_detection_non_blocking() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=1,
        source_frame_width=640,
        source_frame_height=640,
        objects=[
            FakeObjectMeta(0, 0.9, FakeRectParams(0, 0, 1, 1)),
            FakeObjectMeta(1, 0.8, FakeRectParams(0, 0, 1, 1)),
        ],
    )
    batch = FakeBatchMeta(frames=[frame])
    pyds_module = FakePydsModule(batch_meta_by_buffer={hash("buf"): batch})

    enqueued: list[dict] = []
    handle_buffer(pyds_module, "buf", enqueued.append)

    assert len(enqueued) == 2
    assert _AGENT_OWNED_FIELDS.isdisjoint(enqueued[0].keys())
    assert "messageId" in enqueued[0]


def test_handle_buffer_missing_batch_meta_is_a_noop() -> None:
    pyds_module = FakePydsModule()

    enqueued: list[dict] = []
    handle_buffer(pyds_module, "buf", enqueued.append)

    assert enqueued == []


def test_handle_buffer_none_buffer_is_a_noop() -> None:
    enqueued: list[dict] = []

    handle_buffer(FakePydsModule(), None, enqueued.append)

    assert enqueued == []


def test_handle_buffer_invokes_on_candidate_once_per_detection() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=7,
        source_frame_width=640,
        source_frame_height=640,
        objects=[
            FakeObjectMeta(0, 0.9, FakeRectParams(0, 0, 1, 1)),
            FakeObjectMeta(1, 0.8, FakeRectParams(0, 0, 1, 1)),
        ],
    )
    batch = FakeBatchMeta(frames=[frame])
    pyds_module = FakePydsModule(batch_meta_by_buffer={hash("buf"): batch})

    candidates: list[tuple] = []
    handle_buffer(
        pyds_module,
        "buf",
        lambda _payload: None,
        lambda message_id, frame_number: candidates.append((message_id, frame_number)),
    )

    assert len(candidates) == 2
    assert all(frame_number == 7 for _message_id, frame_number in candidates)
    assert len({message_id for message_id, _frame_number in candidates}) == 2


def test_handle_buffer_none_on_candidate_is_safe() -> None:
    frame = FakeFrameMeta(
        source_id=0,
        frame_num=1,
        source_frame_width=640,
        source_frame_height=640,
        objects=[FakeObjectMeta(0, 0.9, FakeRectParams(0, 0, 1, 1))],
    )
    batch = FakeBatchMeta(frames=[frame])
    pyds_module = FakePydsModule(batch_meta_by_buffer={hash("buf"): batch})

    handle_buffer(pyds_module, "buf", lambda _payload: None, None)  # must not raise


def test_frame_number_from_buffer_reads_first_frame() -> None:
    frame = FakeFrameMeta(
        source_id=0, frame_num=99, source_frame_width=640, source_frame_height=640, objects=[]
    )
    batch = FakeBatchMeta(frames=[frame])
    pyds_module = FakePydsModule(batch_meta_by_buffer={hash("buf"): batch})

    assert frame_number_from_buffer(pyds_module, "buf") == 99


def test_frame_number_from_buffer_missing_batch_meta_returns_none() -> None:
    assert frame_number_from_buffer(FakePydsModule(), "buf") is None


def test_frame_number_from_buffer_none_buffer_returns_none() -> None:
    assert frame_number_from_buffer(FakePydsModule(), None) is None


def test_frame_number_from_buffer_empty_frame_list_returns_none() -> None:
    batch = FakeBatchMeta(frames=[])
    pyds_module = FakePydsModule(batch_meta_by_buffer={hash("buf"): batch})

    assert frame_number_from_buffer(pyds_module, "buf") is None
