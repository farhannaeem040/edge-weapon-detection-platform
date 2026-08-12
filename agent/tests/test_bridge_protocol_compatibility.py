"""Test-only Agent<->Bridge protocol compatibility check (IP-07 T-90, FS-05 §4.6; IP-10 T-131/T-139,
FS-08 §4, task item 5).

The Agent (``weapon_detection_agent.detection.protocol``) and the Bridge
(``deployment/jetson/deepstream/bridge/app/deepstream_bridge/protocol.py``) each **duplicate** the
wire-protocol constants by design (FS-05 §4.6 binding rule 5: the Bridge may never import
``agent/src/``, so there is no shared dependency to keep them in sync automatically). This module is
the "test-only compatibility check [that] may load both definitions independently" the task
explicitly permits — it imports the Bridge's ``protocol``/``probe`` modules via a scoped
``sys.path`` insertion, undone after the test session, so no other Agent test or Agent production
code is ever exposed to the Bridge's package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import weapon_detection_agent.detection.protocol as agent_protocol
from weapon_detection_agent.detection.validation import (
    _OPTIONAL_INT_FIELDS,
    _REQUIRED_FLOAT_FIELDS,
    _REQUIRED_INT_FIELDS,
)

_BRIDGE_APP_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "jetson"
    / "deepstream"
    / "bridge"
    / "app"
)

_AGENT_OWNED_FIELDS = frozenset(
    {
        "event_id",
        "device_id",
        "camera_id",
        "class_name",
        "detected_at_utc",
        "created_at_utc",
        "delivery_status",
    }
)


@pytest.fixture(scope="module")
def bridge_protocol() -> Any:
    """Import ``deepstream_bridge.protocol`` for the duration of this module only, then remove both
    the sys.path entry and every ``deepstream_bridge*`` module this import added — so no other test
    module in this same pytest process ever sees the Bridge package as importable/imported."""
    inserted = str(_BRIDGE_APP_DIR) not in sys.path
    if inserted:
        sys.path.insert(0, str(_BRIDGE_APP_DIR))
    try:
        import deepstream_bridge.probe as bridge_probe
        import deepstream_bridge.protocol as bridge_protocol_module

        yield bridge_protocol_module, bridge_probe
    finally:
        if inserted:
            sys.path.remove(str(_BRIDGE_APP_DIR))
        for name in [
            m for m in sys.modules if m == "deepstream_bridge" or m.startswith("deepstream_bridge.")
        ]:
            del sys.modules[name]


def test_frame_length_prefix_bytes_match(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert (
        agent_protocol.FRAME_LENGTH_PREFIX_BYTES
        == bridge_protocol_module.FRAME_LENGTH_PREFIX_BYTES
        == 4
    )


def test_frame_length_byteorder_matches(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert (
        agent_protocol.FRAME_LENGTH_BYTEORDER
        == bridge_protocol_module.FRAME_LENGTH_BYTEORDER
        == "big"
    )


def test_max_frame_bytes_match(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert agent_protocol.MAX_FRAME_BYTES == bridge_protocol_module.MAX_FRAME_BYTES


def test_max_snapshot_frame_bytes_match(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert (
        agent_protocol.MAX_SNAPSHOT_FRAME_BYTES == bridge_protocol_module.MAX_SNAPSHOT_FRAME_BYTES
    )


def test_frame_kind_values_match(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert agent_protocol.FRAME_KIND_DETECTION == bridge_protocol_module.FRAME_KIND_DETECTION == 1
    assert agent_protocol.FRAME_KIND_SNAPSHOT == bridge_protocol_module.FRAME_KIND_SNAPSHOT == 2
    assert (
        agent_protocol.FRAME_KIND_ACKNOWLEDGEMENT
        == bridge_protocol_module.FRAME_KIND_ACKNOWLEDGEMENT
        == 3
    )


def test_frame_header_bytes_match(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert agent_protocol.FRAME_HEADER_BYTES == bridge_protocol_module.FRAME_HEADER_BYTES == 5


def test_schema_version_matches(bridge_protocol: Any) -> None:
    """The Bridge (IP-10) emits schemaVersion 2; the Agent accepts both 1 (legacy) and 2 (FS-08
    §4.2)."""
    bridge_protocol_module, _ = bridge_protocol
    assert bridge_protocol_module.SCHEMA_VERSION == 2
    assert bridge_protocol_module.SCHEMA_VERSION in agent_protocol.SUPPORTED_SCHEMA_VERSIONS
    assert agent_protocol.SNAPSHOT_PROTOCOL_SCHEMA_VERSION == bridge_protocol_module.SCHEMA_VERSION
    assert bridge_protocol_module.LEGACY_SCHEMA_VERSION == agent_protocol.SUPPORTED_SCHEMA_VERSION
    assert bridge_protocol_module.LEGACY_SCHEMA_VERSION in agent_protocol.SUPPORTED_SCHEMA_VERSIONS


def test_encode_frame_produces_identical_bytes_on_both_sides(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    payload = b'{"schemaVersion":2,"classId":0}'
    assert agent_protocol.encode_frame(payload) == bridge_protocol_module.encode_frame(payload)
    assert agent_protocol.encode_frame(
        payload, kind=agent_protocol.FRAME_KIND_SNAPSHOT
    ) == bridge_protocol_module.encode_frame(
        payload, kind=bridge_protocol_module.FRAME_KIND_SNAPSHOT
    )


def test_decode_frame_header_matches(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    frame = agent_protocol.encode_frame(b"abc", kind=agent_protocol.FRAME_KIND_SNAPSHOT)
    header = frame[: agent_protocol.FRAME_HEADER_BYTES]

    assert agent_protocol.decode_frame_header(header) == bridge_protocol_module.decode_frame_header(
        header
    )


def test_bridge_raw_wire_fields_match_agent_required_fields(bridge_protocol: Any) -> None:
    """The Bridge's internal ``RawDetection`` payload fields (minus ``schema_version``/
    ``message_id``, which are framing/correlation concerns the Agent's raw-fact validator never
    reads as required fields) are exactly the Agent validator's required raw fields — no more, no
    less."""
    _, bridge_probe = bridge_protocol
    detection = bridge_probe.RawDetection(
        schema_version=2,
        message_id="11111111-1111-1111-1111-111111111111",
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
    bridge_fields = set(detection.to_payload().keys()) - {"schema_version", "message_id"}
    agent_fields = (
        set(_REQUIRED_INT_FIELDS) | set(_OPTIONAL_INT_FIELDS) | set(_REQUIRED_FLOAT_FIELDS)
    )

    assert bridge_fields == agent_fields


def test_bridge_never_emits_agent_owned_fields(bridge_protocol: Any) -> None:
    _, bridge_probe = bridge_protocol
    detection = bridge_probe.RawDetection(
        schema_version=2,
        message_id="11111111-1111-1111-1111-111111111111",
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
    assert _AGENT_OWNED_FIELDS.isdisjoint(detection.to_payload().keys())
    assert _AGENT_OWNED_FIELDS.isdisjoint(detection.to_wire_message().keys())


def test_multiple_frames_per_connection_supported_by_both_sides(bridge_protocol: Any) -> None:
    """Both sides frame independently (no connection-level state beyond byte-stream position) —
    two consecutive encoded frames concatenate and split back out identically on both sides."""
    bridge_protocol_module, _ = bridge_protocol

    agent_frame = agent_protocol.encode_frame(json.dumps({"a": 1}).encode("utf-8"))
    bridge_frame = bridge_protocol_module.encode_message({"b": 2})
    combined = agent_frame + bridge_frame

    header_bytes = agent_protocol.FRAME_HEADER_BYTES
    kind1, length1 = agent_protocol.decode_frame_header(combined[:header_bytes])
    body1 = combined[header_bytes : header_bytes + length1]
    rest = combined[header_bytes + length1 :]
    kind2, length2 = agent_protocol.decode_frame_header(rest[:header_bytes])
    body2 = rest[header_bytes : header_bytes + length2]

    assert kind1 == kind2 == agent_protocol.FRAME_KIND_DETECTION
    assert json.loads(body1) == {"a": 1}
    assert json.loads(body2) == {"b": 2}


def test_snapshot_message_round_trips_across_both_sides(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    event_id = "22222222-2222-2222-2222-222222222222"
    jpeg_bytes = b"\xff\xd8\xff\xe0fake-jpeg-body"

    bridge_frame = bridge_protocol_module.encode_snapshot_message(event_id, jpeg_bytes)
    agent_frame = agent_protocol.encode_snapshot_message(event_id, jpeg_bytes)
    assert agent_frame == bridge_frame

    header_bytes = agent_protocol.FRAME_HEADER_BYTES
    kind, length = agent_protocol.decode_frame_header(bridge_frame[:header_bytes])
    body = bridge_frame[header_bytes : header_bytes + length]

    assert kind == agent_protocol.FRAME_KIND_SNAPSHOT == bridge_protocol_module.FRAME_KIND_SNAPSHOT
    assert agent_protocol.decode_snapshot_message(body) == (event_id, jpeg_bytes)
    assert bridge_protocol_module.decode_snapshot_message(body) == (event_id, jpeg_bytes)
