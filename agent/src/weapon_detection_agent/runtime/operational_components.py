"""The operational-component contract enforced by the operational-state coordinator (IP-05 T-60).

An *operational component* is any long-lived Agent subsystem that may run only while the device
holds valid credentials — later milestones' DeepStream supervision, detection processing, alert
synchronization, live-stream handling, remote-command handling, and siren control. None of those
exist yet; T-60 defines only the smallest contract the coordinator needs so those features can plug
in unchanged, and stops/prevents them the moment the credentials are revoked
(``ReactivationRequired``).

The contract is deliberately minimal — start, stop, and a safe name — with no feature-specific
method (nothing DeepStream-shaped). ``start`` and ``stop`` are coroutines so a component can do real
async setup/teardown; the coordinator awaits each one and never fires a background task. ``name`` is
a static, human-readable identifier used only for structured logging and safe error reporting: it
must carry **no** credential material (no Device ID, secret, key, or row content).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class OperationalComponent(Protocol):
    """A startable/stoppable Agent subsystem gated on valid credentials (IP-05 T-60).

    Implementations must make ``start`` and ``stop`` safe to await in sequence: ``stop`` may be
    called on a component the coordinator believes is running, and should not raise merely because
    the component was already idle. A raise from either is treated by the coordinator as a start or
    shutdown failure and handled without unlocking the Agent.
    """

    @property
    def name(self) -> str:
        """A safe, static component identifier for logs and errors — never credential material."""
        ...

    async def start(self) -> None:
        """Begin operating. Awaited by the coordinator only while the state is ``Operational``."""
        ...

    async def stop(self) -> None:
        """Stop operating and release resources. Awaited when the Agent locks or rolls back."""
        ...


def safe_component_name(component: object) -> str:
    """Return a component's safe log/error name, falling back to its class name.

    Uses the component's ``name`` when it is a non-empty string; otherwise the type name. Both are
    static identifiers, so neither can leak a Device ID, secret, key, or database row content even
    if a component supplies no ``name``.
    """
    name = getattr(component, "name", None)
    if isinstance(name, str) and name:
        return name
    return type(component).__name__
