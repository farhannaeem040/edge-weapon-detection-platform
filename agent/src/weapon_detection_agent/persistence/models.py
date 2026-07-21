"""Immutable typed records for the local SQLite store (IP-02 T-36, §7, §10).

Each model mirrors one row of the T-35 schema exactly — same fields, same meaning — with two
deliberate refinements over the raw row:

* **Timestamps are timezone-aware UTC ``datetime`` objects**, not strings. The schema stores them as
  ISO-8601 UTC ``TEXT``; :func:`to_iso_utc` / :func:`parse_iso_utc` are the single conversion point,
  so callers work with real datetimes and the on-disk representation stays consistent.
* **The device shared secret is a ``SecretStr``**, so it is redacted in every ``repr``/``str`` and
  by the logging redactor (IP-02 §10, §15). "Protected" on the Jetson means file-permission-
  protected (D-4): the value is stored as-is inside the ``0600`` database, with no application-layer
  encryption — this model neither adds nor claims encryption.

The configuration cache carries its stored ``ConfigJson`` as **raw text**. No configuration schema
is defined in this milestone (OI-2), so nothing here parses, validates, or invents configuration
fields; a later configuration feature owns that contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import SecretStr


def to_iso_utc(value: datetime) -> str:
    """Serialize a timezone-aware datetime to ISO-8601 in UTC (the schema's storage form).

    Rejects a naive datetime rather than guessing its zone — a stored timestamp with an ambiguous
    offset would undermine the "ISO-8601 UTC" schema contract (IP-02 §7).
    """
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def parse_iso_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp back to a timezone-aware UTC datetime.

    Raises ``ValueError`` for text that is not parseable or lacks an offset; the caller turns that
    into a safe repository error (the raw text is never echoed).
    """
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp is not timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DeviceIdentity:
    """The Agent's persistent local identity (one ``DeviceIdentity`` row).

    ``device_id`` is a public identifier — it travels in a header on operational requests and is
    shown in the Dashboard (FS-02 §5.4), so it appears normally. ``shared_secret`` is a credential
    and is held as a ``SecretStr`` so it can never leak through a ``repr``, ``str``, log line, or
    exception. ``activated_at`` is set at first activation and never changes; ``last_activated_at``
    advances on each (re)activation.
    """

    device_id: str
    shared_secret: SecretStr
    activated_at: datetime
    last_activated_at: datetime


@dataclass(frozen=True)
class CachedConfiguration:
    """The last synchronized configuration (one ``ConfigCache`` row).

    ``config_json`` is the stored payload as **raw text**: this milestone defines no configuration
    schema (OI-2), so it is neither parsed nor interpreted here. It is never logged or placed in an
    error message. ``updated_at`` is a timezone-aware UTC datetime.
    """

    config_json: str
    updated_at: datetime
