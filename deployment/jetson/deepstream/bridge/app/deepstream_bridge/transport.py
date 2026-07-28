"""Bounded queue + Unix domain socket transport worker (IP-07 T-88, FS-05 §4.4, task items 7-8).

:class:`TransportWorker` owns both halves of the boundary between the (real-time, never-blockable)
GStreamer pad-probe thread and the (best-effort, may block on I/O) Agent socket connection:

* :meth:`TransportWorker.enqueue` — called from the probe thread. Non-blocking by construction
  (``queue.Queue.put_nowait``): on a full queue it drops the *newest* item and counts it, logging at
  a bounded/aggregated rate, never once per frame (task item 7).
* A dedicated worker thread — never the GStreamer thread — that drains the queue and lazily
  connects/reconnects to the Agent's socket, sending one length-prefixed JSON frame per item (task
  item 8). A message that cannot be sent (no listener, connection dropped mid-send) is dropped, not
  retried or buffered — "bounded documented policy" (task item 7/8): at most one send attempt per
  queued item, so the queue itself is the only backlog and it is already capacity-bounded.

Socket construction is injectable (``socket_factory``) so tests exercise ENOENT/ECONNREFUSED/
BrokenPipeError/partial-send handling with a fabricated socket object, never a real AF_UNIX socket
(this module still imports the real ``socket`` module for its production default, but that default
is never exercised by the offline test suite).

Never logs full payload content or the socket path in a way that could be mistaken for a credential
— only counters and exception type names (task item 8: "do not log full payloads").
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from pathlib import Path
from typing import Any, Callable, Protocol

from deepstream_bridge.protocol import encode_message

_LOGGER = logging.getLogger("deepstream_bridge.transport")

# Bounded so stop() always returns promptly even if the worker thread is blocked in a slow connect
# attempt when shutdown is requested.
_QUEUE_GET_TIMEOUT_SECONDS = 0.5
_THREAD_JOIN_TIMEOUT_SECONDS = 5.0

# Emit the first drop/failure immediately, then only every Nth occurrence — bounded/aggregated
# logging (task items 7-8), never one line per dropped/failed frame.
_LOG_INTERVAL = 50


class SocketLike(Protocol):
    """The minimal socket surface this module depends on — lets tests inject a fake."""

    def connect(self, address: str) -> None: ...

    def sendall(self, data: bytes) -> None:  # pragma: no cover - Protocol
        ...

    def close(self) -> None: ...


SocketFactory = Callable[[], SocketLike]


def _default_socket_factory() -> SocketLike:
    # socket.AF_UNIX exists only on POSIX (the Bridge's actual Jetson/Linux target); this module is
    # never exercised on Windows in production, only import-checked there (mypy sees Windows'
    # socket stubs, hence the two ignores).
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined,return-value]


class TransportWorker:
    """Owns the bounded producer queue and the background UDS sender thread.

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
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")

        self._socket_path = str(socket_path)
        self._socket_factory = socket_factory
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_capacity)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: SocketLike | None = None

        self._dropped_enqueue_count = 0
        self._dropped_send_count = 0
        self._connected = False

    # --- Producer side (probe thread) --------------------------------------------------------

    def enqueue(self, payload: dict[str, Any]) -> None:
        """Non-blocking enqueue (task item 6/7). Never raises, never blocks the caller."""
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped_enqueue_count += 1
            if self._dropped_enqueue_count == 1 or self._dropped_enqueue_count % _LOG_INTERVAL == 0:
                _LOGGER.warning(
                    "bridge_queue_full_dropped_newest",
                    extra={"dropped_count": self._dropped_enqueue_count},
                )

    @property
    def dropped_enqueue_count(self) -> int:
        return self._dropped_enqueue_count

    @property
    def dropped_send_count(self) -> int:
        return self._dropped_send_count

    # --- Worker lifecycle ----------------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="deepstream-bridge-transport", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the worker thread and close the socket, discarding any still-queued items (task
        item 10: "drain or discard queued messages according to one documented bounded policy" —
        this worker's policy is discard, matching the Agent-side ``DetectionIngestHandler.stop()``'s
        own prompt-shutdown-over-completeness posture)."""
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
        self._close_socket()

    # --- Worker thread body ---------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._queue.get(timeout=_QUEUE_GET_TIMEOUT_SECONDS)
            except queue.Empty:
                continue
            self._send(payload)

    def _send(self, payload: dict[str, Any]) -> None:
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

        self._sock = sock
        self._connected = True
        _LOGGER.info("bridge_transport_connected")
        return True

    def _close_socket(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
            if self._connected:
                _LOGGER.info("bridge_transport_disconnected")
            self._connected = False

    def _count_dropped_send(self) -> None:
        self._dropped_send_count += 1
        if self._dropped_send_count == 1 or self._dropped_send_count % _LOG_INTERVAL == 0:
            _LOGGER.warning(
                "bridge_transport_dropped", extra={"dropped_count": self._dropped_send_count}
            )
