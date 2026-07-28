"""Unit tests for the in-memory duplicate-suppression cooldown tracker (IP-07 T-84, FS-05 §6).

All timing is driven by a fake, test-controlled monotonic clock — no ``time.sleep`` and no
wall-clock dependency anywhere in this file.
"""

from __future__ import annotations

import pytest

from weapon_detection_agent.detection.cooldown import CooldownDecision, DetectionCooldownTracker


class FakeClock:
    """A monotonic-clock stand-in whose value only ever advances when a test tells it to."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _tracker(cooldown_seconds: float, clock: FakeClock) -> DetectionCooldownTracker:
    return DetectionCooldownTracker(cooldown_seconds=cooldown_seconds, monotonic_clock=clock)


def test_first_detection_is_accepted() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)

    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_same_key_within_cooldown_is_suppressed() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(4.999)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.SUPPRESS


def test_exact_boundary_is_accepted() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(5.0)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_after_boundary_is_accepted() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(5.001)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_suppressed_detection_does_not_move_the_acceptance_window() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(2.0)
    suppressed = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert suppressed is CooldownDecision.SUPPRESS

    # Still measured from the first accept (t=0), not the suppressed attempt (t=2.0): at t=4.9
    # elapsed-since-accept is 4.9 < 5.0, so this must still suppress.
    clock.advance(2.9)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert decision is CooldownDecision.SUPPRESS


def test_accepted_detection_starts_a_new_cooldown_window() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(5.0)
    reaccepted = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert reaccepted is CooldownDecision.ACCEPT

    clock.advance(4.999)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert decision is CooldownDecision.SUPPRESS


def test_different_classes_are_independent() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="knife")

    assert decision is CooldownDecision.ACCEPT


def test_different_cameras_are_independent() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    decision = tracker.evaluate(device_id="device1", camera_id="camera2", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_evaluate_has_no_tracking_id_parameter() -> None:
    """T-93 (tracker-removal evaluation) requirement: the cooldown key must be exactly
    (device_id, camera_id, class_name) and never a DeepStream tracking ID — structurally enforced
    by ``evaluate``'s signature accepting no such parameter, so a bridge that omits nvtracker
    entirely (no tracking ID ever produced) cannot change cooldown behaviour."""
    import inspect

    params = set(inspect.signature(DetectionCooldownTracker.evaluate).parameters)
    assert params == {"self", "device_id", "camera_id", "class_name"}


def test_repeated_same_class_detections_cooldown_identically_without_tracking_id() -> None:
    """Simulates what a tracker-disabled Bridge produces: many raw per-frame detections of the same
    class with no tracking ID at all (the caller never has one to pass, tracker enabled or not) —
    cooldown suppression behaves exactly the same as the tracker-enabled case, proving tracker
    presence/absence cannot affect accepted-event cadence."""
    clock = FakeClock()
    tracker = _tracker(5.0, clock)

    decisions = []
    for _ in range(20):
        decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
        decisions.append(decision)
        clock.advance(0.2)  # ~5 fps of raw detections, no tracking ID involved at any point

    assert decisions.count(CooldownDecision.ACCEPT) == 1
    assert decisions[0] is CooldownDecision.ACCEPT


def test_different_devices_are_independent() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    decision = tracker.evaluate(device_id="device2", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_zero_second_cooldown_accepts_every_detection() -> None:
    clock = FakeClock()
    tracker = _tracker(0.0, clock)

    first = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    second = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert first is CooldownDecision.ACCEPT
    assert second is CooldownDecision.ACCEPT


def test_negative_cooldown_is_rejected() -> None:
    with pytest.raises(ValueError, match="cooldown_seconds"):
        DetectionCooldownTracker(cooldown_seconds=-1.0, monotonic_clock=FakeClock())


def test_fractional_cooldown_boundary_is_respected() -> None:
    clock = FakeClock()
    tracker = _tracker(0.5, clock)
    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(0.49)
    suppressed = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert suppressed is CooldownDecision.SUPPRESS

    clock.advance(0.01)
    accepted = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    assert accepted is CooldownDecision.ACCEPT


def test_new_tracker_has_no_previous_state() -> None:
    clock = FakeClock(start=100.0)
    tracker = _tracker(5.0, clock)

    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


def test_clock_is_called_for_each_evaluation_not_wall_clock() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)

    tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")
    clock.advance(1000.0)
    decision = tracker.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.ACCEPT


@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_id": "", "camera_id": "camera1", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "camera1", "class_name": ""},
        {"device_id": "  ", "camera_id": "camera1", "class_name": "gun"},
    ],
)
def test_blank_identity_fields_are_rejected(kwargs: dict[str, str]) -> None:
    tracker = _tracker(5.0, FakeClock())

    with pytest.raises(ValueError):
        tracker.evaluate(**kwargs)


# --- IP-07 T-86 amendment: decide()/commit() split ----------------------------------------------


def test_decide_does_not_record_anything() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)

    first = tracker.decide(device_id="device1", camera_id="camera1", class_name="gun")
    second = tracker.decide(device_id="device1", camera_id="camera1", class_name="gun")

    # Calling decide() repeatedly never suppresses — only commit() starts the window.
    assert first is CooldownDecision.ACCEPT
    assert second is CooldownDecision.ACCEPT


def test_commit_without_prior_decide_starts_the_window() -> None:
    clock = FakeClock()
    tracker = _tracker(5.0, clock)

    tracker.commit(device_id="device1", camera_id="camera1", class_name="gun")
    clock.advance(1.0)
    decision = tracker.decide(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision is CooldownDecision.SUPPRESS


def test_decide_then_commit_matches_evaluate() -> None:
    clock_a = FakeClock()
    tracker_a = _tracker(5.0, clock_a)
    clock_b = FakeClock()
    tracker_b = _tracker(5.0, clock_b)

    decision = tracker_a.decide(device_id="device1", camera_id="camera1", class_name="gun")
    tracker_a.commit(device_id="device1", camera_id="camera1", class_name="gun")
    evaluated = tracker_b.evaluate(device_id="device1", camera_id="camera1", class_name="gun")

    assert decision == evaluated

    clock_a.advance(4.999)
    clock_b.advance(4.999)
    assert tracker_a.decide(
        device_id="device1", camera_id="camera1", class_name="gun"
    ) == tracker_b.evaluate(device_id="device1", camera_id="camera1", class_name="gun")


def test_decide_without_commit_never_suppresses_a_later_decision() -> None:
    """The exact scenario the T-86 amendment exists for: a failed persist must not
    phantom-suppress."""
    clock = FakeClock()
    tracker = _tracker(5.0, clock)
    tracker.commit(device_id="device1", camera_id="camera1", class_name="gun")

    clock.advance(5.0)
    # A second detection is decided ACCEPT (cooldown has elapsed) but its "persistence" fails, so
    # the caller never calls commit() for it.
    decision = tracker.decide(device_id="device1", camera_id="camera1", class_name="gun")
    assert decision is CooldownDecision.ACCEPT

    clock.advance(0.001)
    # A third, genuinely new detection must still be evaluated against the *last successful commit*
    # (t=0), not against the uncommitted decide() above — so at t=5.001 it is still within the
    # original 5-second window measured from t=0 and must suppress.
    still_within_original_window = tracker.decide(
        device_id="device1", camera_id="camera1", class_name="gun"
    )
    assert still_within_original_window is CooldownDecision.ACCEPT  # t=5.001 - 0 = 5.001 >= 5.0

    # Now prove no phantom window was started: committing the third decision and checking a
    # near-immediate follow-up still suppresses, exactly as if the failed one never happened.
    tracker.commit(device_id="device1", camera_id="camera1", class_name="gun")
    clock.advance(0.001)
    assert (
        tracker.decide(device_id="device1", camera_id="camera1", class_name="gun")
        is CooldownDecision.SUPPRESS
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_id": "", "camera_id": "camera1", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "camera1", "class_name": ""},
    ],
)
def test_decide_rejects_blank_identity_fields(kwargs: dict[str, str]) -> None:
    tracker = _tracker(5.0, FakeClock())

    with pytest.raises(ValueError):
        tracker.decide(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"device_id": "", "camera_id": "camera1", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "", "class_name": "gun"},
        {"device_id": "device1", "camera_id": "camera1", "class_name": ""},
    ],
)
def test_commit_rejects_blank_identity_fields(kwargs: dict[str, str]) -> None:
    tracker = _tracker(5.0, FakeClock())

    with pytest.raises(ValueError):
        tracker.commit(**kwargs)
