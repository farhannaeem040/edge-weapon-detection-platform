"""Generates the atomic, multi-source DeepStream runtime configuration (FS-11 §7, IP-13 T-235).

The repository's committed, reviewed static template (``settings.deepstream_config_path`) remains
the single source of truth for every non-Camera setting — model, tracker, inference interval,
confidence, parser, NMS, encoder, decoder/memory/timeout settings. This module never edits that
template in place. It reads it once, replaces only the source section(s) and
``[streammux] batch-size``, and writes the result to a separate, generated runtime path — one
section per enabled Camera, in ``source_order``.
"""

from __future__ import annotations

import configparser
import logging
import os
from pathlib import Path

from weapon_detection_agent.configuration.models import DeviceCameraConfig

_LOGGER = logging.getLogger("weapon_detection_agent.deepstream.config_generator")

# FS-11 §7: the managed runtime path this module is the sole writer of.
GENERATED_CONFIG_FILENAME = "deepstream.generated.conf"

_SOURCE_SECTION_PREFIX = "source"
_STREAMMUX_SECTION = "streammux"
_RTSP_SOURCE_TYPE = "4"  # DeepStream `type=4` — an RTSP URI source (uri-decode-bin).

# Non-uri/type/enable keys the static template's own [source0] section carries and that must be
# preserved verbatim onto every generated source section (num-sources, gpu-id, cudadec-memtype, and
# any future addition) — copied from whichever source section the template defines, never hardcoded
# here, so a template change never silently drifts from the generator.
# `camera-id`/`output-path` are excluded too (FS-11 §11): they are per-Camera identity written from
# the fetched configuration, so a stale value in the template must never be carried over the top of
# them (the carry loop runs after they are set).
_CARRIED_SOURCE_KEYS_EXCLUDE = frozenset(
    {"enable", "type", "uri", "camera-id", "camera-key", "output-path"}
)


def generate_runtime_config(
    *,
    template_path: Path,
    runtime_dir: Path,
    cameras: tuple[DeviceCameraConfig, ...],
) -> Path:
    """Read the static template and write a multi-source runtime config for ``cameras``.

    ``cameras`` must already be validated (FS-11 §7) — enabled-only, unique CameraId/SourceOrder,
    non-negative order. Written atomically: a temp file in ``runtime_dir``, ``fsync``, then
    ``os.replace`` onto the final path, so a reader (the Bridge, on its next start) never observes a
    partially-written file. Returns the final generated path.
    """
    parser = configparser.ConfigParser()
    # Preserve exact-case option names — DeepStream option keys are case-sensitive (e.g. `gpu-id`).
    parser.optionxform = str  # type: ignore[method-assign,assignment]
    try:
        with template_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except OSError as exc:
        raise RuntimeError(f"cannot read DeepStream template at {template_path}") from exc

    template_source_section = _first_source_section(parser)
    carried_keys = {
        key: value
        for key, value in parser.items(template_source_section)
        if key not in _CARRIED_SOURCE_KEYS_EXCLUDE
    }

    for existing_source_section in _all_source_sections(parser):
        parser.remove_section(existing_source_section)

    for camera in cameras:
        section = f"{_SOURCE_SECTION_PREFIX}{camera.source_order}"
        parser.add_section(section)
        parser.set(section, "enable", "1")
        parser.set(section, "type", _RTSP_SOURCE_TYPE)
        parser.set(section, "uri", camera.stream_url)
        # FS-11 §11: the source section carries its own immutable identity and its own annotated
        # output mount, so the Bridge builds one output branch per source without ever having to
        # infer either from list position or from the display name.
        parser.set(section, "camera-id", str(camera.camera_id))
        # FS-12 §2: written for operator/diagnostic readability. `camera-id` remains the value the
        # source mapping and every DetectionEvent use; `output-path` remains what the Bridge mounts.
        # Nothing downstream reads `camera-key` as an identity.
        parser.set(section, "camera-key", camera.camera_key)
        parser.set(section, "output-path", camera.output_path)
        for key, value in carried_keys.items():
            parser.set(section, key, value)

    if not parser.has_section(_STREAMMUX_SECTION):
        parser.add_section(_STREAMMUX_SECTION)
    parser.set(_STREAMMUX_SECTION, "batch-size", str(len(cameras)))

    runtime_dir.mkdir(parents=True, exist_ok=True)
    final_path = runtime_dir / GENERATED_CONFIG_FILENAME
    tmp_path = runtime_dir / f".{GENERATED_CONFIG_FILENAME}.tmp"

    with tmp_path.open("w", encoding="utf-8") as handle:
        parser.write(handle)
        handle.flush()
        os.fsync(handle.fileno())
    tmp_path.chmod(0o600)
    os.replace(tmp_path, final_path)

    _LOGGER.info(
        "deepstream_runtime_config_generated",
        extra={"camera_count": len(cameras), "path": str(final_path)},
    )
    return final_path


def _first_source_section(parser: configparser.ConfigParser) -> str:
    sections = _all_source_sections(parser)
    if not sections:
        raise RuntimeError("DeepStream template has no [sourceN] section to derive settings from")
    return sections[0]


def _all_source_sections(parser: configparser.ConfigParser) -> list[str]:
    return sorted(s for s in parser.sections() if s.startswith(_SOURCE_SECTION_PREFIX))
