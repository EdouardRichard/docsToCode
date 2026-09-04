"""Provider usage accounting per retrieval request (006, T056).

FR-016: provider_usage accumulates embedding / rerank / llm call counts and
character volumes over one request and is written with the run record. The
LLM accounting mirrors the 005 real-call contract (SC-007): cache hits are
NOT counted — only real HTTP calls and their prompt/completion characters
contribute.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProviderUsageAccumulator:
    """In-memory per-request provider usage counter (requests are isolated)."""

    embedding_calls: int = 0
    rerank_calls: int = 0
    llm_calls: int = 0
    llm_prompt_chars: int = 0
    llm_completion_chars: int = 0

    def record_embedding(self, count: int = 1) -> None:
        self.embedding_calls += max(0, count)

    def record_rerank(self, count: int = 1) -> None:
        self.rerank_calls += max(0, count)

    # LLM usage is not accumulated here: the deterministic path makes no LLM
    # calls, and the 005 agentic path records its LLM usage via
    # AgenticStateMachine.get_llm_usage() -> get_provider_usage() (real-call
    # only, cache hits excluded). The llm_* fields below remain for the
    # provider_usage schema and stay 0 for the deterministic path (T088).

    def to_dict(self) -> dict[str, int]:
        return {
            "embedding_calls": self.embedding_calls,
            "rerank_calls": self.rerank_calls,
            "llm_calls": self.llm_calls,
            "llm_prompt_chars": self.llm_prompt_chars,
            "llm_completion_chars": self.llm_completion_chars,
        }
