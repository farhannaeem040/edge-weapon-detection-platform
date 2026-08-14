"""Offline tests for run.sh (IP-07 T-89, FS-05 §4.3).

Runs the real, committed run.sh as a subprocess (not a reimplementation) against a fabricated
``<fixture>/venv/bin/python`` — a tiny POSIX-sh stub standing in for the real venv interpreter, which
records the argv/environment it received and exits with a controllable code. This proves run.sh's
own argument validation, path resolution, environment handling, and ``exec`` behaviour without
needing a real Python 3.8/pyds venv.

Requires a POSIX shell (``sh``) on PATH — skipped otherwise (e.g. a Windows machine with no Git Bash
on PATH), since run.sh itself is a ``#!/bin/sh`` script only ever run on the Jetson/Linux in
production.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

RUN_SH = Path(__file__).resolve().parent.parent / "run.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="requires a POSIX sh on PATH")

_FAKE_PYTHON_STUB = """#!/bin/sh
# Records what it was invoked with instead of actually running Python.
: "${TEST_OUT_DIR:?TEST_OUT_DIR must be set}"
{
    printf 'ARGC=%s\\n' "$#"
    i=0
    for a in "$@"; do
        i=$((i + 1))
        printf 'ARGV%s=%s\\n' "$i" "$a"
    done
    printf 'PYTHONPATH=%s\\n' "${PYTHONPATH:-}"
    printf 'PID=%s\\n' "$$"
} > "${TEST_OUT_DIR}/observed.txt"

if [ -n "${FAKE_SLEEP_SECONDS:-}" ]; then
    trap 'echo "SIGTERM_RECEIVED" > "${TEST_OUT_DIR}/signal.txt"; exit 77' TERM
    sleep "${FAKE_SLEEP_SECONDS}"
fi

