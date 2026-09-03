"""Integration tests for agentic persistence (T059 Red).

During an agentic run the runtime state MUST be persisted:
  - every recalled evidence -> evidence_ledger_entry (isolation triple,
    append-only, round_index/sub_problem_id, retriever/score/version/source,
    FR-008/FR-009)
  - every evidence-analyst judgment -> agent_judgment
  - step-8 selection decisions -> context_selection_list
  - run end -> agentic_retrieval_run (FR-031)
  - (request_id, evidence_id) bridge key resolves ledger entries (SC-006)
  - 5 concurrent requests persist without crosstalk (SC-013)

This test MUST FAIL before persistence is wired (TDD Red).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator, RefResolver
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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONTRACTS = _REPO_ROOT / "specs" / "005-agentic-retrieval-orchestration" / "contracts"
_RUN_SCHEMA = json.loads((_CONTRACTS / "agentic-retrieval-run.schema.json").read_text(encoding="utf-8"))
_COMMON_SCHEMA = json.loads((_CONTRACTS / "common.schema.json").read_text(encoding="utf-8"))


class _FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    async def embed_texts(self, texts):
        return [[0.1 * (i + 1)] * self._dim for i, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [0.5] * self._dim

    def get_dimension(self):
        return self._dim


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
    from rag_mcp.agents.context_orchestrator import ContextOrchestratorAgent
    from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    planner = QueryPlannerAgent(model_and_version="persist-v1")
    planner._llm_decompose = lambda q, ctx: [
        {"query": "alpha repository", "signals": ["dense", "sparse"]},
        {"query": "beta repository", "signals": ["dense", "sparse"]},
    ]
    analyst = EvidenceAnalystAgent(model_and_version="persist-v1")
    analyst._llm_judge = lambda ctx: {
        "coverage_state": "covered",
        "conflict_type": "none",
        "uncovered_sub_problem_ids": [],
        "needs_supplementary": False,
        "gap_descriptions": [],
    }
    return planner, analyst, ContextOrchestratorAgent(model_and_version="persist-v1")


async def _make_project(db_session, store, name, chunk_texts):
    """Create a project with published version + chunks + qdrant points."""
    from rag_mcp.schemas.project import ProjectCreate
    from rag_mcp.services.ingestion_service import _derive_index_version
    from rag_mcp.services.project_service import ProjectService

    svc = ProjectService(db_session)
    project = await svc.create_project(
        ProjectCreate(name=name, alias=f"{name.lower().replace(' ', '-')}-{generate_id()}")
    )
    await db_session.commit()

    scope_id = project.knowledge_scope_id
    version_id = generate_id()
    source_id = generate_id()
    index_version = _derive_index_version(get_settings().embedding_model)

    db_session.add(KnowledgeSource(
        source_id=source_id, knowledge_scope_id=scope_id,
        filename=f"{name}.java",
        content_hash=hashlib.sha256(name.encode()).hexdigest(),
        format="java", size_bytes=len(name), status="published",
    ))
    db_session.add(KnowledgeVersion(
        version_id=version_id, knowledge_scope_id=scope_id, version_number=1,
        capabilities={"dense_ready": True, "lexical_ready": True},
        status="published", published_at=None,
    ))
    chunk_ids = []
    texts = []
    for i, (path, content) in enumerate(chunk_texts.items()):
        cid = generate_id()
        chunk_ids.append(cid)
        texts.append(content)
        db_session.add(Chunk(
            chunk_id=cid, source_id=source_id, version_id=version_id,
            knowledge_scope_id=scope_id, content_text=content,
            position_path=path, chunk_type="symbol", start_line=1, end_line=2,
            token_count=8, embedding_model=get_settings().embedding_model,
            index_version=index_version,
        ))
    await db_session.commit()

    collection = f"chunks_hybrid_{index_version}"
    if not store.collection_exists(collection):
        store.create_hybrid_collection(collection, dimension=1024)
    encoder = BM25SparseEncoder()
    encoder.fit(texts)
    for cid, (path, content) in zip(chunk_ids, chunk_texts.items()):
        store.upsert_hybrid(collection, cid, [0.5] * 1024, encoder.encode(content), {
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
        })
    return {"project": project, "scope_id": scope_id, "chunk_ids": chunk_ids}


@pytest_asyncio.fixture
async def persist_setup(db_session: AsyncSession):
    store = QdrantStore()
    setup = await _make_project(db_session, store, "Persist Test", {
        "com.example.P#repo": "private final Repository repository",
        "com.example.P#findA": "public A findA() { return repository.findA(); }",
        "com.example.P#findB": "public B findB() { return repository.findB(); }",
    })
    setup["store"] = store
    yield setup
    collection = f"chunks_hybrid_{setup['index_version'] if 'index_version' in setup else ''}"
    # cleanup best-effort
    from rag_mcp.services.ingestion_service import _derive_index_version
    col = f"chunks_hybrid_{_derive_index_version(get_settings().embedding_model)}"
    if store.collection_exists(col):
        try:
            store.delete_points_by_scope(col, setup["scope_id"])
        except Exception:
            pass


@pytest_asyncio.fixture
async def five_projects(db_session: AsyncSession):
    store = QdrantStore()
    setups = []
    for i in range(5):
        s = await _make_project(db_session, store, f"Conc Proj {i}", {
            f"com.example.C{i}#repo": f"repository field of component {i}",
            f"com.example.C{i}#use": f"component {i} uses repository for data access",
        })
        setups.append(s)
    yield {"setups": setups, "store": store}
    from rag_mcp.services.ingestion_service import _derive_index_version
    col = f"chunks_hybrid_{_derive_index_version(get_settings().embedding_model)}"
    if store.collection_exists(col):
        for s in setups:
            try:
                store.delete_points_by_scope(col, s["scope_id"])
            except Exception:
                pass


async def _run_search(setup, store, query="repository usage"):
    from rag_mcp.orchestration.entry import run_agentic_search

    return await run_agentic_search(
        query=query,
        project_scopes=[str(setup["project"].project_id)],
        top_k=5,
        task_context=None,
        session_factory=_session_factory(),
        qdrant_store=store,
        embedding_provider=_FakeEmbeddingProvider(),
        reranker=None,
        agent_builder=_stub_agent_builder,
        return_record=True,
    )


class TestLedgerPersistence:
    """Every recalled evidence row lands in evidence_ledger_entry."""

    @pytest.mark.asyncio
    async def test_ledger_entries_persisted_with_metadata(self, db_session, persist_setup):
        response, record = await _run_search(persist_setup, persist_setup["store"])
        request_id = response["request_id"]

        rows = (await db_session.execute(sa_text(
            "SELECT round_index, sub_problem_id, evidence_id, retrieval_query, "
            "retriever, score, source_version, knowledge_scope_id, project_id, "
            "index_version, referenced_by_agent, run_id "
            "FROM evidence_ledger_entry WHERE request_id = :rid"
        ), {"rid": request_id})).all()
        assert rows, "no ledger entries persisted for the run"
        for r in rows:
            assert r.round_index >= 0
            assert r.sub_problem_id >= 1
            assert r.retriever in ("dense", "sparse", "graph", "fusion", "rerank")
            assert 0.0 <= float(r.score) <= 1.0
            assert r.source_version >= 1
            assert r.knowledge_scope_id == persist_setup["scope_id"]
            assert r.project_id == persist_setup["project"].project_id
            assert r.index_version >= 1
            assert r.referenced_by_agent in (
                "query_planner", "evidence_analyst", "context_orchestrator",
            )
            assert r.retrieval_query

    @pytest.mark.asyncio
    async def test_bridge_key_resolves_every_returned_evidence(self, db_session, persist_setup):
        """(request_id, evidence_id) resolves the internal ledger (SC-006)."""
        response, record = await _run_search(persist_setup, persist_setup["store"])
        request_id = response["request_id"]
        assert response["evidence"], "expected evidence in response"
        for ev in response["evidence"]:
            rows = (await db_session.execute(sa_text(
                "SELECT retrieval_query, retriever, score, source_version, "
                "round_index, sub_problem_id FROM evidence_ledger_entry "
                "WHERE request_id = :rid AND evidence_id = :eid"
            ), {"rid": request_id, "eid": ev["evidence_id"]})).all()
            assert rows, f"bridge key ({request_id}, {ev['evidence_id']}) unresolved"
            row = rows[0]
            assert row.retriever
            assert row.source_version == ev["source_version"]


class TestJudgmentPersistence:
    @pytest.mark.asyncio
    async def test_judgments_persisted(self, db_session, persist_setup):
        response, record = await _run_search(persist_setup, persist_setup["store"])
        run_id = record["run_id"]
        rows = (await db_session.execute(sa_text(
            "SELECT round_index, coverage_state, conflict_type, needs_supplementary, "
            "model_and_version, schema_valid FROM agent_judgment WHERE run_id = :rid "
            "ORDER BY round_index"
        ), {"rid": str(run_id)})).all()
        assert rows, "no agent judgments persisted"
        for r in rows:
            assert r.coverage_state in ("covered", "partial", "uncovered")
            assert r.conflict_type in ("none", "version_conflict", "source_conflict", "domain_conflict")
            assert r.model_and_version


class TestSelectionPersistence:
    @pytest.mark.asyncio
    async def test_selection_list_persisted_without_ledger_overwrite(self, db_session, persist_setup):
        response, record = await _run_search(persist_setup, persist_setup["store"])
        run_id = str(record["run_id"])

        sel_rows = (await db_session.execute(sa_text(
            "SELECT decision, ledger_entry_id FROM context_selection_list WHERE run_id = :rid"
        ), {"rid": run_id})).all()
        assert sel_rows, "no selection list persisted"
        decisions = {r.decision for r in sel_rows}
        assert decisions <= {"selected", "truncated", "deduped"}
        assert "selected" in decisions

        # Ledger entries referenced by selections exist and are append-only
        # (original rows still present with their original scores)
        for r in sel_rows:
            ledger = (await db_session.execute(sa_text(
                "SELECT count(*) FROM evidence_ledger_entry WHERE ledger_entry_id = :lid"
            ), {"lid": r.ledger_entry_id})).scalar()
            assert ledger == 1, "ledger entry missing or duplicated (append-only violated)"


class TestRunRecordPersistence:
    @pytest.mark.asyncio
    async def test_run_record_persisted_schema_conformant(self, db_session, persist_setup):
        response, record = await _run_search(persist_setup, persist_setup["store"])
        run_id = str(record["run_id"])

        rows = (await db_session.execute(sa_text(
            "SELECT request_id, completion_status, max_rounds, rounds_completed, "
            "sub_path_timings, agent_outputs_ref, ledger_ref, schema_valid_all "
            "FROM agentic_retrieval_run WHERE run_id = :rid"
        ), {"rid": int(run_id)})).all()
        assert rows, "agentic_retrieval_run row missing"
        row = rows[0]
        assert row.request_id == response["request_id"]
        assert row.completion_status in ("complete", "partial", "no_evidence", "failed")
        assert row.rounds_completed >= 1

        # Full run record (in-memory) conforms to the contract schema
        registry = {
            _RUN_SCHEMA["$id"]: _RUN_SCHEMA,
            "https://ai-engineering-rag-mcp.local/schemas/005/common.schema.json": _COMMON_SCHEMA,
        }
        resolver = RefResolver.from_schema(_RUN_SCHEMA, store=registry)
        validator = Draft202012Validator(_RUN_SCHEMA, resolver=resolver)
        errors = list(validator.iter_errors(record))
        assert not errors, f"run record schema violations: {[e.message for e in errors][:5]}"

        # ledger_ref in the persisted record carries real entry ids
        assert record["ledger_ref"]["ledger_entry_ids"], "ledger_ref must carry entry ids"


class TestConcurrencyIsolation:
    @pytest.mark.asyncio
    async def test_five_concurrent_runs_no_crosstalk(self, db_session, five_projects):
        """5 concurrent requests, different scopes -> no persistence crosstalk."""
        store = five_projects["store"]
        setups = five_projects["setups"]

        async def one(setup):
            return await _run_search(setup, store, query=f"repository usage")

        results = await asyncio.gather(*[one(s) for s in setups])

        for setup, (response, record) in zip(setups, results):
            request_id = response["request_id"]
            rows = (await db_session.execute(sa_text(
                "SELECT knowledge_scope_id, project_id FROM evidence_ledger_entry "
                "WHERE request_id = :rid"
            ), {"rid": request_id})).all()
            assert rows, f"no ledger rows for concurrent request {request_id}"
            for r in rows:
                assert r.knowledge_scope_id == setup["scope_id"], "cross-scope ledger crosstalk"
                assert r.project_id == setup["project"].project_id, "cross-project crosstalk"
            # judgments for this run only
            judgment_rows = (await db_session.execute(sa_text(
                "SELECT count(*) FROM agent_judgment WHERE run_id = :rid"
            ), {"rid": str(record["run_id"])})).scalar()
            assert judgment_rows >= 1
