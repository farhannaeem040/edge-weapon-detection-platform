"""Uvicorn entry point for the Jetson Agent (IP-02 T-39, §5, ADR-010).

``weapon_detection_agent.main:app`` is the ASGI application Uvicorn serves. The application is built
by :func:`weapon_detection_agent.app.create_app`, which attaches the T-39 startup lifespan — so all
of the Agent's real work (settings, provisioning, logging, SQLite, activation) runs when the server
enters the lifespan, **not** at import. Importing this module therefore does no I/O, opens no
socket, configures no logging, and performs no activation; it only builds the ``FastAPI`` object.

**Single Uvicorn worker (ADR-010).** The Agent owns process-local singleton responsibilities (device
identity, and DeepStream process supervision — IP-06), so it must run under exactly **one** worker.
The provided :func:`run` helper pins ``workers=1``; the documented launch command does the same:

    uvicorn weapon_detection_agent.main:app --host 0.0.0.0 --port 8000 --workers 1

Multiple workers are never configured. Enforcing this in the systemd unit is T-41's job; this module
neither infers nor depends on any Uvicorn CLI internals, and never calls ``uvicorn.run`` at import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from weapon_detection_agent.app import create_app
from weapon_detection_agent.deepstream.process_manager import (
    default_deepstream_components_factory,
)
from weapon_detection_agent.detection.ingest_handler import default_detection_components_factory
from weapon_detection_agent.runtime.operational_components import OperationalComponent

if TYPE_CHECKING:
    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )


def _components_factory(
    settings: AgentSettings,
    paths: AgentPaths,
    identity_repository: DeviceIdentityRepository,
) -> tuple[OperationalComponent, ...]:
    """Compose every real operational component this entrypoint wires in (IP-06 T-74, IP-07 T-87).

    Runtime structure this builds (systemd supervises only the FastAPI Agent process itself; no
    second lifecycle manager is introduced — both components are plain
    :class:`~weapon_detection_agent.runtime.operational_components.OperationalComponent`\\ s the
    existing :class:`~weapon_detection_agent.runtime.operational_state_coordinator.
    OperationalStateCoordinator` starts/stops exactly as it would any other)::

        systemd -> FastAPI Agent -> DetectionIngestHandler
                                  -> DeepStreamProcessManager -> (later) DeepStream Bridge

    **Order matters.** The coordinator starts registered components in list order and stops them in
    the *reverse* of the order they actually started (``OperationalStateCoordinator``, unchanged) —
    so listing the detection ingest handler first and DeepStream second means:

    * **startup:** the ingest handler's Unix domain socket is bound and its listener is already
      accepting connections *before* DeepStream's child process (and, from IP-07 T-88 onward, the
      Bridge it eventually launches) ever starts — the Bridge can never race an absent listener;
    * **shutdown:** DeepStream is stopped first (no further detections are produced), and only then
      does the ingest handler finish/cancel its in-flight processing and remove the socket (T-86
      policy) — so no detection can be produced with nowhere for it to land.

    Each factory is independently gated (``default_detection_components_factory`` checks both
    ``deepstream_enabled`` and ``detection_events_enabled`` itself; ``default_deepstream_components_
    factory`` checks only ``deepstream_enabled``), so every combination of the two kill switches
    composes correctly here with no branching in this function.
    """
    return (
        *default_detection_components_factory(settings, paths, identity_repository),
        *default_deepstream_components_factory(settings),
    )


# The control-plane application object Uvicorn serves. Constructing it runs no startup work.
# This is the one place DeepStream supervision (IP-06 T-74) and detection-event ingestion (IP-07
# T-87) are wired in — create_app()'s own default stays empty so every other caller (tests included)
# is unaffected by either feature's existence.
app = create_app(components_factory=_components_factory)


def run() -> None:
    """Run the Agent under a single Uvicorn worker (ADR-010). Not called at import."""
    import uvicorn

    uvicorn.run(
        "weapon_detection_agent.main:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
    )


if __name__ == "__main__":
    run()
