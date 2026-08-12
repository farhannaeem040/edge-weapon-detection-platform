"""Static regression checks for the tracker-removal evaluation (T-93).

Same rationale as ``test_pipeline_source_construction.py``/``test_pipeline_rtsp_out_idr.py``:
``pipeline.py`` requires real ``gi``/``Gst``/``pyds`` at import time, so these are source-text
checks proving which properties/control-flow are wired, cheap enough to run everywhere (including
this suite's Windows dev environment). The *functional* proof that tracker=0 measurably changes
FPS/GPU load/box behaviour is the Jetson isolated A/B trial, not here.

Project requirement (T-93 task brief): detect gun/knife, send raw detections to the Agent, apply
class/camera cooldown, store/report accepted events — persistent object identity, trajectory, and
unique-object counting are explicitly NOT required, so this evaluation treats the tracker as
optional infrastructure, never as a fix for the unrelated H.264/RTP corruption investigated
separately (T-92).
"""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")


def test_tracker_element_creation_is_conditional_on_config() -> None:
    """``nvtracker`` must only be created when ``cfg.tracker is not None`` — never unconditionally,
    and never created-then-discarded."""
    assert 'self._make("nvtracker", "tracker") if cfg.tracker is not None else None' in (
        _PIPELINE_SOURCE
    )


def test_tracker_configuration_only_runs_when_tracker_present() -> None:
    """``_configure_tracker`` (which sets ``display-tracking-id`` among other properties) must be
    gated behind the same ``tracker is not None`` check as element creation — it must never run
    against a ``None`` tracker."""
    assert "if tracker is not None:" in _PIPELINE_SOURCE
    assert "self._configure_tracker(tracker, cfg.tracker)" in _PIPELINE_SOURCE


def test_display_tracking_id_property_only_exists_inside_configure_tracker() -> None:
    """``display-tracking-id`` (the property that makes nvtracker write a tracking ID into
    ``text_params``) must only be set inside ``_configure_tracker`` — proving that when tracker is
    disabled, nothing else in the pipeline can produce a tracking-ID label."""
    occurrences = _PIPELINE_SOURCE.count('"display-tracking-id"')
    assert occurrences == 1, (
        f"expected exactly one 'display-tracking-id' reference (inside _configure_tracker), "
        f"found {occurrences}"
    )


def test_pipeline_chain_conditionally_includes_tracker() -> None:
    """The linked element chain must splice the tracker in only when present, never leaving a
    dangling/unlinked tracker element and never omitting it from the chain when it exists."""
    assert "*([tracker] if tracker is not None else [])" in _PIPELINE_SOURCE


def test_metadata_probe_is_registered_exactly_once() -> None:
    """``_attach_probe`` (the existing metadata probe on nvdsosd's sink pad) must be called exactly
    once from ``build()`` — regardless of tracker state, since the probe is attached downstream of
    both the tracker-present and tracker-absent chains. IP-10 added a second, distinct
    ``add_probe(`` call (the snapshot branch's valve-gating probe, ``_on_snapshot_valve_probe``,
    only reachable when snapshot capture is enabled) — this test now asserts the metadata probe's
    own attachment count specifically, not a whole-file ``add_probe(`` count, since the file
    legitimately contains two different probes for two different purposes."""
    assert _PIPELINE_SOURCE.count("self._attach_probe(nvosd)") == 1
    assert _PIPELINE_SOURCE.count("add_probe(") == 2
    assert "_on_snapshot_valve_probe" in _PIPELINE_SOURCE


def test_probe_is_attached_after_pgie_regardless_of_tracker() -> None:
    """The probe attaches to nvdsosd's sink pad, which every chain (tracker present or absent)
    links through after nvinfer — inference metadata reaches the probe/transport either way."""
    assert "def _attach_probe(self, nvosd: Any) -> None:" in _PIPELINE_SOURCE
    assert 'sink_pad = nvosd.get_static_pad("sink")' in _PIPELINE_SOURCE


def test_no_nms_or_confidence_logic_in_pipeline_module() -> None:
    """This module must never implement NMS, confidence filtering, or class-name hardcoding — those
    remain exclusively in infer-config.txt/the TensorRT engine (T-93 task brief: do not touch NMS,
    parser, confidence, cooldown, or SQLite logic)."""
    for forbidden in ("nms", "confidence", "cooldown", "sqlite", "INSERT INTO"):
        assert forbidden not in _PIPELINE_SOURCE.lower(), (
            f"pipeline.py must not reference '{forbidden}' — that logic belongs in "
            "infer-config.txt/the Agent, never the Bridge's pipeline construction"
        )
