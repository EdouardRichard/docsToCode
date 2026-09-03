"""006 convergence T081: embedding dimension consistency wiring (RED first).

FR-011/FR-013 (constitution VIII): a different-dimension embedding must not
be reused against an existing collection; the only legal path is a new index
version + re-vectorization. The check must be wired into ingestion, not left
as an unused helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def test_get_collection_dimension_hybrid():
    from rag_mcp.indexing.qdrant_client import QdrantStore

    store = QdrantStore()
    store._client = MagicMock()
    info = MagicMock()
    info.config.params.vectors = {"dense": MagicMock(size=1024)}
    store._client.get_collection.return_value = info

    assert store.get_collection_dimension("chunks_hybrid_x_v1") == 1024


def test_get_collection_dimension_simple():
    from rag_mcp.indexing.qdrant_client import QdrantStore

    store = QdrantStore()
    store._client = MagicMock()
    info = MagicMock()
    info.config.params.vectors = MagicMock(size=768)
    store._client.get_collection.return_value = info

    assert store.get_collection_dimension("chunks_dense_x_v1") == 768


def test_validate_collection_dimension_refuses_mismatch():
    from rag_mcp.services.ingestion_service import _validate_collection_dimension

    class Emb768:
        def get_dimension(self) -> int:
            return 768

    with pytest.raises(ValueError):
        _validate_collection_dimension(Emb768(), 1024)


def test_validate_collection_dimension_allows_match():
    from rag_mcp.services.ingestion_service import _validate_collection_dimension

    class Emb1024:
        def get_dimension(self) -> int:
            return 1024

    _validate_collection_dimension(Emb1024(), 1024)  # no raise
    _validate_collection_dimension(Emb1024(), None)  # unknown -> skip
