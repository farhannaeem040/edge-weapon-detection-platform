"""Uvicorn entry point for the Jetson Agent (IP-02 T-39, §5, ADR-010).

``weapon_detection_agent.main:app`` is the ASGI application Uvicorn serves. The application is built
by :func:`weapon_detection_agent.app.create_app`, which attaches the T-39 startup lifespan — so all
of the Agent's real work (settings, provisioning, logging, SQLite, activation) runs when the server
enters the lifespan, **not** at import. Importing this module therefore does no I/O, opens no
socket, configures no logging, and performs no activation; it only builds the ``FastAPI`` object.

**Single Uvicorn worker (ADR-010).** The Agent owns process-local singleton responsibilities (device
identity, and later DeepStream supervision), so it must run under exactly **one** worker. The
provided :func:`run` helper pins ``workers=1``; the documented launch command does the same:

    uvicorn weapon_detection_agent.main:app --host 0.0.0.0 --port 8000 --workers 1

Multiple workers are never configured. Enforcing this in the systemd unit is T-41's job; this module
neither infers nor depends on any Uvicorn CLI internals, and never calls ``uvicorn.run`` at import.
"""

from weapon_detection_agent.app import create_app

# The control-plane application object Uvicorn serves. Constructing it runs no startup work.
app = create_app()


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
