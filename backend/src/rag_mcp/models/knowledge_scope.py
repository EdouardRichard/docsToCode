"""KnowledgeScope ORM model.

A knowledge scope is the unified abstraction for project-scoped and
public-scoped knowledge domains.  All downstream entities (sources, chunks,
versions) carry a mandatory ``knowledge_scope_id`` FK to enforce domain
isolation (Constitution I).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

if TYPE_CHECKING:
    from rag_mcp.models.chunk import Chunk
    from rag_mcp.models.knowledge_source import KnowledgeSource
    from rag_mcp.models.knowledge_version import KnowledgeVersion
    from rag_mcp.models.project import Project


class KnowledgeScope(Base):
    __tablename__ = "knowledge_scopes"

    scope_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    scope_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="'project' or 'public'"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
        comment="'active', 'archived', or 'deleting'",
    )
    created_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    updated_at: Mapped[str] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    # -- relationships --------------------------------------------------------
    project: Mapped["Project"] = relationship(
        back_populates="knowledge_scope", uselist=False
    )
    sources: Mapped[List["KnowledgeSource"]] = relationship(
        back_populates="knowledge_scope"
    )
    versions: Mapped[List["KnowledgeVersion"]] = relationship(
        back_populates="knowledge_scope"
    )
    chunks: Mapped[List["Chunk"]] = relationship(back_populates="knowledge_scope")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeScope(scope_id={self.scope_id}, "
            f"scope_type={self.scope_type!r}, name={self.name!r})>"
        )
