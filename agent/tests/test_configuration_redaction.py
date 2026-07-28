"""Configuration-error redaction tests (IP-07 T-90, task item 10).

``load_settings``/``_describe`` already build every ``ConfigurationError`` message from field names
and pydantic's own rule text only — never from the provided value (IP-02 §6, ARCH-001 §15.6). This
module proves that structural guarantee empirically with concrete, credential/URL-shaped inputs,
including a fabricated credential-bearing URL, rather than relying solely on reading the
implementation.
"""

from __future__ import annotations

import pytest

from weapon_detection_agent.config.settings import ConfigurationError, load_settings

# A fabricated, credential-bearing URL — never a real device/backend credential.
_CREDENTIAL_BEARING_URL = "ftp://backend-admin:Sup3rSecretPassw0rd!@203.0.113.7/api"
_SECRET_FRAGMENT = "Sup3rSecretPassw0rd"


def test_invalid_backend_url_error_never_echoes_the_provided_value() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(backend_base_url=_CREDENTIAL_BEARING_URL)

    message = str(excinfo.value)
    assert _SECRET_FRAGMENT not in message
    assert _CREDENTIAL_BEARING_URL not in message
    assert "WDA_BACKEND_BASE_URL" in message  # still names the offending field


def test_activation_key_never_appears_in_any_validation_error_message() -> None:
    """A second, unrelated field failure (an invalid log level) while a real-shaped Activation Key
    is also supplied — proves the key never leaks through an error about a *different* field."""
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(
            backend_base_url="http://backend.example.invalid:8080",
            activation_key="deviceKeyId123.Sup3rSecretPassw0rd!",  # noqa: S106
            log_level="NOT_A_REAL_LEVEL",
        )

    message = str(excinfo.value)
    assert _SECRET_FRAGMENT not in message
    assert "deviceKeyId123" not in message


def test_incompatible_detection_configuration_error_never_echoes_executable_path_value() -> None:
    """The T-90 cross-field preflight error (settings.py) names the *variables*
    (WDA_DETECTION_EVENTS_ENABLED, WDA_DEEPSTREAM_EXECUTABLE_PATH) but must never be tempted to echo
    an operator-supplied *value* for either — this stays true even for a path someone mistakenly
    populated with URL-shaped, credential-looking text."""
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(
            backend_base_url="http://backend.example.invalid:8080",
            deepstream_enabled=True,
            detection_events_enabled=True,
            deepstream_executable_path="/usr/bin/deepstream-app",
        )

    message = str(excinfo.value)
    assert "WDA_DETECTION_EVENTS_ENABLED" in message
    assert "WDA_DEEPSTREAM_EXECUTABLE_PATH" in message
