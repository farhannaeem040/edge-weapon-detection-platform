"""Unit tests for the snapshot candidate bookkeeping (IP-10 T-133/T-135/T-136, FS-08 §3/§9/§4.4).

Pure Python, no Gst/pyds — same offline-testability discipline as ``test_probe.py``/
``test_transport.py``.
"""

from __future__ import annotations

from deepstream_bridge.snapshot import (
    CandidateFrameTracker,
    SnapshotAcknowledgementHandler,
    SnapshotCandidateCache,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- CandidateFrameTracker -----------------------------------------------------------------------


def test_record_detection_marks_frame_as_candidate() -> None:
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    tracker.record_detection(0, "msg-1", 42)

    assert tracker.consume_candidate(0, 42) is True


def test_consume_candidate_is_one_shot() -> None:
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    tracker.record_detection(0, "msg-1", 42)

    assert tracker.consume_candidate(0, 42) is True
    assert tracker.consume_candidate(0, 42) is False


def test_unknown_frame_is_never_a_candidate() -> None:
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    assert tracker.consume_candidate(0, 999) is False


def test_resolve_and_release_returns_frame_number() -> None:
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    tracker.record_detection(0, "msg-1", 42)

    assert tracker.resolve_and_release("msg-1") == (0, 42)


def test_resolve_and_release_is_idempotent_for_duplicate_lookups() -> None:
    """Duplicate acknowledgement handling (FS-08 §4.4: "must never write a second JPEG file") relies
    on this — a second lookup for the same message_id must find nothing."""
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    tracker.record_detection(0, "msg-1", 42)

    assert tracker.resolve_and_release("msg-1") == (0, 42)
    assert tracker.resolve_and_release("msg-1") is None


def test_unknown_message_id_resolves_to_none() -> None:
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=5.0)
    assert tracker.resolve_and_release("never-seen") is None


def test_candidate_frames_expire_after_ttl() -> None:
    clock = _FakeClock()
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=1.0, clock=clock)
    tracker.record_detection(0, "msg-1", 42)

    clock.advance(1.5)

    assert tracker.consume_candidate(0, 42) is False


def test_message_correlation_expires_after_ttl() -> None:
    clock = _FakeClock()
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=1.0, clock=clock)
    tracker.record_detection(0, "msg-1", 42)

    clock.advance(1.5)

    assert tracker.resolve_and_release("msg-1") is None


def test_candidate_frame_set_has_a_hard_max() -> None:
    tracker = CandidateFrameTracker(max_retained=2, ttl_seconds=60.0)
    tracker.record_detection(0, "msg-1", 1)
    tracker.record_detection(0, "msg-2", 2)
    tracker.record_detection(0, "msg-3", 3)

    assert len(tracker) == 2
    assert tracker.consume_candidate(0, 1) is False  # evicted (oldest)
    assert tracker.consume_candidate(0, 2) is True
    assert tracker.consume_candidate(0, 3) is True


def test_message_correlation_map_has_a_hard_max() -> None:
    tracker = CandidateFrameTracker(max_retained=2, ttl_seconds=60.0)
    tracker.record_detection(0, "msg-1", 1)
    tracker.record_detection(0, "msg-2", 2)
    tracker.record_detection(0, "msg-3", 3)

    assert tracker.resolve_and_release("msg-1") is None  # evicted (oldest)
    assert tracker.resolve_and_release("msg-2") == (0, 2)


def test_max_retained_must_be_positive() -> None:
    try:
        CandidateFrameTracker(max_retained=0, ttl_seconds=1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# --- SnapshotCandidateCache -----------------------------------------------------------------------


def test_put_then_pop_returns_jpeg_bytes() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=5.0)
    cache.put(0, 42, b"\xff\xd8fake-jpeg")

    assert cache.pop(0, 42) == b"\xff\xd8fake-jpeg"


def test_pop_removes_the_entry() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=5.0)
    cache.put(0, 42, b"jpeg")

    cache.pop(0, 42)

    assert cache.pop(0, 42) is None


def test_pop_unknown_frame_returns_none() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=5.0)
    assert cache.pop(0, 999) is None


def test_drop_discards_without_returning() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=5.0)
    cache.put(0, 42, b"jpeg")

    cache.drop(0, 42)

    assert cache.pop(0, 42) is None


