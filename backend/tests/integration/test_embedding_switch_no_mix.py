"""Integration test: embedding dimension switch prevents mixing (T049/T050).

SC-005/FR-013: a different-dimension/model embedding must not act on an
existing published index version in place; the only legal path is a new
index version + re-vectorization. Mixing events = 0.
"""

from __future__ import annotations

import pytest


def test_dimension_mismatch_rejected():
    """Different embedding dimension -> existing collection is rejected."""
    from rag_mcp.providers.factory import check_embedding_dimension

    class Emb768:
        def get_dimension(self) -> int:
            return 768

    result = check_embedding_dimension(Emb768(), active_collection_dimension=1024)
    assert result.valid is False
    assert any("dimension" in e.message.lower() for e in result.errors)


def test_dimension_match_allowed():
    from rag_mcp.providers.factory import check_embedding_dimension

    class Emb1024:
        def get_dimension(self) -> int:
            return 1024

    result = check_embedding_dimension(Emb1024(), active_collection_dimension=1024)
    assert result.valid is True


def test_mismatch_error_requires_new_index_version():
    """The only legal resolution is a new index version + re-vectorization."""
    from rag_mcp.providers.factory import check_embedding_dimension

    class Emb512:
        def get_dimension(self) -> int:
            return 512

    result = check_embedding_dimension(Emb512(), active_collection_dimension=1024)
    assert result.valid is False
    joined = " ".join(e.message for e in result.errors)
    assert "new index version" in joined
    assert "re-vectorize" in joined or "re-vectoriz" in joined


def test_distinct_models_derive_distinct_index_versions():
    """FR-013: distinct embedding models map to distinct index versions (no mixing)."""
    from rag_mcp.services.ingestion_service import _derive_index_version

    v1 = _derive_index_version("BAAI/bge-m3")
    v2 = _derive_index_version("BAAI/bge-large-en-v1.5")
    assert v1 != v2


def test_same_model_derives_same_index_version():
    from rag_mcp.services.ingestion_service import _derive_index_version

    assert _derive_index_version("BAAI/bge-m3") == _derive_index_version("BAAI/bge-m3")
    assert _derive_index_version("BAAI/bge-m3") == "bge-m3_v1"
