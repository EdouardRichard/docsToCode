"""Unit test for DDL foreign-key extractor (T022).

Validates deterministic extraction of fk_references/fk_referenced_by edges
from DDL SQL with FOREIGN KEY constraints (blueprint §10.1, research sec 7,
FR-001/FR-002).

This test MUST FAIL before the extractor is implemented (TDD).
"""

from __future__ import annotations

import pytest

from rag_mcp.graph.extractors.ddl_fk import DdlFkExtractor
from rag_mcp.graph.store.base import GraphScope


# Fixture DDL: orders references users via an unnamed table-level FOREIGN KEY.
_DDL = """CREATE TABLE users (
    id INT PRIMARY KEY,
    email VARCHAR(255)
);

CREATE TABLE orders (
    id INT PRIMARY KEY,
    user_id INT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""


def _make_chunks():
    """Create chunk dicts with chunk_ids matching the users/orders tables.

    Mirrors the DDL parser output shape but uses the symbol_path/symbol_type
    naming convention (table:<name>) so the extractor can resolve chunk ids.
    """
    return [
        {"chunk_id": 5001, "symbol_path": "table:users",
         "symbol_type": "table", "content_text": "CREATE TABLE users (...)",
         "start_line": 1, "end_line": 4},
        {"chunk_id": 5002, "symbol_path": "table:users.column:id",
         "symbol_type": "column", "content_text": "id INT PRIMARY KEY",
         "start_line": 2, "end_line": 2},
        {"chunk_id": 5003, "symbol_path": "table:users.column:email",
         "symbol_type": "column", "content_text": "email VARCHAR(255)",
         "start_line": 3, "end_line": 3},
        {"chunk_id": 5004, "symbol_path": "table:orders",
         "symbol_type": "table", "content_text": "CREATE TABLE orders (...)",
         "start_line": 6, "end_line": 11},
        {"chunk_id": 5005, "symbol_path": "table:orders.column:id",
         "symbol_type": "column", "content_text": "id INT PRIMARY KEY",
         "start_line": 7, "end_line": 7},
        {"chunk_id": 5006, "symbol_path": "table:orders.column:user_id",
         "symbol_type": "column", "content_text": "user_id INT",
         "start_line": 8, "end_line": 8},
    ]


class TestExtraction:
    def test_extracts_fk_references_edges(self):
        """orders references users via FK -> fk_references edge orders->users."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_DDL, _make_chunks(), scope)
        fk_ref = [e for e in edges if e["relation_type"] == "fk_references"]
        pairs = {(e["source_chunk_id"], e["target_chunk_id"]) for e in fk_ref}
        assert (5004, 5001) in pairs, "orders should fk_reference users"

    def test_extracts_fk_referenced_by_edges(self):
        """fk_referenced_by is the reverse: users is referenced_by orders."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_DDL, _make_chunks(), scope)
        fk_rby = [e for e in edges if e["relation_type"] == "fk_referenced_by"]
        pairs = {(e["source_chunk_id"], e["target_chunk_id"]) for e in fk_rby}
        assert (5001, 5004) in pairs, "users is fk_referenced_by orders"

    def test_parse_evidence_present(self):
        """Each edge MUST have parse_evidence with ddl locator (table:X.fk:Y)."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_DDL, _make_chunks(), scope)
        assert edges, "expected at least one edge"
        for e in edges:
            assert "parse_evidence" in e
            pe = e["parse_evidence"]
            assert pe["source_format"] == "ddl"
            assert pe["extractor"] == "ddl_fk"
            assert "locator" in pe
            assert pe["locator"].startswith("table:"), pe["locator"]
            assert ".fk:" in pe["locator"], pe["locator"]
        # the fk_references edge orders->users carries the canonical locator
        fk_ref = next(e for e in edges if e["relation_type"] == "fk_references")
        assert fk_ref["parse_evidence"]["locator"] == "table:orders.fk:users"

    def test_all_edges_hard(self):
        """All extracted edges MUST be is_hard=true."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_DDL, _make_chunks(), scope)
        assert edges, "expected at least one edge"
        for e in edges:
            assert e["is_hard"] is True

    def test_isolation_fields_present(self):
        """Edges MUST carry the isolation triple."""
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(_DDL, _make_chunks(), scope)
        assert edges, "expected at least one edge"
        for e in edges:
            assert e["knowledge_scope_id"] == 100
            assert e["project_id"] == 200
            assert e["index_version"] == 1

    def test_no_foreign_keys_produces_no_edges(self):
        """DDL without FOREIGN KEY constraints produces no edges (Edge Case)."""
        ddl = """CREATE TABLE users (
    id INT PRIMARY KEY,
    name VARCHAR(100)
);
"""
        chunks = [
            {"chunk_id": 6001, "symbol_path": "table:users",
             "symbol_type": "table", "content_text": ddl,
             "start_line": 1, "end_line": 5},
        ]
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(ddl, chunks, scope)
        assert edges == []

    def test_no_self_edges(self):
        """A table referencing itself should not create a self-edge."""
        ddl = """CREATE TABLE employees (
    id INT PRIMARY KEY,
    manager_id INT,
    FOREIGN KEY (manager_id) REFERENCES employees(id)
);
"""
        chunks = [
            {"chunk_id": 7001, "symbol_path": "table:employees",
             "symbol_type": "table", "content_text": ddl,
             "start_line": 1, "end_line": 6},
        ]
        extractor = DdlFkExtractor()
        scope = GraphScope(100, 200, 1)
        edges = extractor.extract(ddl, chunks, scope)
        for e in edges:
            assert e["source_chunk_id"] != e["target_chunk_id"], "No self-edges"
        # a self-referencing FK yields no determinable cross-table edge
        assert edges == []
