"""Detection-event Backend synchronization (IP-08, FS-06) — outbox delivery of already-persisted
:class:`~weapon_detection_agent.detection.models.DetectionEvent` rows to the ASP.NET Core Backend.

Mirrors :mod:`weapon_detection_agent.validation`'s package layout: :mod:`.models` holds the typed,
credential-free results; :mod:`.client` is the dedicated HTTP client
(:class:`~weapon_detection_agent.sync.client.BackendSyncClient`); :mod:`.worker` is the
``OperationalComponent`` drain loop (:class:`~weapon_detection_agent.sync.worker.
DetectionEventSyncWorker`) and its lifecycle-wiring factory.
"""

from __future__ import annotations
