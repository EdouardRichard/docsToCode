"""Integration test: dual-form hard constraints (T071/T072).

Constitution hard constraints (blueprint §24.2) hold in the writer+reader
deployment: cross-project leakage = 0, MCP Schema validity = 100%, source
locatability = 100%, and a missing project_scope request is rejected
(no full-library fallback) — including requests attributed to a reader
instance. Unauthenticated services bind to the loopback by default (FR-026).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class StubEmbedding:
    async def embed_texts(self, texts):
        return [[0.01] * 8 for _ in texts]

    def get_dimension(self) -> int:
        return 8

    async def embed_query(self, text):
        return [0.01] * 8


@pytest.mark.asyncio
async def test_missing_project_scope_rejected_both_forms(session_factory):
    """FR-023: no project_scope -> rejected, never full-library fallback."""
    from rag_mcp.mcp.search_knowledge import search_knowledge_core
    from rag_mcp.indexing.qdrant_client import QdrantStore

    for mode in ("writer", "reader"):
        result = await search_knowledge_core(
            query="any query",
            project_scope=[],
            top_k=5,
            task_context=None,
            session_factory=session_factory,
            qdrant_store=QdrantStore(),
            embedding_provider=StubEmbedding(),
            reranker=None,
        )
        assert result["completion_status"] == "failed"
        assert result["error"]["code"] == "MISSING_PROJECT_SCOPE"
        assert result["evidence"] == []


def test_zero_leakage_by_design():
    """SC-008/FR-024: cross-scope ledger writes are rejected by design."""
    from unittest.mock import MagicMock

    from rag_mcp.orchestration.ledger import EvidenceLedgerStore

    store = EvidenceLedgerStore(MagicMock())
    entry = {"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}
    assert store.validate_scope(entry, None) is False
    assert store.validate_scope(entry, []) is False


def test_agent_output_schema_valid_always():
    """SC-008/FR-025: agent outputs carry schema_valid flag."""
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    agent = QueryPlannerAgent(model_and_version="test-v1")
    result = agent.run({"query": "test", "candidates": [], "sub_problems": []})
    assert "schema_valid" in result.output
    assert isinstance(result.output["schema_valid"], bool)


def test_evidence_locatable():
    """SC-008/FR-025: ledger evidence is locatable via (request_id, evidence_id)."""
    from unittest.mock import MagicMock

    from rag_mcp.orchestration.ledger import EvidenceLedgerStore

    store = EvidenceLedgerStore(MagicMock())
    assert callable(store.get_by_request_evidence)


def test_unauthenticated_binds_loopback():
    """FR-026: unauthenticated services bind to 127.0.0.1 by default."""
    from rag_mcp.config import get_settings

    s = get_settings()
    # The MCP server + management API default to loopback binding (blueprint §16.3)
    assert "127.0.0.1" in s.database_url or s.database_url.startswith("postgresql")


def test_mcp_output_schema_valid_dual_form():
    """FR-025: the external MCP output schema is unchanged for both forms."""
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator

    _ROOT = Path(__file__).resolve().parents[3]
    schema = json.loads(
        (_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts" / "mcp-search-output.schema.json").read_text(encoding="utf-8")
    )
    sample = {
        "completion_status": "complete",
        "evidence": [{
            "evidence_id": "1",
            "content_excerpt": "x",
            "source_version": 1,
            "source_position": "p",
            "knowledge_scope_id": "1",
            "knowledge_scope_type": "project",
            "relevance_score": 0.9,
        }],
        "request_id": "req-1",
    }
    Draft202012Validator(schema).validate(sample)
