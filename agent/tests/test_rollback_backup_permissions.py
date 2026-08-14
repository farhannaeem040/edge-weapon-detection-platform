"""Tests for create-rollback-backup.sh / restore-rollback-backup.sh (IP-07 T-91 hardening, Part B).

Proves the exact regression the T-91 incident found: creating (or restoring) a rollback backup
must never alter the LIVE deployed files' ownership/mode, and the backup directory boundary itself
is the only place a blanket root:root/0700 applies.

Requires ``bash``/``cp``/``rsync``/``stat`` on PATH — skipped otherwise (Linux/the Jetson target).
Ownership (``chown root:root``) is only asserted when the test itself runs as root (e.g. on the
Jetson via sudo); a non-root CI/dev run still exercises every mode/content-preservation assertion.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_JETSON_DEPLOY_DIR = Path(__file__).resolve().parent.parent.parent / "deployment" / "jetson"
_CREATE_SCRIPT = _JETSON_DEPLOY_DIR / "create-rollback-backup.sh"
_RESTORE_SCRIPT = _JETSON_DEPLOY_DIR / "restore-rollback-backup.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("rsync") is None or os.name != "posix",
    reason="requires bash/rsync on a POSIX system (available on the Jetson/Linux target)",
)

_SECRET_FRAGMENT = "Sup3rSecretBackendToken"


def _make_fake_live_tree(tmp_path: Path) -> dict[str, Path]:
    env_file = tmp_path / "live" / "agent.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(f"WDA_BACKEND_BASE_URL=http://x/{_SECRET_FRAGMENT}\n", encoding="utf-8")
    os.chmod(env_file, 0o600)

    agent_source_dir = tmp_path / "live" / "agent"
    (agent_source_dir / "src" / "weapon_detection_agent").mkdir(parents=True)
    (agent_source_dir / "src" / "weapon_detection_agent" / "__init__.py").write_text(
        "__version__ = '0.1.0'\n", encoding="utf-8"
    )

    unit_file = tmp_path / "live" / "weapon-detection-agent.service"
    unit_file.write_text("[Unit]\nDescription=fake\n", encoding="utf-8")

    deepstream_config_dir = tmp_path / "live" / "deepstream-config"
    (deepstream_config_dir / "profiles" / "yolov4-fp16").mkdir(parents=True)
    app_txt = deepstream_config_dir / "deepstream-app.txt"
    app_txt.write_text("[source0]\nuri=rtsp://camera.example/1\n", encoding="utf-8")
    os.chmod(app_txt, 0o644)

    return {
        "env_file": env_file,
        "agent_source_dir": agent_source_dir,
        "unit_file": unit_file,
        "deepstream_config_dir": deepstream_config_dir,
        "app_txt": app_txt,
    }


def _run_create_backup(output_dir: Path, live: dict[str, Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(_CREATE_SCRIPT),
            "--output-dir",
            str(output_dir),
            "--env-file",
            str(live["env_file"]),
            "--agent-source-dir",
            str(live["agent_source_dir"]),
            "--unit-file",
            str(live["unit_file"]),
            "--deepstream-config-dir",
            str(live["deepstream_config_dir"]),
            "--service-name",
            "does-not-exist-in-tests",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _run_restore_backup(backup_dir: Path, live: dict[str, Path]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            str(_RESTORE_SCRIPT),
            str(backup_dir),
            "--env-file",
            str(live["env_file"]),
            "--agent-source-dir",
            str(live["agent_source_dir"]),
            "--unit-file",
            str(live["unit_file"]),
            "--deepstream-config-dir",
            str(live["deepstream_config_dir"]),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_creating_a_backup_does_not_change_live_file_modes(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    env_mode_before = stat.S_IMODE(live["env_file"].stat().st_mode)
    app_txt_mode_before = stat.S_IMODE(live["app_txt"].stat().st_mode)

    result = _run_create_backup(tmp_path / "backup", live)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(live["env_file"].stat().st_mode) == env_mode_before
    assert stat.S_IMODE(live["app_txt"].stat().st_mode) == app_txt_mode_before


def test_creating_a_backup_does_not_change_live_file_ownership(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    owner_before = live["app_txt"].stat().st_uid
    group_before = live["app_txt"].stat().st_gid

    result = _run_create_backup(tmp_path / "backup", live)

    assert result.returncode == 0, result.stderr
    assert live["app_txt"].stat().st_uid == owner_before
    assert live["app_txt"].stat().st_gid == group_before


def test_backup_directory_itself_is_mode_0700(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    output_dir = tmp_path / "backup"

    result = _run_create_backup(output_dir, live)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0, reason="requires real root")
def test_backup_directory_is_root_owned_when_run_as_root(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    output_dir = tmp_path / "backup"

    result = _run_create_backup(output_dir, live)

    assert result.returncode == 0, result.stderr
    assert output_dir.stat().st_uid == 0


def test_backup_copy_preserves_customised_content_exactly(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    output_dir = tmp_path / "backup"

    result = _run_create_backup(output_dir, live)

    assert result.returncode == 0, result.stderr
    assert (output_dir / "deepstream-config" / "deepstream-app.txt").read_text(
        encoding="utf-8"
    ) == live["app_txt"].read_text(encoding="utf-8")


def test_backup_never_prints_secret_content(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)

    result = _run_create_backup(tmp_path / "backup", live)

    assert _SECRET_FRAGMENT not in result.stdout
    assert _SECRET_FRAGMENT not in result.stderr


def test_restoration_reproduces_the_original_mode(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    original_content = live["app_txt"].read_text(encoding="utf-8")
    os.chmod(live["app_txt"], 0o640)

    backup_dir = tmp_path / "backup"
    assert _run_create_backup(backup_dir, live).returncode == 0

    # Simulate corruption/replacement of the live file (a different mode and content).
    live["app_txt"].write_text("[source0]\nuri=file:///corrupted\n", encoding="utf-8")
    os.chmod(live["app_txt"], 0o600)

    result = _run_restore_backup(backup_dir, live)

    assert result.returncode == 0, result.stderr
    assert live["app_txt"].read_text(encoding="utf-8") == original_content
    assert stat.S_IMODE(live["app_txt"].stat().st_mode) == 0o640


def test_restoration_never_prints_secret_content(tmp_path: Path) -> None:
    live = _make_fake_live_tree(tmp_path)
    backup_dir = tmp_path / "backup"
    assert _run_create_backup(backup_dir, live).returncode == 0

    result = _run_restore_backup(backup_dir, live)

    assert _SECRET_FRAGMENT not in result.stdout
    assert _SECRET_FRAGMENT not in result.stderr


def test_service_account_read_bit_is_not_stripped_by_backup(tmp_path: Path) -> None:
    """The service account (weapon-detection) reads deepstream-app.txt via the "other" read bit
    (mode 0644) — proves that bit specifically survives a backup run, not just "some" mode."""
    live = _make_fake_live_tree(tmp_path)
    before = stat.S_IMODE(live["app_txt"].stat().st_mode)
    assert before & stat.S_IROTH  # the fixture itself is 0644 — sanity-check the premise

    assert _run_create_backup(tmp_path / "backup", live).returncode == 0

    after = stat.S_IMODE(live["app_txt"].stat().st_mode)
    assert after & stat.S_IROTH
    assert after == before
