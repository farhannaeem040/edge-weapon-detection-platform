"""DeepStream process supervision (IP-06, FS-04 Phase 1).

Contains :class:`~weapon_detection_agent.deepstream.process_manager.DeepStreamProcessManager`, the
sole ``OperationalComponent`` this package exposes. It knows only a process to launch, watch, and
stop — no model, dataset, or profile detail lives here (FS-04 §8).
"""

from __future__ import annotations
