"""Unit tests for the snapshot candidate rendezvous (IP-10 T-133/T-135/T-136, FS-08 §3/§9/§4.4).

Pure Python, no Gst/pyds — same offline-testability discipline as ``test_probe.py``/
``test_transport.py``.

Covers both legitimate arrival orderings deliberately, not just the "JPEG always arrives first"
happy path a real Jetson deployment proved was the only one this module used to support:

    JPEG arrives -> acknowledgement arrives -> exactly one snapshot sent   (§ "JPEG-first")
    acknowledgement arrives -> JPEG arrives -> exactly one snapshot sent   (§ "ACK-first")
"""

from __future__ import annotations

from deepstream_bridge.snapshot import SnapshotAcknowledgementHandler, SnapshotRendezvous


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _rendezvous(**kwargs) -> SnapshotRendezvous:
    kwargs.setdefault("max_retained", 8)
    kwargs.setdefault("ttl_seconds", 5.0)
    return SnapshotRendezvous(**kwargs)


# --- record_detection / consume_candidate (valve-gating side) -------------------------------------


def test_record_detection_marks_frame_as_candidate() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.consume_candidate(0, 42) is True


def test_consume_candidate_is_one_shot() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.consume_candidate(0, 42) is True
    assert r.consume_candidate(0, 42) is False


def test_unknown_frame_is_never_a_candidate() -> None:
    r = _rendezvous()
    assert r.consume_candidate(0, 999) is False


def test_candidate_frame_set_has_a_hard_max() -> None:
    r = _rendezvous(max_retained=2, ttl_seconds=60.0)
    r.record_detection(0, "msg-1", 1)
    r.record_detection(0, "msg-2", 2)
    r.record_detection(0, "msg-3", 3)

    assert len(r) == 2
    assert r.consume_candidate(0, 1) is False  # evicted (oldest)
    assert r.consume_candidate(0, 2) is True
    assert r.consume_candidate(0, 3) is True


def test_max_retained_must_be_positive() -> None:
    try:
        SnapshotRendezvous(max_retained=0, ttl_seconds=1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


# --- Ordering A: JPEG arrives, then acknowledgement arrives ----------------------------------------


def test_jpeg_then_accepted_ack_completes_and_returns_snapshot() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    waiting = r.submit_jpeg(0, 42, b"\xff\xd8fake-jpeg")
    assert waiting is None  # ack hasn't arrived yet — still waiting, not lost

    completed = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    assert completed == ("evt-1", b"\xff\xd8fake-jpeg")


def test_jpeg_then_ack_removes_candidate_so_no_second_completion() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_jpeg(0, 42, b"jpeg")
    r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)

    assert len(r) == 0
    # A second JPEG for the same (source, frame) after completion must be ignored, not resurrect it.
    assert r.submit_jpeg(0, 42, b"jpeg-again") is None


# --- Ordering B: acknowledgement arrives, then JPEG arrives (the race that was broken) -------------


def test_accepted_ack_then_jpeg_completes_and_returns_snapshot() -> None:
    """This is the exact ordering that was broken: the old implementation destructively popped the
    message_id->frame mapping on the acknowledgement's first lookup, regardless of whether a JPEG
    was cached yet, so a JPEG arriving after an early acknowledgement was silently orphaned."""
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    waiting = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    assert waiting is None  # jpeg hasn't arrived yet — still waiting, not lost

    completed = r.submit_jpeg(0, 42, b"\xff\xd8fake-jpeg")
    assert completed == ("evt-1", b"\xff\xd8fake-jpeg")


def test_ack_then_jpeg_removes_candidate_so_no_second_completion() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    r.submit_jpeg(0, 42, b"jpeg")

    assert len(r) == 0
    assert (
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True) is None
    )


# --- Duplicates --------------------------------------------------------------------------------


def test_duplicate_ack_before_jpeg_still_completes_exactly_once() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert (
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True) is None
    )
    assert (
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True) is None
    )
    assert r.submit_jpeg(0, 42, b"jpeg") == ("evt-1", b"jpeg")


def test_duplicate_ack_after_completion_is_a_safe_no_op() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_jpeg(0, 42, b"jpeg")
    first = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    second = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)

    assert first == ("evt-1", b"jpeg")
    assert second is None


def test_duplicate_jpeg_before_ack_still_completes_exactly_once() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.submit_jpeg(0, 42, b"jpeg-1") is None
    assert r.submit_jpeg(0, 42, b"jpeg-2") is None  # overwrites, still waiting for ack
    completed = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    assert completed == ("evt-1", b"jpeg-2")  # the latest bytes, sent exactly once


