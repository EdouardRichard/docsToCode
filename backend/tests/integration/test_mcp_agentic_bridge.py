"""Integration tests for the MCP agentic bridge (T057 Red).

search_knowledge routes to the Agent orchestration path when
AGENTIC_RETRIEVAL_ENABLED=true and keeps the deterministic 001 behaviour
byte-identical when the switch is off (FR-024, Constitution X):

  - OFF: behaviour identical to 001; no agentic component is invoked
  - ON: search_knowledge returns through the state machine
  - external response schema unchanged (additionalProperties:false)
  - partial terminal state carries gaps via the existing gaps field (FR-016)
  - request_id bridges the internal ledger (FR-024/SC-004)
  - agentic-path failure degrades to the deterministic path (SC-011)

This test MUST FAIL before the bridge exists (TDD Red).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.indexing.sparse_encoder import BM25SparseEncoder
from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_source import KnowledgeSource
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.utils.snowflake import generate_id

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SEARCH_OUTPUT_SCHEMA = json.loads((
    _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
    / "mcp-search-output.schema.json"
).read_text(encoding="utf-8"))


class _FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [0.5] * self._dim

    def get_dimension(self):
        return self._dim


@pytest_asyncio.fixture
async def bridge_setup(db_session: AsyncSession):
    """Own scope with a few chunks in the real Qdrant hybrid collection."""
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name="Bridge Test", alias=f"bridge-{generate_id()}")
    )
    await db_session.commit()

    scope_id = project.knowledge_scope_id
    version_id = generate_id()
    source_id = generate_id()

    from rag_mcp.services.ingestion_service import _derive_index_version
    index_version = _derive_index_version(get_settings().embedding_model)

    db_session.add(KnowledgeSource(
        source_id=source_id,
        knowledge_scope_id=scope_id,
        filename="Bridge.java",
        content_hash=hashlib.sha256(b"bridge-test").hexdigest(),
        format="java",
        size_bytes=11,
        status="published",
    ))
    db_session.add(KnowledgeVersion(
        version_id=version_id,
        knowledge_scope_id=scope_id,
        version_number=1,
        capabilities={"dense_ready": True, "lexical_ready": True},
        status="published",
        published_at=None,
    ))

    chunks = {
        "findA": ("com.example.Bridge#findA", "public A findA() { return repo.findA(); }"),
        "findB": ("com.example.Bridge#findB", "public B findB() { return repo.findB(); }"),
    }
    chunk_ids = {}
    texts = []
    for key, (path, content) in chunks.items():
        cid = generate_id()
        chunk_ids[key] = cid
        texts.append(content)
        db_session.add(Chunk(
            chunk_id=cid, source_id=source_id, version_id=version_id,
            knowledge_scope_id=scope_id, content_text=content,
            position_path=path, chunk_type="symbol", start_line=1, end_line=2,
            token_count=8, embedding_model=get_settings().embedding_model,
            index_version=index_version,
        ))
    await db_session.commit()

    store = QdrantStore()
    collection = f"chunks_hybrid_{index_version}"
    if not store.collection_exists(collection):
        store.create_hybrid_collection(collection, dimension=1024)
    encoder = BM25SparseEncoder()
    encoder.fit(texts)
    for key, (path, content) in chunks.items():
        store.upsert_hybrid(collection, chunk_ids[key], [0.5] * 1024, encoder.encode(content), {
            "knowledge_scope_id": str(scope_id),
            "source_id": str(source_id),
            "version_id": str(version_id),
            "chunk_id": str(chunk_ids[key]),
            "chunk_type": "symbol",
            "position_path": path,
            "start_line": 1,
            "end_line": 2,
            "index_version": index_version,
            "embedding_model": get_settings().embedding_model,
        })

    yield {
        "project": project,
        "scope_id": scope_id,
        "chunk_ids": chunk_ids,
        "store": store,
        "collection": collection,
    }

    if store.collection_exists(collection):
        try:
            store.delete_points_by_scope(collection, scope_id)
        except Exception:
            pass


def _session_factory():
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    @asynccontextmanager
    async def factory():
        eng = create_async_engine(get_settings().database_url, echo=False)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            yield session
        await eng.dispose()

    return factory


def _stub_agent_builder():
    """Deterministic agents: planner splits in two, analyst judges covered."""
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    planner = QueryPlannerAgent(model_and_version="test-v1")
    planner._llm_decompose = lambda q, ctx: [
        {"query": "findA", "signals": ["dense", "sparse"]},
        {"query": "findB", "signals": ["dense", "sparse"]},
    ]
    analyst = EvidenceAnalystAgent(model_and_version="test-v1")
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }
    orchestrator = ContextOrchestratorAgent(model_and_version="test-v1")
    return planner, analyst, orchestrator


class TestSwitchOffDeterministic:
    """OFF: byte-identical to 001; agentic path never invoked."""

    @pytest.mark.asyncio
    async def test_off_byte_identical_and_no_agentic(self, db_session, bridge_setup, monkeypatch):
        monkeypatch.setenv("AGENTIC_RETRIEVAL_ENABLED", "false")
        import rag_mcp.mcp.search_knowledge as sk_mod

        called = {"n": 0}

        async def _spy(**kwargs):
            called["n"] += 1
            raise AssertionError("agentic entry must not run when switch is off")

        monkeypatch.setattr(sk_mod, "_run_agentic_search", _spy, raising=False)

        args = dict(
            query="findA",
            project_scope=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
        )
        r1 = await sk_mod.search_knowledge_core(**args)
        r2 = await sk_mod.search_knowledge_core(**args)

        assert called["n"] == 0
        # Byte-identical except for the per-request id
        rid1, rid2 = r1.pop("request_id"), r2.pop("request_id")
        assert rid1 and rid2 and rid1 != rid2
        assert r1 == r2, "deterministic path must be reproducible when OFF"


class TestSwitchOnRouting:
    """ON: search_knowledge returns through the state machine."""

    @pytest.mark.asyncio
    async def test_on_routes_to_agentic_entry(self, db_session, bridge_setup, monkeypatch):
        monkeypatch.setenv("AGENTIC_RETRIEVAL_ENABLED", "true")
        import rag_mcp.mcp.search_knowledge as sk_mod

        marker = {
            "completion_status": "complete",
            "evidence": [],
            "request_id": "agentic-marker",
        }
        captured = {}

        async def _fake_entry(**kwargs):
            captured.update(kwargs)
            return marker

        monkeypatch.setattr(sk_mod, "_run_agentic_search", _fake_entry)

        result = await sk_mod.search_knowledge_core(
            query="findA",
            project_scope=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
        )
        assert result is marker, "ON must return the state-machine response verbatim"
        assert captured["query"] == "findA"
        assert captured["project_scopes"] == [str(bridge_setup["project"].project_id)]

    @pytest.mark.asyncio
    async def test_on_path_failure_degrades_to_deterministic(self, db_session, bridge_setup, monkeypatch):
        monkeypatch.setenv("AGENTIC_RETRIEVAL_ENABLED", "true")
        import rag_mcp.mcp.search_knowledge as sk_mod
        from rag_mcp.orchestration.entry import AgenticPathUnavailable

        async def _broken(**kwargs):
            raise AgenticPathUnavailable("boom")

        monkeypatch.setattr(sk_mod, "_run_agentic_search", _broken)

        result = await sk_mod.search_knowledge_core(
            query="findA",
            project_scope=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
        )
        # Degraded to the deterministic path instead of failing the request
        assert result["completion_status"] in ("complete", "partial", "no_evidence")
        assert "error" not in result


class TestAgenticEntryResponse:
    """run_agentic_search produces schema-valid MCP responses."""

    @pytest.mark.asyncio
    async def test_response_conforms_to_search_output_schema(self, db_session, bridge_setup):
        from rag_mcp.orchestration.entry import run_agentic_search

        response, record = await run_agentic_search(
            query="what does findA call",
            project_scopes=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
            agent_builder=_stub_agent_builder,
            return_record=True,
        )
        validator = Draft202012Validator(_SEARCH_OUTPUT_SCHEMA)
        errors = list(validator.iter_errors(response))
        assert not errors, f"schema violations: {[e.message for e in errors]}"
        assert response["completion_status"] in ("complete", "partial", "no_evidence", "failed")

    @pytest.mark.asyncio
    async def test_evidence_returned_from_state_machine(self, db_session, bridge_setup):
        from rag_mcp.orchestration.entry import run_agentic_search

        response, _ = await run_agentic_search(
            query="what does findA call",
            project_scopes=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
            agent_builder=_stub_agent_builder,
            return_record=True,
        )
        assert response["completion_status"] in ("complete", "partial")
        ids = {e["evidence_id"] for e in response["evidence"]}
        assert str(bridge_setup["chunk_ids"]["findA"]) in ids

    @pytest.mark.asyncio
    async def test_partial_carries_gaps(self, db_session, bridge_setup):
        from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
        from rag_mcp.agents.query_planner import QueryPlannerAgent
        from rag_mcp.orchestration.entry import run_agentic_search

        planner = QueryPlannerAgent(model_and_version="test-v1")
        planner._llm_decompose = lambda q, ctx: [
            {"query": "findA", "signals": ["dense", "sparse"]},
            {"query": "missing audit logging", "signals": ["dense"]},
        ]
        analyst = EvidenceAnalystAgent(model_and_version="test-v1")
        analyst._llm_judge = lambda ctx: {
            "coverage_state": "partial",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [2],
            "needs_supplementary": False,
            "gap_descriptions": [
                {"description": "sub-problem 2 uncovered", "suggested_action": "broaden scope"},
            ],
        }

        def builder():
            return planner, analyst, ContextOrchestratorAgent(model_and_version="test-v1")

        response, record = await run_agentic_search(
            query="findA and audit logging",
            project_scopes=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
            agent_builder=builder,
            return_record=True,
        )
        assert response["completion_status"] == "partial"
        assert response.get("gaps"), "partial must carry gaps (FR-016)"
        gap_text = json.dumps(response["gaps"], ensure_ascii=False)
        assert "sub-problem 2 uncovered" in gap_text
        # schema still valid with gaps
        errors = list(Draft202012Validator(_SEARCH_OUTPUT_SCHEMA).iter_errors(response))
        assert not errors, f"schema violations: {[e.message for e in errors]}"

    @pytest.mark.asyncio
    async def test_request_id_bridges_run_record(self, db_session, bridge_setup):
        from rag_mcp.orchestration.entry import run_agentic_search

        response, record = await run_agentic_search(
            query="findA",
            project_scopes=[str(bridge_setup["project"].project_id)],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
            agent_builder=_stub_agent_builder,
            return_record=True,
        )
        # (request_id, evidence_id) is the bridge key into the internal
        # ledger (FR-024/SC-004): the run record shares the request_id.
        assert response["request_id"] == record["request_id"]
        assert record["ledger_ref"]["ledger_entry_ids"] is not None

    @pytest.mark.asyncio
    async def test_no_scope_rejected(self, db_session, bridge_setup):
        from rag_mcp.orchestration.entry import run_agentic_search

        response, _ = await run_agentic_search(
            query="findA",
            project_scopes=["definitely-not-a-project-ref"],
            top_k=5,
            task_context=None,
            session_factory=_session_factory(),
            qdrant_store=bridge_setup["store"],
            embedding_provider=_FakeEmbeddingProvider(),
            reranker=None,
            agent_builder=_stub_agent_builder(),
            return_record=True,
        )
        assert response["completion_status"] == "failed"
        assert response["error"]["code"] == "MISSING_PROJECT_SCOPE"
