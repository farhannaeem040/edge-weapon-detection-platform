"""Snapshot candidate bookkeeping (IP-10 T-133/T-135, FS-08 §3/§9).

Two small, bounded, TTL-expiring, thread-safe structures — pure Python, no ``Gst``/``pyds`` import
at module scope (same offline-testability discipline as ``probe.py``):

* :class:`CandidateFrameTracker` — records, per detection, that a ``frame_number`` had >=1 raw
  detection (for the snapshot branch's valve-gating probe) and a ``message_id -> frame_number``
  correlation (for the acknowledgement-driven capture-request wiring in ``pipeline.py``).
* :class:`SnapshotCandidateCache` — holds the JPEG **bytes** (never a ``GstBuffer``) produced by the
  appsink's ``new-sample`` callback, keyed by ``frame_number``, released on TTL expiry, explicit
  ``pop``/``drop``, or :meth:`clear` (shutdown).

Both are accessed from at least two different threads (the GStreamer pad-probe/streaming threads and
the transport worker's acknowledgement-reader thread) — every public method is protected by a single
internal lock per instance.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

SendSnapshot = Callable[[str, bytes], None]


class CandidateFrameTracker:
    """Tracks which frames/messages are current snapshot candidates (FS-08 §3's gating paragraph).

    ``max_retained`` bounds both the candidate-frame set and the message-id correlation map
    independently; ``ttl_seconds`` bounds how long either kind of entry survives without being
    consumed/resolved. Never raises for an already-expired/absent/unknown key — callers (a
    pad-probe callback, an acknowledgement dispatch callback) must never fault the pipeline.
    """

    def __init__(
        self,
        *,
        max_retained: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retained <= 0:
            raise ValueError("max_retained must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self._max_retained = max_retained
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # (source_id, frame_number) -> expiry.
        self._candidate_frames: "OrderedDict[Tuple[int, int], float]" = OrderedDict()
        # message_id -> ((source_id, frame_number), expiry). The value is the *source-qualified*
        # key, because a frame number alone is not unique across demultiplexed cameras.
        self._message_frames: "OrderedDict[str, Tuple[Tuple[int, int], float]]" = OrderedDict()

    def record_detection(self, source_id: int, message_id: str, frame_number: int) -> None:
        """Mark ``frame_number`` as a snapshot candidate and remember ``message_id`` resolves to
        it, both with a fresh TTL. Called once per raw detection (task item 6's ``on_candidate``)."""
        with self._lock:
            now = self._clock()
            expiry = now + self._ttl_seconds
            self._expire_locked(now)

            key = (source_id, frame_number)
            self._candidate_frames[key] = expiry
            self._candidate_frames.move_to_end(key)
            while len(self._candidate_frames) > self._max_retained:
                self._candidate_frames.popitem(last=False)

            self._message_frames[message_id] = ((source_id, frame_number), expiry)
            self._message_frames.move_to_end(message_id)
            while len(self._message_frames) > self._max_retained:
                self._message_frames.popitem(last=False)

    def consume_candidate(self, source_id: int, frame_number: int) -> bool:
        """Return whether ``frame_number`` is (still, unexpired) a candidate, consuming the entry
        either way — the snapshot branch's valve-gating probe calls this exactly once per buffer
        reaching that branch."""
        with self._lock:
            self._expire_locked(self._clock())
            return self._candidate_frames.pop((source_id, frame_number), None) is not None

    def resolve_and_release(self, message_id: str) -> Optional[Tuple[int, int]]:
        """Look up and remove the ``frame_number`` correlated with ``message_id``, or ``None`` if
        unknown/expired/already resolved.

        Removing on lookup (rather than leaving the mapping in place) is what makes a duplicate
        acknowledgement for the same ``message_id`` a safe no-op (FS-08 §4.4: "must never write a
        second JPEG file") — the second lookup simply finds nothing.
        """
        with self._lock:
            self._expire_locked(self._clock())
            entry = self._message_frames.pop(message_id, None)
            return entry[0] if entry is not None else None

    def _expire_locked(self, now: float) -> None:
        for mapping in (self._candidate_frames, self._message_frames):
            expired = [
                key
                for key, value in mapping.items()
                if (value if isinstance(value, float) else value[1]) <= now
            ]
            for key in expired:
                del mapping[key]

    def __len__(self) -> int:
        with self._lock:
            self._expire_locked(self._clock())
            return len(self._candidate_frames)


class SnapshotCandidateCache:
    """Bounded, TTL-expiring cache of JPEG ``bytes`` keyed by ``frame_number`` (FS-08 §3: "holds
    only the resulting plain bytes ... never a GstBuffer"). Entries are populated on the appsink's
    own callback thread and read/consumed on the transport worker's acknowledgement-dispatch thread.
    """

    def __init__(
        self,
        *,
        max_retained_frames: int,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_retained_frames <= 0:
            raise ValueError("max_retained_frames must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self._max_retained_frames = max_retained_frames
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # (source_id, frame_number) -> (jpeg_bytes, expiry).
        self._entries: "OrderedDict[Tuple[int, int], Tuple[bytes, float]]" = OrderedDict()

    def put(self, source_id: int, frame_number: int, jpeg_bytes: bytes) -> None:
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            key = (source_id, frame_number)
            self._entries[key] = (jpeg_bytes, now + self._ttl_seconds)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_retained_frames:
                self._entries.popitem(last=False)

    def pop(self, source_id: int, frame_number: int) -> Optional[bytes]:
        """Remove and return the cached JPEG bytes for ``frame_number``, or ``None`` if
        absent/expired. Used on ``accepted``/``suppressed``/``rejected`` outcomes alike — accepted
        pops (and sends) the bytes; suppressed/rejected pops (and discards) them (FS-08 §4.4)."""
        with self._lock:
            self._expire_locked(self._clock())
            entry = self._entries.pop((source_id, frame_number), None)
            return entry[0] if entry is not None else None

    def drop(self, source_id: int, frame_number: int) -> None:
        with self._lock:
            self._entries.pop((source_id, frame_number), None)

    def clear(self) -> None:
        """Release every retained entry (shutdown, task item: "shutdown releases all retained
        cache entries")."""
        with self._lock:
            self._entries.clear()

    def _expire_locked(self, now: float) -> None:
        expired = [key for key, (_, expiry) in self._entries.items() if expiry <= now]
        for key in expired:
            del self._entries[key]

    def __len__(self) -> int:
        with self._lock:
            self._expire_locked(self._clock())
            return len(self._entries)


class SnapshotAcknowledgementHandler:
    """Wires an Agent acknowledgement (FS-08 §4.4) to the cached JPEG bytes it refers to (IP-10
    T-136). Intended as the ``on_acknowledgement`` callback passed to
    :class:`~deepstream_bridge.transport.TransportWorker`; runs on the transport's reader thread.

    Rules, all from FS-08 §4.4 (binding):

    * Unknown/expired ``messageId`` (never a candidate, or already resolved by a prior
      acknowledgement for the same ``messageId``) -> ignored silently, nothing sent.
    * ``suppressed``/``rejected`` -> the cached JPEG bytes are dropped, nothing sent.
    * ``accepted`` without ``snapshotRequired`` -> treated the same as suppressed/rejected (dropped,
      nothing sent) — the Agent is the sole authority on whether a snapshot is even wanted.
    * ``accepted`` with ``snapshotRequired`` -> the cached bytes are popped and handed to
      ``send_snapshot(event_id, jpeg_bytes)``.
    * A duplicate acknowledgement for an already-handled ``messageId`` is a no-op (idempotent) —
      guaranteed by :meth:`CandidateFrameTracker.resolve_and_release` removing the mapping on first
      lookup, so a second lookup for the same ``messageId`` always finds nothing.

    Never raises — a malformed acknowledgement (missing/wrong-typed fields) is treated the same as
    an unknown ``messageId``.
    """

    def __init__(
        self,
        *,
        candidate_tracker: CandidateFrameTracker,
        snapshot_cache: SnapshotCandidateCache,
        send_snapshot: SendSnapshot,
    ) -> None:
        self._candidate_tracker = candidate_tracker
        self._snapshot_cache = snapshot_cache
        self._send_snapshot = send_snapshot

    def __call__(self, message: dict) -> None:
        message_id = message.get("messageId")
        if not isinstance(message_id, str):
            return

        resolved = self._candidate_tracker.resolve_and_release(message_id)
        if resolved is None:
            return  # unknown/expired/already-handled messageId (FS-08 §4.4) — ignored, not an error

        # Source-qualified: the messageId resolves to *which camera* as well as which frame, so a
        # second camera presenting the same frame number can never satisfy this acknowledgement.
        source_id, frame_number = resolved

        outcome = message.get("outcome")
        if outcome != "accepted" or not message.get("snapshotRequired"):
            self._snapshot_cache.drop(source_id, frame_number)
            return

        event_id = message.get("eventId")
        jpeg_bytes = self._snapshot_cache.pop(source_id, frame_number)
        if not isinstance(event_id, str) or jpeg_bytes is None:
            return

        self._send_snapshot(event_id, jpeg_bytes)
