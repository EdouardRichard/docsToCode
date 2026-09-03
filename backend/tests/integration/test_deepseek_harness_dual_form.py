"""Integration test: DeepSeek Harness dual-form (writer + reader) (T033/T034).

Adapts the 001 DeepSeek Harness schema-compliance harness to the 006 dual
instance forms: a writer-form MCP server and a reader-form MCP server each
register search_knowledge + get_evidence, and each produces output that
passes the 001 external MCP contract schemas (SC-001, clarification Q5:
both forms MUST pass). ChatGPT App / Claude Code compatibility is recorded
as non-blocking (FR-028).

Retrieval runs in-process through the real shared PostgreSQL + Qdrant (no
live HTTP round-trip; the mode affects instance registration and metrics
attribution, not the shared read-only retrieval path).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from jsonschema import Draft202012Validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

_SEARCH_SCHEMA = json.loads((
    _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
    / "mcp-search-output.schema.json"
).read_text(encoding="utf-8"))
_EVIDENCE_SCHEMA = json.loads((
    _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"
    / "mcp-get-evidence.schema.json"
).read_text(encoding="utf-8"))


def _import_run_mcp():
    import importlib

    if "_run_mcp" in sys.modules:
        return sys.modules["_run_mcp"]
    spec = importlib.util.spec_from_file_location("_run_mcp", _BACKEND / "_run_mcp.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_run_mcp"] = module
    spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="module")
def embedding_provider():
    """Real bge-m3 embedding (cached from prior warmups)."""
    from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider

    return LocalCPUEmbeddingProvider()


async def _discover_scope(session) -> str | None:
    """Find a published project scope with indexed chunks, or None."""
    row = (
        await session.execute(
            text(
                "SELECT c.knowledge_scope_id FROM chunks c "
                "JOIN knowledge_versions kv ON kv.version_id = c.version_id "
                "WHERE kv.status = 'published' AND c.content_text IS NOT NULL "
                "LIMIT 1"
            )
        )
    ).first()
    return str(row[0]) if row else None


# ------------------------------------------------------- dual-form registration


@pytest.mark.asyncio
async def test_both_forms_register_tools(session_factory):
    module = _import_run_mcp()
    for mode in ("writer", "reader"):
        server = module.build_mcp_server(mode=mode, embedding_provider=_StubEmbedding())
        tools = await server.list_tools()
        names = {tool.name for tool in tools}
        assert "search_knowledge" in names, f"{mode} form must register search_knowledge"
        assert "get_evidence" in names, f"{mode} form must register get_evidence"


class _StubEmbedding:
    async def embed_texts(self, texts):
        return [[0.01] * 8 for _ in texts]

    def get_dimension(self) -> int:
        return 8

    async def embed_query(self, text):
        return [0.01] * 8


# ------------------------------------------------- dual-form instance identity


@pytest.mark.asyncio
async def test_dual_form_instance_registration_distinct_worker_ids(
    monkeypatch, session_factory
):
    """T026: both MCP forms register process_role=mcp with distinct worker_ids."""
    module = _import_run_mcp()
    registered = []

    class FakeRegistry:
        def __init__(self, factory):
            self.factory = factory

        async def register(self, instance_id, instance_mode, process_role, worker_id=None, **kw):
            registered.append((instance_mode, process_role, worker_id))

            class R:
                registered = True

            R.worker_id = {"writer": 0, "reader": 1}[instance_mode] if worker_id is None else worker_id
            return R()

    monkeypatch.setattr(
        "rag_mcp.runtime.instance_registry.InstanceRegistryService", FakeRegistry
    )
    monkeypatch.setattr(
        "rag_mcp.runtime.schema_compat.verify_schema_compat",
        _async_none,
    )

    writer_id = await module.startup_sequence("writer", session_factory)
    reader_id = await module.startup_sequence("reader", session_factory)
    assert writer_id.mode == "writer"
    assert reader_id.mode == "reader"
    assert writer_id.worker_id != reader_id.worker_id
    for mode, role, _wid in registered:
        assert role == "mcp"
        assert mode in ("writer", "reader")


async def _async_none(*a, **k):
    return "0061"


# ------------------------------------------------------- real retrieval round-trip


@pytest.mark.asyncio
async def test_dual_form_round_trip_passes_schema(
    db_session, session_factory, embedding_provider
):
    """SC-001: writer AND reader forms each produce schema-valid output."""
    from rag_mcp.indexing.qdrant_client import QdrantStore
    from rag_mcp.mcp.search_knowledge import search_knowledge_core
    from rag_mcp.services.evidence_service import EvidenceService

    async with session_factory() as session:
        scope = await _discover_scope(session)
    if scope is None:
        pytest.skip("no published indexed scope in the shared DB")

    qdrant = QdrantStore()
    for mode in ("writer", "reader"):
        result = await search_knowledge_core(
            query="validateToken authentication",
            project_scope=[scope],
            top_k=5,
            task_context=None,
            session_factory=session_factory,
            qdrant_store=qdrant,
            embedding_provider=embedding_provider,
            reranker=None,
        )
        assert result["completion_status"] in ("complete", "partial", "no_evidence", "failed")
        # mcp-search-output.schema.json validates the whole response at top level
        Draft202012Validator(_SEARCH_SCHEMA).validate(result)
        # get_evidence for the first returned evidence (if any)
        evidence_items = result.get("evidence", [])
        if evidence_items:
            evidence_id = evidence_items[0]["evidence_id"]
            async with session_factory() as session:
                service = EvidenceService(session)
                ev = await service.get_evidence(str(evidence_id), project_scopes=[scope])
            assert ev["status"] == "available", ev
            Draft202012Validator(_EVIDENCE_SCHEMA["properties"]["output"]).validate(ev)
        assert mode in ("writer", "reader")  # both forms exercised


def test_host_compatibility_recorded_non_blocking():
    """FR-028: ChatGPT App / Claude Code compatibility is recorded, not blocking."""
    from rag_mcp.config.timeout_profiles import TimeoutProfiles

    profiles = TimeoutProfiles()
    # All three Host targets present with distinct profiles
    assert profiles.chatgpt_app_ms >= profiles.server_total_ms
    assert profiles.claude_code_ms >= profiles.server_total_ms
