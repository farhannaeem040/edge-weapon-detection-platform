"""Detection socket-path agreement across the Agent/Bridge boundary (IP-07 T-90, task item 4).

Five places must agree on the production socket path:

1. ``AgentSettings.detection_socket_path`` (this file's default/override source).
2. ``AgentPaths.detection_socket_file`` (root-derived; matches (1) only for the *default* root).
3. ``run.sh``'s ``${WDA_DETECTION_SOCKET_PATH:-...}`` literal fallback.
4. The Bridge's ``--socket-path`` argument (whatever ``run.sh`` passes it — proven by
   ``deployment/jetson/deepstream/bridge/tests/test_run_sh.py``, not re-proven here).
5. ``DetectionIngestHandler``'s actual bind path (wired from (1) as of IP-07 T-90 — see
   ``default_detection_components_factory`` in ``detection/ingest_handler.py``; previously wired
   from (2), which silently dropped a ``WDA_DETECTION_SOCKET_PATH``-only override — fixed here).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from weapon_detection_agent.config.paths import DEFAULT_ROOT, resolve_paths
from weapon_detection_agent.config.settings import load_settings
from weapon_detection_agent.detection.ingest_handler import default_detection_components_factory
from weapon_detection_agent.persistence.device_identity_repository import DeviceIdentityRepository

VALID_URL = "http://backend.example.invalid:8080"
_RUN_SH = (
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "jetson"
    / "deepstream"
    / "bridge"
    / "run.sh"
)


def _run_sh_socket_path_fallback() -> str:
    text = _RUN_SH.read_text(encoding="utf-8")
    match = re.search(r'SOCKET_PATH="\$\{WDA_DETECTION_SOCKET_PATH:-([^}]+)\}"', text)
    assert match is not None, "could not find run.sh's WDA_DETECTION_SOCKET_PATH fallback literal"
    return match.group(1)


def test_default_settings_path_matches_default_agent_paths_path() -> None:
    settings = load_settings(backend_base_url=VALID_URL)
    paths = resolve_paths(DEFAULT_ROOT)

    assert settings.detection_socket_path == paths.detection_socket_file


def test_default_settings_path_matches_run_sh_fallback_literal() -> None:
    settings = load_settings(backend_base_url=VALID_URL)

    assert settings.detection_socket_path.as_posix() == _run_sh_socket_path_fallback()


def test_default_agent_paths_path_matches_run_sh_fallback_literal() -> None:
    paths = resolve_paths(DEFAULT_ROOT)

    assert paths.detection_socket_file.as_posix() == _run_sh_socket_path_fallback()


def test_default_is_the_documented_production_path() -> None:
    settings = load_settings(backend_base_url=VALID_URL)
    assert (
        settings.detection_socket_path.as_posix() == "/opt/weapon-detection/runtime/detection.sock"
    )


def test_environment_override_is_actually_used_by_the_ingest_handler(tmp_path: Path) -> None:
    """The regression this test module exists to catch: WDA_DETECTION_SOCKET_PATH must actually
    reach DetectionIngestHandler's bind path, matching run.sh's own use of the same variable — not
    silently ignored in favour of the root-derived AgentPaths path."""
    custom_socket = tmp_path / "elsewhere" / "custom-detection.sock"
    custom_socket.parent.mkdir(parents=True)

    paths = resolve_paths(tmp_path / "weapon-detection")
    paths.provision()
    identity_repository = DeviceIdentityRepository(paths.database_file)
    from weapon_detection_agent.persistence import initialize_database

    initialize_database(paths.database_file)

    profile_dir = paths.root / "config" / "deepstream" / "profiles" / "yolov4-fp16"
    profile_dir.mkdir(parents=True)
    (profile_dir / "labels.txt").write_text("gun\n", encoding="utf-8")

    settings = load_settings(
        backend_base_url=VALID_URL,
        root_path=str(tmp_path / "weapon-detection"),
        deepstream_enabled=True,
        detection_events_enabled=True,
        deepstream_executable_path="/opt/weapon-detection/deepstream-bridge/run.sh",
        detection_socket_path=str(custom_socket),
    )

    components = default_detection_components_factory(settings, paths, identity_repository)

    assert len(components) == 1
    handler = components[0]
    assert handler._socket_path == custom_socket  # noqa: SLF001
    assert handler._socket_path != paths.detection_socket_file  # noqa: SLF001


def test_no_dependency_or_socket_exists_under_runtime_before_startup(tmp_path: Path) -> None:
    """AgentPaths.provision() creates the runtime/ directory itself (mode 0700) but never a socket
    file or any dependency inside it — the socket is created only by
    DetectionIngestHandler.start(), never at provisioning/construction time."""
    paths = resolve_paths(tmp_path / "weapon-detection")
    paths.provision()

    assert paths.runtime_dir.is_dir()
    assert list(paths.runtime_dir.iterdir()) == []
    assert not paths.detection_socket_file.exists()


@pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission modes are not meaningful on Windows"
)
def test_runtime_directory_mode_is_0700(tmp_path: Path) -> None:
    import stat

    paths = resolve_paths(tmp_path / "weapon-detection")
    paths.provision()

    mode = stat.S_IMODE(paths.runtime_dir.stat().st_mode)
    assert mode == 0o700


# --- IP-07 T-90 item 7: no dependency can be staged under runtime/ ------------------------------

_DEPLOYMENT_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent / "deployment" / "jetson" / "install.sh",
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "jetson"
    / "deepstream"
    / "bridge"
    / "stage-bridge-source.sh",
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "jetson"
    / "deepstream"
    / "bridge"
    / "deploy-bridge.sh",
)


@pytest.mark.parametrize("script", _DEPLOYMENT_SCRIPTS, ids=lambda p: p.name)
def test_deployment_scripts_never_write_a_path_under_runtime(script: Path) -> None:
    """Static guard (task item 7: "dependencies cannot be staged under runtime/") — none of the
    scripts that stage source or build the Bridge venv ever reference a `.../runtime/...` path as a
    write target. The only legitimate mention of "runtime" anywhere in these scripts is
    `WDA_DETECTION_SOCKET_PATH`'s own fallback default (run.sh, not one of these three) or an
    unrelated English use of the word ("Agent runtime package", "never root-run at runtime") — this
    regex specifically looks for a `/runtime/` *path component*, not the bare word.
    """
    text = script.read_text(encoding="utf-8")
    offending_lines = [line for line in text.splitlines() if re.search(r"[\"'/]runtime/", line)]
    assert offending_lines == [], f"{script.name} references a runtime/ path: {offending_lines}"
