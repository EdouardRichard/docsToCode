"""Unit tests for agentic runtime-metrics wiring (T083/T085, RED first).

FR-016: the agentic path must contribute LLM call counts + prompt/completion
chars (from the wired agents' clients) and embedding/rerank counts (from the
retrieval pipeline) to provider_usage, and a successful agentic request must
write a retrieval_runs row (retrieval_mode='agentic') so request totals /
status / latency / provider-usage aggregation covers the agentic form.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from rag_mcp.models import RetrievalRun
from rag_mcp.orchestration.state_machine import AgenticStateMachine


class _Client:
    def __init__(self, calls=0, prompt_chars=0, completion_chars=0):
        self.calls = calls
        self.prompt_chars = prompt_chars
        self.completion_chars = completion_chars


class _Agent:
    def __init__(self, client=None):
        self._llm_client = client


class _Pipeline:
    def __init__(self, embedding_calls=0, rerank_calls=0):
        self._embedding_calls = embedding_calls
        self._rerank_calls = rerank_calls

    def get_provider_usage(self):
        return {"embedding_calls": self._embedding_calls, "rerank_calls": self._rerank_calls}


def _make_machine():
    return AgenticStateMachine(
        run_id="1",
        request_id="req-1",
        project_scope=["100"],
        knowledge_scope_ids=["1"],
    )


def test_get_llm_usage_sums_clients():
    machine = _make_machine()
    machine.set_query_planner(_Agent(_Client(calls=2, prompt_chars=10, completion_chars=5)))
    machine.set_evidence_analyst(_Agent(_Client(calls=1, prompt_chars=3, completion_chars=2)))
    assert machine.get_llm_usage() == {
        "llm_calls": 3,
        "llm_prompt_chars": 13,
        "llm_completion_chars": 7,
    }


def test_get_provider_usage_merges_pipeline_and_llm():
    machine = _make_machine()
    machine.set_retrieval_pipeline(_Pipeline(embedding_calls=4, rerank_calls=2))
    machine.set_query_planner(_Agent(_Client(calls=1, prompt_chars=6, completion_chars=4)))
    usage = machine.get_provider_usage()
    assert usage["embedding_calls"] == 4
    assert usage["rerank_calls"] == 2
    assert usage["llm_calls"] == 1
    assert usage["llm_prompt_chars"] == 6
    assert usage["llm_completion_chars"] == 4


def test_get_provider_usage_defaults_zero_without_wiring():
    machine = _make_machine()
    assert machine.get_provider_usage() == {
        "embedding_calls": 0,
        "rerank_calls": 0,
        "llm_calls": 0,
        "llm_prompt_chars": 0,
        "llm_completion_chars": 0,
    }


class _RecordMachine:
    def get_failed_paths(self):
        return ["dense"]

    def get_subpath_timings(self):
        return {"dense_ms": 10, "rerank_ms": 5}

    def get_provider_usage(self):
        return {
            "embedding_calls": 1,
            "rerank_calls": 1,
            "llm_calls": 2,
            "llm_prompt_chars": 20,
            "llm_completion_chars": 10,
        }


@pytest.mark.asyncio
async def test_record_agentic_retrieval_run_writes_row(db_session):
    from rag_mcp.orchestration.entry import _record_agentic_retrieval_run

    machine = _RecordMachine()
    response = {
        "completion_status": "partial",
        "evidence": [{"evidence_id": "111"}, {"evidence_id": "222"}],
    }
    record = {"completion_status": "partial"}

    await _record_agentic_retrieval_run(
        db_session,
        machine=machine,
        record=record,
        response=response,
        query="secret query",
        project_scopes=["100"],
        duration_ms=42,
    )
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(RetrievalRun).where(RetrievalRun.retrieval_mode == "agentic")
        )
    ).scalars().all()
    assert rows, "expected an agentic retrieval_runs row"
    run = rows[-1]
    assert run.tool == "search_knowledge"
    assert run.completion_status == "partial"
    assert run.evidence_count == 2
    assert run.evidence_ref_ids == ["111", "222"]
    assert run.provider_usage["llm_calls"] == 2
    assert run.provider_usage["llm_prompt_chars"] == 20
    assert run.provider_usage["llm_completion_chars"] == 10
    assert run.provider_usage["embedding_calls"] == 1
    assert run.error_summary["code"] == "PARTIAL_PATHS_FAILED"
    assert run.error_summary["failed_paths"] == ["dense"]
    await db_session.execute(delete(RetrievalRun).where(RetrievalRun.run_id == run.run_id))
    await db_session.commit()
