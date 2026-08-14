"""Wire protocol for the Bridge <-> Agent Unix domain socket channel (IP-07 T-88, IP-10 T-131,
FS-05 §4/§4.6, FS-08 §4).

This module deliberately **duplicates** the constants in the Agent's own
``weapon_detection_agent.detection.protocol`` rather than importing it — FS-05 §4.6 binding rule 5
forbids the Bridge from importing anything under ``agent/src/``, so the two modules are the
documented reference each side's framing code is written to match, not a shared dependency. Keeping
them in sync is a manual, reviewed discipline (T-90 adds a mechanical check for the one place this
could silently drift — the socket-path fallback literal in ``run.sh`` — but the framing constants
themselves have no such check; a change to one side's framing is a breaking wire change either way).

**Frame shape (v2, FS-08 §4.1 — the transport itself is now bidirectional).** A 1-byte frame ``kind``
discriminator, a 4-byte unsigned big-endian length prefix, then exactly that many body bytes:

* ``FRAME_KIND_DETECTION`` (Bridge -> Agent): JSON body, the v2 detection message (§4.3), bounded by
  ``MAX_FRAME_BYTES`` (unchanged from v1 — still a few hundred bytes in practice).
* ``FRAME_KIND_SNAPSHOT`` (Bridge -> Agent): a small ``eventId`` sub-header followed by raw JPEG
  bytes (never JSON/base64 — base64 would inflate a multi-megabyte image by a third for no reason),
  bounded by ``MAX_SNAPSHOT_FRAME_BYTES``. See :func:`encode_snapshot_message`/
  :func:`decode_snapshot_message`.
* ``FRAME_KIND_ACKNOWLEDGEMENT`` (Agent -> Bridge): JSON body, the acknowledgement message (§4.4),
  bounded by ``MAX_FRAME_BYTES`` (small, same cap as a detection message).

A single 4-byte-prefix-only v1 frame (no kind byte) is not something *this* Bridge build ever emits
or reads — FS-08 §4.2's backward-compatibility statement is about an *un-upgraded* (pre-IP-10)
Bridge process talking to an upgraded Agent, not about this module speaking two wire formats at
once. ``SCHEMA_VERSION``/``LEGACY_SCHEMA_VERSION`` below exist only so this module still knows which
JSON ``schemaVersion``/``schema_version`` value the old, no-longer-built Bridge used to send.
"""

from __future__ import annotations

import json
from typing import Any, Tuple

# Must match weapon_detection_agent.detection.protocol.FRAME_LENGTH_PREFIX_BYTES exactly.
FRAME_LENGTH_PREFIX_BYTES = 4
FRAME_LENGTH_BYTEORDER = "big"

# New in v2 (FS-08 §4.1): a 1-byte frame-kind discriminator ahead of the existing length prefix, so
# a JSON detection/acknowledgement frame and a much larger raw-JPEG snapshot frame can share one
# connection without raising MAX_FRAME_BYTES for the common (detection) case.
FRAME_KIND_PREFIX_BYTES = 1
FRAME_KIND_DETECTION = 1
FRAME_KIND_SNAPSHOT = 2
FRAME_KIND_ACKNOWLEDGEMENT = 3

FRAME_HEADER_BYTES = FRAME_KIND_PREFIX_BYTES + FRAME_LENGTH_PREFIX_BYTES

# Must match weapon_detection_agent.detection.protocol.MAX_FRAME_BYTES exactly. Real detection/
# acknowledgement messages (§4.3/§4.4) are a few hundred bytes; 64 KiB leaves headroom without
# letting a malformed/hostile frame drive an unbounded allocation.
MAX_FRAME_BYTES = 65536

# WDA_SNAPSHOT_MAX_FILE_BYTES (FS-08 §5 / task brief settings list) default is 5 MiB — this is the
# Agent-side spool's own maximum accepted JPEG file size. The wire cap here is that same 5 MiB plus
# a small, generous allowance for encode_snapshot_message's own eventId sub-header (well under 1
# KiB in practice, a UUID) so a legitimate max-size JPEG is never rejected purely for wire overhead.
MAX_SNAPSHOT_FRAME_BYTES = 5 * 1024 * 1024 + 4096

# The wire schema version this Bridge build emits (FS-08 §4.2). LEGACY_SCHEMA_VERSION documents the
# value a pre-IP-10 Bridge build used to send — this module never emits it, only records it.
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1

