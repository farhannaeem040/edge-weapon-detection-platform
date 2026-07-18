"""Jetson Agent control plane for the Edge-Based Weapon Detection platform.

This package is the FastAPI control plane that runs on the NVIDIA Jetson Orin Nano (ARCH-001 §9,
ADR-001). IP-02 T-31 establishes only the project scaffold: package metadata, tooling, and a
minimal importable application. The Agent's actual behaviour — settings, filesystem/SQLite
persistence, Backend activation, and the startup workflow — is added by the later IP-02 tasks
(T-32–T-41) and is deliberately absent here.

Importing this package performs no I/O, no network access, and no platform-specific work, so it
imports identically on a development workstation (Windows or Linux) and on Jetson Linux.
"""

# Kept in sync with [project].version in pyproject.toml. The application exposes this as its
# version metadata (see weapon_detection_agent.main).
__version__ = "0.1.0"
