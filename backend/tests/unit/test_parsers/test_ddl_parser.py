"""Unit tests for the DDL (SQL) statement-aware parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from rag_mcp.parsers.ddl_parser import DDLParser

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"

REQUIRED_KEYS = {
    "content_text",
    "structure_path",
    "start_line",
    "end_line",
    "parent_structure_path",
    "token_count",
    "chunk_type",
}

VALID_CHUNK_TYPES = {
    "table",
    "column",
    "constraint",
    "index",
    "view",
    "procedure",
}


@pytest.fixture
def parser() -> DDLParser:
    """Return a fresh DDLParser instance."""
    return DDLParser()


@pytest.fixture
def sample_sql() -> str:
    """Sample DDL + DML from tests/fixtures/samples/schema.sql."""
    return (FIXTURES / "schema.sql").read_text(encoding="utf-8")


@pytest.fixture
def unsupported_dialect_sql() -> str:
    """SQL with PostgreSQL-specific dialect features."""
    return (FIXTURES / "unsupported_dialect.sql").read_text(encoding="utf-8")


# ------------------------------------------------------------------ #
# 1. Table extraction
# ------------------------------------------------------------------ #


class TestExtractsTables:
    def test_extracts_tables(self, parser, sample_sql):
        """Parser finds the users and orders tables."""
        chunks = parser.parse(sample_sql, "schema.sql")
        tables = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "table"
        }
        assert "table:users" in tables, tables
        assert "table:orders" in tables, tables


# ------------------------------------------------------------------ #
# 2. Column extraction
# ------------------------------------------------------------------ #


class TestExtractsColumns:
    def test_extracts_columns(self, parser, sample_sql):
        """Parser finds columns from both tables."""
        chunks = parser.parse(sample_sql, "schema.sql")
        cols = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "column"
        }
        assert "table:users.column:email" in cols, cols
        assert "table:users.column:username" in cols, cols
        assert "table:orders.column:total_amount" in cols, cols
        assert "table:orders.column:user_id" in cols, cols

    def test_column_count_matches_definitions(self, parser, sample_sql):
        """users has 6 columns, orders has 6 columns."""
        chunks = parser.parse(sample_sql, "schema.sql")
        users_cols = [
            c for c in chunks
            if c["chunk_type"] == "column"
            and c["parent_structure_path"] == "table:users"
        ]
        orders_cols = [
            c for c in chunks
            if c["chunk_type"] == "column"
            and c["parent_structure_path"] == "table:orders"
        ]
        assert len(users_cols) == 6, len(users_cols)
        assert len(orders_cols) == 5, len(orders_cols)


# ------------------------------------------------------------------ #
# 3. Named constraint extraction
# ------------------------------------------------------------------ #


class TestExtractsNamedConstraints:
    def test_extracts_named_constraints(self, parser, sample_sql):
        """Parser finds fk_orders_user and chk_order_amount constraints."""
        chunks = parser.parse(sample_sql, "schema.sql")
        cons = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "constraint"
        }
        assert "constraint:fk_orders_user" in cons, cons
        assert "constraint:chk_order_amount" in cons, cons

    def test_only_named_constraints_produce_chunks(self, parser, sample_sql):
        """Inline column-level PRIMARY KEY is NOT an independent constraint chunk."""
        chunks = parser.parse(sample_sql, "schema.sql")
        # Only the two named table-level constraints should exist.
        cons = [c for c in chunks if c["chunk_type"] == "constraint"]
        assert len(cons) == 2, [c["structure_path"] for c in cons]


# ------------------------------------------------------------------ #
# 4. Index extraction
# ------------------------------------------------------------------ #


class TestExtractsIndexes:
    def test_extracts_indexes(self, parser, sample_sql):
        """Parser finds both CREATE INDEX statements."""
        chunks = parser.parse(sample_sql, "schema.sql")
        idx = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "index"
        }
        assert "index:idx_orders_user_id" in idx, idx
        assert "index:idx_orders_status" in idx, idx


# ------------------------------------------------------------------ #
# 5. View extraction
# ------------------------------------------------------------------ #


class TestExtractsView:
    def test_extracts_view(self, parser, sample_sql):
        """Parser finds the active_orders view."""
        chunks = parser.parse(sample_sql, "schema.sql")
        views = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "view"
        }
        assert "view:active_orders" in views, views


# ------------------------------------------------------------------ #
# 6. Procedure extraction
# ------------------------------------------------------------------ #


class TestExtractsProcedure:
    def test_extracts_procedure(self, parser, sample_sql):
        """Parser finds the calculate_stats procedure."""
        chunks = parser.parse(sample_sql, "schema.sql")
        procs = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "procedure"
        }
        assert "procedure:calculate_stats" in procs, procs


# ------------------------------------------------------------------ #
# 7. structure_path format
# ------------------------------------------------------------------ #


class TestStructurePathFormat:
    def test_table_path_format(self, parser, sample_sql):
        """Table paths are 'table:{name}' with empty parent."""
        chunks = parser.parse(sample_sql, "schema.sql")
        for c in chunks:
            if c["chunk_type"] == "table":
                assert c["structure_path"].startswith("table:"), c["structure_path"]
                assert c["parent_structure_path"] == "", c["structure_path"]

    def test_column_path_format(self, parser, sample_sql):
        """Column paths are 'table:{table}.column:{col}'."""
        chunks = parser.parse(sample_sql, "schema.sql")
        for c in chunks:
            if c["chunk_type"] == "column":
                assert ".column:" in c["structure_path"], c["structure_path"]
                assert c["structure_path"].startswith("table:"), c["structure_path"]

    def test_constraint_path_format(self, parser, sample_sql):
        """Constraint paths are 'constraint:{name}'."""
        chunks = parser.parse(sample_sql, "schema.sql")
        for c in chunks:
            if c["chunk_type"] == "constraint":
                assert c["structure_path"].startswith("constraint:"), c["structure_path"]

    def test_specific_paths(self, parser, sample_sql):
        """Verify exact structure_path strings for known objects."""
        chunks = parser.parse(sample_sql, "schema.sql")
        paths = {c["structure_path"] for c in chunks}
        assert "table:users" in paths
        assert "table:users.column:email" in paths
        assert "constraint:fk_orders_user" in paths
        assert "index:idx_orders_user_id" in paths
        assert "view:active_orders" in paths
        assert "procedure:calculate_stats" in paths


# ------------------------------------------------------------------ #
# 8. DML does not produce chunks
# ------------------------------------------------------------------ #


class TestDMLDoesNotProduceChunks:
    def test_dml_skipped(self, parser, sample_sql):
        """INSERT/UPDATE/DELETE produce no chunks."""
        chunks = parser.parse(sample_sql, "schema.sql")
        assert chunks, "expected DDL chunks from sample"
        for c in chunks:
            assert c["chunk_type"] in VALID_CHUNK_TYPES, c["chunk_type"]
            low = c["content_text"].lower()
            # No chunk should carry DML statement text (unambiguous substrings
            # avoid false-matching the real `updated_at` column).
            assert "insert into" not in low, c["structure_path"]
            assert "delete from" not in low, c["structure_path"]
            assert "update orders set" not in low, c["structure_path"]

    def test_no_chunk_for_dml_lines(self, parser, sample_sql):
        """Lines 38-40 (DML) are covered by no chunk's line range."""
        chunks = parser.parse(sample_sql, "schema.sql")
        for c in chunks:
            # No chunk should start on the INSERT/UPDATE/DELETE lines.
            assert c["start_line"] not in (38, 39, 40), (
                c["structure_path"], c["start_line"]
            )


