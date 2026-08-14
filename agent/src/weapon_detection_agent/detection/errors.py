"""Typed errors for :class:`~weapon_detection_agent.detection.ingest_handler.DetectionIngestHandler`
(IP-07 T-86).

Every message here is a static string or names only a filesystem path — never a payload, a stored
value, or credential material — mirroring :mod:`weapon_detection_agent.deepstream.errors`'s posture.
"""

from __future__ import annotations


class DetectionIngestHandlerError(RuntimeError):
    """Base class for a :class:`DetectionIngestHandler` failure."""


class DetectionIngestHandlerAlreadyRunningError(DetectionIngestHandlerError):
    """``start()`` was called while the handler already has an active listener.

    No second listener is bound (mirrors ``DeepStreamAlreadyRunningError``'s "no duplicate" rule).
    """


class DetectionRuntimeDirectoryMissingError(DetectionIngestHandlerError):
    """The detection socket's parent directory does not exist.

    Provisioning ``runtime/`` is the filesystem-layout task's responsibility (T-82); this handler
    never creates it, mirroring how ``persistence.database.connect`` refuses to create
    ``database/``.
    """


class DetectionSocketPathConflictError(DetectionIngestHandlerError):
    """The configured socket path exists and is not a Unix domain socket left by a prior run.

    Raised for a regular file, directory, or symlink found at the socket path — anything the handler
    cannot safely conclude is a stale socket from a previous crashed process. Never removed
    automatically; an operator must resolve the conflict.
    """


class DetectionDeviceIdentityUnavailableError(DetectionIngestHandlerError):
    """No usable persisted device identity exists while detection events are enabled (IP-07 T-87).

    Detection events require the Agent's own persisted Device ID (T-58) — never one invented here,
    hardcoded, or trusted from the Bridge/wire (FS-05 §5). Rather than start a partially functional
    ingest pipeline with a placeholder identity, this is raised and propagates through the same
    fail-loud startup path every other construction failure already uses.
    """


class DetectionClassLabelsUnavailableError(DetectionIngestHandlerError):
    """The active model profile's class-label file could not be read, or is empty.

    Named only by path — never by file content — mirroring this module's no-value-in-error-message
    discipline.
    """
