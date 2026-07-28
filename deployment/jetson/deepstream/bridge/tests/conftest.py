"""Test bootstrap: make ``deepstream_bridge`` importable without installing it.

Mirrors ``run.sh``'s own ``PYTHONPATH=$BRIDGE_ROOT/app`` mechanism (FS-05 §4.3) — the package is
never pip-installed, so tests need the same path trick production uses, not a separate one.
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))
