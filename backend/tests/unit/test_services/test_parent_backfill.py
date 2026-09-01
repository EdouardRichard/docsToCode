"""Unit tests for parent-child chunk backfill (T058 / FR-007 / US-3).

Verifies that ``backfill_parent_chunk_ids`` resolves each chunk's explicit
parent reference (``parent_section_path`` for Markdown, ``parent_symbol_path``
for Java) into a concrete ``parent_chunk_id``, so that ``get_evidence`` can
return parent context for hierarchical chunks.
"""

from __future__ import annotations

from rag_mcp.services.ingestion_service import backfill_parent_chunk_ids


def _md_chunk(chunk_id: int, section_path: str, parent_section_path: str) -> dict:
    """Build a Markdown parser-shaped chunk dict."""
    return {
        "chunk_id": chunk_id,
        "content_text": f"content for {chunk_id}",
        "section_path": section_path,
        "start_line": 1,
        "end_line": 2,
        "parent_section_path": parent_section_path,
        "token_count": 10,
        "chunk_type": "section",
    }


def _java_chunk(
    chunk_id: int, symbol_path: str, parent_symbol_path: str, symbol_type: str
) -> dict:
    """Build a Java parser-shaped chunk dict."""
    return {
        "chunk_id": chunk_id,
        "content_text": f"content for {chunk_id}",
        "symbol_path": symbol_path,
        "symbol_type": symbol_type,
        "start_line": 1,
        "end_line": 2,
        "parent_symbol_path": parent_symbol_path,
        "token_count": 10,
        "chunk_type": "symbol",
    }


class TestBackfillParentChunkIds:
    def test_markdown_child_backfills_parent_chunk_id(self):
        """A child section's parent_section_path resolves to the parent chunk id."""
        parent = _md_chunk(100, "# 项目概述", "")
        child = _md_chunk(101, "# 项目概述 > ## 安装指南", "# 项目概述")
        grandchild = _md_chunk(
            102, "# 项目概述 > ## 安装指南 > ### 环境要求", "# 项目概述 > ## 安装指南"
        )
        chunks = [parent, child, grandchild]

        backfill_parent_chunk_ids(chunks)

        assert child["parent_chunk_id"] == 100
        assert grandchild["parent_chunk_id"] == 101
        assert "parent_chunk_id" not in parent

    def test_java_method_backfills_class_parent(self):
        """A method's parent_symbol_path resolves to the enclosing class chunk id."""
        klass = _java_chunk(200, "com.example.UserService", "", "class")
        method = _java_chunk(
            201, "com.example.UserService#findById", "com.example.UserService", "method"
        )
        field = _java_chunk(
            202, "com.example.UserService#repository", "com.example.UserService", "field"
        )
        chunks = [klass, method, field]

        backfill_parent_chunk_ids(chunks)

        assert method["parent_chunk_id"] == 200
        assert field["parent_chunk_id"] == 200
        assert "parent_chunk_id" not in klass

    def test_top_level_chunk_gets_no_parent(self):
        """A top-level chunk with empty parent reference gets no parent_chunk_id."""
        chunk = _md_chunk(300, "# 顶层", "")
        chunks = [chunk]

        backfill_parent_chunk_ids(chunks)

        assert "parent_chunk_id" not in chunk

    def test_unresolvable_parent_is_ignored(self):
        """A parent reference that matches no chunk is silently ignored."""
        child = _md_chunk(400, "# A > ## B", "# MissingParent")
        chunks = [child]

        backfill_parent_chunk_ids(chunks)

        assert "parent_chunk_id" not in child

    def test_no_self_reference(self):
        """A chunk whose parent path equals its own path is not self-linked."""
        chunk = _md_chunk(500, "# X", "# X")
        chunks = [chunk]

        backfill_parent_chunk_ids(chunks)

        assert "parent_chunk_id" not in chunk

    def test_does_not_mutate_missing_parent_key(self):
        """Chunks without any parent key are left untouched."""
        chunk = _md_chunk(600, "# Y", "")
        del chunk["parent_section_path"]
        chunks = [chunk]

        backfill_parent_chunk_ids(chunks)

        assert "parent_chunk_id" not in chunk


def _structure_chunk(chunk_id, structure_path, parent_structure_path, chunk_type):
    """Build an OpenAPI/DDL parser-shaped chunk dict with structure_path."""
    return {
        "chunk_id": chunk_id,
        "content_text": f"content for {chunk_id}",
        "structure_path": structure_path,
        "start_line": 1,
        "end_line": 2,
        "parent_structure_path": parent_structure_path,
        "token_count": 10,
        "chunk_type": chunk_type,
    }


class TestBackfillStructurePath:
    """T004: Verify backfill supports structure_path key (OpenAPI/DDL)."""

    def test_openapi_endpoint_backfills_schema_parent(self):
        """An endpoint's parent_structure_path resolves to the referenced Schema."""
        schema = _structure_chunk(300, "schema:components.schemas.User", "", "schema")
        endpoint = _structure_chunk(
            301, "GET /api/v1/users", "schema:components.schemas.User", "endpoint"
        )
        chunks = [schema, endpoint]

        backfill_parent_chunk_ids(chunks)

        assert endpoint["parent_chunk_id"] == 300
        assert "parent_chunk_id" not in schema

    def test_ddl_column_backfills_table_parent(self):
        """A column's parent_structure_path resolves to the table chunk id."""
        table = _structure_chunk(400, "table:users", "", "table")
        column = _structure_chunk(
            401, "table:users.column:email", "table:users", "column"
        )
        chunks = [table, column]

        backfill_parent_chunk_ids(chunks)

        assert column["parent_chunk_id"] == 400
        assert "parent_chunk_id" not in table

    def test_ddl_constraint_backfills_table_parent(self):
        """A named constraint's parent_structure_path resolves to the table."""
        table = _structure_chunk(500, "table:orders", "", "table")
        constraint = _structure_chunk(
            501, "constraint:fk_orders_user", "table:orders", "constraint"
        )
        chunks = [table, constraint]

        backfill_parent_chunk_ids(chunks)

        assert constraint["parent_chunk_id"] == 500

    def test_mixed_keys_in_same_batch(self):
        """Mixed section_path, symbol_path, structure_path chunks work together."""
        md_parent = _md_chunk(600, "# Docs", "")
        md_child = _md_chunk(601, "# Docs > ## Section", "# Docs")
        java_klass = _java_chunk(602, "com.example.Svc", "", "class")
        java_method = _java_chunk(603, "com.example.Svc#doWork", "com.example.Svc", "method")
        ddl_table = _structure_chunk(604, "table:users", "", "table")
        ddl_col = _structure_chunk(605, "table:users.col:id", "table:users", "column")
        chunks = [md_parent, md_child, java_klass, java_method, ddl_table, ddl_col]

        backfill_parent_chunk_ids(chunks)

        assert md_child["parent_chunk_id"] == 600
        assert java_method["parent_chunk_id"] == 602
        assert ddl_col["parent_chunk_id"] == 604
        assert "parent_chunk_id" not in md_parent
        assert "parent_chunk_id" not in java_klass
        assert "parent_chunk_id" not in ddl_table