# Used only when the Bridge's own CLI/config exposes no override (item 7: "queue capacity must be
# configurable or use the approved Bridge protocol default") — this is that default, matching
# AgentSettings.detection_queue_capacity's own default (WDA_DETECTION_QUEUE_CAPACITY=1000) so the
# two sides of the boundary agree on a sane bound even though neither reads the other's setting.
DEFAULT_QUEUE_CAPACITY = 1000

# A UUID4 string is 36 ASCII characters; 2 bytes (max 65535) is generous headroom for
# encode_snapshot_message's eventId sub-header without inventing an unbounded field.
_EVENT_ID_LENGTH_PREFIX_BYTES = 2


def encode_frame(payload: bytes, kind: int = FRAME_KIND_DETECTION) -> bytes:
    """Prefix ``payload`` with its 1-byte frame kind and 4-byte big-endian length, producing one
    complete wire frame."""
    return (
        kind.to_bytes(FRAME_KIND_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + len(payload).to_bytes(FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + payload
    )


def decode_frame_header(header: bytes) -> Tuple[int, int]:
    """Parse exactly ``FRAME_HEADER_BYTES`` of header bytes into ``(kind, body_length)``."""
    kind = int.from_bytes(header[:FRAME_KIND_PREFIX_BYTES], FRAME_LENGTH_BYTEORDER)
    length = int.from_bytes(header[FRAME_KIND_PREFIX_BYTES:], FRAME_LENGTH_BYTEORDER)
    return kind, length


def encode_message(payload: dict) -> bytes:
    """Serialize a v2 detection payload (a plain dict, FS-08 §4.3 schema) to a complete
    ``FRAME_KIND_DETECTION`` wire frame.

    Uses the standard library ``json`` module only — never ``pickle``/``eval`` (FS-05 §4.5: no
    arbitrary code execution risk from what this process sends).
    """
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return encode_frame(body, kind=FRAME_KIND_DETECTION)


def decode_acknowledgement(body: bytes) -> dict:
    """Parse an acknowledgement frame's body (FS-08 §4.4 schema) into a plain dict."""
    return json.loads(body.decode("utf-8"))


def encode_snapshot_message(event_id: str, jpeg_bytes: bytes) -> bytes:
    """Build a complete ``FRAME_KIND_SNAPSHOT`` wire frame carrying ``jpeg_bytes`` plus the
    ``eventId`` the Agent needs to correlate it (FS-08 §5's "second, larger frame type").

    Sub-framing inside the body (rather than JSON/base64) keeps a multi-megabyte JPEG's wire size
    equal to its actual byte size: a 2-byte length-prefixed UTF-8 ``eventId``, then the raw JPEG
    bytes verbatim.
    """
    event_id_bytes = event_id.encode("utf-8")
    body = (
        len(event_id_bytes).to_bytes(_EVENT_ID_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + event_id_bytes
        + jpeg_bytes
    )
    return encode_frame(body, kind=FRAME_KIND_SNAPSHOT)


def decode_snapshot_message(body: bytes) -> Tuple[str, bytes]:
    """Inverse of :func:`encode_snapshot_message`."""
    length = int.from_bytes(body[:_EVENT_ID_LENGTH_PREFIX_BYTES], FRAME_LENGTH_BYTEORDER)
    offset = _EVENT_ID_LENGTH_PREFIX_BYTES
    event_id = body[offset : offset + length].decode("utf-8")
    jpeg_bytes = body[offset + length :]
    return event_id, jpeg_bytes


def build_detection_message(
    *,
    message_id: str,
    source_id: int,
    frame_number: int,
    class_id: int,
    confidence: float,
    bbox_left: float,
    bbox_top: float,
    bbox_width: float,
    bbox_height: float,
) -> dict[str, Any]:
    """The exact v2 detection message shape (FS-08 §4.3) — camelCase field names and a nested
    ``boundingBox`` object, a deliberate, documented deviation from v1's flat snake_case fields for
    the new fields only (FS-08 §4.3's own binding note)."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "messageId": message_id,
        "sourceId": source_id,
        "frameNumber": frame_number,
        "classId": class_id,
        "confidence": confidence,
        "boundingBox": {
            "left": bbox_left,
            "top": bbox_top,
            "width": bbox_width,
            "height": bbox_height,
        },
    }
