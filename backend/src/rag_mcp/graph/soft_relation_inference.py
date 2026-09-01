"""Offline LLM soft-relation inference (T028).

The LLM is offline: it only proposes relations as
(source_chunk_id, target_chunk_id, confidence, supporting_evidence_ids)
tuples. This layer materialises those proposals into SoftRelation rows carrying
the five mandatory metadata (research sec 5), drives the deterministic 4-state
lifecycle machine, and applies deterministic supersede by triple + confidence.

State machine (Constitution VI - deterministic, the LLM never decides a
transition alone):

    inferred  -> active     when confidence >= threshold AND evidence non-empty
    active    -> superseded when same triple has strictly higher confidence
    superseded -> retired   on retire (e.g. TTL / manual)

Constitution III: soft relations never upgrade to hard - is_hard is always
False and relation_type is always "inferred" (enforced by the SoftRelation
model and never mutated here).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from rag_mcp.config import get_settings
from rag_mcp.graph.models import SoftRelation
from rag_mcp.utils.snowflake import generate_id

__all__ = ["SoftRelationInference"]

# The four lifecycle states (data-model sec 3, research sec 4).
_LIFECYCLE_STATES: tuple[str, ...] = ("inferred", "active", "superseded", "retired")


class SoftRelationInference:
    """Offline LLM soft-relation inference + 4-state lifecycle + deterministic supersede.

    The LLM only proposes relations; every lifecycle transition is decided by
    the confidence threshold and triple matching (Constitution VI). Soft
    relations never upgrade to hard (Constitution III): is_hard is always False.
    """

    def __init__(self, confidence_threshold: float | None = None) -> None:
        """Initialise the inference layer.

        Args:
            confidence_threshold: confidence floor for the inferred -> active
                transition. Defaults to the configured soft_confidence_threshold
                (research sec 4, FR-005).
        """
        if confidence_threshold is None:
            confidence_threshold = get_settings().graph.soft_confidence_threshold
        self._confidence_threshold: float = float(confidence_threshold)

    @property
    def confidence_threshold(self) -> float:
        """Configured confidence floor for the inferred -> active transition."""
        return self._confidence_threshold

    def infer(
        self,
        chunks: Sequence[Any],
        scope: Mapping[str, int],
        llm: Callable[[Sequence[Any]], Sequence[tuple]],
        *,
        model_and_version: str = "local-llm-v1",
        inference_source: str = "llm-offline",
        direction: str = "out",
        version: int = 1,
    ) -> list[SoftRelation]:
        """Infer soft relations from chunks via an offline LLM callable.

        The LLM callable receives chunks and returns a sequence of
        (source_chunk_id, target_chunk_id, confidence, supporting_evidence_ids)
        tuples. Each tuple is materialised into a SoftRelation with all five
        mandatory metadata populated and lifecycle_state set deterministically:

            * active iff confidence >= soft_confidence_threshold AND
              supporting_evidence_ids is non-empty;
            * inferred otherwise (low confidence or no supporting evidence).

        Args:
            chunks: chunk sequence forwarded to the LLM callable.
            scope: mapping with knowledge_scope_id / project_id / index_version
                (the isolation triple, data-model sec 3).
            llm: callable(chunks) -> sequence of 4-tuples.
            model_and_version: identifier of the LLM used (metadata field 3).
            inference_source: provenance label (metadata field 1).
            direction: edge direction ('out' or 'in').
            version: relation version.

        Returns:
            list of SoftRelation objects.

        Raises:
            ValueError: if the LLM omits a mandatory metadata field (e.g. None
                confidence) - the 5-metadata contract is enforced by SoftRelation.
        """
        scope = self._coerce_scope(scope)
        generated_at = datetime.now(timezone.utc)
        relations: list[SoftRelation] = []
        for raw in llm(chunks):
            source_chunk_id, target_chunk_id, confidence, supporting_evidence_ids = raw
            lifecycle_state = self._decide_lifecycle(confidence, supporting_evidence_ids)
            relations.append(
                SoftRelation(
                    edge_id=generate_id(),
                    knowledge_scope_id=scope["knowledge_scope_id"],
                    project_id=scope["project_id"],
                    index_version=scope["index_version"],
                    source_chunk_id=source_chunk_id,
                    target_chunk_id=target_chunk_id,
                    relation_type="inferred",
                    direction=direction,
                    is_hard=False,
                    version=version,
                    inference_source=inference_source,
                    confidence=confidence,
                    model_and_version=model_and_version,
                    generated_at=generated_at,
                    supporting_evidence_ids=self._coerce_evidence(supporting_evidence_ids),
                    lifecycle_state=lifecycle_state,
                )
            )
        return relations

    def supersede_relations(
        self,
        new_relation: SoftRelation,
        existing_relations: Sequence[SoftRelation],
    ) -> list[SoftRelation]:
        """Deterministically supersede existing relations for the same triple.

        For each existing relation sharing the
        (source_chunk_id, target_chunk_id, relation_type) triple with
        new_relation and having strictly lower confidence, mark it
        superseded with superseded_by -> new_relation.edge_id and
        superseded_at -> now.

        Returns the list of relations that were superseded. This decision
        depends only on the triple + confidence (Constitution VI): the LLM has
        no say in the transition.
        """
        superseded: list[SoftRelation] = []
        now = datetime.now(timezone.utc)
        for existing in existing_relations:
            if not self._same_triple(new_relation, existing):
                continue
            if float(new_relation.confidence) > float(existing.confidence):
                existing.lifecycle_state = "superseded"
                existing.superseded_by = new_relation.edge_id
                existing.superseded_at = now
                superseded.append(existing)
        return superseded

    def retire(self, relations: Sequence[SoftRelation]) -> list[SoftRelation]:
        """Transition superseded relations to the terminal retired state.

        The 4-state machine: superseded -> retired (e.g. on TTL expiry or manual
        cleanup). Only relations currently superseded are retired.
        """
        retired: list[SoftRelation] = []
        for rel in relations:
            if rel.lifecycle_state == "superseded":
                rel.lifecycle_state = "retired"
                retired.append(rel)
        return retired

    def _decide_lifecycle(self, confidence: Any, supporting_evidence_ids: Any) -> str:
        """Deterministic inferred -> active gate (Constitution VI).

        active requires BOTH confidence >= soft_confidence_threshold AND a
        non-empty supporting_evidence_ids list (research sec 4, FR-005).
        Everything else stays inferred.
        """
        conf = float(confidence) if confidence is not None else 0.0
        has_evidence = bool(supporting_evidence_ids)
        if conf >= self._confidence_threshold and has_evidence:
            return "active"
        return "inferred"

    @staticmethod
    def _coerce_evidence(supporting_evidence_ids: Any) -> Any:
        """Normalise evidence ids to a list; pass None through so the model rejects it."""
        if supporting_evidence_ids is None:
            return None
        return list(supporting_evidence_ids)

    @staticmethod
    def _coerce_scope(scope: Any) -> dict:
        """Accept a GraphScope object or a mapping; return a plain dict."""
        if isinstance(scope, dict):
            return scope
        if hasattr(scope, "knowledge_scope_id"):
            return {
                "knowledge_scope_id": scope.knowledge_scope_id,
                "project_id": scope.project_id,
                "index_version": scope.index_version,
            }
        return dict(scope)

    @staticmethod
    def _same_triple(a: SoftRelation, b: SoftRelation) -> bool:
        """True iff a and b share the (source, target, relation_type) triple."""
        return (
            a.source_chunk_id == b.source_chunk_id
            and a.target_chunk_id == b.target_chunk_id
            and a.relation_type == b.relation_type
        )
