"""Snapshot candidate rendezvous (IP-10 T-133/T-135/T-136, FS-08 §3/§4.4/§9).

A single, thread-safe, TTL-expiring rendezvous between two independent, unordered arrivals for the
same detection:

* the JPEG bytes (produced by the appsink's ``new-sample`` callback, on the GStreamer streaming
  thread), and
* the Agent's acknowledgement (``accepted``/``suppressed``/``rejected`` + ``EventId``, arriving on
  the transport worker's reader thread).

Earlier versions of this module used two separate structures — a candidate/message tracker and a
JPEG-bytes cache — correlated only at acknowledgement-handling time. That worked when the JPEG always
arrived before the acknowledgement, but a real Jetson deployment proved the opposite order is also
possible: the acknowledgement's lookup destructively consumed the ``message_id`` mapping whether or
not a JPEG was present yet, so an acknowledgement that arrived first silently orphaned the JPEG that
showed up moments later (never resent, never logged, dropped only when its own TTL expired). Both
orderings are legitimate and neither may lose the snapshot:

    JPEG arrives -> acknowledgement arrives -> exactly one snapshot sent
    acknowledgement arrives -> JPEG arrives -> exactly one snapshot sent

:class:`SnapshotRendezvous` fixes this by keeping ONE candidate record per detection (keyed primarily
by ``message_id``, with a secondary ``(source_id, frame_number) -> message_id`` index for the
valve-gating probe and the appsink callback, neither of which know ``message_id`` directly) and a
single completion function, :meth:`SnapshotRendezvous._try_complete_locked`, called from both arrival
paths. Whichever side arrives second is the one that observes both halves present and completes the
candidate; whichever arrives first simply records its half and returns "still waiting" — nothing is
consumed or discarded until either completion or terminal disposal (suppressed/rejected/expired).

Pure Python, no ``Gst``/``pyds`` import at module scope (same offline-testability discipline as
``probe.py``) — the module is exercised directly on Windows dev machines via ``test_snapshot.py``.
Every public method is protected by a single internal lock; per the class docstring's own
concurrency contract, no method here ever performs I/O (disk, socket, Backend call) while holding
it — a caller that must send bytes/somewhere always does so with the lock already released, using
the ``(event_id, jpeg_bytes)`` tuple a completing call returns.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional, Tuple

_LOGGER = logging.getLogger("deepstream_bridge.snapshot")

SendSnapshot = Callable[[str, bytes], None]

# Only enough of message_id to correlate a handful of log lines by eye — never the full value (IP-10
# task item: "truncated message_id", never full secrets/identifiers in logs).
_LOGGED_ID_PREFIX_LEN = 8

# Terminal ack outcomes that discard the candidate outright (FS-08 §4.4) — anything other than
# "accepted" and truthy "snapshotRequired" is dropped, never sent.
_ACCEPTED = "accepted"


class _Candidate:
    """One detection's in-flight rendezvous state. Never exposed outside this module — every access
    happens with :class:`SnapshotRendezvous`'s lock already held."""

    __slots__ = (
        "source_id",
        "frame_number",
        "message_id",
        "expires_at",
        "valve_matched",
        "jpeg_bytes",
        "ack_outcome",
        "event_id",
        "snapshot_required",
        "completed",
    )

    def __init__(
        self, *, source_id: int, frame_number: int, message_id: str, expires_at: float
    ) -> None:
        self.source_id = source_id
        self.frame_number = frame_number
        self.message_id = message_id
        self.expires_at = expires_at
        self.valve_matched = False
        self.jpeg_bytes: Optional[bytes] = None
        self.ack_outcome: Optional[str] = None
        self.event_id: Optional[str] = None
        self.snapshot_required = False
        self.completed = False


