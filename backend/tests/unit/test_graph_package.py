"""Unit test for graph package skeleton (T001).

Asserts that the graph sub-package and its store/extractors sub-packages
can be imported, consistent with the existing rag_mcp package structure.

This test MUST FAIL before the package skeleton is created (TDD).
"""

from __future__ import annotations

import importlib


def test_import_graph_package():
    """rag_mcp.graph must be importable."""
    mod = importlib.import_module("rag_mcp.graph")
    assert mod is not None


def test_import_graph_store_subpackage():
    """rag_mcp.graph.store must be importable."""
    mod = importlib.import_module("rag_mcp.graph.store")
    assert mod is not None


def test_import_graph_extractors_subpackage():
    """rag_mcp.graph.extractors must be importable."""
    mod = importlib.import_module("rag_mcp.graph.extractors")
    assert mod is not None


def test_graph_package_has_expected_modules():
    """The graph package should expose the planned module names as importable."""
    for mod_name in (
        "rag_mcp.graph.models",
        "rag_mcp.graph.store.base",
        "rag_mcp.graph.store.postgres_graph_store",
        "rag_mcp.graph.expansion",
        "rag_mcp.graph.capabilities",
        "rag_mcp.graph.extractors.java_call_graph",
        "rag_mcp.graph.extractors.ddl_fk",
        "rag_mcp.graph.soft_relation_inference",
        "rag_mcp.graph.trace_recorder",
    ):
        importlib.import_module(mod_name)
