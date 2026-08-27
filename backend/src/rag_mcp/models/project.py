"""Project ORM model.

A project is the user's working context identity, bound 1:1 to a
project-type KnowledgeScope via a UNIQUE FK constraint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base

if TYPE_CHECKING:
    from rag_mcp.models.knowledge_scope import KnowledgeScope


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    alias: Mapped[str | None] = mapped_column(
        String(128), unique=True, nullable=True, comment="Optional MCP alias for project resolution"
    )
    repo_path: Mapped[str | None] = mapped_column(
        String(1024), unique=True, nullable=True, comment="Optional repository / workspace path"
    )
    knowledge_scope_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_scopes.scope_id"),
        unique=True,
        nullable=False,
        comment="1:1 link to the project knowledge scope",
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
    knowledge_scope: Mapped["KnowledgeScope"] = relationship(
        back_populates="project"
    )

    def __repr__(self) -> str:
        return (
            f"<Project(project_id={self.project_id}, "
            f"name={self.name!r}, alias={self.alias!r})>"
        )
