"""Configuration parsing (IP-07 T-88, FS-05 §4.3/§4.6, task item 4).

The Bridge treats the deployed ``deepstream-app.txt``-equivalent config and the active profile's
``infer-config.txt`` as the single authoritative source for the pipeline it builds — it never
duplicates model settings (dimensions, class count, engine path, parser) in Python; those come from
``infer-config.txt`` verbatim via ``nvinfer``'s own ``config-file-path`` property. This module reads
only the *structural* properties DeepStream's own ``deepstream-app`` reference binary already reads
from the same file: element names/positions and their INI-section properties (``[source0]``,
``[streammux]``, ``[primary-gie]``, ``[tracker]``, ``[osd]``) — never a hardcoded model name.

**Bridge-specific configuration mapping (task item 4's documented escape hatch).** The committed
``deepstream-app.txt`` template ships a ``[sink0] type=1`` (fakesink) for the Agent-managed lifecycle
test (T-77); the operator's live RTSP deployment uses an internal, uncommitted ``[sink] type=4``
whose concrete bitrate/port/mount values are not captured in any repository file (see
``deployment/jetson/deepstream/README.md``, "One gap found and deferred to T-88"). Full direct
compatibility with that undocumented sink section is therefore impractical (task item 4 explicitly
permits this). Instead this module recognizes one new, additive, optional section,
``[bridge-rtsp-out]``, with documented defaults matching the values the current multicast-loopback
route fix (``fix-rtsp-multicast-route.sh``) and NVIDIA's own ``deepstream-test1-rtsp-out.py`` sample
already assume (multicast group ``224.224.255.255``, port ``8554``, mount ``/ds-test``, H.264,
4 Mbps) — this is the smallest deterministic mapping needed to keep the RTSP output compatible with
``rtsp://<jetson-tailscale-ip>:8554/ds-test`` (task item 9) without inventing new authority over
values ``infer-config.txt``/``deepstream-app.txt`` already own. If the section is absent, these
documented defaults apply; nothing about ``deepstream-app.txt`` or ``infer-config.txt`` is modified
to add it.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from deepstream_bridge.errors import BridgeConfigurationError

# Bridge-specific defaults for the RTSP-out branch (see module docstring). Matches the multicast
# group `fix-rtsp-multicast-route.sh` already routes via loopback, and the port/mount-point the task
# brief names explicitly (rtsp://<jetson-tailscale-ip>:8554/ds-test).
_DEFAULT_RTSP_MULTICAST_GROUP = "224.224.255.255"
_DEFAULT_RTSP_PORT = 8554
_DEFAULT_RTSP_UDP_PORT = 5400
_DEFAULT_RTSP_MOUNT_POINT = "/ds-test"
_DEFAULT_RTSP_BITRATE = 4_000_000
_DEFAULT_RTSP_CODEC = "H264"
# nvv4l2h264enc's own idrinterval default (256 frames, ~8.5s@30fps) lets packet-loss-induced P-frame
# corruption propagate via motion compensation for up to 8.5s before the next full-recovery IDR
# (T-92 box-artifact investigation). 30 frames = ~1s@30fps — short enough that any corruption event
# self-heals almost immediately, at the cost of a modest bitrate/encoder-overhead increase from more
# frequent full (IDR) frames. Frame-rate is fixed at 30fps in this deployment (streammux/profile);
# there is no frame-rate-derived mechanism elsewhere in this config schema to compute this from, so
# the default is a literal frame count, matching how every other RTSP-out property here is expressed.
_DEFAULT_RTSP_IDR_INTERVAL = 30
# Generous but not unbounded — beyond this, a single corrupted GOP could visibly persist for minutes.
_MAX_RTSP_IDR_INTERVAL = 3600

# nvv4l2h264enc's own `iframeinterval` (T-94 late-join/stale-OSD fix) already defaults to 30 on this
# device (confirmed via `gst-inspect-1.0 nvv4l2h264enc` on the real Jetson, DS 6.2/JetPack 5.1.2) —
# this default is expressed explicitly here (rather than left unset) so it is config-driven and
# documented alongside `idr-interval`, not because the runtime default was wrong. Same bound/shape as
# idr-interval: both are frame counts on the same encoder.
_DEFAULT_RTSP_IFRAME_INTERVAL = 30
_MAX_RTSP_IFRAME_INTERVAL = 3600

# `insert-sps-pps` (T-94): confirmed via `gst-inspect-1.0 nvv4l2h264enc` on the real Jetson —
# Boolean, encoder default `false`. With idr-interval reduced to ~1s, `rtph264pay`'s own
# `config-interval=-1` (below) already re-embeds its *cached* SPS/PPS before every IDR; enabling
# `insert-sps-pps` additionally makes the encoder itself physically emit SPS/PPS NAL units into the
# elementary stream at every IDR, so a late-joining client (or an `h264parse` downstream of a
# rebuilt reference chain) has an in-band parameter set to decode against rather than relying solely
# on payload-level caching — the two are complementary, not redundant, per NVIDIA's own RTSP-out
# sample pattern.
_DEFAULT_INSERT_SPS_PPS = True

# `h264parse`/`rtph264pay` `config-interval` (T-94): confirmed via `gst-inspect-1.0 h264parse` and
# `gst-inspect-1.0 rtph264pay` on the real Jetson — both elements expose the identical property
# (Integer, range -1..3600, default 0/disabled; -1 = "send with every IDR frame"). `rtph264pay`'s own
# config-interval was already `-1` (T-91's late-joiner fix); `h264parse`'s was never set at all
# (silently defaulting to `0`, disabled) — the actual, previously undeployed gap this fix closes.
_DEFAULT_H264PARSE_CONFIG_INTERVAL = -1
_DEFAULT_RTPH264PAY_CONFIG_INTERVAL = -1
_MIN_CONFIG_INTERVAL = -1
_MAX_CONFIG_INTERVAL = 3600

# deepstream-app's own [source0] `type=` enum picks between different *reference-binary* source
# elements (1=V4L2 camera, 2/3=URI via decodebin, 4=RTSP via an internal rtspsrc-based path) — but
# the Bridge always builds its source with `nvurisrcbin` (module docstring below), which already
# auto-selects the right internal element (rtspsrc for an rtsp:// URI, filesrc/etc. for file://)
# purely from the URI scheme. So every URI-bearing type is equally supported here; only type=1
# (camera, no `uri=` key at all) has no Bridge equivalent. Confirmed against the live-deployed
# config (IP-07 T-88 Jetson validation, 2026-07-26): the operator's real Phase-2 RTSP deployment
# uses type=4 with `uri=rtsp://...`, not the committed template's type=3 file:// URI — both must
# work unmodified.
_SUPPORTED_SOURCE_TYPES = frozenset({"2", "3", "4"})

# nvurisrcbin's own `select-rtp-protocol` enum (confirmed via gst-inspect-1.0 on the real Jetson,
# IP-07 T-91 incident follow-up) has exactly two values: 0 = "rtp-multi" (UDP + UDP Multicast + TCP,
# the element's own default) and 4 = "rtp-tcp" (TCP only). deepstream-app.txt's own
# `select-rtp-protocol=` value happens to share the same numeric encoding for the TCP-only case
# (4 = "RTSP/RTP over TCP only", per the production config's own comment) — any other configured
# value (0/1/2/7/unset) maps to nvurisrcbin's default (0), a safe superset rather than a guess at a
# value nvurisrcbin doesn't expose.
_RTP_PROTOCOL_TCP_ONLY = 4


@dataclass(frozen=True)
class SourceConfig:
    """``[source0]`` — the RTSP/file URI DeepStream decodes, plus the RTSP resilience properties
    the reference ``deepstream-app`` binary relies on to survive a transient source disconnection
    (task item 3, "current RTSP input behaviour"; IP-07 T-91 incident follow-up).

    **Why ``nvurisrcbin``, not plain GStreamer ``uridecodebin`` (binding decision, corrects the
    original T-88 implementation).** A live T-91 cutover found that a Bridge built on bare
    ``uridecodebin`` has no equivalent of ``deepstream-app.txt``'s own
    ``rtsp-reconnect-interval-sec``/``rtsp-reconnect-attempts`` — those are not standard
    ``rtspsrc`` GObject properties at all; ``deepstream-app`` implements its own reconnect handling
    around them. A single transient upstream hiccup that the reference binary silently reconnected
    from instead reached a clean EOS on ``uridecodebin`` and terminated the whole Bridge process.
    DeepStream's own ``nvurisrcbin`` element (confirmed via ``gst-inspect-1.0 nvurisrcbin`` on the
    real Jetson) exposes ``rtsp-reconnect-interval``, ``select-rtp-protocol``, ``latency``,
    ``drop-frame-interval``, ``num-extra-surfaces``, ``cudadec-memtype``, and ``file-loop`` as
    native properties matching ``deepstream-app.txt``'s own key names almost exactly, and already
    performs internal decode (its dynamic ``vsrc_%u`` pad emits ``video/x-raw(memory:NVMM)``
    directly) — so it replaces both ``uridecodebin`` and the separate NVIDIA decoder step in one
    element, matching what ``deepstream-app`` itself uses internally.
    """

    uri: str
    gpu_id: int
    latency_ms: int
    select_rtp_protocol: int
    rtsp_reconnect_interval_sec: int
    drop_frame_interval: int
    num_extra_surfaces: int
    cudadec_memtype: int
    file_loop: bool


@dataclass(frozen=True)
class StreammuxConfig:
    """``[streammux]`` — batching/dimension properties, unchanged from the proven config."""

    batch_size: int
    batched_push_timeout: int
    width: int
    height: int
    live_source: bool
    enable_padding: bool
    nvbuf_memory_type: int
    gpu_id: int


@dataclass(frozen=True)
class InferConfig:
    """``[primary-gie]`` — points ``nvinfer`` at the profile's unmodified ``infer-config.txt``; the
    model's own dimensions/engine/parser/class-count live only in that file, never duplicated here."""

    config_file: Path
    interval: int
    gpu_id: int
    gie_unique_id: int


