"""Tests for the Agent logging foundation and redaction (IP-02 T-33, §15, §16.1).

Each test configures a **test-specific** logger name and cleans up its handlers afterwards, so no
test leaks configuration into another or into the real ``weapon_detection_agent`` package logger.
Structured output is captured from stdout with ``capsys`` and, for file cases, read back from a
``tmp_path`` file — never a real path and never ``/opt``.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import json
import logging
from pathlib import Path

import pytest
from pydantic import SecretStr

from weapon_detection_agent.config.settings import AgentSettings
from weapon_detection_agent.logging.configuration import (
    LoggingConfigurationError,
    configure_logging,
)
from weapon_detection_agent.logging.redaction import REDACTED, Redactor

TEST_LOGGER = "weapon_detection_agent.tests.logging_suite"


@pytest.fixture
def logger_name() -> str:
    return TEST_LOGGER


@pytest.fixture(autouse=True)
def _reset_loggers() -> None:
    yield
    # Detach and close everything the test attached, on both the test logger and the real package
    # logger, so state never carries between tests.
    for name in (TEST_LOGGER, "weapon_detection_agent"):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(logging.NOTSET)
        logger.propagate = True


def _parse(text: str) -> list[dict]:
    return [json.loads(line) for line in text.strip().splitlines() if line.strip()]


# --- 1-4. Structured stdout output, valid JSON, required fields, UTC ISO-8601 timestamp ---------


def test_stdout_structured_logging(capsys: pytest.CaptureFixture[str], logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info("agent started")

    records = _parse(capsys.readouterr().out)
    assert len(records) == 1


def test_output_is_valid_json_with_required_fields(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).warning("something happened")

    record = _parse(capsys.readouterr().out)[0]
    assert set(record) >= {"timestamp", "level", "logger", "message"}
    assert record["level"] == "WARNING"
    assert record["logger"] == logger_name
    assert record["message"] == "something happened"


def test_timestamp_is_utc_iso8601(capsys: pytest.CaptureFixture[str], logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info("tick")

    timestamp = _parse(capsys.readouterr().out)[0]["timestamp"]
    parsed = _dt.datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == _dt.timedelta(0)
    assert timestamp.endswith("+00:00")


# --- 5. Configured level respected -------------------------------------------------------------


def test_configured_level_is_respected(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(log_level="WARNING", logger_name=logger_name)
    logger = logging.getLogger(logger_name)
    logger.info("suppressed")
    logger.warning("emitted")

    records = _parse(capsys.readouterr().out)
    assert [r["message"] for r in records] == ["emitted"]


def test_invalid_level_is_rejected(logger_name: str) -> None:
    with pytest.raises(ValueError):
        configure_logging(log_level="VERBOSE", logger_name=logger_name)


# --- 6. Idempotent configuration ---------------------------------------------------------------


def test_repeated_configuration_does_not_duplicate_handlers(logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    configure_logging(log_level="INFO", logger_name=logger_name)
    configure_logging(log_level="DEBUG", logger_name=logger_name)

    # Only the single stdout handler remains, not three.
    assert len(logging.getLogger(logger_name).handlers) == 1


# --- 7-9. Key-based redaction: nested, case-insensitive, inside collections ---------------------


def test_nested_sensitive_keys_are_redacted(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "state",
        extra={"context": {"activation_key": "k", "inner": {"password": "p"}}},
    )

    context = _parse(capsys.readouterr().out)[0]["context"]
    assert context["activation_key"] == REDACTED
    assert context["inner"]["password"] == REDACTED


def test_sensitive_keys_are_matched_case_insensitively(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "state",
        extra={"context": {"Activation_Key": "a", "SHARED-SECRET": "b", "ApiKey": "c"}},
    )

    context = _parse(capsys.readouterr().out)[0]["context"]
    assert list(context.values()) == [REDACTED, REDACTED, REDACTED]


def test_collections_are_redacted(capsys: pytest.CaptureFixture[str], logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "state",
        extra={"context": {"items": [{"token": "t"}, {"harmless": "v"}]}},
    )

    items = _parse(capsys.readouterr().out)[0]["context"]["items"]
    assert items[0]["token"] == REDACTED
    assert items[1]["harmless"] == "v"


# --- 10. Redaction does not mutate the input ---------------------------------------------------


def test_redaction_does_not_mutate_input() -> None:
    original = {"activation_key": "k", "nested": {"password": "p"}, "keep": [1, 2]}
    redactor = Redactor()

    result = redactor.redact(original)

    assert original == {"activation_key": "k", "nested": {"password": "p"}, "keep": [1, 2]}
    assert result["activation_key"] == REDACTED
    assert result["nested"]["password"] == REDACTED


# --- 11. Bearer tokens ------------------------------------------------------------------------


def test_bearer_token_is_redacted(capsys: pytest.CaptureFixture[str], logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "request rejected: Authorization: Bearer abc.def.ghi",
        extra={"context": {"header": "Bearer zzz.yyy"}},
    )

    record = _parse(capsys.readouterr().out)[0]
    assert "abc.def.ghi" not in json.dumps(record)
    assert "zzz.yyy" not in json.dumps(record)
    assert "Bearer [REDACTED]" in record["message"]
    assert record["context"]["header"] == "Bearer [REDACTED]"


# --- 12. Known literal secret values -----------------------------------------------------------


def test_known_literal_secret_is_redacted(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(
        log_level="INFO", sensitive_values=["literal-secret-123"], logger_name=logger_name
    )
    logging.getLogger(logger_name).info(
        "value was literal-secret-123",
        extra={"context": {"note": "contains literal-secret-123 here"}},
    )

    dumped = json.dumps(_parse(capsys.readouterr().out)[0])
    assert "literal-secret-123" not in dumped
    assert REDACTED in dumped


def test_secretstr_sensitive_value_is_accepted_and_redacted(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(
        log_level="INFO",
        sensitive_values=[SecretStr("keyid.supersecret")],
        logger_name=logger_name,
    )
    logging.getLogger(logger_name).info("emitting keyid.supersecret in the clear")

    assert "supersecret" not in json.dumps(_parse(capsys.readouterr().out)[0])


# --- 13. SecretStr values ----------------------------------------------------------------------


def test_secretstr_value_is_redacted(capsys: pytest.CaptureFixture[str], logger_name: str) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "state", extra={"context": {"value": SecretStr("do-not-show")}}
    )

    record = _parse(capsys.readouterr().out)[0]
    assert record["context"]["value"] == REDACTED
    assert "do-not-show" not in json.dumps(record)


# --- 14-15. Exception message redacted, traceback still useful ---------------------------------


def test_exception_message_secret_is_redacted_and_traceback_is_useful(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(
        log_level="INFO", sensitive_values=["exc-secret-xyz"], logger_name=logger_name
    )
    logger = logging.getLogger(logger_name)

    try:
        raise ValueError("activation failed for exc-secret-xyz")
    except ValueError:
        logger.error("activation error", exc_info=True)

    record = _parse(capsys.readouterr().out)[0]
    exception = record["exception"]

    # The secret is gone from message and traceback...
    assert "exc-secret-xyz" not in json.dumps(record)
    assert exception["message"] == REDACTED.join(["activation failed for ", ""])
    # ...but the type and a real stack trace remain useful.
    assert exception["type"] == "ValueError"
    assert "Traceback (most recent call last)" in exception["traceback"]
    assert "ValueError" in exception["traceback"]


# --- 16-17. File output, and stdout/file equivalence -------------------------------------------


def test_file_output_writes_structured_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, logger_name: str
) -> None:
    log_file = tmp_path / "agent.log"
    configure_logging(log_level="INFO", log_file=log_file, logger_name=logger_name)
    logging.getLogger(logger_name).info("to file")

    records = _parse(log_file.read_text(encoding="utf-8"))
    assert records[0]["message"] == "to file"


def test_stdout_and_file_records_are_equivalent(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, logger_name: str
) -> None:
    log_file = tmp_path / "agent.log"
    configure_logging(log_level="INFO", log_file=log_file, logger_name=logger_name)
    logging.getLogger(logger_name).info("same everywhere", extra={"operation": "startup"})

    stdout_record = _parse(capsys.readouterr().out)[0]
    file_record = _parse(log_file.read_text(encoding="utf-8"))[0]
    assert stdout_record == file_record
    assert stdout_record["operation"] == "startup"


# --- 18. Missing file parent fails clearly without creating directories ------------------------


def test_missing_file_directory_fails_without_creating_it(tmp_path: Path, logger_name: str) -> None:
    missing_dir = tmp_path / "not-created"
    log_file = missing_dir / "agent.log"

    with pytest.raises(LoggingConfigurationError):
        configure_logging(log_level="INFO", log_file=log_file, logger_name=logger_name)

    # Directory provisioning is T-34's job; this task must not have created it.
    assert not missing_dir.exists()


# --- 19. Non-serializable context does not crash logging ---------------------------------------


def test_non_serializable_context_does_not_crash(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque object>"

    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info("state", extra={"context": {"obj": Opaque()}})

    record = _parse(capsys.readouterr().out)[0]
    assert record["context"]["obj"] == "<Opaque object>"


# --- 20. Harmless metadata is not redacted -----------------------------------------------------


def test_harmless_metadata_is_not_redacted(
    capsys: pytest.CaptureFixture[str], logger_name: str
) -> None:
    configure_logging(log_level="INFO", logger_name=logger_name)
    logging.getLogger(logger_name).info(
        "state",
        extra={
            "context": {
                "secret_status": "configured",
                "token_count": 3,
                "credential_identifier": "admin@example.invalid",
            }
        },
    )

    context = _parse(capsys.readouterr().out)[0]["context"]
    assert context["secret_status"] == "configured"
    assert context["token_count"] == 3
    assert context["credential_identifier"] == "admin@example.invalid"


# --- 21-22. Importing main configures nothing and creates nothing ------------------------------


def test_importing_main_does_not_configure_logging() -> None:
    root = logging.getLogger()
    before = len(root.handlers)

    importlib.reload(importlib.import_module("weapon_detection_agent.main"))

    assert len(root.handlers) == before
    # The package logger gains no handlers merely from importing the app.
    assert logging.getLogger("weapon_detection_agent").handlers == []


def test_importing_main_creates_no_log_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    importlib.reload(importlib.import_module("weapon_detection_agent.main"))

    # No log file (or any file) is produced as a side effect of importing the app.
    assert list(tmp_path.iterdir()) == []


# --- 23. Settings representation never exposes the Activation Key -------------------------------


def test_settings_repr_does_not_expose_activation_key() -> None:
    settings = AgentSettings(
        backend_base_url="http://localhost:5230", activation_key="keyid.topsecret"
    )

    assert "topsecret" not in repr(settings)
    assert "topsecret" not in str(settings)


# --- 24-25. End-to-end: no secret in stdout or file, across message/context/exception ----------


def test_no_secret_reaches_stdout_or_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, logger_name: str
) -> None:
    secret = "keyid.absolutely-secret"
    log_file = tmp_path / "agent.log"
    configure_logging(
        log_level="INFO", log_file=log_file, sensitive_values=[secret], logger_name=logger_name
    )
    logger = logging.getLogger(logger_name)

    try:
        raise RuntimeError(f"failed with {secret}")
    except RuntimeError:
        logger.error(
            f"leaky message {secret}",
            extra={"context": {"activation_key": secret, "note": f"and {secret} again"}},
            exc_info=True,
        )

    stdout = capsys.readouterr().out
    file_text = log_file.read_text(encoding="utf-8")

    assert "absolutely-secret" not in stdout
    assert "absolutely-secret" not in file_text
    assert REDACTED in stdout
    assert REDACTED in file_text
