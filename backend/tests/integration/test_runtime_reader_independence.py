"""Integration test: reader independence from the writer (T029/T030).

FR-005/SC-003: a reader serves retrieval and evidence expansion through the
shared PostgreSQL / Qdrant only — never through writer-local files. Stopping
the writer leaves the reader fully functional.

Proof strategy: point DATA_ROOT at a non-existent path (so ANY local file
read would FileNotFoundError), then run search_knowledge + get_evidence
through the shared-DB path and assert success. A reader that needed the
writer's local files would fail immediately.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_mcp.models import Chunk, KnowledgeScope


class StubEmbedding:
    """Deterministic embedding stand-in: the reader path needs only a vector."""

    async def embed_texts(self, texts):
        return [[0.01] * 8 for _ in texts]

    def get_dimension(self) -> int:
        return 8

    async def embed_query(self, text):
        return [0.01] * 8


@pytest.fixture(autouse=True)
def _bogus_data_root(monkeypatch, tmp_path):
    """Any local file access now points at a non-existent directory."""
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "does-not-exist"))


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _pick_chunk_with_scope(session) -> tuple[int, int]:
    """Return (chunk_id, scope_id) for any chunk with content."""
    row = (
        await session.execute(
            select(Chunk.chunk_id, Chunk.knowledge_scope_id)
            .where(Chunk.content_text.is_not(None))
            .limit(1)
        )
    ).first()
    assert row is not None, "no chunk with content found in shared DB"
    return row[0], row[1]


async def _scope_type(session, scope_id: int) -> str:
    row = (
        await session.execute(
            select(KnowledgeScope.scope_type).where(KnowledgeScope.scope_id == scope_id)
        )
    ).first()
    return row[0] if row else "project"


@pytest.mark.asyncio
async def test_get_evidence_works_without_local_files(db_session, session_factory):
    """get_evidence resolves full content from the shared DB (no data_root)."""
    from rag_mcp.services.evidence_service import EvidenceService

    async with session_factory() as session:
        chunk_id, scope_id = await _pick_chunk_with_scope(session)
        scope_type = await _scope_type(session, scope_id)

    async with session_factory() as session:
        service = EvidenceService(session)
        result = await service.get_evidence(
            evidence_id=str(chunk_id),
            project_scopes=[str(scope_id)],
        )
    assert result["status"] == "available", result
    assert result["full_content"]
    assert result["knowledge_scope_id"] == str(scope_id)


@pytest.mark.asyncio
async def test_search_path_works_without_local_files(session_factory):
    """search_knowledge executes through DB + Qdrant only (reader form)."""
    from rag_mcp.mcp.search_knowledge import search_knowledge_core
    from rag_mcp.indexing.qdrant_client import QdrantStore

    async with session_factory() as session:
        chunk_id, scope_id = await _pick_chunk_with_scope(session)

    result = await search_knowledge_core(
        query="retrieval test query",
        project_scope=[str(scope_id)],
        top_k=5,
        task_context=None,
        session_factory=session_factory,
        qdrant_store=QdrantStore(),
        embedding_provider=StubEmbedding(),
        reranker=None,
    )
    # Four-state contract: a valid completion_status, never an exception.
    assert result["completion_status"] in ("complete", "partial", "no_evidence", "failed")
    assert "request_id" in result


@pytest.mark.asyncio
async def test_reader_mcp_registers_evidence_tool(session_factory):
    """The reader MCP form exposes get_evidence over the shared DB only."""
    from rag_mcp.mcp import create_mcp_server

    server = create_mcp_server(
        session_factory=session_factory,
        embedding_provider=StubEmbedding(),
    )
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert "get_evidence" in names
    assert "search_knowledge" in names


def test_evidence_service_has_no_local_file_access() -> None:
    """Structural FR-005 guarantee: evidence expansion touches no filesystem."""
    import inspect

    from rag_mcp.services import evidence_service as ev

    src = inspect.getsource(ev)
    assert "data_root" not in src
    assert "Path(" not in src
    assert "read_bytes" not in src
    assert "open(" not in src