@dataclass(frozen=True)
class TrackerConfig:
    """``[tracker]``. Only constructed when ``enable=1`` in the source config — task item 3: "tracker
    disabled unless the existing config explicitly enables it." The committed template's own
    ``[tracker]`` section currently sets ``enable=1``, so a Bridge reproducing that exact config
    enables the tracker too; this is preservation of the existing config's own choice, not a new
    Bridge decision, and no object-tracking *logic* is added anywhere in the Bridge itself (the
    element is NVIDIA's own ``nvtracker``, driven entirely by the profile-agnostic config values
    below)."""

    tracker_width: int
    tracker_height: int
    ll_lib_file: str
    ll_config_file: str
    gpu_id: int
    enable_batch_process: bool
    display_tracking_id: bool


@dataclass(frozen=True)
class OsdConfig:
    """``[osd]`` -> the subset of keys that map to real ``nvdsosd`` GObject properties.

    Confirmed via ``gst-inspect-1.0 nvdsosd`` on the real Jetson (IP-07 T-88 isolated smoke test,
    2026-07-26): ``border-width``/``text-size``/``text-color``/``font``/``show-clock`` etc. are
    **not** ``nvdsosd`` element properties at all — ``deepstream-app``'s own C code applies most of
    them to per-object ``rect_params``/``text_params`` metadata at render time, outside anything a
    generic GStreamer property can set. Only ``gpu-id``, ``process-mode``, ``display-bbox``,
    ``display-text``, and ``display-mask`` are real, settable properties; those are all this Bridge
    reproduces. The bounding boxes themselves are already drawn from ``NvDsObjectMeta``'s own
    ``rect_params`` written by ``nvinfer``/``nvtracker`` upstream — this section only controls
    whether ``nvdsosd`` renders them at all, not their exact cosmetic styling.
    """

    gpu_id: int
    process_mode: int
    display_bbox: bool
    display_text: bool
    display_mask: bool


