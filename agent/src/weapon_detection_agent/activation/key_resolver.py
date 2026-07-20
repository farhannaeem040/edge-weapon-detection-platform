"""Activation Key resolution and single-use key-file cleanup (IP-02 T-38, §6.1, D-1).

Resolves the complete plaintext Activation Key from one of two approved sources, in this precedence
(IP-02 §6.1):

1. the ``WDA_ACTIVATION_KEY`` environment variable, already validated into ``AgentSettings`` and
   passed in here as an optional ``SecretStr`` — **it wins, and the file is not read**;
2. a single-line file at ``<root>/config/activation-key`` (``AgentPaths.activation_key_file``),
   read only through an explicit call.

The key is always carried as a ``SecretStr`` so it never appears in a ``repr``/``str``, log, or
error. The resolver **never** reads ``os.environ`` — the environment value arrives pre-resolved from
settings (IP-02 §6). It performs filesystem I/O only inside :meth:`resolve` and
:meth:`delete_key_file`, never at import or construction.

Whitespace rule (IP-02 §6.1 fixes "single-line file" but not the exact trimming): surrounding
whitespace — including a normal trailing newline — is stripped, and an empty or whitespace-only file
is rejected. A ``keyId.secret`` key contains no internal spaces, so stripping cannot corrupt it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import SecretStr

from weapon_detection_agent.activation.errors import (
    ActivationKeyCleanupError,
    ActivationKeyFileError,
)


class KeySource(Enum):
    """Where a resolved Activation Key came from — used to decide post-success cleanup."""

    ENVIRONMENT = "environment"
    FILE = "file"


@dataclass(frozen=True)
class ResolvedActivationKey:
    """A resolved Activation Key and its source.

    ``key`` is a ``SecretStr`` (redacted in ``repr``/``str``); ``source`` decides whether the key
    file is deleted after a successful activation (only ``FILE`` keys are — an environment key is
    never removed by the Agent, IP-02 §6.1). No plaintext is exposed by this record's repr.
    """

    key: SecretStr
    source: KeySource


class ActivationKeyResolver:
    """Resolves the Activation Key (env over file) and removes the file after a successful use.

    Construct with the optional environment key (``settings.activation_key``) and the resolved key
    file path (``AgentPaths.activation_key_file``). Construction performs no I/O; only
    :meth:`resolve` and :meth:`delete_key_file` touch the filesystem, and :meth:`delete_key_file`
    only ever targets the one configured path — never an arbitrary location.
    """

    def __init__(self, *, environment_key: SecretStr | None, key_file_path: Path) -> None:
        self._environment_key = environment_key
        self._key_file_path = key_file_path

    def resolve(self) -> ResolvedActivationKey | None:
        """Return the resolved key, or ``None`` when neither source provides one.

        The environment key wins and the file is not read. When the environment key is absent, the
        file is read if it exists; a missing file yields ``None`` (a normal restart), while an
        unreadable or empty/whitespace-only file raises :class:`ActivationKeyFileError`.
        """
        if self._environment_key is not None:
            return ResolvedActivationKey(key=self._environment_key, source=KeySource.ENVIRONMENT)

        if not self._key_file_path.exists():
            return None

        try:
            raw = self._key_file_path.read_text(encoding="utf-8")
        except OSError as exc:
            # Never include the path's contents; the path itself is not a secret.
            raise ActivationKeyFileError(
                f"the activation key file could not be read: {self._key_file_path}"
            ) from exc

        key = raw.strip()
        if not key:
            raise ActivationKeyFileError(f"the activation key file is empty: {self._key_file_path}")

        return ResolvedActivationKey(key=SecretStr(key), source=KeySource.FILE)

    def delete_key_file(self) -> None:
        """Delete the configured key file (idempotent). Raises :class:`ActivationKeyCleanupError` on
        an OS failure. Only the single configured path is ever removed; no directory is created or
        traversed, and file contents never appear in the error."""
        try:
            self._key_file_path.unlink(missing_ok=True)
        except OSError as exc:
            raise ActivationKeyCleanupError(
                f"the activation key file could not be removed: {self._key_file_path}"
            ) from exc
