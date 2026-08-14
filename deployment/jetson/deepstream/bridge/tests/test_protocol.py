from __future__ import annotations

import json

from deepstream_bridge.protocol import (
    FRAME_HEADER_BYTES,
    FRAME_KIND_ACKNOWLEDGEMENT,
    FRAME_KIND_DETECTION,
    FRAME_KIND_SNAPSHOT,
    FRAME_KIND_PREFIX_BYTES,
    FRAME_LENGTH_PREFIX_BYTES,
    MAX_FRAME_BYTES,
    MAX_SNAPSHOT_FRAME_BYTES,
    SCHEMA_VERSION,
    build_detection_message,
    decode_acknowledgement,
    decode_frame_header,
    decode_snapshot_message,
    encode_frame,
    encode_message,
    encode_snapshot_message,
)


def test_encode_frame_prefixes_kind_and_exact_length() -> None:
    frame = encode_frame(b"abc", kind=FRAME_KIND_DETECTION)
    assert frame[:FRAME_KIND_PREFIX_BYTES] == FRAME_KIND_DETECTION.to_bytes(
        FRAME_KIND_PREFIX_BYTES, "big"
    )
    length_start = FRAME_KIND_PREFIX_BYTES
    length_end = length_start + FRAME_LENGTH_PREFIX_BYTES
    assert frame[length_start:length_end] == (3).to_bytes(FRAME_LENGTH_PREFIX_BYTES, "big")
    assert frame[length_end:] == b"abc"


def test_decode_frame_header_round_trips_encode_frame() -> None:
    frame = encode_frame(b"hello", kind=FRAME_KIND_SNAPSHOT)
    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    assert kind == FRAME_KIND_SNAPSHOT
    assert length == 5


def test_encode_message_round_trips_json_and_uses_detection_kind() -> None:
    payload = {"schemaVersion": SCHEMA_VERSION, "classId": 0, "confidence": 0.91}
    frame = encode_message(payload)

    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    body = frame[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length]

    assert kind == FRAME_KIND_DETECTION
    assert json.loads(body) == payload


def test_multiple_messages_concatenate_and_split_correctly() -> None:
    combined = encode_message({"a": 1}) + encode_message({"b": 2})

    kind1, length1 = decode_frame_header(combined[:FRAME_HEADER_BYTES])
    body1 = combined[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length1]
    rest = combined[FRAME_HEADER_BYTES + length1 :]
    kind2, length2 = decode_frame_header(rest[:FRAME_HEADER_BYTES])
    body2 = rest[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length2]

    assert kind1 == kind2 == FRAME_KIND_DETECTION
    assert json.loads(body1) == {"a": 1}
    assert json.loads(body2) == {"b": 2}


def test_build_detection_message_matches_fs08_shape() -> None:
    message = build_detection_message(
        message_id="b7e2",
        source_id=0,
        frame_number=12345,
        class_id=0,
        confidence=0.91,
        bbox_left=420.0,
        bbox_top=180.0,
        bbox_width=250.0,
        bbox_height=190.0,
    )

    assert message == {
        "schemaVersion": SCHEMA_VERSION,
        "messageId": "b7e2",
        "sourceId": 0,
        "frameNumber": 12345,
        "classId": 0,
        "confidence": 0.91,
        "boundingBox": {"left": 420.0, "top": 180.0, "width": 250.0, "height": 190.0},
    }


def test_schema_version_is_2() -> None:
    assert SCHEMA_VERSION == 2


def test_decode_acknowledgement_parses_json_body() -> None:
    body = json.dumps(
        {"messageId": "abc", "outcome": "accepted", "eventId": "xyz", "snapshotRequired": True}
    ).encode("utf-8")
    message = decode_acknowledgement(body)
    assert message["outcome"] == "accepted"
    assert message["snapshotRequired"] is True


def test_acknowledgement_frame_kind_is_distinct_from_detection_and_snapshot() -> None:
    assert len({FRAME_KIND_DETECTION, FRAME_KIND_SNAPSHOT, FRAME_KIND_ACKNOWLEDGEMENT}) == 3


def test_encode_snapshot_message_round_trips_event_id_and_jpeg_bytes() -> None:
    jpeg_bytes = b"\xff\xd8\xff\xe0fake-jpeg-body"
    frame = encode_snapshot_message("3f9c-event-id", jpeg_bytes)

    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    body = frame[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length]
    event_id, decoded_jpeg_bytes = decode_snapshot_message(body)

    assert kind == FRAME_KIND_SNAPSHOT
    assert event_id == "3f9c-event-id"
    assert decoded_jpeg_bytes == jpeg_bytes


def test_max_snapshot_frame_bytes_accommodates_5_mib_jpeg_plus_overhead() -> None:
    assert MAX_SNAPSHOT_FRAME_BYTES > 5 * 1024 * 1024
    assert MAX_SNAPSHOT_FRAME_BYTES > MAX_FRAME_BYTES
