"""KnowledgeVersion ORM model.

A publishable snapshot of knowledge within a scope.  Version numbers are
monotonically increasing per scope and enforced unique via a composite
constraint ``(knowledge_scope_id, version_number)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger, ForeignKey, Integer, String, text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

if TYPE_CHECKING:
    from rag_mcp.models.chunk import Chunk
    from rag_mcp.models.knowledge_scope import KnowledgeScope


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_scope_id", "version_number", name="uq_kv_scope_version"
        ),
    )

    version_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_scopes.scope_id"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Monotonically increasing, > 0"
    )
    capabilities: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment='Index capability manifest, e.g. {"dense_ready": true}',
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'draft'"),
        comment="'draft', 'published', or 'superseded'",
    )
    # 004: graph_ready capability flag (migration 0044, FR-013/FR-014)
    graph_ready: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
        comment="Graph relation capability ready (004, FR-013/FR-014)",
    )
    published_at: Mapped[str | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # -- relationships --------------------------------------------------------
    knowledge_scope: Mapped["KnowledgeScope"] = relationship(
        back_populates="versions"
    )
    chunks: Mapped[List["Chunk"]] = relationship(back_populates="version")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeVersion(version_id={self.version_id}, "
            f"v{self.version_number}, status={self.status!r})>"
        )
