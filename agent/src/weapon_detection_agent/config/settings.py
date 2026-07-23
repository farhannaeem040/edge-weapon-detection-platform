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

import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The environment-variable prefix for every Agent setting (IP-02 §6).
ENV_PREFIX = "WDA_"

# The URL schemes the Backend base URL may use. Trusted-LAN HTTP is the prototype posture
# (ADR-002/CON-005); HTTPS is permitted so a future hardened deployment needs no code change here.
ALLOWED_URL_SCHEMES = ("http", "https")

# The logging levels the Agent accepts for WDA_LOG_LEVEL. The logging foundation (T-33) reuses this
# same set and normalizer via `normalize_log_level` so there is exactly one definition of a valid
# level across the settings model and the logging configuration (IP-02 §10).
VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

# The restart policies WDA_DEEPSTREAM_RESTART_POLICY accepts. Deliberately a closed set of one
# value for Phase 1 (IP-06 T-71) — auto-restart-on-crash is a later-phase decision; an unexpected
# DeepStream exit is detected and logged (T-73) but never auto-restarted, so a real failure loop is
# never masked.
VALID_DEEPSTREAM_RESTART_POLICIES = frozenset({"none"})

# The safe-name pattern shared by WDA_DEEPSTREAM_MODEL_PROFILE and deploy-engine.sh's own
# profile-name validation (IP-06 T-70/T-71) — lowercase alphanumeric, optionally hyphenated, never
# starting with a hyphen. This is a naming convention only: DeepStreamProcessManager never reads
# this field (T-72's
# genericness requirement) — it exists for observability and future phases only.
_PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def normalize_log_level(value: str) -> str:
    """Normalize a logging level name to canonical upper case, rejecting unknown names.

    Shared by :class:`AgentSettings` and the logging configuration so both accept exactly the same
    levels. Raises ``ValueError`` for anything outside :data:`VALID_LOG_LEVELS`.
    """
    normalized = value.strip().upper()
    if normalized not in VALID_LOG_LEVELS:
        raise ValueError(f"must be one of {', '.join(sorted(VALID_LOG_LEVELS))}")
    return normalized


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

    # How often the Agent re-validates its stored credentials against the Backend while operational
    # (IP-05 T-56, §6). Integer seconds, must be positive. The per-request timeout reuses
    # http_timeout_seconds. This is a detect-only validation cadence, never an activation retry —
    # activation stays one-shot (IP-02 §14).
    credential_validation_interval_seconds: int = Field(default=30, gt=0)

    # Logging level name; consumed by the logging foundation (T-33).
    log_level: str = "INFO"

    # --- DeepStream process supervision (IP-06 T-71). DeepStreamProcessManager reads only six of
    # these (never deepstream_enabled or deepstream_model_profile — see each field's own comment).
    # None of these fields — including the executable and config paths — are checked for existence
    # here; settings loading performs no filesystem I/O (same rule as root_path above).

    # The rollout kill switch for this feature. Defaults to False (disabled): a fresh deployment
    # never launches DeepStream until an operator deliberately opts in, even on the real Jetson.
    # Read only by main.py's composition wiring, never by DeepStreamProcessManager itself — when
    # False, no DeepStreamProcessManager is even constructed, so no component is registered with the
    # supervisor at all (a disabled feature is the same as if it did not exist, not merely "off").
    deepstream_enabled: bool = False

    # The deepstream-app binary. Absolute path, existence checked only when DeepStreamProcessManager
    # actually starts it (T-73) — not here, so a CI/dev machine without DeepStream installed can
    # still construct valid settings.
    deepstream_executable_path: Path = Path("/usr/bin/deepstream-app")

    # The single, profile-agnostic DeepStream application config DeepStreamProcessManager launches
    # with `-c <this path>`. Which model actually runs is decided entirely by this file's own
    # content (assembled at deployment time by deploy-engine.sh/install.sh) — never by Agent code.
    deepstream_config_path: Path = Path(
        "/opt/weapon-detection/config/deepstream/deepstream-app.txt"
    )

    # The explicit `cwd=` passed to the DeepStream subprocess — never inherited from the Agent's own
    # ambient working directory.
    deepstream_working_directory: Path = Path("/opt/weapon-detection")

    # Graceful-stop budget in seconds: SIGTERM, then wait up to this long before SIGKILL (T-72).
    # Kept shorter than the systemd unit's own TimeoutStopSec=20 so the Agent's own stop completes
    # with margin inside systemd's budget.
    deepstream_stop_timeout_seconds: float = Field(default=10.0, gt=0)

    # Closed to "none" for Phase 1 (see VALID_DEEPSTREAM_RESTART_POLICIES above).
    deepstream_restart_policy: str = "none"

    # Where DeepStream's own stdout/stderr are captured (T-73) — a distinct file from the Agent's
    # own structured JSON log; DeepStream's output format is foreign and kept separate on purpose.
    deepstream_log_path: Path = Path("/opt/weapon-detection/logs/deepstream/deepstream.log")

    # Which model profile is nominally active (IP-06 T-71 amendment). Observability/future-phase use
    # only (e.g. a later metadata-extraction phase needing a profile's labels file) — deliberately
    # never read by DeepStreamProcessManager, which only ever sees deepstream_config_path.
    deepstream_model_profile: str = "yolov4-fp16"

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
        return normalize_log_level(value)

    @field_validator("deepstream_restart_policy")
    @classmethod
    def _validate_deepstream_restart_policy(cls, value: str) -> str:
        """Restrict the restart policy to the closed Phase-1 set (IP-06 T-71)."""
        if value not in VALID_DEEPSTREAM_RESTART_POLICIES:
            allowed = ", ".join(sorted(VALID_DEEPSTREAM_RESTART_POLICIES))
            raise ValueError(f"must be one of {allowed}")
        return value

    @field_validator("deepstream_model_profile")
    @classmethod
    def _validate_deepstream_model_profile(cls, value: str) -> str:
        """Restrict the profile name to a safe, filesystem-path-safe pattern (IP-06 T-70/T-71).

        The same pattern deploy-engine.sh enforces before building a filesystem path from a
        profile name. This field is never used to build a path in the Agent itself
        (DeepStreamProcessManager never reads it), but the same discipline is applied here so the
        value can never become unsafe if a future phase does start deriving a path from it.
        """
        if not _PROFILE_NAME_PATTERN.match(value):
            raise ValueError(
                "must be lowercase alphanumeric, optionally hyphenated (e.g. 'yolov4-fp16')"
            )
        return value


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
