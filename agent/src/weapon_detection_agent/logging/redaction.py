"""Central sensitive-value redaction (IP-02 §15, ARCH-001 §15.6; FS-02 §11).

Redaction is *structural*, not a matter of every call site remembering: one :class:`Redactor` is
built when logging is configured and applied to every record by the formatter, so a value can only
reach the log after passing through here. It redacts by two independent means:

* **Key-based** — a mapping key whose name is (or clearly contains) a credential term has its whole
  value replaced, whatever that value is. Case and separators are ignored, and an explicit allowlist
  keeps harmless metadata (``secret_status``, ``token_count``, ``credential_identifier``) intact.
* **Value-based** — any known secret literal registered with the redactor, any ``Bearer <token>``
  span, and any ``pydantic`` ``SecretStr`` is replaced wherever it appears, including inside a
  message string or an exception's text — regardless of the key it sits under.

Redaction never mutates its input: it returns new containers/strings and leaves the caller's objects
untouched.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence, Set
from typing import Any

from pydantic import SecretStr

# The single replacement marker used everywhere a value is removed.
REDACTED = "[REDACTED]"

# Credential-bearing key names, normalized (lower-cased, separators removed). A key is sensitive if,
# after the same normalization, it equals or contains one of these — see `is_sensitive_key`.
_SENSITIVE_KEY_TERMS = frozenset(
    {
        "activationkey",
        "sharedsecret",
        "authorization",
        "proxyauthorization",
        "password",
        "passwd",
        "secret",
        "token",
        "accesstoken",
        "refreshtoken",
        "jwt",
        "apikey",
        "credential",
        "cookie",
        "setcookie",
    }
)

# Keys that *contain* a sensitive term but are non-sensitive metadata, so must never be redacted
# (IP-02 §7). Normalized the same way as keys under test.
_HARMLESS_KEY_ALLOWLIST = frozenset(
    {
        "secretstatus",
        "tokencount",
        "credentialidentifier",
    }
)

# "Bearer <token>" anywhere in a string (covers "Authorization: Bearer ..." too). The scheme word is
# kept — that it was a bearer credential is useful, non-sensitive context — and only the token is
# replaced.
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+")


def _normalize_key(key: object) -> str:
    """Lower-case a key and strip everything but letters and digits, so ``Activation-Key``,
    ``activation_key`` and ``activationKey`` all compare equal."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    """Whether a mapping key names a credential (case- and separator-insensitive).

    Exact and containment matches against :data:`_SENSITIVE_KEY_TERMS` both count, except for the
    explicit harmless-metadata allowlist (IP-02 §7).
    """
    normalized = _normalize_key(key)
    if normalized in _HARMLESS_KEY_ALLOWLIST:
        return False
    if normalized in _SENSITIVE_KEY_TERMS:
        return True
    return any(term in normalized for term in _SENSITIVE_KEY_TERMS)


class Redactor:
    """Removes sensitive values from arbitrary log payloads without mutating the input.

    Its own representation never reveals the registered secrets — only how many there are — so the
    redactor itself cannot become the leak (IP-02 §11).
    """

    def __init__(self, sensitive_values: Sequence[str | SecretStr] | None = None) -> None:
        # Coerce SecretStr to its value and drop empties; a blank "secret" would match everywhere.
        cleaned: list[str] = []
        for value in sensitive_values or ():
            literal = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
            if literal:
                cleaned.append(literal)
        # Longest first, so a secret that contains a shorter one is masked before its substring.
        self._secret_literals: tuple[str, ...] = tuple(sorted(cleaned, key=len, reverse=True))

    def __repr__(self) -> str:
        return f"Redactor(sensitive_values=<{len(self._secret_literals)} redacted>)"

    def redact(self, value: Any) -> Any:
        """Return a redacted copy of ``value``, recursing through mappings and collections.

        The original is never modified: new dicts/lists/tuples/sets and new strings are returned.
        """
        if isinstance(value, SecretStr):
            return REDACTED
        if isinstance(value, Mapping):
            return {
                key: (REDACTED if is_sensitive_key(key) else self.redact(item))
                for key, item in value.items()
            }
        if isinstance(value, str):
            return self._redact_string(value)
        # Ordered before the generic Sequence/Set checks so str/bytes are not treated as sequences.
        if isinstance(value, bytes):
            return value
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return {self.redact(item) for item in value}
        if isinstance(value, (Sequence, Set)):
            return [self.redact(item) for item in value]
        return value

    def redact_text(self, text: str) -> str:
        """Redact a plain string (known literals and Bearer tokens). Public for exception text."""
        return self._redact_string(text)

    def _redact_string(self, text: str) -> str:
        redacted = text
        for literal in self._secret_literals:
            if literal in redacted:
                redacted = redacted.replace(literal, REDACTED)
        return _BEARER_RE.sub(r"\1 " + REDACTED, redacted)
