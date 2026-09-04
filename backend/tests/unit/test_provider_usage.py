"""Unit tests for provider_usage recording (T055, RED first).

FR-016: provider_usage accumulates embedding / rerank call counts over the
request. LLM usage is recorded by the 005 agentic path via its own
get_llm_usage() accounting (real-call only, cache hits excluded) and the
llm_* fields here stay 0 for the deterministic path (T088 removed the
dead record_llm method).
"""

from __future__ import annotations

from rag_mcp.services.provider_usage import ProviderUsageAccumulator


def test_defaults_all_zero() -> None:
    d = ProviderUsageAccumulator().to_dict()
    assert d == {
        "embedding_calls": 0,
        "rerank_calls": 0,
        "llm_calls": 0,
        "llm_prompt_chars": 0,
        "llm_completion_chars": 0,
    }


def test_accumulate_embedding_and_rerank() -> None:
    a = ProviderUsageAccumulator()
    a.record_embedding(2)
    a.record_rerank(1)
    d = a.to_dict()
    assert d["embedding_calls"] == 2
    assert d["rerank_calls"] == 1
    # llm fields remain 0: deterministic path makes no LLM calls
    assert d["llm_calls"] == 0


def test_cumulative_embedding_records() -> None:
    a = ProviderUsageAccumulator()
    a.record_embedding(1)
    a.record_embedding(3)
    a.record_rerank(2)
    d = a.to_dict()
    assert d["embedding_calls"] == 4
    assert d["rerank_calls"] == 2


def test_llm_usage_removed_from_accumulator() -> None:
    """T088: record_llm was dead code; the accumulator must not expose it."""
    assert not hasattr(ProviderUsageAccumulator(), "record_llm")