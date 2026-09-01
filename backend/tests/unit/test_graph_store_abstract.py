"""Unit test for GraphStore abstract interface (T009).

Validates the abstract method signatures and isolation contract
(blueprint §8.3, FR-006/FR-007).

This test MUST FAIL before the abstract base is defined (TDD).
"""

from __future__ import annotations

import inspect

import pytest

from rag_mcp.graph.store.base import GraphStore


def test_graph_store_is_abstract():
    """GraphStore must not be directly instantiable (abstract)."""
    with pytest.raises(TypeError):
        GraphStore()


def test_get_neighbors_signature():
    """get_neighbors must accept the documented parameters."""
    sig = inspect.signature(GraphStore.get_neighbors)
    params = set(sig.parameters.keys())
    for required in ("chunk_id", "relation_types", "direction", "hop", "budget", "scope"):
        assert required in params, f"get_neighbors missing param: {required}"


def test_expand_signature():
    """expand must accept the documented parameters."""
    sig = inspect.signature(GraphStore.expand)
    params = set(sig.parameters.keys())
    for required in ("start_chunk_ids", "scope", "hop", "budget", "direction", "relation_types"):
        assert required in params, f"expand missing param: {required}"


def test_isolation_contract():
    """GraphStore must expose isolation scope parameters (FR-010)."""
    sig = inspect.signature(GraphStore.expand)
    # scope param must exist and is the isolation triple
    assert "scope" in sig.parameters
