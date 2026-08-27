"""Credential redaction E2E verification (T056).

Verifies that credential values never appear in parsed chunks or API responses.
Field names and structure are preserved per SC-006.
"""

import pytest

from rag_mcp.parsers.credential_redactor import redact_credentials
from rag_mcp.parsers.markdown_parser import MarkdownParser
from rag_mcp.parsers.java_parser import JavaParser


class TestCredentialSafetyE2E:
    """End-to-end credential safety through the parsing pipeline."""

    def test_markdown_credentials_redacted_before_chunking(self, sample_markdown):
        """Credentials in Markdown are redacted before chunking produces any output."""
        redacted = redact_credentials(sample_markdown)

        # Verify no raw credentials remain
        assert "MySecret123" not in redacted
        assert "sk-abc123def456ghi789jkl012mno345" not in redacted
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted

        # Verify field names preserved
        assert "password" in redacted.lower()
        assert "api_key" in redacted.lower() or "api-key" in redacted.lower()

        # Verify placeholders present
        assert "<password>" in redacted
        assert "<api-key>" in redacted
        assert "<token>" in redacted

    def test_java_credentials_redacted_before_chunking(self, sample_java):
        """Credentials in Java source are redacted before chunking."""
        redacted = redact_credentials(sample_java)

        assert "SuperSecret456" not in redacted
        assert "DB_PASSWORD" in redacted  # field name preserved

    def test_chunks_contain_no_raw_credentials(self, sample_markdown):
        """Chunks produced from redacted Markdown contain no raw credentials."""
        redacted = redact_credentials(sample_markdown)
        parser = MarkdownParser()
        chunks = parser.parse(redacted)

        for chunk in chunks:
            content = chunk["content_text"]
            assert "MySecret123" not in content, f"Credential leaked in chunk: {chunk['section_path']}"
            assert "sk-abc123def456" not in content, f"API key leaked in chunk: {chunk['section_path']}"

    def test_java_chunks_contain_no_raw_credentials(self, sample_java):
        """Chunks produced from redacted Java contain no raw credentials."""
        redacted = redact_credentials(sample_java)
        parser = JavaParser()
        chunks = parser.parse(redacted, "UserService.java")

        for chunk in chunks:
            content = chunk["content_text"]
            assert "SuperSecret456" not in content, f"Credential leaked in chunk: {chunk.get('symbol_path', 'unknown')}"

    def test_structure_preserved_after_redaction(self, sample_markdown):
        """Document structure (headings, lists, code) is preserved after redaction."""
        redacted = redact_credentials(sample_markdown)
        parser = MarkdownParser()
        chunks = parser.parse(redacted)

        # Should still produce meaningful chunks
        assert len(chunks) > 0

        # Section paths should still reflect heading hierarchy
        section_paths = [c["section_path"] for c in chunks]
        assert any("安装指南" in p or "Installation" in p for p in section_paths), \
            f"Heading structure lost: {section_paths}"
