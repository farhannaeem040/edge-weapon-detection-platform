"""Static Python 3.8 syntax-compatibility check (IP-07 T-90, task item 1).

The Bridge's own venv runs system Python 3.8 (FS-05 §4.6) — never the Agent's 3.11. This parses
every Bridge source file with ``ast.parse(..., feature_version=(3, 8))``, which rejects syntax gated
to a newer Python (``match`` statements, 3.10+; certain newer grammar productions) even though the
interpreter actually running this test suite may itself be much newer. This is a syntax-level check
only — it cannot catch a 3.9+-only *standard-library API* being used with 3.8-compatible syntax; that
class of incompatibility is caught by the real Jetson-side venv smoke tests (T-88/T-89), not here.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BRIDGE_APP_ROOT = Path(__file__).resolve().parent.parent / "app" / "deepstream_bridge"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_bridge_source_tree_is_non_empty() -> None:
    assert len(_iter_python_files(_BRIDGE_APP_ROOT)) >= 8


def test_bridge_source_parses_as_python_3_8() -> None:
    offenders: dict[str, str] = {}
    for path in _iter_python_files(_BRIDGE_APP_ROOT):
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text, filename=str(path), feature_version=(3, 8))
        except SyntaxError as exc:
            offenders[str(path.relative_to(_BRIDGE_APP_ROOT.parent.parent))] = str(exc)

    assert offenders == {}, f"Bridge source not Python 3.8-compatible: {offenders}"
