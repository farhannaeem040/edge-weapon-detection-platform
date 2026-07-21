"""FastAPI application factory for the Jetson Agent (IP-02 T-39, §5).

:func:`create_app` builds the control-plane ``FastAPI`` application and attaches the T-39 startup
lifespan. Constructing the app performs **no** I/O: it loads no settings, provisions no directories,
opens no database or socket, configures no logging, and runs no activation — all of that happens
inside the lifespan when an ASGI server (or the test client's lifespan context) starts, not at
import or construction time.

The application defines **no** operational routes: no `/health`, `/status`, `/ready`, `/live`, or
`/activate`. The Agent's approved routes (ARCH-001 §14.2) belong to excluded features, and no Agent
health endpoint is approved (OI-3). FastAPI's built-in ``/docs`` and ``/openapi.json`` remain.

Dependencies (a settings loader, a clock, and a Backend client factory) are injectable so tests can
exercise startup without a real Backend, network, or ``/opt`` root; production uses the real
defaults.
"""

from __future__ import annotations

from fastapi import FastAPI

from weapon_detection_agent import __version__
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.runtime.startup import (
    BackendClientFactory,
    Clock,
    SettingsLoader,
    create_lifespan,
    default_backend_client_factory,
    default_clock,
)

_DESCRIPTION = (
    "Control-plane API for the Jetson Agent. The application performs its device activation "
    "in the startup lifespan (IP-02 T-39) and defines no operational endpoints."
)


def create_app(
    *,
    settings_loader: SettingsLoader = load_settings,
    clock: Clock = default_clock,
    backend_client_factory: BackendClientFactory = default_backend_client_factory,
) -> FastAPI:
    """Create a FastAPI application wired to the T-39 startup lifespan.

    Each call returns an independent application instance. No startup work runs until the lifespan
    is entered by an ASGI server (or a ``TestClient`` context manager).
    """
    return FastAPI(
        title="Weapon Detection Agent",
        version=__version__,
        description=_DESCRIPTION,
        lifespan=create_lifespan(
            settings_loader=settings_loader,
            clock=clock,
            backend_client_factory=backend_client_factory,
        ),
    )
