"""Test-only Agent<->Bridge protocol compatibility check (IP-07 T-90, FS-05 §4.6, task item 5).

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

import sys
from pathlib import Path
from typing import Any

import pytest

import weapon_detection_agent.detection.protocol as agent_protocol
from weapon_detection_agent.detection.validation import _REQUIRED_FLOAT_FIELDS, _REQUIRED_INT_FIELDS

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


def test_schema_version_matches(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    assert agent_protocol.SUPPORTED_SCHEMA_VERSION == bridge_protocol_module.SCHEMA_VERSION == 1


def test_encode_frame_produces_identical_bytes_on_both_sides(bridge_protocol: Any) -> None:
    bridge_protocol_module, _ = bridge_protocol
    payload = b'{"schema_version":1,"class_id":0}'
    assert agent_protocol.encode_frame(payload) == bridge_protocol_module.encode_frame(payload)


def test_bridge_raw_wire_fields_match_agent_required_fields(bridge_protocol: Any) -> None:
    """The Bridge's ``RawDetection`` payload fields (minus ``schema_version``, which is a framing
    concern the transport layer checks before validation ever runs) are exactly the Agent
    validator's required raw fields — no more, no less."""
    _, bridge_probe = bridge_protocol
    detection = bridge_probe.RawDetection(
        schema_version=1,
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
    bridge_fields = set(detection.to_payload().keys()) - {"schema_version"}
    agent_required_fields = set(_REQUIRED_INT_FIELDS) | set(_REQUIRED_FLOAT_FIELDS)

    assert bridge_fields == agent_required_fields


def test_bridge_never_emits_agent_owned_fields(bridge_protocol: Any) -> None:
    _, bridge_probe = bridge_protocol
    detection = bridge_probe.RawDetection(
        schema_version=1,
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


def test_multiple_frames_per_connection_supported_by_both_sides(bridge_protocol: Any) -> None:
    """Both sides frame independently (no connection-level state beyond byte-stream position) —
    two consecutive encoded frames concatenate and split back out identically on both sides."""
    bridge_protocol_module, _ = bridge_protocol
    import json

    agent_frame = agent_protocol.encode_frame(json.dumps({"a": 1}).encode("utf-8"))
    bridge_frame = bridge_protocol_module.encode_message({"b": 2})
    combined = agent_frame + bridge_frame

    length1 = int.from_bytes(combined[:4], "big")
    body1 = combined[4 : 4 + length1]
    rest = combined[4 + length1 :]
    length2 = int.from_bytes(rest[:4], "big")
    body2 = rest[4 : 4 + length2]

    assert json.loads(body1) == {"a": 1}
    assert json.loads(body2) == {"b": 2}
