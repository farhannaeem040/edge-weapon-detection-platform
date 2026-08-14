"""CLI argument parsing and validation (IP-07 T-88, FS-05 §4.3).

The Bridge's real entry point: ``python -m deepstream_bridge.main --socket-path PATH --config
PATH`` — both required, an explicit named CLI rather than a positional ``-c`` argument (that
positional shape is ``run.sh``'s job, T-89, translating ``DeepStreamProcessManager``'s fixed
``[executable, "-c", config]`` argv into this one; this module is never given ``-c`` directly in
production).

Never logs or echoes credentials/RTSP URLs — neither argument can carry one (the socket path and
the DeepStream config *path* are filesystem locations, not URIs), and this module never reads the
config's contents, so there is nothing sensitive here to redact in the first place.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


class BridgeArgumentError(ValueError):
    """An invalid or missing CLI argument, or an unusable path (item 2: "validate paths clearly")."""


@dataclass(frozen=True)
class BridgeArguments:
    """Validated, typed CLI arguments."""

    socket_path: Path
    config_path: Path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m deepstream_bridge.main",
        description=(
            "DeepStream Bridge: extracts real NvDsObjectMeta and publishes raw detection facts "
            "to the Agent's Unix domain socket (IP-07 T-88)."
        ),
    )
    parser.add_argument(
        "--socket-path",
        required=True,
        dest="socket_path",
        help="Path of the Agent's detection ingest Unix domain socket.",
    )
    parser.add_argument(
        "--config",
        required=True,
        dest="config_path",
        help="Path of the DeepStream application config (deepstream-app.txt-equivalent).",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> BridgeArguments:
    """Parse and validate argv. ``argparse`` itself exits(2) with a usage message for a missing or
    unrecognized argument (task requirement: non-zero exit for invalid arguments)."""
    parser = build_arg_parser()
    namespace = parser.parse_args(argv)
    return validate_arguments(socket_path=namespace.socket_path, config_path=namespace.config_path)


def validate_arguments(*, socket_path: str, config_path: str) -> BridgeArguments:
    """Validate raw CLI strings into typed, existence-checked paths.

    - ``socket_path``'s parent directory must already exist. The Bridge never creates the Agent's
      ``runtime/`` directory — provisioning that layout is exclusively the Agent's own job (IP-07
      T-82); the Bridge only ever connects to a socket a running/stopped Agent owns.
    - ``config_path`` must exist and be a regular, readable file.

    Raises :class:`BridgeArgumentError` with a clear, path-naming message on either failure —
    never silently defaults or falls back.
    """
    socket = Path(socket_path)
    if not socket.parent.is_dir():
        raise BridgeArgumentError(f"--socket-path parent directory does not exist: {socket.parent}")

    config = Path(config_path)
    if not config.is_file():
        raise BridgeArgumentError(f"--config path is not a readable file: {config}")

    return BridgeArguments(socket_path=socket, config_path=config)
