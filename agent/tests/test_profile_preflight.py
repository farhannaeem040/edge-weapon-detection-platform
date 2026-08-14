"""Class/profile preflight tests (IP-07 T-90, task item 6)."""

from __future__ import annotations

from pathlib import Path

from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.detection.profile_preflight import (
    resolve_engine_path,
    resolve_profile_directory,
    run_profile_preflight,
)

VALID_URL = "http://backend.example.invalid:8080"


def _settings(tmp_path: Path, **overrides: object):
    return load_settings(
        backend_base_url=VALID_URL, root_path=str(tmp_path / "weapon-detection"), **overrides
    )


def _stage_complete_profile(
    tmp_path: Path, profile: str = "yolov4-fp16", labels: str = "gun\nknife\n"
) -> None:
    profile_dir = tmp_path / "weapon-detection" / "config" / "deepstream" / "profiles" / profile
    profile_dir.mkdir(parents=True)
    (profile_dir / "infer-config.txt").write_text("[property]\n", encoding="utf-8")
    (profile_dir / "labels.txt").write_text(labels, encoding="utf-8")

    engine_dir = tmp_path / "weapon-detection" / "models" / profile
    engine_dir.mkdir(parents=True)
    (engine_dir / "model.engine").write_bytes(b"\x00\x01\x02")


def test_resolve_profile_directory_and_engine_path_are_pure_no_io(tmp_path: Path) -> None:
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    profile_dir = resolve_profile_directory(settings)
    engine_path = resolve_engine_path(settings)

    assert (
        profile_dir
        == tmp_path / "weapon-detection" / "config" / "deepstream" / "profiles" / "yolov4-fp16"
    )
    assert engine_path == tmp_path / "weapon-detection" / "models" / "yolov4-fp16" / "model.engine"


def test_complete_profile_passes_with_correct_class_count(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path)
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is True
    assert result.problems == ()
    assert result.class_count == 2
    assert result.duplicate_labels == ()


def test_missing_profile_directory_is_a_single_problem(tmp_path: Path) -> None:
    settings = _settings(tmp_path, deepstream_model_profile="does-not-exist")

    result = run_profile_preflight(settings)

    assert bool(result) is False
    assert len(result.problems) == 1
    assert result.problems[0].check == "profile_directory"


def test_missing_infer_config_is_reported(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path)
    infer_config = (
        tmp_path
        / "weapon-detection"
        / "config"
        / "deepstream"
        / "profiles"
        / "yolov4-fp16"
        / "infer-config.txt"
    )
    infer_config.unlink()
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is False
    assert any(p.check == "infer_config" for p in result.problems)


def test_missing_labels_file_is_reported(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path)
    labels = (
        tmp_path
        / "weapon-detection"
        / "config"
        / "deepstream"
        / "profiles"
        / "yolov4-fp16"
        / "labels.txt"
    )
    labels.unlink()
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is False
    assert any(p.check == "labels_file" for p in result.problems)
    assert result.class_count is None


def test_empty_labels_file_is_reported(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path, labels="")
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is False
    assert any(p.check == "labels_file" for p in result.problems)


def test_missing_engine_file_is_reported(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path)
    engine = tmp_path / "weapon-detection" / "models" / "yolov4-fp16" / "model.engine"
    engine.unlink()
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is False
    assert any(p.check == "engine_file" for p in result.problems)


def test_all_problems_reported_together_not_one_at_a_time(tmp_path: Path) -> None:
    """A profile directory that exists but has nothing else staged reports every missing file in
    one preflight call, not just the first."""
    profile_dir = (
        tmp_path / "weapon-detection" / "config" / "deepstream" / "profiles" / "yolov4-fp16"
    )
    profile_dir.mkdir(parents=True)
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    checks = {p.check for p in result.problems}
    assert checks == {"infer_config", "labels_file", "engine_file"}


def test_duplicate_labels_are_reported_but_not_fatal(tmp_path: Path) -> None:
    _stage_complete_profile(tmp_path, labels="gun\nknife\ngun\n")
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is True  # duplicates alone never fail preflight
    assert result.duplicate_labels == ("gun",)
    assert result.class_count == 3


def test_blank_label_in_the_middle_is_preserved_positionally_not_fatal(tmp_path: Path) -> None:
    """Matches class_labels.load_class_names's own documented rule: a blank line anywhere but the
    trailing position is preserved as an empty-string class name at that position, never treated as
    a preflight failure and never causing later class ids to shift."""
    _stage_complete_profile(tmp_path, labels="gun\n\nknife\n")
    settings = _settings(tmp_path, deepstream_model_profile="yolov4-fp16")

    result = run_profile_preflight(settings)

    assert bool(result) is True
    assert result.class_count == 3  # gun=0, ""=1, knife=2 — knife's id is NOT shifted to 1


def test_no_hardcoded_class_names_a_different_profile_name_works_unmodified(tmp_path: Path) -> None:
    """Genericness (task item 6): a profile named anything, with any label set, passes with no code
    change — never an assumption that class 0/1 or 'gun'/'knife' specifically must appear."""
    _stage_complete_profile(tmp_path, profile="some-other-model", labels="cat\ndog\nbird\n")
    settings = _settings(tmp_path, deepstream_model_profile="some-other-model")

    result = run_profile_preflight(settings)

    assert bool(result) is True
    assert result.class_count == 3
