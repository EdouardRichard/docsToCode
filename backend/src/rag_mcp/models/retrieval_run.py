"""RetrievalRun ORM model.

Append-only audit record for each retrieval invocation.  Rows are never
updated or deleted by application code; a periodic cleanup task removes
records past ``expires_at`` (default 7-day TTL, blueprint §20).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from rag_mcp.models import Base


class RetrievalRun(Base):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        # Hybrid mode must record subpath timings (data-model §3.3)
        CheckConstraint(
            "retrieval_mode <> 'hybrid' OR "
            "(subpath_timings IS NOT NULL AND subpath_timings::text <> 'null')",
            name="chk_hybrid_timings",
        ),
        # Index for querying by mode + time range (data-model §6.2)
        Index("idx_rr_mode_created", "retrieval_mode", "created_at"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
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
        comment="'dense' (001) or 'hybrid' (002 Dense+Sparse+RRF+Rerank)",
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
