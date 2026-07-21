"""Unit tests for the activation result model (IP-02 T-37, §11).

Fixtures use obviously fake values (`device-test-001`, `test-shared-secret`, `branch-test-001`); no
real credential appears. The fake secret must never surface in a repr, str, or error, which several
tests assert.
"""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import SecretStr

from weapon_detection_agent.activation.models import ActivationResult

FAKE_SECRET = "test-shared-secret-ZZZ"
DEVICE_ID = "device-test-001"
BRANCH_ID = "branch-test-001"


def _valid_data() -> dict[str, object]:
    return {"deviceId": DEVICE_ID, "sharedSecret": FAKE_SECRET, "branchId": BRANCH_ID}


# --- Construction and field parsing ------------------------------------------------------------


def test_from_response_data_parses_all_fields() -> None:
    result = ActivationResult.from_response_data(_valid_data())

    assert result.device_id == DEVICE_ID
    assert result.branch_id == BRANCH_ID
    assert isinstance(result.shared_secret, SecretStr)
    assert result.shared_secret.get_secret_value() == FAKE_SECRET


def test_extra_fields_in_data_are_ignored() -> None:
    # The Backend serializer omits nulls and returns only the three fields; a stray extra field must
    # not break parsing (forward-compatibility), and no configuration is invented.
    data = _valid_data() | {"unexpected": "ignored", "config": {"x": 1}}

    result = ActivationResult.from_response_data(data)

    assert result.device_id == DEVICE_ID
    assert not hasattr(result, "config")


# --- Secret redaction --------------------------------------------------------------------------


def test_secret_absent_from_repr_and_str() -> None:
    result = ActivationResult.from_response_data(_valid_data())

    assert FAKE_SECRET not in repr(result)
    assert FAKE_SECRET not in str(result)


def test_secret_not_json_serializable_via_asdict() -> None:
    # dataclasses.asdict keeps the SecretStr instance; it is not JSON-serializable, so an accidental
    # dump raises rather than exposing the value.
    import json

    dumped = dataclasses.asdict(ActivationResult.from_response_data(_valid_data()))
    with pytest.raises(TypeError):
        json.dumps(dumped)


# --- Rejection of malformed data ---------------------------------------------------------------


def test_non_dict_data_is_rejected() -> None:
    with pytest.raises(ValueError):
        ActivationResult.from_response_data(None)


@pytest.mark.parametrize("field", ["deviceId", "sharedSecret", "branchId"])
def test_missing_field_is_rejected(field: str) -> None:
    data = _valid_data()
    del data[field]

    with pytest.raises(ValueError):
        ActivationResult.from_response_data(data)


@pytest.mark.parametrize("field", ["deviceId", "sharedSecret", "branchId"])
def test_blank_field_is_rejected(field: str) -> None:
    data = _valid_data()
    data[field] = "   "

    with pytest.raises(ValueError):
        ActivationResult.from_response_data(data)


@pytest.mark.parametrize("field", ["deviceId", "sharedSecret", "branchId"])
def test_wrong_type_field_is_rejected(field: str) -> None:
    data = _valid_data()
    data[field] = 12345

    with pytest.raises(ValueError):
        ActivationResult.from_response_data(data)


def test_validation_error_does_not_expose_secret() -> None:
    data = _valid_data()
    data["branchId"] = "   "  # invalid, while a valid secret is present in the same payload

    with pytest.raises(ValueError) as excinfo:
        ActivationResult.from_response_data(data)

    assert FAKE_SECRET not in str(excinfo.value)


# --- Direct construction still enforces invariants ---------------------------------------------


def test_direct_construction_rejects_blank_secret() -> None:
    with pytest.raises(ValueError):
        ActivationResult(device_id=DEVICE_ID, shared_secret=SecretStr("  "), branch_id=BRANCH_ID)


def test_direct_construction_rejects_blank_device_id() -> None:
    with pytest.raises(ValueError):
        ActivationResult(device_id="", shared_secret=SecretStr(FAKE_SECRET), branch_id=BRANCH_ID)
