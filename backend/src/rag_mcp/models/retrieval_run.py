"""RetrievalRun ORM model.

Append-only audit record for each retrieval invocation.  Rows are never
updated or deleted by application code; a periodic cleanup task removes
records past ``expires_at`` (default 7-day TTL, blueprint §20; 006 makes the
TTL configurable via RETRIEVAL_TTL_DAYS).

006 (data-model §4.1) adds the runtime columns: tool (search_knowledge /
get_evidence), instance_id / instance_mode (instance attribution),
error_summary, trace_body_recorded, provider_usage; query_text becomes
nullable so the unified trace-body switch (FR-018) can record runs without
any query/evidence body.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from rag_mcp.models import Base


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        # Hybrid and graph-enhanced modes must record subpath timings
        # (data-model §3.3; 004 migration 0045 adds graph_enhanced, FR-026)
        CheckConstraint(
            "retrieval_mode NOT IN ('hybrid', 'graph_enhanced') OR "
            "(subpath_timings IS NOT NULL AND subpath_timings::text <> 'null')",
            name="chk_hybrid_timings",
        ),
        # 003: format must be NULL or one of the 8 valid values (FR-027)
        CheckConstraint(
            "format IS NULL OR format IN "
            "('markdown','java','openapi','ddl','go','python','word','pdf')",
            name="chk_retrieval_run_format",
        ),
        # 006: tool attribution (FR-016, aggregation by Tool)
        CheckConstraint(
            "tool IN ('search_knowledge', 'get_evidence')",
            name="chk_rr_tool",
        ),
        # 006: instance_mode is NULL for legacy (pre-006) rows
        CheckConstraint(
            "instance_mode IS NULL OR instance_mode IN ('writer', 'reader')",
            name="chk_rr_instance_mode",
        ),
        # Index for querying by mode + time range (data-model §6.2)
        Index("idx_rr_mode_created", "retrieval_mode", "created_at"),
        # 006 aggregation support (data-model §6): metrics group by
        # instance_mode/tool and completion_status within the TTL window.
        Index("idx_rr_instance_tool_created", "instance_mode", "tool", "created_at"),
        Index("idx_rr_status_created", "completion_status", "created_at"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    # 006 FR-018: nullable so TRACE_BODY_ENABLED=false stores no query body
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_scopes: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment="List of resolved scope descriptors",
    )
    completion_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="'complete', 'partial', 'no_evidence', or 'failed'",
    )
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Number of evidence items returned, >= 0"
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Wall-clock retrieval time in ms, >= 0"
    )
    # 002 hybrid retrieval fields (data-model §3.3)
    retrieval_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'dense'"),
        comment="'dense' (001), 'hybrid' (002) or 'graph_enhanced' (004)",
    )
    subpath_timings: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="Hybrid retrieval sub-path timings (dense/sparse/fusion/rerank/total ms)",
    )
    evidence_ref_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Returned evidence IDs for problem tracing",
    )
    # 003: format of top-1 evidence hit (FR-027, internal audit, not in MCP contract)
    format: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="Format of top-1 evidence hit: markdown/java/openapi/ddl/go/python/word/pdf or NULL",
    )
    # 006 runtime columns (data-model §4.1)
    tool: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'search_knowledge'"),
        comment="'search_knowledge' or 'get_evidence' (FR-016 by-Tool metrics)",
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Serving instance (NULL for pre-006 rows)",
    )
    instance_mode: Mapped[str | None] = mapped_column(
        String(8),
        nullable=True,
        comment="Instance mode redundancy (survives registry row purge)",
    )
    error_summary: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="{code, message, failed_paths[]} (FR-020 error backtrace)",
    )
    trace_body_recorded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
        comment="Whether the query body was recorded for this row",
    )
    provider_usage: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="{embedding_calls, rerank_calls, llm_calls, llm_prompt_chars, llm_completion_chars}",
    )
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    expires_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW() + INTERVAL '7 days'"),
        comment="TTL expiry; cleanup task deletes rows past this timestamp",
    )

    def __repr__(self) -> str:
        return (
            f"<RetrievalRun(run_id={self.run_id}, "
            f"status={self.completion_status!r}, evidence={self.evidence_count})>"
        )
