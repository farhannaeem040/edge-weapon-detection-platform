"""Static regression checks for the RTSP-out branch's IDR-interval mitigation (T-92 box-artifact
investigation, extended by T-94's late-join/stale-OSD fix).

Same rationale as ``test_pipeline_source_construction.py``: ``pipeline.py`` requires real
``gi``/``Gst``/``pyds`` at import time, so these are source-text checks proving which properties are
wired, cheap enough to run everywhere (including this suite's Windows dev environment). The
*functional* proof that ``idrinterval`` measurably shortens visible corruption is the Jetson isolated
A/B trial, not here.

T-92 originally left ``rtph264pay``'s ``config-interval`` hardcoded to the literal ``-1`` and
deliberately did not enable ``insert-sps-pps`` (documented as "a separate concern to evaluate only if
later evidence shows an additional need" — T-94's investigation found that need: production was found
to be running an undeployed build missing even the ``idrinterval`` fix, and neither revision ever
covered ``h264parse``'s own ``config-interval`` or the encoder's ``insert-sps-pps``, together
producing the reported "non-existing PPS"/"no frame" late-join loop). T-94 makes ``rtph264pay``'s
``config-interval`` config-driven (same pattern as ``idr_interval``) and enables ``insert-sps-pps`` by
default — the two tests below that encoded the old, now-superseded scope decision are updated
accordingly; see ``test_pipeline_late_join_config.py`` for T-94's full property coverage.
"""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")


def test_encoder_idrinterval_is_set_from_config() -> None:
    assert '"idrinterval"' in _PIPELINE_SOURCE
    assert "cfg.idr_interval" in _PIPELINE_SOURCE


def test_rtppay_config_interval_is_config_driven() -> None:
    """T-91's SPS/PPS acceptance fix (repeat SPS/PPS with every IDR) must not be removed — T-94 makes
    the value config-driven (default still -1) rather than a hardcoded literal."""
    assert 'rtppay.set_property("config-interval", cfg.rtph264pay_config_interval)' in (
        _PIPELINE_SOURCE
    )


def test_insert_sps_pps_is_now_enabled_by_t94() -> None:
    """T-94 supersedes T-92's "deliberately out of scope" decision: production was found to be
    running an undeployed build missing even the idrinterval fix, so insert-sps-pps is now enabled
    (config-driven, default true) to make the encoder itself emit in-band SPS/PPS at every IDR."""
    assert 'encoder.set_property("insert-sps-pps", cfg.insert_sps_pps)' in _PIPELINE_SOURCE


def test_encoder_bitrate_property_is_unchanged() -> None:
    assert 'encoder.set_property("bitrate", cfg.bitrate)' in _PIPELINE_SOURCE
