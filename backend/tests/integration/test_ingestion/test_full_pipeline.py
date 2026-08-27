"""Integration tests for the ingestion pipeline deterministic core (T026).

Covers the deterministic stages — credential redaction → format-aware parsing →
chunk assembly → embedding contract — without requiring PostgreSQL/Qdrant or
the real bge-m3 model. The full DB + Qdrant + real-model end-to-end is covered
by quickstart VS-001 manual validation (deferred per the original T026 note).

Uses the real parsers and credential redactor, plus a lightweight deterministic
fake embedding provider to verify the embedding stage contract.
"""

import pytest

from rag_mcp.parsers.credential_redactor import redact_credentials
from rag_mcp.parsers.java_parser import JavaParser
from rag_mcp.parsers.markdown_parser import MarkdownParser
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.services.ingestion_service import (
    IngestionService,
    _derive_index_version,
    _estimate_tokens,
)


class _FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic fake embedding provider for pipeline tests."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7) + 0.1] * self._dim for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.5] * self._dim

    def get_dimension(self) -> int:
        return self._dim


class TestIndexVersionDerivation:
    def test_derive_index_version_short_name(self):
        assert _derive_index_version("BAAI/bge-m3") == "bge-m3_v1"

    def test_derive_index_version_preserves_convention(self):
        assert _derive_index_version("sentence-transformers/all-MiniLM-L6-v2") == "all-MiniLM-L6-v2_v1"

    def test_estimate_tokens_empty_text_is_one(self):
        assert _estimate_tokens("") == 1

    def test_estimate_tokens_scales_with_chars(self):
        assert _estimate_tokens("12345678") == 2  # 8 chars / 4


class TestParseContentDispatch:
    def _svc(self) -> IngestionService:
        return IngestionService(session=None, embedding_provider=None, qdrant_store=None)

    def test_markdown_dispatch_produces_section_chunks(self):
        svc = self._svc()
        text = "# 顶层\n\n## 子章节\n\n正文内容"
        chunks = svc._parse_content(text, "markdown", "doc.md")
        assert len(chunks) > 0
        assert all(c["chunk_type"] == "section" for c in chunks)

    def test_java_dispatch_produces_symbol_chunks(self, sample_java):
        svc = self._svc()
        chunks = svc._parse_content(sample_java, "java", "UserService.java")
        assert len(chunks) > 0
        assert all(c["chunk_type"] == "symbol" for c in chunks)

    def test_unsupported_format_raises(self):
        svc = self._svc()
        with pytest.raises(ValueError, match="Unsupported format"):
            svc._parse_content("x", "pdf", "doc.pdf")


class TestFullPipelineDeterministicCore:
    @pytest.mark.asyncio
    async def test_markdown_redact_parse_embed_contract(self, sample_markdown):
        provider = _FakeEmbeddingProvider(dim=8)
        redacted = redact_credentials(sample_markdown)
        chunks = MarkdownParser().parse(redacted)

        assert chunks, "Markdown parsing produced no chunks"
        texts = [c["content_text"] for c in chunks]
        vectors = await provider.embed_texts(texts)

        assert len(vectors) == len(texts)
        assert all(len(v) == 8 for v in vectors)
        assert all("MySecret123" not in t for t in texts), "credential leaked into embedded text"
        assert all("sk-abc123def456" not in t for t in texts), "api key leaked into embedded text"

    @pytest.mark.asyncio
    async def test_java_redact_parse_embed_contract(self, sample_java):
        provider = _FakeEmbeddingProvider(dim=8)
        redacted = redact_credentials(sample_java)
        chunks = JavaParser().parse(redacted, "UserService.java")

        assert chunks, "Java parsing produced no chunks"
        texts = [c["content_text"] for c in chunks]
        vectors = await provider.embed_texts(texts)

        assert len(vectors) == len(texts)
        assert all("SuperSecret456" not in t for t in texts), "credential leaked into embedded text"

    def test_chunks_carry_position_and_line_range(self, sample_markdown):
        redacted = redact_credentials(sample_markdown)
        chunks = MarkdownParser().parse(redacted)

        for c in chunks:
            assert c["content_text"]
            assert c["start_line"] >= 1
            assert c["end_line"] >= c["start_line"]
            assert c["chunk_type"] == "section"

    def test_java_chunks_carry_symbol_path(self, sample_java):
        redacted = redact_credentials(sample_java)
        chunks = JavaParser().parse(redacted, "UserService.java")

        symbol_paths = [c.get("symbol_path", "") for c in chunks]
        assert any("UserService" in p for p in symbol_paths), \
            f"Expected a symbol path referencing UserService, got {symbol_paths}"
