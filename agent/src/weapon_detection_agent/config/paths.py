"""Filesystem layout resolution and provisioning (IP-02 T-34, §8).

This module is the single source of truth for *where* the Agent keeps its state on disk and for
*creating* that layout with the restrictive permissions ARCH-001 §13.3 / ADR-008 require. It does
two things and nothing more:

* **Resolve** the paths of the ``/opt/weapon-detection/`` layout from a root (honouring
  ``WDA_ROOT_PATH`` via :attr:`AgentSettings.root_path`, IP-02 §8.2) — pure path arithmetic with no
  I/O, so a path object can be built and inspected without touching the disk.
* **Provision** only *this milestone's* directories, idempotently, each with its ADR-008 mode.

Deliberately out of scope for this task (IP-02 §8):

* The media/runtime directories ``snapshots/``, ``recordings/``, ``models/``, ``pipeline/`` and
  ``runtime/`` are part of the approved ADR-008 layout but have **no writer yet**, so they are not
  created here — the plans that introduce their writers create them (Engineering Principle 9).
* **Ownership** is not changed. Assigning the layout to the unprivileged ``weapon-detection``
  service user (D-2) is the installer's job on the Jetson (T-41); this module only sets *modes*.
* No file is created — the database file (T-35), the activation-key file (§6.1), and the log file
  (T-33/T-39) are written by the tasks that own them. This module resolves their paths and creates
  the directories they live in.

Windows note (IP-02 §17): POSIX permission modes are not meaningfully representable on Windows, so
mode enforcement is skipped there while directory creation still happens. The modes are exercised
for real on Linux/Jetson. :func:`modes_enforceable` exposes this so tests skip the mode assertions
on a platform that cannot honour them, rather than asserting something the OS ignores.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The default Agent root (ADR-008). Overridable solely via WDA_ROOT_PATH for tests and workstation
# runs (IP-02 §8.2); the systemd unit pins the real deployment to this path (§17).
DEFAULT_ROOT = Path("/opt/weapon-detection")

# Directory modes fixed by ADR-008 / IP-02 §8. config/ and database/ are 0700 because they hold the
# Activation Key file and the shared-secret-bearing database; the root and logs/ are 0750.
ROOT_MODE = 0o750
CONFIG_DIR_MODE = 0o700
DATABASE_DIR_MODE = 0o700
LOGS_DIR_MODE = 0o750

# Well-known file names within the layout. Their *paths* are resolved here as the single source of
# the layout; their *contents* are written by later tasks (see the module docstring).
DATABASE_FILENAME = "agent.db"
ACTIVATION_KEY_FILENAME = "activation-key"
LOG_FILENAME = "agent.log"

# ADR-008 directories this milestone must NOT create, because nothing writes to them yet. Named
# explicitly so the test that asserts their absence reads against one authoritative list.
DEFERRED_DIRECTORIES: tuple[str, ...] = (
    "snapshots",
    "recordings",
    "models",
    "pipeline",
    "runtime",
)


def modes_enforceable() -> bool:
    """Return whether POSIX permission modes can be enforced on this platform.

    ``True`` on POSIX systems (Linux/Jetson, macOS); ``False`` on Windows, where ``os.chmod`` cannot
    represent a mode like ``0o700``. Provisioning still creates the directories on every platform;
    only the mode enforcement is conditional (IP-02 §17).
    """
    return os.name == "posix"


@dataclass(frozen=True)
class AgentPaths:
    """The resolved ``/opt/weapon-detection/`` layout for a given root (IP-02 §8).

    Constructing this performs no I/O — every member below is a pure path derived from :attr:`root`,
    so it is safe to build and inspect without a filesystem. Call :meth:`provision` to create the
    directories on disk with their ADR-008 modes.
    """

    root: Path

    @property
    def config_dir(self) -> Path:
        """The ``config/`` directory (mode ``0700``) — holds the activation-key file (§6.1)."""
        return self.root / "config"

    @property
    def database_dir(self) -> Path:
        """The ``database/`` directory (mode ``0700``) — holds the SQLite store (T-35)."""
        return self.root / "database"

    @property
    def logs_dir(self) -> Path:
        """The ``logs/`` directory (mode ``0750``) — holds the Agent log files (T-33/T-39)."""
        return self.root / "logs"

    @property
    def database_file(self) -> Path:
        """Path of the SQLite database file (``database/agent.db``). Not created here (T-35)."""
        return self.database_dir / DATABASE_FILENAME

    @property
    def activation_key_file(self) -> Path:
        """Path of the activation-key file (``config/activation-key``). Not created here (§6.1)."""
        return self.config_dir / ACTIVATION_KEY_FILENAME

    @property
    def log_file(self) -> Path:
        """Path of the Agent log file (``logs/agent.log``). Not created here (T-33/T-39)."""
        return self.logs_dir / LOG_FILENAME

    @property
    def managed_directories(self) -> tuple[tuple[Path, int], ...]:
        """The directories this milestone creates, each with its ADR-008 mode, root-first.

        Root is first so it exists before its children are created. The media/runtime directories of
        ADR-008 are deliberately absent (see the module docstring).
        """
        return (
            (self.root, ROOT_MODE),
            (self.config_dir, CONFIG_DIR_MODE),
            (self.database_dir, DATABASE_DIR_MODE),
            (self.logs_dir, LOGS_DIR_MODE),
        )

    def provision(self) -> AgentPaths:
        """Create this milestone's directories with their ADR-008 modes, idempotently.

        Safe to call repeatedly: existing directories are left in place, and their modes are
        re-applied each run so a drifted permission is corrected rather than trusted. Only the four
        :attr:`managed_directories` are created — never the deferred media/runtime directories, and
        never any file. Returns ``self`` for convenient chaining.
        """
        for directory, mode in self.managed_directories:
            _ensure_directory(directory, mode)
        return self


def resolve_paths(root: Path | str = DEFAULT_ROOT) -> AgentPaths:
    """Resolve the Agent layout for ``root`` without touching the filesystem.

    ``root`` typically comes from :attr:`AgentSettings.root_path`. This only wraps it in an
    :class:`AgentPaths`; provisioning is an explicit, separate step (:meth:`AgentPaths.provision`).
    """
    return AgentPaths(root=Path(root))


def _ensure_directory(directory: Path, mode: int) -> None:
    """Create ``directory`` (with any missing parents) and enforce ``mode`` where the OS allows it.

    ``mkdir`` is masked by the process umask and does not alter an existing directory's mode, so the
    mode is applied explicitly afterwards — which is also what makes a repeated run self-healing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if modes_enforceable():
        os.chmod(directory, mode)
