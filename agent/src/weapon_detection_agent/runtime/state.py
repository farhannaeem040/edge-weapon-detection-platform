"""The immutable runtime state published after a successful startup (IP-02 T-39, §12).

``AgentRuntime`` is the small, typed object the lifespan places on ``app.state.runtime`` once — and
only once — every required startup step has succeeded. It holds the initialized components later
approved features will need (the validated settings, the resolved paths, both persistence
repositories, and the activation outcome). It deliberately carries **no** secret: the Activation Key
lives only inside the settings' ``SecretStr`` (redacted in every ``repr``/``str``), and the
activation result carries no shared secret at all. So the runtime object cannot leak a credential
through a representation, and it is never exposed through an HTTP endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from weapon_detection_agent.activation.service import ActivationServiceResult
from weapon_detection_agent.config.paths import AgentPaths
from weapon_detection_agent.config.settings import AgentSettings
from weapon_detection_agent.persistence.config_cache_repository import ConfigCacheRepository
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository

# The single attribute name the runtime is published under on ``app.state``.
RUNTIME_STATE_ATTR = "runtime"


@dataclass(frozen=True)
class AgentRuntime:
    """The successfully initialized Agent runtime (published on ``app.state.runtime``).

    Every field is safe to hold and to represent: ``settings`` redacts its Activation Key via
    ``SecretStr``; the repositories hold only a database path; ``activation`` is a secret-free
    result (outcome, public device/branch ids, timestamps). ``config_cache_repository`` is here so a
    later feature can read the cache — this milestone loads it once at startup (may be empty, OI-2)
    but stores nothing in it.
    """

    settings: AgentSettings
    paths: AgentPaths
    identity_repository: DeviceIdentityRepository
    config_cache_repository: ConfigCacheRepository
    activation: ActivationServiceResult


def get_runtime(app: FastAPI) -> AgentRuntime | None:
    """Return the published runtime, or ``None`` before startup / after shutdown.

    Accessing ``app.state.runtime`` directly raises before it is set; this helper returns ``None``
    instead, so callers can safely check whether the Agent has finished initializing.
    """
    runtime: AgentRuntime | None = getattr(app.state, RUNTIME_STATE_ATTR, None)
    return runtime
