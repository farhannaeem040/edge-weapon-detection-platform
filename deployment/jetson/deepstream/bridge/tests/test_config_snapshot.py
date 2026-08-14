"""Configuration tests for ``[bridge-snapshot]`` (IP-10 T-137, FS-08 §3/§9/§13).

Same style/rigor as ``test_config.py``'s ``[bridge-rtsp-out]`` coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepstream_bridge.config import load_bridge_config
from deepstream_bridge.errors import BridgeConfigurationError
from test_config import _base_app_config, _write  # noqa: E402 - shared test fixtures


def test_snapshot_disabled_by_default_without_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.snapshot.enabled is False


def test_snapshot_defaults_without_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.snapshot.frame_ttl_ms == 3000
    assert config.snapshot.max_retained_frames == 32
    assert config.snapshot.jpeg_quality == 85


def test_snapshot_can_be_explicitly_enabled(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nenable=1\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.snapshot.enabled is True


def test_snapshot_section_present_but_not_enabled_stays_disabled(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nframe-ttl-ms=5000\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.snapshot.enabled is False
    assert config.snapshot.frame_ttl_ms == 5000


def test_snapshot_explicit_values_are_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = """
[bridge-snapshot]
enable=1
frame-ttl-ms=4000
max-retained-frames=64
jpeg-quality=90
"""
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.snapshot.enabled is True
    assert config.snapshot.frame_ttl_ms == 4000
    assert config.snapshot.max_retained_frames == 64
    assert config.snapshot.jpeg_quality == 90


def test_snapshot_disabling_rtsp_out_settings_are_unaffected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = """
[bridge-rtsp-out]
enable=1
bitrate=2000000

[bridge-snapshot]
enable=1
"""
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.snapshot.enabled is True
    assert config.rtsp_out.bitrate == 2000000


@pytest.mark.parametrize("key", ["frame-ttl-ms", "max-retained-frames", "jpeg-quality"])
def test_snapshot_non_integer_value_is_rejected(tmp_path: Path, key: str) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = f"\n[bridge-snapshot]\nenable=1\n{key}=fast\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match=key):
        load_bridge_config(app_config)


def test_snapshot_jpeg_quality_zero_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nenable=1\njpeg-quality=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="jpeg-quality"):
        load_bridge_config(app_config)


def test_snapshot_jpeg_quality_above_100_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nenable=1\njpeg-quality=101\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="jpeg-quality"):
        load_bridge_config(app_config)


def test_snapshot_frame_ttl_ms_zero_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nenable=1\nframe-ttl-ms=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="frame-ttl-ms"):
        load_bridge_config(app_config)


def test_snapshot_max_retained_frames_zero_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-snapshot]\nenable=1\nmax-retained-frames=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="max-retained-frames"):
        load_bridge_config(app_config)
