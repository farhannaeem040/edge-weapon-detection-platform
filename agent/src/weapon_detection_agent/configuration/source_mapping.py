"""Generation-scoped ``source_id`` → immutable ``Camera.CameraId`` mapping (FS-11 §9, IP-13 T-237).

The Bridge's wire protocol reports only a numeric ``source_id`` (IP-07, unchanged by this feature).
The Agent owns translating that into the Backend's immutable Camera identity, keyed by an applied-
configuration *generation*: each time
:class:`~weapon_detection_agent.configuration.coordinator.DeviceConfigurationCoordinator` applies a
new configuration, it builds a fresh mapping and assigns it the next generation number. Exactly one
generation is ever "current" at a time — the one belonging to the currently-running Bridge process
— which is what makes a detection race-safe across a Bridge restart (FS-11 §9's "stop old Bridge →
wait for exit → install new mapping → start new Bridge" ordering, enforced by the coordinator, not
here).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from weapon_detection_agent.configuration.models import DeviceConfiguration


@dataclass(frozen=True)
class SourceGeneration:
    """One immutable ``{source_id: CameraId}`` mapping, tagged with its generation number."""

    generation: int
    source_to_camera_id: dict[int, UUID]

    def resolve(self, source_id: int) -> UUID | None:
        """Return the ``CameraId`` for ``source_id`` in this generation, or ``None`` if unknown.

        An unknown ``source_id`` is a safe, expected outcome (e.g. a stale detection from a Bridge
        that has already been superseded) — never an exception (FS-11 §9: "an unknown source_id is
        rejected safely").
        """
        return self.source_to_camera_id.get(source_id)


class SourceGenerationTracker:
    """Holds the single currently-applied :class:`SourceGeneration` and issues the next one.

    Not thread-safe by locking — the coordinator is the sole writer, on its own single-flight apply
    path, and ``current`` is read by the detection-ingest path from the same asyncio event loop, so
    no lock is needed for correctness under asyncio's cooperative scheduling.
    """

    def __init__(self) -> None:
        self._next_generation = 1
        self._current: SourceGeneration | None = None

    @property
    def current(self) -> SourceGeneration | None:
        """The generation belonging to the currently-running Bridge, or ``None`` before the first
        configuration has ever been applied."""
        return self._current

    def install(self, configuration: DeviceConfiguration) -> SourceGeneration:
        """Build and install a new generation from ``configuration``'s enabled cameras.

        Must only be called after the previous Bridge process (if any) has fully stopped and been
        reaped — the caller (the coordinator) owns that ordering guarantee (FS-11 §9).
        """
        mapping = {
            camera.source_order: camera.camera_id
            for camera in configuration.cameras
            if camera.enabled
        }
        generation = SourceGeneration(generation=self._next_generation, source_to_camera_id=mapping)
        self._next_generation += 1
        self._current = generation
        return generation
