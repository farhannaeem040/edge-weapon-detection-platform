"""Bounded queue + Unix domain socket transport worker (IP-07 T-88, IP-10 T-132, FS-05 §4.4,
FS-08 §4.1, task items 7-8).

:class:`TransportWorker` owns every side of the boundary between the (real-time, never-blockable)
GStreamer pad-probe thread and the (best-effort, may block on I/O) Agent socket connection:

* :meth:`TransportWorker.enqueue` — called from the probe thread. Non-blocking by construction
  (``queue.Queue.put_nowait``): on a full queue it drops the *newest* item and counts it, logging at
  a bounded/aggregated rate, never once per frame (task item 7).
* :meth:`TransportWorker.enqueue_snapshot` (IP-10 T-136) — called from the acknowledgement-dispatch
  callback (itself invoked from the reader thread below). Same non-blocking, drop-newest,
  bounded/aggregated-logging contract, on its own separate bounded queue so a burst of snapshot
  sends can never starve or be starved by ordinary detection messages.
* A dedicated **sender** thread — never the GStreamer thread — that drains both queues (snapshot
  queue checked first, non-blocking; detection queue then polled with a short timeout) and lazily
  connects/reconnects to the Agent's socket, sending one length-prefixed frame per item (task item
  8). A message that cannot be sent (no listener, connection dropped mid-send) is dropped, not
  retried or buffered — "bounded documented policy" (task item 7/8): at most one send attempt per
  queued item, so the queues themselves are the only backlog and both are already capacity-bounded.
* A dedicated **reader** thread (IP-10 T-132, new) that parses framed acknowledgements
  (``FRAME_KIND_ACKNOWLEDGEMENT``) off the same connection and invokes a caller-supplied
  ``on_acknowledgement`` callback. Connection lifecycle (connect/reconnect/close) remains solely
  owned by the sender thread, exactly as before this change — the reader thread only ever reads
  from whatever socket is currently connected and tolerates it disappearing/erroring by backing off
  and re-checking, never itself attempting to connect or close the shared socket. This avoids a
  connect/close race between two threads that would otherwise both think they own reconnection.

Socket construction is injectable (``socket_factory``) so tests exercise ENOENT/ECONNREFUSED/
BrokenPipeError/partial-send/partial-recv handling with a fabricated socket object, never a real
AF_UNIX socket (this module still imports the real ``socket`` module for its production default, but
that default is never exercised by the offline test suite).

Never logs full payload content, JPEG bytes, or the socket path in a way that could be mistaken for
a credential — only counters and exception type names (task item 8: "do not log full payloads").
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Tuple

from deepstream_bridge.protocol import (
    FRAME_HEADER_BYTES,
    FRAME_KIND_ACKNOWLEDGEMENT,
    MAX_FRAME_BYTES,
    decode_acknowledgement,
    decode_frame_header,
    encode_message,
    encode_snapshot_message,
)

_LOGGER = logging.getLogger("deepstream_bridge.transport")

# Bounded so stop() always returns promptly even if the worker thread is blocked in a slow connect
# attempt when shutdown is requested.
_QUEUE_GET_TIMEOUT_SECONDS = 0.5
_THREAD_JOIN_TIMEOUT_SECONDS = 5.0

# The reader thread's own socket timeout (task item: the reader must never block indefinitely, so
# stop() stays bounded the same way the sender thread already is). Real sockets get this via
# settimeout(); the fake sockets tests inject implement the same call as a no-op or a controllable
# stub.
_READ_TIMEOUT_SECONDS = 0.5
_NO_SOCKET_POLL_SECONDS = 0.1

# Emit the first drop/failure immediately, then only every Nth occurrence — bounded/aggregated
# logging (task items 7-8), never one line per dropped/failed frame.
_LOG_INTERVAL = 50

AcknowledgementCallback = Callable[[dict], None]


class SocketLike(Protocol):
    """The minimal socket surface this module depends on — lets tests inject a fake."""

    def connect(self, address: str) -> None: ...

    def sendall(self, data: bytes) -> None:  # pragma: no cover - Protocol
        ...

    def recv(self, bufsize: int) -> bytes:  # pragma: no cover - Protocol
        ...

    def settimeout(self, value: Optional[float]) -> None:  # pragma: no cover - Protocol
        ...

    def close(self) -> None: ...


SocketFactory = Callable[[], SocketLike]


def _default_socket_factory() -> SocketLike:
    # socket.AF_UNIX exists only on POSIX (the Bridge's actual Jetson/Linux target); this module is
    # never exercised on Windows in production, only import-checked there (mypy sees Windows'
    # socket stubs, hence the two ignores).
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined,return-value]


class _ConnectionUnavailable(Exception):
    """Internal-only: raised by the reader loop's helpers when the current socket cannot be read
    from right now (no socket connected, or a benign timeout) — never escapes ``_reader_run``."""


class TransportWorker:
    """Owns the bounded producer queues and the background UDS sender/reader threads.

    Constructed once per Bridge run; :meth:`start`/:meth:`stop` bracket its lifetime, mirroring the
    pipeline's own start/stop bracketing in ``main.py`` so shutdown order is explicit and visible in
    one place rather than implied by object destruction.
    """

    def __init__(
        self,
        *,
        socket_path: str | Path,
        queue_capacity: int,
        socket_factory: SocketFactory = _default_socket_factory,
        snapshot_queue_capacity: int = 16,
        on_acknowledgement: Optional[AcknowledgementCallback] = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if snapshot_queue_capacity <= 0:
            raise ValueError("snapshot_queue_capacity must be positive")

        self._socket_path = str(socket_path)
        self._socket_factory = socket_factory
        self._on_acknowledgement = on_acknowledgement

        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=queue_capacity)
        self._snapshot_queue: "queue.Queue[Tuple[str, bytes]]" = queue.Queue(
            maxsize=snapshot_queue_capacity
        )

        self._stop_event = threading.Event()
        self._sender_thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None

        self._sock_lock = threading.Lock()
        self._sock: Optional[SocketLike] = None

        self._dropped_enqueue_count = 0
        self._dropped_send_count = 0
        self._dropped_snapshot_enqueue_count = 0
        self._dropped_snapshot_send_count = 0
        self._connected = False

    # --- Producer side (probe / acknowledgement-dispatch threads) --------------------------------

    def enqueue(self, payload: dict) -> None:
        """Non-blocking enqueue of a detection message (task item 6/7). Never raises, never blocks
        the caller."""
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped_enqueue_count += 1
            if self._dropped_enqueue_count == 1 or self._dropped_enqueue_count % _LOG_INTERVAL == 0:
                _LOGGER.warning(
                    "bridge_queue_full_dropped_newest",
                    extra={"dropped_count": self._dropped_enqueue_count},
                )

    def enqueue_snapshot(self, event_id: str, jpeg_bytes: bytes) -> None:
        """Non-blocking enqueue of a snapshot frame (IP-10 T-136). Same drop-newest,
        bounded/aggregated-logging contract as :meth:`enqueue`, on its own queue."""
        try:
            self._snapshot_queue.put_nowait((event_id, jpeg_bytes))
        except queue.Full:
            self._dropped_snapshot_enqueue_count += 1
            if (
                self._dropped_snapshot_enqueue_count == 1
                or self._dropped_snapshot_enqueue_count % _LOG_INTERVAL == 0
            ):
                _LOGGER.warning(
                    "bridge_snapshot_queue_full_dropped_newest",
                    extra={"dropped_count": self._dropped_snapshot_enqueue_count},
                )

    @property
    def dropped_enqueue_count(self) -> int:
        return self._dropped_enqueue_count

    @property
    def dropped_send_count(self) -> int:
        return self._dropped_send_count

    @property
    def dropped_snapshot_enqueue_count(self) -> int:
        return self._dropped_snapshot_enqueue_count

    @property
    def dropped_snapshot_send_count(self) -> int:
        return self._dropped_snapshot_send_count

    def set_acknowledgement_callback(self, callback: Optional[AcknowledgementCallback]) -> None:
        """Set/replace the acknowledgement callback after construction — lets callers build the
        callback (e.g. :class:`~deepstream_bridge.snapshot.SnapshotAcknowledgementHandler`) using
        this worker's own bound :meth:`enqueue_snapshot`, which naturally must exist before the
        callback that references it can be constructed."""
        self._on_acknowledgement = callback

    # --- Worker lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        if self._sender_thread is not None:
            return
        self._stop_event.clear()
        self._sender_thread = threading.Thread(
            target=self._sender_run, name="deepstream-bridge-transport-sender", daemon=True
        )
        self._sender_thread.start()
        self._reader_thread = threading.Thread(
            target=self._reader_run, name="deepstream-bridge-transport-reader", daemon=True
        )
        self._reader_thread.start()

    def stop(self) -> None:
        """Stop both worker threads and close the socket, discarding any still-queued items (task
        item 10: "drain or discard queued messages according to one documented bounded policy" —
        this worker's policy is discard, matching the Agent-side ``DetectionIngestHandler.stop()``'s
        own prompt-shutdown-over-completeness posture)."""
        self._stop_event.set()
        sender_thread, self._sender_thread = self._sender_thread, None
        reader_thread, self._reader_thread = self._reader_thread, None
        if sender_thread is not None:
            sender_thread.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
        if reader_thread is not None:
            reader_thread.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
        self._close_socket()

    @property
    def _thread(self) -> Optional[threading.Thread]:
        # Preserved for test/back-compat readability: "is the worker running" now means "is the
        # sender thread running" (the reader thread's lifecycle always mirrors it).
        return self._sender_thread

    # --- Sender thread body ---------------------------------------------------------------------

    def _sender_run(self) -> None:
        while not self._stop_event.is_set():
            if self._drain_snapshot_once():
                continue
            try:
                payload = self._queue.get(timeout=_QUEUE_GET_TIMEOUT_SECONDS)
            except queue.Empty:
                continue
            self._send(payload)

    def _drain_snapshot_once(self) -> bool:
        try:
            event_id, jpeg_bytes = self._snapshot_queue.get_nowait()
        except queue.Empty:
            return False
        self._send_snapshot(event_id, jpeg_bytes)
        return True

    def _send(self, payload: dict) -> None:
        if self._sock is None and not self._connect():
            self._count_dropped_send()
            return

        try:
            assert self._sock is not None
            self._sock.sendall(encode_message(payload))
        except (BrokenPipeError, ConnectionResetError, OSError):
            _LOGGER.warning("bridge_transport_send_failed")
            self._close_socket()
            self._count_dropped_send()

    def _send_snapshot(self, event_id: str, jpeg_bytes: bytes) -> None:
        if self._sock is None and not self._connect():
            self._count_dropped_snapshot_send()
            return

        try:
            assert self._sock is not None
            self._sock.sendall(encode_snapshot_message(event_id, jpeg_bytes))
        except (BrokenPipeError, ConnectionResetError, OSError):
            _LOGGER.warning("bridge_transport_snapshot_send_failed")
            self._close_socket()
            self._count_dropped_snapshot_send()

    def _connect(self) -> bool:
        sock = self._socket_factory()
        try:
            sock.connect(self._socket_path)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            sock.close()
            if self._connected:
                _LOGGER.debug(
                    "bridge_transport_connect_failed", extra={"error_type": type(exc).__name__}
                )
            self._connected = False
            return False

        try:
            sock.settimeout(_READ_TIMEOUT_SECONDS)
        except (AttributeError, OSError):
            pass

        with self._sock_lock:
            self._sock = sock
        self._connected = True
        _LOGGER.info("bridge_transport_connected")
        return True

    def _close_socket(self) -> None:
        with self._sock_lock:
            sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            finally:
                pass
            if self._connected:
                _LOGGER.info("bridge_transport_disconnected")
            self._connected = False

    def _count_dropped_send(self) -> None:
        self._dropped_send_count += 1
        if self._dropped_send_count == 1 or self._dropped_send_count % _LOG_INTERVAL == 0:
            _LOGGER.warning(
                "bridge_transport_dropped", extra={"dropped_count": self._dropped_send_count}
            )

    def _count_dropped_snapshot_send(self) -> None:
        self._dropped_snapshot_send_count += 1
        if (
            self._dropped_snapshot_send_count == 1
            or self._dropped_snapshot_send_count % _LOG_INTERVAL == 0
        ):
            _LOGGER.warning(
                "bridge_transport_snapshot_dropped",
                extra={"dropped_count": self._dropped_snapshot_send_count},
            )

    # --- Reader thread body (IP-10 T-132) ---------------------------------------------------------

    def _reader_run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._read_one_frame()
            except _ConnectionUnavailable:
                self._stop_event.wait(_NO_SOCKET_POLL_SECONDS)

    def _read_one_frame(self) -> None:
        sock = self._current_sock()
        header = self._recv_exact(sock, FRAME_HEADER_BYTES)
        kind, length = decode_frame_header(header)

        if length > MAX_FRAME_BYTES:
            _LOGGER.warning("bridge_transport_ack_frame_oversized", extra={"length": length})
            # Cannot safely resynchronize mid-stream — drop this connection; the sender thread
            # reconnects lazily on its next send, same recovery path as any other transport error.
            self._close_socket()
            raise _ConnectionUnavailable()

        body = self._recv_exact(sock, length)

        if kind != FRAME_KIND_ACKNOWLEDGEMENT:
            return  # not an acknowledgement — ignore defensively, never raise

        try:
            message = decode_acknowledgement(body)
        except (ValueError, UnicodeDecodeError):
            _LOGGER.warning("bridge_transport_ack_decode_failed")
            return

        if self._on_acknowledgement is not None:
            try:
                self._on_acknowledgement(message)
            except Exception:  # noqa: BLE001 - a callback fault must never kill the reader thread
                _LOGGER.exception("bridge_transport_acknowledgement_callback_failed")

    def _current_sock(self) -> SocketLike:
        with self._sock_lock:
            sock = self._sock
        if sock is None:
            raise _ConnectionUnavailable()
        return sock

    def _recv_exact(self, sock: SocketLike, size: int) -> bytes:
        if size == 0:
            return b""
        chunks = bytearray()
        while len(chunks) < size:
            try:
                chunk = sock.recv(size - len(chunks))
            except (socket.timeout, OSError, AttributeError):
                raise _ConnectionUnavailable() from None
            if not chunk:
                raise _ConnectionUnavailable()  # peer closed mid-frame
            chunks.extend(chunk)
        return bytes(chunks)
