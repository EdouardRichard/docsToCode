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
