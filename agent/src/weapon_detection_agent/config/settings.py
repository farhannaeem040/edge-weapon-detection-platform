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

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
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

# The reference DeepStream binary's default path — named here as a module-level constant (IP-07
# T-90) so the incompatible-configuration preflight below and the field default are provably the
# same value, never two independently-typed literals that could drift apart.
DEFAULT_DEEPSTREAM_EXECUTABLE_PATH = Path("/usr/bin/deepstream-app")

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
    deepstream_executable_path: Path = DEFAULT_DEEPSTREAM_EXECUTABLE_PATH

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
    deepstream_model_profile: str = "yolo26-fp16"

    # --- Detection event bridge (IP-07 T-81, FS-05 §9). DetectionIngestHandler reads all six of
    # these; none are read by DeepStreamProcessManager or the pyds pipeline child (which receives
    # its own copy of the socket path via its deployment wrapper script, not via AgentSettings —
    # that process has no Agent configuration at all, FS-05 §4.3).

    # The rollout kill switch for this feature, mirroring deepstream_enabled exactly. Defaults to
    # False: a fresh or freshly-updated deployment starts no Unix-socket listener and constructs no
    # DetectionIngestHandler until an operator deliberately opts in.
    detection_events_enabled: bool = False

    # Detections below this confidence are rejected (not "suppressed" — a distinct diagnostic
    # reason, FS-05 §5/§8). Bounded to the valid probability range.
    detection_min_confidence: float = Field(default=0.50, ge=0.0, le=1.0)

    # The cooldown window (FS-05 §6) for the in-memory duplicate-suppression key
    # (device id, camera id, class name), measured on a monotonic clock. Must be positive.
    detection_cooldown_seconds: float = Field(default=5.0, gt=0)

    # The bounded capacity of DetectionIngestHandler's internal queue decoupling socket reads from
    # SQLite writes (T-86). A full queue drops the newest message rather than blocking or growing
    # unbounded. Must be positive.
    detection_queue_capacity: int = Field(default=1000, gt=0)

    # The single configured camera identity attached to every accepted event (FS-05 §5) — never
    # trusted from the wire payload. Must not be blank.
    detection_camera_id: str = "camera1"

    # The Unix domain socket DetectionIngestHandler listens on and the pyds pipeline child writes
    # to (ADR-005). Lives under the runtime/ directory this feature is the first writer for (T-82).
    detection_socket_path: Path = Path("/opt/weapon-detection/runtime/detection.sock")

    # --- Detection event Backend sync (IP-08, FS-06 §10). DetectionEventSyncWorker reads all five
    # of these; the per-request timeout reuses http_timeout_seconds rather than adding a duplicate.

    # The rollout kill switch for this feature, mirroring detection_events_enabled exactly. Defaults
    # to False: a fresh or freshly-updated deployment constructs no DetectionEventSyncWorker and
    # sends no Backend traffic until an operator deliberately opts in (FS-06 §10/§11 — production
    # enablement is explicitly out of scope for this feature's own completion).
    detection_sync_enabled: bool = False

    # How long the worker sleeps when the outbox is empty (or nothing sendable is pending) before
    # checking again. Must be positive.
    detection_sync_interval_seconds: float = Field(default=1.0, gt=0)

    # The bounded batch size drained per POST /api/v1/sync/events request. Must be within the
    # closed [1, 100] range the Backend's own request-size bound (IP-08 T-97) is built around.
    detection_sync_batch_size: int = Field(default=25, ge=1, le=100)

    # The first backoff delay after a whole-batch failure (network/timeout/5xx/401/malformed
    # response, FS-06 §9). Must be positive; doubles on each consecutive failure, capped below.
    detection_sync_initial_backoff_seconds: float = Field(default=1.0, gt=0)

    # The backoff cap. Must be at least detection_sync_initial_backoff_seconds (enforced below) —
    # otherwise the "doubling" backoff would immediately be clamped below its own starting point.
    detection_sync_max_backoff_seconds: float = Field(default=60.0, gt=0)

    # --- Server-driven Device Camera configuration (FS-11 §11, IP-13 T-234).
    # DeviceConfigurationCoordinator reads all four; the Backend poll reuses http_timeout_seconds
    # rather than adding a duplicate.

    # The rollout kill switch, mirroring detection_sync_enabled exactly. Defaults False: a fresh or
    # freshly-updated deployment starts no DeviceConfigurationCoordinator and keeps the pre-FS-11
    # static deepstream-app.txt/WDA_DETECTION_CAMERA_ID pipeline unchanged until an operator
    # deliberately opts in (FS-11 §11 — production enablement is a separate, explicitly-approved
    # step).
    device_config_enabled: bool = False

    # How often the coordinator polls the Backend for a configuration change once Operational. Must
    # be positive.
    device_config_refresh_seconds: float = Field(default=30.0, gt=0)

    # The maximum enabled-Camera count this Agent will accept in one configuration (FS-11 §7) — a
    # bound against a malformed/hostile response, not a hardware claim about how many streams this
    # Jetson can actually decode.
    device_config_max_cameras: int = Field(default=8, ge=1)

    # How old a cached configuration may be before the coordinator refuses to apply it at startup
    # without a fresh Backend fetch first. 0 means no forced expiry (FS-11 §5/§15: an edge Device
    # may be offline indefinitely and must keep running its last-known-good pipeline).
    device_config_cache_max_age_seconds: int = Field(default=0, ge=0)

    # --- Snapshot evidence capture/upload (IP-10 T-146, FS-08 §13). Both kill switches default
    # False: Stage 1 of the rollout ships with the protocol/plumbing present but inert (FS-08 §13).

    # Gates whether DetectionIngestHandler ever writes a captured JPEG to the spool or creates a
    # SnapshotOutbox row. Independent of detection_events_enabled's own gate — see the cross-field
    # rule below requiring detection_events_enabled=True whenever this is True.
    snapshot_capture_enabled: bool = False

    # Gates whether SnapshotUploadWorker is constructed at all (FS-08 §11's two-layer kill switch,
    # mirroring detection_sync_enabled). Independent of snapshot_capture_enabled: an operator could
    # in principle capture without uploading, though production rollout flips both together (§13).
    snapshot_upload_enabled: bool = False

    # The spool root a captured JPEG is written under (FS-08 §5) — temp-file + fsync + atomic-rename
    # to `<EventId>.jpg`. Never created by settings loading (no filesystem I/O here).
    snapshot_spool_path: Path = Path("/opt/weapon-detection/snapshots")

    # The nvjpegenc-equivalent JPEG quality recorded here for documentation/future use; the Agent
    # itself never encodes an image (the Bridge does) — this value is not read by any Agent code
    # path today, kept only so the full FS-08/task-brief settings list is complete and validated.
    snapshot_jpeg_quality: int = Field(default=85, ge=1, le=100)

    # The maximum accepted size of one captured JPEG file (FS-08 §5 validation bound), and the size
    # used to derive detection/protocol.MAX_SNAPSHOT_FRAME_BYTES's headroom. Must be positive.
    snapshot_max_file_bytes: int = Field(default=5_242_880, gt=0)

    # The disk-quota enforcement bound (T-149): capture (not detection, not metadata sync) is
    # skipped once the spool's total size would exceed this. Must be at least
    # snapshot_max_file_bytes so a single legitimate file can always fit under an otherwise-empty
    # quota.
    snapshot_max_spool_bytes: int = Field(default=1_073_741_824, gt=0)

    # The bounded batch size SnapshotUploadWorker drains per iteration (mirrors
    # detection_sync_batch_size). Must be positive.
    snapshot_upload_batch_size: int = Field(default=5, gt=0)

    # How long the upload worker sleeps when nothing is upload-ready before checking again. Must be
    # positive.
    snapshot_upload_interval_seconds: float = Field(default=2.0, gt=0)

    # The first backoff delay after an upload failure (mirrors detection_sync_initial_backoff_
    # seconds). Must be positive.
    snapshot_upload_initial_backoff_seconds: float = Field(default=1.0, gt=0)

    # The upload backoff cap. Must be at least snapshot_upload_initial_backoff_seconds (enforced
    # below).
    snapshot_upload_max_backoff_seconds: float = Field(default=60.0, gt=0)

    # The Bridge-side snapshot candidate-frame cache TTL, in milliseconds — recorded here only for
    # the full settings list's completeness (FS-08 §9 describes the Bridge's own cache, a separate
    # process/venv this Agent settings model does not configure). Not read by any Agent code path.
    snapshot_frame_ttl_milliseconds: int = Field(default=750, gt=0)

    # The Bridge-side snapshot candidate-frame cache's maximum retained entries — same "recorded for
    # completeness, Bridge-only" note as snapshot_frame_ttl_milliseconds above.
    snapshot_max_retained_frames: int = Field(default=4, gt=0)

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

    @field_validator("detection_camera_id")
    @classmethod
    def _validate_detection_camera_id(cls, value: str) -> str:
        """Reject a blank camera id (IP-07 T-81, FS-05 §9) — required whenever the field is set,
        not only when detection events are enabled, so a later flip of the kill switch cannot be
        undermined by an already-invalid stored/inherited value."""
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_detection_events_require_a_compatible_pipeline(self) -> AgentSettings:
        """Cross-field incompatible-configuration preflight (IP-07 T-90, FS-05 §4.3/§4.6
        requirement 6).

        Two settings-combination rules, both pure boolean/path comparisons — no filesystem I/O,
        preserving this module's "settings loading performs no filesystem I/O" invariant
        (`root_path`/`deepstream_executable_path`'s own field comments). Whether the *file* at
        `deepstream_executable_path` actually exists, is executable, or is really Bridge-compatible
        is a separate, I/O-performing concern the production cutover preflight command owns
        (`deployment/jetson/deepstream/bridge/preflight-cutover.sh`) — never this model.

        1. `detection_events_enabled=True` requires `deepstream_enabled=True` — a detection pipeline
           with no DeepStream process supervised underneath it can never receive anything; this is
           always wrong, not merely inefficient.
        2. `detection_events_enabled=True` together with `deepstream_executable_path` left at its
           literal default (`DEFAULT_DEEPSTREAM_EXECUTABLE_PATH`, the reference `deepstream-app`
           binary) is rejected — that binary implements none of the T-86 Unix-socket detection
           protocol, so `DetectionIngestHandler` would start and listen forever with no possible
           sender, silently. **Deliberately an explicit-value comparison, not filename-pattern
           guesswork** (task item 2: "prefer an explicit configuration rule") — any executable path
           other than the one literal default is accepted, matching FS-05 §4.3's own posture that
           the Bridge's `run.sh` is simply "whatever `WDA_DEEPSTREAM_EXECUTABLE_PATH` is repointed
           at," never a name this model needs to recognize.

        Both checks are skipped entirely when `detection_events_enabled` is `False` (the default) —
        a disabled feature is never inconsistent with anything, exactly like every other kill-switch
        in this file.
        """
        if not self.detection_events_enabled:
            return self

        problems: list[str] = []
        if not self.deepstream_enabled:
            problems.append(
                "WDA_DETECTION_EVENTS_ENABLED=true requires WDA_DEEPSTREAM_ENABLED=true "
                "(a detection pipeline needs a supervised DeepStream/Bridge process to read from)"
            )
        if self.deepstream_executable_path == DEFAULT_DEEPSTREAM_EXECUTABLE_PATH:
            problems.append(
                "WDA_DETECTION_EVENTS_ENABLED=true requires WDA_DEEPSTREAM_EXECUTABLE_PATH to be "
                "repointed away from the reference binary default "
                f"({DEFAULT_DEEPSTREAM_EXECUTABLE_PATH}) at a Bridge-compatible launcher "
                "(e.g. .../deepstream-bridge/run.sh) — the "
                "reference binary cannot publish the detection-event Unix-socket protocol"
            )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    @model_validator(mode="after")
    def _validate_detection_sync_backoff_bounds(self) -> AgentSettings:
        """The backoff cap must never be tighter than the first backoff delay (FS-06 §10).

        A ``max`` below ``initial`` would make the very first retry immediately clamp below its own
        starting point, defeating the exponential-backoff shape entirely. Checked unconditionally
        (not gated on ``detection_sync_enabled``) so a later flip of that kill switch can never be
        undermined by an already-invalid stored/inherited value — the same posture
        ``_validate_detection_camera_id`` already takes.
        """
        if self.detection_sync_max_backoff_seconds < self.detection_sync_initial_backoff_seconds:
            raise ValueError(
                "WDA_DETECTION_SYNC_MAX_BACKOFF_SECONDS must be greater than or equal to "
                "WDA_DETECTION_SYNC_INITIAL_BACKOFF_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def _validate_snapshot_upload_backoff_bounds(self) -> AgentSettings:
        """The snapshot upload backoff cap must never be tighter than the first delay (FS-08 §11),
        the same rule ``_validate_detection_sync_backoff_bounds`` enforces for metadata sync."""
        if self.snapshot_upload_max_backoff_seconds < self.snapshot_upload_initial_backoff_seconds:
            raise ValueError(
                "WDA_SNAPSHOT_UPLOAD_MAX_BACKOFF_SECONDS must be greater than or equal to "
                "WDA_SNAPSHOT_UPLOAD_INITIAL_BACKOFF_SECONDS"
            )
        return self

    @model_validator(mode="after")
    def _validate_snapshot_max_spool_bytes_fits_one_file(self) -> AgentSettings:
        """The spool quota must be able to hold at least one file at the maximum permitted size —
        otherwise capture would be unconditionally skipped by the T-149 quota check even on an empty
        spool, which is always a misconfiguration, not a real quota."""
        if self.snapshot_max_spool_bytes < self.snapshot_max_file_bytes:
            raise ValueError(
                "WDA_SNAPSHOT_MAX_SPOOL_BYTES must be greater than or equal to "
                "WDA_SNAPSHOT_MAX_FILE_BYTES"
            )
        return self

    @model_validator(mode="after")
    def _validate_snapshot_capture_requires_detection_events(self) -> AgentSettings:
        """Snapshot capture needs the detection-ingest pipeline it hangs off of (FS-08 §12).

        Mirrors ``_validate_detection_events_require_a_compatible_pipeline``'s posture: skipped
        entirely when ``snapshot_capture_enabled`` is ``False`` (the default), so a disabled feature
        is never inconsistent with anything.
        """
        if self.snapshot_capture_enabled and not self.detection_events_enabled:
            raise ValueError(
                "WDA_SNAPSHOT_CAPTURE_ENABLED=true requires WDA_DETECTION_EVENTS_ENABLED=true "
                "(snapshot capture rides on the detection ingest connection)"
            )
        return self


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
