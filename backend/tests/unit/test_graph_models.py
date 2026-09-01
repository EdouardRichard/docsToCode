"""Unit test for graph ORM models (T008).

Validates GraphEdge / SoftRelation / GraphExpansionPath field-model mapping,
enum constraints, and metadata validation (data-model §2/§3/§4, DM-1).

This test MUST FAIL before the models are implemented (TDD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rag_mcp.graph.models import GraphEdge, GraphExpansionPath, SoftRelation


class TestGraphEdge:
    def test_valid_hard_calls_edge(self):
        edge = GraphEdge(
            edge_id=1, knowledge_scope_id=100, project_id=200, index_version=1,
            source_chunk_id=300, target_chunk_id=301,
            relation_type="calls", direction="out", is_hard=True, version=1,
            parse_evidence={"source_format": "java", "locator": "x", "extractor": "e"},
        )
        assert edge.relation_type == "calls"
        assert edge.is_hard is True

    def test_inferred_relation_type_rejected(self):
        """graph_edge (hard) MUST NOT allow relation_type='inferred'."""
        with pytest.raises((ValueError, TypeError)):
            GraphEdge(
                edge_id=2, knowledge_scope_id=100, project_id=200, index_version=1,
                source_chunk_id=300, target_chunk_id=301,
                relation_type="inferred", direction="out", is_hard=True, version=1,
                parse_evidence={"source_format": "java", "locator": "x", "extractor": "e"},
            )

    def test_invalid_direction_rejected(self):
        with pytest.raises((ValueError, TypeError)):
            GraphEdge(
                edge_id=3, knowledge_scope_id=100, project_id=200, index_version=1,
                source_chunk_id=300, target_chunk_id=301,
                relation_type="calls", direction="sideways", is_hard=True, version=1,
                parse_evidence={"source_format": "java", "locator": "x", "extractor": "e"},
            )


class TestSoftRelation:
    def _base_kwargs(self):
        return dict(
            edge_id=10, knowledge_scope_id=100, project_id=200, index_version=1,
            source_chunk_id=300, target_chunk_id=305,
            relation_type="inferred", direction="out", is_hard=False, version=1,
            inference_source="llm-offline", confidence=0.85,
            model_and_version="local-llm-v1",
            generated_at=datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc),
            supporting_evidence_ids=[400],
            lifecycle_state="active",
        )

    def test_valid_active_soft_relation(self):
        rel = SoftRelation(**self._base_kwargs())
        assert rel.lifecycle_state == "active"
        assert rel.is_hard is False

    def test_missing_metadata_rejected(self):
        """Soft relation MUST raise when 5 metadata fields are missing."""
        kw = self._base_kwargs()
        del kw["inference_source"]
        with pytest.raises((ValueError, TypeError)):
            SoftRelation(**kw)

    def test_invalid_lifecycle_state_rejected(self):
        kw = self._base_kwargs()
        kw["lifecycle_state"] = "invalid_state"
        with pytest.raises((ValueError, TypeError)):
            SoftRelation(**kw)


class TestGraphExpansionPath:
    def test_valid_path(self):
        path = GraphExpansionPath(
            request_id=1, evidence_id=300, chunk_id=300, start_chunk_id=301,
            edge_path=[{"hop": 1, "edge_id": 500, "relation_type": "calls",
                        "direction": "out", "is_hard": True}],
            hop_count=1, structure_weight=1.0, graph_rank=1,
        )
        assert path.chunk_id == 300
        assert path.evidence_id == 300  # DM-1: chunk_id ↔ evidence_id bridge

    def test_dm1_chunk_evidence_bridge(self):
        """DM-1: chunk_id and evidence_id must both be present (bidirectional)."""
        path = GraphExpansionPath(
            request_id=2, evidence_id=305, chunk_id=305, start_chunk_id=301,
            edge_path=[{"hop": 1, "edge_id": 501, "relation_type": "called_by",
                        "direction": "in", "is_hard": True}],
            hop_count=1, structure_weight=0.5, graph_rank=2,
        )
        assert path.chunk_id == path.evidence_id  # same chunk became evidence