@dataclass(frozen=True)
class RtspOutConfig:
    """The Bridge-specific ``[bridge-rtsp-out]`` mapping (module docstring). ``enabled=False``
    (only reachable if the section explicitly sets ``enable=0``) routes to a fakesink instead —
    used by the isolated Stage A/B smoke tests (task item 13) so RTSP output never has to be proven
    working before metadata/transport are.

    ``idr_interval`` (T-92 box-artifact investigation) is measured in **frames**, applied directly
    to ``nvv4l2h264enc``'s own ``idrinterval`` property: at this deployment's fixed 30 FPS, a value
    of 30 means approximately one IDR (full decoder-recovery frame) per second. A shorter interval
    lets the decoder recover from RTP packet loss faster — a lost/corrupted P-frame can otherwise
    propagate visible corruption via motion compensation until the next IDR — at the cost of a
    modest bitrate/encoder-overhead increase from encoding more full frames.

    ``iframe_interval``/``insert_sps_pps``/``h264parse_config_interval``/
    ``rtph264pay_config_interval`` (T-94 late-join/stale-OSD fix) close the gap T-92 left open: T-92
    only set ``idrinterval`` and ``rtph264pay``'s own ``config-interval``, but the deployed Bridge
    was found (T-94 investigation) to have never actually shipped even that fix, and neither revision
    ever enabled the encoder's own ``insert-sps-pps`` or ``h264parse``'s ``config-interval`` — so a
    late-joining client could wait up to one full (undeployed default 256-frame, ~8.5s) IDR interval
    with no in-band SPS/PPS to decode against, producing a sustained "non-existing PPS"/"no frame"
    loop. All four properties are confirmed to exist with these exact names/types/ranges on the real
    Jetson (``gst-inspect-1.0 nvv4l2h264enc``/``h264parse``/``rtph264pay``, DS 6.2/JetPack 5.1.2).
    """

    enabled: bool
    multicast_group: str
    port: int
    udp_port: int
    mount_point: str
    bitrate: int
    codec: str
    idr_interval: int
    iframe_interval: int
    insert_sps_pps: bool
    h264parse_config_interval: int
    rtph264pay_config_interval: int


