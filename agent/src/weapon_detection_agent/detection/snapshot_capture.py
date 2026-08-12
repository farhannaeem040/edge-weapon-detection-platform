"""Snapshot file validation and atomic spool write (IP-10 T-140, FS-08 §5).

Pure-Python only — no third-party image library dependency is introduced for a bounds check this
small (Engineering Principle 9): :func:`parse_jpeg_dimensions` is a minimal JPEG SOF-marker scanner,
enough to confirm the payload decodes to a non-zero width/height without pulling in Pillow/OpenCV.

Every validation failure is a returned/raised :class:`SnapshotValidationError` naming only a safe
category — never raw image bytes are logged or embedded in an error message.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from weapon_detection_agent.config.paths import modes_enforceable
from weapon_detection_agent.persistence.snapshot_models import CaptureStatus

if TYPE_CHECKING:
    from weapon_detection_agent.persistence.snapshot_outbox_repository import (
        SnapshotOutboxRepository,
    )

_LOGGER = logging.getLogger("weapon_detection_agent.detection.snapshot_capture")

JPEG_MAGIC = b"\xff\xd8\xff"
CONTENT_TYPE_JPEG = "image/jpeg"

# FS-08 §5: "directory mode 0750, file mode 0640". Directory mode also lives in config/paths.py's
# SNAPSHOTS_DIR_MODE (the same value) since AgentPaths.provision() applies it at startup; this
# module reapplies it defensively to the exact spool directory it writes into, in case a caller
# supplies a spool path other than the provisioned default.
SNAPSHOT_DIR_MODE = 0o750
SNAPSHOT_FILE_MODE = 0o640

# JPEG Start-Of-Frame markers that carry a height/width pair, immediately following the 2-byte
# segment length. 0xC4 (DHT), 0xC8 (JPG), 0xCC (DAC) are deliberately excluded — they are not SOF
# markers despite falling in the 0xC0-0xCF range.
_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_MARKERS_WITHOUT_LENGTH = frozenset({0xD8, 0xD9, *range(0xD0, 0xD8)})  # SOI/EOI/RSTn


class SnapshotValidationError(Exception):
    """A captured snapshot failed FS-08 §5 validation — named category only, no image content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return ``(width, height)`` from the first SOF marker found, or ``None`` if undecodable.

    A minimal, defensive scanner: bounds every read against ``len(data)`` and returns ``None`` on
    any structural inconsistency rather than raising or reading out of bounds.
    """
    n = len(data)
    if n < 4 or data[0:2] != b"\xff\xd8":
        return None

    i = 2
    while i + 1 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1  # fill byte; the real marker byte follows
            continue
        if marker in _MARKERS_WITHOUT_LENGTH:
            i += 2
            continue
        if i + 4 > n:
            return None
        seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
        if seg_len < 2:
            return None
        if marker in _SOF_MARKERS:
            if i + 2 + 2 + 1 + 4 > n:  # marker+len, precision byte, height+width
                return None
            height = int.from_bytes(data[i + 5 : i + 7], "big")
            width = int.from_bytes(data[i + 7 : i + 9], "big")
            return width, height
        i += 2 + seg_len

    return None


def validate_snapshot_bytes(data: bytes, *, max_file_bytes: int) -> tuple[int, int]:
    """Validate raw JPEG bytes per FS-08 §5. Returns ``(width, height)``.

    Raises :class:`SnapshotValidationError` for: an empty payload, one exceeding
    ``max_file_bytes``, a missing JPEG magic, or undecodable/zero dimensions. The wire-supplied
    SHA-256 (if any) is never consulted here — :func:`compute_sha256` is the only trusted source.
    """
    if not data:
        raise SnapshotValidationError("empty_payload")
    if len(data) > max_file_bytes:
        raise SnapshotValidationError("oversized_file")
    if not data.startswith(JPEG_MAGIC):
        raise SnapshotValidationError("invalid_jpeg_magic")

    dimensions = parse_jpeg_dimensions(data)
    if dimensions is None or dimensions[0] <= 0 or dimensions[1] <= 0:
        raise SnapshotValidationError("undecodable_dimensions")
    return dimensions


def compute_sha256(data: bytes) -> str:
    """The Agent's own recomputed SHA-256 — never trusted from the wire (FS-08 §5)."""
    return hashlib.sha256(data).hexdigest()


