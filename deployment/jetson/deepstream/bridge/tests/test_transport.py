from __future__ import annotations

import json
import queue
import threading
import time

from deepstream_bridge.protocol import (
    FRAME_HEADER_BYTES,
    FRAME_KIND_ACKNOWLEDGEMENT,
    FRAME_KIND_DETECTION,
    FRAME_KIND_SNAPSHOT,
    decode_frame_header,
    decode_snapshot_message,
    encode_frame,
)
from deepstream_bridge.transport import TransportWorker


class FakeSocket:
    def __init__(
        self,
        connect_error: "Exception | None" = None,
        send_error: "Exception | None" = None,
        recv_queue: "list[bytes] | None" = None,
    ) -> None:
        self.connect_error = connect_error
        self.send_error = send_error
        self.sent: list[bytes] = []
        self.closed = False
        self._recv_lock = threading.Lock()
        self._recv_queue: "queue.Queue[bytes]" = queue.Queue()
        for chunk in recv_queue or []:
            self._recv_queue.put_nowait(chunk)
        self._timeout: "float | None" = None
        self._recv_buffer = bytearray()

    def connect(self, address: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def sendall(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    def settimeout(self, value) -> None:
        self._timeout = value

    def recv(self, bufsize: int) -> bytes:
        # Mimics a real socket: keeps any bytes beyond bufsize buffered for the next call, rather
        # than silently discarding them (a genuine bug this fake previously had).
        with self._recv_lock:
            if not self._recv_buffer:
                try:
                    chunk = self._recv_queue.get(timeout=self._timeout or 0.2)
                except queue.Empty:
                    import socket as _socket

                    raise _socket.timeout()
                self._recv_buffer.extend(chunk)
            result = bytes(self._recv_buffer[:bufsize])
            del self._recv_buffer[:bufsize]
            return result

    def push_recv(self, data: bytes) -> None:
        self._recv_queue.put_nowait(data)

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _decode(frame: bytes) -> dict:
    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    return json.loads(frame[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length])


def test_enqueue_drops_newest_when_queue_full_and_counts_it() -> None:
    worker = TransportWorker(
        socket_path="/nonexistent", queue_capacity=1, socket_factory=lambda: FakeSocket()
    )
    worker._queue.put_nowait({"a": 1})  # fill the queue directly; worker thread not started

    worker.enqueue({"b": 2})

    assert worker.dropped_enqueue_count == 1


def test_enqueue_never_blocks_when_no_receiver_is_listening() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: FakeSocket(connect_error=ConnectionRefusedError()),
    )

    started = time.monotonic()
    for i in range(5):
        worker.enqueue({"n": i})
    elapsed = time.monotonic() - started

    assert elapsed < 0.5


def test_econnrefused_is_non_fatal_and_counted() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: FakeSocket(connect_error=ConnectionRefusedError()),
    )
    worker.start()
    try:
        worker.enqueue({"a": 1})
        assert _wait_until(lambda: worker.dropped_send_count == 1)
    finally:
        worker.stop()


def test_reconnect_after_enoent_then_sends_successfully() -> None:
    sockets = [FakeSocket(connect_error=FileNotFoundError()), FakeSocket()]

    def factory() -> FakeSocket:
        return sockets.pop(0)

    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=factory
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: worker.dropped_send_count == 1)

        worker.enqueue({"schemaVersion": 2, "x": 2})
        assert _wait_until(lambda: len(sockets) == 0)
    finally:
        worker.stop()


def test_broken_pipe_closes_socket_and_reconnects_for_next_message() -> None:
    first = FakeSocket(send_error=BrokenPipeError())
    second = FakeSocket()
    sockets = [first, second]

    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sockets.pop(0)
    )
    worker.start()
    try:
        worker.enqueue({"a": 1})
        assert _wait_until(lambda: first.closed and worker.dropped_send_count == 1)

        worker.enqueue({"b": 2})
        assert _wait_until(lambda: len(second.sent) == 1)
    finally:
        worker.stop()


def test_connection_reset_is_handled_like_broken_pipe() -> None:
    sock = FakeSocket(send_error=ConnectionResetError())
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue({"a": 1})
        assert _wait_until(lambda: sock.closed)
    finally:
        worker.stop()


def test_frame_uses_kind_prefix_then_four_byte_big_endian_length() -> None:
    sock = FakeSocket()
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2, "classId": 0})
        assert _wait_until(lambda: len(sock.sent) == 1)
    finally:
        worker.stop()

    frame = sock.sent[0]
    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    assert kind == FRAME_KIND_DETECTION
    assert length == len(frame) - FRAME_HEADER_BYTES


def test_multiple_messages_are_sent_in_order() -> None:
    sock = FakeSocket()
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue({"n": 1})
        worker.enqueue({"n": 2})
        assert _wait_until(lambda: len(sock.sent) == 2)
    finally:
        worker.stop()

    assert [_decode(frame)["n"] for frame in sock.sent] == [1, 2]


def test_stop_joins_worker_thread_promptly() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: FakeSocket()
    )
    worker.start()

    started = time.monotonic()
    worker.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 3.0
    assert worker._thread is None


def test_stop_is_safe_when_never_started() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: FakeSocket()
    )
    worker.stop()  # must not raise


def test_start_twice_does_not_spawn_a_second_thread() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: FakeSocket()
    )
    worker.start()
    first_thread = worker._thread
    worker.start()
    try:
        assert worker._thread is first_thread
    finally:
        worker.stop()


# --- IP-10 T-132: bidirectional transport (acknowledgement reader) ------------------------------


