"""Tests for the production cutover preflight CLI (IP-07 T-90, task item 11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from weapon_detection_agent.detection.cutover_preflight import (
    EXIT_OK,
    EXIT_PROFILE_INVALID,
    EXIT_SETTINGS_INVALID,
    run,
)

BRIDGE_RUN_SH = "/opt/weapon-detection/deepstream-bridge/run.sh"


def _set_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    monkeypatch.setenv("WDA_BACKEND_BASE_URL", "http://backend.example.invalid:8080")
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_events_disabled_passes_without_checking_the_profile(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch)

    assert run([]) == EXIT_OK
    out = capsys.readouterr().out
    assert "PREFLIGHT_OK settings" in out
    assert "profile: skipped" in out


def test_incompatible_settings_combination_fails_with_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(
        monkeypatch,
        WDA_DEEPSTREAM_ENABLED="true",
        WDA_DETECTION_EVENTS_ENABLED="true",
        WDA_DEEPSTREAM_EXECUTABLE_PATH="/usr/bin/deepstream-app",
    )

    assert run([]) == EXIT_SETTINGS_INVALID
    err = capsys.readouterr().err
    assert "PREFLIGHT_FAIL settings" in err
    assert "WDA_DETECTION_EVENTS_ENABLED" in err


def test_valid_settings_but_missing_profile_fails_with_exit_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(
        monkeypatch,
        WDA_ROOT_PATH=str(tmp_path / "weapon-detection"),
        WDA_DEEPSTREAM_ENABLED="true",
        WDA_DETECTION_EVENTS_ENABLED="true",
        WDA_DEEPSTREAM_EXECUTABLE_PATH=BRIDGE_RUN_SH,
    )

    assert run([]) == EXIT_PROFILE_INVALID
    err = capsys.readouterr().err
    assert "PREFLIGHT_FAIL profile" in err


def test_fully_valid_configuration_passes_with_exit_0(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile_dir = (
        tmp_path / "weapon-detection" / "config" / "deepstream" / "profiles" / "yolov4-fp16"
    )
    profile_dir.mkdir(parents=True)
    (profile_dir / "infer-config.txt").write_text("[property]\n", encoding="utf-8")
    (profile_dir / "labels.txt").write_text("gun\nknife\n", encoding="utf-8")
    engine_dir = tmp_path / "weapon-detection" / "models" / "yolov4-fp16"
    engine_dir.mkdir(parents=True)
    (engine_dir / "model.engine").write_bytes(b"\x00")

    _set_env(
        monkeypatch,
        WDA_ROOT_PATH=str(tmp_path / "weapon-detection"),
        WDA_DEEPSTREAM_ENABLED="true",
        WDA_DETECTION_EVENTS_ENABLED="true",
        WDA_DEEPSTREAM_EXECUTABLE_PATH=BRIDGE_RUN_SH,
    )

    assert run([]) == EXIT_OK
    out = capsys.readouterr().out
    assert "PREFLIGHT_OK profile: 2 class(es) resolved" in out


def test_never_prints_the_activation_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_env(monkeypatch, WDA_ACTIVATION_KEY="deviceKeyId123.Sup3rSecretPassw0rd!")

    run([])

    captured = capsys.readouterr()
    assert "Sup3rSecretPassw0rd" not in captured.out
    assert "Sup3rSecretPassw0rd" not in captured.err
