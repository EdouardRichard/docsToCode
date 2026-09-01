"""Shared helpers for graph-relation ingestion wiring tests (T042, 004).

Provides a deterministic fake embedding provider, an in-memory Qdrant stand-in
covering the ingestion surface, and scope/source setup utilities so the tests
can drive a real IngestionService.ingest() end-to-end against PostgreSQL
without the bge-m3 model or a live Qdrant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from rag_mcp.config import get_settings
from rag_mcp.providers.base import EmbeddingProvider
from rag_mcp.utils.hashing import hash_bytes


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic lightweight embedding provider for graph ingest tests."""

    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7) + 0.1] * self._dim for t in texts]

    async def embed_query(self, text_value: str) -> list[float]:
        return [0.5] * self._dim

    def get_dimension(self) -> int:
        return self._dim


class MockQdrantStore:
    """In-memory stand-in for QdrantStore covering the ingestion surface."""

    def __init__(self) -> None:
        self.points: list[tuple] = []
        self.collections: list[str] = []

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def create_hybrid_collection(self, name: str, dim: int) -> None:
        self.collections.append(name)

    def upsert_hybrid(self, collection, point_id, dense_vector, sparse_vector, payload) -> None:
        self.points.append((collection, point_id, payload))

    def delete_points_by_version(self, collection, version_id) -> None:
        self.points = [p for p in self.points if p[2].get("version_id") != str(version_id)]

    def delete_points_by_source(self, collection, source_id) -> None:
        self.points = [p for p in self.points if p[2].get("source_id") != str(source_id)]

    def delete_points_by_scope(self, collection, scope_id) -> None:
        self.points = [
            p for p in self.points
            if p[2].get("knowledge_scope_id") != str(scope_id)
        ]


async def setup_graph_scope(session, scope_id: int, project_id: int) -> None:
    """Insert knowledge_scope + project rows for an isolated graph test scope."""
    await session.execute(text(
        "INSERT INTO knowledge_scopes (scope_id, scope_type, name, status) "
        "VALUES (:sid, 'project', :name, 'active') "
        "ON CONFLICT (scope_id) DO NOTHING"
    ), {"sid": scope_id, "name": f"graph-scope-{scope_id}"})
    await session.execute(text(
        "INSERT INTO projects (project_id, name, knowledge_scope_id) "
        "VALUES (:pid, :name, :sid) "
        "ON CONFLICT (project_id) DO NOTHING"
    ), {"pid": project_id, "name": f"graph-proj-{project_id}", "sid": scope_id})


async def upload_source_file(
    session, scope_id: int, source_id: int, filename: str, content: str, fmt: str
) -> None:
    """Register an 'uploaded' KnowledgeSource and write its raw file to data_root."""
    data_root = Path(get_settings().data_root)
    save_dir = data_root / str(scope_id) / str(source_id)
    save_dir.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8")
    (save_dir / filename).write_bytes(raw)

    now = datetime.now(timezone.utc)
    await session.execute(text(
        "INSERT INTO knowledge_sources (source_id, knowledge_scope_id, filename, "
        "content_hash, format, size_bytes, status, created_at, updated_at) "
        "VALUES (:sid, :ksid, :fn, :ch, :fmt, :sz, 'uploaded', :now, :now)"
    ), {
        "sid": source_id, "ksid": scope_id, "fn": filename,
        "ch": hash_bytes(raw), "fmt": fmt, "sz": len(raw), "now": now,
    })
