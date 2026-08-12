"""Value objects for the Device Camera configuration contract (FS-11 §3/§6, IP-13 T-229).

Mirrors :mod:`weapon_detection_agent.sync.models`'s shape: frozen dataclasses, no behavior beyond
construction, no I/O, no validation (that is
:mod:`weapon_detection_agent.configuration.validation`'s job). Nothing here ever holds a Device
secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID


@dataclass(frozen=True)
class DeviceCameraConfig:
    """One Camera's pipeline-relevant configuration, exactly as the Backend returns it (FS-11 §3).

    ``name`` is carried only as display metadata (FS-11 §2) — no pipeline-identity code path may
    ever read it; only ``camera_id`` is the authoritative identity.

    ``output_path`` (FS-11 §11, FS-12 §2.1) is the relative RTSP mount this Camera's *annotated*
    stream is published on by the Bridge — now ``cameras/front-entrance`` rather than
    ``cameras/2613b331-...``. The Backend derives it from the administrator-defined ``camera_key``,
    so it is stable across a rename and across a ``stream_url`` change alike; it is an output
    identity, never a label and never the input URI.

    ``camera_key`` (FS-12 §2) is that administrator-defined *public* identifier. It is carried for
    diagnostics and contract fidelity only: ``output_path`` remains the authoritative value the
    Bridge acts on, and ``camera_id`` remains the sole detection identity. Nothing in this Agent may
    derive ``camera_id`` from ``camera_key``, or ``camera_key`` from ``name``.
    """

    camera_id: UUID
    camera_key: str
    name: str
    stream_url: str
    enabled: bool
    source_order: int
    output_path: str


@dataclass(frozen=True)
class DeviceConfiguration:
    """The full configuration payload the Agent applies (FS-11 §3/§4/§6).

    ``cameras`` is expected already-ordered by ``source_order`` (the Backend's own contract); this
    type does not re-sort — :mod:`weapon_detection_agent.configuration.validation` is responsible
    for rejecting a response that violates that ordering/uniqueness contract before it ever reaches
    here.
    """

    schema_version: int
    configuration_version: str
    device_id: UUID
    branch_id: UUID
    generated_at_utc: datetime
    cameras: tuple[DeviceCameraConfig, ...] = field(default_factory=tuple)


class ConfigurationFailureReason(str, Enum):
    """Why a configuration fetch produced no configuration at all (FS-11 §5 step 7)."""

    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True)
class ConfigurationFetchFailure:
    """A whole-fetch failure — no configuration was obtained (FS-11 §5 step 7).

    Distinct from a validation failure (:exc:`~weapon_detection_agent.configuration.validation.
    ConfigurationValidationError`): this means the Backend round trip itself did not produce a
    usable payload, not that a payload was obtained and rejected.
    """

    reason: ConfigurationFailureReason
    status_code: int | None = None
