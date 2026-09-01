"""Integration test for US5: graph_ready capability enforcement (T031).

Validates that only graph_ready versions enter graph expansion; non-graph
versions continue hybrid retrieval (FR-013/FR-014).

This test MUST FAIL before the capability gating is wired (TDD).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from rag_mcp.services.retrieval_service import RetrievalService
from rag_mcp.utils.snowflake import generate_id
from tests.unit.test_postgres_graph_store import _insert_chunk, _insert_edge, _setup_scope


@pytest.fixture
async def two_scopes(db_session):
    """Create two scopes: one with graph_ready, one without."""
    scope_graph = generate_id()
    scope_no_graph = generate_id()
    proj_g = generate_id()
    proj_ng = generate_id()
    ver_g = generate_id()
    ver_ng = generate_id()

    src_g = await _setup_scope(db_session, scope_graph, proj_g, ver_g)
    src_ng = await _setup_scope(db_session, scope_no_graph, proj_ng, ver_ng)

    # Mark the graph scope's version as graph_ready
    await db_session.execute(text(
        "UPDATE knowledge_versions SET graph_ready = true WHERE version_id = :vid"
    ), {"vid": ver_g})
    await db_session.commit()

    return {
        "scope_graph": scope_graph,
        "scope_no_graph": scope_no_graph,
        "ver_graph": ver_g,
        "ver_no_graph": ver_ng,
        "src_graph": src_g,
        "src_no_graph": src_ng,
    }


@pytest.mark.asyncio
async def test_graph_ready_version_detected(db_session, two_scopes):
    """_has_graph_ready_versions MUST return True for graph_ready scope."""
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from rag_mcp.providers.base import EmbeddingProvider

    # Create a minimal mock — we only test the gating method
    class MockEmbedding(EmbeddingProvider):
        async def embed_query(self, query):
            return [0.0]
        async def embed_texts(self, texts):
            return [[0.0]] * len(texts)
        def get_dimension(self):
            return 1

    store = QdrantStore(url="http://localhost:6333")
    service = RetrievalService(db_session, store, MockEmbedding())

    has_graph = await service._has_graph_ready_versions([two_scopes["scope_graph"]])
    assert has_graph is True, "graph_ready version should be detected"

    no_graph = await service._has_graph_ready_versions([two_scopes["scope_no_graph"]])
    assert no_graph is False, "non-graph version should not be detected"


@pytest.mark.asyncio
async def test_graph_edges_check(db_session, two_scopes):
    """_has_graph_edges MUST detect existing graph_edge records."""
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from rag_mcp.providers.base import EmbeddingProvider

    class MockEmbedding(EmbeddingProvider):
        async def embed_query(self, query):
            return [0.0]
        async def embed_texts(self, texts):
            return [[0.0]] * len(texts)
        def get_dimension(self):
            return 1

    store = QdrantStore(url="http://localhost:6333")
    service = RetrievalService(db_session, store, MockEmbedding())

    # No edges yet -> False
    has_edges = await service._has_graph_edges([two_scopes["scope_graph"]])
    assert has_edges is False

    # Insert a graph edge
    src_chunk = generate_id()
    tgt_chunk = generate_id()
    await _insert_chunk(db_session, src_chunk, two_scopes["scope_graph"],
                        two_scopes["ver_graph"], two_scopes["src_graph"])
    await _insert_chunk(db_session, tgt_chunk, two_scopes["scope_graph"],
                        two_scopes["ver_graph"], two_scopes["src_graph"])
    await _insert_edge(db_session, two_scopes["scope_graph"], 0,
                       src_chunk, tgt_chunk, "calls")
    await db_session.commit()

    has_edges = await service._has_graph_edges([two_scopes["scope_graph"]])
    assert has_edges is True, "graph_edge records should be detected"


@pytest.mark.asyncio
async def test_non_graph_scope_not_detected(db_session, two_scopes):
    """Scope without graph_ready MUST NOT be detected as graph-ready."""
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from rag_mcp.providers.base import EmbeddingProvider

    class MockEmbedding(EmbeddingProvider):
        async def embed_query(self, query):
            return [0.0]
        async def embed_texts(self, texts):
            return [[0.0]] * len(texts)
        def get_dimension(self):
            return 1

    store = QdrantStore(url="http://localhost:6333")
    service = RetrievalService(db_session, store, MockEmbedding())

    # The non-graph scope should not have graph_ready
    has_graph = await service._has_graph_ready_versions([two_scopes["scope_no_graph"]])
    assert has_graph is False
