from __future__ import annotations

from pathlib import Path

import pytest

from deepstream_bridge.config import load_bridge_config
from deepstream_bridge.errors import BridgeConfigurationError


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _base_app_config(tmp_path: Path, infer_path: Path, extra: str = "") -> Path:
    content = f"""
[source0]
type=3
uri=file:///opt/weapon-detection/samples/deepstream/input.mp4
gpu-id=0

[streammux]
batch-size=1
batched-push-timeout=40000
width=1920
height=1080
live-source=0
enable-padding=0
nvbuf-memory-type=0
gpu-id=0

[primary-gie]
enable=1
gpu-id=0
gie-unique-id=1
config-file={infer_path}

[osd]
gpu-id=0
border-width=3
display-bbox=1
display-text=1
display-mask=0
{extra}
"""
    return _write(tmp_path / "deepstream-app.txt", content)


def test_parses_minimal_config(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.source.uri == "file:///opt/weapon-detection/samples/deepstream/input.mp4"
    assert config.streammux.width == 1920
    assert config.streammux.height == 1080
    assert config.infer.config_file == infer
    assert config.tracker is None
    # Bridge-specific defaults apply when [bridge-rtsp-out] is absent (module docstring).
    assert config.rtsp_out.enabled is True
    assert config.rtsp_out.port == 8554
    assert config.rtsp_out.mount_point == "/ds-test"
    assert config.rtsp_out.multicast_group == "224.224.255.255"
    # Source-resilience fields (IP-07 T-91 incident follow-up) fall back to nvurisrcbin's own
    # element defaults when [source0] doesn't set them.
    assert config.source.latency_ms == 100
    assert config.source.select_rtp_protocol == 0
    assert config.source.rtsp_reconnect_interval_sec == 0
    assert config.source.drop_frame_interval == 0
    assert config.source.num_extra_surfaces == 1
    assert config.source.cudadec_memtype == 2
    assert config.source.file_loop is False


def test_source_resilience_properties_are_parsed_from_production_style_config(
    tmp_path: Path,
) -> None:
    """Matches the real production [source0] section (IP-07 T-91 incident: these values are what
    the reference deepstream-app binary relies on to survive a transient RTSP disconnection, and
    what nvurisrcbin's own properties now carry through instead of being silently dropped)."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=4
uri=rtsp://100.77.146.5:8554/camera1
latency=1000
select-rtp-protocol=4
rtsp-reconnect-interval-sec=10
rtsp-reconnect-attempts=-1
drop-frame-interval=0
num-extra-surfaces=8
cudadec-memtype=0
gpu-id=0

[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    config = load_bridge_config(app_config)

    assert config.source.uri == "rtsp://100.77.146.5:8554/camera1"
    assert config.source.latency_ms == 1000
    assert config.source.select_rtp_protocol == 4
    assert config.source.rtsp_reconnect_interval_sec == 10
    assert config.source.drop_frame_interval == 0
    assert config.source.num_extra_surfaces == 8
    assert config.source.cudadec_memtype == 0


def test_select_rtp_protocol_other_values_fall_back_to_nvurisrcbin_default(tmp_path: Path) -> None:
    """Only the TCP-only value (4) has a direct nvurisrcbin equivalent — any other configured
    value maps to nvurisrcbin's own default (0, try everything) rather than guessing at a value it
    doesn't expose."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=4
uri=rtsp://example.invalid/camera1
select-rtp-protocol=1

[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    config = load_bridge_config(app_config)

    assert config.source.select_rtp_protocol == 0


def test_file_loop_is_read_from_tests_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=3
uri=file:///opt/weapon-detection/samples/deepstream/input.mp4

[streammux]
batch-size=1

[primary-gie]
config-file={infer}

[tests]
file-loop=1
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    config = load_bridge_config(app_config)

    assert config.source.file_loop is True


def test_file_loop_defaults_false_without_tests_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.source.file_loop is False


def test_tracker_enabled_when_config_enables_it(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = """
[tracker]
enable=1
tracker-width=640
tracker-height=384
ll-lib-file=/opt/nvidia/deepstream/deepstream-6.2/lib/libnvds_nvmultiobjecttracker.so
ll-config-file=/opt/nvidia/deepstream/deepstream-6.2/samples/configs/deepstream-app/config_tracker_NvDCF_perf.yml
gpu-id=0
enable-batch-process=1
display-tracking-id=1
"""
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.tracker is not None
    assert config.tracker.tracker_width == 640
    assert config.tracker.ll_lib_file.endswith("libnvds_nvmultiobjecttracker.so")


def test_tracker_absent_when_config_omits_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.tracker is None


def test_tracker_disabled_explicitly(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[tracker]\nenable=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.tracker is None


def test_bridge_rtsp_out_section_overrides_defaults(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = """
[bridge-rtsp-out]
enable=1
rtsp-port=8555
mount-point=/ds-test-alt
udp-port=5401
bitrate=2000000
codec=H264
"""
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.port == 8555
    assert config.rtsp_out.mount_point == "/ds-test-alt"
    assert config.rtsp_out.udp_port == 5401
    assert config.rtsp_out.bitrate == 2000000


def test_idr_interval_defaults_to_30_without_bridge_rtsp_out_section(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    app_config = _base_app_config(tmp_path, infer)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.idr_interval == 30


def test_idr_interval_defaults_to_30_with_bridge_rtsp_out_section_present(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nrtsp-port=8555\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.idr_interval == 30


def test_idr_interval_explicit_value_is_parsed(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=90\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.idr_interval == 90


def test_idr_interval_zero_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="idr-interval"):
        load_bridge_config(app_config)


def test_idr_interval_negative_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=-5\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="idr-interval"):
        load_bridge_config(app_config)


def test_idr_interval_non_integer_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=30.5\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="idr-interval"):
        load_bridge_config(app_config)


def test_idr_interval_non_numeric_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=fast\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="idr-interval"):
        load_bridge_config(app_config)


def test_idr_interval_beyond_upper_bound_is_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=1\nidr-interval=999999\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    with pytest.raises(BridgeConfigurationError, match="idr-interval"):
        load_bridge_config(app_config)


def test_idr_interval_does_not_affect_bitrate_or_other_rtsp_settings(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = """
[bridge-rtsp-out]
enable=1
rtsp-port=8555
mount-point=/ds-test-alt
udp-port=5401
bitrate=2000000
codec=H264
idr-interval=90
"""
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.idr_interval == 90
    assert config.rtsp_out.port == 8555
    assert config.rtsp_out.mount_point == "/ds-test-alt"
    assert config.rtsp_out.udp_port == 5401
    assert config.rtsp_out.bitrate == 2000000
    assert config.rtsp_out.codec == "H264"


def test_rtsp_out_can_be_disabled_for_smoke_tests(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    extra = "\n[bridge-rtsp-out]\nenable=0\n"
    app_config = _base_app_config(tmp_path, infer, extra=extra)

    config = load_bridge_config(app_config)

    assert config.rtsp_out.enabled is False


def test_unsupported_source_type_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=1
uri=file:///whatever

[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(app_config)


def test_missing_infer_config_file_rejected(tmp_path: Path) -> None:
    missing_infer = tmp_path / "does-not-exist.txt"
    content = f"""
[source0]
type=3
uri=file:///whatever

[streammux]
batch-size=1

[primary-gie]
config-file={missing_infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(app_config)


def test_missing_config_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(tmp_path / "nope.txt")


def test_missing_source_section_rejected(tmp_path: Path) -> None:
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    with pytest.raises(BridgeConfigurationError):
        load_bridge_config(app_config)


# --- IP-07 T-91 hardening, Part C: nvurisrcbin regression coverage ------------------------------


def test_rtsp_reconnect_attempts_is_accepted_but_has_no_effect(tmp_path: Path) -> None:
    """``rtsp-reconnect-attempts`` (deepstream-app.txt's companion to
    ``rtsp-reconnect-interval-sec``) has no ``nvurisrcbin`` equivalent — nvurisrcbin's own model is
    a single reconnect-on-timeout, retried indefinitely once the interval is non-zero. Parsing must
    not fail when the key is present (real production configs set it to -1), and no fabricated
    ``SourceConfig`` field exists for it — this test locks in that it is silently, safely ignored
    rather than causing a parse error or being mapped to something nvurisrcbin doesn't expose."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=4
uri=rtsp://example.invalid/camera1
rtsp-reconnect-interval-sec=10
rtsp-reconnect-attempts=-1

[streammux]
batch-size=1

[primary-gie]
config-file={infer}
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    config = load_bridge_config(app_config)

    assert config.source.rtsp_reconnect_interval_sec == 10
    assert not hasattr(config.source, "rtsp_reconnect_attempts")


def test_file_loop_parsing_is_independent_of_source_type(tmp_path: Path) -> None:
    """``[tests] file-loop=`` is read regardless of ``[source0] type=`` — safety for an RTSP
    source is delegated to ``nvurisrcbin`` itself (its own documented behaviour: "Src type must be
    source-type-uri and uri starting with 'file:/'"), not duplicated here. This proves config.py
    does not (incorrectly) suppress file_loop=True just because the configured source is RTSP."""
    infer = _write(tmp_path / "infer-config.txt", "[property]\n")
    content = f"""
[source0]
type=4
uri=rtsp://example.invalid/camera1

[streammux]
batch-size=1

[primary-gie]
config-file={infer}

[tests]
file-loop=1
"""
    app_config = _write(tmp_path / "deepstream-app.txt", content)

    config = load_bridge_config(app_config)

    assert config.source.file_loop is True
