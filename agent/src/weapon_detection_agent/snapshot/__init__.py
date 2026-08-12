"""Agent-side snapshot evidence capture/upload package (IP-10, FS-08).

Mirrors :mod:`weapon_detection_agent.sync`'s package shape: :mod:`.models` (typed result/failure
types), :mod:`.client` (the ``SnapshotUploadClient`` HTTP client), :mod:`.worker`
(``SnapshotUploadWorker`` and its composition-root factory).
"""

from __future__ import annotations
