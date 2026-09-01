"""Unit test for offline LLM soft-relation inference (T028).

Validates SoftRelationInference: an offline LLM proposes relations and the
inference layer materialises them as SoftRelation objects carrying the 5
mandatory metadata, drives the deterministic 4-state lifecycle machine
(inferred -> active -> superseded -> retired), applies deterministic supersede
by (source, target, relation_type) triple + confidence, and guarantees that
soft relations never upgrade to hard (research §4/§5, Constitution III/VI).

This test MUST FAIL before soft_relation_inference.py is implemented (TDD).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rag_mcp.config import get_settings
from rag_mcp.graph.models import SoftRelation
from rag_mcp.graph.soft_relation_inference import SoftRelationInference
from rag_mcp.utils.snowflake import generate_id


SCOPE = {
    "knowledge_scope_id": 100,
    "project_id": 200,
    "index_version": 1,
}


def _llm_returning(relations):
    """Build a mock offline-LLM callable returning predetermined relations.

    The callable signature is llm(chunks) -> list of
    (source_chunk_id, target_chunk_id, confidence, supporting_evidence_ids).
    """
    def _llm(chunks):
        return list(relations)
    return _llm


class TestFiveMetadata:
    def test_creates_relation_with_five_metadata_fields(self):
        """infer() must produce a SoftRelation carrying all 5 metadata (research §5)."""
        llm = _llm_returning([(300, 301, 0.85, [400, 401])])
        inf = SoftRelationInference()
        relations = inf.infer(
            chunks=[], scope=SCOPE, llm=llm, model_and_version="local-llm-v1"
        )
        assert len(relations) == 1
        rel = relations[0]
        assert isinstance(rel, SoftRelation)
        # 1. inference_source
        assert rel.inference_source is not None and rel.inference_source != ""
        # 2. confidence
        assert float(rel.confidence) == 0.85
        # 3. model_and_version
        assert rel.model_and_version == "local-llm-v1"
        # 4. generated_at (deterministic datetime, not LLM-decided)
        assert isinstance(rel.generated_at, datetime)
        # 5. supporting_evidence_ids
        assert rel.supporting_evidence_ids == [400, 401]

    def test_missing_metadata_raises_value_error(self):
        """infer() must reject LLM output missing required metadata (5-field contract).

        An LLM returning None confidence -> the produced SoftRelation is missing
        a mandatory metadata field -> ValueError (Constitution VI, research §5).
        """
        llm = _llm_returning([(300, 301, None, [400])])
        inf = SoftRelationInference()
        with pytest.raises(ValueError):
            inf.infer(chunks=[], scope=SCOPE, llm=llm)


class TestLifecycleStateMachine:
    def test_inferred_to_active_when_confidence_high_and_evidence_nonempty(self):
        """inferred -> active when confidence >= threshold AND evidence non-empty."""
        llm = _llm_returning([(300, 301, 0.7, [400])])
        inf = SoftRelationInference()
        relations = inf.infer(chunks=[], scope=SCOPE, llm=llm)
        assert relations[0].lifecycle_state == "active"

    def test_stays_inferred_when_confidence_below_threshold(self):
        """confidence < threshold keeps lifecycle_state='inferred' (not active)."""
        llm = _llm_returning([(300, 301, 0.4, [400])])
        inf = SoftRelationInference()
        relations = inf.infer(chunks=[], scope=SCOPE, llm=llm)
        assert relations[0].lifecycle_state == "inferred"

    def test_low_confidence_does_not_enter_active(self):
        """A low-confidence relation must never reach the active state (FR-005)."""
        threshold = get_settings().graph.soft_confidence_threshold
        llm = _llm_returning([(300, 301, threshold - 0.01, [400])])
        inf = SoftRelationInference()
        relations = inf.infer(chunks=[], scope=SCOPE, llm=llm)
        assert relations[0].lifecycle_state != "active"
        assert relations[0].lifecycle_state == "inferred"

    def test_high_confidence_but_empty_evidence_stays_inferred(self):
        """active requires BOTH confidence >= threshold AND non-empty evidence."""
        llm = _llm_returning([(300, 301, 0.9, [])])
        inf = SoftRelationInference()
        relations = inf.infer(chunks=[], scope=SCOPE, llm=llm)
        assert relations[0].lifecycle_state == "inferred"


class TestDeterministicSupersede:
    def test_higher_confidence_supersedes_old(self):
        """Same triple + strictly higher confidence -> old superseded, new active."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [400])])
        )[0]
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.9, [401])])
        )[0]
        assert old.lifecycle_state == "active"
        assert new.lifecycle_state == "active"

        superseded = inf.supersede_relations(new, [old])

        assert old in superseded
        assert old.lifecycle_state == "superseded"
        assert new.lifecycle_state == "active"

    def test_superseded_by_points_to_new_edge_id(self):
        """The superseded relation's superseded_by must point to the new edge_id."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [400])])
        )[0]
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.9, [401])])
        )[0]
        inf.supersede_relations(new, [old])
        assert old.superseded_by == new.edge_id
        assert isinstance(old.superseded_at, datetime)

    def test_lower_confidence_does_not_supersede(self):
        """A new relation with lower confidence must NOT supersede the old one."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.9, [400])])
        )[0]
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [401])])
        )[0]
        superseded = inf.supersede_relations(new, [old])
        assert superseded == []
        assert old.lifecycle_state == "active"
        assert old.superseded_by is None

    def test_different_triple_not_superseded(self):
        """Only the same (source, target, relation_type) triple is superseded."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [400])])
        )[0]
        # different target -> different triple
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 302, 0.99, [401])])
        )[0]
        superseded = inf.supersede_relations(new, [old])
        assert superseded == []
        assert old.lifecycle_state == "active"

    def test_superseded_can_be_retired(self):
        """superseded -> retired completes the 4-state lifecycle machine."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [400])])
        )[0]
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.9, [401])])
        )[0]
        inf.supersede_relations(new, [old])
        assert old.lifecycle_state == "superseded"
        retired = inf.retire([old])
        assert old in retired
        assert old.lifecycle_state == "retired"