exit "${FAKE_EXIT_CODE:-0}"
"""


def _make_bridge_root(
    tmp_path: Path, *, with_python: bool = True, python_executable: bool = True
) -> Path:
    bridge_root = tmp_path / "deepstream-bridge"
    bridge_root.mkdir()
    shutil.copy2(RUN_SH, bridge_root / "run.sh")
    os.chmod(bridge_root / "run.sh", 0o755)

    venv_bin = bridge_root / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    if with_python:
        python_path = venv_bin / "python"
        # `newline=` on Path.write_text requires Python >=3.10; this test suite must also run
        # under the Bridge's own Python 3.8 target, so the file is written in binary mode instead
        # to pin the line ending explicitly on every interpreter version.
        python_path.write_bytes(_FAKE_PYTHON_STUB.encode("utf-8"))
        if python_executable:
            os.chmod(python_path, 0o755)
        else:
            os.chmod(python_path, 0o644)

    return bridge_root


def _run(
    bridge_root: Path, args: list[str], *, env: dict[str, str] | None = None, timeout: float = 10.0
):
    full_env = dict(os.environ)
    full_env.pop("PYTHONPATH", None)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["sh", str(bridge_root / "run.sh"), *args],
        cwd=str(bridge_root.parent),  # never assume the shell's cwd is bridge_root itself
        env=full_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _shell_view(path: Path) -> str:
    """How the POSIX shell itself resolves ``path`` via ``pwd -P`` — the same mechanism run.sh
    uses internally. Comparing against this (rather than a raw ``pathlib`` string) keeps the
    PYTHONPATH assertions correct under Git Bash/MSYS on Windows, where a Windows-style path
    argument is transparently translated to a POSIX-style one before the script ever sees it."""
    result = subprocess.run(
        ["sh", "-c", 'cd -- "$1" && pwd -P', "sh", str(path)],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _observed(bridge_root: Path, out_dir: Path) -> dict[str, str]:
    text = (out_dir / "observed.txt").read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        result[key] = value
    return result


def test_missing_arguments_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    result = _run(bridge_root, [])
    assert result.returncode == 2
    assert "expected exactly" in result.stderr


def test_wrong_first_argument_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    result = _run(bridge_root, ["--config", str(config)])
    assert result.returncode == 2


def test_too_many_arguments_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    result = _run(bridge_root, ["-c", str(config), "extra"])
    assert result.returncode == 2


def test_missing_config_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    result = _run(bridge_root, ["-c", str(tmp_path / "does-not-exist.txt")])
    assert result.returncode == 4
    assert "not readable" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions are not meaningful on Windows")
def test_unreadable_config_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    os.chmod(config, 0o000)
    try:
        result = _run(bridge_root, ["-c", str(config)])
        assert result.returncode == 4
    finally:
        os.chmod(config, 0o644)


def test_missing_venv_interpreter_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path, with_python=False)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    result = _run(bridge_root, ["-c", str(config)])
    assert result.returncode == 3
    assert "not found or not executable" in result.stderr


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX executable-bit semantics are not available on Windows"
)
def test_non_executable_venv_interpreter_rejected(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path, python_executable=False)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    result = _run(bridge_root, ["-c", str(config)])
    assert result.returncode == 3


def test_valid_invocation_exact_argv_shape(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(
        bridge_root,
        ["-c", str(config)],
        env={"TEST_OUT_DIR": str(out_dir), "FAKE_EXIT_CODE": "0"},
    )

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["ARGC"] == "6"
    assert observed["ARGV1"] == "-m"
    assert observed["ARGV2"] == "deepstream_bridge.main"
    assert observed["ARGV3"] == "--socket-path"
    assert observed["ARGV5"] == "--config"
    assert observed["ARGV6"] == str(config)


def test_default_socket_path_fallback(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(bridge_root, ["-c", str(config)], env={"TEST_OUT_DIR": str(out_dir)})

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    full_argv = [observed[f"ARGV{i}"] for i in range(1, int(observed["ARGC"]) + 1)]
    assert full_argv == [
        "-m",
        "deepstream_bridge.main",
        "--socket-path",
        "/opt/weapon-detection/runtime/detection.sock",
        "--config",
        str(config),
    ]


def test_socket_path_environment_override(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(
        bridge_root,
        ["-c", str(config)],
        env={
            "TEST_OUT_DIR": str(out_dir),
            "WDA_DETECTION_SOCKET_PATH": "/tmp/custom-detection.sock",
        },
    )

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    full_argv = [observed[f"ARGV{i}"] for i in range(1, int(observed["ARGC"]) + 1)]
    assert full_argv == [
        "-m",
        "deepstream_bridge.main",
        "--socket-path",
        "/tmp/custom-detection.sock",
        "--config",
        str(config),
    ]


def test_pythonpath_points_at_app_directory(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(bridge_root, ["-c", str(config)], env={"TEST_OUT_DIR": str(out_dir)})

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["PYTHONPATH"] == f"{_shell_view(bridge_root)}/app"


def test_pythonpath_prepends_to_existing_value(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(
        bridge_root,
        ["-c", str(config)],
        env={"TEST_OUT_DIR": str(out_dir), "PYTHONPATH": "/some/other/path"},
    )

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["PYTHONPATH"] == f"{_shell_view(bridge_root)}/app:/some/other/path"


def test_exit_code_propagates_from_python(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(
        bridge_root,
        ["-c", str(config)],
        env={"TEST_OUT_DIR": str(out_dir), "FAKE_EXIT_CODE": "17"},
    )

    assert result.returncode == 17


@pytest.mark.skipif(
    os.name == "nt", reason="MSYS/Cygwin maintains a separate PID namespace from native Win32 PIDs"
)
def test_exec_replaces_the_shell_same_pid(tmp_path: Path) -> None:
    """No intermediate shell remains after exec: the launched process's PID equals the PID the
    fake python stub observes for itself (`$$`), proving `exec` replaced the shell image rather
    than forking a child under it."""
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    full_env = dict(os.environ)
    full_env.pop("PYTHONPATH", None)
    full_env["TEST_OUT_DIR"] = str(out_dir)
    process = subprocess.Popen(
        ["sh", str(bridge_root / "run.sh"), "-c", str(config)],
        cwd=str(tmp_path),
        env=full_env,
    )
    launched_pid = process.pid
    process.wait(timeout=10.0)

    observed = _observed(bridge_root, out_dir)
    assert observed["PID"] == str(launched_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics are not available on Windows")
def test_sigterm_reaches_the_exec_d_process_directly(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    full_env = dict(os.environ)
    full_env.pop("PYTHONPATH", None)
    full_env["TEST_OUT_DIR"] = str(out_dir)
    full_env["FAKE_SLEEP_SECONDS"] = "5"
    process = subprocess.Popen(
        ["sh", str(bridge_root / "run.sh"), "-c", str(config)],
        cwd=str(tmp_path),
        env=full_env,
    )
    # Give the fake python stub time to install its trap before signalling.
    deadline = time.monotonic() + 5.0
    while not (out_dir / "observed.txt").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)

    os.kill(process.pid, signal.SIGTERM)
    process.wait(timeout=10.0)

    assert (out_dir / "signal.txt").read_text(encoding="utf-8").strip() == "SIGTERM_RECEIVED"
    assert process.returncode == 77


# --- IP-07 T-90: run.sh <-> DeepStreamProcessManager invocation-agreement checks -------------------
#
# DeepStreamProcessManager (agent/src/weapon_detection_agent/deepstream/process_manager.py) always
# builds argv as exactly `(str(executable_path), "-c", str(config_path))` via
# `asyncio.create_subprocess_exec` — an argv-array exec, never a shell string, so no quoting layer
# exists on the Agent side at all; the only place a space-in-path could misbehave is inside run.sh's
# own `"$@"`/`"${CONFIG_PATH}"` handling. These tests prove that handling is correct.


def test_config_path_containing_spaces_is_preserved_exactly(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config_dir = tmp_path / "config dir with spaces"
    config_dir.mkdir()
    config = config_dir / "deep stream app.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(bridge_root, ["-c", str(config)], env={"TEST_OUT_DIR": str(out_dir)})

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["ARGV6"] == str(config)


def test_bridge_root_path_containing_spaces_resolves_pythonpath_correctly(tmp_path: Path) -> None:
    parent = tmp_path / "opt space" / "weapon detection"
    parent.mkdir(parents=True)
    bridge_root = _make_bridge_root(parent)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = _run(bridge_root, ["-c", str(config)], env={"TEST_OUT_DIR": str(out_dir)})

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["PYTHONPATH"] == f"{_shell_view(bridge_root)}/app"


def test_socket_path_containing_spaces_is_preserved_exactly(tmp_path: Path) -> None:
    bridge_root = _make_bridge_root(tmp_path)
    config = tmp_path / "config.txt"
    config.write_text("x", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    socket_path = str(tmp_path / "runtime dir" / "detection socket.sock")

    result = _run(
        bridge_root,
        ["-c", str(config)],
        env={"TEST_OUT_DIR": str(out_dir), "WDA_DETECTION_SOCKET_PATH": socket_path},
    )

    assert result.returncode == 0
    observed = _observed(bridge_root, out_dir)
    assert observed["ARGV4"] == socket_path
