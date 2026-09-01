"""Unit test verifying no new runtime deps beyond 001/002/003 (T003).

004 must reuse existing dependencies (psycopg/SQLAlchemy/tree-sitter/qdrant)
and not introduce new runtime dependencies. This test reads pyproject.toml
and asserts the dependency set matches the recorded baseline.

This test MUST FAIL before the baseline snapshot is recorded (TDD).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYPROJECT = _REPO_ROOT / "backend" / "pyproject.toml"
_BASELINE = _REPO_ROOT / "backend" / "tests" / "fixtures" / "deps_baseline.txt"


def _read_runtime_deps() -> set[str]:
    """Read the runtime dependency names from pyproject.toml."""
    with open(_PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    deps: set[str] = set()
    for dep in data["project"].get("dependencies", []):
        # Extract package name (strip version specifiers and extras)
        name = dep.split(">")[0].split("<")[0].split("=")[0].split("!")[0].split("[")[0].strip().lower()
        deps.add(name)
    return deps


def test_deps_match_baseline():
    """Runtime dependency set must match the recorded baseline (no new deps)."""
    assert _BASELINE.exists(), (
        f"Baseline snapshot not found at {_BASELINE}. "
        "Run the Green step to record it."
    )
    with open(_BASELINE, "r", encoding="utf-8") as f:
        baseline = {line.strip().lower() for line in f if line.strip()}

    current = _read_runtime_deps()
    new_deps = current - baseline
    assert not new_deps, (
        f"New runtime dependencies introduced (forbidden by T003/AC): {sorted(new_deps)}"
    )


def test_no_graph_specific_deps():
    """No graph-database-specific dependencies (neo4j, networkx, etc.)."""
    forbidden = {"neo4j", "networkx", "igraph", "graph-tool", "python-igraph"}
    current = _read_runtime_deps()
    found = current & forbidden
    assert not found, f"Forbidden graph-database deps found: {sorted(found)}"
