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
    SparseVector,
    SparseVectorParams,
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

    def get_collection_dimension(self, name: str) -> int | None:
        """Return the Dense vector dimension of an existing collection, or None.

        Handles both hybrid (named 'dense' vector) and simple (single vector)
        collection shapes. Returns None when the collection is missing or its
        vector config cannot be introspected.
        """
        try:
            info = self._client.get_collection(collection_name=name)
        except Exception:  # noqa: BLE001 - introspection is best-effort
            return None
        vectors = getattr(info.config.params, "vectors", None)
        if vectors is None:
            return None
        if isinstance(vectors, dict):
            dense = vectors.get("dense")
            return int(dense.size) if dense is not None else None
        return int(vectors.size)

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

    def delete_points_by_version(self, collection: str, version_id: int) -> None:
        """Delete all points belonging to a specific knowledge version.

        Used when a version is superseded after reprocessing (FR-009): the old
        version's points must be removed so they do not linger as orphans.

        Args:
            collection: Collection name.
            version_id: Knowledge version ID whose points should be deleted.
        """
        self._client.delete(
            collection_name=collection,
            points_selector=Filter(
                must=[
                    # Payload values are stored as strings (see ingestion upsert),
                    # so the match value must be the string form.
                    FieldCondition(
                        key="version_id", match=MatchValue(value=str(version_id))
                    )
                ]
            ),
        )

    # ------------------------------------------------------------------
    # 002 Hybrid collection methods (Dense + Sparse named vectors)
    # ------------------------------------------------------------------

    def _build_scope_version_filter(
        self,
        scope_ids: list[int] | None = None,
        version_id: int | None = None,
    ) -> Filter | None:
        """Build a Qdrant filter for scope and version (shared by search methods).

        Payload values are stored as strings (see ingestion upsert), so filter
        match values must be the string form of scope/version IDs.
        """
        must_conditions: list[FieldCondition] = []
        should_conditions: list[FieldCondition] = []

        if scope_ids:
            if len(scope_ids) == 1:
                must_conditions.append(
                    FieldCondition(
                        key="knowledge_scope_id",
                        match=MatchValue(value=str(scope_ids[0])),
                    )
                )
            else:
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

        if must_conditions or should_conditions:
            return Filter(
                must=must_conditions if must_conditions else None,
                should=should_conditions if should_conditions else None,
            )
        return None

    def create_hybrid_collection(self, name: str, dimension: int) -> None:
        """Create a collection with Dense + Sparse named vectors (002).

        Args:
            name: Collection name (e.g., 'chunks_hybrid_bge-m3_v1').
            dimension: Dense vector dimensionality (1024 for bge-m3).
        """
        self._client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": VectorParams(size=dimension, distance=Distance.COSINE),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )
        for field in ["knowledge_scope_id", "version_id", "source_id", "chunk_type", "index_version"]:
            self._client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema="keyword",
            )

    def upsert_hybrid(
        self,
        collection: str,
        point_id: int,
        dense_vector: list[float],
        sparse_vector: dict[str, list],
        payload: dict[str, Any],
    ) -> None:
        """Upsert a point with both Dense and Sparse named vectors (002).

        Dense and Sparse vectors share the same Point and Payload (data-model §5.2).

        Args:
            collection: Collection name.
            point_id: Point ID (chunk_id as u64).
            dense_vector: Dense embedding vector.
            sparse_vector: Sparse vector {indices: [int], values: [float]}.
            payload: Point payload (scope, version, chunk metadata).
        """
        point = PointStruct(
            id=point_id,
            vector={
                "dense": dense_vector,
                "sparse": SparseVector(
                    indices=sparse_vector["indices"],
                    values=sparse_vector["values"],
                ),
            },
            payload=payload,
        )
        self._client.upsert(collection_name=collection, points=[point])

    def search_sparse(
        self,
        collection: str,
        sparse_vector: dict[str, list],
        scope_ids: list[int] | None = None,
        version_id: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search using the sparse named vector with payload filters (002).

        Args:
            collection: Collection name.
            sparse_vector: Sparse query vector {indices: [int], values: [float]}.
            scope_ids: Optional knowledge_scope_id values to filter by.
            version_id: Optional version_id to filter by.
            limit: Maximum number of results.

        Returns:
            List of dicts with id, score, and payload.
        """
        query_filter = self._build_scope_version_filter(scope_ids, version_id)

        response = self._client.query_points(
            collection_name=collection,
            query=SparseVector(
                indices=sparse_vector["indices"],
                values=sparse_vector["values"],
            ),
            using="sparse",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )

        return [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in response.points
        ]

    def search_dense_named(
        self,
        collection: str,
        vector: list[float],
        scope_ids: list[int] | None = None,
        version_id: int | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Dense-only search on a collection with named vectors (005, T065).

        Used by the agentic pipeline's dense fallback when only the hybrid
        collection (dense+sparse named vectors) exists. Scope+version filters
        are enforced like every other retrieval path (FR-008).
        """
        query_filter = self._build_scope_version_filter(scope_ids, version_id)
        response = self._client.query_points(
            collection_name=collection,
            query=vector,
            using="dense",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in response.points
        ]

    def query_hybrid(
        self,
        collection: str,
        dense_vector: list[float],
        sparse_vector: dict[str, list],
        scope_ids: list[int] | None = None,
        version_id: int | None = None,
        limit: int = 5,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Query both Dense and Sparse named vectors with scope+version filter (002).

        Both Dense and Sparse searches enforce the same scope+version filter
        (FR-008: cross-project leakage is zero).

        Args:
            collection: Collection name.
            dense_vector: Dense query vector.
            sparse_vector: Sparse query vector {indices, values}.
            scope_ids: Optional knowledge_scope_id values to filter by.
            version_id: Optional version_id to filter by.
            limit: Maximum number of results per retriever.

        Returns:
            Tuple of (dense_results, sparse_results), each a list of dicts.
        """
        query_filter = self._build_scope_version_filter(scope_ids, version_id)

        dense_response = self._client.query_points(
            collection_name=collection,
            query=dense_vector,
            using="dense",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        dense_results = [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in dense_response.points
        ]

        sparse_response = self._client.query_points(
            collection_name=collection,
            query=SparseVector(
                indices=sparse_vector["indices"],
                values=sparse_vector["values"],
            ),
            using="sparse",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        sparse_results = [
            {"id": p.id, "score": p.score, "payload": p.payload or {}}
            for p in sparse_response.points
        ]

        return dense_results, sparse_results