# ------------------------------------------------------------------ #
# 9. parent / child relationship
# ------------------------------------------------------------------ #


class TestParentChild:
    def test_column_parent_is_table(self, parser, sample_sql):
        """Every column's parent_structure_path is its table path."""
        chunks = parser.parse(sample_sql, "schema.sql")
        cols = [c for c in chunks if c["chunk_type"] == "column"]
        assert cols, "expected column chunks"
        for c in cols:
            parent = c["parent_structure_path"]
            assert parent.startswith("table:"), parent
            assert c["structure_path"] == f"{parent}.column:{_leaf(c['structure_path'])}", (
                c["structure_path"], parent
            )

    def test_constraint_parent_is_table(self, parser, sample_sql):
        """Named constraints reference their containing table as parent."""
        chunks = parser.parse(sample_sql, "schema.sql")
        cons = [c for c in chunks if c["chunk_type"] == "constraint"]
        assert cons, "expected constraint chunks"
        for c in cons:
            assert c["parent_structure_path"] == "table:orders", (
                c["structure_path"], c["parent_structure_path"]
            )


def _leaf(path: str) -> str:
    """Return the last segment after the final '.' or ':'."""
    return path.replace(".", ":").split(":")[-1]


# ------------------------------------------------------------------ #
# 10. Unsupported dialect handling
# ------------------------------------------------------------------ #


