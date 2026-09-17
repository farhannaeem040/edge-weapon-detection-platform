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
import threading
from collections import deque
from typing import Any, Callable, Optional, Sequence

from deepstream_bridge.config import (
    BridgeConfig,
    RtspOutConfig,
    SnapshotConfig,
    SourceConfig,
    TrackerConfig,
)
from deepstream_bridge.errors import (
    MissingGstRtspServerBindingsError,
    MissingGStreamerBindingsError,
    MissingPydsError,
    PipelineBusError,
    PipelineElementCreationError,
    PipelineLinkError,
)
from deepstream_bridge.probe import apply_display_text, frame_number_from_buffer, handle_buffer
from deepstream_bridge.snapshot import SendSnapshot, SnapshotRendezvous

_LOGGER = logging.getLogger("deepstream_bridge.pipeline")

# GstQueue's "leaky" enum (confirmed via gst-inspect-1.0 queue): 0=no, 1=upstream, 2=downstream.
# FS-08 §3 requires downstream-leaky (drop the newest/incoming buffer on overflow, never block the
# OSD-rendering thread waiting for the snapshot branch to keep up).
_QUEUE_LEAK_DOWNSTREAM = 2

# Small and bounded (FS-08 §3: "queue leaky=downstream, small bounded max-size-buffers") — this
# branch only ever needs to hold the handful of in-flight candidate frames between the valve and the
# hardware JPEG encoder; it is not a general backlog buffer (that's the queue's whole point here).
_SNAPSHOT_QUEUE_MAX_SIZE_BUFFERS = 4

