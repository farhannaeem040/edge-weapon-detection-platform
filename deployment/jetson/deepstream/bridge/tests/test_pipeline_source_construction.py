"""Static regression checks proving the Bridge's source element (IP-07 T-91 hardening, Part C).

``pipeline.py`` requires real ``gi``/``Gst``/``pyds`` at import time (``BridgePipeline.__init__``
calls ``import_gst()``/``import_pyds()`` immediately) — by this package's own established design,
that module is deliberately excluded from offline property-wiring tests and verified for real only
on the Jetson (see the module's own docstring: "keeping the pure logic ... importable ... on a
machine with none of these installed"). These are static, source-text checks instead: they prove
*which* GStreamer element/methods the source code names, without needing to run it — a real,
meaningful regression guard for "the old uridecodebin/ghost-pad path is gone" and "nvurisrcbin is
used", cheap enough to run everywhere including this suite's Windows dev environment.

The *functional* proof that the properties this module sets are honoured by the real element
(reconnect interval, latency, protocol, etc.) is the Jetson isolated smoke test (task item 3), not
here — a stub Gst would only prove this file calls ``set_property``, never that ``nvurisrcbin``
itself behaves as documented.
"""

from __future__ import annotations

from pathlib import Path

_PIPELINE_SOURCE = (
    Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge" / "pipeline.py"
).read_text(encoding="utf-8")


def test_nvurisrcbin_is_the_source_element_factory() -> None:
    assert '"nvurisrcbin"' in _PIPELINE_SOURCE


def test_no_uridecodebin_factory_remains() -> None:
    assert '"uridecodebin"' not in _PIPELINE_SOURCE


def test_no_old_source_bin_ghost_pad_methods_remain() -> None:
    for dead_symbol in (
        "_create_source_bin",
        "_on_decode_bin_pad_added",
        "_on_decode_bin_child_added",
        "GhostPad",
    ):
        assert dead_symbol not in _PIPELINE_SOURCE, (
            f"dead uridecodebin-era symbol still present: {dead_symbol}"
        )


def test_source_element_wires_every_resilience_property() -> None:
    """Every field IP-07 T-91's incident follow-up added to ``SourceConfig`` must actually be
    passed to ``nvurisrcbin`` via ``set_property`` — this is the "wired, not just parsed" check;
    ``test_config.py`` already proves the parsing half."""
    for property_name in (
        '"uri"',
        '"gpu-id"',
        '"latency"',
        '"select-rtp-protocol"',
        '"rtsp-reconnect-interval"',
        '"drop-frame-interval"',
        '"num-extra-surfaces"',
        '"cudadec-memtype"',
        '"file-loop"',
    ):
        assert property_name in _PIPELINE_SOURCE, f"nvurisrcbin property never set: {property_name}"


def test_source_pad_linking_uses_dynamic_pad_added_not_static_pad() -> None:
    """nvurisrcbin's video pad ('vsrc_%u') has "Sometimes" availability (confirmed via
    gst-inspect-1.0 on the real Jetson) — the source code must link it via a pad-added callback,
    never assume a static pad is available immediately after construction."""
    assert '"pad-added"' in _PIPELINE_SOURCE
    assert "_on_source_pad_added" in _PIPELINE_SOURCE
