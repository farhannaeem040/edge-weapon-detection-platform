"""DeepStream Bridge entry point (IP-07 T-88, FS-05 §4.3).

::

    python -m deepstream_bridge.main --socket-path <unix-socket-path> --config <deepstream-config-path>

Wires ``cli.py`` -> ``config.py`` -> ``pipeline.py``/``transport.py`` and runs the GLib main loop.
Distinguishes every failure category task item 11 requires (argument, configuration, missing
dependency, pipeline build, fatal bus error) with a distinct non-zero exit code, so
``DeepStreamProcessManager``'s existing unexpected-exit detection (IP-06 T-73) sees a real signal
either way. Transport/socket failures never reach this module — they are fully contained and
rate-limited inside ``transport.py`` (task item 11).

Shutdown order on SIGTERM/SIGINT or a clean pipeline stop (task item 10): pipeline state -> NULL,
GLib loop stopped (both inside ``BridgePipeline.run``/``shutdown``), then the transport worker is
stopped and its socket closed — the transport is always stopped *after* the pipeline, so no
in-flight probe callback can try to enqueue into an already-stopped transport.
"""

from __future__ import annotations

import logging
import signal
import sys
from typing import Sequence

from deepstream_bridge.cli import BridgeArgumentError, parse_args
from deepstream_bridge.config import BridgeConfig, load_bridge_config
from deepstream_bridge.errors import (
    BridgeConfigurationError,
    MissingGstRtspServerBindingsError,
    MissingGStreamerBindingsError,
    MissingPydsError,
    PipelineBusError,
    PipelineElementCreationError,
    PipelineLinkError,
)
from deepstream_bridge.pipeline import BridgePipeline
from deepstream_bridge.protocol import DEFAULT_QUEUE_CAPACITY
from deepstream_bridge.snapshot import SnapshotAcknowledgementHandler, SnapshotRendezvous
from deepstream_bridge.transport import TransportWorker

_LOGGER = logging.getLogger("deepstream_bridge.main")

EXIT_OK = 0
EXIT_ARGUMENT_ERROR = 2
EXIT_CONFIGURATION_ERROR = 3
EXIT_MISSING_DEPENDENCY = 4
EXIT_PIPELINE_ERROR = 5

_SHUTDOWN_SIGNALS: Sequence[int] = (signal.SIGTERM, signal.SIGINT)


class _ExtraFieldsFormatter(logging.Formatter):
    """Appends any ``extra={...}`` fields as ``key=value`` pairs so structured log calls (e.g.
    ``snapshot.py``'s ``snapshot_rendezvous_completed``) are actually observable in this Bridge's
    plain-text log; ``logging.basicConfig``'s default formatter silently drops ``extra`` fields."""

    _STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__.keys())

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            key: value for key, value in record.__dict__.items() if key not in self._STANDARD_ATTRS
        }
        if not extras:
            return base
        rendered = " ".join(f"{key}={value!r}" for key, value in sorted(extras.items()))
        return f"{base} {rendered}"


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_ExtraFieldsFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def run(argv: list[str] | None = None) -> int:
    """Run the Bridge to completion; returns the process exit code (never calls ``sys.exit``
    itself, so this is directly unit-testable)."""
    _configure_logging()

    try:
        args = parse_args(argv)
    except BridgeArgumentError as exc:
        _LOGGER.error("bridge_argument_error: %s", exc)
        return EXIT_ARGUMENT_ERROR
    except SystemExit as exc:
        # argparse's own --help/missing-required-argument path already printed usage and wants a
        # specific code; propagate it rather than mapping to one of ours.
        return int(exc.code or EXIT_ARGUMENT_ERROR)

    try:
        config: BridgeConfig = load_bridge_config(args.config_path)
    except BridgeConfigurationError as exc:
        _LOGGER.error("bridge_configuration_error: %s", exc)
        return EXIT_CONFIGURATION_ERROR

    transport = TransportWorker(socket_path=args.socket_path, queue_capacity=DEFAULT_QUEUE_CAPACITY)

    # IP-10 T-136: only constructed when snapshot capture is configured on — a disabled deployment
    # (the default) never builds the rendezvous/acknowledgement handler at all, matching the
    # pipeline's own "no tee/valve/snapshot branch" kill-switch behavior.
    if config.snapshot.enabled:
        snapshot_rendezvous = SnapshotRendezvous(
            max_retained=config.snapshot.max_retained_frames,
            ttl_seconds=config.snapshot.frame_ttl_ms / 1000.0,
        )
        transport.set_acknowledgement_callback(
            SnapshotAcknowledgementHandler(
                rendezvous=snapshot_rendezvous,
                send_snapshot=transport.enqueue_snapshot,
            )
        )
        send_snapshot = transport.enqueue_snapshot
    else:
        snapshot_rendezvous = None
        send_snapshot = None

    try:
        pipeline = BridgePipeline(
            config=config,
            enqueue=transport.enqueue,
            snapshot_rendezvous=snapshot_rendezvous,
            send_snapshot=send_snapshot,
        )
        pipeline.build()
    except (
        MissingGStreamerBindingsError,
        MissingPydsError,
        MissingGstRtspServerBindingsError,
    ) as exc:
        _LOGGER.error("bridge_missing_dependency: %s", exc)
        return EXIT_MISSING_DEPENDENCY
    except (PipelineElementCreationError, PipelineLinkError) as exc:
        _LOGGER.error("bridge_pipeline_build_failed: %s", exc)
        return EXIT_PIPELINE_ERROR

    transport.start()
    exit_code = EXIT_OK
    try:
        pipeline.run(shutdown_signals=_SHUTDOWN_SIGNALS)
    except PipelineBusError as exc:
        _LOGGER.error("bridge_pipeline_bus_error: %s", exc)
        exit_code = EXIT_PIPELINE_ERROR
    finally:
        # Pipeline first, transport second (module docstring: no in-flight probe callback can
        # enqueue into an already-stopped transport).
        pipeline.shutdown()
        transport.stop()

    return exit_code


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