def test_entries_expire_after_ttl() -> None:
    clock = _FakeClock()
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=1.0, clock=clock)
    cache.put(0, 42, b"jpeg")

    clock.advance(1.5)

    assert cache.pop(0, 42) is None


def test_retained_frame_cache_has_a_hard_max() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=2, ttl_seconds=60.0)
    cache.put(0, 1, b"a")
    cache.put(0, 2, b"b")
    cache.put(0, 3, b"c")

    assert len(cache) == 2
    assert cache.pop(0, 1) is None  # evicted (oldest)
    assert cache.pop(0, 2) == b"b"
    assert cache.pop(0, 3) == b"c"


def test_clear_releases_every_retained_entry() -> None:
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=60.0)
    cache.put(0, 1, b"a")
    cache.put(0, 2, b"b")

    cache.clear()

    assert len(cache) == 0
    assert cache.pop(0, 1) is None
    assert cache.pop(0, 2) is None


# --- SnapshotAcknowledgementHandler (IP-10 T-136, FS-08 §4.4) --------------------------------------


def _handler():
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=60.0)
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=60.0)
    sent: list[tuple] = []
    handler = SnapshotAcknowledgementHandler(
        candidate_tracker=tracker,
        snapshot_cache=cache,
        send_snapshot=lambda event_id, jpeg_bytes: sent.append((event_id, jpeg_bytes)),
    )
    return tracker, cache, sent, handler


def test_accepted_with_snapshot_required_sends_cached_jpeg() -> None:
    tracker, cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")

    handler({"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True})

    assert sent == [("evt-1", b"jpeg-bytes")]
    assert cache.pop(0, 42) is None  # popped (sent), not left behind


def test_suppressed_releases_frame_without_sending() -> None:
    tracker, cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")

    handler({"messageId": "msg-1", "outcome": "suppressed", "snapshotRequired": False})

    assert sent == []
    assert cache.pop(0, 42) is None


def test_rejected_releases_frame_without_sending() -> None:
    tracker, cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")

    handler(
        {
            "messageId": "msg-1",
            "outcome": "rejected",
            "snapshotRequired": False,
            "errorCode": "UNKNOWN_CLASS",
        }
    )

    assert sent == []
    assert cache.pop(0, 42) is None


def test_accepted_without_snapshot_required_does_not_send() -> None:
    tracker, cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")

    handler({"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": False})

    assert sent == []


def test_unknown_message_id_is_ignored_safely() -> None:
    _tracker, _cache, sent, handler = _handler()

    handler({"messageId": "never-seen", "outcome": "accepted", "snapshotRequired": True})  # no raise

    assert sent == []


def test_duplicate_acknowledgement_does_not_send_twice() -> None:
    tracker, cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")
    message = {"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True}

    handler(message)
    handler(message)

    assert sent == [("evt-1", b"jpeg-bytes")]


def test_missing_ack_leaves_entry_to_expire_via_ttl() -> None:
    """No acknowledgement ever arrives: the cache/tracker entries simply age out (§9) — this test
    only proves the handler is never involved in that path (nothing to assert beyond "no crash"),
    complementing the direct TTL-expiry tests above."""
    clock = _FakeClock()
    tracker = CandidateFrameTracker(max_retained=8, ttl_seconds=1.0, clock=clock)
    cache = SnapshotCandidateCache(max_retained_frames=8, ttl_seconds=1.0, clock=clock)
    tracker.record_detection(0, "msg-1", 42)
    cache.put(0, 42, b"jpeg-bytes")

    clock.advance(2.0)

    assert tracker.resolve_and_release("msg-1") is None
    assert cache.pop(0, 42) is None


def test_malformed_acknowledgement_missing_message_id_is_ignored() -> None:
    _tracker, _cache, sent, handler = _handler()

    handler({"outcome": "accepted", "snapshotRequired": True})  # no messageId at all

    assert sent == []


def test_accepted_missing_cached_jpeg_does_not_send() -> None:
    """Candidate frame recorded and message correlated, but the appsink never actually produced a
    cached JPEG (e.g. capture failure) — the handler must not send a bogus/empty snapshot."""
    tracker, _cache, sent, handler = _handler()
    tracker.record_detection(0, "msg-1", 42)
    # deliberately never cache.put(0, ...)

    handler({"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True})

    assert sent == []
