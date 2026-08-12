"""Static regression checks for the multi-source pipeline construction (FS-11 §8, IP-13 T-243/
T-246).

Mirrors ``test_pipeline_source_construction.py``'s established style: ``pipeline.py`` requires real
``gi``/``Gst``/``pyds`` at import time, so this suite proves the source text builds one bin per
configured source and requests the correct ``sink_%u`` pad per source, without needing a real
GStreamer runtime — the functional proof (real multi-camera negotiation) is the isolated Jetson
multi-camera validation (task brief Phase 21), not here.
"""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")

_CONFIG_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "config.py"
).read_text(encoding="utf-8")


def test_build_creates_one_source_element_per_configured_source() -> None:
    # One source bin per cfg.sources entry — never a single hardcoded cfg.source.
    assert "for src in cfg.sources" in _PIPELINE_SOURCE
    assert "cfg.source)" not in _PIPELINE_SOURCE  # the old singular attribute must be gone


def test_each_source_element_gets_a_unique_name() -> None:
    # GStreamer element names must be unique per-bin; the source_index makes each one distinct.
    assert 'f"uri-source-bin-{cfg.source_index}"' in _PIPELINE_SOURCE


def test_pad_added_callback_bound_to_its_own_sources_index() -> None:
    # Each source's pad-added callback is bound to that specific source's own index — never a
    # shared/hardcoded "sink_0" for every source.
    assert "source_cfg.source_index" in _PIPELINE_SOURCE
    assert 'f"sink_{sink_pad_index}"' in _PIPELINE_SOURCE
    assert '"sink_0"' not in _PIPELINE_SOURCE  # the old single-source hardcoded pad name is gone


def test_streammux_batch_size_comes_from_config_not_a_computed_source_count() -> None:
    # The Bridge trusts the config file's own streammux.batch-size (set by the Agent's generator to
    # the enabled-camera count) — it never recomputes batch-size from len(cfg.sources) itself,
    # keeping the Bridge as config-driven/generic as every other property (module docstring).
    assert 'streammux.set_property("batch-size", cfg.batch_size)' in _PIPELINE_SOURCE


def test_inference_and_muxing_stay_shared_across_all_sources() -> None:
    # nvstreammux batches multiple sources into one buffer for a single shared nvinfer — the
    # architecture this feature must not change (FS-11 "Important DeepStream clarification").
    # Per-camera *outputs* (FS-11 §11) fan out after inference and deliberately do not affect this.
    assert _PIPELINE_SOURCE.count('self._make("nvinfer"') == 1
    assert _PIPELINE_SOURCE.count('self._make("nvstreammux"') == 1
    # Exactly one demuxer performs the entire fan-out — never one demuxer per camera.
    assert _PIPELINE_SOURCE.count('self._make("nvstreamdemux"') == 1
    # A tiled output is explicitly out of scope (FS-11 §11: "Do not add nvmultistreamtiler").
    assert "nvmultistreamtiler" not in _PIPELINE_SOURCE


def test_encoder_is_built_per_output_branch_not_per_pipeline() -> None:
    # Two encoder construction sites exist by design: the legacy single shared output branch
    # (_attach_rtsp_out, still used when no per-camera output paths are configured) and the
    # per-camera branch builder. Neither is a second *inference* pipeline.
    assert _PIPELINE_SOURCE.count('self._make("nvv4l2h264enc"') == 2
    # The per-camera encoder is named per source index, so N branches cannot collide.
    assert 'f"out-encoder{suffix}"' in _PIPELINE_SOURCE


def test_config_requires_at_least_one_source_section() -> None:
    assert "no [sourceN] section found" in _CONFIG_SOURCE


def test_config_sources_are_a_tuple_not_a_single_value() -> None:
    assert "sources: " in _CONFIG_SOURCE
    assert "tuple[SourceConfig, ...]" in _CONFIG_SOURCE


# --- FS-11 §11: dynamic per-camera annotated outputs --------------------------------------------


def test_output_branches_are_built_by_loop_never_by_a_fixed_camera_count() -> None:
    # The number of outputs must follow the configuration, so no branch count may be hardcoded.
    assert "for offset, source in enumerate(sources)" in _PIPELINE_SOURCE
    for forbidden in ("camera_count == 2", "camera_count == 1", "len(cfg.sources) == 2"):
        assert forbidden not in _PIPELINE_SOURCE


def test_each_output_branch_has_its_own_osd_and_full_encode_chain() -> None:
    # Per-camera OSD (drawn after demux so each output carries only its own detections) plus the
    # required conversion/caps/encode/payload chain.
    for element in (
        'f"out-queue{suffix}"',
        'f"out-preconv{suffix}"',
        'f"out-osd{suffix}"',
        'f"out-postconv{suffix}"',
        'f"out-caps{suffix}"',
        'f"out-encoder{suffix}"',
        'f"out-h264parse{suffix}"',
        'f"out-rtppay{suffix}"',
        'f"out-udpsink{suffix}"',
    ):
        assert element in _PIPELINE_SOURCE


def test_demux_src_pad_uses_the_sources_own_index_not_enumeration_position() -> None:
    # Output N must show Camera N: the demux src pad index is the source's own source_index, the
    # same index it occupies on the streammux sink side.
    assert 'demux.get_request_pad(f"src_{index}")' in _PIPELINE_SOURCE
    assert "index = source.source_index" in _PIPELINE_SOURCE


def test_detection_probe_stays_on_the_batched_pre_demux_pad() -> None:
    # The identity contract (frame_meta.source_id -> CameraId) lives in the batched metadata, so the
    # probe must attach to the demuxer's sink side, never to a per-branch OSD.
    assert "self._attach_probe(demux)" in _PIPELINE_SOURCE
    assert "self._attach_probe(nvosd)" in _PIPELINE_SOURCE  # legacy single-output path retains it


def test_udp_ports_are_allocated_deterministically_two_apart() -> None:
    # RTP and its RTCP companion occupy consecutive ports, so each branch advances the base by two.
    assert "udp_port = cfg.udp_port + (offset * 2)" in _PIPELINE_SOURCE


def test_mount_points_are_derived_from_output_path_never_from_camera_name() -> None:
    assert "source.output_path.strip" in _PIPELINE_SOURCE
    # A display name must never become part of a mount identifier.
    assert "source.name" not in _PIPELINE_SOURCE


def test_registered_mounts_are_tracked_and_removed_on_shutdown() -> None:
    assert "self._mount_points_added.append" in _PIPELINE_SOURCE
    assert "mount_points.remove_factory(mount_point)" in _PIPELINE_SOURCE


def test_duplicate_output_paths_are_rejected_by_the_config_parser() -> None:
    assert "the same output-path" in _CONFIG_SOURCE


def test_output_path_is_validated_as_a_safe_relative_mount() -> None:
    assert "output-path must be a safe relative RTSP mount path" in _CONFIG_SOURCE
    assert "output-path must not contain control characters" in _CONFIG_SOURCE