class SnapshotRendezvous:
    """Tracks snapshot candidates from detection through valve-gating, JPEG capture, and Agent
    acknowledgement — in either arrival order — down to exactly one ``send_snapshot`` call or a safe,
    silent discard.

    ``max_retained`` bounds the number of in-flight candidates; ``ttl_seconds`` bounds how long a
    candidate survives without completing. Never raises for an already-expired/absent/unknown key —
    callers (a pad-probe callback, an appsink callback, an acknowledgement dispatch callback) must
    never fault the pipeline.
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
        # message_id -> candidate (primary index).
        self._by_message: "OrderedDict[str, _Candidate]" = OrderedDict()
        # (source_id, frame_number) -> message_id (secondary index; frame numbers alone are not
        # unique across demultiplexed cameras, so this is always source-qualified).
        self._by_frame: "OrderedDict[Tuple[int, int], str]" = OrderedDict()

    # --- Detection-side arrival (pad-probe thread) -------------------------------------------------

    def record_detection(self, source_id: int, message_id: str, frame_number: int) -> None:
        """Register a fresh candidate. Called once per raw detection (the metadata pad-probe's
        ``on_candidate``), before either the JPEG or the acknowledgement can possibly arrive."""
        with self._lock:
            now = self._clock()
            self._expire_locked(now)

            candidate = _Candidate(
                source_id=source_id,
                frame_number=frame_number,
                message_id=message_id,
                expires_at=now + self._ttl_seconds,
            )
            self._by_message[message_id] = candidate
            self._by_message.move_to_end(message_id)
            self._by_frame[(source_id, frame_number)] = message_id
            self._by_frame.move_to_end((source_id, frame_number))

            while len(self._by_message) > self._max_retained:
                _oldest_message_id, oldest = self._by_message.popitem(last=False)
                self._by_frame.pop((oldest.source_id, oldest.frame_number), None)
            while len(self._by_frame) > self._max_retained:
                self._by_frame.popitem(last=False)
            entry_count = len(self._by_message)

        _LOGGER.info(
            "snapshot_candidate_registered",
            extra={
                "message_id": message_id[:_LOGGED_ID_PREFIX_LEN],
                "source_id": source_id,
                "frame_number": frame_number,
                "cache_entry_count": entry_count,
            },
        )

    def consume_candidate(self, source_id: int, frame_number: int) -> bool:
        """Return whether ``frame_number`` is a not-yet-valve-matched, unexpired candidate, marking
        it valve-matched either way it is found — the snapshot branch's valve-gating probe calls
        this exactly once per buffer reaching that branch, and must open the valve for exactly the
        one buffer matching a real candidate. Does **not** remove the candidate: its rendezvous state
        must survive for the appsink callback's later :meth:`submit_jpeg` lookup by the same
        ``(source_id, frame_number)`` key."""
        with self._lock:
            self._expire_locked(self._clock())
            message_id = self._by_frame.get((source_id, frame_number))
            if message_id is None:
                return False
            candidate = self._by_message.get(message_id)
            if candidate is None or candidate.valve_matched:
                return False
            candidate.valve_matched = True
            return True

    # --- JPEG-side arrival (appsink "new-sample" thread) --------------------------------------------

    def submit_jpeg(
        self, source_id: int, frame_number: int, jpeg_bytes: bytes
    ) -> Optional[Tuple[str, bytes]]:
        """Record the captured JPEG bytes for ``(source_id, frame_number)`` and attempt completion.

        Returns ``(event_id, jpeg_bytes)`` if the acknowledgement had *already* arrived (this call
        completed the rendezvous — the caller must send it, with this lock already released) or
        ``None`` if the candidate is unknown/expired/already-terminal, or the acknowledgement simply
        hasn't arrived yet (in which case the bytes are now stored, and the eventual
        :meth:`submit_ack` call will find them and complete instead)."""
        message_id: Optional[str] = None
        status = "unknown_frame"
        result: Optional[Tuple[str, bytes]] = None
        with self._lock:
            self._expire_locked(self._clock())
            message_id = self._by_frame.get((source_id, frame_number))
            if message_id is not None:
                candidate = self._by_message.get(message_id)
                if candidate is None or candidate.completed:
                    status = "unknown_or_terminal"
                else:
                    candidate.jpeg_bytes = jpeg_bytes
                    result = self._try_complete_locked(candidate)
                    status = "completed" if result is not None else "waiting_for_ack"

        if status == "unknown_frame":
            _LOGGER.info(
                "snapshot_jpeg_received",
                extra={"source_id": source_id, "frame_number": frame_number, "outcome": status},
            )
        else:
            event_name = (
                "snapshot_rendezvous_completed" if result is not None else "snapshot_jpeg_received"
            )
            _LOGGER.info(
                event_name,
                extra={
                    "message_id": (message_id or "")[:_LOGGED_ID_PREFIX_LEN],
                    "source_id": source_id,
                    "frame_number": frame_number,
                    "jpeg_bytes": len(jpeg_bytes),
                    "outcome": status,
                    **({"completing_side": "jpeg"} if result is not None else {}),
                },
            )
        return result

    # --- Acknowledgement-side arrival (transport reader thread) -------------------------------------

    def submit_ack(
        self,
        message_id: str,
        *,
        outcome: Optional[str],
        event_id: Optional[str],
        snapshot_required: bool,
    ) -> Optional[Tuple[str, bytes]]:
        """Record the acknowledgement outcome for ``message_id`` and attempt completion (FS-08
        §4.4). A non-``accepted``/non-``snapshotRequired`` outcome discards the candidate outright
        (any JPEG bytes already stored are released with it) so a JPEG arriving later for the same
        key is safely ignored rather than reviving a terminal candidate.

        Returns ``(event_id, jpeg_bytes)`` if the JPEG had *already* arrived (this call completed the
        rendezvous — the caller must send it, with this lock already released), or ``None`` in every
        other case (unknown/expired/already-terminal message_id, a discarded outcome, or simply the
        JPEG hasn't arrived yet — :meth:`submit_jpeg` will find the stored outcome and complete when
        it does)."""
        status = "unknown_or_terminal"
        result: Optional[Tuple[str, bytes]] = None
        source_id = frame_number = None
        with self._lock:
            self._expire_locked(self._clock())
            candidate = self._by_message.get(message_id)
            if candidate is not None and not candidate.completed:
                source_id, frame_number = candidate.source_id, candidate.frame_number
                if outcome != _ACCEPTED or not snapshot_required:
                    self._discard_locked(candidate)
                    status = (
                        "discarded_suppressed_or_rejected"
                        if outcome != _ACCEPTED
                        else ("discarded_snapshot_not_required")
                    )
                else:
                    candidate.ack_outcome = outcome
                    candidate.event_id = event_id
                    candidate.snapshot_required = snapshot_required
                    result = self._try_complete_locked(candidate)
                    status = "completed" if result is not None else "waiting_for_jpeg"

        event_name = (
            "snapshot_rendezvous_completed" if result is not None else "snapshot_ack_received"
        )
        _LOGGER.info(
            event_name,
            extra={
                "message_id": message_id[:_LOGGED_ID_PREFIX_LEN],
                "source_id": source_id,
                "frame_number": frame_number,
                "event_id": (event_id or "")[:_LOGGED_ID_PREFIX_LEN] if event_id else None,
                "outcome": status,
                **({"completing_side": "ack"} if result is not None else {}),
            },
        )
        return result

    # --- Shared completion/discard/expiry (always called with the lock held) ------------------------

    def _try_complete_locked(self, candidate: _Candidate) -> Optional[Tuple[str, bytes]]:
        if candidate.completed:
            return None
        if candidate.ack_outcome != _ACCEPTED or not candidate.snapshot_required:
            return None
        if not isinstance(candidate.event_id, str) or candidate.jpeg_bytes is None:
            return None

        candidate.completed = True
        result = (candidate.event_id, candidate.jpeg_bytes)
        self._remove_locked(candidate)
        return result

    def _discard_locked(self, candidate: _Candidate) -> None:
        candidate.completed = True
        self._remove_locked(candidate)

    def _remove_locked(self, candidate: _Candidate) -> None:
        self._by_message.pop(candidate.message_id, None)
        self._by_frame.pop((candidate.source_id, candidate.frame_number), None)

    def _expire_locked(self, now: float) -> None:
        expired = [
            candidate
            for candidate in self._by_message.values()
            if candidate.expires_at <= now and not candidate.completed
        ]
        for candidate in expired:
            self._remove_locked(candidate)
            # Expiry is rare (bounded by TTL, never per-frame) — logging here, still under the lock,
            # is a deliberate small exception to the "no I/O while holding the lock" rule elsewhere
            # in this class, traded for not having to thread expired-candidate details back out
            # through every caller of _expire_locked.
            _LOGGER.info(
                "snapshot_candidate_expired",
                extra={
                    "message_id": candidate.message_id[:_LOGGED_ID_PREFIX_LEN],
                    "source_id": candidate.source_id,
                    "frame_number": candidate.frame_number,
                    "age_ms": int((now - (candidate.expires_at - self._ttl_seconds)) * 1000),
                    "had_jpeg": candidate.jpeg_bytes is not None,
                    "had_ack": candidate.ack_outcome is not None,
                    "ack_outcome": candidate.ack_outcome,
                },
            )

    # --- Shutdown -------------------------------------------------------------------------------

    def clear(self) -> None:
        """Release every retained candidate (shutdown, task item: "shutdown releases all retained
        cache entries")."""
        with self._lock:
            self._by_message.clear()
            self._by_frame.clear()

    def __len__(self) -> int:
        with self._lock:
            self._expire_locked(self._clock())
            return len(self._by_message)


class SnapshotAcknowledgementHandler:
    """Thin adapter from the Agent's raw acknowledgement message shape to
    :meth:`SnapshotRendezvous.submit_ack`, and from a completed rendezvous to
    ``send_snapshot(event_id, jpeg_bytes)``. Intended as the ``on_acknowledgement`` callback passed to
    :class:`~deepstream_bridge.transport.TransportWorker`; runs on the transport's reader thread.

    Never raises — a malformed acknowledgement (missing/wrong-typed fields) is treated the same as an
    unknown ``messageId``."""

    def __init__(
        self,
        *,
        rendezvous: SnapshotRendezvous,
        send_snapshot: SendSnapshot,
    ) -> None:
        self._rendezvous = rendezvous
        self._send_snapshot = send_snapshot

    def __call__(self, message: dict) -> None:
        message_id = message.get("messageId")
        if not isinstance(message_id, str):
            return

        completed = self._rendezvous.submit_ack(
            message_id,
            outcome=message.get("outcome"),
            event_id=message.get("eventId"),
            snapshot_required=bool(message.get("snapshotRequired")),
        )
        if completed is not None:
            event_id, jpeg_bytes = completed
            self._send_snapshot(event_id, jpeg_bytes)