def test_duplicate_jpeg_after_completion_is_a_safe_no_op() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
    first = r.submit_jpeg(0, 42, b"jpeg")
    second = r.submit_jpeg(0, 42, b"jpeg-again")

    assert first == ("evt-1", b"jpeg")
    assert second is None


# --- Suppressed / rejected, both orderings ----------------------------------------------------


def test_suppressed_ack_before_jpeg_discards_candidate() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert (
        r.submit_ack("msg-1", outcome="suppressed", event_id=None, snapshot_required=False) is None
    )
    # JPEG arrives later for an already-terminal candidate — ignored safely, not recreated.
    assert r.submit_jpeg(0, 42, b"jpeg") is None
    assert len(r) == 0


def test_jpeg_before_suppressed_ack_discards_candidate() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.submit_jpeg(0, 42, b"jpeg") is None
    assert (
        r.submit_ack("msg-1", outcome="suppressed", event_id=None, snapshot_required=False) is None
    )
    assert len(r) == 0


def test_rejected_ack_before_jpeg_discards_candidate() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.submit_ack("msg-1", outcome="rejected", event_id=None, snapshot_required=False) is None
    assert r.submit_jpeg(0, 42, b"jpeg") is None


def test_jpeg_before_rejected_ack_discards_candidate() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    assert r.submit_jpeg(0, 42, b"jpeg") is None
    assert r.submit_ack("msg-1", outcome="rejected", event_id=None, snapshot_required=False) is None


def test_accepted_without_snapshot_required_discards_like_suppressed() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_jpeg(0, 42, b"jpeg")

    completed = r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=False)
    assert completed is None
    assert len(r) == 0


# --- Unknown / mismatched keys -------------------------------------------------------------------


def test_unknown_message_id_ack_is_safe() -> None:
    r = _rendezvous()
    assert (
        r.submit_ack("never-seen", outcome="accepted", event_id="evt-1", snapshot_required=True)
        is None
    )


def test_unknown_source_frame_jpeg_is_safe() -> None:
    r = _rendezvous()
    assert r.submit_jpeg(0, 999, b"jpeg") is None


def test_source_mismatch_never_collides() -> None:
    """Two cameras can present the same frame_number simultaneously — a JPEG for source1 must never
    satisfy source0's candidate, and vice versa."""
    r = _rendezvous()
    r.record_detection(0, "msg-src0", 42)
    r.record_detection(1, "msg-src1", 42)

    # JPEG tagged with the wrong source must not complete the other source's candidate.
    assert (
        r.submit_jpeg(1, 42, b"jpeg-for-source1") is None
    )  # source1 still waiting for its own ack
    assert (
        r.submit_jpeg(0, 42, b"jpeg-for-source0") is None
    )  # source0 still waiting for its own ack

    completed0 = r.submit_ack(
        "msg-src0", outcome="accepted", event_id="evt-0", snapshot_required=True
    )
    completed1 = r.submit_ack(
        "msg-src1", outcome="accepted", event_id="evt-1", snapshot_required=True
    )
    assert completed0 == ("evt-0", b"jpeg-for-source0")
    assert completed1 == ("evt-1", b"jpeg-for-source1")


# --- Expiry --------------------------------------------------------------------------------------


def test_expiry_with_jpeg_only_cleans_state() -> None:
    clock = _FakeClock()
    r = _rendezvous(ttl_seconds=1.0, clock=clock)
    r.record_detection(0, "msg-1", 42)
    r.submit_jpeg(0, 42, b"jpeg")  # ack never arrives

    clock.advance(1.5)

    assert len(r) == 0
    assert (
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True) is None
    )


def test_expiry_with_ack_only_cleans_state() -> None:
    clock = _FakeClock()
    r = _rendezvous(ttl_seconds=1.0, clock=clock)
    r.record_detection(0, "msg-1", 42)
    r.submit_ack(
        "msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True
    )  # jpeg never arrives

    clock.advance(1.5)

    assert len(r) == 0
    assert r.submit_jpeg(0, 42, b"jpeg") is None


def test_expiry_removes_both_indexes() -> None:
    clock = _FakeClock()
    r = _rendezvous(ttl_seconds=1.0, clock=clock)
    r.record_detection(0, "msg-1", 42)

    clock.advance(1.5)

    assert r.consume_candidate(0, 42) is False
    assert (
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True) is None
    )


def test_completion_removes_all_indexes() -> None:
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)
    r.submit_jpeg(0, 42, b"jpeg")
    r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)

    assert len(r) == 0
    assert r.consume_candidate(0, 42) is False


def test_clear_releases_every_retained_candidate() -> None:
    r = _rendezvous(ttl_seconds=60.0)
    r.record_detection(0, "msg-1", 1)
    r.record_detection(0, "msg-2", 2)

    r.clear()

    assert len(r) == 0
    assert r.consume_candidate(0, 1) is False
    assert r.consume_candidate(0, 2) is False


