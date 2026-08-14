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
import dataclasses
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
    FRAME_HEADER_BYTES,
    FRAME_KIND_ACKNOWLEDGEMENT,
    FRAME_KIND_DETECTION,
    FRAME_KIND_SNAPSHOT,
    MAX_FRAME_BYTES,
    MAX_SNAPSHOT_FRAME_BYTES,
    SNAPSHOT_PROTOCOL_SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    decode_frame_header,
    decode_snapshot_message,
    encode_frame,
)
from weapon_detection_agent.detection.snapshot_capture import (
    SnapshotValidationError,
    compute_sha256,
    reconcile_snapshot_spool,
    spool_usage_bytes,
    validate_snapshot_bytes,
    write_snapshot_atomic,
)
from weapon_detection_agent.detection.validation import (
    DetectionRejection,
    DetectionRejectionReason,
    normalize_v2_payload,
    validate_detection,
)
from weapon_detection_agent.persistence.detection_event_repository import DetectionEventRepository
from weapon_detection_agent.persistence.errors import (
    DetectionEventAlreadyExistsError,
    SnapshotOutboxAlreadyExistsError,
)

if TYPE_CHECKING:
    from weapon_detection_agent.config.paths import AgentPaths
    from weapon_detection_agent.config.settings import AgentSettings
    from weapon_detection_agent.persistence.device_identity_repository import (
        DeviceIdentityRepository,
    )
    from weapon_detection_agent.persistence.snapshot_outbox_repository import (
        SnapshotOutboxRepository,
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


@dataclasses.dataclass(frozen=True)
class _DetectionQueueItem:
    """One queued detection message, paired with the connection it arrived on.

    ``writer`` is carried alongside the payload (IP-10 T-139, FS-08 §4) so the consumer task can
    send the acknowledgement back over the *same* connection after persistence — the connection that
    sent the message is not otherwise recoverable once the item is sitting in the shared queue.
    """

    payload: Mapping[str, Any]
    writer: asyncio.StreamWriter


@dataclasses.dataclass(frozen=True)
class _SnapshotQueueItem:
    """One queued snapshot frame's correlator + raw JPEG bytes (IP-10 T-140, FS-08 §5).

    A ``FRAME_KIND_SNAPSHOT`` wire frame's body is not JSON — it is a 2-byte-length-prefixed UTF-8
    ``eventId`` sub-header followed by raw JPEG bytes (:func:`~weapon_detection_agent.detection.
    protocol.decode_snapshot_message`), already split apart by the time this item is queued.
    """

    event_id: str
    data: bytes


# Emit the spool-quota-exceeded warning on the first skip, then only every Nth skip thereafter —
# mirrors _QUEUE_FULL_LOG_INTERVAL's bounded/aggregated logging discipline (T-149).
_SPOOL_QUOTA_LOG_INTERVAL = 50


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
        camera_id_resolver: Callable[[int], UUID | None] | None = None,
        class_names: Mapping[int, str],
        min_confidence: float,
        cooldown_tracker: DetectionCooldownTracker,
        repository: DetectionEventRepository,
        clock: Callable[[], datetime] = _utc_now,
        event_id_factory: Callable[[], UUID] = uuid4,
        snapshot_capture_enabled: bool = False,
        snapshot_repository: SnapshotOutboxRepository | None = None,
        snapshot_spool_path: Path | str | None = None,
        snapshot_max_file_bytes: int = 5_242_880,
        snapshot_max_spool_bytes: int = 1_073_741_824,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if snapshot_capture_enabled and (
            snapshot_repository is None or snapshot_spool_path is None
        ):
            raise ValueError(
                "snapshot_repository and snapshot_spool_path are required when "
                "snapshot_capture_enabled is True"
            )

        self._socket_path = Path(socket_path)
        self._queue_capacity = queue_capacity
        self._device_id_provider = device_id_provider
        self._device_id: str | None = None  # resolved lazily — see _resolve_device_id
        self._camera_id = camera_id
        # FS-11 §9: when set (server-driven Camera configuration mode), resolves the Bridge-reported
        # numeric source_id to an immutable Camera.CameraId for each detection, in place of the
        # static self._camera_id above. None in the pre-FS-11/feature-disabled static mode.
        self._camera_id_resolver = camera_id_resolver
        self._class_names = class_names
        self._min_confidence = min_confidence
        self._cooldown = cooldown_tracker
        self._repository = repository
        self._clock = clock
        self._event_id_factory = event_id_factory

        # --- Snapshot evidence capture (IP-10 T-139/T-140, FS-08 §4/§5) -----------------------
        self._snapshot_capture_enabled = snapshot_capture_enabled
        self._snapshot_repository = snapshot_repository
        self._snapshot_spool_path = (
            Path(snapshot_spool_path) if snapshot_spool_path is not None else None
        )
        self._snapshot_max_file_bytes = snapshot_max_file_bytes
        self._snapshot_max_spool_bytes = snapshot_max_spool_bytes
        self._spool_quota_skip_count = 0

        self._server: asyncio.Server | None = None
        self._queue: asyncio.Queue[_DetectionQueueItem | _SnapshotQueueItem] | None = None
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

        if self._snapshot_capture_enabled:
            assert self._snapshot_repository is not None  # noqa: S101 -- enforced by __init__
            assert self._snapshot_spool_path is not None  # noqa: S101 -- enforced by __init__
            reconcile_snapshot_spool(self._snapshot_repository, self._snapshot_spool_path)

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
                    # Bounded by the larger of the two content caps up front (the frame's `kind`,
                    # which decides which cap actually applies, is not known until the header is
                    # parsed) — a detection frame claiming a length over MAX_FRAME_BYTES is rejected
                    # as oversized separately, below, once its kind is known.
                    frame = await self._read_frame(
                        reader, MAX_SNAPSHOT_FRAME_BYTES, allow_clean_eof=True
                    )
                except _FrameRejected as exc:
                    _LOGGER.warning(
                        "detection_connection_rejected",
                        extra={"component": self.name, "reason": exc.reason},
                    )
                    return
                except asyncio.IncompleteReadError:
                    return  # client disconnected mid-frame; a normal, safe occurrence
                if frame is None:
                    return  # clean EOF between messages

                kind, body = frame

                if kind == FRAME_KIND_SNAPSHOT:
                    try:
                        event_id, data = decode_snapshot_message(body)
                    except (UnicodeDecodeError, IndexError, ValueError):
                        _LOGGER.warning(
                            "detection_connection_rejected",
                            extra={"component": self.name, "reason": "malformed_snapshot_frame"},
                        )
                        return
                    self._enqueue(_SnapshotQueueItem(event_id=event_id, data=data))
                    continue

                if kind != FRAME_KIND_DETECTION:
                    _LOGGER.warning(
                        "detection_connection_rejected",
                        extra={"component": self.name, "reason": "unexpected_frame_kind"},
                    )
                    return

                if len(body) > MAX_FRAME_BYTES:
                    _LOGGER.warning(
                        "detection_connection_rejected",
                        extra={"component": self.name, "reason": "oversized_frame"},
                    )
                    return

                try:
                    payload = self._parse_detection_body(body)
                except _FrameRejected as exc:
                    _LOGGER.warning(
                        "detection_connection_rejected",
                        extra={"component": self.name, "reason": exc.reason},
                    )
                    return

                self._enqueue(_DetectionQueueItem(payload=payload, writer=writer))
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            _LOGGER.debug("detection_connection_closed", extra={"component": self.name})

    async def _read_message(self, reader: asyncio.StreamReader) -> Mapping[str, Any] | None:
        """Read exactly one ``FRAME_KIND_DETECTION`` frame's JSON body, or ``None`` on a clean
        between-message EOF.

        Kept as its own method (rather than inlined into :meth:`_handle_connection`) because the
        portable frame-level unit tests exercise it directly against a fake reader, with no queue or
        connection involved. Raises :class:`_FrameRejected` for any protocol violation (unexpected
        frame kind, oversized/zero-length frame, malformed UTF-8/JSON, non-object JSON, unsupported
        schema version) and lets ``asyncio.IncompleteReadError`` propagate for a mid-frame
        disconnect.
        """
        frame = await self._read_frame(reader, MAX_FRAME_BYTES, allow_clean_eof=True)
        if frame is None:
            return None
        kind, body = frame
        if kind != FRAME_KIND_DETECTION:
            raise _FrameRejected("unexpected_frame_kind")
        return self._parse_detection_body(body)

    @staticmethod
    def _parse_detection_body(body: bytes) -> Mapping[str, Any]:
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

        schema_version = parsed.get("schema_version", parsed.get("schemaVersion"))
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise _FrameRejected("unsupported_schema_version")

        return parsed

    async def _read_frame(
        self, reader: asyncio.StreamReader, max_bytes: int, *, allow_clean_eof: bool = False
    ) -> tuple[int, bytes] | None:
        """Read exactly one frame — its 1-byte kind, 4-byte length, and body — bounded by
        ``max_bytes``. Returns ``(kind, body)``, or ``None`` on a clean between-message EOF when
        ``allow_clean_eof`` is set.
        """
        try:
            header = await reader.readexactly(FRAME_HEADER_BYTES)
        except asyncio.IncompleteReadError as exc:
            if allow_clean_eof and exc.partial == b"":
                return None
            raise

        kind, length = decode_frame_header(header)
        if length <= 0:
            raise _FrameRejected("zero_length_frame")
        if length > max_bytes:
            # Rejected from the header alone — the body itself is never read/allocated.
            raise _FrameRejected("oversized_frame")

        try:
            body = await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            raise _FrameRejected("incomplete_payload") from exc

        return kind, body

    # --- Queue / backpressure (item 9) ------------------------------------------------------------

    def _enqueue(self, item: _DetectionQueueItem | _SnapshotQueueItem) -> None:
        queue = self._queue
        if queue is None:
            return  # stopping; nothing left to hand work to
        try:
            queue.put_nowait(item)
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
            item = await queue.get()
            try:
                if isinstance(item, _SnapshotQueueItem):
                    self._process_snapshot(item)
                else:
                    await self._process(item.payload, item.writer)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A backstop only: _process/_process_snapshot already handle every expected failure
                # internally (rejection, suppression, persistence/capture failure). Nothing here may
                # kill this task — a single bad item must never stop later messages from being
                # processed.
                _LOGGER.exception(
                    "detection_event_processing_failed", extra={"component": self.name}
                )

    async def _process(
        self, payload: Mapping[str, Any], writer: asyncio.StreamWriter | None = None
    ) -> None:
        """Validate -> cooldown -> persist -> (v2 only) acknowledge one detection payload.

        ``writer`` is ``None`` for direct portable-test calls that exercise this pipeline without a
        real connection (mirrors this method's pre-FS-08 signature, which took only ``payload``) —
        an acknowledgement is simply never sent in that case, exactly as if the message had arrived
        as schema_version 1.
        """
        schema_version = payload.get("schema_version", payload.get("schemaVersion"))
        raw_message_id = payload.get("messageId")
        # A single narrowed (writer, message_id) pair — never two separately-Optional locals — so
        # every acknowledgement call site below is unconditionally well-typed rather than needing a
        # repeated `writer is not None and isinstance(message_id, str)` guard at each of the three
        # possible outcomes (rejected/suppressed/accepted).
        ack_target: tuple[asyncio.StreamWriter, str] | None = None
        if (
            writer is not None
            and schema_version == SNAPSHOT_PROTOCOL_SCHEMA_VERSION
            and isinstance(raw_message_id, str)
        ):
            ack_target = (writer, raw_message_id)

        # v2's wire shape (camelCase, nested boundingBox, FS-08 §4.3) is normalized to the flat
        # snake_case shape validate_detection has always expected — the one place schema-version-
        # specific wire translation belongs, before any validation rule runs.
        if schema_version == SNAPSHOT_PROTOCOL_SCHEMA_VERSION:
            payload = normalize_v2_payload(payload)

        camera_id = self._camera_id
        if self._camera_id_resolver is not None:
            # FS-11 §9: resolve per-message from the Bridge-reported source_id rather than trusting
            # a single static Agent-wide camera identity. An unresolvable source_id (unknown to the
            # currently applied configuration generation, or a malformed/missing field) is rejected
            # safely — never forwarded as a DetectionEvent with a guessed or stale identity.
            raw_source_id = payload.get("source_id")
            resolved_camera_id = (
                self._camera_id_resolver(raw_source_id)
                if isinstance(raw_source_id, int) and not isinstance(raw_source_id, bool)
                else None
            )
            if resolved_camera_id is None:
                _LOGGER.warning(
                    "detection_event_rejected",
                    extra={
                        "component": self.name,
                        "reason": DetectionRejectionReason.UNRESOLVED_CAMERA_SOURCE.value,
                        "field": "source_id",
                    },
                )
                if ack_target is not None:
                    ack_writer, message_id = ack_target
                    self._send_ack(
                        ack_writer,
                        message_id,
                        "rejected",
                        error_code=DetectionRejectionReason.UNRESOLVED_CAMERA_SOURCE.value,
                    )
                return
            camera_id = str(resolved_camera_id)

        now = self._clock()
        result = validate_detection(
            payload,
            device_id=self._resolve_device_id(),
            camera_id=camera_id,
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
            if ack_target is not None:
                ack_writer, message_id = ack_target
                self._send_ack(ack_writer, message_id, "rejected", error_code=result.reason.value)
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
            if ack_target is not None:
                ack_writer, message_id = ack_target
                self._send_ack(ack_writer, message_id, "suppressed")
            return

        try:
            self._repository.insert(event)
        except DetectionEventAlreadyExistsError:
            # Defensive-only (the cooldown decision above should make this unreachable in practice).
            # Never commits the cooldown window: nothing new was persisted by this call. No
            # acknowledgement is sent either — the row was not durably created by *this* call
            # (T-139 binding rule: only send an acknowledgement after this call's own persistence).
            _LOGGER.warning(
                "detection_event_duplicate_rejected",
                extra={"component": self.name, "event_id": str(event.event_id)},
            )
            return
        except Exception:
            # Any other persistence failure (e.g. disk full): logged without raw payload content,
            # the consumer keeps running, and — critically — the cooldown window is NOT committed,
            # so the next genuine detection for this key is not phantom-suppressed (T-86 binding
            # rule: "accepted for cooldown" means "successfully persisted"). No acknowledgement is
            # sent (T-139: only after a successful commit) — the Bridge's cache entry simply
            # expires.
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

        # The acknowledgement is sent only now — strictly after the DetectionEventRepository.insert
        # call above has committed (T-139 binding rule, FS-08 §4.4).
        if ack_target is not None:
            ack_writer, message_id = ack_target
            self._send_ack(
                ack_writer,
                message_id,
                "accepted",
                event_id=event.event_id,
                snapshot_required=self._snapshot_capture_enabled,
            )

    # --- Acknowledgement send (IP-10 T-139, FS-08 §4.4) ---------------------------------------

    def _send_ack(
        self,
        writer: asyncio.StreamWriter,
        message_id: str,
        outcome: str,
        *,
        event_id: UUID | None = None,
        snapshot_required: bool = False,
        error_code: str | None = None,
    ) -> None:
        """Write one framed acknowledgement back over ``writer``. Fire-and-forget, never awaited.

        A cheap, bounded, best-effort send (FS-08 §4.4) — never a new synchronous dependency the
        ingest path waits on. ``writer.write()`` only buffers; it is not flushed here (no
        ``drain()``), so this can never block the consumer task regardless of how slow/stalled the
        Bridge's reader is. Any failure (closed/closing writer, a transport error) is swallowed —
        a missing/late acknowledgement is an expected, safe outcome for the Bridge (FS-08 §4.4), not
        a fault on this side.
        """
        if writer.is_closing():
            return
        body: dict[str, Any] = {
            "messageId": message_id,
            "outcome": outcome,
            "snapshotRequired": snapshot_required,
        }
        if event_id is not None:
            body["eventId"] = str(event_id)
        if error_code is not None:
            body["errorCode"] = error_code
        try:
            writer.write(
                encode_frame(json.dumps(body).encode("utf-8"), kind=FRAME_KIND_ACKNOWLEDGEMENT)
            )
        except Exception:
            _LOGGER.debug("detection_ack_send_failed", extra={"component": self.name})

    # --- Snapshot capture (IP-10 T-140/T-149, FS-08 §5) ----------------------------------------

    def _process_snapshot(self, item: _SnapshotQueueItem) -> None:
        if not self._snapshot_capture_enabled:
            # Defensive only — a Bridge respecting the shared kill switch (FS-08 §13) never sends
            # these while capture is disabled. Dropped silently, never treated as a protocol fault.
            return

        try:
            event_id = UUID(item.event_id)
        except ValueError:
            _LOGGER.warning(
                "snapshot_capture_rejected",
                extra={"component": self.name, "reason": "malformed_event_id"},
            )
            return

        assert self._snapshot_spool_path is not None  # noqa: S101 -- enforced by __init__
        assert self._snapshot_repository is not None  # noqa: S101 -- enforced by __init__

        usage = spool_usage_bytes(self._snapshot_spool_path)
        if usage + len(item.data) > self._snapshot_max_spool_bytes:
            self._spool_quota_skip_count += 1
            if (
                self._spool_quota_skip_count == 1
                or self._spool_quota_skip_count % _SPOOL_QUOTA_LOG_INTERVAL == 0
            ):
                _LOGGER.warning(
                    "snapshot_capture_spool_quota_exceeded",
                    extra={
                        "component": self.name,
                        "skipped_count": self._spool_quota_skip_count,
                    },
                )
            return  # capture only is skipped — detection/persistence is entirely unaffected

        try:
            _width, _height = validate_snapshot_bytes(
                item.data, max_file_bytes=self._snapshot_max_file_bytes
            )
        except SnapshotValidationError as exc:
            _LOGGER.warning(
                "snapshot_capture_rejected",
                extra={"component": self.name, "reason": exc.reason},
            )
            return

        sha256 = compute_sha256(item.data)
        try:
            local_path = write_snapshot_atomic(self._snapshot_spool_path, event_id, item.data)
        except OSError:
            _LOGGER.exception(
                "snapshot_capture_write_failed",
                extra={"component": self.name, "event_id": str(event_id)[:8]},
            )
            return

        try:
            self._snapshot_repository.create_captured(
                event_id=event_id,
                local_path=local_path,
                content_type="image/jpeg",
                size_bytes=len(item.data),
                sha256=sha256,
            )
        except SnapshotOutboxAlreadyExistsError:
            # Idempotent: a duplicate acknowledgement/snapshot for an already-captured EventId must
            # never write a second file record (FS-08 §4.4). The file itself was already
            # (over)written above with identical bytes for the same EventId — harmless.
            _LOGGER.debug("snapshot_capture_duplicate", extra={"component": self.name})
            return
        except Exception:
            _LOGGER.exception(
                "snapshot_outbox_persistence_failed",
                extra={"component": self.name, "event_id": str(event_id)[:8]},
            )
            return

        _LOGGER.info(
            "snapshot_captured",
            extra={
                "component": self.name,
                "event_id": str(event_id)[:8],
                "size_bytes": len(item.data),
            },
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
    camera_id_resolver: Callable[[int], UUID | None] | None = None,
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

    ``camera_id_resolver`` (FS-11 §9, IP-13 T-238) is ``None`` in the default/static mode
    (``settings.device_config_enabled=False``) — every detection keeps using the single static
    ``settings.detection_camera_id`` exactly as before. When server-driven Camera configuration is
    enabled, the caller (``main.py``) passes
    :meth:`~weapon_detection_agent.configuration.coordinator.DeviceConfigurationCoordinator.
    resolve_camera_id` here so each detection's Camera identity is resolved per-message from the
    Bridge-reported ``source_id`` instead.
    """
    if not settings.deepstream_enabled or not settings.detection_events_enabled:
        return ()

    class_names = load_class_names(resolve_class_labels_path(settings))

    snapshot_repository = None
    if settings.snapshot_capture_enabled:
        from weapon_detection_agent.persistence.snapshot_outbox_repository import (
            SnapshotOutboxRepository,
        )

        snapshot_repository = SnapshotOutboxRepository(paths.database_file)

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
            camera_id_resolver=camera_id_resolver,
            class_names=class_names,
            min_confidence=settings.detection_min_confidence,
            cooldown_tracker=DetectionCooldownTracker(
                cooldown_seconds=settings.detection_cooldown_seconds
            ),
            repository=DetectionEventRepository(paths.database_file),
            snapshot_capture_enabled=settings.snapshot_capture_enabled,
            snapshot_repository=snapshot_repository,
            snapshot_spool_path=settings.snapshot_spool_path,
            snapshot_max_file_bytes=settings.snapshot_max_file_bytes,
            snapshot_max_spool_bytes=settings.snapshot_max_spool_bytes,
        ),
    )
