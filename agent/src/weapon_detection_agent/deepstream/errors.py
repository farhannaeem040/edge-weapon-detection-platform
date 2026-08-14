"""Typed errors for :class:`DeepStreamProcessManager` (IP-06 T-72).

Every message here is a static string — never a path, argv, or log line — so nothing environment-
or deployment-specific (and certainly no credential; DeepStream Phase 1 has none to leak) can reach
a caller through an exception.
"""

from __future__ import annotations


class DeepStreamProcessError(RuntimeError):
    """Base class for a :class:`DeepStreamProcessManager` failure."""


class DeepStreamAlreadyRunningError(DeepStreamProcessError):
    """``start()`` was called while a DeepStream process is already tracked as running.

    No second process is spawned (FS-04 Behavior #6, "no more than one DeepStream process exists").
    """
