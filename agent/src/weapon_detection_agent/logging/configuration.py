"""Deterministic logging configuration for the Agent (IP-02 §15).

One explicit call — :func:`configure_logging` — wires the Agent's package logger to emit redacted,
newline-delimited JSON to stdout and, optionally, to an explicit file. Nothing here runs at import
time: importing the package (or the FastAPI app) configures no handlers and creates no files, so a
missing environment variable can never break an import (IP-02 §12). Wiring this into the runtime
startup is T-39's job, not this task's.

Filesystem boundary (IP-02 §9): this function will write to a log file whose parent directory
already exists, but it never creates that directory or the Agent root — provisioning the
``/opt/weapon-detection/`` layout is T-34's responsibility. A file path whose parent is missing is a
clear, immediate error rather than a silent directory creation or a fallback location.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import SecretStr

from weapon_detection_agent.config.settings import normalize_log_level
from weapon_detection_agent.logging.formatter import JsonLogFormatter
from weapon_detection_agent.logging.redaction import Redactor

# The logger this configuration owns. Child loggers (e.g. "weapon_detection_agent.activation")
# propagate to it, so configuring this one configures the whole Agent tree without touching the root
# logger or any third-party logger.
DEFAULT_LOGGER_NAME = "weapon_detection_agent"

# Marks handlers this module installed, so a reconfiguration removes exactly its own handlers and
# never a handler some other code attached to the same logger.
_MANAGED_HANDLER_FLAG = "_wda_managed"


class LoggingConfigurationError(RuntimeError):
    """Raised when logging cannot be configured as requested — e.g. a log file whose parent
    directory does not exist (creating it is T-34's responsibility, not this task's)."""


def configure_logging(
    *,
    log_level: str = "INFO",
    log_file: str | Path | None = None,
    sensitive_values: Sequence[str | SecretStr] | None = None,
    logger_name: str = DEFAULT_LOGGER_NAME,
) -> None:
    """Configure the Agent's package logger deterministically and idempotently.

    Emits redacted JSON to stdout, and to ``log_file`` as well when given. ``sensitive_values`` are
    literal secrets (e.g. the Activation Key) that must be scrubbed wherever they appear. Calling
    this more than once replaces this module's own handlers rather than stacking new ones, which
    keeps it safe for test isolation and for a future reconfiguration.

    Raises :class:`LoggingConfigurationError` for a log file whose parent directory is missing, and
    ``ValueError`` (via the shared normalizer) for an unknown ``log_level``. It logs nothing itself,
    so no configuration value — secret or otherwise — is emitted during setup.
    """
    level_name = normalize_log_level(log_level)

    redactor = Redactor(sensitive_values)
    formatter = JsonLogFormatter(redactor)

    logger = logging.getLogger(logger_name)

    # Remove only the handlers we previously installed, so repeated calls do not duplicate output
    # and a foreign handler on this logger is left alone.
    for handler in [h for h in logger.handlers if getattr(h, _MANAGED_HANDLER_FLAG, False)]:
        logger.removeHandler(handler)
        handler.close()

    stdout_handler = logging.StreamHandler(sys.stdout)
    _install(logger, stdout_handler, formatter)

    if log_file is not None:
        file_handler = _build_file_handler(log_file)
        _install(logger, file_handler, formatter)

    logger.setLevel(level_name)
    # Keep Agent output on its own handlers only; do not also bubble to the root logger.
    logger.propagate = False


def _install(logger: logging.Logger, handler: logging.Handler, formatter: JsonLogFormatter) -> None:
    handler.setFormatter(formatter)
    setattr(handler, _MANAGED_HANDLER_FLAG, True)
    logger.addHandler(handler)


def _build_file_handler(log_file: str | Path) -> logging.FileHandler:
    path = Path(log_file)
    parent = path.parent
    if not parent.exists():
        # Do not create the directory (T-34 owns the filesystem layout) and do not fall back
        # elsewhere — surface the problem clearly.
        raise LoggingConfigurationError(
            f"log file directory does not exist: {parent} "
            "(creating the Agent log directory is the provisioning task's responsibility)"
        )
    # A plain FileHandler: no rotation or retention policy is committed in this task (IP-02 §24
    # defers those). The file itself is created inside the existing directory; the directory is not.
    return logging.FileHandler(path, encoding="utf-8")
