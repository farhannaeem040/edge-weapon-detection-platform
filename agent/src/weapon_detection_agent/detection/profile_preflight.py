"""Class/profile preflight checks before a detection-enabled cutover (IP-07 T-90, task item 6).

Pure, read-only filesystem checks confirming the active model profile's files are present and
readable — never rebuilds or re-verifies the TensorRT engine's *contents* (`deploy-engine.sh`'s
checksum verification already did that at deploy time; task item 6 explicitly excludes rebuilding
it here). Deliberately **not** wired into `AgentSettings`/`main.py`'s own startup path — adding
filesystem I/O to a component-construction step that today performs none would be a real behaviour
change beyond this task's "static checks and preflight" scope. Instead this is the library the
production cutover preflight command (task item 11,
`deployment/jetson/deepstream/bridge/preflight-cutover.sh`) calls on demand before a cutover.

No hardcoded gun/knife or YOLOv4 assumption anywhere below — every fact comes from
`settings.deepstream_model_profile` and the profile's own files, mirroring
`class_labels.py`/`deploy-engine.sh`'s own genericness discipline.

**Duplicate/blank label rule (task item 6, "approved rule").** A duplicate label (two class ids
sharing the same name) is not a preflight failure — a model's own class ids are what the wire
protocol and validator key on (FS-05 §5); a repeated name is unusual but not unsafe, so it is
reported as a non-fatal `duplicate_labels` fact, not a `problems` entry. A blank line is handled
exactly as `class_labels.load_class_names` already documents: a single trailing blank (a common
editor/newline-at-EOF artifact) is ignored, while a blank line anywhere else is preserved
positionally (it becomes an empty-string class name for that id, which the wire-side validator
already rejects as an unresolvable class — FS-05 §5 — never silently reindexing later classes).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from weapon_detection_agent.detection.class_labels import load_class_names
from weapon_detection_agent.detection.errors import DetectionClassLabelsUnavailableError

if TYPE_CHECKING:
    from weapon_detection_agent.config.settings import AgentSettings

_PROFILES_SUBDIR = ("config", "deepstream", "profiles")
_MODELS_SUBDIR = ("models",)
_INFER_CONFIG_FILENAME = "infer-config.txt"
_LABELS_FILENAME = "labels.txt"
_ENGINE_FILENAME = "model.engine"


@dataclass(frozen=True)
class ProfilePreflightProblem:
    """One fatal preflight finding. ``detail`` names a path/fact only — never file *contents*."""

    check: str
    detail: str


@dataclass(frozen=True)
class ProfilePreflightResult:
    """The full preflight outcome. Truthy (``bool(result)``) iff every fatal check passed."""

    problems: tuple[ProfilePreflightProblem, ...] = field(default_factory=tuple)
    class_count: int | None = None
    duplicate_labels: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return not self.problems


def resolve_profile_directory(settings: AgentSettings) -> Path:
    """The active model profile's directory — pure path arithmetic, no I/O."""
    return settings.root_path.joinpath(*_PROFILES_SUBDIR, settings.deepstream_model_profile)


def resolve_engine_path(settings: AgentSettings) -> Path:
    """The active model profile's expected TensorRT engine path (`deploy-engine.sh`'s own
    install target) — pure path arithmetic, no I/O."""
    return settings.root_path.joinpath(
        *_MODELS_SUBDIR, settings.deepstream_model_profile, _ENGINE_FILENAME
    )


def run_profile_preflight(settings: AgentSettings) -> ProfilePreflightResult:
    """Run every read-only profile/class-label check task item 6 requires.

    Stops adding further checks once the profile directory itself is missing (nothing else can be
    meaningfully checked relative to a directory that doesn't exist), but otherwise runs every
    remaining check even after an earlier one fails, so a single preflight call reports every
    problem at once rather than one-at-a-time across repeated runs.
    """
    problems: list[ProfilePreflightProblem] = []
    profile_dir = resolve_profile_directory(settings)

    if not profile_dir.is_dir():
        problems.append(
            ProfilePreflightProblem(
                "profile_directory", f"missing or not a directory: {profile_dir}"
            )
        )
        return ProfilePreflightResult(problems=tuple(problems))

    infer_config = profile_dir / _INFER_CONFIG_FILENAME
    if not infer_config.is_file():
        problems.append(ProfilePreflightProblem("infer_config", f"missing: {infer_config}"))
    else:
        try:
            infer_config.read_text(encoding="utf-8")
        except OSError:
            problems.append(
                ProfilePreflightProblem("infer_config", f"not readable: {infer_config}")
            )

    class_count: int | None = None
    duplicate_labels: tuple[str, ...] = ()
    labels_path = profile_dir / _LABELS_FILENAME
    if not labels_path.is_file():
        problems.append(ProfilePreflightProblem("labels_file", f"missing: {labels_path}"))
    else:
        try:
            class_names = load_class_names(labels_path)
        except DetectionClassLabelsUnavailableError as exc:
            problems.append(ProfilePreflightProblem("labels_file", str(exc)))
        else:
            class_count = len(class_names)
            counts = Counter(name for name in class_names.values() if name)
            duplicate_labels = tuple(sorted(name for name, count in counts.items() if count > 1))

    engine_path = resolve_engine_path(settings)
    if not engine_path.is_file():
        problems.append(ProfilePreflightProblem("engine_file", f"missing: {engine_path}"))

    return ProfilePreflightResult(
        problems=tuple(problems), class_count=class_count, duplicate_labels=duplicate_labels
    )
