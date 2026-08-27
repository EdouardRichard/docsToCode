"""ProcessingRun ORM model.

Records a single ingestion execution (initial or retry) against a
KnowledgeSource.  The ``stages`` JSONB column tracks per-stage progress
for SSE feedback and auditability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

if TYPE_CHECKING:
    from rag_mcp.models.knowledge_source import KnowledgeSource


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    run_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_sources.source_id"),
        nullable=False,
    )
    run_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="'initial' or 'retry'"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
        comment="'pending', 'running', 'completed', or 'failed'",
    )
    started_at: Mapped[str | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    completed_at: Mapped[str | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stages: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Ordered list of stage records",
    )

    # -- relationships --------------------------------------------------------
    source: Mapped["KnowledgeSource"] = relationship(back_populates="processing_runs")

    def __repr__(self) -> str:
        return (
            f"<ProcessingRun(run_id={self.run_id}, "
            f"type={self.run_type!r}, status={self.status!r})>"
        )