@dataclass(frozen=True)
class BridgeConfig:
    """The fully resolved, Bridge-specific view of one DeepStream application config."""

    source: SourceConfig
    streammux: StreammuxConfig
    infer: InferConfig
    tracker: TrackerConfig | None
    osd: OsdConfig
    rtsp_out: RtspOutConfig


def load_bridge_config(config_path: Path) -> BridgeConfig:
    """Parse ``config_path`` (a ``deepstream-app.txt``-shaped INI file) into a :class:`BridgeConfig`.

    Raises :class:`BridgeConfigurationError` for any missing required section/key, an unsupported
    source type, or a ``[primary-gie] config-file`` that does not resolve to an existing file —
    fails fast and clearly rather than building a partially-configured pipeline.
    """
    parser = configparser.ConfigParser()
    try:
        read_files = parser.read(config_path, encoding="utf-8")
    except configparser.Error as exc:
        raise BridgeConfigurationError(f"failed to parse config file {config_path}: {exc}") from exc
    if not read_files:
        raise BridgeConfigurationError(f"config file not found or unreadable: {config_path}")

    source = _parse_source(parser, config_path)
    streammux = _parse_streammux(parser)
    infer = _parse_infer(parser, config_path)
    tracker = _parse_tracker(parser)
    osd = _parse_osd(parser)
    rtsp_out = _parse_rtsp_out(parser)

    return BridgeConfig(
        source=source,
        streammux=streammux,
        infer=infer,
        tracker=tracker,
        osd=osd,
        rtsp_out=rtsp_out,
    )


def _require_section(parser: configparser.ConfigParser, section: str) -> configparser.SectionProxy:
    if not parser.has_section(section):
        raise BridgeConfigurationError(f"missing required section [{section}]")
    return parser[section]


def _require_key(section: configparser.SectionProxy, key: str, section_name: str) -> str:
    if key not in section:
        raise BridgeConfigurationError(f"missing required key '{key}' in [{section_name}]")
    return section[key]


def _parse_source(parser: configparser.ConfigParser, config_path: Path) -> SourceConfig:
    section = _require_section(parser, "source0")
    source_type = section.get("type", "3")
    if source_type not in _SUPPORTED_SOURCE_TYPES:
        raise BridgeConfigurationError(
            f"[source0] type={source_type} is not supported; the Bridge reproduces URI-based "
            f"sources only (type in {sorted(_SUPPORTED_SOURCE_TYPES)}), driven entirely by the "
            "uri= key via nvurisrcbin"
        )
    uri = _require_key(section, "uri", "source0")

    raw_protocol = section.getint("select-rtp-protocol", fallback=0)
    select_rtp_protocol = _RTP_PROTOCOL_TCP_ONLY if raw_protocol == _RTP_PROTOCOL_TCP_ONLY else 0

    return SourceConfig(
        uri=uri,
        gpu_id=section.getint("gpu-id", fallback=0),
        latency_ms=section.getint("latency", fallback=100),
        select_rtp_protocol=select_rtp_protocol,
        # deepstream-app.txt's own key carries a "-sec" suffix; nvurisrcbin's property does not —
        # the unit (seconds) is identical, only the name differs. "rtsp-reconnect-attempts" (the
        # config's companion key) has no nvurisrcbin equivalent — deliberately not read: the
        # element's own model is a single reconnect-on-timeout, retried indefinitely once the
        # interval is non-zero, which already matches the production config's own
        # rtsp-reconnect-attempts=-1 ("infinite") intent without needing a separate cap.
        rtsp_reconnect_interval_sec=section.getint("rtsp-reconnect-interval-sec", fallback=0),
        drop_frame_interval=section.getint("drop-frame-interval", fallback=0),
        num_extra_surfaces=section.getint("num-extra-surfaces", fallback=1),
        cudadec_memtype=section.getint("cudadec-memtype", fallback=2),
        file_loop=_parse_file_loop(parser),
    )


