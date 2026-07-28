"""Configuration/error redaction tests (IP-07 T-90, task item 10).

Uses a fabricated, credential-bearing sample RTSP URI (never a real device credential) to prove the
Bridge's configuration-parsing error paths never echo it. ``config.py`` stores the URI verbatim in
``BridgeConfig.source.uri`` (it must — that value is what ``pipeline.py`` passes to
``uridecodebin``), but no error message, log call, or exception string may ever include it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepstream_bridge.config import load_bridge_config
from deepstream_bridge.errors import BridgeConfigurationError

# A fabricated, syntactically-realistic RTSP URL with embedded credentials — never a real device.
_CREDENTIAL_BEARING_URI = "rtsp://admin:Sup3rSecretPassw0rd!@203.0.113.7:554/Streaming/Channels/101"
_SECRET_FRAGMENT = "Sup3rSecretPassw0rd"


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _config_with_credential_uri(tmp_path: Path, *, infer_config_path: str) -> Path:
    content = f"""
[source0]
type=3
uri={_CREDENTIAL_BEARING_URI}

[streammux]
batch-size=1

[primary-gie]
config-file={infer_config_path}
"""
    return _write(tmp_path / "deepstream-app.txt", content)


def test_missing_infer_config_error_never_leaks_the_credential_uri(tmp_path: Path) -> None:
    missing_infer = tmp_path / "does-not-exist.txt"
    app_config = _config_with_credential_uri(tmp_path, infer_config_path=str(missing_infer))

    with pytest.raises(BridgeConfigurationError) as excinfo:
        load_bridge_config(app_config)

    message = str(excinfo.value)
    assert _SECRET_FRAGMENT not in message
    assert _CREDENTIAL_BEARING_URI not in message


def test_unsupported_source_type_error_never_leaks_the_credential_uri(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=1
uri={_CREDENTIAL_BEARING_URI}

[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    with pytest.raises(BridgeConfigurationError) as excinfo:
        load_bridge_config(app_config)

    message = str(excinfo.value)
    assert _SECRET_FRAGMENT not in message
    assert _CREDENTIAL_BEARING_URI not in message


def test_successful_parse_still_stores_the_uri_for_pipeline_construction(tmp_path: Path) -> None:
    """Redaction applies to error/log paths only — the parsed config object itself must still carry
    the real URI, since pipeline.py genuinely needs it to build the source element."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _config_with_credential_uri(tmp_path, infer_config_path=str(infer))

    config = load_bridge_config(app_config)

    assert config.source.uri == _CREDENTIAL_BEARING_URI


def test_missing_config_file_error_never_leaks_arbitrary_path_content(tmp_path: Path) -> None:
    """A generic sanity check alongside the URI-specific ones above: a missing-file error names only
    the path, never file content (there is none to leak here, but the message shape itself must stay
    path-only, matching every other BridgeConfigurationError in this module)."""
    with pytest.raises(BridgeConfigurationError) as excinfo:
        load_bridge_config(tmp_path / "nope.txt")

    message = str(excinfo.value)
    assert _SECRET_FRAGMENT not in message
