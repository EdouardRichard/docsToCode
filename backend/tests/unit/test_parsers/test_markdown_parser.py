"""Unit tests for MarkdownParser — section-aware Markdown chunking."""

from __future__ import annotations

import pytest

from rag_mcp.parsers.markdown_parser import MarkdownParser


@pytest.fixture
def parser() -> MarkdownParser:
    """Create a MarkdownParser instance."""
    return MarkdownParser()


class TestMarkdownParser:
    """Tests for MarkdownParser.parse()."""

    def test_parses_heading_hierarchy(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """Verify section_path reflects heading levels correctly."""
        chunks = parser.parse(sample_markdown)

        # Collect all unique section paths and all content text
        section_paths = [c["section_path"] for c in chunks]
        all_content = " ".join(c["content_text"] for c in chunks)

        # Should have top-level heading as a chunk
        assert any("# 项目概述" in p for p in section_paths), (
            f"Expected top-level '# 项目概述' in section paths: {section_paths}"
        )

        # ## 安装指南 appears in child section paths (it has children with content)
        assert any("## 安装指南" in p for p in section_paths), (
            f"Expected '## 安装指南' in section paths: {section_paths}"
        )

        # ## API 配置 is small (< 64 tokens) so it merges into parent;
        # verify its content still appears in some chunk
        assert "API" in all_content, (
            "Expected 'API' content to appear in at least one chunk"
        )

        # Should have third-level headings nested under ## 安装指南
        assert any("### 环境要求" in p for p in section_paths), (
            f"Expected '### 环境要求' in section paths: {section_paths}"
        )
        assert any("### 安装步骤" in p for p in section_paths), (
            f"Expected '### 安装步骤' in section paths: {section_paths}"
        )

    def test_produces_chunks_with_required_fields(
        self, parser: MarkdownParser, sample_markdown: str
    ) -> None:
        """Every chunk must have content_text, section_path, start_line, end_line, chunk_type."""
        chunks = parser.parse(sample_markdown)
        assert len(chunks) > 0, "Expected at least one chunk from sample markdown"

        required_keys = {"content_text", "section_path", "start_line", "end_line", "chunk_type"}

        for i, chunk in enumerate(chunks):
            missing = required_keys - set(chunk.keys())
            assert not missing, f"Chunk {i} missing keys: {missing}"

            # Validate types
            assert isinstance(chunk["content_text"], str), f"Chunk {i}: content_text must be str"
            assert isinstance(chunk["section_path"], str), f"Chunk {i}: section_path must be str"
            assert isinstance(chunk["start_line"], int), f"Chunk {i}: start_line must be int"
            assert isinstance(chunk["end_line"], int), f"Chunk {i}: end_line must be int"
            assert isinstance(chunk["chunk_type"], str), f"Chunk {i}: chunk_type must be str"

            # Validate non-empty content
            assert chunk["content_text"].strip(), f"Chunk {i}: content_text should not be empty"

            # Validate line numbers are positive
            assert chunk["start_line"] >= 1, f"Chunk {i}: start_line must be >= 1"
            assert chunk["end_line"] >= chunk["start_line"], (
                f"Chunk {i}: end_line ({chunk['end_line']}) must be >= start_line ({chunk['start_line']})"
            )

    def test_section_path_format(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """Paths use ' > ' separator between heading levels."""
        chunks = parser.parse(sample_markdown)

        # Find chunks with nested section paths (containing ' > ')
        nested_paths = [c["section_path"] for c in chunks if " > " in c["section_path"]]

        # The sample has ### headings under ## headings, so we should see nested paths
        assert len(nested_paths) > 0, (
            f"Expected at least one nested section path with ' > ' separator. "
            f"All paths: {[c['section_path'] for c in chunks]}"
        )

        # Verify format: each part should start with # prefix
        for path in nested_paths:
            parts = path.split(" > ")
            assert len(parts) >= 2, f"Nested path should have >= 2 parts: {path}"
            for part in parts:
                assert part.startswith("#"), (
                    f"Each part of section path should start with '#': '{part}' in '{path}'"
                )

    def test_chunk_type_is_section(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """All chunks have chunk_type == 'section'."""
        chunks = parser.parse(sample_markdown)
        assert len(chunks) > 0

        for i, chunk in enumerate(chunks):
            assert chunk["chunk_type"] == "section", (
                f"Chunk {i}: expected chunk_type='section', got '{chunk['chunk_type']}'"
            )

    def test_line_numbers_are_accurate(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """start_line/end_line match actual content position in source."""
        chunks = parser.parse(sample_markdown)
        lines = sample_markdown.split("\n")

        for i, chunk in enumerate(chunks):
            start = chunk["start_line"]
            end = chunk["end_line"]

            # Lines should be within document bounds
            assert start >= 1, f"Chunk {i}: start_line {start} < 1"
            assert end <= len(lines), f"Chunk {i}: end_line {end} > total lines {len(lines)}"
            assert start <= end, f"Chunk {i}: start_line {start} > end_line {end}"

            # The content at start_line should relate to this chunk's section
            # At minimum, the start line should exist and not be empty for headed sections
            if chunk["section_path"]:
                start_content = lines[start - 1]  # convert to 0-based
                # The start line should contain the heading or be near it
                assert start_content.strip(), (
                    f"Chunk {i}: start_line {start} points to empty line"
                )

    def test_parent_section_tracking(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """Child sections reference correct parent path."""
        chunks = parser.parse(sample_markdown)

        # Find chunks that have a parent_section_path
        child_chunks = [c for c in chunks if c["parent_section_path"]]

        # The ### headings should have ## 安装指南 as parent
        assert len(child_chunks) > 0, (
            f"Expected at least one chunk with a parent_section_path. "
            f"All chunks: {[(c['section_path'], c['parent_section_path']) for c in chunks]}"
        )

        for chunk in child_chunks:
            parent_path = chunk["parent_section_path"]
            section_path = chunk["section_path"]

            # Parent path should be a prefix of the section path
            assert section_path.startswith(parent_path + " > "), (
                f"Section path '{section_path}' should start with parent '{parent_path} > '"
            )

    def test_handles_empty_input(self, parser: MarkdownParser) -> None:
        """Empty string returns empty list."""
        assert parser.parse("") == []
        assert parser.parse("   ") == []
        assert parser.parse("\n\n\n") == []

    def test_single_heading_document(self, parser: MarkdownParser) -> None:
        """Document with only # title produces valid chunks."""
        text = "# Single Title\n\nSome content here."
        chunks = parser.parse(text)

        assert len(chunks) >= 1, "Expected at least one chunk from single-heading document"

        # Should have the heading in section_path
        chunk = chunks[0]
        assert "# Single Title" in chunk["section_path"], (
            f"Expected '# Single Title' in section_path: {chunk['section_path']}"
        )
        assert chunk["chunk_type"] == "section"
        assert chunk["start_line"] >= 1
        assert chunk["end_line"] >= chunk["start_line"]
        assert chunk["content_text"].strip()

    def test_additional_fields_present(self, parser: MarkdownParser, sample_markdown: str) -> None:
        """Chunks also include parent_section_path and token_count."""
        chunks = parser.parse(sample_markdown)

        for i, chunk in enumerate(chunks):
            assert "parent_section_path" in chunk, f"Chunk {i} missing parent_section_path"
            assert "token_count" in chunk, f"Chunk {i} missing token_count"
            assert isinstance(chunk["parent_section_path"], str)
            assert isinstance(chunk["token_count"], int)
            assert chunk["token_count"] > 0, f"Chunk {i}: token_count should be > 0"

    def test_no_headings_document(self, parser: MarkdownParser) -> None:
        """Document without headings still produces chunks."""
        text = "Just some plain text.\n\nAnother paragraph."
        chunks = parser.parse(text)

        assert len(chunks) >= 1
        assert chunks[0]["content_text"].strip()
        assert chunks[0]["chunk_type"] == "section"
