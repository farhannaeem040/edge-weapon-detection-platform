"""Unit tests for the generated multi-source DeepStream runtime config (FS-11 §7, IP-13 T-235)."""

from __future__ import annotations

import configparser
from pathlib import Path
from uuid import uuid4

from weapon_detection_agent.configuration.models import DeviceCameraConfig
from weapon_detection_agent.deepstream.config_generator import (
    GENERATED_CONFIG_FILENAME,
    generate_runtime_config,
)

_TEMPLATE = """[application]
enable-perf-measurement=1

[source0]
enable=1
type=3
uri=file:///opt/weapon-detection/samples/deepstream/input.mp4
num-sources=1
gpu-id=0
cudadec-memtype=0

[streammux]
gpu-id=0
live-source=0
batch-size=1
width=1920
height=1080

[primary-gie]
enable=1
gpu-id=0
gie-unique-id=1
config-file=/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/infer-config.txt

[tracker]
enable=0
"""


def _camera(source_order: int, stream_url: str) -> DeviceCameraConfig:
    camera_id = uuid4()
    return DeviceCameraConfig(
        camera_id=camera_id,
        camera_key=str(camera_id),
        name=f"Camera {source_order}",
        stream_url=stream_url,
        enabled=True,
        source_order=source_order,
        output_path=f"cameras/{camera_id}",
    )


def _write_template(tmp_path: Path) -> Path:
    template_path = tmp_path / "deepstream-app.txt"
    template_path.write_text(_TEMPLATE, encoding="utf-8")
    return template_path


