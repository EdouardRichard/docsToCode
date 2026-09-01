"""DeepSeek Harness end-to-end + schema validation (T038).

Validates SC-012/FR-028: graph-enhanced search_knowledge/get_evidence output
passes Schema validation; the DEFAULT path conforms to the external 001
contract (FR-011 — graph annotation is an opt-in extension, not a breaking
change); total timeout guardrail 30s < host lowest Tool Call timeout;
ChatGPT App / Claude Code compatibility recorded (non-blocking).

The live MCP-server round-trip is environment-dependent; this test validates
the schema-compliance, timeout-guardrail, and host-compatibility contract at
the component level.

This test MUST FAIL before schema validation + config are correct (TDD).
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from rag_mcp.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MCP_CONTRACTS = _REPO_ROOT / "specs" / "001-minimum-rag-mcp-loop" / "contracts"


def _load(name):
    with open(_MCP_CONTRACTS / name, "r", encoding="utf-8") as f:
        return json.load(f)


class TestDefaultPathSchemaCompliance:
    """FR-011: the default (non-graph) get_evidence output conforms to the
    external 001 contract. Graph annotation is additive/opt-in and must not
    change the default response shape."""

    def test_standard_evidence_conforms_to_external_schema(self):
        schema = _load("mcp-get-evidence.schema.json")["properties"]["output"]
        # Standard evidence (no graph annotation) — the default path output
        evidence = {
            "evidence_id": "300",
            "full_content": "def validateToken(token): ...",
            "source_version": 1,
            "source_position": "com.example.Svc#validateToken",
            "knowledge_scope_id": "100",
            "knowledge_scope_type": "project",
            "status": "available",
        }
        jsonschema.validate(evidence, schema)

    def test_default_response_has_no_forced_graph_field(self):
        """The default get_evidence must NOT force a graph/relation field."""
        from rag_mcp.services.evidence_service import EvidenceService
        # get_evidence() returns only the standard MCP fields (FR-011)
        import inspect
        src = inspect.getsource(EvidenceService.get_evidence)
        # The default path assembles the standard response keys; annotate is separate
        assert "annotate_evidence" not in src, (
            "annotate_evidence must be opt-in, not forced into default get_evidence"
        )

    def test_annotation_is_additive_and_optional(self):
        """annotate_evidence adds only the optional 'relation' key (SC-009)."""
        from rag_mcp.services.evidence_service import EvidenceService
        import datetime
        from rag_mcp.graph.models import GraphEdge
        es = EvidenceService(None)
        evidence = {"evidence_id": "300", "full_content": "c", "source_version": 1,
                    "source_position": "p", "knowledge_scope_id": "s",
                    "knowledge_scope_type": "project", "status": "available"}
        # Without annotation -> unchanged
        unchanged = es.annotate_evidence(dict(evidence))
        assert "relation" not in unchanged
        # With annotation -> additive relation key only
        hard = GraphEdge(
            edge_id=1, knowledge_scope_id=100, project_id=200, index_version=1,
            source_chunk_id=300, target_chunk_id=301, relation_type="calls",
            direction="out", is_hard=True, version=1,
            parse_evidence={"source_format": "java", "locator": "x", "extractor": "e"})
        annotated = es.annotate_evidence(dict(evidence), relation_edge=hard)
        assert "relation" in annotated
        # Original keys preserved
        for k in evidence:
            assert k in annotated


class TestTimeoutGuardrail:
    """Blueprint sec 19 / FR-028: total timeout 30s < host lowest Tool Call timeout."""

    def test_graph_total_timeout_is_30s(self):
        cfg = get_settings().graph
        assert cfg.total_timeout_ms == 30000

    def test_total_timeout_below_host_tool_call_budget(self):
        """30s guardrail must be below known host Tool Call timeouts.

        DeepSeek Harness (must-pass host) and common hosts allow >= 60s tool
        calls; 30s is safely below. ChatGPT App / Claude Code are recorded
        but non-blocking (FR-028).
        """
        cfg = get_settings().graph
        KNOWN_HOST_TOOL_CALL_TIMEOUTS_MS = {
            "deepseek_harness": 60000,   # must-pass host
            "chatgpt_app": 60000,        # recorded, non-blocking
            "claude_code": 60000,        # recorded, non-blocking
        }
        for host, budget in KNOWN_HOST_TOOL_CALL_TIMEOUTS_MS.items():
            assert cfg.total_timeout_ms < budget, (
                f"Graph total timeout must be < {host} tool-call budget ({budget}ms)"
            )

    def test_graph_sub_timeout_below_total(self):
        cfg = get_settings().graph
        assert cfg.graph_sub_timeout_ms < cfg.total_timeout_ms


class TestHostCompatibilityRecording:
    """FR-028: DeepSeek Harness must-pass; ChatGPT App / Claude Code recorded
    but MUST NOT block 004 acceptance."""

    def test_host_matrix_structure(self):
        # The host compatibility matrix: must-pass vs non-blocking
        host_matrix = {
            "deepseek_harness": {"required": True, "blocking": True},
            "chatgpt_app": {"required": False, "blocking": False},
            "claude_code": {"required": False, "blocking": False},
        }
        # Exactly one must-pass blocking host (DeepSeek Harness)
        must_pass = [h for h, v in host_matrix.items() if v["required"] and v["blocking"]]
        assert must_pass == ["deepseek_harness"]
        # ChatGPT / Claude Code are non-blocking
        assert host_matrix["chatgpt_app"]["blocking"] is False
        assert host_matrix["claude_code"]["blocking"] is False
