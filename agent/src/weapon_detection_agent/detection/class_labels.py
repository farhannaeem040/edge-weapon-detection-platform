"""Loads a DeepStream model profile's class-id -> class-name mapping (IP-07 T-87, FS-05 §5/§9).

**Genericness (binding, mirrors `deepstream/process_manager.py`'s own discipline).** This module
knows only the profile-directory layout `deploy-engine.sh` already stages
(`<root>/config/deepstream/profiles/<profile>/labels.txt`) and the DeepStream `nvinfer` labelfile
convention itself: one class name per line, the class id being the zero-based line position. It
contains no hardcoded class count, no hardcoded class name, and no assumption that class 0 is any
particular thing — reading a different profile's `labels.txt` changes the returned mapping with no
code change here.

The path is resolved from :class:`~weapon_detection_agent.config.settings.AgentSettings` alone (pure
path arithmetic, no I/O — the same posture as `config/paths.py`); reading it is a separate, explicit
step, done once by the caller at construction/startup time (IP-07 T-87 item 7), never per detection
event.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from weapon_detection_agent.detection.errors import DetectionClassLabelsUnavailableError

if TYPE_CHECKING:
    from weapon_detection_agent.config.settings import AgentSettings

# The profile-directory layout deploy-engine.sh already stages under the Agent root (deployment/
# jetson/deepstream/deploy-engine.sh's own PROFILES_DIR) — named here as the single place the Agent
# side of that convention is written down, mirroring how config/paths.py names its own file names.
_PROFILES_SUBDIR = ("config", "deepstream", "profiles")
_LABELS_FILENAME = "labels.txt"


def resolve_class_labels_path(settings: AgentSettings) -> Path:
    """The active model profile's ``labels.txt`` path, derived from settings alone (no I/O)."""
    return settings.root_path.joinpath(
        *_PROFILES_SUBDIR, settings.deepstream_model_profile
    ).joinpath(_LABELS_FILENAME)


def load_class_names(path: Path) -> dict[int, str]:
    """Read a DeepStream labelfile into a class-id -> class-name mapping.

    One label per line; the class id is the label's zero-based line position, matching the
    `nvinfer` labelfile convention every profile's `infer-config.txt` already relies on. A trailing
    blank line (a common text-editor/newline-at-EOF artifact) is ignored; a blank line anywhere else
    in the file is preserved positionally, since silently skipping it would shift every later class
    id relative to the model's own output.

    Raises :class:`DetectionClassLabelsUnavailableError` if the file cannot be read or is empty —
    an empty mapping means no detection could ever resolve a class name, which is a configuration
    problem, not a runtime one to discover per-event.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DetectionClassLabelsUnavailableError(
            f"could not read the active model profile's class-label file: {path}"
        ) from exc

    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise DetectionClassLabelsUnavailableError(
            f"the active model profile's class-label file is empty: {path}"
        )

    return dict(enumerate(lines))