def spool_usage_bytes(spool_path: Path) -> int:
    """Sum the size of every ``*.jpg`` file directly under ``spool_path`` (T-149 quota check).

    Returns 0 for a spool directory that does not yet exist. Never raises on a transient stat
    failure (e.g. a concurrent delete by the upload worker) — that entry is simply skipped.
    """
    if not spool_path.is_dir():
        return 0
    total = 0
    for entry in spool_path.glob("*.jpg"):
        try:
            total += entry.stat().st_size
        except OSError:
            continue
    return total


def write_snapshot_atomic(spool_path: Path, event_id: UUID, data: bytes) -> Path:
    """Write ``data`` to ``<spool_path>/<event_id>.jpg`` via temp-file + fsync + atomic rename.

    FS-08 §5's write discipline exactly: a temp file in the *same* directory (so the final
    ``os.replace`` is a same-filesystem atomic rename, never a cross-filesystem copy), ``fsync``ed
    before the rename, so no partially-written file is ever visible under its final name. The temp
    file name is derived from a fresh UUID (never the EventId alone), so two concurrent writers for
    the same event can never collide on the same temp path — though FS-08 §5 guarantees only one
    write per accepted EventId in practice.
    """
    spool_path.mkdir(parents=True, exist_ok=True)
    if modes_enforceable():
        os.chmod(spool_path, SNAPSHOT_DIR_MODE)

    final_path = spool_path / f"{event_id}.jpg"
    temp_path = spool_path / f".{event_id}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if modes_enforceable():
            os.chmod(temp_path, SNAPSHOT_FILE_MODE)
        os.replace(temp_path, final_path)
    except BaseException:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    return final_path


def reconcile_snapshot_spool(repository: SnapshotOutboxRepository, spool_path: Path) -> None:
    """Startup safety reconciliation (IP-10 T-148). Two independent, minimal policies:

    1. **A ``SnapshotOutbox`` row whose ``LocalPath`` is missing** (the process crashed, or the
       filesystem was tampered with, after the row was committed but before/while the file was
       written or after it was externally removed) is marked ``capture_failed`` — never crashes,
       never re-attempts the capture (the original JPEG bytes are gone), and never blocks any other
       row (T-148/T-149 posture).
    2. **A spool file with no matching ``SnapshotOutbox`` row** (a crash between the atomic rename
       completing and the SQLite ``INSERT`` committing) is an orphan: since it can never be
       associated with an ``EventId``'s row, uploaded, or safely correlated back to anything, and
       the spool is a bounded-size cache rather than a source of truth, the documented policy is to
       delete it — the alternative (leaving it forever) would only ever grow the spool for a file
       nothing can ever act on. Any single file's removal failure is logged and does not stop the
       rest of the reconciliation pass.

    Called once, synchronously, before :class:`~weapon_detection_agent.detection.ingest_handler.
    DetectionIngestHandler` starts accepting connections. Never raises — a reconciliation problem is
    logged, never fatal to Agent startup.
    """
    try:
        known_ids = set(repository.list_all_event_ids())
    except Exception:
        _LOGGER.exception("snapshot_spool_reconciliation_failed")
        return

    for event_id in known_ids:
        try:
            record = repository.get(event_id)
        except Exception:
            _LOGGER.exception("snapshot_spool_reconciliation_row_read_failed")
            continue
        if record is None or record.capture_status is not CaptureStatus.CAPTURED:
            continue
        if not record.local_path.is_file():
            try:
                repository.mark_capture_failed(event_id)
            except Exception:
                _LOGGER.exception("snapshot_spool_reconciliation_mark_failed_error")

    if not spool_path.is_dir():
        return

    known_stems = {str(event_id) for event_id in known_ids}
    for entry in spool_path.glob("*.jpg"):
        if entry.stem in known_stems:
            continue
        try:
            entry.unlink()
            _LOGGER.info("snapshot_spool_orphan_removed")
        except OSError:
            _LOGGER.warning("snapshot_spool_orphan_removal_failed")
