"""Static regression checks for the T-94 late-join/stale-OSD fix's pipeline wiring.

Same rationale as test_pipeline_rtsp_out_idr.py: pipeline.py requires real gi/Gst/pyds at import
time, so these are source-text checks proving which properties are wired, cheap enough to run
everywhere (including this suite's Windows dev environment). The *functional* proof (SPS/PPS/IDR
NAL-level evidence, late-join client behaviour) is the Jetson isolated maintenance-window trial, not
here.
"""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")


def test_iframeinterval_is_applied_from_config() -> None:
    assert 'encoder.set_property("iframeinterval", cfg.iframe_interval)' in _PIPELINE_SOURCE


def test_idrinterval_is_applied_from_config() -> None:
    assert 'encoder.set_property("idrinterval", cfg.idr_interval)' in _PIPELINE_SOURCE


def test_insert_sps_pps_is_applied_from_config() -> None:
    assert 'encoder.set_property("insert-sps-pps", cfg.insert_sps_pps)' in _PIPELINE_SOURCE


def test_h264parse_config_interval_is_applied_from_config() -> None:
    assert 'h264parse.set_property("config-interval", cfg.h264parse_config_interval)' in (
        _PIPELINE_SOURCE
    )


def test_rtph264pay_config_interval_is_applied_from_config() -> None:
    assert 'rtppay.set_property("config-interval", cfg.rtph264pay_config_interval)' in (
        _PIPELINE_SOURCE
    )


def test_bitrate_property_is_unchanged() -> None:
    """T-94 must not touch bitrate (task boundary: "Do not change... bitrate, unless evidence
    proves it is directly involved" — no such evidence was found or claimed)."""
    assert 'encoder.set_property("bitrate", cfg.bitrate)' in _PIPELINE_SOURCE


def test_encoder_element_selection_is_unchanged() -> None:
    assert '"nvv4l2h264enc"' in _PIPELINE_SOURCE


def test_tracker_remains_conditional() -> None:
    """T-94 must not touch tracker construction (task boundary) — tracker stays purely conditional
    on the parsed config, same as T-93 established."""
    assert 'self._make("nvtracker", "tracker") if cfg.tracker is not None else None' in (
        _PIPELINE_SOURCE
    )


def test_metadata_probe_is_registered_exactly_once() -> None:
    assert _PIPELINE_SOURCE.count("add_probe(") == 1
    assert _PIPELINE_SOURCE.count("self._attach_probe(nvosd)") == 1


def test_no_nms_parser_confidence_cooldown_sqlite_logic_in_pipeline_module() -> None:
    """T-94 task boundary: do not change NMS, custom parser, confidence thresholds,
    DetectionIngestHandler, cooldown, or SQLite logic — none of that belongs in the Bridge's
    pipeline construction at all."""
    for forbidden in ("nms", "confidence", "cooldown", "sqlite", "insert into", "ingesthandler"):
        assert forbidden not in _PIPELINE_SOURCE.lower(), (
            f"pipeline.py must not reference '{forbidden}' — that logic belongs in "
            "infer-config.txt/the Agent, never the Bridge's pipeline construction"
        )


def test_element_chain_order_is_unchanged() -> None:
    """nvosd -> convertor-postosd -> encoder -> h264parse -> rtppay -> udpsink, unchanged by T-94."""
    assert "chain = (nvosd, nvvidconv2, encoder, h264parse, rtppay, udpsink)" in _PIPELINE_SOURCE


def test_gst_rtsp_server_shared_mode_is_unchanged() -> None:
    assert "factory.set_shared(True)" in _PIPELINE_SOURCE
