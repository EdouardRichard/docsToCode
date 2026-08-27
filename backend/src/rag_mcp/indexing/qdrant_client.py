"""Qdrant vector store client wrapper.

Provides a clean interface for creating collections, upserting points,
searching with payload filters, and deleting points. Supports payload
filtering by knowledge_scope_id, version_id, and source_id.
"""

from typing import Any

from qdrant_client import QdrantClient as _QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from rag_mcp.config import get_settings


class QdrantStore:
    """Wrapper around qdrant-client with project-specific conventions."""

    def __init__(self, url: str | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.qdrant_url
        self._client = _QdrantClient(url=self._url)

    def create_collection(self, name: str, dimension: int) -> None:
        """Create a new collection with HNSW index and cosine distance.

        Args:
            name: Collection name (e.g., 'chunks_dense_bge-m3_v1').
            dimension: Vector dimensionality (1024 for bge-m3).
        """
        self._client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )
        # Create payload indexes for efficient filtering
        for field in ["knowledge_scope_id", "version_id", "source_id", "chunk_type", "index_version"]:
            self._client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema="keyword",
            )

    def collection_exists(self, name: str) -> bool:
        """Check if a collection exists."""
        collections = [c.name for c in self._client.get_collections().collections]
        return name in collections

    def upsert_points(self, collection: str, points: list[PointStruct]) -> None:
        """Upsert points into a collection.

        Args:
            collection: Collection name.
            points: List of PointStruct with id, vector, and payload.
        """
        if not points:
            return
        self._client.upsert(collection_name=collection, points=points)

    def search(
        self,
        collection: str,
        vector: list[float],
        scope_ids: list[int] | None = None,
        version_id: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar vectors with optional payload filters.

        Args:
            collection: Collection name.
            vector: Query vector.
            scope_ids: Optional list of knowledge_scope_id values to filter by.
            version_id: Optional version_id to filter by.
            limit: Maximum number of results.

        Returns:
            List of dicts with id, score, and payload.
        """
        # Payload values are stored as STRINGS (see ingestion upsert), so
        # filters must match string representations of scope/version IDs.
        must_conditions = []
        should_conditions = []

        if scope_ids:
            if len(scope_ids) == 1:
                must_conditions.append(
                    FieldCondition(
                        key="knowledge_scope_id",
                        match=MatchValue(value=str(scope_ids[0])),
                    )
                )
            else:
                # Multiple scopes: any-of match (OR semantics)
                for sid in scope_ids:
                    should_conditions.append(
                        FieldCondition(
                            key="knowledge_scope_id",
                            match=MatchValue(value=str(sid)),
                        )
                    )

        if version_id is not None:
            must_conditions.append(
                FieldCondition(key="version_id", match=MatchValue(value=str(version_id)))
            )

        query_filter = None
        if must_conditions or should_conditions:
            query_filter = Filter(
                must=must_conditions if must_conditions else None,
                should=should_conditions if should_conditions else None,
            )

        # qdrant-client >= 1.9 uses query_points (search() was removed)
        response = self._client.query_points(
            collection_name=collection,
            query=vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {
                "id": p.id,
                "score": p.score,
                "payload": p.payload or {},
            }
            for p in response.points
        ]

    def delete_points_by_source(self, collection: str, source_id: int) -> None:
        """Delete all points belonging to a specific source.

        Args:
            collection: Collection name.
            source_id: Source ID whose points should be deleted.
        """
        self._client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[
                    # Payload values are stored as strings (see ingestion upsert),
                    # so the match value must be the string form.
                    FieldCondition(key="source_id", match=MatchValue(value=str(source_id)))
                ]
            ),
        )

    def delete_points_by_scope(self, collection: str, scope_id: int) -> None:
        """Delete all points belonging to a specific knowledge scope.

        Args:
            collection: Collection name.
            scope_id: Knowledge scope ID whose points should be deleted.
        """
        self._client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[
                    # Payload values are stored as strings (see ingestion upsert),
                    # so the match value must be the string form.
                    FieldCondition(
                        key="knowledge_scope_id", match=MatchValue(value=str(scope_id))
                    )
                ]
            ),
        )
