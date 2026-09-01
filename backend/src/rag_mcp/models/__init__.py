"""SQLAlchemy ORM models for RAG MCP Server.

All models inherit from the shared ``Base`` declarative base defined here.
Import individual models from their respective modules or re-export from
this package for convenience.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


# Re-export models for convenient ``from rag_mcp.models import KnowledgeScope``
from rag_mcp.models.knowledge_scope import KnowledgeScope  # noqa: E402, F401
from rag_mcp.models.project import Project  # noqa: E402, F401
from rag_mcp.models.knowledge_source import KnowledgeSource  # noqa: E402, F401
from rag_mcp.models.knowledge_version import KnowledgeVersion  # noqa: E402, F401
from rag_mcp.models.chunk import Chunk  # noqa: E402, F401
from rag_mcp.models.processing_run import ProcessingRun  # noqa: E402, F401
from rag_mcp.models.retrieval_run import RetrievalRun  # noqa: E402, F401
from rag_mcp.graph.models import GraphEdge, SoftRelation, GraphExpansionPath  # noqa: E402, F401

__all__ = [
    "Base",
    "KnowledgeScope",
    "Project",
    "KnowledgeSource",
    "KnowledgeVersion",
    "Chunk",
    "ProcessingRun",
    "RetrievalRun",
    "GraphEdge",
    "SoftRelation",
    "GraphExpansionPath",
]
