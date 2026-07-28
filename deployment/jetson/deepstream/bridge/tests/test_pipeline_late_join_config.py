"""Configuration tests for the T-94 late-join/stale-OSD fix (extends T-92's idr-interval mitigation).

Root cause (T-94 investigation, see deployment/jetson/deepstream/README.md "T-94"): the *deployed*
Bridge was found to be running a build that predates even T-92's idrinterval fix (encoder defaulting
to nvv4l2h264enc's own 256-frame/~8.5s IDR interval), and neither the deployed nor the repository
build ever set h264parse's own config-interval or the encoder's insert-sps-pps — together meaning a
late-joining client could wait up to one full (undeployed-default) IDR interval with no in-band
SPS/PPS to decode against, producing a sustained "non-existing PPS"/"no frame" loop. This file proves
the four new properties parse/validate correctly; test_config.py's existing idr-interval tests are
unchanged and still cover that property.

Never logs/echoes RTSP credentials (module docstring policy, unchanged) — none of these values could
ever contain one; error-message assertions below confirm the messages stay scoped to the invalid
value and property name only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepstream_bridge.config import load_bridge_config
from deepstream_bridge.errors import BridgeConfigurationError

from test_config import _base_app_config, _write  # noqa: E402 - shared test fixtures, not production code


# --- Defaults (no [bridge-rtsp-out] section) ----------------------------------------------------


def test_iframe_interval_defaults_to_30(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.iframe_interval == 30


def test_insert_sps_pps_defaults_to_true(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.insert_sps_pps is True


def test_h264parse_config_interval_defaults_to_negative_one(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.h264parse_config_interval == -1


def test_rtph264pay_config_interval_defaults_to_negative_one(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.rtph264pay_config_interval == -1


# --- Explicit values parse correctly --------------------------------------------------------------


def test_explicit_iframe_interval_is_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\niframe-interval=60\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.iframe_interval == 60


def test_explicit_insert_sps_pps_false_is_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\ninsert-sps-pps=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.insert_sps_pps is False


def test_explicit_h264parse_config_interval_is_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nh264parse-config-interval=5\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.h264parse_config_interval == 5


def test_explicit_rtph264pay_config_interval_is_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nrtph264pay-config-interval=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.rtph264pay_config_interval == 0


# --- Frame-interval validation (iframe-interval; mirrors test_config.py's idr-interval coverage) ---


def test_iframe_interval_zero_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\niframe-interval=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="iframe-interval"):
        load_bridge_config(app_config)


def test_iframe_interval_negative_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\niframe-interval=-1\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="iframe-interval"):
        load_bridge_config(app_config)


def test_iframe_interval_non_integer_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\niframe-interval=30.5\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="iframe-interval"):
        load_bridge_config(app_config)


def test_iframe_interval_beyond_upper_bound_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\niframe-interval=999999\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="iframe-interval"):
        load_bridge_config(app_config)


# --- config-interval validation (both h264parse and rtph264pay share the real element's -1..3600) --


@pytest.mark.parametrize("key", ["h264parse-config-interval", "rtph264pay-config-interval"])
def test_config_interval_non_integer_is_rejected(tmp_path: Path, key: str) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = f"\n[bridge-rtsp-out]\nenable=1\n{key}=fast\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match=key):
        load_bridge_config(app_config)


@pytest.mark.parametrize("key", ["h264parse-config-interval", "rtph264pay-config-interval"])
def test_config_interval_below_minimum_is_rejected(tmp_path: Path, key: str) -> None:
    """-2 is not a value the real GStreamer element supports (range is -1..3600) — must not be
    silently coerced to -1."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = f"\n[bridge-rtsp-out]\nenable=1\n{key}=-2\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match=key):
        load_bridge_config(app_config)


@pytest.mark.parametrize("key", ["h264parse-config-interval", "rtph264pay-config-interval"])
def test_config_interval_above_maximum_is_rejected(tmp_path: Path, key: str) -> None:
    """3601 exceeds the real GStreamer element's documented range (-1..3600)."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = f"\n[bridge-rtsp-out]\nenable=1\n{key}=3601\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match=key):
        load_bridge_config(app_config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("h264parse-config-interval", -1),
        ("h264parse-config-interval", 0),
        ("h264parse-config-interval", 3600),
        ("rtph264pay-config-interval", -1),
        ("rtph264pay-config-interval", 0),
        ("rtph264pay-config-interval", 3600),
    ],
)
def test_config_interval_boundary_values_are_accepted(tmp_path: Path, key: str, value: int) -> None:
    """-1 ("repeat with every IDR"), 0 ("disabled"), and the documented upper bound 3600 are all
    real, supported values of the GStreamer element and must parse without error."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = f"\n[bridge-rtsp-out]\nenable=1\n{key}={value}\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    actual = (
        config.rtsp_out.h264parse_config_interval
        if key == "h264parse-config-interval"
        else config.rtsp_out.rtph264pay_config_interval
    )
    assert actual == value


# --- Error messages never expose RTSP credentials ---------------------------------------------------


def test_error_message_does_not_expose_rtsp_credentials(tmp_path: Path) -> None:
    """The [source0] uri= (which could in principle carry rtsp:// credentials in a future
    deployment) must never appear in a T-94 validation error message — these errors only ever
    describe the invalid bridge-rtsp-out value/key, never the source configuration."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = """
[source0]
type=4
uri=rtsp://produser:s3cr3t@camera.example/stream
gpu-id=0

[streammux]
batch-size=1
batched-push-timeout=40000
width=1920
height=1080
live-source=1
enable-padding=0
nvbuf-memory-type=0
gpu-id=0

[primary-gie]
enable=1
gpu-id=0
gie-unique-id=1
config-file={infer}

[osd]
gpu-id=0

[bridge-rtsp-out]
enable=1
iframe-interval=-5
""".format(infer=infer)
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    with pytest.raises(BridgeConfigurationError) as exc_info:
        load_bridge_config(app_config)

    assert "s3cr3t" not in str(exc_info.value)
    assert "produser" not in str(exc_info.value)
