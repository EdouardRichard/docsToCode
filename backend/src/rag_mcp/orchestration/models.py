"""ORM models for Agent orchestration runtime tables (005, T003).

Four append-only / TTL-bounded PostgreSQL tables (data-model.md sec 2-5):
  - EvidenceLedgerEntry  : append-only evidence ledger (FR-008/FR-009/FR-032)
  - AgentJudgment         : evidence analyst structured judgments (FR-013/FR-015)
  - ContextSelectionList : append-only selection list (FR-017/FR-032)
  - AgenticRetrievalRun   : run record + state envelope + guardrails (FR-010/FR-031)

All tables carry the isolation triple (knowledge_scope_id, project_id,
index_version) and use TTL expires_at columns (blueprint sec 20).
Append-only invariant: ORM models expose NO update path; only INSERT is
performed by the store layer (FR-008, SC-006).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from rag_mcp.models import Base


class EvidenceLedgerEntry(Base):
    """Append-only evidence ledger entry (data-model sec 2, FR-008/FR-009/FR-032).

    Agents may reference / evaluate / filter entries but MUST NOT overwrite
    the original retrieval record. Only INSERT is permitted (except TTL cleanup).
    """

    __tablename__ = "evidence_ledger_entry"
    __table_args__ = (
        CheckConstraint(
            "retriever IN ('dense','sparse','graph','fusion','rerank')",
            name="chk_ledger_retriever",
        ),
        CheckConstraint(
            "referenced_by_agent IN ('query_planner','evidence_analyst','context_orchestrator')",
            name="chk_ledger_referenced_by_agent",
        ),
        CheckConstraint("round_index >= 0", name="chk_ledger_round_index"),
        CheckConstraint("sub_problem_id >= 1", name="chk_ledger_sub_problem_id"),
        CheckConstraint("score >= 0 AND score <= 1", name="chk_ledger_score"),
        CheckConstraint("source_version >= 1", name="chk_ledger_source_version"),
        # Isolation triple index
        Index(
            "idx_ledger_scope",
            "knowledge_scope_id", "project_id", "index_version", "created_at",
        ),
        # Run/round/sub-problem traceability index (SC-009)
        Index(
            "idx_ledger_run",
            "run_id", "round_index", "sub_problem_id",
        ),
        # External bridge key (request_id, evidence_id) -> ledger (FR-024)
        Index(
            "idx_ledger_request_evidence",
            "request_id", "evidence_id",
        ),
    )

    ledger_entry_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False,
        comment="Snowflake ID (^[0-9]+$ string form in schema)",
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    sub_problem_id: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_id: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False)
    retriever: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_position: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("knowledge_scopes.scope_id"), nullable=False,
        comment="Isolation: scope",
    )
    knowledge_scope_type: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="project or public",
    )
    project_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="Isolation: project",
    )
    index_version: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Isolation: derived index version",
    )
    referenced_by_agent: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    ttl_expires_at: Mapped[str | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
        comment="TTL expiry (blueprint sec 20)",
    )

    def __repr__(self) -> str:
        return (
            f"<EvidenceLedgerEntry(id={self.ledger_entry_id}, "
            f"run={self.run_id}, round={self.round_index}, "
            f"sub={self.sub_problem_id})>"
        )


class AgentJudgment(Base):
    """Evidence analyst structured judgment (data-model sec 3, FR-013/FR-015).

    coverage_state / conflict_type use fixed enums (FR-032).
    needs_supplementary is an Agent judgment INPUT consumed by the
    deterministic controller (not an exclusive jump, Constitution VI).
    """

    __tablename__ = "agent_judgment"
    __table_args__ = (
        CheckConstraint(
            "coverage_state IN ('covered','partial','uncovered')",
            name="chk_judgment_coverage_state",
        ),
        CheckConstraint(
            "conflict_type IN ('none','version_conflict','source_conflict','domain_conflict')",
            name="chk_judgment_conflict_type",
        ),
        CheckConstraint("round_index >= 0", name="chk_judgment_round_index"),
        Index("idx_judgment_run", "run_id", "round_index"),
    )

    judgment_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False,
        comment="Snowflake ID (^[0-9]+$)",
    )
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_state: Mapped[str] = mapped_column(Text, nullable=False)
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    uncovered_sub_problem_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    needs_supplementary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    gap_descriptions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"),
    )
    model_and_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    def __repr__(self) -> str:
        return (
            f"<AgentJudgment(id={self.judgment_id}, run={self.run_id}, "
            f"round={self.round_index}, coverage={self.coverage_state!r})>"
        )


class ContextSelectionList(Base):
    """Append-only context selection list (data-model sec 4, FR-017/FR-032).

    Records selected / truncated / deduped decisions WITHOUT overwriting
    the original ledger entry (FR-008).
    """

    __tablename__ = "context_selection_list"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('selected','truncated','deduped')",
            name="chk_selection_decision",
        ),
        Index("idx_selection_run", "run_id"),
    )

    context_result_id: Mapped[str] = mapped_column(
        Text, primary_key=True,
        comment="Context orchestration result identifier (run_id-stable)",
    )
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    ledger_entry_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("evidence_ledger_entry.ledger_entry_id"),
        primary_key=True,
        comment="Selected / truncated / deduped ledger entry",
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"),
    )

    def __repr__(self) -> str:
        return (
            f"<ContextSelectionList(result={self.context_result_id}, "
            f"entry={self.ledger_entry_id}, decision={self.decision!r})>"
        )


class AgenticRetrievalRun(Base):
    """Agent orchestration retrieval run + state envelope (data-model sec 5).

    Records the full run: project scope, completion status, guardrail state,
    sub-path timings, agent output references, and ledger references.
    Uses TTL (blueprint sec 20); not written back to the knowledge base.
    """

    __tablename__ = "agentic_retrieval_run"
    __table_args__ = (
        CheckConstraint(
            "completion_status IN ('complete','partial','no_evidence','failed')",
            name="chk_agentic_run_completion_status",
        ),
        CheckConstraint("max_rounds >= 1 AND max_rounds <= 3", name="chk_agentic_run_max_rounds"),
        CheckConstraint("rounds_completed >= 0", name="chk_agentic_run_rounds_completed"),
        Index("idx_run_request", "request_id"),
        Index("idx_run_scope", "knowledge_scope_ids", "created_at"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False,
        comment="Snowflake ID (^[0-9]+$)",
    )
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_scope: Mapped[list] = mapped_column(JSONB, nullable=False)
    knowledge_scope_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    task_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    run_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    completion_status: Mapped[str] = mapped_column(Text, nullable=False)
    max_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("2"),
    )
    rounds_completed: Mapped[int] = mapped_column(Integer, nullable=False)
    guardrail_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sub_path_timings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    agent_outputs_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ledger_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    total_cost: Mapped[float | None] = mapped_column(
        Numeric(10, 4), nullable=True, comment="LLM cost (SC-007)",
    )
    schema_valid_all: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"),
    )
    ttl_expires_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=text("NOW() + INTERVAL '7 days'"),
        comment="TTL expiry; cleanup task deletes rows past this timestamp",
    )

    def __repr__(self) -> str:
        return (
            f"<AgenticRetrievalRun(run_id={self.run_id}, "
            f"status={self.completion_status!r}, "
            f"rounds={self.rounds_completed})>"
        )
