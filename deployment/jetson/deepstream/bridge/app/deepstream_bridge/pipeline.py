"""GStreamer/DeepStream pipeline construction and lifecycle (IP-07 T-88, FS-05 §3/§4.2, task
items 3, 5, 9, 10, 11).

Reproduces the proven element chain (task item 3)::

    source (URI + NVIDIA decode via nvurisrcbin, incl. RTSP reconnect resilience) -> nvstreammux
    -> nvinfer (profile's unmodified infer-config.txt) -> [nvtracker, only if the config enables
    it] -> nvvideoconvert -> nvdsosd -> RTSP-out branch (encoder -> rtppay -> udpsink ->
    GstRtspServer) | fakesink

The metadata probe (task item 5) is attached on **nvdsosd's sink pad** — after nvinfer/nvtracker
have written ``NvDsObjectMeta`` and before OSD rendering, and still upstream of anything that could
drop the batch meta. ``gi``/``Gst``/``pyds``/``GstRtspServer`` are imported lazily, inside this
module only, via the ``import_*`` helpers below — no other Bridge module ever imports them, keeping
the pure logic in ``probe.py``/``transport.py``/``config.py``/``cli.py`` importable (and testable)
on a machine with none of these installed (task item 12).

Only this module (and ``main.py``, which only calls it) ever touches ``Gst``/``pyds`` — the process
boundary FS-05 §4.6 draws between the Bridge and the Agent has no equivalent *inside* the Bridge
itself, but keeping the glue isolated here is what makes the rest of the package unit-testable
without hardware (task item 12).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Sequence

from deepstream_bridge.config import BridgeConfig, RtspOutConfig, SourceConfig, TrackerConfig
from deepstream_bridge.errors import (
    MissingGstRtspServerBindingsError,
    MissingGStreamerBindingsError,
    MissingPydsError,
    PipelineBusError,
    PipelineElementCreationError,
    PipelineLinkError,
)
from deepstream_bridge.probe import handle_buffer

_LOGGER = logging.getLogger("deepstream_bridge.pipeline")


def import_gst() -> tuple[Any, Any]:
    """Import ``gi``/``Gst``/``GLib``, wrapping any failure in :class:`MissingGStreamerBindingsError`
    (task item 11: distinguish "missing GStreamer elements" from other failures)."""
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import GLib, Gst
    except (ImportError, ValueError) as exc:
        raise MissingGStreamerBindingsError(str(exc)) from exc
    return Gst, GLib


def import_pyds() -> Any:
    """Import ``pyds``, wrapping any failure in :class:`MissingPydsError` (task item 11)."""
    try:
        import pyds
    except ImportError as exc:
        raise MissingPydsError(str(exc)) from exc
    return pyds


def import_gst_rtsp_server() -> Any:
    """Import ``GstRtspServer``, wrapping any failure in
    :class:`MissingGstRtspServerBindingsError` (task item 11). Only called when the resolved
    config's RTSP-out branch is enabled — a fakesink-only Bridge run (Stage A/B smoke tests, task
    item 13) never needs this package at all."""
    try:
        import gi

        gi.require_version("GstRtspServer", "1.0")
        from gi.repository import GstRtspServer
    except (ImportError, ValueError) as exc:
        raise MissingGstRtspServerBindingsError(str(exc)) from exc
    return GstRtspServer


class BridgePipeline:
    """Builds and runs one DeepStream Bridge pipeline instance.

    ``enqueue`` is the only side effect the metadata probe performs — normally
    :meth:`~deepstream_bridge.transport.TransportWorker.enqueue`, injected so this class has no
    direct dependency on the transport module (mirrors the DI seam style used throughout this
    codebase, e.g. ``DeepStreamProcessManager``'s injectable subprocess factory).
    """

    def __init__(self, *, config: BridgeConfig, enqueue: Callable[[dict[str, Any]], None]) -> None:
        self._config = config
        self._enqueue = enqueue

        self._Gst, self._GLib = import_gst()
        self._pyds = import_pyds()
        self._Gst.init(None)

        self._pipeline: Any | None = None
        self._loop: Any | None = None
        self._rtsp_server: Any | None = None
        self._bus_error: Exception | None = None

    # --- Construction ----------------------------------------------------------------------------

    def build(self) -> None:
        """Construct and link every element (task item 3). Raises
        :class:`PipelineElementCreationError`/:class:`PipelineLinkError` on failure — never silently
        proceeds with a partially built pipeline."""
        Gst = self._Gst
        cfg = self._config

        pipeline = Gst.Pipeline.new("deepstream-bridge-pipeline")
        self._pipeline = pipeline

        source_element = self._create_source_element(cfg.source)
        streammux = self._make("nvstreammux", "stream-muxer")
        pgie = self._make("nvinfer", "primary-inference")
        tracker = self._make("nvtracker", "tracker") if cfg.tracker is not None else None
        nvvidconv = self._make("nvvideoconvert", "convertor")
        nvosd = self._make("nvdsosd", "onscreendisplay")

        for element in (source_element, streammux, pgie, tracker, nvvidconv, nvosd):
            if element is not None:
                pipeline.add(element)

        self._configure_streammux(streammux)
        self._configure_pgie(pgie)
        if tracker is not None:
            assert cfg.tracker is not None
            self._configure_tracker(tracker, cfg.tracker)
        self._configure_osd(nvosd)

        # nvurisrcbin's video src pad ("vsrc_%u") has "Sometimes" availability — it appears only
        # once the source negotiates, so linking happens in the pad-added callback, not here.
        source_element.connect("pad-added", self._on_source_pad_added, streammux)

        chain: Sequence[Any] = [
            streammux,
            pgie,
            *([tracker] if tracker is not None else []),
            nvvidconv,
            nvosd,
        ]
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineLinkError(
                    f"failed to link {upstream.get_name()} -> {downstream.get_name()}"
                )

        if cfg.rtsp_out.enabled:
            self._attach_rtsp_out(pipeline, nvosd, cfg.rtsp_out)
        else:
            self._attach_fakesink(pipeline, nvosd)

        self._attach_probe(nvosd)

    def _make(self, factory_name: str, element_name: str) -> Any:
        element = self._Gst.ElementFactory.make(factory_name, element_name)
        if element is None:
            raise PipelineElementCreationError(
                f"failed to create element '{element_name}' from factory '{factory_name}' "
                "(is the corresponding GStreamer/DeepStream plugin installed?)"
            )
        return element

    def _create_source_element(self, cfg: SourceConfig) -> Any:
        """DeepStream's own ``nvurisrcbin`` (task item 3: "RTSP source -> NVIDIA decoder"; IP-07
        T-91 incident follow-up — see :class:`~deepstream_bridge.config.SourceConfig`'s docstring
        for why this replaced a bare ``uridecodebin``). A single element that both selects the
        right internal source (``rtspsrc`` for ``rtsp://``, a file source for ``file://``) purely
        from the URI scheme *and* decodes internally, emitting ``video/x-raw(memory:NVMM)`` on its
        dynamic ``vsrc_%u`` pad — matching what ``deepstream-app`` itself uses internally, never a
        hardcoded model/decoder assumption.
        """
        element = self._make("nvurisrcbin", "uri-source-bin")
        element.set_property("uri", cfg.uri)
        element.set_property("gpu-id", cfg.gpu_id)
        element.set_property("latency", cfg.latency_ms)
        element.set_property("select-rtp-protocol", cfg.select_rtp_protocol)
        element.set_property("rtsp-reconnect-interval", cfg.rtsp_reconnect_interval_sec)
        element.set_property("drop-frame-interval", cfg.drop_frame_interval)
        element.set_property("num-extra-surfaces", cfg.num_extra_surfaces)
        element.set_property("cudadec-memtype", cfg.cudadec_memtype)
        element.set_property("file-loop", cfg.file_loop)
        return element

    def _on_source_pad_added(self, _element: Any, pad: Any, streammux: Any) -> None:
        """Link ``nvurisrcbin``'s dynamic video pad to ``nvstreammux`` once it appears. Never
        raises (this runs on a GStreamer streaming thread, not the caller of :meth:`build` — an
        exception here would not propagate as a normal Python error) — a link failure is logged
        instead, which surfaces as a downstream negotiation/bus error the run loop already handles.
        """
        caps = pad.get_current_caps() or pad.query_caps()
        structure = caps.get_structure(0)
        if not structure.get_name().startswith("video/"):
            return  # ignore nvurisrcbin's optional audio pad (asrc_%u) — video only

        sink_pad = streammux.get_request_pad("sink_0")
        if sink_pad is None:
            _LOGGER.error("bridge_streammux_request_pad_unavailable")
            return
        if pad.link(sink_pad) != self._Gst.PadLinkReturn.OK:
            _LOGGER.error("bridge_source_pad_link_failed")

    # --- Element property configuration (task item 4) ---------------------------------------------

    def _configure_streammux(self, streammux: Any) -> None:
        cfg = self._config.streammux
        streammux.set_property("batch-size", cfg.batch_size)
        streammux.set_property("batched-push-timeout", cfg.batched_push_timeout)
        streammux.set_property("width", cfg.width)
        streammux.set_property("height", cfg.height)
        streammux.set_property("live-source", cfg.live_source)
        streammux.set_property("enable-padding", cfg.enable_padding)
        streammux.set_property("nvbuf-memory-type", cfg.nvbuf_memory_type)
        streammux.set_property("gpu-id", cfg.gpu_id)

    def _configure_pgie(self, pgie: Any) -> None:
        cfg = self._config.infer
        # The ONLY nvinfer property the Bridge sets that names a file — everything model-specific
        # (dimensions, engine path, parser, class count) lives inside that file, never duplicated
        # here (module docstring / task item 4).
        pgie.set_property("config-file-path", str(cfg.config_file))
        pgie.set_property("gpu-id", cfg.gpu_id)
        pgie.set_property("unique-id", cfg.gie_unique_id)

    def _configure_tracker(self, tracker: Any, cfg: TrackerConfig) -> None:
        tracker.set_property("tracker-width", cfg.tracker_width)
        tracker.set_property("tracker-height", cfg.tracker_height)
        tracker.set_property("ll-lib-file", cfg.ll_lib_file)
        tracker.set_property("ll-config-file", cfg.ll_config_file)
        tracker.set_property("gpu-id", cfg.gpu_id)
        tracker.set_property("enable-batch-process", cfg.enable_batch_process)
        tracker.set_property("display-tracking-id", cfg.display_tracking_id)

    def _configure_osd(self, nvosd: Any) -> None:
        # Only real nvdsosd GObject properties (config.py's OsdConfig docstring) — confirmed via
        # gst-inspect-1.0 nvdsosd on the real Jetson; border-width/text-size/etc. are not settable
        # element properties at all.
        cfg = self._config.osd
        nvosd.set_property("gpu-id", cfg.gpu_id)
        nvosd.set_property("process-mode", cfg.process_mode)
        nvosd.set_property("display-bbox", cfg.display_bbox)
        nvosd.set_property("display-text", cfg.display_text)
        nvosd.set_property("display-mask", cfg.display_mask)

    # --- Output branch (task items 3, 9) -----------------------------------------------------------

    def _attach_fakesink(self, pipeline: Any, nvosd: Any) -> None:
        fakesink = self._make("fakesink", "fake-sink")
        fakesink.set_property("sync", False)
        pipeline.add(fakesink)
        if not nvosd.link(fakesink):
            raise PipelineLinkError("failed to link nvdsosd -> fakesink")

    def _attach_rtsp_out(self, pipeline: Any, nvosd: Any, cfg: RtspOutConfig) -> None:
        """Reconstruct DeepStream's own internal RTSP-out branch, matching NVIDIA's
        ``deepstream-test1-rtsp-out.py`` sample pattern (task item 9): H.264 hardware encode ->
        RTP payload -> multicast ``udpsink`` (the same ``224.224.255.255`` group
        ``fix-rtsp-multicast-route.sh`` already routes via loopback, untouched by this feature) ->
        an in-process ``GstRtspServer`` media factory that reads back from that UDP port on client
        connect. Requires ``GstRtspServer`` bindings (``gir1.2-gst-rtsp-server-1.0``) — raises
        :class:`MissingGstRtspServerBindingsError` if unavailable (confirmed absent on the real
        Jetson as of T-80; see ``deployment/jetson/deepstream/README.md``)."""
        GstRtspServer = import_gst_rtsp_server()

        nvvidconv2 = self._make("nvvideoconvert", "convertor-postosd")
        encoder = self._make("nvv4l2h264enc", "encoder")
        encoder.set_property("bitrate", cfg.bitrate)
        # nvv4l2h264enc's own idrinterval default (256 frames, ~8.5s@30fps) lets packet-loss-induced
        # P-frame corruption propagate via motion compensation for up to 8.5s before the next
        # full-recovery IDR (T-92 box-artifact investigation). Configurable via
        # [bridge-rtsp-out] idr-interval= (config.py), default 30 (~1s@30fps).
        encoder.set_property("idrinterval", cfg.idr_interval)
        # T-94 late-join/stale-OSD fix. iframeinterval already defaults to 30 on this device
        # (gst-inspect-1.0-confirmed) — set explicitly so it is config-driven, not an unstated
        # runtime default. insert-sps-pps makes the encoder itself physically emit SPS/PPS NAL units
        # into the elementary stream at every IDR, complementing (not duplicating) rtph264pay's own
        # cached-copy re-embedding below — a late-joining client has an in-band parameter set to
        # decode against even if payload-level caching hasn't yet observed one.
        encoder.set_property("iframeinterval", cfg.iframe_interval)
        encoder.set_property("insert-sps-pps", cfg.insert_sps_pps)
        h264parse = self._make("h264parse", "h264-parse")
        # T-94: was never set (silently defaulting to 0/disabled) — the parser itself now also
        # re-embeds SPS/PPS into the elementary stream before every IDR, matching rtph264pay below.
        h264parse.set_property("config-interval", cfg.h264parse_config_interval)
        rtppay = self._make("rtph264pay", "rtp-payloader")
        # Without this, SPS/PPS are sent once at pipeline start only — any client (including a late
        # RTSP SETUP) that joins afterward can never decode (T-91 acceptance finding). -1 re-embeds
        # them before every IDR, matching the encoder's own keyframe interval.
        rtppay.set_property("config-interval", cfg.rtph264pay_config_interval)
        udpsink = self._make("udpsink", "udp-sink")
        udpsink.set_property("host", cfg.multicast_group)
        udpsink.set_property("port", cfg.udp_port)
        udpsink.set_property("async", False)
        udpsink.set_property("sync", True)

        for element in (nvvidconv2, encoder, h264parse, rtppay, udpsink):
            pipeline.add(element)

        chain = (nvosd, nvvidconv2, encoder, h264parse, rtppay, udpsink)
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineLinkError(
                    f"failed to link {upstream.get_name()} -> {downstream.get_name()}"
                )

        server = GstRtspServer.RTSPServer()
        server.props.service = str(cfg.port)
        factory = GstRtspServer.RTSPMediaFactory()
        factory.set_launch(
            f"( udpsrc name=pay0 port={cfg.udp_port} buffer-size=524288 "
            f'caps="application/x-rtp, media=video, clock-rate=90000, '
            f'encoding-name={cfg.codec}, payload=96" )'
        )
        factory.set_shared(True)
        server.get_mount_points().add_factory(cfg.mount_point, factory)
        server.attach(None)
        self._rtsp_server = server
        _LOGGER.info(
            "bridge_rtsp_out_attached",
            extra={"port": cfg.port, "mount_point": cfg.mount_point},
        )

    # --- Metadata probe (task item 5) ---------------------------------------------------------------

    def _attach_probe(self, nvosd: Any) -> None:
        sink_pad = nvosd.get_static_pad("sink")
        if sink_pad is None:
            raise PipelineLinkError("nvdsosd has no sink pad to attach the metadata probe to")
        sink_pad.add_probe(self._Gst.PadProbeType.BUFFER, self._on_buffer_probe, None)

    def _on_buffer_probe(self, _pad: Any, info: Any, _user_data: Any) -> Any:
        # Minimal, non-blocking, returns immediately (task item 6) — all real work happens in
        # probe.handle_buffer, which is pure Python + injected pyds, never Gst.
        handle_buffer(self._pyds, info.get_buffer(), self._enqueue)
        return self._Gst.PadProbeReturn.OK

    # --- Run / shutdown (task item 10) ---------------------------------------------------------------

    def run(self, shutdown_signals: Sequence[int]) -> None:
        """Set PLAYING, run the GLib main loop until EOS/shutdown-signal/bus-error, then set NULL.

        Raises :class:`PipelineBusError` if the loop exited because of a fatal bus ``ERROR`` message
        (task item 10: "A GStreamer bus error must stop the Bridge and return a non-zero exit
        code"). A shutdown signal or clean EOS returns normally.
        """
        assert self._pipeline is not None
        Gst, GLib = self._Gst, self._GLib

        loop = GLib.MainLoop()
        self._loop = loop

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message, loop)

        for sig in shutdown_signals:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_shutdown_signal, loop)

        self._pipeline.set_state(Gst.State.PLAYING)
        try:
            loop.run()
        finally:
            self._pipeline.set_state(Gst.State.NULL)

        if self._bus_error is not None:
            error, self._bus_error = self._bus_error, None
            raise error

    def _on_bus_message(self, bus: Any, message: Any, loop: Any) -> bool:
        Gst = self._Gst
        if message.type == Gst.MessageType.EOS:
            _LOGGER.info("bridge_pipeline_eos")
            loop.quit()
        elif message.type == Gst.MessageType.ERROR:
            gerror, debug = message.parse_error()
            _LOGGER.error("bridge_pipeline_bus_error", extra={"error": str(gerror), "debug": debug})
            self._bus_error = PipelineBusError(str(gerror))
            loop.quit()
        return True

    def _on_shutdown_signal(self, loop: Any) -> bool:
        _LOGGER.info("bridge_shutdown_signal_received")
        loop.quit()
        return self._GLib.SOURCE_REMOVE

    def shutdown(self) -> None:
        """Release GStreamer/RTSP-server resources (task item 10). Idempotent and safe to call even
        if :meth:`build`/:meth:`run` never completed."""
        if self._pipeline is not None:
            self._pipeline.set_state(self._Gst.State.NULL)
            self._pipeline = None
        self._rtsp_server = None
        self._loop = None
