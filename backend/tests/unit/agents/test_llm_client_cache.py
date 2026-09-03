"""Tests for the LLM response cache (T070, SC-008).

The cache makes the Agent orchestration path reproducible across two
same-session evaluation passes (SC-008): the first pass records real LLM
responses to a disk cache keyed by (model, system prompt, user payload);
the second pass replays them byte-identically without any HTTP call.

Contract (T070):
  - opt-in via cache_dir (wired from AGENTIC_LLM_CACHE_PATH by the router)
  - identical (system, payload) -> cache hit, no second HTTP call
  - different payload -> separate cache entry
  - LLM failures are cached too (record-replay: the eval session replays
    both successes and failures so pass 2 == pass 1 exactly)
  - failures/errors in cache IO never break the call (best-effort cache)
  - cache hits do not count toward prompt/completion chars (cost stays
    real: only actual HTTP usage is accounted, SC-007)

This test MUST FAIL before the cache exists (TDD Red).
"""

from __future__ import annotations

import httpx
import pytest

from rag_mcp.agents.llm_client import LLMClient


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture()
def http_recorder(monkeypatch):
    """Patch httpx.Client.post to record calls and return canned payloads."""
    calls: list[dict] = []

    def fake_post(self, url, json=None, headers=None):  # noqa: A002
        calls.append({"url": url, "body": json})
        return _FakeResponse(_chat_payload('{"sub_problems": [], "schema_valid": true}'))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    return calls


class TestLLMResponseCache:
    def test_cache_hit_avoids_second_http_call(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        r1 = client.chat_json("system-prompt", {"query": "q1"})
        r2 = client.chat_json("system-prompt", {"query": "q1"})

        assert r1 is not None and r1 == {"sub_problems": [], "schema_valid": True}
        assert r2 == r1
        assert len(http_recorder) == 1, "second identical call must be a cache hit"
        assert client.cache_hits == 1
        assert client.cache_misses == 1

    def test_cache_hit_does_not_count_usage_chars(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        client.chat_json("system-prompt", {"query": "q1"})
        chars_after_miss = client.prompt_chars + client.completion_chars
        assert chars_after_miss > 0, "real (miss) call must count usage"

        client.chat_json("system-prompt", {"query": "q1"})
        assert client.prompt_chars + client.completion_chars == chars_after_miss, (
            "cache hits must not add usage chars (cost stays real, SC-007)"
        )

    def test_different_payload_is_separate_entry(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        client.chat_json("system-prompt", {"query": "q1"})
        client.chat_json("system-prompt", {"query": "q2"})

        assert len(http_recorder) == 2, "different payloads are different keys"

    def test_cache_key_includes_system_prompt(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        client.chat_json("system-A", {"query": "q1"})
        client.chat_json("system-B", {"query": "q1"})

        assert len(http_recorder) == 2, "system prompt participates in the key"

    def test_failures_are_cached_and_replayed(self, tmp_path, monkeypatch):
        calls: list[dict] = []

        def failing_post(self, url, json=None, headers=None):  # noqa: A002
            calls.append({"url": url})
            raise httpx.ConnectError("boom")

        monkeypatch.setattr(httpx.Client, "post", failing_post)
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        r1 = client.chat_json("system-prompt", {"query": "q1"})
        r2 = client.chat_json("system-prompt", {"query": "q1"})

        assert r1 is None and r2 is None
        assert len(calls) == 1, "failure must be replayed from cache, not re-called"

    def test_corrupt_cache_entry_treated_as_miss(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0, cache_dir=str(tmp_path),
        )
        client.chat_json("system-prompt", {"query": "q1"})
        assert len(http_recorder) == 1

        # Corrupt the single cache entry the first call wrote
        entries = list(tmp_path.glob("*.json"))
        assert len(entries) == 1
        entries[0].write_text("NOT JSON", encoding="utf-8")

        r = client.chat_json("system-prompt", {"query": "q1"})
        assert r is not None, "corrupt cache entry falls back to a real call"
        assert len(http_recorder) == 2

    def test_no_cache_dir_disables_caching(self, tmp_path, http_recorder):
        client = LLMClient(
            base_url="http://llm.test", api_key="k", model="m",
            timeout_s=5.0,
        )
        client.chat_json("system-prompt", {"query": "q1"})
        client.chat_json("system-prompt", {"query": "q1"})

        assert len(http_recorder) == 2, "without cache_dir every call is real"
        assert client.cache_hits == 0


class TestRouterCacheWiring:
    def test_router_passes_cache_dir_to_client(self, tmp_path, monkeypatch):
        from rag_mcp.agents.capability_router import CapabilityRouter

        monkeypatch.setenv("AGENTIC_LLM_CACHE_PATH", str(tmp_path))
        router = CapabilityRouter(
            default_model="m", llm_base_url="http://llm.test", llm_api_key="k",
        )
        client = router.create_client("query_planner")
        assert getattr(client, "_cache_dir", None) == str(tmp_path)

    def test_router_without_env_has_no_cache(self, monkeypatch):
        from rag_mcp.agents.capability_router import CapabilityRouter

        monkeypatch.delenv("AGENTIC_LLM_CACHE_PATH", raising=False)
        router = CapabilityRouter(
            default_model="m", llm_base_url="http://llm.test", llm_api_key="k",
        )
        client = router.create_client("query_planner")
        assert getattr(client, "_cache_dir", None) in (None, "")
