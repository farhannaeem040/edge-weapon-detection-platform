"""Validated, immutable Agent bootstrap settings (IP-02 T-32, §6).

This module is the single place the Agent's own startup configuration is defined, loaded, and
validated. Every value comes from a ``WDA_``-prefixed environment variable (or an explicit
constructor override in tests); no other module reads ``os.environ`` directly (IP-02 §6). The
resulting object is frozen, so configuration cannot drift after startup.

Scope note (IP-02 §6 / §6.1): this task implements the *environment* source for every §6 setting,
including the Activation Key via ``WDA_ACTIVATION_KEY``. IP-02 §6.1 also allows the Activation Key
to come from a ``<root>/config/activation-key`` file with the environment taking precedence; that
file fallback needs the resolved filesystem root (T-34) and reads a file, and loading settings here
must perform **no** filesystem I/O — so the file source is deferred to the task that owns the
filesystem layout and the activation workflow. The environment half of the precedence is in place.

Security: the Activation Key is held as a ``SecretStr`` so it never appears in ``repr``/``str`` or
in a validation error, and ``load_settings`` builds its error message from field names only — never
from provided values — so neither the key nor any other configured value can leak through a
configuration failure (IP-02 §6, ARCH-001 §15.6).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The environment-variable prefix for every Agent setting (IP-02 §6).
ENV_PREFIX = "WDA_"

# The URL schemes the Backend base URL may use. Trusted-LAN HTTP is the prototype posture
# (ADR-002/CON-005); HTTPS is permitted so a future hardened deployment needs no code change here.
ALLOWED_URL_SCHEMES = ("http", "https")

# The logging levels the Agent accepts for WDA_LOG_LEVEL. The logging *foundation* that consumes
# this is T-33; validating the name here keeps a bad value from surfacing only later.
_VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


class ConfigurationError(RuntimeError):
    """A clear, actionable configuration failure.

    Raised by :func:`load_settings` when required configuration is missing or invalid. Its message
    names the offending ``WDA_`` variable(s) and the rule each broke, and is built from field names
    only — never from the provided values — so no Activation Key, URL, or other configured value is
    ever echoed back through it (IP-02 §6; ARCH-001 §15.6).
    """


class AgentSettings(BaseSettings):
    """The Agent's immutable bootstrap configuration (IP-02 §6).

    Constructed from ``WDA_``-prefixed environment variables, or from explicit keyword arguments
    which take precedence over the environment (the pydantic-settings default source order:
    constructor > environment > declared defaults). No ``.env`` file is read — provisioning is
    out-of-band (IP-02 §6, ASM-006) — and no network, filesystem, or database access occurs at any
    point in construction. The model is frozen, so it cannot be mutated after validation.
    """

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        frozen=True,
        extra="ignore",
        # No `.env` provisioning: env_file is intentionally left unset (IP-02 §6). Only real
        # environment variables and explicit overrides are sources.
    )

    # Required. The Backend's base URL for later API calls, e.g. "http://server-host:5230".
    # Validated for scheme/host below; never contacted here (IP-02 §6, §11).
    backend_base_url: str

    # The complete plaintext Activation Key (keyId.secret), when provisioned via the environment.
    # Optional at the settings level: whether a key is *required* is a startup-state decision made
    # later (T-39), not a property of the configuration model. Held as SecretStr so it never leaks
    # into logs, reprs, or errors (IP-02 §6, §15).
    activation_key: SecretStr | None = None

    # The Agent's filesystem root. Overridable only so tests and workstation runs avoid /opt
    # (IP-02 §8.2). This is a pure path value; nothing is created or accessed here (T-34 owns that).
    root_path: Path = Path("/opt/weapon-detection")

    # Activation-request timeout in seconds (IP-02 §14). Must be positive.
    http_timeout_seconds: float = Field(default=10.0, gt=0)

    # Logging level name; consumed by the logging foundation (T-33).
    log_level: str = "INFO"

    @field_validator("backend_base_url")
    @classmethod
    def _validate_backend_base_url(cls, value: str) -> str:
        """Validate the Backend base URL without contacting it or rewriting it.

        Checks presence, non-blankness, syntactic validity, an allowed scheme, and a present host.
        The value is only stripped of surrounding whitespace; its scheme, host, port, and path are
        preserved exactly — no path is appended and no normalization is applied (IP-02 §11; the plan
        defines no normalization).
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")

        parts = urlsplit(stripped)
        if parts.scheme not in ALLOWED_URL_SCHEMES:
            raise ValueError(f"must use one of the {', '.join(ALLOWED_URL_SCHEMES)} schemes")
        if not parts.hostname:
            raise ValueError("must include a host")

        return stripped

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Accept a standard logging level name, case-insensitively, stored upper-cased."""
        normalized = value.strip().upper()
        if normalized not in _VALID_LOG_LEVELS:
            raise ValueError(f"must be one of {', '.join(sorted(_VALID_LOG_LEVELS))}")
        return normalized


def load_settings(**overrides: object) -> AgentSettings:
    """Load and validate the Agent settings, failing fast on missing/invalid configuration.

    With no arguments, every value is read from the ``WDA_`` environment. Keyword overrides (used by
    application code and tests) take precedence over the environment. On any validation failure this
    raises :class:`ConfigurationError` with a message naming the offending ``WDA_`` variable(s) and
    the rule each broke — and never the provided value, so no secret or URL leaks (IP-02 §6).

    Importing this module runs none of this; loading is explicit, so merely importing the package —
    or the FastAPI app — stays side-effect free (IP-02 §6).
    """
    try:
        return AgentSettings(**overrides)  # type: ignore[arg-type]
    except ValidationError as error:
        raise ConfigurationError(_describe(error)) from error


def _describe(error: ValidationError) -> str:
    """Render a validation error using field names only, never the provided values.

    Each pydantic error carries a ``loc`` (the field) and a ``msg`` (the rule). This maps the field
    to its ``WDA_`` variable name and joins it with the rule text, deliberately ignoring the
    ``input`` member so no configured value — an Activation Key above all — appears in the message.
    """
    problems: list[str] = []
    for err in error.errors():
        field = err["loc"][0] if err["loc"] else ""
        env_name = f"{ENV_PREFIX}{str(field).upper()}" if field else "configuration"
        message = err["msg"]
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        problems.append(f"{env_name} {message}")

    joined = "; ".join(problems)
    return f"Invalid Agent configuration: {joined}."
