"""Static content checks for the committed `yolo26-fp16` DeepStream profile (FS-13/IP-15 T-298).

These tests read the actual repository-committed profile files under
``deployment/jetson/deepstream/profiles/yolo26-fp16/`` and the active `deepstream-app.txt` pointer —
not a synthetic fixture — because FS-13's acceptance criteria are about the real production
artifact, not about generic Agent mechanism (which is already covered profile-agnostically
elsewhere, e.g. ``test_deepstream_config_generator.py``, ``test_profile_preflight.py``).
"""

from __future__ import annotations

import configparser
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEEPSTREAM_DIR = _REPO_ROOT / "deployment" / "jetson" / "deepstream"
_PROFILE_DIR = _DEEPSTREAM_DIR / "profiles" / "yolo26-fp16"
_APP_CONFIG = _DEEPSTREAM_DIR / "deepstream-app.txt"


def _read_infer_config() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False)
    parser.read(_PROFILE_DIR / "infer-config.txt", encoding="utf-8")
    return parser


def _manifest_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (_PROFILE_DIR / "manifest.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


def test_profile_directory_exists() -> None:
    assert _PROFILE_DIR.is_dir()


def test_labels_are_exactly_gun_and_knife() -> None:
    labels = (_PROFILE_DIR / "labels.txt").read_text(encoding="utf-8").splitlines()
    assert labels == ["gun", "knife"]


def test_labels_contain_no_coco_or_person_mapping() -> None:
    text = (_PROFILE_DIR / "labels.txt").read_text(encoding="utf-8")
    assert "person" not in text.lower()


def test_engine_path_is_production_and_not_best_engine() -> None:
    props = _read_infer_config()["property"]
    engine_path = props["model-engine-file"]
    assert engine_path == "/opt/weapon-detection/models/yolo26-fp16/model.engine"
    assert "best.engine" not in engine_path
    assert "/home/farhan/Desktop" not in engine_path


def test_labelfile_path_is_production() -> None:
    props = _read_infer_config()["property"]
    assert props["labelfile-path"] == (
        "/opt/weapon-detection/config/deepstream/profiles/yolo26-fp16/labels.txt"
    )
    assert "/home/farhan/Desktop" not in props["labelfile-path"]


def test_parser_is_nvdsinfer_parse_yolo() -> None:
    props = _read_infer_config()["property"]
    assert props["parse-bbox-func-name"] == "NvDsInferParseYolo"


def test_custom_lib_path_is_production() -> None:
    props = _read_infer_config()["property"]
    assert props["custom-lib-path"] == (
        "/opt/weapon-detection/lib/yolo26/libnvdsinfer_custom_impl_Yolo.so"
    )
    assert "/home/farhan/Desktop" not in props["custom-lib-path"]


def test_engine_create_func_is_yolo_cuda_engine_get() -> None:
    props = _read_infer_config()["property"]
    assert props["engine-create-func-name"] == "NvDsInferYoloCudaEngineGet"


def test_num_detected_classes_is_two() -> None:
    props = _read_infer_config()["property"]
    assert props["num-detected-classes"] == "2"


def test_network_mode_is_fp16() -> None:
    props = _read_infer_config()["property"]
    assert props["network-mode"] == "2"


def test_infer_dims_is_640x640() -> None:
    props = _read_infer_config()["property"]
    assert props["infer-dims"] == "3;640;640"


def test_batch_size_is_one_matching_the_static_engine_binding() -> None:
    props = _read_infer_config()["property"]
    assert props["batch-size"] == "1"


def test_cluster_mode_is_four() -> None:
    props = _read_infer_config()["property"]
    assert props["cluster-mode"] == "4"


def test_preprocessing_matches_yolo26_not_yolov4_tao_values() -> None:
    props = _read_infer_config()["property"]
    assert props["net-scale-factor"] == "0.0039215697906911373"
    assert props["model-color-format"] == "0"
    assert "offsets" not in props
    assert "tlt-model-key" not in props


def test_maintain_aspect_ratio_and_symmetric_padding() -> None:
    props = _read_infer_config()["property"]
    assert props["maintain-aspect-ratio"] == "1"
    assert props["symmetric-padding"] == "1"


def test_class_attrs_topk_and_threshold() -> None:
    config = _read_infer_config()
    section = config["class-attrs-all"]
    assert section["pre-cluster-threshold"] == "0.25"
    assert section["topk"] == "300"


def test_no_onnx_file_engine_rebuild_on_normal_startup() -> None:
    text = (_PROFILE_DIR / "infer-config.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("onnx-file="), (
            "normal startup must never rebuild the engine from ONNX"
        )


def test_manifest_engine_sha256_matches_device_verified_value() -> None:
    values = _manifest_values()
    assert values["ENGINE_SHA256"] == (
        "ed4810762244c78d04be7617b37e2bd20f8c8252b0f1ed2b5c33475e6eb3f9c1"
    )
    assert values["PROFILE_NAME"] == "yolo26-fp16"
    assert values["CLASS_COUNT"] == "2"
    assert values["PARSER_FUNC"] == "NvDsInferParseYolo"
    assert values["PARSER_LIB"] == (
        "/opt/weapon-detection/lib/yolo26/libnvdsinfer_custom_impl_Yolo.so"
    )


def test_manifest_has_all_fields_deploy_engine_sh_requires() -> None:
    required = {
        "PROFILE_NAME",
        "ENGINE_SHA256",
        "ENGINE_PRECISION",
        "INPUT_DIMENSIONS",
        "CLASS_COUNT",
        "LABELS_FILE",
        "OUTPUT_BLOB_NAMES",
        "DEEPSTREAM_VERSION",
        "TENSORRT_VERSION",
        "TARGET_DEVICE",
        "VALIDATED_DATE",
        "VALIDATED_STATUS",
    }
    values = _manifest_values()
    for field in required:
        assert values.get(field), f"manifest missing required field: {field}"


def test_active_primary_gie_points_at_yolo26_profile() -> None:
    text = _APP_CONFIG.read_text(encoding="utf-8")
    assert (
        "config-file=/opt/weapon-detection/config/deepstream/profiles/yolo26-fp16/infer-config.txt"
        in text
    )
    assert "profiles/yolov4-fp16/infer-config.txt" not in text


def test_tracker_is_enabled() -> None:
    config = configparser.ConfigParser(strict=False)
    config.read(_APP_CONFIG, encoding="utf-8")
    assert config["tracker"]["enable"] == "1"


def test_yolov4_profile_still_present_for_rollback() -> None:
    rollback_dir = _DEEPSTREAM_DIR / "profiles" / "yolov4-fp16"
    assert rollback_dir.is_dir()
    assert (rollback_dir / "infer-config.txt").is_file()
    assert (rollback_dir / "labels.txt").is_file()
    assert (rollback_dir / "manifest.env").is_file()
