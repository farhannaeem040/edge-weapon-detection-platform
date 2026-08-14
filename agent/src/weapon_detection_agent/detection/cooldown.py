"""In-memory duplicate-suppression cooldown tracker (IP-07 T-84, FS-05 §6; amended T-86).

:class:`DetectionCooldownTracker` decides whether a detection for a given identity key
(``device_id``, ``camera_id``, ``class_name``) should be accepted or suppressed, based purely on
elapsed time since that key's last *durably accepted* detection. It performs no I/O, no persistence,
and no bounding-box comparison of any kind (FS-05 §6 — the key is identity-only) — it is
deliberately not object tracking (no tracker ids, no per-object identity, no cleanup workers or
timers).

State is a single in-memory ``dict`` and is lost on process restart; this is documented, accepted
behaviour (FS-05 §6/§10), not a defect to work around here.

**Decide/commit split (T-86 amendment).** :meth:`decide` answers "would this be accepted?" without
recording anything; :meth:`commit` is what actually starts the cooldown window for a key. Splitting
them exists for exactly one reason: :class:`~weapon_detection_agent.detection.ingest_handler.
DetectionIngestHandler` must not let "accepted for cooldown" and "durably persisted" diverge — if it
called a single accept-and-record operation *before* the SQLite insert and that insert then failed,
the real event would be lost while a phantom cooldown window silently suppressed the next, genuine
detection. Calling :meth:`decide` first and :meth:`commit` only after
:class:`~weapon_detection_agent.persistence.detection_event_repository.DetectionEventRepository.
insert` succeeds closes that gap without either method knowing anything about persistence.
:meth:`evaluate` is unchanged (decide immediately followed by commit-if-accepted, in one call) for
any caller that has no such durability step to wait for.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

_CooldownKey = tuple[str, str, str]


class CooldownDecision(Enum):
    """Whether a detection should be accepted or suppressed (FS-05 §6)."""

    ACCEPT = "accept"
    SUPPRESS = "suppress"


@dataclass(frozen=True)
class _Identity:
    device_id: str
    camera_id: str
    class_name: str

    def __post_init__(self) -> None:
        for name, value in (
            ("device_id", self.device_id),
            ("camera_id", self.camera_id),
            ("class_name", self.class_name),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")

    def key(self) -> _CooldownKey:
        return (self.device_id, self.camera_id, self.class_name)


class DetectionCooldownTracker:
    """Deterministic, in-memory cooldown suppression keyed by (device id, camera id, class name).

    ``cooldown_seconds`` must be non-negative; zero means every detection is accepted (no cooldown
    at all). ``monotonic_clock`` defaults to :func:`time.monotonic` and is injectable so tests can
    advance time deterministically without sleeping — the same DI seam pattern used elsewhere in
    this codebase (e.g. :func:`~weapon_detection_agent.detection.validation.validate_detection`'s
    ``event_id_factory``).

    Boundary semantics (FS-05 §6): given ``elapsed = now - last_accepted``,
    ``elapsed >= cooldown_seconds`` means ACCEPT; ``elapsed < cooldown_seconds`` means SUPPRESS.
    Only an ACCEPT decision updates the key's last-accepted timestamp — a suppressed detection
    never extends or resets the cooldown window.
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must not be negative")

        self._cooldown_seconds = cooldown_seconds
        self._monotonic_clock = monotonic_clock
        self._last_accepted_at: dict[_CooldownKey, float] = {}

    def evaluate(
        self,
        *,
        device_id: str,
        camera_id: str,
        class_name: str,
    ) -> CooldownDecision:
        """Return the cooldown decision for this identity and record it immediately if accepted.

        Equivalent to :meth:`decide` followed by :meth:`commit` when the decision is ``ACCEPT``, in
        one call. Callers with a durability step between "accepted" and "should count toward the
        cooldown" (T-86's ingest handler) use :meth:`decide`/:meth:`commit` directly instead.
        """
        decision = self.decide(device_id=device_id, camera_id=camera_id, class_name=class_name)
        if decision is CooldownDecision.ACCEPT:
            self.commit(device_id=device_id, camera_id=camera_id, class_name=class_name)
        return decision

    def decide(
        self,
        *,
        device_id: str,
        camera_id: str,
        class_name: str,
    ) -> CooldownDecision:
        """Return the cooldown decision for this identity **without** recording anything.

        Read-only: calling this any number of times never moves the cooldown window and never
        suppresses a subsequent call for the same key. Only :meth:`commit` does that.
        """
        key = _Identity(device_id, camera_id, class_name).key()
        now = self._monotonic_clock()

        last_accepted = self._last_accepted_at.get(key)
        if last_accepted is not None and now - last_accepted < self._cooldown_seconds:
            return CooldownDecision.SUPPRESS
        return CooldownDecision.ACCEPT

    def commit(
        self,
        *,
        device_id: str,
        camera_id: str,
        class_name: str,
    ) -> None:
        """Record this identity's acceptance now, starting/restarting its cooldown window.

        Call only after a preceding :meth:`decide` returned ``ACCEPT`` **and** the detection it
        described was durably persisted — never for a ``SUPPRESS`` decision, and never for one whose
        persistence failed (T-86's binding rule: "accepted for cooldown" means "successfully
        persisted"). Uses its own fresh clock reading rather than reusing the reading `decide` took,
        so the window starts from the moment acceptance is actually finalized.
        """
        key = _Identity(device_id, camera_id, class_name).key()
        self._last_accepted_at[key] = self._monotonic_clock()
