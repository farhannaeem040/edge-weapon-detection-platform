"""Detection event domain: model, validation, cooldown, persistence, and transport (IP-07).

Importing this subpackage performs no I/O. The DeepStream Bridge (a separate application, FS-05
§4.6) is never imported here or anywhere under ``agent/src`` — this subpackage is the Agent-side
half of the boundary ADR-005 draws at the Unix domain socket.
"""

from weapon_detection_agent.detection.models import DetectionEvent
from weapon_detection_agent.detection.validation import (
    DetectionRejection,
    DetectionRejectionReason,
    validate_detection,
)

__all__ = [
    "DetectionEvent",
    "DetectionRejection",
    "DetectionRejectionReason",
    "validate_detection",
]
