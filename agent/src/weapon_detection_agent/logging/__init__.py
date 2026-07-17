"""Agent logging foundation (IP-02 §15, T-33).

Structured (newline-delimited JSON) logging to stdout and an optional explicit file, with central,
structural redaction of sensitive values. Configuration is explicit via :func:`configure_logging`;
importing this package configures nothing and has no side effects.

This subpackage is named ``logging`` but never shadows the standard library: all imports here are
absolute, so ``import logging`` anywhere resolves to the stdlib module, while this package is always
addressed as ``weapon_detection_agent.logging``.
"""

from weapon_detection_agent.logging.configuration import (
    LoggingConfigurationError,
    configure_logging,
)
from weapon_detection_agent.logging.formatter import JsonLogFormatter
from weapon_detection_agent.logging.redaction import REDACTED, Redactor, is_sensitive_key

__all__ = [
    "REDACTED",
    "JsonLogFormatter",
    "LoggingConfigurationError",
    "Redactor",
    "configure_logging",
    "is_sensitive_key",
]
