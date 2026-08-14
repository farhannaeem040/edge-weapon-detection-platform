from __future__ import annotations

import pytest

from deepstream_bridge.cli import BridgeArgumentError, parse_args, validate_arguments


def test_valid_arguments_are_accepted(tmp_path) -> None:
    config = tmp_path / "deepstream-app.txt"
    config.write_text("[application]\n", encoding="utf-8")

    args = validate_arguments(socket_path=str(tmp_path / "detection.sock"), config_path=str(config))

    assert args.config_path == config
    assert args.socket_path.name == "detection.sock"


def test_missing_socket_parent_directory_rejected(tmp_path) -> None:
    config = tmp_path / "c.txt"
    config.write_text("x", encoding="utf-8")

    with pytest.raises(BridgeArgumentError):
        validate_arguments(
            socket_path=str(tmp_path / "does-not-exist" / "detection.sock"),
            config_path=str(config),
        )


def test_missing_config_file_rejected(tmp_path) -> None:
    with pytest.raises(BridgeArgumentError):
        validate_arguments(
            socket_path=str(tmp_path / "detection.sock"),
            config_path=str(tmp_path / "missing.txt"),
        )


def test_config_path_that_is_a_directory_rejected(tmp_path) -> None:
    directory = tmp_path / "a-directory"
    directory.mkdir()

    with pytest.raises(BridgeArgumentError):
        validate_arguments(socket_path=str(tmp_path / "detection.sock"), config_path=str(directory))


def test_parse_args_requires_both_arguments(tmp_path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--socket-path", str(tmp_path / "s.sock")])

    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_happy_path(tmp_path) -> None:
    config = tmp_path / "c.txt"
    config.write_text("x", encoding="utf-8")

    args = parse_args(["--socket-path", str(tmp_path / "s.sock"), "--config", str(config)])

    assert args.config_path == config