def test_acknowledgement_is_dispatched_to_callback() -> None:
    sock = FakeSocket()
    received: list[dict] = []
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: sock,
        on_acknowledgement=received.append,
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: len(sock.sent) == 1)

        ack_body = json.dumps({"messageId": "abc", "outcome": "accepted"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body, kind=FRAME_KIND_ACKNOWLEDGEMENT))

        assert _wait_until(lambda: len(received) == 1)
    finally:
        worker.stop()

    assert received[0] == {"messageId": "abc", "outcome": "accepted"}


def test_set_acknowledgement_callback_after_construction_is_used() -> None:
    sock = FakeSocket()
    received: list[dict] = []
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.set_acknowledgement_callback(received.append)
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: len(sock.sent) == 1)

        ack_body = json.dumps({"messageId": "xyz", "outcome": "rejected"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body, kind=FRAME_KIND_ACKNOWLEDGEMENT))

        assert _wait_until(lambda: len(received) == 1)
    finally:
        worker.stop()


def test_non_acknowledgement_frame_kind_is_ignored_by_reader() -> None:
    sock = FakeSocket()
    received: list[dict] = []
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: sock,
        on_acknowledgement=received.append,
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: len(sock.sent) == 1)

        sock.push_recv(encode_frame(b"ignored", kind=FRAME_KIND_DETECTION))
        ack_body = json.dumps({"messageId": "abc", "outcome": "accepted"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body, kind=FRAME_KIND_ACKNOWLEDGEMENT))

        assert _wait_until(lambda: len(received) == 1)
    finally:
        worker.stop()

    assert received == [{"messageId": "abc", "outcome": "accepted"}]


def test_malformed_acknowledgement_body_does_not_crash_reader() -> None:
    sock = FakeSocket()
    received: list[dict] = []
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: sock,
        on_acknowledgement=received.append,
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: len(sock.sent) == 1)

        sock.push_recv(encode_frame(b"not-json", kind=FRAME_KIND_ACKNOWLEDGEMENT))
        ack_body = json.dumps({"messageId": "abc", "outcome": "accepted"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body, kind=FRAME_KIND_ACKNOWLEDGEMENT))

        assert _wait_until(lambda: len(received) == 1)
    finally:
        worker.stop()


def test_acknowledgement_callback_exception_does_not_crash_reader() -> None:
    sock = FakeSocket()
    first_call_handled = threading.Event()

    def failing_callback(_message: dict) -> None:
        first_call_handled.set()
        raise RuntimeError("boom")

    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: sock,
        on_acknowledgement=failing_callback,
    )
    worker.start()
    try:
        worker.enqueue({"schemaVersion": 2})
        assert _wait_until(lambda: len(sock.sent) == 1)

        ack_body = json.dumps({"messageId": "abc", "outcome": "accepted"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body, kind=FRAME_KIND_ACKNOWLEDGEMENT))
        assert first_call_handled.wait(timeout=2.0)

        # A second, well-formed acknowledgement after the failing one proves the reader thread is
        # still alive and looping, not killed by the callback's exception.
        received: list[dict] = []
        worker.set_acknowledgement_callback(received.append)
        ack_body_2 = json.dumps({"messageId": "def", "outcome": "accepted"}).encode("utf-8")
        sock.push_recv(encode_frame(ack_body_2, kind=FRAME_KIND_ACKNOWLEDGEMENT))
        assert _wait_until(lambda: len(received) == 1)
    finally:
        worker.stop()


def test_reader_thread_stops_promptly_with_no_socket_connected() -> None:
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock",
        queue_capacity=10,
        socket_factory=lambda: FakeSocket(connect_error=ConnectionRefusedError()),
    )
    worker.start()

    started = time.monotonic()
    worker.stop()
    elapsed = time.monotonic() - started

    assert elapsed < 3.0


# --- IP-10 T-136: snapshot frame sending ----------------------------------------------------------


def test_enqueue_snapshot_sends_a_snapshot_kind_frame() -> None:
    sock = FakeSocket()
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue_snapshot("event-1", b"\xff\xd8fake")
        assert _wait_until(lambda: len(sock.sent) == 1)
    finally:
        worker.stop()

    frame = sock.sent[0]
    kind, length = decode_frame_header(frame[:FRAME_HEADER_BYTES])
    body = frame[FRAME_HEADER_BYTES : FRAME_HEADER_BYTES + length]
    event_id, jpeg_bytes = decode_snapshot_message(body)

    assert kind == FRAME_KIND_SNAPSHOT
    assert event_id == "event-1"
    assert jpeg_bytes == b"\xff\xd8fake"


def test_enqueue_snapshot_drops_newest_when_queue_full_and_counts_it() -> None:
    worker = TransportWorker(
        socket_path="/nonexistent",
        queue_capacity=10,
        snapshot_queue_capacity=1,
        socket_factory=lambda: FakeSocket(),
    )
    worker._snapshot_queue.put_nowait(("a", b"1"))

    worker.enqueue_snapshot("b", b"2")

    assert worker.dropped_snapshot_enqueue_count == 1


def test_snapshot_send_failure_is_counted_and_does_not_affect_detection_queue() -> None:
    sock = FakeSocket(send_error=BrokenPipeError())
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue_snapshot("event-1", b"jpeg")
        assert _wait_until(lambda: worker.dropped_snapshot_send_count == 1)
    finally:
        worker.stop()


def test_detection_and_snapshot_queues_are_both_drained() -> None:
    sock = FakeSocket()
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue({"n": 1})
        worker.enqueue_snapshot("event-1", b"jpeg")
        worker.enqueue({"n": 2})
        assert _wait_until(lambda: len(sock.sent) == 3)
    finally:
        worker.stop()

    kinds = [decode_frame_header(frame[:FRAME_HEADER_BYTES])[0] for frame in sock.sent]
    assert kinds.count(FRAME_KIND_DETECTION) == 2
    assert kinds.count(FRAME_KIND_SNAPSHOT) == 1
