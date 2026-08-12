"""Wire protocol for the Agent <-> DeepStream Bridge Unix domain socket channel (IP-07 T-86, FS-05
§4; ADR-005; IP-10 T-139, FS-08 §4).

This is the single source of truth, on the Agent side, for the framing format both sides of the
socket use. :class:`~weapon_detection_agent.detection.ingest_handler.DetectionIngestHandler` (T-86,
this side) is the only consumer of these constants today; the separate DeepStream Bridge application
(T-88/IP-10 T-131, its own package/venv, FS-05 §4.6) **duplicates these exact constants** rather
than importing this module — it may not import anything under ``agent/src/`` (FS-05 §4.6 binding
rule 5), so the two are kept in sync by hand, verified by
``tests/test_bridge_protocol_compatibility.py``.

**Frame shape (v2, FS-08 §4.1 — the transport is now bidirectional).** A 1-byte frame ``kind``
discriminator, then a 4-byte unsigned big-endian length prefix, then exactly that many body bytes:

* :data:`FRAME_KIND_DETECTION` (Bridge -> Agent): JSON body, the detection message (v1 or v2, FS-08
  §4.2/§4.3), bounded by :data:`MAX_FRAME_BYTES`.
* :data:`FRAME_KIND_SNAPSHOT` (Bridge -> Agent): a 2-byte-length-prefixed UTF-8 ``eventId``
  sub-header followed by raw JPEG bytes (never JSON/base64), bounded by
  :data:`MAX_SNAPSHOT_FRAME_BYTES`.
* :data:`FRAME_KIND_ACKNOWLEDGEMENT` (Agent -> Bridge): JSON body, the acknowledgement message
  (FS-08 §4.4), bounded by :data:`MAX_FRAME_BYTES`.

Deliberately not used: newline framing, ``pickle``, ``eval``, any Python-object serialization
format, or unbounded ``read()`` — the length prefix is always read first and bounds every subsequent
read.
"""

from __future__ import annotations

from typing import Literal

# The length prefix is always exactly this many bytes, unsigned, big-endian. Unchanged since v1.
FRAME_LENGTH_PREFIX_BYTES = 4
FRAME_LENGTH_BYTEORDER: Literal["big"] = "big"

# New in v2 (FS-08 §4.1): a 1-byte frame-kind discriminator ahead of the length prefix, so a JSON
# detection/acknowledgement frame and a much larger raw-JPEG snapshot frame can share one connection
# without raising MAX_FRAME_BYTES for the common (detection) case.
FRAME_KIND_PREFIX_BYTES = 1
FRAME_KIND_DETECTION = 1
FRAME_KIND_SNAPSHOT = 2
FRAME_KIND_ACKNOWLEDGEMENT = 3

FRAME_HEADER_BYTES = FRAME_KIND_PREFIX_BYTES + FRAME_LENGTH_PREFIX_BYTES

# The maximum permitted JSON payload size, in bytes, enforced from the length prefix alone —
# **before** the payload itself is read (a hostile/malformed oversized frame must never cause an
# unbounded allocation or read). A real detection/acknowledgement message is a few hundred bytes of
# JSON; 64 KiB leaves generous headroom. Unchanged from v1.
MAX_FRAME_BYTES = 65536

# Matches the Bridge's own MAX_SNAPSHOT_FRAME_BYTES exactly: WDA_SNAPSHOT_MAX_FILE_BYTES's default
# (5 MiB) plus a small, generous allowance for the eventId sub-header, so a legitimate max-size JPEG
# is never rejected purely by the transport cap. The content-level size bound is enforced separately
# by snapshot capture validation (FS-08 §5), not by this constant.
MAX_SNAPSHOT_FRAME_BYTES = 5 * 1024 * 1024 + 4096

# The 2-byte length prefix on a FRAME_KIND_SNAPSHOT body's eventId sub-header (a UUID4 string is 36
# ASCII characters; 2 bytes is generous headroom without an unbounded field). Matches the Bridge.
SNAPSHOT_EVENT_ID_LENGTH_PREFIX_BYTES = 2

# The wire schema versions this build accepts inside a FRAME_KIND_DETECTION JSON body (FS-08 §4.2) —
# both v1 (legacy, no acknowledgement ever sent) and v2 (adds `messageId`, generates an
# acknowledgement). A frozenset rather than a single int so a caller can express "is this version
# supported" without hardcoding the set twice.
SUPPORTED_SCHEMA_VERSIONS = frozenset({1, 2})

# Retained for any caller still comparing against a single "the" supported version; v1 was the only
# value this build accepted before FS-08. New code should prefer SUPPORTED_SCHEMA_VERSIONS.
SUPPORTED_SCHEMA_VERSION = 1

# The schema_version introduced by FS-08 — the only version whose messages carry `messageId` and
# ever cause an acknowledgement to be sent.
SNAPSHOT_PROTOCOL_SCHEMA_VERSION = 2


def encode_frame(payload: bytes, kind: int = FRAME_KIND_DETECTION) -> bytes:
    """Prefix ``payload`` with its 1-byte frame kind and 4-byte big-endian length.

    Deliberately performs no bounds checking: callers exercising the server's own zero-length/
    oversized-frame rejection build exactly those frames through here too.
    """
    return (
        kind.to_bytes(FRAME_KIND_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + len(payload).to_bytes(FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + payload
    )


def decode_frame_header(header: bytes) -> tuple[int, int]:
    """Parse exactly :data:`FRAME_HEADER_BYTES` of header bytes into ``(kind, body_length)``."""
    kind = int.from_bytes(header[:FRAME_KIND_PREFIX_BYTES], FRAME_LENGTH_BYTEORDER)
    length = int.from_bytes(header[FRAME_KIND_PREFIX_BYTES:], FRAME_LENGTH_BYTEORDER)
    return kind, length


def encode_snapshot_message(event_id: str, jpeg_bytes: bytes) -> bytes:
    """Build a complete :data:`FRAME_KIND_SNAPSHOT` wire frame (never used by the Agent, which only
    reads these — provided so the Agent's own tests can build wire-correct snapshot frames without
    depending on the separate Bridge package)."""
    event_id_bytes = event_id.encode("utf-8")
    body = (
        len(event_id_bytes).to_bytes(SNAPSHOT_EVENT_ID_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER)
        + event_id_bytes
        + jpeg_bytes
    )
    return encode_frame(body, kind=FRAME_KIND_SNAPSHOT)


def decode_snapshot_message(body: bytes) -> tuple[str, bytes]:
    """Inverse of :func:`encode_snapshot_message` — split a snapshot frame's body into
    ``(event_id, jpeg_bytes)``."""
    length = int.from_bytes(body[:SNAPSHOT_EVENT_ID_LENGTH_PREFIX_BYTES], FRAME_LENGTH_BYTEORDER)
    offset = SNAPSHOT_EVENT_ID_LENGTH_PREFIX_BYTES
    event_id = body[offset : offset + length].decode("utf-8")
    jpeg_bytes = body[offset + length :]
    return event_id, jpeg_bytes
