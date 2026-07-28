from __future__ import annotations

import json

from deepstream_bridge.protocol import (
    FRAME_LENGTH_PREFIX_BYTES,
    SCHEMA_VERSION,
    encode_frame,
    encode_message,
)


def test_encode_frame_prefixes_exact_length() -> None:
    frame = encode_frame(b"abc")
    assert frame[:FRAME_LENGTH_PREFIX_BYTES] == (3).to_bytes(FRAME_LENGTH_PREFIX_BYTES, "big")
    assert frame[FRAME_LENGTH_PREFIX_BYTES:] == b"abc"


def test_encode_message_round_trips_json() -> None:
    payload = {"schema_version": SCHEMA_VERSION, "class_id": 0, "confidence": 0.91}
    frame = encode_message(payload)
    length = int.from_bytes(frame[:4], "big")
    body = frame[4 : 4 + length]
    assert json.loads(body) == payload


def test_multiple_messages_concatenate_and_split_correctly() -> None:
    combined = encode_message({"a": 1}) + encode_message({"b": 2})

    length1 = int.from_bytes(combined[:4], "big")
    body1 = combined[4 : 4 + length1]
    rest = combined[4 + length1 :]
    length2 = int.from_bytes(rest[:4], "big")
    body2 = rest[4 : 4 + length2]

    assert json.loads(body1) == {"a": 1}
    assert json.loads(body2) == {"b": 2}
