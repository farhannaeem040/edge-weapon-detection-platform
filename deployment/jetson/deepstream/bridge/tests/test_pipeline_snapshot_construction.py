"""Static regression checks + one runtime check for the snapshot branch (IP-10 T-134/T-135/T-138,
FS-08 §3).

Same rationale as ``test_pipeline_source_construction.py``: ``pipeline.py`` requires real
``gi``/``Gst``/``pyds`` at import time, so most of these are source-text checks proving which
elements/properties/control-flow are wired, cheap enough to run everywhere (including this suite's
Windows dev environment). The *functional* proof (real JPEG bytes, real valve gating, real FPS
impact) is the Jetson isolated capture validation (IP-10 T-158), not here.

One check (``test_constructor_requires_tracker_and_cache_when_snapshot_enabled``) *is* a real
runtime instantiation, not a source check — ``BridgePipeline.__init__`` validates its
``candidate_tracker``/``snapshot_cache`` arguments before calling ``import_gst()``/``import_pyds()``,
so the ``ValueError`` path is reachable without real GStreamer/pyds bindings installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")


def test_tee_is_created_only_inside_the_snapshot_enabled_branch() -> None:
    assert 'if cfg.snapshot.enabled and not dynamic_outputs:' in _PIPELINE_SOURCE
    assert 'tee = self._make("tee", "osd-tee")' in _PIPELINE_SOURCE


def test_existing_branch_functions_are_reused_unchanged_for_both_paths() -> None:
    """The existing RTSP-out/fakesink branch attachment calls must be reused verbatim (same
    ``_attach_rtsp_out``/``_attach_fakesink`` methods, unmodified bodies) for both the
    snapshot-enabled (tee) and snapshot-disabled (nvosd direct) cases — proving the existing branch
    construction logic itself was never touched by IP-10."""
    assert "self._attach_rtsp_out(pipeline, branch_source, cfg.rtsp_out)" in _PIPELINE_SOURCE
    assert "self._attach_fakesink(pipeline, branch_source)" in _PIPELINE_SOURCE
    # The exact literal chain from before IP-10 still exists, untouched, inside _attach_rtsp_out.
    assert "chain = (nvosd, nvvidconv2, encoder, h264parse, rtppay, udpsink)" in _PIPELINE_SOURCE
    assert 'if not nvosd.link(fakesink):' in _PIPELINE_SOURCE


def test_snapshot_branch_element_chain_matches_fs08_design() -> None:
    for factory_call in (
        '"queue", f"snapshot-queue{suffix}"',
        '"valve", f"snapshot-valve{suffix}"',
        '"nvjpegenc", f"snapshot-jpeg-encoder{suffix}"',
        '"appsink", f"snapshot-appsink{suffix}"',
    ):
        assert factory_call in _PIPELINE_SOURCE, f"missing snapshot branch element: {factory_call}"
    assert "chain = (tee, queue_el, valve, jpegenc, appsink)" in _PIPELINE_SOURCE


def test_queue_is_leaky_downstream_and_bounded() -> None:
    assert 'queue_el.set_property("leaky", _QUEUE_LEAK_DOWNSTREAM)' in _PIPELINE_SOURCE
    assert 'queue_el.set_property("max-size-buffers", _SNAPSHOT_QUEUE_MAX_SIZE_BUFFERS)' in (
        _PIPELINE_SOURCE
    )


def test_valve_defaults_closed() -> None:
    assert 'valve.set_property("drop", True)' in _PIPELINE_SOURCE


def test_jpeg_quality_is_config_driven() -> None:
    assert 'jpegenc.set_property("quality", cfg.jpeg_quality)' in _PIPELINE_SOURCE


def test_appsink_is_configured_for_non_blocking_single_buffer_pull() -> None:
    assert 'appsink.set_property("emit-signals", True)' in _PIPELINE_SOURCE
    assert 'appsink.set_property("max-buffers", 1)' in _PIPELINE_SOURCE
    assert 'appsink.set_property("drop", True)' in _PIPELINE_SOURCE


def test_valve_gating_probe_is_attached_to_queue_src_pad() -> None:
    assert 'queue_src_pad = queue_el.get_static_pad("src")' in _PIPELINE_SOURCE
    assert "_on_snapshot_valve_probe" in _PIPELINE_SOURCE


def test_new_sample_callback_never_runs_on_the_metadata_probe_thread() -> None:
    """Structural proof (task requirement: "JPEG work never occurs in the pad-probe thread"): the
    JPEG-bytes-extraction logic lives in ``_on_snapshot_new_sample``, wired to appsink's
    ``new-sample`` signal (its own GStreamer streaming thread) — never called from
    ``_on_buffer_probe`` (the nvdsosd sink-pad probe callback)."""
    assert (
        'appsink.connect("new-sample", self._on_snapshot_new_sample, source_index)'
        in _PIPELINE_SOURCE
    )
    assert "_on_snapshot_new_sample" not in _PIPELINE_SOURCE.split("def _on_buffer_probe")[1].split(
        "def "
    )[0]


def test_new_sample_correlates_via_valve_probe_fifo_not_post_encode_metadata() -> None:
    """Regression check (real-hardware finding, T-158 remediation): nvjpegenc's output buffer does
    not carry ``NvDsBatchMeta`` forward from its input, so re-reading ``frame_number_from_buffer``
    on the *appsink*-side buffer silently returns ``None`` for every candidate — genuine detections,
    zero persisted JPEGs. The frame number must instead be handed off from the valve-gating probe
    (where the metadata is still attached) via a FIFO, consumed in ``_on_snapshot_new_sample``."""
    valve_probe_body = _PIPELINE_SOURCE.split("def _on_snapshot_valve_probe")[1].split("\n    def ")[
        0
    ]
    assert "self._pending_snapshot_frames.setdefault(source_index, deque()).append(" in valve_probe_body

    new_sample_body = _PIPELINE_SOURCE.split("def _on_snapshot_new_sample")[1].split("\n    def ")[0]
    # Source-qualified FIFO: the callback drains only *its own* camera's pending frames, so two
    # cameras encoding concurrently can never hand each other's JPEG to the acknowledgement handler.
    assert "self._pending_snapshot_frames.get(source_index)" in new_sample_body
    assert "pending.popleft() if pending else None" in new_sample_body
    # The comment above documents *why* the old call is gone; the only remaining occurrence of the
    # call form itself must be inside that explanatory comment, never a live statement.
    live_lines = [
        line
        for line in new_sample_body.splitlines()
        if "frame_number_from_buffer(self._pyds, gst_buffer)" in line
        and not line.strip().startswith("#")
    ]
    assert live_lines == []


def test_capture_failure_is_caught_and_never_propagates() -> None:
    new_sample_body = _PIPELINE_SOURCE.split("def _on_snapshot_new_sample")[1].split("\n    def ")[0]
    assert "except Exception" in new_sample_body
    assert "return Gst.FlowReturn.OK" in new_sample_body


def test_shutdown_clears_the_snapshot_cache() -> None:
    shutdown_body = _PIPELINE_SOURCE.split("def shutdown(self)")[1].split("\n    def ")[0]
    assert "self._snapshot_cache.clear()" in shutdown_body


def test_on_buffer_probe_forwards_on_candidate_to_handle_buffer() -> None:
    on_buffer_probe_body = _PIPELINE_SOURCE.split("def _on_buffer_probe")[1].split("\n    def ")[0]
    assert "record_detection" in on_buffer_probe_body
    assert "handle_buffer(self._pyds, info.get_buffer(), self._enqueue, on_candidate)" in (
        on_buffer_probe_body
    )


def test_no_nms_confidence_cooldown_sqlite_logic_added_by_snapshot_branch() -> None:
    for forbidden in ("nms", "cooldown", "sqlite", "insert into", "ingesthandler"):
        assert forbidden not in _PIPELINE_SOURCE.lower(), (
            f"pipeline.py must not reference '{forbidden}' — that logic belongs in the Agent, "
            "never the Bridge's pipeline construction"
        )


# --- Runtime check: constructor-level DI validation (reachable without real Gst/pyds) -------------


def test_constructor_requires_tracker_and_cache_when_snapshot_enabled() -> None:
    from deepstream_bridge.config import SnapshotConfig
    from deepstream_bridge.pipeline import BridgePipeline

    class _FakeConfig:
        snapshot = SnapshotConfig(
            enabled=True, frame_ttl_ms=1000, max_retained_frames=8, jpeg_quality=85
        )

    with pytest.raises(ValueError, match="candidate_tracker"):
        BridgePipeline(config=_FakeConfig(), enqueue=lambda _payload: None)
