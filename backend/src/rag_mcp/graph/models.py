"""Graph ORM models: GraphEdge / SoftRelation / GraphExpansionPath (T008).

Maps 1:1 to the 004 migrations (data-model §2/§3/§4). Enforces hard-relation
enum (inferred forbidden in graph_edge), soft-relation 4-state lifecycle,
and five mandatory metadata at construction time (Constitution III/VI).

Graph node identity = chunk_id (no separate node table, blueprint §8.4).
DM-1: GraphExpansionPath carries both chunk_id and evidence_id for the
bidirectional bridge to the runtime trace ledger.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, validates

from rag_mcp.models import Base

_HARD_RELATION_TYPES = frozenset({
    "calls", "called_by", "fk_references", "fk_referenced_by", "other_hard",
})
_SOFT_LIFECYCLE_STATES = frozenset({
    "inferred", "active", "superseded", "retired",
})
_ALL_DIRECTIONS = frozenset({"out", "in"})


class GraphEdge(Base):
    """Hard relation edge (deterministic AST/DDL extraction, is_hard=true)."""

    __tablename__ = "graph_edge"
    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('calls','called_by','fk_references',"
            "'fk_referenced_by','other_hard')",
            name="chk_graph_edge_relation_type",
        ),
        CheckConstraint("direction IN ('out','in')", name="chk_graph_edge_direction"),
        CheckConstraint("is_hard = true", name="chk_graph_edge_is_hard"),
        Index(
            "idx_graph_edge_source",
            "knowledge_scope_id", "project_id", "index_version",
            "source_chunk_id", "relation_type", "direction",
        ),
        Index(
            "idx_graph_edge_target",
            "knowledge_scope_id", "project_id", "index_version",
            "target_chunk_id", "relation_type", "direction",
        ),
        Index(
            "uniq_graph_edge",
            "knowledge_scope_id", "index_version", "source_chunk_id",
            "target_chunk_id", "relation_type", "direction", "version",
            unique=True,
        ),
    )

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_scopes.scope_id"), nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), nullable=False)
    target_chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    is_hard: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[Any] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    @validates("relation_type")
    def _validate_relation_type(self, _key: str, value: str) -> str:
        if value == "inferred":
            raise ValueError(
                "graph_edge (hard relation) cannot use relation_type='inferred' "
                "(soft relations live in soft_relation table, Constitution III)"
            )
        if value not in _HARD_RELATION_TYPES:
            raise ValueError(
                f"Invalid hard relation_type '{value}'. Must be one of {sorted(_HARD_RELATION_TYPES)}"
            )
        return value

    @validates("direction")
    def _validate_direction(self, _key: str, value: str) -> str:
        if value not in _ALL_DIRECTIONS:
            raise ValueError(f"Invalid direction '{value}'. Must be 'out' or 'in'.")
        return value

    @validates("is_hard")
    def _validate_is_hard(self, _key: str, value: bool) -> bool:
        if value is not True:
            raise ValueError("graph_edge.is_hard must always be true (use soft_relation for false)")
        return value

    def __repr__(self) -> str:
        return f"<GraphEdge(edge_id={self.edge_id}, {self.relation_type}, hard={self.is_hard})>"


class SoftRelation(Base):
    """Soft relation (LLM-inferred, is_hard=false, 4-state lifecycle).

    data-model §3: five mandatory metadata + deterministic supersede rules.
    """

    __tablename__ = "soft_relation"
    __table_args__ = (
        CheckConstraint("relation_type = 'inferred'", name="chk_soft_relation_type"),
        CheckConstraint("direction IN ('out','in')", name="chk_soft_relation_direction"),
        CheckConstraint("is_hard = false", name="chk_soft_relation_is_hard"),
        CheckConstraint(
            "lifecycle_state IN ('inferred','active','superseded','retired')",
            name="chk_soft_relation_lifecycle",
        ),
        Index(
            "idx_soft_relation_pair",
            "knowledge_scope_id", "index_version", "source_chunk_id",
            "target_chunk_id", "relation_type", "lifecycle_state",
        ),
        Index(
            "idx_soft_relation_active",
            "knowledge_scope_id", "project_id", "index_version", "lifecycle_state",
            postgresql_where=text("lifecycle_state = 'active'"),
        ),
    )

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_scopes.scope_id"), nullable=False)
    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), nullable=False)
    target_chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inferred'"))
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    is_hard: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    inference_source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(precision=4, scale=3), nullable=False)
    model_and_version: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[Any] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    supporting_evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'inferred'"))
    superseded_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    superseded_at: Mapped[Optional[Any]] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    _REQUIRED_METADATA = (
        "inference_source", "confidence", "model_and_version",
        "generated_at", "supporting_evidence_ids",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._validate_metadata()

    @validates("relation_type")
    def _validate_relation_type(self, _key: str, value: str) -> str:
        if value != "inferred":
            raise ValueError(f"soft_relation.relation_type must be 'inferred', got '{value}'")
        return value

    @validates("direction")
    def _validate_direction(self, _key: str, value: str) -> str:
        if value not in _ALL_DIRECTIONS:
            raise ValueError(f"Invalid direction '{value}'. Must be 'out' or 'in'.")
        return value

    @validates("lifecycle_state")
    def _validate_lifecycle_state(self, _key: str, value: str) -> str:
        if value not in _SOFT_LIFECYCLE_STATES:
            raise ValueError(
                f"Invalid lifecycle_state '{value}'. Must be one of {sorted(_SOFT_LIFECYCLE_STATES)}"
            )
        return value

    @validates("is_hard")
    def _validate_is_hard(self, _key: str, value: bool) -> bool:
        if value is not False:
            raise ValueError("soft_relation.is_hard must always be false (Constitution III)")
        return value

    def _validate_metadata(self) -> None:
        missing: list[str] = []
        for field in self._REQUIRED_METADATA:
            if getattr(self, field, None) is None:
                missing.append(field)
        if missing:
            raise ValueError(
                f"Soft relation requires 5 metadata fields; missing: {missing}"
            )
        if self.lifecycle_state == "active":
            threshold = 0.6
            conf = float(self.confidence) if self.confidence is not None else 0.0
            if conf < threshold:
                raise ValueError(f"active soft relation requires confidence >= {threshold}, got {conf}")
            if not self.supporting_evidence_ids:
                raise ValueError("active soft relation requires non-empty supporting_evidence_ids")

    def __repr__(self) -> str:
        return f"<SoftRelation(edge_id={self.edge_id}, state={self.lifecycle_state})>"


class GraphExpansionPath(Base):
    """Per-evidence graph expansion hop sequence (retrieval-run sub-table).

    DM-1: carries both chunk_id and evidence_id for bidirectional bridge
    to the runtime trace ledger's graph_candidates.
    """

    __tablename__ = "graph_expansion_path"
    __table_args__ = (
        CheckConstraint("hop_count >= 1 AND hop_count <= 3",
                        name="chk_graph_expansion_path_hops"),
        Index("idx_graph_expansion_path_request", "request_id"),
        Index("idx_graph_expansion_path_chunk", "chunk_id"),
    )

    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("retrieval_runs.run_id"), primary_key=True)
    evidence_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), primary_key=True)
    chunk_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("chunks.chunk_id"), nullable=False)
    start_chunk_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    edge_path: Mapped[list] = mapped_column(JSONB, nullable=False)
    hop_count: Mapped[int] = mapped_column(Integer, nullable=False)
    structure_weight: Mapped[float] = mapped_column(
        Numeric(precision=6, scale=4), nullable=False)
    graph_rank: Mapped[int] = mapped_column(Integer, nullable=False)

    @validates("hop_count")
    def _validate_hop_count(self, _key: str, value: int) -> int:
        if value < 1 or value > 3:
            raise ValueError(f"hop_count must be in [1,3], got {value}")
        return value

    def __repr__(self) -> str:
        return f"<GraphExpansionPath(request={self.request_id}, evidence={self.evidence_id}, hops={self.hop_count})>"
