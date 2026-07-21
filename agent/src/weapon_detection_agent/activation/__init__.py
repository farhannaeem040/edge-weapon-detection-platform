"""Backend activation for the Jetson Agent (IP-02 T-37 client, T-38 orchestration).

This subpackage is the Agent's Backend Sync Client (ARCH-001 §10.2) plus the activation
orchestration. It contains:

* the HTTP client (:class:`BackendActivationClient`) — exactly one ``POST /api/v1/activate`` per
  call, never retried (the key is one-time; IP-02 §14 as amended), with the typed
  :class:`ActivationResult` and the safe client-error hierarchy (T-37);
* the Activation Key resolver (:class:`ActivationKeyResolver`, env-over-file, T-38 §6.1);
* the orchestration service (:class:`ActivationService`) that resolves the key, calls the client
  once, persists via the Device Identity repository, and cleans up a file key (T-38 §9).

Wiring the service into the FastAPI startup lifespan is **T-39**, not here. Importing this
subpackage performs no I/O and constructs no HTTP client, repository, or clock.
"""

from weapon_detection_agent.activation.backend_client import (
    ACTIVATE_PATH,
    BackendActivationClient,
    build_activate_url,
)
from weapon_detection_agent.activation.errors import (
    ActivationClientError,
    ActivationKeyCleanupError,
    ActivationKeyFileError,
    ActivationKeyMissingError,
    ActivationOutcomeAmbiguousError,
    ActivationPersistenceError,
    ActivationRejectedError,
    ActivationServerError,
    ActivationServiceError,
    ActivationTimeoutError,
    ActivationTransportError,
    DeviceIdentityMismatchError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.key_resolver import (
    ActivationKeyResolver,
    KeySource,
    ResolvedActivationKey,
)
from weapon_detection_agent.activation.models import ActivationResult
from weapon_detection_agent.activation.service import (
    ActivationOutcome,
    ActivationService,
    ActivationServiceResult,
)

__all__ = [
    "ACTIVATE_PATH",
    "ActivationClientError",
    "ActivationKeyCleanupError",
    "ActivationKeyFileError",
    "ActivationKeyMissingError",
    "ActivationKeyResolver",
    "ActivationOutcome",
    "ActivationOutcomeAmbiguousError",
    "ActivationPersistenceError",
    "ActivationRejectedError",
    "ActivationResult",
    "ActivationServerError",
    "ActivationService",
    "ActivationServiceError",
    "ActivationServiceResult",
    "ActivationTimeoutError",
    "ActivationTransportError",
    "BackendActivationClient",
    "DeviceIdentityMismatchError",
    "InvalidActivationResponseError",
    "KeySource",
    "ResolvedActivationKey",
    "build_activate_url",
]