# The pseudo source index used by the legacy single-shared-output topology, where one nvdsosd serves
# every source and there is no demultiplexing to attribute a buffer to a real camera. Never collides
# with a real source_index, which is always >= 0.
_SHARED_OUTPUT_SOURCE_INDEX = -1


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

    def __init__(
        self,
        *,
        config: BridgeConfig,
        enqueue: Callable[[dict[str, Any]], None],
        snapshot_rendezvous: Optional[SnapshotRendezvous] = None,
        send_snapshot: Optional[SendSnapshot] = None,
    ) -> None:
        if config.snapshot.enabled and (snapshot_rendezvous is None or send_snapshot is None):
            raise ValueError(
                "snapshot_rendezvous and send_snapshot are required when config.snapshot.enabled"
            )

        self._config = config
        self._enqueue = enqueue
        self._snapshot_rendezvous = snapshot_rendezvous
        self._send_snapshot = send_snapshot

        # Frame numbers the valve-gating probe let through, in the exact order their buffers enter
        # the snapshot branch (queue -> valve -> nvjpegenc -> appsink is a single synchronous push
        # chain per buffer, so FIFO order here matches pull order in the appsink callback below).
        # Populated on the queue's src-pad probe thread, consumed on the appsink's new-sample thread
        # (in practice the same call stack, but a lock is cheap insurance either way).
        # Per-source FIFOs. Keyed by source_index because each camera has its own independent
        # snapshot branch (queue -> valve -> nvjpegenc -> appsink is a synchronous push chain per
        # branch), so pull order matches push order *within* a source but never across sources.
        self._pending_snapshot_frames: "dict[int, deque[int]]" = {}
        self._pending_snapshot_frames_lock = threading.Lock()

        self._Gst, self._GLib = import_gst()
        self._pyds = import_pyds()
        self._Gst.init(None)

        self._pipeline: Any | None = None
        self._loop: Any | None = None
        self._rtsp_server: Any | None = None
        self._bus_error: Exception | None = None
        # FS-11 §11: every RTSP mount this pipeline registered, so shutdown can remove each one
        # explicitly rather than relying on process exit — a stale mount must never outlive the
        # configuration that created it across a controlled Bridge restart.
        self._mount_points_added: "list[str]" = []

    # --- Construction ----------------------------------------------------------------------------

    def build(self) -> None:
        """Construct and link every element (task item 3). Raises
        :class:`PipelineElementCreationError`/:class:`PipelineLinkError` on failure — never silently
        proceeds with a partially built pipeline."""
        Gst = self._Gst
        cfg = self._config

        pipeline = Gst.Pipeline.new("deepstream-bridge-pipeline")
        self._pipeline = pipeline

        # FS-11 §8, IP-13 T-243: one source bin per configured [sourceN] section — the single-
        # camera case (every Bridge run before this feature) is simply a one-element tuple, no
        # separate code path. Each element is named uniquely (its own source_index) so multiple
        # sources coexist in the same pipeline without a GStreamer element-name collision.
        source_elements = [self._create_source_element(src) for src in cfg.sources]
        streammux = self._make("nvstreammux", "stream-muxer")
        pgie = self._make("nvinfer", "primary-inference")
        tracker = self._make("nvtracker", "tracker") if cfg.tracker is not None else None
        nvvidconv = self._make("nvvideoconvert", "convertor")

        # FS-11 §11: which sources have their own annotated output mount. When any do, the OSD moves
        # *into* each per-camera branch (so each output is drawn with only its own camera's
        # detections) and the shared pre-demux nvdsosd is not built at all. Otherwise the original
        # single shared-OSD topology is built byte-for-byte as before.
        per_camera_sources = [src for src in cfg.sources if src.output_path]
        dynamic_outputs = cfg.rtsp_out.enabled and bool(per_camera_sources)

        nvosd = None if dynamic_outputs else self._make("nvdsosd", "onscreendisplay")

        for element in (*source_elements, streammux, pgie, tracker, nvvidconv, nvosd):
            if element is not None:
                pipeline.add(element)

        self._configure_streammux(streammux)
        self._configure_pgie(pgie)
        if tracker is not None:
            assert cfg.tracker is not None
            self._configure_tracker(tracker, cfg.tracker)
        if nvosd is not None:
            self._configure_osd(nvosd)

        # nvurisrcbin's video src pad ("vsrc_%u") has "Sometimes" availability — it appears only
        # once each source negotiates, independently of the others, so linking happens in each
        # source's own pad-added callback, not here. Each callback is bound to its own source's
        # sink_%u request-pad index (FS-11 §8) — never inferred from list position, so a sparse/
        # reordered SourceOrder still maps to the correct streammux pad.
        for source_element, source_cfg in zip(source_elements, cfg.sources):
            source_element.connect(
                "pad-added", self._on_source_pad_added, streammux, source_cfg.source_index
            )

        # One shared nvstreammux and exactly one shared nvinfer, always — batching N sources into a
        # single inference pass is the architecture this feature must not change (FS-11 §11).
        chain: Sequence[Any] = [
            streammux,
            pgie,
            *([tracker] if tracker is not None else []),
            nvvidconv,
            *([nvosd] if nvosd is not None else []),
        ]
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineLinkError(
                    f"failed to link {upstream.get_name()} -> {downstream.get_name()}"
                )
        tail = chain[-1]

        # FS-08 snapshot tap point (corrected).
        #
        # The snapshot branch may only ever hang off an *annotated, demultiplexed, single-camera*
        # buffer. Where that buffer lives depends entirely on the output topology:
        #
        #  * dynamic per-camera outputs (production): the OSD lives inside each per-camera branch,
        #    so the snapshot tee is built there too — see _attach_one_camera_output. Nothing is
        #    tapped here, because `tail` at this point is the shared *pre-demux, pre-OSD*
        #    nvvideoconvert carrying a batched NvBufSurface for every camera at once. Tapping it
        #    (as this code did until FS-08's re-architecture) yields an un-annotated, multi-camera
        #    batched buffer that nvjpegenc cannot turn into one camera's evidence JPEG — which is
        #    precisely why no snapshot ever reached the spool.
        #
        #  * legacy single shared output: the shared nvdsosd is `tail`, so the historical tee here
        #    is still correct and is built exactly as before.
        if cfg.snapshot.enabled and not dynamic_outputs:
            tee = self._make("tee", "osd-tee")
            pipeline.add(tee)
            if not tail.link(tee):
                raise PipelineLinkError(f"failed to link {tail.get_name()} -> osd-tee")
            self._attach_snapshot_branch(
                pipeline, tee, cfg.snapshot, source_index=_SHARED_OUTPUT_SOURCE_INDEX
            )
            branch_source = tee
        else:
            branch_source = tail

        if dynamic_outputs:
            # FS-11 §11: N enabled Cameras -> N independent annotated RTSP outputs, via one shared
            # nvstreamdemux. The detection probe is attached to the demuxer's own *batched* sink pad
            # (see _attach_per_camera_outputs) — never to the per-branch OSDs.
            self._attach_per_camera_outputs(
                pipeline, branch_source, per_camera_sources, cfg.rtsp_out
            )
        else:
            assert nvosd is not None
            # Legacy single shared output: probe on the batched nvdsosd sink pad, exactly as before.
            self._attach_probe(nvosd)
            if cfg.rtsp_out.enabled:
                self._attach_rtsp_out(pipeline, branch_source, cfg.rtsp_out)
            else:
                self._attach_fakesink(pipeline, branch_source)

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
        # Unique element name per source_index (FS-11 §8) — required once more than one source bin
        # can coexist in the same pipeline; GStreamer element names must be unique per-bin.
        element = self._make("nvurisrcbin", f"uri-source-bin-{cfg.source_index}")
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

    def _on_source_pad_added(
        self, _element: Any, pad: Any, streammux: Any, sink_pad_index: int
    ) -> None:
        """Link one ``nvurisrcbin``'s dynamic video pad to ``nvstreammux``'s
        ``sink_<sink_pad_index>`` request pad once it appears (FS-11 §8: the index is this
        source's own ``source_index``,
        never inferred from callback-invocation order — independent sources negotiate
        independently and may fire in any order). Never raises (this runs on a GStreamer streaming
        thread, not the caller of :meth:`build` — an exception here would not propagate as a
        normal Python error) — a link failure is logged instead, which surfaces as a downstream
        negotiation/bus error the run loop already handles. A failure on one source's pad does not
        prevent another source's callback from linking its own pad.
        """
        caps = pad.get_current_caps() or pad.query_caps()
        structure = caps.get_structure(0)
        if not structure.get_name().startswith("video/"):
            return  # ignore nvurisrcbin's optional audio pad (asrc_%u) — video only

        sink_pad = streammux.get_request_pad(f"sink_{sink_pad_index}")
        if sink_pad is None:
            _LOGGER.error(
                "bridge_streammux_request_pad_unavailable", extra={"sink_pad_index": sink_pad_index}
            )
            return
        if pad.link(sink_pad) != self._Gst.PadLinkReturn.OK:
            _LOGGER.error("bridge_source_pad_link_failed", extra={"sink_pad_index": sink_pad_index})

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

    def _attach_per_camera_outputs(
        self,
        pipeline: Any,
        batched_source: Any,
        sources: "list[SourceConfig]",
        cfg: RtspOutConfig,
    ) -> None:
        """FS-11 §11: fan one batched, inferred stream out into N independent annotated RTSP
        outputs — one per enabled Camera — through a single ``nvstreamdemux``.

        Topology (N sources, no per-count special-casing anywhere)::

            ... -> nvstreamdemux -> src_<source_index> -> queue -> nvvideoconvert -> nvdsosd
                -> nvvideoconvert -> capsfilter -> nvv4l2h264enc -> h264parse -> rtph264pay
                -> udpsink  ⇢  GstRtspServer factory on this Camera's own mount

        The detection probe is attached to the demuxer's **sink** pad: that pad still carries the
        batched ``NvDsBatchMeta``, so ``frame_meta.source_id`` remains the single, unchanged source
        of detection identity (FS-11 §11 — per-camera OSD is rendering only). Each branch's OSD
        draws only its own demuxed stream's metadata, so no camera's boxes can appear on another's
        output.
        """
        GstRtspServer = import_gst_rtsp_server()

        demux = self._make("nvstreamdemux", "stream-demuxer")
        pipeline.add(demux)
        if not batched_source.link(demux):
            raise PipelineLinkError(f"failed to link {batched_source.get_name()} -> nvstreamdemux")

        # Probe on the batched side of the demuxer — the identity contract, unchanged.
        self._attach_probe(demux)

        server = GstRtspServer.RTSPServer()
        server.props.service = str(cfg.port)
        mount_points = server.get_mount_points()

        for offset, source in enumerate(sources):
            index = source.source_index
            # Deterministic, collision-free port allocation: RTP and its RTCP companion occupy
            # consecutive ports, so each branch advances the base by two (FS-11 §11). Derived from
            # enumeration position, not source_index, so a sparse index set cannot push a port out
            # of range.
            udp_port = cfg.udp_port + (offset * 2)
            self._attach_one_camera_output(
                pipeline=pipeline,
                demux=demux,
                source=source,
                udp_port=udp_port,
                cfg=cfg,
                mount_points=mount_points,
                GstRtspServer=GstRtspServer,
            )
            _LOGGER.info(
                "bridge_camera_output_attached",
                extra={
                    "source_index": index,
                    "camera_id": source.camera_id,
                    "mount_point": self._mount_point_of(source),
                    "udp_port": udp_port,
                },
            )

        server.attach(None)
        self._rtsp_server = server
        _LOGGER.info(
            "bridge_per_camera_outputs_attached",
            extra={"port": cfg.port, "output_count": len(sources)},
        )

    @staticmethod
    def _mount_point_of(source: "SourceConfig") -> str:
        """GstRtspServer mount points are absolute paths; the configured output path is relative."""
        return "/" + source.output_path.strip("/")

    def _attach_one_camera_output(
        self,
        *,
        pipeline: Any,
        demux: Any,
        source: "SourceConfig",
        udp_port: int,
        cfg: RtspOutConfig,
        mount_points: Any,
        GstRtspServer: Any,
    ) -> None:
        """Build exactly one Camera's demuxed output branch and publish its own RTSP mount."""
        index = source.source_index
        suffix = f"-{index}"

        queue_el = self._make("queue", f"out-queue{suffix}")
        preconv = self._make("nvvideoconvert", f"out-preconv{suffix}")
        nvosd = self._make("nvdsosd", f"out-osd{suffix}")
        postconv = self._make("nvvideoconvert", f"out-postconv{suffix}")
        capsfilter = self._make("capsfilter", f"out-caps{suffix}")
        encoder = self._make("nvv4l2h264enc", f"out-encoder{suffix}")
        h264parse = self._make("h264parse", f"out-h264parse{suffix}")
        rtppay = self._make("rtph264pay", f"out-rtppay{suffix}")
        udpsink = self._make("udpsink", f"out-udpsink{suffix}")

        # Same OSD properties the shared single-output path applies — per-camera rendering must not
        # silently differ from what this deployment already produces.
        self._configure_osd(nvosd)

        capsfilter.set_property(
            "caps", self._Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420")
        )
        # Identical encoder/payloader settings to _attach_rtsp_out (T-92/T-94 fixes) — a per-camera
        # output must inherit the same late-join/packet-loss recovery behaviour.
        encoder.set_property("bitrate", cfg.bitrate)
        encoder.set_property("idrinterval", cfg.idr_interval)
        encoder.set_property("iframeinterval", cfg.iframe_interval)
        encoder.set_property("insert-sps-pps", cfg.insert_sps_pps)
        h264parse.set_property("config-interval", cfg.h264parse_config_interval)
        rtppay.set_property("config-interval", cfg.rtph264pay_config_interval)
        udpsink.set_property("host", cfg.multicast_group)
        udpsink.set_property("port", udp_port)
        udpsink.set_property("async", False)
        udpsink.set_property("sync", True)

        # FS-08: when snapshot capture is enabled, a tee is inserted immediately after *this camera's*
        # nvdsosd. Everything downstream of the tee's first pad is byte-for-byte the branch that
        # already exists, so the live RTSP output is unchanged; the snapshot branch hangs off a
        # second request pad and is bounded/leaky so it can never back-pressure that output.
        #
        # This is the only point in the pipeline where a buffer is simultaneously demultiplexed (one
        # camera) and annotated (post-OSD), which is exactly what FS-08's evidence contract requires.
        snapshot_tee = (
            self._make("tee", f"snapshot-tee{suffix}") if self._config.snapshot.enabled else None
        )

        head = (queue_el, preconv, nvosd)
        tail_elements = (postconv, capsfilter, encoder, h264parse, rtppay, udpsink)
        branch = (*head, *([snapshot_tee] if snapshot_tee is not None else []), *tail_elements)

        for element in branch:
            pipeline.add(element)
        for upstream, downstream in zip(branch, branch[1:]):
            if not upstream.link(downstream):
                raise PipelineLinkError(
                    f"failed to link {upstream.get_name()} -> {downstream.get_name()}"
                )

        if snapshot_tee is not None:
            self._attach_snapshot_branch(
                pipeline, snapshot_tee, self._config.snapshot, source_index=index
            )

        # nvstreamdemux's src_%u pads are request pads, and the index is this source's own
        # source_index — the same index it occupies on nvstreammux's sink_%u side, which is what
        # keeps output N showing Camera N and nothing else.
        src_pad = demux.get_request_pad(f"src_{index}")
        if src_pad is None:
            raise PipelineLinkError(f"nvstreamdemux has no src_{index} request pad for this source")
        queue_sink_pad = queue_el.get_static_pad("sink")
        if src_pad.link(queue_sink_pad) != self._Gst.PadLinkReturn.OK:
            raise PipelineLinkError(f"failed to link nvstreamdemux src_{index} -> its output queue")

        factory = GstRtspServer.RTSPMediaFactory()
        factory.set_launch(
            f"( udpsrc name=pay0 port={udp_port} buffer-size=524288 "
            f'caps="application/x-rtp, media=video, clock-rate=90000, '
            f'encoding-name={cfg.codec}, payload=96" )'
        )
        factory.set_shared(True)
        mount_points.add_factory(self._mount_point_of(source), factory)
        self._mount_points_added.append(self._mount_point_of(source))

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
        # probe.handle_buffer, which is pure Python + injected pyds, never Gst. on_candidate (IP-10
        # T-133) is likewise a cheap, synchronous, in-memory-only call — never JPEG/GStreamer work.
        on_candidate = (
            self._snapshot_rendezvous.record_detection
            if self._snapshot_rendezvous is not None
            else None
        )
        # Overwrite nvinfer/nvtracker's default on-screen text (label + numeric tracking ID, e.g.
        # "Gun 5") with a score-based label (see probe.py) before nvdsosd renders this same buffer —
        # this probe sits on nvdsosd's own sink pad, so the mutation always lands in time. The
        # formatting itself lives in probe.py, never here — this module stays free of any
        # detection-scoring logic by design.
        apply_display_text(self._pyds, info.get_buffer())
        handle_buffer(self._pyds, info.get_buffer(), self._enqueue, on_candidate)
        return self._Gst.PadProbeReturn.OK

    # --- Snapshot branch (IP-10 T-134/T-135, FS-08 §3) ------------------------------------------------

    def _attach_snapshot_branch(
        self, pipeline: Any, tee: Any, cfg: SnapshotConfig, *, source_index: int
    ) -> None:
        """One camera's snapshot branch: ``queue(leaky=downstream, small bounded max-size-buffers)
        -> valve (closed by default) -> nvvideoconvert -> capsfilter(NV12/NVMM) -> nvjpegenc
        (hardware, FS-08 §3 spike finding) -> appsink``.

        Built once **per enabled Camera**, hanging off that camera's own post-OSD tee, so every
        element name is suffixed with the source index and every buffer that reaches it belongs to
        exactly one camera. The existing RTSP-out branch on the tee's first pad is untouched.

        ``source_index`` is carried through the valve probe and the appsink callback into the
        candidate structures, because a frame number alone is **not** unique across demultiplexed
        cameras — two cameras can present the same frame number simultaneously, and correlating on
        it alone could attach one camera's JPEG to the other camera's detection.
        """
        suffix = f"-{source_index}"
        queue_el = self._make("queue", f"snapshot-queue{suffix}")
        queue_el.set_property("leaky", _QUEUE_LEAK_DOWNSTREAM)
        queue_el.set_property("max-size-buffers", _SNAPSHOT_QUEUE_MAX_SIZE_BUFFERS)
        queue_el.set_property("max-size-bytes", 0)
        queue_el.set_property("max-size-time", 0)

        valve = self._make("valve", f"snapshot-valve{suffix}")
        valve.set_property("drop", True)

        # nvdsosd's src pad can negotiate either NV12 or RGBA (gst-inspect-1.0 nvdsosd), but
        # nvjpegenc's NVMM sink template only accepts NV12/I420 (gst-inspect-1.0 nvjpegenc). The
        # RTSP-out branch already handles this with its own nvvideoconvert (see
        # _attach_rtsp_out's postconv); the snapshot branch taps the tee before any such
        # conversion, so it needs its own. Placed *after* the valve (not before) so the
        # comparatively expensive colorspace conversion only ever runs on the rare candidate frame
        # that gets through, never on every frame the branch sees.
        preconv = self._make("nvvideoconvert", f"snapshot-preconv{suffix}")
        capsfilter = self._make("capsfilter", f"snapshot-caps{suffix}")
        capsfilter.set_property(
            "caps", self._Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12")
        )

        jpegenc = self._make("nvjpegenc", f"snapshot-jpeg-encoder{suffix}")
        jpegenc.set_property("quality", cfg.jpeg_quality)

        appsink = self._make("appsink", f"snapshot-appsink{suffix}")
        appsink.set_property("emit-signals", True)
        appsink.set_property("max-buffers", 1)
        appsink.set_property("drop", True)
        appsink.set_property("sync", False)
        # Without this, a GstBaseSink-derived sink on a branch the valve keeps closed almost all
        # the time never receives a buffer during the pipeline's initial PAUSED->PLAYING preroll,
        # so it can stall waiting for that handshake to complete instead of emitting "new-sample"
        # as soon as the first (long-delayed) real buffer finally arrives — proven live on the
        # real Jetson: the buffer visibly reached the appsink's own sink pad (a pad probe there
        # fired, with valid JPEG-encoded data) but "new-sample" never fired until this was added.
        appsink.set_property("async", False)
        appsink.connect("new-sample", self._on_snapshot_new_sample, source_index)

        for element in (queue_el, valve, preconv, capsfilter, jpegenc, appsink):
            pipeline.add(element)

        chain = (tee, queue_el, valve, preconv, capsfilter, jpegenc, appsink)
        for upstream, downstream in zip(chain, chain[1:]):
            if not upstream.link(downstream):
                raise PipelineLinkError(
                    f"failed to link {upstream.get_name()} -> {downstream.get_name()}"
                )

        # The valve-gating probe (task item 3's last paragraph) sits on the queue's src pad — after
        # the bounded queue, before the (comparatively expensive) hardware JPEG encode — so only a
        # candidate frame ever reaches nvjpegenc at all.
        queue_src_pad = queue_el.get_static_pad("src")
        if queue_src_pad is None:
            raise PipelineLinkError(
                f"snapshot-queue{suffix} has no src pad to attach the valve-gating probe to"
            )
        queue_src_pad.add_probe(
            self._Gst.PadProbeType.BUFFER, self._on_snapshot_valve_probe, (valve, source_index)
        )

    def _on_snapshot_valve_probe(self, _pad: Any, info: Any, user_data: Any) -> Any:
        """Briefly open the valve for exactly the buffer matching a recorded candidate frame
        number, then close it again for every other buffer (FS-08 §3). Never raises — an
        unrecognized/expired frame is simply treated as non-candidate (valve stays closed)."""
        valve, source_index = user_data
        frame_number = frame_number_from_buffer(self._pyds, info.get_buffer())
        is_candidate = (
            frame_number is not None
            and self._snapshot_rendezvous is not None
            and self._snapshot_rendezvous.consume_candidate(source_index, frame_number)
        )
        if is_candidate:
            # Record the frame number here, while NvDsBatchMeta is still attached to the buffer —
            # nvjpegenc produces a new output buffer for the encoded JPEG that does not carry the
            # metadata forward, so frame_number_from_buffer() on the appsink side would find nothing
            # (this was the actual root cause of "genuine detections, zero persisted JPEGs": the
            # candidate frame was correctly gated through the valve and encoded, but
            # _on_snapshot_new_sample could never recover which frame_number the resulting JPEG
            # belonged to, so it silently discarded every one). See
            # _pending_snapshot_frames' docstring in __init__.
            assert frame_number is not None  # noqa: S101 -- guaranteed by is_candidate above
            with self._pending_snapshot_frames_lock:
                self._pending_snapshot_frames.setdefault(source_index, deque()).append(frame_number)
        valve.set_property("drop", not is_candidate)
        return self._Gst.PadProbeReturn.OK

    def _on_snapshot_new_sample(self, appsink: Any, source_index: int) -> Any:
        """appsink's ``new-sample`` callback (IP-10 T-135) — runs on the snapshot branch's own
        streaming thread, never the nvdsosd sink-pad probe thread, satisfying "JPEG work never
        occurs in the pad-probe thread". Pulls the encoded JPEG sample, copies it to plain
        ``bytes`` (never retaining the ``GstBuffer``/sample itself), and submits it to the
        rendezvous. If the Agent's acknowledgement already arrived first (IP-10 the ACK-before-JPEG
        ordering), this call itself completes the rendezvous and must send the snapshot — with the
        rendezvous's internal lock already released by the time it returns, so ``send_snapshot`` (a
        non-blocking enqueue, never I/O-blocking, matching ``self._enqueue``'s own contract) is safe
        to call directly here. A capture failure is logged and swallowed — it must never stop the
        pipeline (task item)."""
        Gst = self._Gst
        try:
            sample = appsink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK

            # Not frame_number_from_buffer(self._pyds, gst_buffer) here: this buffer is nvjpegenc's
            # own encoded-JPEG output, which does not carry NvDsBatchMeta forward from its input —
            # the frame number instead comes from the valve-gating probe's FIFO handoff (see
            # __init__/_on_snapshot_valve_probe), recorded while the metadata was still attached.
            with self._pending_snapshot_frames_lock:
                pending = self._pending_snapshot_frames.get(source_index)
                frame_number = pending.popleft() if pending else None
            if frame_number is None:
                _LOGGER.warning("bridge_snapshot_frame_number_unavailable")
                return Gst.FlowReturn.OK
            if self._snapshot_rendezvous is None:
                return Gst.FlowReturn.OK

            gst_buffer = sample.get_buffer()
            mapped, map_info = gst_buffer.map(Gst.MapFlags.READ)
            if not mapped:
                return Gst.FlowReturn.OK
            try:
                jpeg_bytes = bytes(map_info.data)
            finally:
                gst_buffer.unmap(map_info)

            completed = self._snapshot_rendezvous.submit_jpeg(
                source_index, frame_number, jpeg_bytes
            )
            _LOGGER.debug(
                "bridge_snapshot_candidate_cached",
                extra={
                    "frame_number": frame_number,
                    "size_bytes": len(jpeg_bytes),
                    "completed_immediately": completed is not None,
                },
            )
            if completed is not None and self._send_snapshot is not None:
                event_id, completed_jpeg_bytes = completed
                self._send_snapshot(event_id, completed_jpeg_bytes)
        except Exception:  # noqa: BLE001 - capture failure must never fault the pipeline
            _LOGGER.exception("bridge_snapshot_capture_failed")
        return Gst.FlowReturn.OK

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
        if :meth:`build`/:meth:`run` never completed. Also releases every retained snapshot cache
        entry (IP-10 T-135 test requirement: "shutdown releases all retained cache entries") — no
        cached JPEG bytes outlive the Bridge process that captured them."""
        if self._pipeline is not None:
            self._pipeline.set_state(self._Gst.State.NULL)
            self._pipeline = None

        # Remove every mount this pipeline registered before dropping the server reference (FS-11
        # §11: "no stale mount remains"). Best-effort per mount — one failure must not prevent the
        # rest of shutdown, which is the only path that releases the pipeline's GPU resources.
        if self._rtsp_server is not None and self._mount_points_added:
            try:
                mount_points = self._rtsp_server.get_mount_points()
                for mount_point in self._mount_points_added:
                    mount_points.remove_factory(mount_point)
            except Exception:  # noqa: BLE001 - teardown must never raise
                _LOGGER.exception("bridge_rtsp_mount_removal_failed")
        self._mount_points_added = []

        self._rtsp_server = None
        self._loop = None
        if self._snapshot_rendezvous is not None:
            self._snapshot_rendezvous.clear()
        with self._pending_snapshot_frames_lock:
            self._pending_snapshot_frames.clear()
