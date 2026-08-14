"""AgentSettings incompatible-configuration preflight (IP-07 T-90, FS-05 §4.3/§4.6 requirement 6,
task item 2).

Covers the pure, I/O-free settings-combination cases from the task's required-case list. The two
filesystem-level cases ("executable missing", "executable not executable", "Bridge config missing
or unreadable") are deliberately **not** checked here — ``AgentSettings`` performs no filesystem I/O
by design (its own field comments document this), so those live in the separate, I/O-performing
production preflight command (``deployment/jetson/deepstream/bridge/preflight-cutover.sh`` and
``weapon_detection_agent.detection.cutover_preflight``, T-90) instead. See
``test_cutover_preflight.py`` for those.
"""

from __future__ import annotations

import pytest

from weapon_detection_agent.config.settings import (
    DEFAULT_DEEPSTREAM_EXECUTABLE_PATH,
    ConfigurationError,
    load_settings,
)

VALID_URL = "http://backend.example.invalid:8080"
BRIDGE_RUN_SH = "/opt/weapon-detection/deepstream-bridge/run.sh"


def _load(**overrides: object):
    return load_settings(backend_base_url=VALID_URL, **overrides)


def test_deepstream_false_events_false_is_valid() -> None:
    settings = _load(deepstream_enabled=False, detection_events_enabled=False)
    assert settings.deepstream_enabled is False
    assert settings.detection_events_enabled is False


def test_deepstream_true_events_false_reference_binary_is_valid() -> None:
    settings = _load(
        deepstream_enabled=True,
        detection_events_enabled=False,
        deepstream_executable_path=DEFAULT_DEEPSTREAM_EXECUTABLE_PATH,
    )
    assert settings.deepstream_executable_path == DEFAULT_DEEPSTREAM_EXECUTABLE_PATH


def test_deepstream_true_events_true_bridge_run_sh_is_valid() -> None:
    settings = _load(
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path=BRIDGE_RUN_SH,
    )
    assert settings.detection_events_enabled is True
    assert settings.deepstream_executable_path.as_posix() == BRIDGE_RUN_SH


def test_deepstream_true_events_true_reference_binary_is_invalid() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        _load(
            deepstream_enabled=True,
            detection_events_enabled=True,
            deepstream_executable_path=DEFAULT_DEEPSTREAM_EXECUTABLE_PATH,
        )
    message = str(excinfo.value)
    assert "WDA_DETECTION_EVENTS_ENABLED" in message
    assert "WDA_DEEPSTREAM_EXECUTABLE_PATH" in message


def test_deepstream_false_events_true_is_invalid() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        _load(
            deepstream_enabled=False,
            detection_events_enabled=True,
            deepstream_executable_path=BRIDGE_RUN_SH,
        )
    message = str(excinfo.value)
    assert "WDA_DETECTION_EVENTS_ENABLED" in message
    assert "WDA_DEEPSTREAM_ENABLED" in message


def test_deepstream_false_events_true_reference_binary_reports_both_problems() -> None:
    """Both violated rules are named in one error when the configuration breaks both at once —
    proves the validator doesn't stop at the first failure and hide the second."""
    with pytest.raises(ConfigurationError) as excinfo:
        _load(
            deepstream_enabled=False,
            detection_events_enabled=True,
            deepstream_executable_path=DEFAULT_DEEPSTREAM_EXECUTABLE_PATH,
        )
    message = str(excinfo.value)
    assert "WDA_DEEPSTREAM_ENABLED" in message
    assert "WDA_DEEPSTREAM_EXECUTABLE_PATH" in message


def test_events_disabled_is_always_valid_regardless_of_executable_path() -> None:
    """A disabled feature is never inconsistent with anything (matches every other kill-switch in
    this codebase's own documented posture)."""
    settings = _load(
        deepstream_enabled=False,
        detection_events_enabled=False,
        deepstream_executable_path=DEFAULT_DEEPSTREAM_EXECUTABLE_PATH,
    )
    assert settings.detection_events_enabled is False

    settings2 = _load(
        deepstream_enabled=True,
        detection_events_enabled=False,
        deepstream_executable_path=BRIDGE_RUN_SH,
    )
    assert settings2.detection_events_enabled is False


def test_any_non_default_executable_path_is_accepted_not_just_run_sh() -> None:
    """Explicit-value comparison against the one literal default, never filename-pattern guesswork
    (task item 2) — any other path is accepted, including one that doesn't literally say
    'run.sh'."""
    settings = _load(
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path="/opt/weapon-detection/deepstream-bridge/launcher",
    )
    assert settings.detection_events_enabled is True
