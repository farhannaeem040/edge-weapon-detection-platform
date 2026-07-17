"""Unit tests for the Agent's bootstrap settings (IP-02 T-32, §6, §16.1).

Every test isolates the environment: an autouse fixture strips every ``WDA_`` variable so a test
never depends on (or is polluted by) the developer's real machine configuration. No test opens a
socket, touches the filesystem, creates a ``.env`` file, or starts a server.
"""

from __future__ import annotations

import builtins
import importlib
import socket

import pytest
from pydantic import ValidationError

from weapon_detection_agent.config.settings import (
    ConfigurationError,
    load_settings,
)

_WDA_VARS = (
    "WDA_BACKEND_BASE_URL",
    "WDA_ACTIVATION_KEY",
    "WDA_ROOT_PATH",
    "WDA_HTTP_TIMEOUT_SECONDS",
    "WDA_LOG_LEVEL",
)

VALID_URL = "http://localhost:5230"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Remove any WDA_ variable inherited from the real environment so each test is deterministic.
    for name in _WDA_VARS:
        monkeypatch.delenv(name, raising=False)


# --- 1. Valid URL loads ------------------------------------------------------------------------


def test_valid_backend_url_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)

    settings = load_settings()

    assert settings.backend_base_url == VALID_URL


def test_https_scheme_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "https://server.local:8443")

    assert load_settings().backend_base_url == "https://server.local:8443"


# --- 2-4. Missing / empty / whitespace-only required value -------------------------------------


def test_missing_backend_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    # The error names the missing variable so it is actionable, without guessing an address.
    assert "WDA_BACKEND_BASE_URL" in str(excinfo.value)


def test_empty_backend_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "")

    with pytest.raises(ConfigurationError):
        load_settings()


def test_whitespace_only_backend_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "   ")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "WDA_BACKEND_BASE_URL" in str(excinfo.value)


# --- 5-7. Malformed / unsupported-scheme / hostless URL ----------------------------------------


def test_malformed_url_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "not-a-valid-url")

    with pytest.raises(ConfigurationError):
        load_settings()


def test_unsupported_scheme_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "ftp://server.local")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "scheme" in str(excinfo.value).lower()


def test_url_without_host_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "http:///no-host-path")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    assert "host" in str(excinfo.value).lower()


def test_surrounding_whitespace_is_trimmed_not_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", f"  {VALID_URL}  ")

    # Trimming surrounding whitespace is the only change applied; the URL is otherwise preserved.
    assert load_settings().backend_base_url == VALID_URL


# --- 8-9. Explicit constructor override and its precedence -------------------------------------


def test_explicit_constructor_value_works() -> None:
    # No environment variable is set; the explicit value alone satisfies the required setting.
    settings = load_settings(backend_base_url="http://explicit:1234")

    assert settings.backend_base_url == "http://explicit:1234"


def test_constructor_value_takes_precedence_over_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "http://from-env:1")

    settings = load_settings(backend_base_url="http://from-override:2")

    # pydantic-settings source order: constructor > environment > defaults (IP-02 §6).
    assert settings.backend_base_url == "http://from-override:2"


# --- 10. Optional defaults ---------------------------------------------------------------------


def test_optional_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)

    settings = load_settings()

    assert settings.root_path == Path("/opt/weapon-detection")
    assert settings.http_timeout_seconds == 10.0
    assert settings.log_level == "INFO"
    assert settings.activation_key is None


def test_optional_values_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)
    monkeypatch.setenv("WDA_ROOT_PATH", "/tmp/wda-test-root")
    monkeypatch.setenv("WDA_HTTP_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("WDA_LOG_LEVEL", "debug")

    settings = load_settings()

    assert settings.root_path == Path("/tmp/wda-test-root")
    assert settings.http_timeout_seconds == 3.5
    # The level name is accepted case-insensitively and stored upper-cased.
    assert settings.log_level == "DEBUG"


def test_invalid_log_level_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)
    monkeypatch.setenv("WDA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigurationError):
        load_settings()


def test_non_positive_timeout_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)
    monkeypatch.setenv("WDA_HTTP_TIMEOUT_SECONDS", "0")

    with pytest.raises(ConfigurationError):
        load_settings()


# --- 11. Immutability --------------------------------------------------------------------------


def test_settings_are_immutable() -> None:
    settings = load_settings(backend_base_url=VALID_URL)

    # The model is frozen: reassigning any field is rejected rather than silently mutating config.
    with pytest.raises(ValidationError):
        settings.backend_base_url = "http://mutated:9"  # type: ignore[misc]


# --- 12. Importing the app requires no configuration -------------------------------------------


def test_importing_main_requires_no_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _WDA_VARS:
        monkeypatch.delenv(name, raising=False)

    # Importing the FastAPI app must not read or require settings — merely importing loads nothing.
    module = importlib.reload(importlib.import_module("weapon_detection_agent.main"))

    assert module.app.title == "Weapon Detection Agent"


# --- 13. Loading performs no network or filesystem I/O -----------------------------------------


def test_loading_performs_no_network_or_filesystem_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)

    def _forbidden_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("settings loading must not open a socket")

    def _forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("settings loading must not open a file")

    monkeypatch.setattr(socket, "socket", _forbidden_socket)
    monkeypatch.setattr(builtins, "open", _forbidden_open)

    # If loading tried any network or file access, the stubs above would turn it into a failure.
    settings = load_settings()

    assert settings.backend_base_url == VALID_URL


# --- 14. No secret or unrelated value leaks through validation/representation -------------------


def test_activation_key_loads_from_env_as_a_redacted_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", VALID_URL)
    monkeypatch.setenv("WDA_ACTIVATION_KEY", "keyid.secretvalue")

    settings = load_settings()

    assert settings.activation_key is not None
    # The value is retrievable for later use, but never shown by repr/str.
    assert settings.activation_key.get_secret_value() == "keyid.secretvalue"
    assert "secretvalue" not in repr(settings)
    assert "secretvalue" not in str(settings)


def test_validation_error_does_not_expose_secret_or_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WDA_ACTIVATION_KEY", "keyid.SUPERSECRETVALUE")
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "ftp://rejected-host")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    # The rejected configuration must name the variable and rule, but leak no provided value.
    assert "SUPERSECRETVALUE" not in message
    assert "rejected-host" not in message
    assert "WDA_BACKEND_BASE_URL" in message