def _parse_file_loop(parser: configparser.ConfigParser) -> bool:
    """``[tests] file-loop=`` (task item 3: preserve the Agent-managed lifecycle test's local-file
    looping behaviour) — ``nvurisrcbin``'s own ``file-loop`` property implements exactly this,
    replacing the ad hoc "no loop support" gap a bare ``uridecodebin`` source had."""
    if not parser.has_section("tests"):
        return False
    return parser["tests"].getboolean("file-loop", fallback=False)


def _parse_streammux(parser: configparser.ConfigParser) -> StreammuxConfig:
    section = _require_section(parser, "streammux")
    return StreammuxConfig(
        batch_size=section.getint("batch-size", fallback=1),
        batched_push_timeout=section.getint("batched-push-timeout", fallback=40000),
        width=section.getint("width", fallback=1920),
        height=section.getint("height", fallback=1080),
        live_source=section.getboolean("live-source", fallback=False),
        enable_padding=section.getboolean("enable-padding", fallback=False),
        nvbuf_memory_type=section.getint("nvbuf-memory-type", fallback=0),
        gpu_id=section.getint("gpu-id", fallback=0),
    )


def _parse_infer(parser: configparser.ConfigParser, config_path: Path) -> InferConfig:
    section = _require_section(parser, "primary-gie")
    config_file_value = _require_key(section, "config-file", "primary-gie")
    config_file = Path(config_file_value)
    if not config_file.is_file():
        raise BridgeConfigurationError(
            f"[primary-gie] config-file does not resolve to an existing file: {config_file}"
        )
    return InferConfig(
        config_file=config_file,
        interval=section.getint("interval", fallback=0),
        gpu_id=section.getint("gpu-id", fallback=0),
        gie_unique_id=section.getint("gie-unique-id", fallback=1),
    )


def _parse_tracker(parser: configparser.ConfigParser) -> TrackerConfig | None:
    if not parser.has_section("tracker"):
        return None
    section = parser["tracker"]
    if not section.getboolean("enable", fallback=False):
        return None

    ll_lib_file = _require_key(section, "ll-lib-file", "tracker")
    ll_config_file = _require_key(section, "ll-config-file", "tracker")
    return TrackerConfig(
        tracker_width=section.getint("tracker-width", fallback=640),
        tracker_height=section.getint("tracker-height", fallback=384),
        ll_lib_file=ll_lib_file,
        ll_config_file=ll_config_file,
        gpu_id=section.getint("gpu-id", fallback=0),
        enable_batch_process=section.getboolean("enable-batch-process", fallback=True),
        display_tracking_id=section.getboolean("display-tracking-id", fallback=True),
    )


def _parse_osd(parser: configparser.ConfigParser) -> OsdConfig:
    if not parser.has_section("osd"):
        return OsdConfig(
            gpu_id=0, process_mode=0, display_bbox=True, display_text=True, display_mask=False
        )
    section = parser["osd"]
    return OsdConfig(
        gpu_id=section.getint("gpu-id", fallback=0),
        process_mode=section.getint("process-mode", fallback=0),
        display_bbox=section.getboolean("display-bbox", fallback=True),
        display_text=section.getboolean("display-text", fallback=True),
        display_mask=section.getboolean("display-mask", fallback=False),
    )


def _parse_positive_frame_interval(
    section: configparser.SectionProxy, key: str, default: int, max_value: int
) -> int:
    """Validate a positive-integer frame-count property (``idr-interval``/``iframe-interval``, T-92/
    T-94): reject zero, negative, non-integer, and values above ``max_value``. Never silently
    clamps — an out-of-range or malformed value is a configuration error, not a value to coerce."""
    raw = section.get(key, fallback=None)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BridgeConfigurationError(
            f"[bridge-rtsp-out] {key} must be an integer, got: {raw!r}"
        ) from None
    if value <= 0:
        raise BridgeConfigurationError(
            f"[bridge-rtsp-out] {key} must be a positive integer (frames), got: {value}"
        )
    if value > max_value:
        raise BridgeConfigurationError(
            f"[bridge-rtsp-out] {key} must not exceed {max_value} frames, got: {value}"
        )
    return value


