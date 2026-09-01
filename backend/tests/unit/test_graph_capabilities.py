"""Unit tests for graph_ready capability gating (T012).

Validates the capability gating logic that controls whether a knowledge
version may participate in graph expansion:

  * FR-015: graph_ready=true MUST imply dense_ready=true AND lexical_ready=true.
  * FR-013: declaring graph_ready before graph relations are ready prevents
            publish (here: prevents entering graph expansion).
  * FR-014: versions not declaring graph_ready MUST NOT participate in graph
            expansion but continue hybrid retrieval.

Contract: specs/003-structured-asset-expansion/contracts/
          knowledge-capabilities.graph-extension.schema.json

This test MUST FAIL before capabilities.py is implemented (TDD Red).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rag_mcp.graph.capabilities import (
    GraphCapabilities,
    can_enter_graph_expansion,
    is_graph_ready_version,
    validate_capabilities,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _version(*, graph_ready: bool, dense_ready: bool, lexical_ready: bool,
             extra: dict | None = None) -> SimpleNamespace:
    """Build a lightweight version-like object mirroring KnowledgeVersion.

    The capabilities JSONB and the graph_ready boolean column are the only
    attributes the gating logic depends on, so a SimpleNamespace is enough
    for a unit test (no DB / ORM session required).
    """
    capabilities: dict = {"dense_ready": dense_ready, "lexical_ready": lexical_ready}
    if extra:
        capabilities.update(extra)
    return SimpleNamespace(graph_ready=graph_ready, capabilities=capabilities)


# ---------------------------------------------------------------------------
# validate_capabilities
# ---------------------------------------------------------------------------

class TestValidateCapabilities:
    """FR-015: graph_ready=true implies dense_ready + lexical_ready."""

    def test_graph_ready_without_dense_rejected(self):
        """graph_ready=true but dense_ready=false MUST raise ValueError."""
        caps = {"dense_ready": False, "lexical_ready": True, "graph_ready": True}
        with pytest.raises(ValueError):
            validate_capabilities(caps)

    def test_graph_ready_without_lexical_rejected(self):
        """graph_ready=true but lexical_ready=false MUST raise ValueError."""
        caps = {"dense_ready": True, "lexical_ready": False, "graph_ready": True}
        with pytest.raises(ValueError):
            validate_capabilities(caps)

    def test_graph_ready_with_dense_and_lexical_passes(self):
        """graph_ready=true with dense+lexical ready MUST pass (no raise)."""
        caps = {"dense_ready": True, "lexical_ready": True, "graph_ready": True}
        # Should not raise.
        validate_capabilities(caps)

    def test_graph_ready_false_passes_without_dense(self):
        """graph_ready absent/false does not impose the dense+lexical gate."""
        validate_capabilities({"dense_ready": False, "graph_ready": False})

    def test_graph_ready_key_absent_passes(self):
        """A capabilities dict that does not declare graph_ready passes."""
        validate_capabilities({"dense_ready": True, "lexical_ready": True})


# ---------------------------------------------------------------------------
# is_graph_ready_version
# ---------------------------------------------------------------------------

class TestIsGraphReadyVersion:
    """graph_ready column true AND dense+lexical ready in capabilities JSONB."""

    def test_returns_true_when_column_and_capabilities_ready(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert is_graph_ready_version(version) is True

    def test_returns_false_when_graph_ready_column_false(self):
        """graph_ready column false -> MUST NOT be graph ready (FR-014)."""
        version = _version(graph_ready=False, dense_ready=True, lexical_ready=True)
        assert is_graph_ready_version(version) is False

    def test_returns_false_when_column_true_but_dense_missing(self):
        version = _version(graph_ready=True, dense_ready=False, lexical_ready=True)
        assert is_graph_ready_version(version) is False

    def test_returns_false_when_column_true_but_lexical_missing(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=False)
        assert is_graph_ready_version(version) is False


# ---------------------------------------------------------------------------
# can_enter_graph_expansion
# ---------------------------------------------------------------------------

class TestCanEnterGraphExpansion:
    """FR-013/FR-014: graph expansion requires graph_ready AND graph edges."""

    def test_true_when_graph_ready_and_has_edges(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert can_enter_graph_expansion(version, has_graph_edges=True) is True

    def test_false_when_graph_ready_but_no_edges(self):
        """graph_ready declared but relations not ready -> cannot expand (FR-013)."""
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert can_enter_graph_expansion(version, has_graph_edges=False) is False

    def test_false_when_not_graph_ready_even_with_edges(self):
        """Non-graph version MUST NOT enter graph expansion (FR-014)."""
        version = _version(graph_ready=False, dense_ready=True, lexical_ready=True)
        assert can_enter_graph_expansion(version, has_graph_edges=True) is False

    def test_truthy_edges_flag_is_accepted(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert can_enter_graph_expansion(version, has_graph_edges=1) is True


# ---------------------------------------------------------------------------
# GraphCapabilities facade (class-based API mirrors module functions)
# ---------------------------------------------------------------------------

class TestGraphCapabilitiesFacade:
    """The class facade must mirror the module-level functions."""

    def test_facade_validate_matches_module(self):
        caps = {"dense_ready": False, "lexical_ready": True, "graph_ready": True}
        with pytest.raises(ValueError):
            GraphCapabilities.validate_capabilities(caps)

    def test_facade_is_graph_ready_matches_module(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert GraphCapabilities.is_graph_ready_version(version) is True

    def test_facade_can_enter_matches_module(self):
        version = _version(graph_ready=True, dense_ready=True, lexical_ready=True)
        assert GraphCapabilities.can_enter_graph_expansion(
            version, has_graph_edges=True) is True
