"""Unit tests for provider_usage recording (T055, RED first).

FR-016: provider_usage accumulates embedding / rerank / llm call counts and
character volumes over the request, matching the 005 real-call accounting —
LLM cache hits are NOT counted (SC-007).
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


def test_accumulate_calls_and_chars() -> None:
    a = ProviderUsageAccumulator()
    a.record_embedding(2)
    a.record_rerank(1)
    a.record_llm(calls=1, prompt_chars=10, completion_chars=5)
    d = a.to_dict()
    assert d["embedding_calls"] == 2
    assert d["rerank_calls"] == 1
    assert d["llm_calls"] == 1
    assert d["llm_prompt_chars"] == 10
    assert d["llm_completion_chars"] == 5


def test_llm_cache_hit_not_counted() -> None:
    """LLM cache hits never count toward usage (005 SC-007/SC-008)."""
    a = ProviderUsageAccumulator()
    a.record_llm(calls=1, prompt_chars=99, completion_chars=88, cache_hit=True)
    d = a.to_dict()
    assert d["llm_calls"] == 0
    assert d["llm_prompt_chars"] == 0
    assert d["llm_completion_chars"] == 0


def test_cumulative_across_multiple_records() -> None:
    a = ProviderUsageAccumulator()
    a.record_embedding(1)
    a.record_embedding(3)
    a.record_llm(calls=1, prompt_chars=4, completion_chars=2)
    a.record_llm(calls=1, prompt_chars=6, completion_chars=3)
    d = a.to_dict()
    assert d["embedding_calls"] == 4
    assert d["llm_calls"] == 2
    assert d["llm_prompt_chars"] == 10
    assert d["llm_completion_chars"] == 5
