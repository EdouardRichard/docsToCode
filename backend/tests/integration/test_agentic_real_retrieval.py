"""Integration test: state machine steps 4/5 wired to real retrieval (T058 Red).

Validates against the real retrieval stack (QdrantStore dense+sparse + RRF +
Rerank, 004 graph expansion with guardrails):
  - each sub-problem query goes through 002 hybrid recall (FR-005)
  - graph signal expands via 004 graph reusing 004 guardrails (FR-033)
  - per-source evidence cap 3/limit 5 enforced (FR-006)
  - recall candidates carry retriever / score / source / version metadata
  - supplementary round candidates re-enter fusion/Rerank/analysis (FR-014)
  - US1/US2 independent test measurable: Recall@K computable

This test MUST FAIL before steps 4/5 are wired (TDD Red).
"""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.utils.snowflake import generate_id


class _FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic fake embedding provider (same convention as 002 tests)."""

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [0.5] * self._dim

    def get_dimension(self):
        return self._dim


class _MockReranker(RerankerProvider):
    """Deterministic mock reranker (same convention as 002 tests)."""

    async def rerank(self, query, candidates, top_k=5):
        results = []
        for i, c in enumerate(candidates):
            enriched = dict(c)
            enriched["rerank_score"] = 0.5 - i * 0.01
            results.append(enriched)
        results.sort(key=lambda r: (-r.get("rerank_score", 0), str(r.get("chunk_id", ""))))
        return results[:top_k]


_CHUNKS = {
    "cls": ("com.example.Service", "class Service with repository field"),
    "repo": ("com.example.Service#repository", "private final Repository repository"),
    "findA": ("com.example.Service#findA", "public A findA() { return repository.findA(); }"),
    "findB": ("com.example.Service#findB", "public B findB() { return repository.findB(); }"),
    "save": ("com.example.Service#save", "public void save() { repository.save(entity); }"),
    "other": ("com.example.Other#unrelated", "unrelated helper text for diversity"),
}


@pytest_asyncio.fixture
async def pipeline_setup(db_session: AsyncSession):
    """Own scope with 6 chunks in the real Qdrant hybrid collection."""
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Agentic Pipeline Test", alias=f"apt-{generate_id()}")
    )
    await db_session.commit()

    scope_id = project.knowledge_scope_id
    version_id = generate_id()
    source_id = generate_id()

    from rag_mcp.services.ingestion_service import _derive_index_version
    index_version = _derive_index_version(get_settings().embedding_model)

    source = KnowledgeSource(
        source_id=source_id,
        knowledge_scope_id=scope_id,
        filename="Service.java",
        content_hash=hashlib.sha256(b"agentic-pipeline-test").hexdigest(),
        format="java",
        size_bytes=21,
        status="published",
    )
    db_session.add(source)
    version = KnowledgeVersion(
        version_id=version_id,
        knowledge_scope_id=scope_id,
        version_number=1,
        capabilities={"dense_ready": True, "lexical_ready": True},
        status="published",
        published_at=None,
    )
    db_session.add(version)

    chunk_ids: dict[str, int] = {}
    texts: list[str] = []
    for key, (path, content) in _CHUNKS.items():
        cid = generate_id()
        chunk_ids[key] = cid
        texts.append(content)
        db_session.add(Chunk(
            chunk_id=cid,
            source_id=source_id,
            version_id=version_id,
            knowledge_scope_id=scope_id,
            content_text=content,
            position_path=path,
            chunk_type="symbol",
            start_line=1,
            end_line=2,
            token_count=8,
            embedding_model=get_settings().embedding_model,
            index_version=index_version,
        ))
    await db_session.commit()

    store = QdrantStore()
    collection = f"chunks_hybrid_{index_version}"
    dim = 1024
    if not store.collection_exists(collection):
        store.create_hybrid_collection(collection, dimension=dim)

    encoder = BM25SparseEncoder()
    encoder.fit(texts)

    for (key, (path, content)) in _CHUNKS.items():
        cid = chunk_ids[key]
        payload = {
            "knowledge_scope_id": str(scope_id),
            "source_id": str(source_id),
            "version_id": str(version_id),
            "chunk_id": str(cid),
            "chunk_type": "symbol",
            "position_path": path,
            "start_line": 1,
            "end_line": 2,
            "index_version": index_version,
            "embedding_model": get_settings().embedding_model,
        }
        store.upsert_hybrid(collection, cid, [0.5] * dim, encoder.encode(content), payload)

    yield {
        "project": project,
        "scope_id": scope_id,
        "version_id": version_id,
        "chunk_ids": chunk_ids,
        "store": store,
        "collection": collection,
    }

    if store.collection_exists(collection):
        try:
            store.delete_points_by_scope(collection, scope_id)
        except Exception:
            pass


def _make_pipeline(setup, reranker=None):
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from rag_mcp.orchestration.retrieval_pipeline import AgenticRetrievalPipeline

    @asynccontextmanager
    async def session_factory():
        eng = create_async_engine(get_settings().database_url, echo=False)
        factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            yield session
        await eng.dispose()

    return AgenticRetrievalPipeline(
        session_factory=session_factory,
        qdrant_store=setup["store"],
        embedding_provider=_FakeEmbeddingProvider(dim=1024),
        reranker=reranker,
    )


class TestPipelineRecall:
    """Step 4: per-sub-problem hybrid recall with metadata."""

    @pytest.mark.asyncio
    async def test_recall_candidates_carry_metadata(self, db_session, pipeline_setup):
        """Candidates carry retriever / score / source / version (ledger input)."""
        pipeline = _make_pipeline(pipeline_setup)
        result = await pipeline.retrieve_round(
            sub_problems=[{"sub_problem_id": 1, "query": "repository", "signals": ["dense", "sparse"]}],
            scope_ids=[pipeline_setup["scope_id"]],
            round_index=0,
        )
        cands = result["candidates"]
        assert len(cands) >= 1
        for c in cands:
            assert c["retrievers"], "candidate must record at least one retriever"
            for r in c["retrievers"]:
                assert r in ("dense", "sparse", "graph", "fusion", "rerank")
            assert 0.0 <= c["score"] <= 1.0
            assert c["source_id"]
            assert c["source_version"] >= 1
            assert c["knowledge_scope_id"] == pipeline_setup["scope_id"]
            assert c["project_id"] == pipeline_setup["project"].project_id
            assert c["index_version"] >= 1
            assert c["evidence_id"]
            assert c["content_excerpt"]

    @pytest.mark.asyncio
    async def test_recall_returns_recall_at_k_measurable(self, db_session, pipeline_setup):
        """Recall@K is computable: expected chunk appears in candidates."""
        pipeline = _make_pipeline(pipeline_setup)
        expected = str(pipeline_setup["chunk_ids"]["repo"])
        result = await pipeline.retrieve_round(
            sub_problems=[{"sub_problem_id": 1, "query": "repository field", "signals": ["dense", "sparse"]}],
            scope_ids=[pipeline_setup["scope_id"]],
            round_index=0,
        )
        ids = {c["evidence_id"] for c in result["candidates"]}
        assert expected in ids

    @pytest.mark.asyncio
    async def test_multi_subproblem_merge_traceability(self, db_session, pipeline_setup):
        """Two sub-problems merge with sub_problem_id traceability (FR-009)."""
        pipeline = _make_pipeline(pipeline_setup)
        result = await pipeline.retrieve_round(
            sub_problems=[
                {"sub_problem_id": 1, "query": "repository", "signals": ["dense", "sparse"]},
                {"sub_problem_id": 2, "query": "save", "signals": ["dense", "sparse"]},
            ],
            scope_ids=[pipeline_setup["scope_id"]],
            round_index=0,
        )
        cands = result["candidates"]
        assert len(cands) >= 2
        for c in cands:
            assert c["sub_problem_ids"], "merged candidate keeps sub-problem traceability"
            assert c["sub_problem_id"] >= 1
        # all sub_problem_ids reference declared sub-problems only
        for c in cands:
            assert set(c["sub_problem_ids"]) <= {1, 2}

    @pytest.mark.asyncio
    async def test_rerank_reenters_with_scores(self, db_session, pipeline_setup):
        """Rerank participates and candidates carry rerank scores (FR-014)."""
        pipeline = _make_pipeline(pipeline_setup, reranker=_MockReranker())
        result = await pipeline.retrieve_round(
            sub_problems=[{"sub_problem_id": 1, "query": "repository", "signals": ["dense", "sparse"]}],
            scope_ids=[pipeline_setup["scope_id"]],
            round_index=0,
        )
        reranked = [c for c in result["candidates"] if c.get("rerank_score") is not None]
        assert reranked, "rerank scores must be carried into candidates"
        assert result["subpath_timings"].get("rerank_ms", 0) >= 0


class TestPerSourceCap:
    """FR-006: single-source max evidence 3 / limit 5."""

    @pytest.mark.asyncio
    async def test_single_source_capped_at_config_default(self, db_session, pipeline_setup):
        pipeline = _make_pipeline(pipeline_setup)
        result = await pipeline.retrieve_round(
            sub_problems=[
                {"sub_problem_id": 1, "query": "repository", "signals": ["dense", "sparse"]},
                {"sub_problem_id": 2, "query": "service", "signals": ["dense", "sparse"]},
            ],
            scope_ids=[pipeline_setup["scope_id"]],
            round_index=0,
        )
        # all 6 chunks belong to one source; default cap is 3 (limit 5)
        settings = get_settings()
        cap = settings.agentic.max_evidence_per_source
        assert cap == 3
        source_counts: dict[str, int] = {}
        for c in result["candidates"]:
            source_counts[c["source_id"]] = source_counts.get(c["source_id"], 0) + 1
        for sid, count in source_counts.items():
            assert count <= cap, f"source {sid} exceeds per-source cap: {count}"


class TestStateMachineWiring:
    """Steps 4/5 of the state machine use the wired pipeline."""

    @pytest.mark.asyncio
    async def test_run_async_produces_candidates(self, db_session, pipeline_setup):
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [
            {"query": "repository", "signals": ["dense", "sparse"]},
        ]
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda ctx: {
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
        }

        machine = AgenticStateMachine(
            run_id=str(generate_id()),
            request_id="req-t058",
            project_scope=[str(pipeline_setup["project"].project_id)],
            knowledge_scope_ids=[str(pipeline_setup["scope_id"])],
        )
        machine.set_query_planner(planner)
        machine.set_evidence_analyst(analyst)
        machine.set_retrieval_pipeline(_make_pipeline(pipeline_setup))

        record = await machine.run_async(context={
            "query": "what uses repository",
            "scope_ids": [pipeline_setup["scope_id"]],
        })
        # steps 4/5 executed with real recall
        assert "parallel_retrieval" in machine.get_executed_steps()
        assert "fusion_rerank" in machine.get_executed_steps()
        cands = machine.get_candidates()
        assert len(cands) >= 1
        expected = str(pipeline_setup["chunk_ids"]["repo"])
        assert any(c["evidence_id"] == expected for c in cands)
        assert record["completion_status"] in ("complete", "partial")

    @pytest.mark.asyncio
    async def test_recall_at_k_measurable_in_machine(self, db_session, pipeline_setup):
        """US1 independent test: Recall@K computable from machine candidates."""
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.orchestration.state_machine import AgenticStateMachine

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [
            {"query": "repository field", "signals": ["dense", "sparse"]},
        ]
        machine = AgenticStateMachine(
            run_id=str(generate_id()),
            request_id="req-t058-recall",
            project_scope=[str(pipeline_setup["project"].project_id)],
            knowledge_scope_ids=[str(pipeline_setup["scope_id"])],
        )
        machine.set_query_planner(planner)
        machine.set_retrieval_pipeline(_make_pipeline(pipeline_setup))

        await machine.run_async(context={
            "query": "repository field",
            "scope_ids": [pipeline_setup["scope_id"]],
        })
        expected = {str(pipeline_setup["chunk_ids"]["repo"])}
        recalled = {c["evidence_id"] for c in machine.get_candidates()}
        recall = len(expected & recalled) / len(expected)
        assert recall == pytest.approx(1.0), f"Recall@K={recall}, recalled={recalled}"
