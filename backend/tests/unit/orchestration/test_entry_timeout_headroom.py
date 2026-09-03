"""Tests for the degradation headroom budget (T071, SC-007).

SC-007: the agentic P95 latency MUST stay within the server 30s total
timeout guardrail. A degraded request pays "orchestration timeout + a
deterministic fallback", so the orchestration budget must RESERVE
headroom inside the 30s guardrail (default 2s): orchestration is
cancelled at ~28s and the fallback (~0.3-0.7s) finishes well under 30s.

This test MUST FAIL before the headroom helper exists (TDD Red).
"""

from __future__ import annotations

import pytest

from rag_mcp.orchestration import entry


class TestOrchestrationBudget:
    def test_default_headroom_2000ms(self):
        assert entry.orchestration_budget_ms(30_000) == 28_000

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEGRADATION_HEADROOM_MS", "5000")
        assert entry.orchestration_budget_ms(30_000) == 25_000

    def test_budget_floor(self, monkeypatch):
        # A pathological headroom must never yield a zero/negative budget
        monkeypatch.setenv("AGENTIC_DEGRADATION_HEADROOM_MS", "60000")
        assert entry.orchestration_budget_ms(30_000) >= 5_000

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_DEGRADATION_HEADROOM_MS", "not-a-number")
        assert entry.orchestration_budget_ms(30_000) == 28_000

    def test_headroom_helper_nonnegative(self):
        assert entry.degradation_headroom_ms() >= 0
