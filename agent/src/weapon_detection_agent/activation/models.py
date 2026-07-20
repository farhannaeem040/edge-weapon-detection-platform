"""Typed activation result (IP-02 T-37, §11).

``ActivationResult`` mirrors the Backend's ``POST /api/v1/activate`` success ``data`` — exactly
``deviceId``, ``sharedSecret``, and ``branchId`` (FS-02 §10.4), and nothing else. It is a distinct
type from the persisted :class:`~weapon_detection_agent.persistence.models.DeviceIdentity`: the
activation response carries a ``branchId`` and no timestamps, whereas the stored identity carries
timestamps and no branch id, so the two have genuinely different responsibilities (T-38 maps one to
the other).

The response returns **no configuration** (OI-2): this model neither expects nor invents any
configuration field. The shared secret is a ``SecretStr`` so it is redacted in every
``repr``/``str`` and by the logging redactor, and it cannot be leaked through a generic
``asdict``/JSON dump (a ``SecretStr`` is not JSON-serializable, so a dump raises rather than leaks).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr


@dataclass(frozen=True)
class ActivationResult:
    """The validated result of a successful activation exchange.

    ``device_id`` and ``branch_id`` are public identifiers (they appear in the Dashboard and in
    request headers) and are represented normally. ``shared_secret`` is a credential and is held as
    a ``SecretStr``. All three are required and non-blank — the invariants are enforced here so a
    malformed Backend payload cannot produce a half-valid result.
    """

    device_id: str
    shared_secret: SecretStr
    branch_id: str

    def __post_init__(self) -> None:
        if not self.device_id or not self.device_id.strip():
            raise ValueError("activation result device id must not be blank")
        if not self.branch_id or not self.branch_id.strip():
            raise ValueError("activation result branch id must not be blank")
        if not self.shared_secret.get_secret_value().strip():
            raise ValueError("activation result shared secret must not be blank")

    @classmethod
    def from_response_data(cls, data: object) -> ActivationResult:
        """Build a result from the envelope's ``data`` object, validating structure and types.

        Raises ``ValueError`` — with a message naming only the offending JSON field, never a value —
        when ``data`` is not an object or a required field is missing, not a string, or blank. The
        client turns that into an :class:`InvalidActivationResponseError`.
        """
        if not isinstance(data, dict):
            raise ValueError("activation response contained no data object")

        device_id = _require_non_blank_str(data, "deviceId")
        shared_secret = _require_non_blank_str(data, "sharedSecret")
        branch_id = _require_non_blank_str(data, "branchId")
        return cls(
            device_id=device_id,
            shared_secret=SecretStr(shared_secret),
            branch_id=branch_id,
        )


def _require_non_blank_str(data: dict[Any, Any], key: str) -> str:
    """Return ``data[key]`` when it is a non-blank string; else raise a value-free ``ValueError``.

    Handles all three malformed cases at once — missing (``None``), wrong type, and blank — and
    never includes the value in the message (only the field name, which is not sensitive).
    """
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"activation response field '{key}' is missing, not a string, or blank")
    return value
