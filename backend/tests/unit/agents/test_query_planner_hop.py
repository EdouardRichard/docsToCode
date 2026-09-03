"""Tests for query planner hop-limit selection (T067 Red).

The planner MAY choose a graph expansion hop limit within the 004 guardrail
band (1..3); the choice passes Schema validation; invalid or missing values
fall back to the 004 default hop 2 (FR-033). The contract addition is
backward compatible (Constitution VII).

This test MUST FAIL before the hop field exists (TDD Red).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_REPO_ROOT = Path(__file__).resolve().parents[4]
_RUN_SCHEMA_PATH = (
    _REPO_ROOT / "specs" / "005-agentic-retrieval-orchestration" / "contracts"
    / "agentic-retrieval-run.schema.json"
)


def _planner_with_llm_payload(payload):
    from rag_mcp.agents.query_planner import QueryPlannerAgent

    planner = QueryPlannerAgent(model_and_version="hop-v1")
    planner._llm_client = object()  # any non-None client
    planner._llm_decompose = lambda q, ctx: payload
    return planner


class TestHopSelection:
    def test_valid_hop_is_kept(self):
        planner = _planner_with_llm_payload([
            {"query": "hop q", "signals": ["graph"], "graph_hop": 3,
             "relation_directions": ["calls", "called_by"]},
        ])
        out = planner.execute({"query": "multi-hop"})
        sp = out["sub_problems"][0]
        assert sp["graph_hop"] == 3
        result = planner.validate_output(out)
        assert result.schema_valid is True

    def test_hop_bounds(self):
        planner = _planner_with_llm_payload([
            {"query": "hop q", "signals": ["graph"], "graph_hop": 1},
        ])
        sp = planner.execute({"query": "q"})["sub_problems"][0]
        assert sp["graph_hop"] == 1

    def test_invalid_hop_falls_back_to_default_2(self):
        planner = _planner_with_llm_payload([
            {"query": "hop q", "signals": ["graph"], "graph_hop": 7},
        ])
        sp = planner.execute({"query": "q"})["sub_problems"][0]
        assert sp["graph_hop"] == 2, "out-of-band hop must fall back to 004 default 2"

    def test_non_numeric_hop_falls_back_to_default_2(self):
        planner = _planner_with_llm_payload([
            {"query": "hop q", "signals": ["graph"], "graph_hop": "abc"},
        ])
        sp = planner.execute({"query": "q"})["sub_problems"][0]
        assert sp["graph_hop"] == 2

    def test_missing_hop_omitted_for_graph_signal(self):
        planner = _planner_with_llm_payload([
            {"query": "hop q", "signals": ["graph"]},
        ])
        out = planner.execute({"query": "q"})
        sp = out["sub_problems"][0]
        # Missing -> deterministic default 2 (FR-033)
        assert sp.get("graph_hop", 2) == 2
        assert planner.validate_output(out).schema_valid is True

    def test_no_hop_for_non_graph_signals(self):
        planner = _planner_with_llm_payload([
            {"query": "dense q", "signals": ["dense"], "graph_hop": 3},
        ])
        sp = planner.execute({"query": "q"})["sub_problems"][0]
        assert "graph_hop" not in sp


class TestContractBackwardCompat:
    def test_run_schema_accepts_graph_hop(self):
        schema = json.loads(_RUN_SCHEMA_PATH.read_text(encoding="utf-8"))
        items = (
            schema["properties"]["agent_outputs_ref"]["properties"]
            ["query_planner"]["properties"]["sub_problems"]["items"]
        )
        assert "graph_hop" in items["properties"], "contract must carry graph_hop (T067)"
        hop = items["properties"]["graph_hop"]
        assert hop.get("minimum") == 1
        assert hop.get("maximum") == 3

    def test_node_schema_validates_hop_output(self):
        from rag_mcp.agents.query_planner import NODE_SCHEMA

        validator = Draft202012Validator(NODE_SCHEMA)
        output = {
            "sub_problems": [{
                "sub_problem_id": 1,
                "query": "q",
                "signals": ["graph"],
                "graph_hop": 3,
                "relation_directions": ["calls"],
            }],
            "schema_valid": True,
        }
        assert not list(validator.iter_errors(output))

    def test_old_output_without_hop_still_valid(self):
        from rag_mcp.agents.query_planner import NODE_SCHEMA

        validator = Draft202012Validator(NODE_SCHEMA)
        output = {
            "sub_problems": [{
                "sub_problem_id": 1,
                "query": "q",
                "signals": ["dense"],
            }],
            "schema_valid": True,
        }
        assert not list(validator.iter_errors(output)), "backward compatible"
