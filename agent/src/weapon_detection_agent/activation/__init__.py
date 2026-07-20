"""Backend activation for the Jetson Agent (IP-02 T-37, §11).

This subpackage is the Agent's Backend Sync Client (ARCH-001 §10.2) for the one endpoint this
milestone calls — ``POST /api/v1/activate``. It contains the HTTP client
(:class:`BackendActivationClient`), the typed :class:`ActivationResult`, and the safe error
hierarchy. It performs exactly one request per call and never retries (the activation key is
one-time; IP-02 §14 as amended).

It does **not** decide whether to activate, read the key from env/file, persist identity, compare
Device IDs, or wire into startup — those belong to T-38/T-39. Importing this subpackage performs no
I/O and constructs no HTTP client.
"""

from weapon_detection_agent.activation.backend_client import (
    ACTIVATE_PATH,
    BackendActivationClient,
    build_activate_url,
)
from weapon_detection_agent.activation.errors import (
    ActivationClientError,
    ActivationRejectedError,
    ActivationServerError,
    ActivationTimeoutError,
    ActivationTransportError,
    InvalidActivationResponseError,
)
from weapon_detection_agent.activation.models import ActivationResult

__all__ = [
    "ACTIVATE_PATH",
    "ActivationClientError",
    "ActivationRejectedError",
    "ActivationResult",
    "ActivationServerError",
    "ActivationTimeoutError",
    "ActivationTransportError",
    "BackendActivationClient",
    "InvalidActivationResponseError",
    "build_activate_url",
]