def _read_generated(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    parser.read(path, encoding="utf-8")
    return parser


def test_single_camera_generates_one_source_section(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"
    cameras = (_camera(0, "rtsp://camera.example.invalid:554/s0"),)

    generated_path = generate_runtime_config(
        template_path=template_path, runtime_dir=runtime_dir, cameras=cameras
    )

    assert generated_path == runtime_dir / GENERATED_CONFIG_FILENAME
    parser = _read_generated(generated_path)
    assert parser.get("source0", "uri") == "rtsp://camera.example.invalid:554/s0"
    assert parser.get("source0", "type") == "4"
    assert parser.get("source0", "enable") == "1"
    # Non-uri/type/enable keys carried over from the template's own [source0] section.
    assert parser.get("source0", "gpu-id") == "0"
    assert parser.get("source0", "cudadec-memtype") == "0"
    assert parser.get("streammux", "batch-size") == "1"


def test_two_cameras_generate_two_source_sections_with_correct_batch_size(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"
    cameras = (
        _camera(0, "rtsp://camera.example.invalid:554/s0"),
        _camera(1, "rtsp://camera.example.invalid:554/s1"),
    )

    generated_path = generate_runtime_config(
        template_path=template_path, runtime_dir=runtime_dir, cameras=cameras
    )

    parser = _read_generated(generated_path)
    assert parser.get("source0", "uri") == "rtsp://camera.example.invalid:554/s0"
    assert parser.get("source1", "uri") == "rtsp://camera.example.invalid:554/s1"
    assert parser.get("streammux", "batch-size") == "2"


def test_non_source_sections_are_preserved_unchanged(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"
    cameras = (_camera(0, "rtsp://camera.example.invalid:554/s0"),)

    generated_path = generate_runtime_config(
        template_path=template_path, runtime_dir=runtime_dir, cameras=cameras
    )

    parser = _read_generated(generated_path)
    assert parser.get("tracker", "enable") == "0"
    assert (
        parser.get("primary-gie", "config-file")
        == "/opt/weapon-detection/config/deepstream/profiles/yolov4-fp16/infer-config.txt"
    )
    assert parser.get("streammux", "width") == "1920"


def test_zero_cameras_generates_zero_source_sections_and_batch_size_zero(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"

    generated_path = generate_runtime_config(
        template_path=template_path, runtime_dir=runtime_dir, cameras=()
    )

    parser = _read_generated(generated_path)
    assert not any(s.startswith("source") for s in parser.sections())
    assert parser.get("streammux", "batch-size") == "0"


def test_regenerating_replaces_the_previous_source_sections(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"

    generate_runtime_config(
        template_path=template_path,
        runtime_dir=runtime_dir,
        cameras=(
            _camera(0, "rtsp://camera.example.invalid:554/s0"),
            _camera(1, "rtsp://camera.example.invalid:554/s1"),
        ),
    )
    generated_path = generate_runtime_config(
        template_path=template_path,
        runtime_dir=runtime_dir,
        cameras=(_camera(0, "rtsp://camera.example.invalid:554/only"),),
    )

    parser = _read_generated(generated_path)
    assert parser.get("source0", "uri") == "rtsp://camera.example.invalid:554/only"
    assert not parser.has_section("source1")
    assert parser.get("streammux", "batch-size") == "1"


def test_no_temp_file_left_behind_after_generation(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    runtime_dir = tmp_path / "runtime"

    generate_runtime_config(
        template_path=template_path, runtime_dir=runtime_dir, cameras=(_camera(0, "rtsp://x/y"),)
    )

    leftover_temp_files = list(runtime_dir.glob(".*.tmp"))
    assert leftover_temp_files == []


# --- FS-11 §11: per-source identity and annotated output mount ----------------------------------


def test_each_generated_source_carries_its_camera_id_and_output_path(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    cameras = (
        _camera(0, "rtsp://camera.example.invalid:554/a"),
        _camera(1, "rtsp://camera.example.invalid:554/b"),
    )

    generated = generate_runtime_config(
        template_path=template_path, runtime_dir=tmp_path, cameras=cameras
    )
    parser = _read_generated(generated)

    for camera in cameras:
        section = f"source{camera.source_order}"
        # The Bridge reads identity from the section itself — never from list position or Name.
        assert parser.get(section, "camera-id") == str(camera.camera_id)
        assert parser.get(section, "output-path") == camera.output_path


def test_generated_output_paths_are_unique_per_source(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    cameras = tuple(
        _camera(index, f"rtsp://camera.example.invalid:554/s{index}") for index in range(4)
    )

    generated = generate_runtime_config(
        template_path=template_path, runtime_dir=tmp_path, cameras=cameras
    )
    parser = _read_generated(generated)

    output_paths = [parser.get(f"source{camera.source_order}", "output-path") for camera in cameras]
    assert len(set(output_paths)) == len(output_paths)


def test_output_count_always_equals_enabled_camera_count(tmp_path: Path) -> None:
    template_path = _write_template(tmp_path)
    for count in (1, 2, 4, 8):
        cameras = tuple(
            _camera(index, f"rtsp://camera.example.invalid:554/s{index}") for index in range(count)
        )
        generated = generate_runtime_config(
            template_path=template_path, runtime_dir=tmp_path, cameras=cameras
        )
        parser = _read_generated(generated)

        sources = [s for s in parser.sections() if s.startswith("source")]
        outputs = [s for s in sources if parser.get(s, "output-path", fallback="")]
        assert len(sources) == count
        assert len(outputs) == count
        assert parser.get("streammux", "batch-size") == str(count)


def test_template_source_keys_never_override_generated_identity(tmp_path: Path) -> None:
    # A stale camera-id/output-path in the template must not be carried over the real values.
    template_path = tmp_path / "stale-template.txt"
    template_path.write_text(
        _TEMPLATE + "\ncamera-id = stale-value\noutput-path = cameras/stale\n",
        encoding="utf-8",
    )
    camera = _camera(0, "rtsp://camera.example.invalid:554/a")

    generated = generate_runtime_config(
        template_path=template_path, runtime_dir=tmp_path, cameras=(camera,)
    )
    parser = _read_generated(generated)

    assert parser.get("source0", "camera-id") == str(camera.camera_id)
    assert parser.get("source0", "output-path") == camera.output_path
