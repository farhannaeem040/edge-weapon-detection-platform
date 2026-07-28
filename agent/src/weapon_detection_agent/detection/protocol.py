"""Wire protocol for the Agent <-> DeepStream Bridge Unix domain socket channel (IP-07 T-86, FS-05
§4; ADR-005).

This is the single source of truth for the framing format both sides of the socket use: a fixed
4-byte unsigned big-endian length prefix, followed by exactly that many UTF-8 JSON bytes — one
message per frame, any number of frames per connection. :class:`~weapon_detection_agent.detection.
ingest_handler.DetectionIngestHandler` (T-86, this side) is the only consumer of these constants
today; the separate DeepStream Bridge application (T-88, its own package/venv, FS-05 §4.6) is
expected to **duplicate these exact constants** rather than import this module — it may not
import anything under ``agent/src/`` (FS-05 §4.6 binding rule 5), so this module is the documented
reference the Bridge's own framing code is written to match, not a shared dependency.

Deliberately not used: newline framing, ``pickle``, ``eval``, any Python-object serialization
format, or unbounded ``read()`` — the length prefix is read first and bounds every subsequent read.
"""

from __future__ import annotations

from typing import Literal

# The length prefix is always exactly this many bytes, unsigned, big-endian.
FRAME_LENGTH_PREFIX_BYTES = 4
FRAME_LENGTH_BYTEORDER: Literal["big"] = "big"

# The maximum permitted JSON payload size, in bytes, enforced from the length prefix alone —
# **before** the payload itself is read (task requirement: a hostile/malformed oversized frame must
# never cause an unbounded allocation or read). A real detection message (FS-05 §5) is a few hundred
# bytes of JSON; 64 KiB leaves generous headroom for future fields while still bounding memory. No
# `WDA_DETECTION_*` setting exists for this (T-81 did not introduce one), so this is the protocol
# constant FS-05/IP-07 anticipate — see IP-07 T-86.
MAX_FRAME_BYTES = 65536

# The only wire schema version this build accepts (FS-05 §5). A Bridge sending any other value is
# rejected explicitly, never guessed at or partially parsed.
SUPPORTED_SCHEMA_VERSION = 1


def encode_frame(payload: bytes) -> bytes:
    """Prefix ``payload`` with its length, producing one complete wire frame.

    The Agent-side handler never calls this (it only reads frames); it exists so this protocol's own
    tests — and, verbatim, the future Bridge's transport module (T-88) — build wire-correct frames
    from one documented place. Deliberately performs no bounds checking: callers exercising the
    server's own zero-length/oversized-frame rejection build exactly those frames through here too.
    """
    return len(payload).to_bytes(FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER) + payload
