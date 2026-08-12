"""Unit tests for the ``WDA_SNAPSHOT_*`` settings (IP-10 T-146, FS-08 §13).

Mirrors ``test_settings.py``'s style: explicit constructor overrides, no environment mutation
required for these cross-field/bounds checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from weapon_detection_agent.config.settings import ConfigurationError, load_settings

VALID_URL = "http://backend.local:5230"


def _settings(**overrides: object):
    return load_settings(backend_base_url=VALID_URL, **overrides)


def test_snapshot_capture_and_upload_default_disabled() -> None:
    settings = _settings()

    assert settings.snapshot_capture_enabled is False
    assert settings.snapshot_upload_enabled is False


def test_snapshot_defaults_match_the_documented_values() -> None:
    settings = _settings()

    assert settings.snapshot_spool_path == Path("/opt/weapon-detection/snapshots")
    assert settings.snapshot_jpeg_quality == 85
    assert settings.snapshot_max_file_bytes == 5_242_880
    assert settings.snapshot_max_spool_bytes == 1_073_741_824
    assert settings.snapshot_upload_batch_size == 5
    assert settings.snapshot_upload_interval_seconds == 2.0
    assert settings.snapshot_upload_initial_backoff_seconds == 1.0
    assert settings.snapshot_upload_max_backoff_seconds == 60.0
    assert settings.snapshot_frame_ttl_milliseconds == 750
    assert settings.snapshot_max_retained_frames == 4


def test_snapshot_capture_enabled_requires_detection_events_enabled() -> None:
    with pytest.raises(ConfigurationError):
        _settings(
            snapshot_capture_enabled=True,
            detection_events_enabled=False,
        )


def test_snapshot_capture_enabled_with_detection_events_enabled_and_compatible_pipeline() -> None:
    settings = _settings(
        snapshot_capture_enabled=True,
        detection_events_enabled=True,
        deepstream_enabled=True,
        deepstream_executable_path="/opt/weapon-detection/deepstream-bridge/run.sh",
    )
    assert settings.snapshot_capture_enabled is True


def test_snapshot_upload_backoff_max_below_initial_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        _settings(
            snapshot_upload_initial_backoff_seconds=5.0,
            snapshot_upload_max_backoff_seconds=1.0,
        )


def test_snapshot_max_spool_bytes_below_max_file_bytes_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        _settings(snapshot_max_file_bytes=1000, snapshot_max_spool_bytes=500)


def test_snapshot_jpeg_quality_out_of_range_is_rejected() -> None:
    with pytest.raises(ConfigurationError):
        _settings(snapshot_jpeg_quality=101)
    with pytest.raises(ConfigurationError):
        _settings(snapshot_jpeg_quality=0)


def test_snapshot_max_file_bytes_must_be_positive() -> None:
    with pytest.raises(ConfigurationError):
        _settings(snapshot_max_file_bytes=0)


def test_snapshot_upload_batch_size_must_be_positive() -> None:
    with pytest.raises(ConfigurationError):
        _settings(snapshot_upload_batch_size=0)


def test_configuration_error_never_echoes_provided_values() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        _settings(snapshot_max_file_bytes=1000, snapshot_max_spool_bytes=500)

    assert "1000" not in str(excinfo.value)
    assert "500" not in str(excinfo.value)
