"""Raw metadata extraction (IP-07 T-88, FS-05 §4/§5, task items 5-6).

This module is deliberately **``pyds``-free at import time** — every function takes the ``pyds``
module as an injected argument rather than importing it at module scope, so the pure
extraction/serialization logic is unit-testable on any machine (including Windows, task item 12's
"no pyds import required for pure tests") with a fabricated ``pyds``-shaped stub standing in for the
real module. Only ``pipeline.py`` (never this module) imports the real ``pyds``/``gi``/``Gst``.

**What is extracted (task item 5).** Only raw inference facts already present on
``NvDsObjectMeta``/``NvDsFrameMeta``: ``class_id``, ``confidence``, ``source_id``, ``frame_number``,
frame dimensions, and the raw pixel bounding box. **Never** ``event_id``, ``device_id``,
``camera_id``, ``class_name``, ``detected_at_utc``, ``created_at_utc``, or ``delivery_status`` — the
:class:`RawDetection` dataclass has no fields for any of them, so it is structurally impossible for
this module to emit one; those are Agent-owned (FS-05 §5, T-83's validator resolves/generates them).

**Probe-path minimalism (task item 6).** :func:`handle_buffer` does no validation, no cooldown, no
SQLite, no backend/network I/O, no label resolution, no JSON retries, and no object tracking — it
only walks the batch/frame/object meta lists, builds plain immutable payload dicts, and hands each
one to the caller-supplied ``enqueue`` callable, which must itself be non-blocking (the real
callable, :meth:`~deepstream_bridge.transport.TransportWorker.enqueue`, uses ``queue.put_nowait``
and never raises). This function returns as soon as the batch is walked; the actual pad-probe
wrapper in ``pipeline.py`` returns ``Gst.PadProbeReturn.OK`` immediately after calling it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable

from deepstream_bridge.protocol import SCHEMA_VERSION

# The exact wire field set (task item 5) — nothing more, nothing less. Any change here is a wire
# schema change and must be mirrored in the Agent's validator (weapon_detection_agent.detection
# .validation) by hand, per FS-05 §4.6's documented no-shared-dependency tradeoff.
_WIRE_FIELDS = (
    "schema_version",
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


@dataclass(frozen=True)
class RawDetection:
    """One raw, unfiltered detection fact set for a single ``NvDsObjectMeta`` (task item 5).

    Immutable and plain (a frozen dataclass of primitives only) — the smallest structure that
    satisfies item 6's "construct a small immutable/plain payload" requirement.
    """

    schema_version: int
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


def extract_detections(pyds_module: Any, batch_meta: Any) -> list[RawDetection]:
    """Walk ``NvDsBatchMeta`` -> ``NvDsFrameMeta`` -> ``NvDsObjectMeta`` and return one
    :class:`RawDetection` per object, across every frame in the batch (task item 5: "iterate
    NvDsFrameMeta", "iterate NvDsObjectMeta", supports "multiple objects in one frame" and
    "multiple frames in a batch").

    ``pyds_module`` is injected (real ``pyds`` in production, a fabricated stub in tests). Mirrors
    the standard ``deepstream_python_apps`` iteration idiom: each linked-list node's ``.data`` is
    cast via the module's ``NvDs*Meta.cast``, and advancing past the last node raises
    ``StopIteration`` (the real ``pyds`` GList wrapper's documented behaviour) rather than yielding
    ``None`` — this function relies on exactly that contract.
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
) -> None:
    """Extract every detection from one ``GstBuffer`` and hand each payload to ``enqueue``.

    A no-op if ``gst_buffer`` is falsy/``None`` or no batch meta is attached — never raises for a
    frame with zero detections or missing metadata (task item 6: minimal, never blocking, never
    raising into the pad-probe callback). ``enqueue`` is the only I/O-adjacent call this function
    makes, and it must be non-blocking by contract (documented on
    :meth:`~deepstream_bridge.transport.TransportWorker.enqueue`).
    """
    if not gst_buffer:
        return

    batch_meta = pyds_module.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    if batch_meta is None:
        return

    for detection in extract_detections(pyds_module, batch_meta):
        enqueue(detection.to_payload())
