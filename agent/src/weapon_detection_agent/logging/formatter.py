"""Newline-delimited JSON log formatter with built-in redaction (IP-02 §15, §6 of the task).

Each record becomes one JSON object with a stable core — ``timestamp`` (UTC, ISO-8601), ``level``,
``logger``, ``message`` — plus any structured ``extra`` fields the caller attached (``event``,
``component``, ``operation``, ``device_id``, ``branch_id``, ``camera_id``, ``context``, …) and, when
present, a safe ``exception`` object. Every field passes through the shared :class:`Redactor` before
serialization, and non-JSON-serializable values are coerced to a redacted string rather than raising
a second logging failure.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import traceback
from typing import Any

from weapon_detection_agent.logging.redaction import Redactor

# LogRecord attributes that are framework internals, not caller-supplied context. Everything else in
# a record's __dict__ is treated as a structured "extra" field and included in the output. Derived
# from a real record instance so it tracks the running Python version, plus the two attributes the
# formatting machinery adds later.
_RESERVED_RECORD_ATTRS = set(logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


class JsonLogFormatter(logging.Formatter):
    """Formats a :class:`logging.LogRecord` as a single redacted JSON line."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self._format_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Caller-supplied structured fields (via `extra=`), excluding private/dunder names.
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[key] = value

        # Redact the whole payload in one pass: key-based redaction for sensitive field names,
        # value-based for known literals / Bearer tokens / SecretStr, recursively.
        redacted: dict[str, Any] = self._redactor.redact(payload)

        if record.exc_info:
            redacted["exception"] = self._format_exception(record.exc_info)

        return json.dumps(redacted, default=self._safe_default)

    def _format_timestamp(self, created: float) -> str:
        # UTC, ISO-8601 with an explicit +00:00 offset — unambiguous and machine-parseable.
        return _dt.datetime.fromtimestamp(created, tz=_dt.timezone.utc).isoformat()

    def _format_exception(self, exc_info: Any) -> dict[str, Any]:
        """A redaction-safe structured exception: type, redacted message, redacted traceback.

        Standard traceback formatting does not include local variable values, so the only place a
        secret can appear is the exception's own message line — which the redactor scrubs — while
        the exception type and stack frames are preserved as useful, non-sensitive troubleshooting.
        """
        exc_type, exc_value, exc_tb = exc_info
        stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        return {
            "type": exc_type.__name__ if exc_type is not None else None,
            "message": self._redactor.redact_text(str(exc_value))
            if exc_value is not None
            else None,
            "traceback": self._redactor.redact_text(stack),
        }

    def _safe_default(self, obj: object) -> str:
        # Reached only for values json cannot serialize. Coerce to a redacted string so an odd
        # context object cannot crash logging or leak a secret through its repr.
        return self._redactor.redact_text(repr(obj))
