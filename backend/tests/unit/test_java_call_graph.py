"""Unit test for Java call-graph extractor (T019).

Validates deterministic AST extraction of calls/called_by edges from
Java source, with parse_evidence containing AST locators (blueprint sec 10.1,
research sec 7, FR-001/FR-002).

This test MUST FAIL before the extractor is implemented (TDD).
"""

from __future__ import annotations

import pytest

from rag_mcp.graph.extractors.java_call_graph import JavaCallGraphExtractor
from rag_mcp.graph.store.base import GraphScope


_SOURCE = """package com.example;

public class Calculator {
    public int compute(int x) {
        return square(add(x, 1));
    }
    public int add(int a, int b) {
        return a + b;
    }
    public int square(int x) {
        return x * x;
    }
}
"""


def _make_chunks():
    """Create chunk dicts with chunk_ids matching the Calculator methods."""
    return [
        {"chunk_id": 1001, "symbol_path": "com.example.Calculator",
         "symbol_type": "class", "content_text": _SOURCE,
         "start_line": 3, "end_line": 14},
        {"chunk_id": 1002, "symbol_path": "com.example.Calculator#compute",
         "symbol_type": "method", "content_text": "compute body",
         "start_line": 4, "end_line": 6},
        {"chunk_id": 1003, "symbol_path": "com.example.Calculator#add",
         "symbol_type": "method", "content_text": "add body",
         "start_line": 7, "end_line": 9},
        {"chunk_id": 1004, "symbol_path": "com.example.Calculator#square",
         "symbol_type": "method", "content_text": "square body",
         "start_line": 10, "end_line": 12},
    ]


class TestExtraction:
    def test_extracts_calls_edges(self):
        """compute calls add and square."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_SOURCE, _make_chunks(), scope)
        calls_edges = [e for e in edges if e["relation_type"] == "calls"]
        # compute calls add and square
        calls_targets = {(e["source_chunk_id"], e["target_chunk_id"]) for e in calls_edges}
        assert (1002, 1003) in calls_targets, "compute should call add"
        assert (1002, 1004) in calls_targets, "compute should call square"

    def test_extracts_called_by_edges(self):
        """called_by edges are the reverse of calls."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_SOURCE, _make_chunks(), scope)
        cb_edges = [e for e in edges if e["relation_type"] == "called_by"]
        cb_pairs = {(e["source_chunk_id"], e["target_chunk_id"]) for e in cb_edges}
        assert (1003, 1002) in cb_pairs, "add is called_by compute"
        assert (1004, 1002) in cb_pairs, "square is called_by compute"

    def test_parse_evidence_present(self):
        """Each edge MUST have parse_evidence with AST locator."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_SOURCE, _make_chunks(), scope)
        for e in edges:
            assert "parse_evidence" in e
            pe = e["parse_evidence"]
            assert pe["source_format"] == "java"
            assert pe["extractor"] == "java_call_graph"
            assert "locator" in pe

    def test_all_edges_hard(self):
        """All extracted edges MUST be is_hard=true."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_SOURCE, _make_chunks(), scope)
        for e in edges:
            assert e["is_hard"] is True

    def test_isolation_fields_present(self):
        """Edges MUST carry the isolation triple."""
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_SOURCE, _make_chunks(), scope)
        for e in edges:
            assert e["knowledge_scope_id"] == 100
            assert e["project_id"] == 200
            assert e["index_version"] == 1

    def test_no_self_edges(self):
        """A method calling itself should not create a self-edge."""
        src = """package com.example;
public class Recursive {
    public int factorial(int n) {
        if (n <= 1) return 1;
        return n * factorial(n - 1);
    }
}
"""
        chunks = [
            {"chunk_id": 2001, "symbol_path": "com.example.Recursive",
             "symbol_type": "class", "content_text": src, "start_line": 2, "end_line": 8},
            {"chunk_id": 2002, "symbol_path": "com.example.Recursive#factorial",
             "symbol_type": "method", "content_text": "factorial body",
             "start_line": 3, "end_line": 7},
        ]
        extractor = JavaCallGraphExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(src, chunks, scope)
        for e in edges:
            assert e["source_chunk_id"] != e["target_chunk_id"], "No self-edges"