# --- Concurrency: ACK and JPEG arriving "simultaneously" (interleaved calls) -----------------------


def test_concurrent_style_interleaved_ack_and_jpeg_emit_exactly_once() -> None:
    """Simulates ACK/JPEG arriving on different threads with no guaranteed order by interleaving
    calls without any sleep — both `submit_*` calls are individually atomic (single lock acquisition
    each), so whichever call observes both halves present is the one that completes."""
    r = _rendezvous()
    r.record_detection(0, "msg-1", 42)

    results = [
        r.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True),
        r.submit_jpeg(0, 42, b"jpeg"),
    ]
    completions = [x for x in results if x is not None]
    assert completions == [("evt-1", b"jpeg")]


# --- SnapshotAcknowledgementHandler (IP-10 T-136, FS-08 §4.4) --------------------------------------


def _handler():
    rendezvous = _rendezvous(ttl_seconds=60.0)
    sent: list[tuple] = []
    handler = SnapshotAcknowledgementHandler(
        rendezvous=rendezvous,
        send_snapshot=lambda event_id, jpeg_bytes: sent.append((event_id, jpeg_bytes)),
    )
    return rendezvous, sent, handler


def test_handler_sends_when_jpeg_already_cached() -> None:
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")

    handler(
        {"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True}
    )

    assert sent == [("evt-1", b"jpeg-bytes")]


def test_handler_sends_when_jpeg_arrives_after_ack() -> None:
    """The handler itself only drives the ack half; completion when the JPEG arrives later happens
    via pipeline.py calling `submit_jpeg` directly and sending on its own — this test proves the
    handler's ack-first call correctly leaves the candidate waiting rather than losing it."""
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)

    handler(
        {"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True}
    )
    assert sent == []  # jpeg not cached yet — must not have been lost

    completed = rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")
    assert completed == ("evt-1", b"jpeg-bytes")


def test_handler_suppressed_releases_without_sending() -> None:
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")

    handler({"messageId": "msg-1", "outcome": "suppressed", "snapshotRequired": False})

    assert sent == []


def test_handler_rejected_releases_without_sending() -> None:
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")

    handler(
        {
            "messageId": "msg-1",
            "outcome": "rejected",
            "snapshotRequired": False,
            "errorCode": "UNKNOWN_CLASS",
        }
    )

    assert sent == []


def test_handler_accepted_without_snapshot_required_does_not_send() -> None:
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")

    handler(
        {"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": False}
    )

    assert sent == []


def test_handler_unknown_message_id_is_ignored_safely() -> None:
    _rendezvous_obj, sent, handler = _handler()

    handler(
        {"messageId": "never-seen", "outcome": "accepted", "snapshotRequired": True}
    )  # no raise

    assert sent == []


def test_handler_duplicate_acknowledgement_does_not_send_twice() -> None:
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")
    message = {
        "messageId": "msg-1",
        "outcome": "accepted",
        "eventId": "evt-1",
        "snapshotRequired": True,
    }

    handler(message)
    handler(message)

    assert sent == [("evt-1", b"jpeg-bytes")]


def test_handler_missing_ack_leaves_entry_to_expire_via_ttl() -> None:
    clock = _FakeClock()
    rendezvous = _rendezvous(ttl_seconds=1.0, clock=clock)
    rendezvous.record_detection(0, "msg-1", 42)
    rendezvous.submit_jpeg(0, 42, b"jpeg-bytes")

    clock.advance(2.0)

    assert (
        rendezvous.submit_ack("msg-1", outcome="accepted", event_id="evt-1", snapshot_required=True)
        is None
    )


def test_handler_malformed_acknowledgement_missing_message_id_is_ignored() -> None:
    _rendezvous_obj, sent, handler = _handler()

    handler({"outcome": "accepted", "snapshotRequired": True})  # no messageId at all

    assert sent == []


def test_handler_accepted_missing_cached_jpeg_does_not_send() -> None:
    """Candidate frame recorded and message correlated, but the appsink never actually produced a
    cached JPEG (e.g. capture failure) — the handler must not send a bogus/empty snapshot, and the
    candidate must remain available in case a JPEG shows up moments later."""
    rendezvous, sent, handler = _handler()
    rendezvous.record_detection(0, "msg-1", 42)
    # deliberately never submit_jpeg(...)

    handler(
        {"messageId": "msg-1", "outcome": "accepted", "eventId": "evt-1", "snapshotRequired": True}
    )

    assert sent == []
    # Still waiting — a JPEG arriving now must complete it (this is exactly the fixed race).
    assert rendezvous.submit_jpeg(0, 42, b"late-jpeg") == ("evt-1", b"late-jpeg")
