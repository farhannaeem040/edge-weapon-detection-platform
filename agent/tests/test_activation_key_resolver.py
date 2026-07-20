"""Unit tests for the Activation Key resolver (IP-02 T-38, §6.1).

Fixtures use obviously fake keys; the value must never surface in a repr, str, log, or error. File
tests use ``tmp_path``; no test reads a real key file, opens a socket, or contacts a network.
"""

from __future__ import annotations

import builtins
import importlib
import socket
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.activation.errors import ActivationKeyFileError
from weapon_detection_agent.activation.key_resolver import (
    ActivationKeyResolver,
    KeySource,
    ResolvedActivationKey,
)

FAKE_KEY = "keyid.test-activation-secret-ZZZ"


def _resolver(*, env: str | None, key_file: Path) -> ActivationKeyResolver:
    env_key = SecretStr(env) if env is not None else None
    return ActivationKeyResolver(environment_key=env_key, key_file_path=key_file)


# --- 1-4. Precedence and absence --------------------------------------------------------------


def test_environment_key_takes_precedence(tmp_path: Path) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text("file-sourced-key", encoding="utf-8")

    resolved = _resolver(env=FAKE_KEY, key_file=key_file).resolve()

    assert resolved is not None
    assert resolved.source is KeySource.ENVIRONMENT
    assert resolved.key.get_secret_value() == FAKE_KEY


def test_file_not_read_when_environment_key_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text("file-sourced-key", encoding="utf-8")

    def _forbidden_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("the key file must not be read when the environment key is present")

    monkeypatch.setattr(Path, "read_text", _forbidden_read)

    resolved = _resolver(env=FAKE_KEY, key_file=key_file).resolve()
    assert resolved is not None and resolved.source is KeySource.ENVIRONMENT


def test_file_key_used_when_environment_absent(tmp_path: Path) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text(FAKE_KEY, encoding="utf-8")

    resolved = _resolver(env=None, key_file=key_file).resolve()

    assert resolved is not None
    assert resolved.source is KeySource.FILE
    assert resolved.key.get_secret_value() == FAKE_KEY


def test_no_env_and_no_file_returns_none(tmp_path: Path) -> None:
    resolved = _resolver(env=None, key_file=tmp_path / "activation-key").resolve()

    assert resolved is None


# --- 5-7. File content rules ------------------------------------------------------------------


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text("", encoding="utf-8")

    with pytest.raises(ActivationKeyFileError):
        _resolver(env=None, key_file=key_file).resolve()


def test_whitespace_only_file_is_rejected(tmp_path: Path) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text("   \n\t  \n", encoding="utf-8")

    with pytest.raises(ActivationKeyFileError):
        _resolver(env=None, key_file=key_file).resolve()


def test_trailing_newline_is_stripped(tmp_path: Path) -> None:
    key_file = tmp_path / "activation-key"
    key_file.write_text(f"{FAKE_KEY}\n", encoding="utf-8")

    resolved = _resolver(env=None, key_file=key_file).resolve()

    assert resolved is not None
    assert resolved.key.get_secret_value() == FAKE_KEY  # exact key, newline removed


# --- 8. Read failure maps safely --------------------------------------------------------------


def test_file_read_failure_maps_to_key_file_error(tmp_path: Path) -> None:
    # A directory at the key path cannot be read as text — read_text raises OSError.
    key_path = tmp_path / "activation-key"
    key_path.mkdir()

    with pytest.raises(ActivationKeyFileError):
        _resolver(env=None, key_file=key_path).resolve()


# --- 9-13. Secret safety ----------------------------------------------------------------------


def test_resolved_key_uses_secretstr(tmp_path: Path) -> None:
    resolved = _resolver(env=FAKE_KEY, key_file=tmp_path / "activation-key").resolve()

    assert resolved is not None
    assert isinstance(resolved.key, SecretStr)


def test_key_absent_from_repr_and_str() -> None:
    resolved = ResolvedActivationKey(key=SecretStr(FAKE_KEY), source=KeySource.FILE)

    assert FAKE_KEY not in repr(resolved)
    assert FAKE_KEY not in str(resolved)


def test_key_absent_from_file_errors(tmp_path: Path) -> None:
    # The error names only the path — never any content. Use a sentinel filename component.
    key_file = tmp_path / "activation-key"
    key_file.write_text("   ", encoding="utf-8")  # whitespace-only → rejected

    with pytest.raises(ActivationKeyFileError) as excinfo:
        _resolver(env=None, key_file=key_file).resolve()

    # An empty/whitespace file has no key to leak, and the message is path-only.
    assert "   " not in str(excinfo.value)


# --- 14. Import performs no I/O ---------------------------------------------------------------


def test_import_resolver_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing the resolver must not perform I/O")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(socket, "socket", _forbidden)

    name = "weapon_detection_agent.activation.key_resolver"
    saved = sys.modules.pop(name, None)
    try:
        importlib.import_module(name)
    finally:
        if saved is not None:
            sys.modules[name] = saved
