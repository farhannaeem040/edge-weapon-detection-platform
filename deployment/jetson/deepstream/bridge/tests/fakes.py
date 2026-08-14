"""Fabricated ``pyds``-shaped stubs for offline probe tests (IP-07 T-88, task item 12).

Mirrors just enough of the real ``pyds`` surface (``NvDsFrameMeta.cast``/``NvDsObjectMeta.cast``,
``gst_buffer_get_nvds_batch_meta``, and the GList ``.data``/``.next`` iteration idiom) for
``deepstream_bridge.probe`` to run completely unmodified against fabricated data. Never imports the
real ``pyds`` — this file is safely importable on Windows with no DeepStream/GStreamer installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeRectParams:
    left: float
    top: float
    width: float
    height: float


@dataclass
class FakeObjectMeta:
    class_id: int
    confidence: float
    rect_params: FakeRectParams


@dataclass
class FakeFrameMeta:
    source_id: int
    frame_num: int
    source_frame_width: int
    source_frame_height: int
    objects: list = field(default_factory=list)

    @property
    def obj_meta_list(self) -> "_Node | None":
        return _build_list(self.objects)


@dataclass
class FakeBatchMeta:
    frames: list

    @property
    def frame_meta_list(self) -> "_Node | None":
        return _build_list(self.frames)


class _Node:
    """A fabricated GList node: ``.data`` is the payload, ``.next`` is the following node or
    ``None`` at the end of the list — the same shape ``probe.extract_detections`` relies on."""

    def __init__(self, data: Any, remaining: list[Any]) -> None:
        self.data = data
        self._remaining = remaining

    @property
    def next(self) -> "_Node | None":
        if not self._remaining:
            return None
        head, *rest = self._remaining
        return _Node(head, rest)


def _build_list(items: list[Any]) -> _Node | None:
    if not items:
        return None
    head, *rest = items
    return _Node(head, rest)


class _CastNamespace:
    @staticmethod
    def cast(data: Any) -> Any:
        # The fake meta objects are already the "cast" type — identity is enough for a stub.
        return data


class FakePydsModule:
    """Stands in for the real ``pyds`` module in offline tests."""

    NvDsFrameMeta = _CastNamespace
    NvDsObjectMeta = _CastNamespace

    def __init__(self, batch_meta_by_buffer: dict[Any, FakeBatchMeta] | None = None) -> None:
        self._batch_meta_by_buffer = batch_meta_by_buffer or {}

    def gst_buffer_get_nvds_batch_meta(self, buffer_hash: int) -> FakeBatchMeta | None:
        return self._batch_meta_by_buffer.get(buffer_hash)
