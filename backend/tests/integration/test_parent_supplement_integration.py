"""Integration test: parent-context supplementation against the real DB (T065).

The parent chunk exists only in PostgreSQL (not indexed in Qdrant): it can
only enter the final context through orchestration supplementation, proving
the 001 parent backfill reuse, the boxing cap, and selection-list
traceability end-to-end.
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.utils.snowflake import generate_id


class _FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [0.5] * self._dim

    def get_dimension(self):
        return self._dim


def _stub_agent_builder():
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    planner = QueryPlannerAgent(model_and_version="t")
    planner._llm_decompose = lambda q, ctx: [
        {"query": "validate token", "signals": ["dense", "sparse"]},
    ]
    analyst = EvidenceAnalystAgent(model_and_version="t")
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }
    return planner, analyst, ContextOrchestratorAgent(model_and_version="t")


@pytest_asyncio.fixture
async def parent_setup(db_session: AsyncSession):
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.ingestion_service import _derive_index_version
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Parent Supp Test", alias=f"psupp-{generate_id()}")
    )
    await db_session.commit()

    scope_id = project.knowledge_scope_id
    version_id = generate_id()
    source_id = generate_id()
    index_version = _derive_index_version(get_settings().embedding_model)

    db_session.add(KnowledgeSource(
        source_id=source_id, knowledge_scope_id=scope_id,
        filename="Parent.java",
        content_hash=hashlib.sha256(b"parent-supp").hexdigest(),
        format="java", size_bytes=11, status="published",
    ))
    db_session.add(KnowledgeVersion(
        version_id=version_id, knowledge_scope_id=scope_id, version_number=1,
        capabilities={"dense_ready": True, "lexical_ready": True},
        status="published", published_at=None,
    ))

    parent_id = generate_id()
    child_id = generate_id()
    db_session.add(Chunk(
        chunk_id=parent_id, source_id=source_id, version_id=version_id,
        knowledge_scope_id=scope_id,
        content_text="public class TokenService { /* class-level context */ }",
        position_path="com.example.TokenService", chunk_type="symbol",
        start_line=1, end_line=1, token_count=8,
        embedding_model=get_settings().embedding_model,
        index_version=index_version,
    ))
    db_session.add(Chunk(
        chunk_id=child_id, source_id=source_id, version_id=version_id,
        knowledge_scope_id=scope_id,
        content_text="private void validateToken(String t) { check(t); }",
        position_path="com.example.TokenService#validateToken", chunk_type="symbol",
        start_line=2, end_line=2, token_count=8,
        embedding_model=get_settings().embedding_model,
        index_version=index_version,
        parent_chunk_id=parent_id,
    ))
    await db_session.commit()

    store = QdrantStore()
    collection = f"chunks_hybrid_{index_version}"
    if not store.collection_exists(collection):
        store.create_hybrid_collection(collection, dimension=1024)
    child_text = "private void validateToken(String t) { check(t); }"
    encoder = BM25SparseEncoder()
    encoder.fit([child_text])
    # Only the CHILD is indexed in Qdrant; the parent stays PG-only so it can
    # only reach the final context via orchestration supplementation.
    store.upsert_hybrid(collection, child_id, [0.5] * 1024, encoder.encode(child_text), {
        "knowledge_scope_id": str(scope_id),
        "source_id": str(source_id),
        "version_id": str(version_id),
        "chunk_id": str(child_id),
        "chunk_type": "symbol",
        "position_path": "com.example.TokenService#validateToken",
        "start_line": 2,
        "end_line": 2,
        "index_version": index_version,
        "embedding_model": get_settings().embedding_model,
    })

    yield {
        "project": project,
        "scope_id": scope_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "store": store,
    }

    if store.collection_exists(collection):
        try:
            store.delete_points_by_scope(collection, scope_id)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_parent_supplemented_end_to_end(db_session, parent_setup):
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from rag_mcp.orchestration.entry import run_agentic_search

    @asynccontextmanager
    async def session_factory():
        eng = create_async_engine(get_settings().database_url, echo=False)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            yield session
        await eng.dispose()

    response, record = await run_agentic_search(
        query="validate token",
        project_scopes=[str(parent_setup["project"].project_id)],
        top_k=5,
        task_context=None,
        session_factory=session_factory,
        qdrant_store=parent_setup["store"],
        embedding_provider=_FakeEmbeddingProvider(),
        reranker=None,
        agent_builder=_stub_agent_builder,
        return_record=True,
    )

    ids = {e["evidence_id"] for e in response["evidence"]}
    assert str(parent_setup["child_id"]) in ids, "recalled child must be returned"
    assert str(parent_setup["parent_id"]) in ids, (
        "parent scope must be supplemented into the final context"
    )

    # Selection list carries the parent decision with a resolvable ledger FK
    run_id = str(record["run_id"])
    sel_rows = (await db_session.execute(sa_text(
        "SELECT ledger_entry_id, decision FROM context_selection_list "
        "WHERE run_id = :rid AND decision = 'selected'"
    ), {"rid": run_id})).all()
    parent_ledger_rows = (await db_session.execute(sa_text(
        "SELECT ledger_entry_id, referenced_by_agent, retriever "
        "FROM evidence_ledger_entry "
        "WHERE request_id = :req AND evidence_id = :eid"
    ), {"req": response["request_id"], "eid": str(parent_setup["parent_id"])})).all()
    assert parent_ledger_rows, "parent supplement must be ledgered (traceable)"
    assert parent_ledger_rows[0].referenced_by_agent == "context_orchestrator"
    parent_ledger_id = parent_ledger_rows[0].ledger_entry_id
    assert any(r.ledger_entry_id == parent_ledger_id for r in sel_rows), (
        "selection list must reference the parent ledger entry"
    )
