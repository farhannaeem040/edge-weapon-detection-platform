"""Agent-side Unix domain socket detection ingest handler (IP-07 T-86, FS-05 §4/§8; ADR-005).

``DetectionIngestHandler`` implements the existing ``OperationalComponent`` protocol (IP-05 T-60)
unchanged — no new lifecycle abstraction. It is the Agent-side half of the boundary ADR-005 draws at
the Unix domain socket: it never imports ``pyds``/``Gst``, never builds a DeepStream pipeline, and
never trusts identity or timing facts from the wire.

**Pipeline (FS-05 §4/§8).** Each accepted connection is read frame-by-frame
(:mod:`weapon_detection_agent.detection.protocol`); a well-formed, schema-versioned JSON payload
becomes a queued work item. A single consumer task drains the queue and, per item, runs
:func:`~weapon_detection_agent.detection.validation.validate_detection` (raw-fact validation, class
resolution, event-id/timestamp generation — never trusting the wire for identity), then
:class:`~weapon_detection_agent.detection.cooldown.DetectionCooldownTracker` (deduplication), then
:class:`~weapon_detection_agent.persistence.detection_event_repository.DetectionEventRepository`
(persistence). SQLite writes happen only on the consumer task, never inside a socket callback, so a
slow write can never stall accepting the next frame (FS-05 §4.4/§9.2).

**Persistence-failure / cooldown ordering (T-86 binding rule).** The cooldown window for a key is
only started by :meth:`~weapon_detection_agent.detection.cooldown.DetectionCooldownTracker.commit`,
called **after** ``DetectionEventRepository.insert`` succeeds — never before. A failed insert (or a
defensive, expected-to-be-unreachable duplicate ``event_id``) is logged and the message is dropped,
but the *next* genuine detection for that key is still evaluated as if the failed one never
happened — "accepted for cooldown" means "successfully persisted," not "decided ACCEPT."

**Wire-payload strictness (FS-05 §5, item 7 of this task).** The payload is *never* rejected merely
for carrying an identity/authority-looking field (``event_id``, ``device_id``, ``camera_id``,
``class_name``, ``detected_at_utc``, ``created_at_utc``, ``delivery_status``) — FS-05 §5 states
these are "never read from payload even if present," which this handler implements by simply never
reading them (:func:`validate_detection` already only reads the approved raw-fact keys).
Hard-rejecting the whole message for an extra key would couple this handler to the Bridge's exact
wire shape for no correctness benefit FS-05 requires; the chosen strictness is "never trusted,"
not "rejected on presence." ``schema_version`` is the one field checked structurally, before any
other parsing, since an unsupported version makes the rest of the payload's shape unknowable.

**Queue/backpressure (FS-05 §9.2, item 9 of this task).** The internal ``asyncio.Queue`` (capacity
``WDA_DETECTION_QUEUE_CAPACITY``) decouples socket reads from SQLite writes. A full queue drops the
newest item (never blocks the reader) with a bounded, edge-triggered + periodic warning — never one
log line per dropped frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from weapon_detection_agent.config.paths import modes_enforceable
from weapon_detection_agent.detection.class_labels import (
    load_class_names,
    resolve_class_labels_path,
)
from weapon_detection_agent.detection.cooldown import CooldownDecision, DetectionCooldownTracker
from weapon_detection_agent.detection.errors import (
    DetectionDeviceIdentityUnavailableError,
    DetectionIngestHandlerAlreadyRunningError,
    DetectionRuntimeDirectoryMissingError,
    DetectionSocketPathConflictError,
)
from weapon_detection_agent.detection.protocol import (
    FRAME_LENGTH_BYTEORDER,
    FRAME_LENGTH_PREFIX_BYTES,
    MAX_FRAME_BYTES,
    SUPPORTED_SCHEMA_VERSION,
)
from weapon_detection_agent.detection.validation import DetectionRejection, validate_detection
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.errors import DetectionEventAlreadyExistsError

if TYPE_CHECKING:
    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )

_LOGGER = logging.getLogger("weapon_detection_agent.detection.ingest_handler")

# A lazy Device ID source (IP-07 T-87 correction) — never resolved until called. The same DI seam
# style as every other injectable dependency in this module (``clock``, ``event_id_factory``), but
# deliberately a callable rather than a plain ``str`` constructor argument: the persisted identity
# may not exist yet at the moment this handler is *constructed* (a brand-new device activates only
# later, at operational-component *start* time — see ``DetectionIngestHandler.start``), so the
# value cannot be a plain eagerly-supplied argument without recreating the bug this correction
# fixes.
DeviceIdentityProvider = Callable[[], str]

# Owner-only, matching the database file's posture (FS-05 §4.5) — the socket is at least as
# sensitive as the credential-bearing database, since anything that can connect to it can inject
# fabricated detections.
SOCKET_FILE_MODE = 0o600

# Emit the queue-full warning on the first drop, then only every Nth drop thereafter — never once
# per dropped frame (task requirement: bounded/aggregated, not unbounded).
_QUEUE_FULL_LOG_INTERVAL = 50

# Python 3.11's asyncio.BaseSelectorEventLoop (asyncio/selector_events.py,
# BaseSelectorEventLoop._accept_connection/_accept_connection2) splits accepting one connection
# across two separate event-loop ticks, not one atomic step:
#   tick N   — the listening socket's reader callback fires, calls sock.accept() (the OS-level
#              connection now exists), and schedules a *separate* Task for the rest of the setup.
#   tick N+1 — that Task's first step actually runs: it builds the transport/protocol, which is
#              what calls our _on_client_connected callback and adds the entry to
#              self._connection_tasks.
# A client whose connect() completed just before stop() runs can therefore be sitting in exactly
# that one-tick gap: accepted at the OS level, but not yet registered with us. If stop() called
# server.close() immediately, it would remove the listening socket's reader before tick N+1 ever
# ran — silently orphaning that connection (never cancelled, its writer never closed, the client
# left blocked on read() forever, and a leaked, un-awaited Task).
#
# The fix is to yield the event loop a few bare ticks *before* closing the listener, so any
# accept already queued at the OS level gets a chance to finish registering while we are still
# accepting. `await asyncio.sleep(0)` does not sleep for any wall-clock duration — it re-queues
# the current coroutine at the back of the event loop's ready queue, i.e. it advances scheduling
# by exactly one tick, the same unit the race above is measured in. This is why sleep(0) is the
# correct tool here and a real-time delay (e.g. sleep(0.1)) would not be: the race is about
# scheduling order, not elapsed time, and a fixed real-time sleep would either be needlessly slow
# or still theoretically racy under different scheduling conditions.
#
# The operation stays bounded because the loop count is a small, fixed constant, not a retry-until-
# condition loop: empirically 2 ticks are sufficient to close the gap (verified over 50 stress
# trials on the target Jetson runtime); this constant is double that for margin, so stop() always
# performs exactly this many no-op yields — a few microseconds — regardless of whether a race was
# actually in progress.
_ACCEPT_DRAIN_YIELDS = 4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _FrameRejected(Exception):
    """A single connection's frame violated the wire protocol; close only that connection."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DetectionIngestHandler:
    """The Agent-side Unix domain socket server for raw DeepStream Bridge detection messages.

    Construct with the socket path, queue capacity, and every piece of Agent-owned context the
    validator/cooldown/repository pipeline needs — all explicit, injectable (the same DI seam
    style as :class:`~weapon_detection_agent.deepstream.process_manager.DeepStreamProcessManager`).
    None of ``device_id``/``camera_id``/``class_names``/``min_confidence`` is ever read from the
    wire.

    ``class_names`` is the already-resolved class-id -> class-name map (the active profile's
    ``labels.txt``, read once by the caller — this handler never reads a labels file itself, so it
    is read exactly once regardless of how many detections arrive, task requirement).

    ``device_id_provider`` is a **lazy** ``Callable[[], str]`` (IP-07 T-87 correction) — never
    resolved at construction time. The Agent constructs its operational components before it has
    necessarily activated (a brand-new device has no persisted identity yet at that point), so
    reading the persisted Device ID at construction would fail startup before activation ever gets
    a chance to run. Resolution is deferred to :meth:`start`, which the operational-state
    coordinator only calls *after* activation has created or confirmed the identity — the provider
    always reads :class:`~weapon_detection_agent.persistence.device_identity_repository.
    DeviceIdentityRepository` fresh, never generates a replacement Device ID, and never accepts one
    from the wire/Bridge.
    """

    def __init__(
        self,
        *,
        socket_path: str | Path,
        queue_capacity: int,
        device_id_provider: DeviceIdentityProvider,
        camera_id: str,
        class_names: Mapping[int, str],
        min_confidence: float,
        cooldown_tracker: DetectionCooldownTracker,
        repository: DetectionEventRepository,
        clock: Callable[[], datetime] = _utc_now,
        event_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")

        self._socket_path = Path(socket_path)
        self._queue_capacity = queue_capacity
        self._device_id_provider = device_id_provider
        self._device_id: str | None = None  # resolved lazily — see _resolve_device_id
        self._camera_id = camera_id
        self._class_names = class_names
        self._min_confidence = min_confidence
        self._cooldown = cooldown_tracker
        self._repository = repository
        self._clock = clock
        self._event_id_factory = event_id_factory

        self._server: asyncio.Server | None = None
        self._queue: asyncio.Queue[Mapping[str, Any]] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._dropped_count = 0

    def _resolve_device_id(self) -> str:
        """Resolve and cache the persisted Device ID, calling the provider at most once.

        Called first thing in :meth:`start` (IP-07 T-87 correction) so a missing/invalid identity
        fails ``start()`` cleanly before anything else (the socket path, the class labels, nothing)
        is touched. Also called from :meth:`_process` so unit tests exercising the portable
        validation/cooldown/persistence pipeline directly (never calling ``start()``/binding a real
        socket) still resolve identity exactly the same lazy way production does; in production
        ``_process`` never runs before ``start()`` already resolved it, since the consumer task that
        calls it is itself created inside ``start()``.
        """
        if self._device_id is None:
            self._device_id = self._device_id_provider()
        return self._device_id

    # --- OperationalComponent protocol (IP-05 T-60) ---------------------------------------------

    @property
    def name(self) -> str:
        """A safe, static component identifier — never derived from a path or payload."""
        return "detection-ingest"

    async def start(self) -> None:
        """Resolve the persisted device identity, then bind the Unix domain socket and start
        accepting connections.

        Raises :class:`DetectionIngestHandlerAlreadyRunningError` if already started — no second
        listener is bound. Resolves the Device ID *first*, before anything else — including the
        socket path — is touched (IP-07 T-87 correction): if the provider still cannot produce a
        persisted identity at this point (activation never completed, or failed), this propagates
        straight out of ``start()`` and nothing is bound, so the operational-state coordinator's
        existing start-failure handling applies unchanged (the component never starts; a later
        component in the same start attempt, e.g. DeepStream, is never started either). Otherwise
        raises :class:`DetectionRuntimeDirectoryMissingError` if the socket's parent directory does
        not exist (provisioning it is T-82's job, not this handler's), and
        :class:`DetectionSocketPathConflictError` if the socket path exists but is not a Unix domain
        socket left by a previous run (a stale socket *is* removed automatically; anything else is
        not).
        """
        if self._server is not None:
            raise DetectionIngestHandlerAlreadyRunningError(
                "the detection ingest handler is already running"
            )

        self._resolve_device_id()
        self._ensure_socket_path_ready()

        self._dropped_count = 0
        self._queue = asyncio.Queue(maxsize=self._queue_capacity)
        server = await asyncio.start_unix_server(
            self._on_client_connected, path=str(self._socket_path)
        )
        self._apply_socket_permissions()
        self._server = server
        self._consumer_task = asyncio.create_task(self._consume(), name="detection-ingest-consumer")
        _LOGGER.info("detection_ingest_started", extra={"component": self.name})

    async def stop(self) -> None:
        """Stop accepting connections, close/await every in-flight task, and remove the socket file.

        Idempotent — a no-op if not running. Queued-but-not-yet-processed items are discarded, not
        drained: a bounded, prompt shutdown is preferred over completeness (consistent with the
        already-accepted drop-newest backpressure policy), and item 18's "stop returns within a
        bounded time" requirement rules out waiting for arbitrarily many queued SQLite writes.
        """
        server = self._server
        if server is None:
            return
        self._server = None

        for _ in range(_ACCEPT_DRAIN_YIELDS):
            await asyncio.sleep(0)

        server.close()
        await server.wait_closed()

        connection_tasks = list(self._connection_tasks)
        for task in connection_tasks:
            task.cancel()
        for task in connection_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._connection_tasks.clear()

        consumer_task = self._consumer_task
        self._consumer_task = None
        if consumer_task is not None:
            consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer_task

        self._queue = None

        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()

        _LOGGER.info("detection_ingest_stopped", extra={"component": self.name})

    # --- Startup filesystem safety (item 3) ------------------------------------------------------

    def _ensure_socket_path_ready(self) -> None:
        parent = self._socket_path.parent
        if not parent.exists():
            raise DetectionRuntimeDirectoryMissingError(
                f"detection socket directory does not exist: {parent} "
                "(provisioning the Agent filesystem layout is the layout task's responsibility)"
            )

        try:
            # lstat, never stat: a symlink at the socket path must be treated as "unexpected type"
            # (item 3), not silently followed to whatever it points at.
            mode = os.lstat(self._socket_path).st_mode
        except FileNotFoundError:
            return

        if stat.S_ISSOCK(mode):
            self._socket_path.unlink()
            _LOGGER.info("detection_stale_socket_removed", extra={"component": self.name})
            return

        raise DetectionSocketPathConflictError(
            f"detection socket path exists and is not a Unix domain socket: {self._socket_path}"
        )

    def _apply_socket_permissions(self) -> None:
        if modes_enforceable():
            os.chmod(self._socket_path, SOCKET_FILE_MODE)

    # --- Connection handling (items 4-7, 12) ------------------------------------------------------

    def _on_client_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # A plain (non-async) callback so *we* create and track the Task, giving stop() a handle to
        # cancel/await every in-flight connection — asyncio.start_unix_server would otherwise create
        # an untracked Task itself if this were a coroutine function.
        task = asyncio.create_task(
            self._handle_connection(reader, writer), name="detection-ingest-connection"
        )
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        _LOGGER.debug("detection_connection_opened", extra={"component": self.name})
        try:
            while True:
                try:
                    payload = await self._read_message(reader)
                except _FrameRejected as exc:
                    _LOGGER.warning(
                        "detection_connection_rejected",
                        extra={"component": self.name, "reason": exc.reason},
                    )
                    return
                except asyncio.IncompleteReadError:
                    return  # client disconnected mid-frame; a normal, safe occurrence
                if payload is None:
                    return  # clean EOF between messages
                self._enqueue(payload)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            _LOGGER.debug("detection_connection_closed", extra={"component": self.name})

    async def _read_message(self, reader: asyncio.StreamReader) -> Mapping[str, Any] | None:
        """Read exactly one length-prefixed frame, or ``None`` on a clean between-message EOF.

        Raises :class:`_FrameRejected` for any protocol violation (oversized/zero-length frame,
        malformed UTF-8/JSON, non-object JSON, unsupported schema version) and lets
        ``asyncio.IncompleteReadError`` propagate for a disconnect mid-frame — both are handled by
        the caller, which closes only this connection.
        """
        try:
            prefix = await reader.readexactly(FRAME_LENGTH_PREFIX_BYTES)
        except asyncio.IncompleteReadError as exc:
            if exc.partial == b"":
                return None  # a clean disconnect between messages, not an error
            raise  # disconnected mid-prefix

        length = int.from_bytes(prefix, FRAME_LENGTH_BYTEORDER)
        if length <= 0:
            raise _FrameRejected("zero_length_frame")
        if length > MAX_FRAME_BYTES:
            # Rejected from the length prefix alone — the payload itself is never read/allocated.
            raise _FrameRejected("oversized_frame")

        try:
            body = await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            raise _FrameRejected("incomplete_payload") from exc

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _FrameRejected("invalid_utf8") from exc

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _FrameRejected("invalid_json") from exc

        if not isinstance(parsed, dict):
            raise _FrameRejected("invalid_json_shape")

        if parsed.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
            raise _FrameRejected("unsupported_schema_version")

        return parsed

    # --- Queue / backpressure (item 9) ------------------------------------------------------------

    def _enqueue(self, payload: Mapping[str, Any]) -> None:
        queue = self._queue
        if queue is None:
            return  # stopping; nothing left to hand work to
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            self._dropped_count += 1
            if self._dropped_count == 1 or self._dropped_count % _QUEUE_FULL_LOG_INTERVAL == 0:
                _LOGGER.warning(
                    "detection_queue_full_dropped_newest",
                    extra={"component": self.name, "dropped_count": self._dropped_count},
                )

    # --- Consumer: validation -> cooldown -> persistence (items 8, 10, 11) ------------------------

    async def _consume(self) -> None:
        assert self._queue is not None  # set immediately before this task is created
        queue = self._queue
        while True:
            payload = await queue.get()
            try:
                await self._process(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A backstop only: _process already handles every expected failure internally
                # (rejection, suppression, persistence failure). Nothing here may kill this task —
                # a single bad item must never stop later messages from being processed.
                _LOGGER.exception(
                    "detection_event_processing_failed", extra={"component": self.name}
                )

    async def _process(self, payload: Mapping[str, Any]) -> None:
        now = self._clock()
        result = validate_detection(
            payload,
            device_id=self._resolve_device_id(),
            camera_id=self._camera_id,
            class_names=self._class_names,
            min_confidence=self._min_confidence,
            now=now,
            event_id_factory=self._event_id_factory,
        )

        if isinstance(result, DetectionRejection):
            _LOGGER.warning(
                "detection_event_rejected",
                extra={
                    "component": self.name,
                    "reason": result.reason.value,
                    "field": result.field,
                },
            )
            return

        event = result
        decision = self._cooldown.decide(
            device_id=event.device_id, camera_id=event.camera_id, class_name=event.class_name
        )
        if decision is CooldownDecision.SUPPRESS:
            _LOGGER.debug(
                "detection_event_suppressed",
                extra={"component": self.name, "class_name": event.class_name},
            )
            return

        try:
            self._repository.insert(event)
        except DetectionEventAlreadyExistsError:
            # Defensive-only (the cooldown decision above should make this unreachable in practice).
            # Never commits the cooldown window: nothing new was persisted by this call.
            _LOGGER.warning(
                "detection_event_duplicate_rejected",
                extra={"component": self.name, "event_id": str(event.event_id)},
            )
            return
        except Exception:
            # Any other persistence failure (e.g. disk full): logged without raw payload content,
            # the consumer keeps running, and — critically — the cooldown window is NOT committed,
            # so the next genuine detection for this key is not phantom-suppressed (T-86 binding
            # rule: "accepted for cooldown" means "successfully persisted").
            _LOGGER.exception(
                "detection_event_persistence_failed",
                extra={"component": self.name, "class_name": event.class_name},
            )
            return

        self._cooldown.commit(
            device_id=event.device_id, camera_id=event.camera_id, class_name=event.class_name
        )
        _LOGGER.info(
            "detection_event_persisted",
            extra={"component": self.name, "event_id": str(event.event_id)},
        )


def default_device_identity_provider(
    identity_repository: DeviceIdentityRepository,
) -> DeviceIdentityProvider:
    """Build a lazy :data:`DeviceIdentityProvider` bound to ``identity_repository`` (IP-07 T-87).

    Reads nothing when this function itself runs — it only closes over the repository. The
    repository is read fresh on every call of the returned callable (``DetectionIngestHandler``
    calls it at most once per its own lifetime via ``_resolve_device_id``'s caching, so in practice
    this means "exactly once, whenever the handler first needs it," never per detection event).

    Raises :class:`~weapon_detection_agent.detection.errors.DetectionDeviceIdentityUnavailableError`
    if no usable persisted identity exists at call time — never returns a placeholder, never
    generates a replacement Device ID, and never reads one from anywhere but T-58's
    ``DeviceIdentityRepository`` (FS-05 §5: the Bridge/wire is never trusted for identity).
    """

    def _provide() -> str:
        identity = identity_repository.load()
        if identity is None or not identity.device_id:
            raise DetectionDeviceIdentityUnavailableError(
                "detection events are enabled but no persisted device identity is available; the "
                "Agent will not start a partially functional detection ingest pipeline"
            )
        return identity.device_id

    return _provide


def default_detection_components_factory(
    settings: AgentSettings,
    paths: AgentPaths,
    identity_repository: DeviceIdentityRepository,
) -> tuple[DetectionIngestHandler, ...]:
    """Build the real ``DetectionIngestHandler`` from Agent-owned dependencies (IP-07 T-87).

    Mirrors ``default_deepstream_components_factory``'s two-layer kill-switch discipline, but is a
    **self-contained** gate on *both* ``settings.deepstream_enabled`` and
    ``settings.detection_events_enabled`` (FS-05 §4: the ingest handler runs "only while
    ``deepstream_enabled`` and ``detection_events_enabled`` are both true") — this function
    enforces that invariant itself rather than depending on the caller to compose it correctly.
    Returns ``()`` whenever either switch is off: no ``DetectionIngestHandler`` is constructed, no
    socket path is touched, no class-label file is read, and no persisted identity is read — a
    disabled feature behaves as if it does not exist, exactly like the DeepStream factory's own
    documented posture.

    **Identity is never read here (IP-07 T-87 correction).** This factory runs during Agent
    component *construction*, which — per ``runtime/startup.py``'s ``_start()`` — happens *before*
    ``AgentRuntimeSupervisor.startup()`` runs activation. A brand-new device has no persisted
    identity yet at exactly this point, so eagerly reading it here (as an earlier version of this
    function did) would fail Agent startup before the device ever gets a chance to activate. Instead
    this only builds a lazy :func:`default_device_identity_provider`, deferring the actual read to
    :meth:`DetectionIngestHandler.start`, which the coordinator only calls *after* activation has
    created or confirmed the identity. If it is still unavailable at that later point, ``start()``
    raises and fails loudly through the existing operational-component start-failure path — never a
    partially functional ingest pipeline.

    The active model profile's class-id -> class-name mapping is read exactly once, here, from
    ``labels.txt`` (:mod:`weapon_detection_agent.detection.class_labels`) — never per detection
    event, and never a hardcoded class count or name (genericness, IP-07 T-87 item 7). This has no
    activation dependency (it comes from ``settings.deepstream_model_profile`` alone), so — unlike
    identity — reading it at construction time is safe and matches the original task wording
    ("loaded once during construction/startup").
    """
    if not settings.deepstream_enabled or not settings.detection_events_enabled:
        return ()

    class_names = load_class_names(resolve_class_labels_path(settings))

    return (
        DetectionIngestHandler(
            # settings.detection_socket_path, not paths.detection_socket_file (IP-07 T-90 fix):
            # FS-05 §9 and this field's own docstring document WDA_DETECTION_SOCKET_PATH as a real,
            # independently-overridable setting "DetectionIngestHandler reads" — wiring the
            # root-derived paths.detection_socket_file here instead silently dropped that override
            # (an operator setting only WDA_DETECTION_SOCKET_PATH, not WDA_ROOT_PATH, would have had
            # no effect on the Agent side while run.sh honored it on the Bridge side, desyncing the
            # two). The two agree by construction for the default root (both resolve to
            # /opt/weapon-detection/runtime/detection.sock); only a deliberate, explicit override of
            # one without the other can now diverge them, exactly as intended.
            socket_path=settings.detection_socket_path,
            queue_capacity=settings.detection_queue_capacity,
            device_id_provider=default_device_identity_provider(identity_repository),
            camera_id=settings.detection_camera_id,
            class_names=class_names,
            min_confidence=settings.detection_min_confidence,
            cooldown_tracker=DetectionCooldownTracker(
                cooldown_seconds=settings.detection_cooldown_seconds
            ),
            repository=DetectionEventRepository(paths.database_file),
        ),
    )
