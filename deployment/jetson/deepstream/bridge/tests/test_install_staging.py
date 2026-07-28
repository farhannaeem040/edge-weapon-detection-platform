"""Offline tests for stage-bridge-source.sh (IP-07 T-89, FS-05 §4.6, task item 7).

Exercises the real, committed staging script — the exact logic install.sh delegates to — against a
temporary fake source/destination tree. Proves the idempotency/venv-preservation guarantees FS-05
§4.6 requires: repeated staging never deletes, truncates, or rebuilds an existing venv/, and never
places a dependency under runtime/.

Requires ``rsync``/``bash``/``install`` on PATH — skipped otherwise (this script targets Linux/the
Jetson in production; a Windows dev machine without them is expected to skip locally and rely on the
Jetson-side verification for a true run).
"""

from __future__ import annotations

import filecmp
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

STAGE_SCRIPT = Path(__file__).resolve().parent.parent / "stage-bridge-source.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("bash") is None,
    reason="requires rsync and bash on PATH (available on the Jetson/Linux target)",
)


def _real_bridge_src() -> Path:
    return Path(__file__).resolve().parent.parent


def _run_stage(
    src: Path, dest: Path, service_user: str = "", service_group: str = ""
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(STAGE_SCRIPT), str(src), str(dest), service_user, service_group],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_first_staging_creates_expected_files(tmp_path: Path) -> None:
    dest = tmp_path / "deepstream-bridge"
    result = _run_stage(_real_bridge_src(), dest)

    assert result.returncode == 0, result.stderr
    assert (dest / "app" / "deepstream_bridge" / "main.py").is_file()
    assert (dest / "app" / "deepstream_bridge" / "__init__.py").is_file()
    assert (dest / "run.sh").is_file()
    assert (dest / "deploy-bridge.sh").is_file()
    assert (dest / "requirements.lock").is_file()
    assert (dest / "README.md").is_file()
    # tests/ and pyproject.toml are NOT part of the deployed layout (task item 1's five entries only).
    assert not (dest / "tests").exists()


def test_second_staging_updates_source(tmp_path: Path) -> None:
    fake_src = tmp_path / "src"
    shutil.copytree(_real_bridge_src() / "app", fake_src / "app")
    shutil.copy2(_real_bridge_src() / "run.sh", fake_src / "run.sh")
    shutil.copy2(_real_bridge_src() / "deploy-bridge.sh", fake_src / "deploy-bridge.sh")
    shutil.copy2(_real_bridge_src() / "requirements.lock", fake_src / "requirements.lock")
    shutil.copy2(_real_bridge_src() / "README.md", fake_src / "README.md")

    dest = tmp_path / "deepstream-bridge"
    assert _run_stage(fake_src, dest).returncode == 0

    marker = fake_src / "app" / "deepstream_bridge" / "UPDATED_MARKER.py"
    marker.write_text("# proves a second staging run picks up new source\n", encoding="utf-8")

    result = _run_stage(fake_src, dest)
    assert result.returncode == 0, result.stderr
    assert (dest / "app" / "deepstream_bridge" / "UPDATED_MARKER.py").is_file()


def test_marker_file_under_venv_survives_byte_for_byte(tmp_path: Path) -> None:
    dest = tmp_path / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    venv_dir = dest / "venv" / "lib" / "python3.8" / "site-packages"
    venv_dir.mkdir(parents=True)
    marker = venv_dir / "pyds.so"
    marker_content = os.urandom(4096)
    marker.write_bytes(marker_content)
    marker_mtime_before = marker.stat().st_mtime

    # Simulate update.sh: restage source (possibly multiple times).
    for _ in range(3):
        result = _run_stage(_real_bridge_src(), dest)
        assert result.returncode == 0, result.stderr

    assert marker.read_bytes() == marker_content
    assert marker.stat().st_mtime == marker_mtime_before


def test_arbitrary_venv_contents_survive_restaging(tmp_path: Path) -> None:
    dest = tmp_path / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    venv_dir = dest / "venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "bin" / "python").write_bytes(os.urandom(512))
    (venv_dir / "bin" / "pip").write_bytes(os.urandom(256))
    (venv_dir / "lib" / "python3.8" / "site-packages").mkdir(parents=True)
    (venv_dir / "lib" / "python3.8" / "site-packages" / "pgi").mkdir()
    (venv_dir / "lib" / "python3.8" / "site-packages" / "pgi" / "__init__.py").write_bytes(
        os.urandom(1024)
    )
    (venv_dir / "pyvenv.cfg").write_text("home = /usr\nversion = 3.8.10\n", encoding="utf-8")

    before = {
        p.relative_to(venv_dir): (p.read_bytes() if p.is_file() else None)
        for p in venv_dir.rglob("*")
    }

    assert _run_stage(_real_bridge_src(), dest).returncode == 0
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    after = {
        p.relative_to(venv_dir): (p.read_bytes() if p.is_file() else None)
        for p in venv_dir.rglob("*")
    }
    assert before == after


def test_staging_never_creates_dependencies_under_runtime(tmp_path: Path) -> None:
    root = tmp_path / "opt" / "weapon-detection"
    dest = root / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    runtime_dir = root / "runtime"
    assert not runtime_dir.exists()


def test_run_sh_and_deploy_bridge_sh_retain_executable_modes(tmp_path: Path) -> None:
    dest = tmp_path / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    for name in ("run.sh", "deploy-bridge.sh"):
        mode = (dest / name).stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} lost its owner-execute bit after staging"


def test_no_production_environment_file_is_touched(tmp_path: Path) -> None:
    fake_env_dir = tmp_path / "etc" / "weapon-detection-agent"
    fake_env_dir.mkdir(parents=True)
    fake_env_file = fake_env_dir / "agent.env"
    fake_env_file.write_text("WDA_BACKEND_BASE_URL=http://example.invalid\n", encoding="utf-8")
    before_mtime = fake_env_file.stat().st_mtime
    before_content = fake_env_file.read_bytes()

    dest = tmp_path / "opt" / "weapon-detection" / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    assert fake_env_file.read_bytes() == before_content
    assert fake_env_file.stat().st_mtime == before_mtime


def test_staged_app_source_matches_real_source_tree(tmp_path: Path) -> None:
    dest = tmp_path / "deepstream-bridge"
    assert _run_stage(_real_bridge_src(), dest).returncode == 0

    comparison = filecmp.dircmp(str(_real_bridge_src() / "app"), str(dest / "app"))
    assert not comparison.left_only or comparison.left_only == {"__pycache__"}
    assert not comparison.diff_files
