"""Chunk ORM model.

The smallest retrieval unit produced from a KnowledgeSource.  Each Chunk
maps 1:1 to a Qdrant Point (``chunk_id`` as Point ID).  Supports an
optional self-referential parent relationship for hierarchical structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

if TYPE_CHECKING:
    from rag_mcp.models.knowledge_scope import KnowledgeScope
    from rag_mcp.models.knowledge_source import KnowledgeSource
    from rag_mcp.models.knowledge_version import KnowledgeVersion


class Chunk(Base):
    __tablename__ = "chunks"

    chunk_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID; also Qdrant Point ID"
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_sources.source_id"),
        nullable=False,
    )
    version_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_versions.version_id"),
        nullable=False,
    )
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_scopes.scope_id"),
        nullable=False,
        comment="Redundant FK for fast scope-filtered queries",
    )
    parent_chunk_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("chunks.chunk_id"),
        nullable=True,
        comment="Self-referential; NULL for top-level chunks",
    )
    content_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Credential-sanitized chunk body"
    )
    position_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="Section path or fully-qualified symbol path"
    )
    chunk_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="'section' or 'symbol'"
    )
    start_line: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1-based inclusive start line, > 0"
    )
    end_line: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="1-based inclusive end line, >= start_line"
    )
    token_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="Estimated token count, > 0"
    )
    embedding_model: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="Model that generated the embedding"
    )
    index_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Composite identifier: embedding model + chunking strategy version",
    )

    # -- relationships --------------------------------------------------------
    source: Mapped["KnowledgeSource"] = relationship(back_populates="chunks")
    version: Mapped["KnowledgeVersion"] = relationship(back_populates="chunks")
    knowledge_scope: Mapped["KnowledgeScope"] = relationship(back_populates="chunks")
    parent_chunk: Mapped[Optional["Chunk"]] = relationship(
        "Chunk", remote_side="Chunk.chunk_id", backref="child_chunks"
    )

    def __repr__(self) -> str:
        return (
            f"<Chunk(chunk_id={self.chunk_id}, "
            f"type={self.chunk_type!r}, path={self.position_path!r})>"
        )
