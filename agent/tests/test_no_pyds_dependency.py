"""Static dependency-boundary checks (IP-07 T-90, FS-05 §4.6 requirement 5, task item 1).

Mechanically enforces the binding rule that the Agent's own Python 3.11 process must never import
``pyds``, ``pgi``, or any GStreamer/DeepStream Python binding (``gi``, ``Gst``, ``GstRtspServer``) —
those live exclusively in the separate DeepStream Bridge application and its own Python 3.8 venv
(FS-05 §4.6). These checks inspect the *complete* Agent source tree (``agent/src/``), not just
``main.py`` — a single forgotten import anywhere in the tree would violate the boundary.

The companion check (this module's ``test_bridge_never_imports_agent_source``) proves the converse:
the Bridge source tree never imports ``weapon_detection_agent``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_AGENT_SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "weapon_detection_agent"
_AGENT_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"
_BRIDGE_APP_ROOT = (
    Path(__file__).resolve().parent.parent.parent
    / "deployment"
    / "jetson"
    / "deepstream"
    / "bridge"
    / "app"
    / "deepstream_bridge"
)

# The exact set of tokens the Agent must never import — matching FS-05 §4.6 requirement 5's binding
# list verbatim (pyds, pgi, and every GStreamer Python binding name the Bridge itself uses).
_FORBIDDEN_BRIDGE_TOKENS = ("pyds", "pgi", "gi", "Gst", "GstRtspServer")


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _top_level_imported_names(tree: ast.Module) -> set[str]:
    """Every top-level module name a file imports, via ``import x`` or ``from x import y``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_agent_source_tree_is_non_empty() -> None:
    """Guards every other assertion in this module against a silently-empty file list (e.g. a
    moved/renamed directory) making them vacuously true."""
    assert len(_iter_python_files(_AGENT_SRC_ROOT)) > 20


@pytest.mark.parametrize("token", _FORBIDDEN_BRIDGE_TOKENS)
def test_agent_source_never_imports_bridge_dependency(token: str) -> None:
    """No file under ``agent/src/`` imports ``token`` — checked via the AST (a real import
    statement), not a substring search, so a comment or docstring *mentioning* e.g. ``pyds`` (this
    codebase has several, documenting exactly this boundary) never produces a false failure."""
    offenders: list[str] = []
    for path in _iter_python_files(_AGENT_SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if token in _top_level_imported_names(tree):
            offenders.append(str(path.relative_to(_AGENT_SRC_ROOT.parent.parent)))

    assert offenders == [], f"agent/src/ imports forbidden token {token!r} in: {offenders}"


def test_agent_source_never_dynamically_references_pyds() -> None:
    """A narrower literal-text backstop than a blanket ``pyds`` search (which would also flag this
    codebase's own legitimate boundary-documentation comments/docstrings — several already exist,
    intentionally, explaining exactly this rule). Catches only a *dynamic* reference that the
    AST-import check above cannot see: ``importlib.import_module("pyds")``, ``getattr(x, "pyds")``,
    or similar string-based access."""
    offenders: list[str] = []
    pattern = re.compile(r'["\']pyds["\']')
    for path in _iter_python_files(_AGENT_SRC_ROOT):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(_AGENT_SRC_ROOT.parent.parent)}:{line_number}")

    assert offenders == [], f"dynamic reference to 'pyds' found in agent/src/ at: {offenders}"


def test_agent_pyproject_declares_no_deepstream_python_binding() -> None:
    """The Agent's dependency manifest itself names none of the Bridge's dependencies (pyds/pgi) —
    a static guard against the Agent's *packaging* ever growing this dependency, independent of
    whether any code actually imports it."""
    text = _AGENT_PYPROJECT.read_text(encoding="utf-8")
    for token in ("pyds", "pgi", "PyGObject"):
        assert token not in text, f"agent/pyproject.toml unexpectedly names {token!r}"


def test_bridge_never_imports_agent_source() -> None:
    """The converse boundary (FS-05 §4.6): no file under the Bridge's own source tree imports
    ``weapon_detection_agent`` — the two applications share no runtime dependency in either
    direction, only the documented, independently-duplicated wire protocol."""
    offenders: list[str] = []
    for path in _iter_python_files(_BRIDGE_APP_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if "weapon_detection_agent" in _top_level_imported_names(tree):
            offenders.append(str(path.relative_to(_BRIDGE_APP_ROOT.parent.parent)))

    assert offenders == [], f"Bridge source imports weapon_detection_agent in: {offenders}"


def test_agent_declares_python_311_compatible_baseline() -> None:
    """The Agent's own ``requires-python`` floor stays at least 3.10 (its documented Jetson-floor
    baseline, IP-02 T-31) — a regression here would silently invalidate every "Agent runs on Python
    3.11, never the Bridge's 3.8" assumption this boundary depends on."""
    text = _AGENT_PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*">=\s*3\.(\d+)"', text)
    assert match is not None, "could not find requires-python in agent/pyproject.toml"
    assert int(match.group(1)) >= 10


def test_agent_source_parses_as_valid_python_ast() -> None:
    """Every Agent source file parses under the standard library's own parser (implicitly the
    interpreter's own grammar) — a cheap, real proof the tree is at least syntactically compatible
    with whatever Python version is running the test, standing in for "3.11 compatible" without
    requiring a second interpreter to be installed in CI."""
    for path in _iter_python_files(_AGENT_SRC_ROOT):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
