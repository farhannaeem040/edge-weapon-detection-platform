"""Wire protocol for the Bridge -> Agent Unix domain socket channel (IP-07 T-88, FS-05 §4/§4.6).

This module deliberately **duplicates** the constants in the Agent's own
``weapon_detection_agent.detection.protocol`` rather than importing it — FS-05 §4.6 binding rule 5
forbids the Bridge from importing anything under ``agent/src/``, so the two modules are the
documented reference each side's framing code is written to match, not a shared dependency. Keeping
them in sync is a manual, reviewed discipline (T-90 adds a mechanical check for the one place this
could silently drift — the socket-path fallback literal in ``run.sh`` — but the framing constants
themselves have no such check; a change to one side's framing is a breaking wire change either way).

Frame shape: a fixed 4-byte unsigned big-endian length prefix, followed by exactly that many UTF-8
JSON bytes. One message per frame, any number of frames per connection.
"""

from __future__ import annotations

import json
from typing import Any, Literal

# Must match weapon_detection_agent.detection.protocol.FRAME_LENGTH_PREFIX_BYTES exactly.
FRAME_LENGTH_PREFIX_BYTES = 4
FRAME_LENGTH_BYTEORDER: Literal["big"] = "big"

# Must match weapon_detection_agent.detection.protocol.MAX_FRAME_BYTES exactly. Real detection
# messages (§ schema below) are a few hundred bytes; 64 KiB leaves headroom without letting a
# malformed/hostile frame drive an unbounded allocation.
MAX_FRAME_BYTES = 65536

# The only wire schema version this Bridge build emits (FS-05 §5).
SCHEMA_VERSION = 1

# Used only when the Bridge's own CLI/config exposes no override (item 7: "queue capacity must be
# configurable or use the approved Bridge protocol default") — this is that default, matching
# AgentSettings.detection_queue_capacity's own default (WDA_DETECTION_QUEUE_CAPACITY=1000) so the
# two sides of the boundary agree on a sane bound even though neither reads the other's setting.
DEFAULT_QUEUE_CAPACITY = 1000


def encode_frame(payload: bytes) -> bytes:
    """Prefix ``payload`` with its 4-byte big-endian length, producing one complete wire frame."""
    return len(payload).to_bytes(FRAME_LENGTH_PREFIX_BYTES, FRAME_LENGTH_BYTEORDER) + payload


def encode_message(payload: dict[str, Any]) -> bytes:
    """Serialize a raw detection payload (a plain dict, §5 schema) to a complete wire frame.

    Uses the standard library ``json`` module only — never ``pickle``/``eval`` (FS-05 §4.5: no
    arbitrary code execution risk from what this process sends).
    """
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return encode_frame(body)
