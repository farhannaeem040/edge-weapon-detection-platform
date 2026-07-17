"""Minimal FastAPI application for the Jetson Agent (IP-02 T-31 scaffold).

This module exposes a single importable ``app`` so the scaffold is provably wired up — the package
installs, the application object is constructed, and its metadata is set — without yet doing any of
the Agent's real work.

What this deliberately does **not** contain, because it belongs to later IP-02 tasks:

* no routes or endpoints of any kind — the Agent's approved routes (ARCH-001 §14.2) all belong to
  excluded features, and no Agent health route is approved (IP-02 §2.2, OI-3);
* no lifespan/startup handler, settings loading, filesystem or SQLite access, activation, or
  Backend communication (IP-02 T-32–T-39);
* no Uvicorn server invocation — the process is launched with a single worker from the command line
  (``uvicorn weapon_detection_agent.main:app --workers 1``), per ADR-010; wiring the single-worker
  runtime and the startup lifespan is T-39's work.

Because it only constructs the ``FastAPI`` object, importing this module does no I/O, opens no
socket, and touches no platform-specific API, so it imports identically on a workstation and on the
Jetson.
"""

from fastapi import FastAPI

from weapon_detection_agent import __version__

# The control-plane application object. It carries title and version metadata only; endpoints,
# startup work, and dependencies are added by later tasks.
app = FastAPI(
    title="Weapon Detection Agent",
    version=__version__,
    description=(
        "Control-plane API for the Jetson Agent. Scaffold only (IP-02 T-31): no operational "
        "endpoints are defined yet."
    ),
)
