"""Unit test for graph guardrail config defaults (T002).

Validates that the runtime configuration exposes graph-retrieval guardrails
with the expected default values (research §2/§3, data-model, FR-017).

This test MUST FAIL before the graph config is registered in config.py (TDD).
"""

from __future__ import annotations

from rag_mcp.config import Settings


def test_graph_config_exists():
    """Settings must expose a graph sub-config."""
    s = Settings()
    assert hasattr(s, "graph"), "Settings must have a 'graph' sub-config"
    assert s.graph is not None


def test_graph_config_defaults():
    """Graph config defaults must match research §3 / FR-017."""
    s = Settings()
    g = s.graph
    assert g.hop_default == 2
    assert g.hop_max == 3
    assert g.candidate_budget == 10
    assert g.candidate_budget_max == 20
    assert g.graph_sub_timeout_ms == 3000
    assert g.total_timeout_ms == 30000
    assert g.direction_default == "bidirectional"
    assert g.structure_weight_hard == 1.0
    assert g.structure_weight_soft == 0.3
    assert g.structure_weight_hop_decay == 0.5
    assert g.soft_confidence_threshold == 0.6


def test_graph_config_env_override():
    """Graph config should be overridable via environment variables."""
    import os
    os.environ["GRAPH_HOP_DEFAULT"] = "3"
    try:
        s = Settings()
        assert s.graph.hop_default == 3
    finally:
        del os.environ["GRAPH_HOP_DEFAULT"]
