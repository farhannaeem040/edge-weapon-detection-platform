"""Tests for stage-deepstream-config.sh (IP-07 T-91 hardening, Part A).

Exercises the real, committed staging script — the exact logic install.sh delegates to — proving
the preserve-by-default policy: an existing, operator-customized deepstream-app.txt/profile config
survives an install re-run untouched unless --replace is explicitly passed, in which case a backup
is taken first and only the intended files change.

Requires ``rsync``/``bash``/``install`` on PATH — skipped otherwise (this script targets Linux/the
Jetson in production; a Windows dev machine without them is expected to skip locally and rely on
the Jetson-side verification for a true run).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_JETSON_DEPLOY_DIR = Path(__file__).resolve().parent.parent.parent / "deployment" / "jetson"
_SCRIPT = _JETSON_DEPLOY_DIR / "stage-deepstream-config.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("bash") is None,
    reason="requires rsync and bash on PATH (available on the Jetson/Linux target)",
)

_CUSTOM_CAMERA_URI = "rtsp://198.51.100.7:8554/customer-camera-42"


def _run_stage(
    src: Path, config_dir: Path, profiles_dir: Path, *extra_args: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_SCRIPT), str(src), str(config_dir), str(profiles_dir), *extra_args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_fake_source(tmp_path: Path) -> Path:
    """A minimal, real-shaped deepstream/ source tree (deepstream-app.txt + one profile)."""
    src = tmp_path / "src-deepstream"
    (src / "profiles" / "yolov4-fp16").mkdir(parents=True)
    (src / "deepstream-app.txt").write_text(
        "[source0]\ntype=3\nuri=file:///opt/weapon-detection/samples/deepstream/input.mp4\n",
        encoding="utf-8",
    )
    (src / "profiles" / "yolov4-fp16" / "infer-config.txt").write_text(
        "[property]\ntemplate-value=1\n", encoding="utf-8"
    )
    (src / "profiles" / "yolov4-fp16" / "labels.txt").write_text("gun\nknife\n", encoding="utf-8")
    (src / "profiles" / "yolov4-fp16" / "manifest.env").write_text(
        "PROFILE_NAME=yolov4-fp16\n", encoding="utf-8"
    )
    return src


def test_first_install_creates_deepstream_app_txt_and_profile(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"

    result = _run_stage(src, config_dir, profiles_dir)

    assert result.returncode == 0, result.stderr
    assert (config_dir / "deepstream-app.txt").is_file()
    assert (profiles_dir / "yolov4-fp16" / "infer-config.txt").is_file()
    assert (profiles_dir / "yolov4-fp16" / "labels.txt").is_file()
    assert "template-value=1" in (profiles_dir / "yolov4-fp16" / "infer-config.txt").read_text()


def test_second_install_preserves_a_customised_config(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    # Simulate an operator customizing the deployed config after first install.
    custom_app_txt = f"[source0]\ntype=4\nuri={_CUSTOM_CAMERA_URI}\nlatency=1000\n"
    (config_dir / "deepstream-app.txt").write_text(custom_app_txt, encoding="utf-8")
    custom_infer = "[property]\ncustom-tuned-value=42\n"
    (profiles_dir / "yolov4-fp16" / "infer-config.txt").write_text(custom_infer, encoding="utf-8")

    result = _run_stage(src, config_dir, profiles_dir)

    assert result.returncode == 0, result.stderr
    assert (config_dir / "deepstream-app.txt").read_text(encoding="utf-8") == custom_app_txt
    assert (profiles_dir / "yolov4-fp16" / "infer-config.txt").read_text(
        encoding="utf-8"
    ) == custom_infer


def test_camera_uri_remains_byte_for_byte_unchanged_across_reinstalls(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    custom_app_txt = f"[source0]\ntype=4\nuri={_CUSTOM_CAMERA_URI}\n"
    (config_dir / "deepstream-app.txt").write_bytes(custom_app_txt.encode("utf-8"))
    before = (config_dir / "deepstream-app.txt").read_bytes()

    for _ in range(3):
        assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    after = (config_dir / "deepstream-app.txt").read_bytes()
    assert after == before
    assert _CUSTOM_CAMERA_URI.encode("utf-8") in after


def test_infer_config_custom_values_remain_unchanged(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    infer_path = profiles_dir / "yolov4-fp16" / "infer-config.txt"
    custom_infer = "[property]\ntracker-width=960\ntracker-height=544\n"
    infer_path.write_text(custom_infer, encoding="utf-8")

    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    assert infer_path.read_text(encoding="utf-8") == custom_infer


def test_explicit_replace_flag_updates_the_intended_files(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    (config_dir / "deepstream-app.txt").write_text(
        f"[source0]\nuri={_CUSTOM_CAMERA_URI}\n", encoding="utf-8"
    )

    backup_root = tmp_path / "backup"
    result = _run_stage(
        src, config_dir, profiles_dir, "--replace", "--backup-root", str(backup_root)
    )

    assert result.returncode == 0, result.stderr
    # Replaced: back to the source template's content, not the operator-customized value.
    replaced_content = (config_dir / "deepstream-app.txt").read_text(encoding="utf-8")
    assert _CUSTOM_CAMERA_URI not in replaced_content
    assert "input.mp4" in replaced_content


def test_replace_flag_creates_a_backup_of_the_old_config(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    custom_content = f"[source0]\nuri={_CUSTOM_CAMERA_URI}\n"
    (config_dir / "deepstream-app.txt").write_text(custom_content, encoding="utf-8")

    backup_root = tmp_path / "backup"
    result = _run_stage(
        src, config_dir, profiles_dir, "--replace", "--backup-root", str(backup_root)
    )

    assert result.returncode == 0, result.stderr
    backed_up = backup_root / "deepstream-app.txt"
    assert backed_up.is_file()
    assert backed_up.read_text(encoding="utf-8") == custom_content


def test_replace_flag_never_prints_the_camera_uri(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    (config_dir / "deepstream-app.txt").write_text(
        f"[source0]\nuri={_CUSTOM_CAMERA_URI}\n", encoding="utf-8"
    )

    backup_root = tmp_path / "backup"
    result = _run_stage(
        src, config_dir, profiles_dir, "--replace", "--backup-root", str(backup_root)
    )

    assert _CUSTOM_CAMERA_URI not in result.stdout
    assert _CUSTOM_CAMERA_URI not in result.stderr


def test_replaced_files_get_the_correct_mode(tmp_path: Path) -> None:
    src = _make_fake_source(tmp_path)
    config_dir = tmp_path / "config" / "deepstream"
    profiles_dir = config_dir / "profiles"
    assert _run_stage(src, config_dir, profiles_dir).returncode == 0

    import stat

    mode = stat.S_IMODE((config_dir / "deepstream-app.txt").stat().st_mode)
    assert mode == 0o644


def test_bridge_staging_is_not_gated_by_the_replace_flag() -> None:
    """Structural check on install.sh: staging the Bridge's source (stage-bridge-source.sh) must
    never be conditioned on REPLACE_DEEPSTREAM_CONFIG — the two concerns (Bridge app source vs.
    operator-managed DeepStream config) are deliberately independent."""
    install_sh = _JETSON_DEPLOY_DIR / "install.sh"
    text = install_sh.read_text(encoding="utf-8")

    # rindex, not index: the first occurrence of this substring is in a comment describing the
    # delegation; the actual call site (what matters here) is the last occurrence.
    bridge_call_index = text.rindex("stage-bridge-source.sh")
    # The nearest preceding "if" governing the Bridge-staging call must test the Bridge source
    # directory's existence, not the replace flag.
    preceding = text[:bridge_call_index]
    last_if_index = preceding.rfind("if [[")
    governing_condition = preceding[last_if_index:bridge_call_index]
    assert "REPLACE_DEEPSTREAM_CONFIG" not in governing_condition
    assert "SRC_BRIDGE_DIR" in governing_condition
