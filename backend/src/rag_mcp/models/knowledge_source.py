"""KnowledgeSource ORM model.

Represents an uploaded raw material (Markdown / Java) and its lifecycle
status within a knowledge scope.  Content-hash deduplication is enforced
at the application layer within the same scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

# All supported formats (003 extends 001/002 markdown+java to 8 values)
_SUPPORTED_FORMATS = (
    "markdown", "java", "openapi", "ddl", "go", "python", "word", "pdf",
)

if TYPE_CHECKING:
    from rag_mcp.models.chunk import Chunk
    from rag_mcp.models.knowledge_scope import KnowledgeScope
    from rag_mcp.models.processing_run import ProcessingRun


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        CheckConstraint(
            "format IN ('markdown','java','openapi','ddl','go','python','word','pdf')",
            name="knowledge_sources_format_check",
        ),
    )

    source_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_scopes.scope_id"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="SHA-256 hex digest"
    )
    format: Mapped[str] = mapped_column(
        String(16), nullable=False,
        comment="Source format: markdown, java, openapi, ddl, go, python, word, or pdf",
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="File size in bytes, >= 0"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'uploaded'"),
        comment="'uploaded', 'processing', 'published', 'failed', or 'deleted'",
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    knowledge_scope: Mapped["KnowledgeScope"] = relationship(
        back_populates="sources"
    )
    processing_runs: Mapped[List["ProcessingRun"]] = relationship(
        back_populates="source"
    )
    chunks: Mapped[List["Chunk"]] = relationship(back_populates="source")

    def __repr__(self) -> str:
        return (
            f"<KnowledgeSource(source_id={self.source_id}, "
            f"filename={self.filename!r}, status={self.status!r})>"
        )
