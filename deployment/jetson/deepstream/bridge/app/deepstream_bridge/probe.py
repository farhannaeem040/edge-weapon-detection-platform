"""Raw metadata extraction (IP-07 T-88, IP-10 T-131/T-133, FS-05 §4/§5, FS-08 §4.3).

This module is deliberately **``pyds``-free at import time** — every function takes the ``pyds``
module as an injected argument rather than importing it at module scope, so the pure
extraction/serialization logic is unit-testable on any machine (including Windows, task item 12's
"no pyds import required for pure tests") with a fabricated ``pyds``-shaped stub standing in for the
real module. Only ``pipeline.py`` (never this module) imports the real ``pyds``/``gi``/``Gst``.

**What is extracted (task item 5).** Only raw inference facts already present on
``NvDsObjectMeta``/``NvDsFrameMeta``: ``class_id``, ``confidence``, ``source_id``, ``frame_number``,
frame dimensions, and the raw pixel bounding box, plus (IP-10 T-131) a Bridge-generated
``message_id`` — a pure correlation handle for the acknowledgement round trip, never trusted as (or
sent instead of) ``EventId``. **Never** ``event_id``, ``device_id``, ``camera_id``, ``class_name``,
``detected_at_utc``, ``created_at_utc``, or ``delivery_status`` — the :class:`RawDetection` dataclass
has no fields for any of them, so it is structurally impossible for this module to emit one; those
are Agent-owned (FS-05 §5, T-83's validator resolves/generates them).

**Probe-path minimalism (task item 6).** :func:`handle_buffer` does no validation, no cooldown, no
SQLite, no backend/network I/O, no label resolution, no JSON retries, and no object tracking — it
only walks the batch/frame/object meta lists, builds plain immutable payload dicts, and hands each
one to the caller-supplied ``enqueue`` callable, which must itself be non-blocking (the real
callable, :meth:`~deepstream_bridge.transport.TransportWorker.enqueue`, uses ``queue.put_nowait``
and never raises). The optional ``on_candidate`` callable (IP-10 T-133) is the only other side effect
— a cheap, synchronous, in-memory bookkeeping call (never I/O) that lets ``pipeline.py``'s
snapshot-branch valve-gating probe and acknowledgement-driven capture logic know which
``source_id``/``frame_number``/``message_id`` triples are snapshot candidates. Both callables are invoked
synchronously on the pad-probe thread and must themselves never block or do JPEG/GStreamer work —
that work happens later, on the appsink's own thread (``pipeline.py``, FS-08 §3/§5).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

from deepstream_bridge.protocol import SCHEMA_VERSION, build_detection_message

# The exact wire field set (task item 5, extended IP-10 T-131 with message_id). Any change here is a
# wire schema change and must be mirrored in the Agent's validator
# (weapon_detection_agent.detection.validation) by hand, per FS-05 §4.6's documented
# no-shared-dependency tradeoff.
_WIRE_FIELDS = (
    "schema_version",
    "message_id",
    "class_id",
    "confidence",
    "source_id",
    "frame_number",
    "frame_width",
    "frame_height",
    "bbox_left",
    "bbox_top",
    "bbox_width",
    "bbox_height",
)

OnCandidate = Callable[[int, str, int], None]


@dataclass(frozen=True)
class RawDetection:
    """One raw, unfiltered detection fact set for a single ``NvDsObjectMeta`` (task item 5).

    Immutable and plain (a frozen dataclass of primitives only) — the smallest structure that
    satisfies item 6's "construct a small immutable/plain payload" requirement.
    """

    schema_version: int
    message_id: str
    class_id: int
    confidence: float
    source_id: int
    frame_number: int
    frame_width: int
    frame_height: int
    bbox_left: float
    bbox_top: float
    bbox_width: float
    bbox_height: float

    def to_payload(self) -> dict[str, Any]:
        """The exact wire dict (task item 5's field list, nothing else)."""
        payload = asdict(self)
        assert tuple(payload.keys()) == _WIRE_FIELDS
        return payload

    def to_wire_message(self) -> dict[str, Any]:
        """The v2 wire message (FS-08 §4.3): camelCase, nested ``boundingBox``, ``messageId`` —
        the shape :meth:`~deepstream_bridge.transport.TransportWorker.enqueue` actually sends. Kept
        distinct from :meth:`to_payload` (the internal, flat, snake_case field set every test in
        this module already asserts against) so a future internal-field addition to
        :class:`RawDetection` does not silently become a wire contract change without this function
        being touched too.
        """
        return build_detection_message(
            message_id=self.message_id,
            source_id=self.source_id,
            frame_number=self.frame_number,
            class_id=self.class_id,
            confidence=self.confidence,
            bbox_left=self.bbox_left,
            bbox_top=self.bbox_top,
            bbox_width=self.bbox_width,
            bbox_height=self.bbox_height,
        )


def extract_detections(
    pyds_module: Any,
    batch_meta: Any,
    message_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> list[RawDetection]:
    """Walk ``NvDsBatchMeta`` -> ``NvDsFrameMeta`` -> ``NvDsObjectMeta`` and return one
    :class:`RawDetection` per object, across every frame in the batch (task item 5: "iterate
    NvDsFrameMeta", "iterate NvDsObjectMeta", supports "multiple objects in one frame" and
    "multiple frames in a batch").

    ``pyds_module`` is injected (real ``pyds`` in production, a fabricated stub in tests).
    ``message_id_factory`` is injected too (IP-10 T-131) so tests can assert deterministic
    ``message_id`` values instead of depending on real UUID generation. Mirrors the standard
    ``deepstream_python_apps`` iteration idiom: each linked-list node's ``.data`` is cast via the
    module's ``NvDs*Meta.cast``, and advancing past the last node raises ``StopIteration`` (the real
    ``pyds`` GList wrapper's documented behaviour) rather than yielding ``None`` — this function
    relies on exactly that contract.
    """
    detections: list[RawDetection] = []

    frame_node = batch_meta.frame_meta_list
    while frame_node is not None:
        try:
            frame_meta = pyds_module.NvDsFrameMeta.cast(frame_node.data)
        except StopIteration:
            break

        source_id = int(frame_meta.source_id)
        frame_number = int(frame_meta.frame_num)
        frame_width = int(frame_meta.source_frame_width)
        frame_height = int(frame_meta.source_frame_height)

        obj_node = frame_meta.obj_meta_list
        while obj_node is not None:
            try:
                obj_meta = pyds_module.NvDsObjectMeta.cast(obj_node.data)
            except StopIteration:
                break

            rect = obj_meta.rect_params
            detections.append(
                RawDetection(
                    schema_version=SCHEMA_VERSION,
                    message_id=message_id_factory(),
                    class_id=int(obj_meta.class_id),
                    confidence=float(obj_meta.confidence),
                    source_id=source_id,
                    frame_number=frame_number,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    bbox_left=float(rect.left),
                    bbox_top=float(rect.top),
                    bbox_width=float(rect.width),
                    bbox_height=float(rect.height),
                )
            )

            try:
                obj_node = obj_node.next
            except StopIteration:
                break

        try:
            frame_node = frame_node.next
        except StopIteration:
            break

    return detections


def handle_buffer(
    pyds_module: Any,
    gst_buffer: Any,
    enqueue: Callable[[dict[str, Any]], None],
    on_candidate: Optional[OnCandidate] = None,
) -> None:
    """Extract every detection from one ``GstBuffer``, hand each wire message to ``enqueue``, and
    (IP-10 T-133) report each ``(source_id, message_id, frame_number)`` triple to ``on_candidate`` so the
    snapshot-branch valve-gating probe and acknowledgement-driven capture logic in ``pipeline.py``
    know which frames/messages are snapshot candidates.

    A no-op if ``gst_buffer`` is falsy/``None`` or no batch meta is attached — never raises for a
    frame with zero detections or missing metadata (task item 6: minimal, never blocking, never
    raising into the pad-probe callback). ``enqueue``/``on_candidate`` are the only I/O-adjacent
    calls this function makes; both must be non-blocking, in-memory-only by contract.
    """
    if not gst_buffer:
        return

    batch_meta = pyds_module.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if batch_meta is None:
        return

    for detection in extract_detections(pyds_module, batch_meta):
        if on_candidate is not None:
            on_candidate(detection.source_id, detection.message_id, detection.frame_number)
        enqueue(detection.to_wire_message())


def frame_number_from_buffer(pyds_module: Any, gst_buffer: Any) -> Optional[int]:
    """Read only the first frame's ``frame_number`` from ``gst_buffer``'s batch meta, if present.

    Used by ``pipeline.py``'s snapshot-branch valve-gating probe (IP-10 T-133/T-134), which runs on
    a pad carrying the same batch/frame metadata as the sink-pad probe above (batch-size is fixed at
    1 in this deployment's ``[streammux]`` config, so "first frame" is the only frame). Returns
    ``None`` for a falsy buffer or missing/empty batch meta — never raises.
    """
    if not gst_buffer:
        return None

    batch_meta = pyds_module.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if batch_meta is None:
        return None

    frame_node = batch_meta.frame_meta_list
    if frame_node is None:
        return None

    try:
        frame_meta = pyds_module.NvDsFrameMeta.cast(frame_node.data)
    except StopIteration:
        return None

    return int(frame_meta.frame_num)