class TestUnsupportedDialect:
    def test_recognizable_ddl_still_parsed(self, parser, unsupported_dialect_sql):
        """CREATE TABLE products produces table + column chunks."""
        chunks = parser.parse(unsupported_dialect_sql, "unsupported_dialect.sql")
        tables = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "table"
        }
        assert "table:products" in tables, tables
        cols = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "column"
        }
        assert "table:products.column:metadata" in cols, cols
        assert "table:products.column:name" in cols, cols

    def test_does_not_crash(self, parser, unsupported_dialect_sql):
        """Unsupported dialect features do not raise."""
        chunks = parser.parse(unsupported_dialect_sql, "unsupported_dialect.sql")
        assert isinstance(chunks, list)

    def test_unrecognized_statements_skipped(self, parser, unsupported_dialect_sql):
        """CREATE EXTENSION and MATERIALIZED VIEW produce no chunks."""
        chunks = parser.parse(unsupported_dialect_sql, "unsupported_dialect.sql")
        paths = {c["structure_path"] for c in chunks}
        assert not any("extension" in p for p in paths), paths
        assert not any("materialized" in p.lower() for p in paths), paths
        for c in chunks:
            assert c["chunk_type"] in VALID_CHUNK_TYPES, c["chunk_type"]

    def test_gin_index_recognized(self, parser, unsupported_dialect_sql):
        """GIN index is still a CREATE INDEX -> index chunk."""
        chunks = parser.parse(unsupported_dialect_sql, "unsupported_dialect.sql")
        idx = {
            c["structure_path"]
            for c in chunks
            if c["chunk_type"] == "index"
        }
        assert "index:idx_products_metadata" in idx, idx


# ------------------------------------------------------------------ #
# 11. Required fields & line numbers
# ------------------------------------------------------------------ #


class TestRequiredFields:
    def test_all_chunks_have_required_fields(self, parser, sample_sql):
        chunks = parser.parse(sample_sql, "schema.sql")
        assert chunks, "expected at least one chunk"
        for c in chunks:
            assert REQUIRED_KEYS.issubset(c.keys()), (
                REQUIRED_KEYS - set(c.keys())
            )

    def test_line_numbers_valid(self, parser, sample_sql):
        chunks = parser.parse(sample_sql, "schema.sql")
        n = len(sample_sql.splitlines())
        for c in chunks:
            assert 1 <= c["start_line"], c["structure_path"]
            assert c["start_line"] <= c["end_line"], c["structure_path"]
            assert c["end_line"] <= n, (c["structure_path"], c["end_line"], n)

    def test_token_count_positive(self, parser, sample_sql):
        chunks = parser.parse(sample_sql, "schema.sql")
        for c in chunks:
            assert c["token_count"] >= 1, c["structure_path"]


# ------------------------------------------------------------------ #
# 12. Empty input
# ------------------------------------------------------------------ #


class TestEmptyInput:
    def test_empty_string(self, parser):
        assert parser.parse("") == []

    def test_whitespace_only(self, parser):
        assert parser.parse("   ") == []
        assert parser.parse("\n\n\n") == []

    def test_comment_only(self, parser):
        assert parser.parse("-- just a comment\n") == []
