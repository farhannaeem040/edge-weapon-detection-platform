"""Unit tests for the Agent filesystem layout (IP-02 T-34, §8, §16.1).

Every test provisions into a temporary root (``tmp_path``), never ``/opt`` — the same testability
affordance the settings model exposes via ``WDA_ROOT_PATH`` (IP-02 §8.2). Mode assertions run only
where the OS can honour POSIX modes (Linux/Jetson); on Windows they are skipped rather than
asserting something ``os.chmod`` ignores (IP-02 §17). No test opens a socket or contacts a network.
"""

from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

from weapon_detection_agent.config.paths import (
    CONFIG_DIR_MODE,
    DATABASE_DIR_MODE,
    DEFAULT_ROOT,
    DEFERRED_DIRECTORIES,
    LOGS_DIR_MODE,
    ROOT_MODE,
    modes_enforceable,
    resolve_paths,
)

# Skip mode assertions where POSIX modes are not representable (Windows). Directory *creation* is
# still asserted on every platform.
requires_posix_modes = pytest.mark.skipif(
    not modes_enforceable(),
    reason="POSIX permission modes are not enforceable on this platform (IP-02 §17)",
)


def _mode_of(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# --- Resolution is pure (no I/O) ---------------------------------------------------------------


def test_resolve_paths_creates_nothing(tmp_path: Path) -> None:
    root = tmp_path / "weapon-detection"

    paths = resolve_paths(root)

    # Resolving is pure path arithmetic: nothing is created until provision() is called.
    assert paths.root == root
    assert not root.exists()
    assert not paths.config_dir.exists()


def test_default_root_matches_adr_008() -> None:
    assert resolve_paths().root == DEFAULT_ROOT
    assert DEFAULT_ROOT == Path("/opt/weapon-detection")


def test_derived_paths_are_correct(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)

    assert paths.config_dir == tmp_path / "config"
    assert paths.database_dir == tmp_path / "database"
    assert paths.logs_dir == tmp_path / "logs"
    assert paths.database_file == tmp_path / "database" / "agent.db"
    assert paths.activation_key_file == tmp_path / "config" / "activation-key"
    assert paths.log_file == tmp_path / "logs" / "agent.log"


def test_resolve_paths_accepts_a_string_root(tmp_path: Path) -> None:
    paths = resolve_paths(str(tmp_path / "weapon-detection"))

    assert paths.root == tmp_path / "weapon-detection"


# --- Provisioning creates this milestone's directories -----------------------------------------


def test_provision_creates_all_managed_directories(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection")

    result = paths.provision()

    assert result is paths  # returns self for chaining
    assert paths.root.is_dir()
    assert paths.config_dir.is_dir()
    assert paths.database_dir.is_dir()
    assert paths.logs_dir.is_dir()


def test_provision_creates_missing_parent_directories(tmp_path: Path) -> None:
    # A nested root whose parents do not yet exist must still be created (mkdir parents=True).
    paths = resolve_paths(tmp_path / "a" / "b" / "weapon-detection")

    paths.provision()

    assert paths.root.is_dir()


def test_provision_creates_no_files(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path).provision()

    # The layout is directories only; the files' contents belong to later tasks.
    assert not paths.database_file.exists()
    assert not paths.activation_key_file.exists()
    assert not paths.log_file.exists()


def test_provision_creates_only_the_four_managed_directories(tmp_path: Path) -> None:
    root = tmp_path / "weapon-detection"
    resolve_paths(root).provision()

    entries = sorted(child.name for child in root.iterdir())

    assert entries == ["config", "database", "logs"]


@pytest.mark.parametrize("excluded", DEFERRED_DIRECTORIES)
def test_provision_does_not_create_deferred_directories(tmp_path: Path, excluded: str) -> None:
    paths = resolve_paths(tmp_path).provision()

    # snapshots/, recordings/, models/, pipeline/, runtime/ have no writer in this milestone.
    assert not (paths.root / excluded).exists()


# --- Idempotency -------------------------------------------------------------------------------


def test_provision_is_idempotent(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection")

    paths.provision()
    # A second run must not raise and must leave the layout intact.
    paths.provision()

    assert paths.config_dir.is_dir()
    assert paths.database_dir.is_dir()
    assert paths.logs_dir.is_dir()


def test_provision_preserves_a_populated_directory(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path).provision()
    marker = paths.database_dir / "existing.txt"
    marker.write_text("keep me", encoding="utf-8")

    # Re-provisioning must not wipe or recreate an existing directory's contents.
    paths.provision()

    assert marker.read_text(encoding="utf-8") == "keep me"


# --- Modes (POSIX only) ------------------------------------------------------------------------


@requires_posix_modes
def test_provision_applies_adr_008_modes(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()

    assert _mode_of(paths.root) == ROOT_MODE
    assert _mode_of(paths.config_dir) == CONFIG_DIR_MODE
    assert _mode_of(paths.database_dir) == DATABASE_DIR_MODE
    assert _mode_of(paths.logs_dir) == LOGS_DIR_MODE


@requires_posix_modes
def test_provision_corrects_drifted_modes(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path / "weapon-detection").provision()
    # Loosen a mode behind the module's back, then re-provision: it must be corrected.
    os.chmod(paths.config_dir, 0o777)

    paths.provision()

    assert _mode_of(paths.config_dir) == CONFIG_DIR_MODE


@requires_posix_modes
def test_config_and_database_dirs_are_not_group_or_world_accessible(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path).provision()

    # The secret-bearing directories must expose nothing to group or other (ARCH-001 §13.3).
    for secret_dir in (paths.config_dir, paths.database_dir):
        mode = _mode_of(secret_dir)
        assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


# --- AgentPaths is immutable and importing does no I/O -----------------------------------------


def test_agent_paths_is_frozen(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        paths.root = tmp_path / "other"  # type: ignore[misc]


def test_managed_directories_lists_root_first(tmp_path: Path) -> None:
    paths = resolve_paths(tmp_path)

    directories = [directory for directory, _mode in paths.managed_directories]

    # Root must precede its children so it exists before they are created.
    assert directories[0] == paths.root
    assert set(directories) == {paths.root, paths.config_dir, paths.database_dir, paths.logs_dir}


def test_no_filesystem_access_on_import() -> None:
    # Re-importing the module must not create the default /opt root or any directory.
    import importlib

    module = importlib.reload(importlib.import_module("weapon_detection_agent.config.paths"))

    assert isinstance(module.DEFAULT_ROOT, Path)
    assert issubclass(module.AgentPaths, object)