def _parse_idr_interval(section: configparser.SectionProxy) -> int:
    """Validate ``idr-interval`` (T-92): see :func:`_parse_positive_frame_interval`."""
    return _parse_positive_frame_interval(
        section, "idr-interval", _DEFAULT_RTSP_IDR_INTERVAL, _MAX_RTSP_IDR_INTERVAL
    )


def _parse_iframe_interval(section: configparser.SectionProxy) -> int:
    """Validate ``iframe-interval`` (T-94): see :func:`_parse_positive_frame_interval`."""
    return _parse_positive_frame_interval(
        section, "iframe-interval", _DEFAULT_RTSP_IFRAME_INTERVAL, _MAX_RTSP_IFRAME_INTERVAL
    )


def _parse_config_interval(section: configparser.SectionProxy, key: str, default: int) -> int:
    """Validate an ``h264parse``/``rtph264pay``-shaped ``config-interval`` value (T-94): must be an
    integer within the range the real GStreamer elements actually support
    (``gst-inspect-1.0``-confirmed: -1 to 3600 inclusive; -1 = "send with every IDR frame", 0 =
    disabled). Never silently clamps."""
    raw = section.get(key, fallback=None)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BridgeConfigurationError(
            f"[bridge-rtsp-out] {key} must be an integer, got: {raw!r}"
        ) from None
    if not (_MIN_CONFIG_INTERVAL <= value <= _MAX_CONFIG_INTERVAL):
        raise BridgeConfigurationError(
            f"[bridge-rtsp-out] {key} must be between {_MIN_CONFIG_INTERVAL} and "
            f"{_MAX_CONFIG_INTERVAL} (the range the GStreamer element itself supports), got: {value}"
        )
    return value


def _parse_rtsp_out(parser: configparser.ConfigParser) -> RtspOutConfig:
    if not parser.has_section("bridge-rtsp-out"):
        return RtspOutConfig(
            enabled=True,
            multicast_group=_DEFAULT_RTSP_MULTICAST_GROUP,
            port=_DEFAULT_RTSP_PORT,
            udp_port=_DEFAULT_RTSP_UDP_PORT,
            mount_point=_DEFAULT_RTSP_MOUNT_POINT,
            bitrate=_DEFAULT_RTSP_BITRATE,
            codec=_DEFAULT_RTSP_CODEC,
            idr_interval=_DEFAULT_RTSP_IDR_INTERVAL,
            iframe_interval=_DEFAULT_RTSP_IFRAME_INTERVAL,
            insert_sps_pps=_DEFAULT_INSERT_SPS_PPS,
            h264parse_config_interval=_DEFAULT_H264PARSE_CONFIG_INTERVAL,
            rtph264pay_config_interval=_DEFAULT_RTPH264PAY_CONFIG_INTERVAL,
        )
    section = parser["bridge-rtsp-out"]
    return RtspOutConfig(
        enabled=section.getboolean("enable", fallback=True),
        multicast_group=section.get("multicast-group", fallback=_DEFAULT_RTSP_MULTICAST_GROUP),
        port=section.getint("rtsp-port", fallback=_DEFAULT_RTSP_PORT),
        udp_port=section.getint("udp-port", fallback=_DEFAULT_RTSP_UDP_PORT),
        mount_point=section.get("mount-point", fallback=_DEFAULT_RTSP_MOUNT_POINT),
        bitrate=section.getint("bitrate", fallback=_DEFAULT_RTSP_BITRATE),
        codec=section.get("codec", fallback=_DEFAULT_RTSP_CODEC),
        idr_interval=_parse_idr_interval(section),
        iframe_interval=_parse_iframe_interval(section),
        insert_sps_pps=section.getboolean("insert-sps-pps", fallback=_DEFAULT_INSERT_SPS_PPS),
        h264parse_config_interval=_parse_config_interval(
            section, "h264parse-config-interval", _DEFAULT_H264PARSE_CONFIG_INTERVAL
        ),
        rtph264pay_config_interval=_parse_config_interval(
            section, "rtph264pay-config-interval", _DEFAULT_RTPH264PAY_CONFIG_INTERVAL
        ),
    )