class TestNeverUpgradesToHard:
    def test_soft_relation_is_hard_always_false(self):
        """Soft relations never upgrade to hard (Constitution III)."""
        llm = _llm_returning([(300, 301, 0.85, [400])])
        inf = SoftRelationInference()
        relations = inf.infer(chunks=[], scope=SCOPE, llm=llm)
        for rel in relations:
            assert rel.is_hard is False
            assert rel.relation_type == "inferred"

    def test_supersede_keeps_is_hard_false(self):
        """Supersede must not flip a soft relation to hard."""
        inf = SoftRelationInference()
        old = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.7, [400])])
        )[0]
        new = inf.infer(
            chunks=[], scope=SCOPE, llm=_llm_returning([(300, 301, 0.9, [401])])
        )[0]
        inf.supersede_relations(new, [old])
        assert old.is_hard is False
        assert new.is_hard is False

    def test_is_hard_true_rejected_by_model(self):
        """The SoftRelation type refuses is_hard=True (Constitution III guard)."""
        with pytest.raises((ValueError, TypeError)):
            SoftRelation(
                edge_id=generate_id(),
                knowledge_scope_id=SCOPE["knowledge_scope_id"],
                project_id=SCOPE["project_id"],
                index_version=SCOPE["index_version"],
                source_chunk_id=300, target_chunk_id=301,
                relation_type="inferred", direction="out", is_hard=True, version=1,
                inference_source="llm-offline", confidence=0.85,
                model_and_version="local-llm-v1",
                generated_at=datetime.now(timezone.utc),
                supporting_evidence_ids=[400],
                lifecycle_state="active",
            )
