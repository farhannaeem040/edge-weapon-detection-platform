from __future__ import annotations

import json
import time

from deepstream_bridge.transport import TransportWorker


class FakeSocket:
    def __init__(
        self, connect_error: Exception | None = None, send_error: Exception | None = None
    ) -> None:
        self.connect_error = connect_error
        self.send_error = send_error
        self.sent: list[bytes] = []
        self.closed = False

    def connect(self, address: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def sendall(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

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
    length = int.from_bytes(frame[:4], "big")
    return json.loads(frame[4 : 4 + length])


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
        worker.enqueue({"schema_version": 1})
        assert _wait_until(lambda: worker.dropped_send_count == 1)

        worker.enqueue({"schema_version": 1, "x": 2})
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


def test_frame_uses_four_byte_big_endian_length_prefix() -> None:
    sock = FakeSocket()
    worker = TransportWorker(
        socket_path="/tmp/whatever.sock", queue_capacity=10, socket_factory=lambda: sock
    )
    worker.start()
    try:
        worker.enqueue({"schema_version": 1, "class_id": 0})
        assert _wait_until(lambda: len(sock.sent) == 1)
    finally:
        worker.stop()

    frame = sock.sent[0]
    length = int.from_bytes(frame[:4], "big")
    assert length == len(frame) - 4


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
