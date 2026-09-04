"""002 T040/T041: hybrid guardrail config wiring + sparse sub-path
degradation.

- rrf_k and fusion_algorithm from HybridRetrievalConfig must reach the
  deterministic fusion call (T040).
- sparse_query_timeout_ms is enforced; a sparse failure/timeout degrades
  to dense-only and records the failed path (T041 / SC-009).
"""
import asyncio
import dataclasses
import time

import pytest

from rag_mcp.config import get_settings
from rag_mcp.services import retrieval_service
from rag_mcp.services.retrieval_service import RetrievalService


class _FakeStore:
    """QdrantStore stand-in exposing the named-vector search surface."""
    def __init__(self, dense_results, sparse_results=None, sparse_exc=None, sparse_delay=0.0):
        self._dense = list(dense_results)
        self._sparse = list(sparse_results or [])
        self._sparse_exc = sparse_exc
        self._sparse_delay = sparse_delay
        self.sparse_calls = 0

    def collection_exists(self, name):
        return True

    def search_dense_named(self, collection, vector, scope_ids=None, version_id=None, limit=5):
        return list(self._dense)

    def search_sparse(self, collection, sparse_vector, scope_ids=None, version_id=None, limit=5):
        self.sparse_calls += 1
        if self._sparse_delay:
            time.sleep(self._sparse_delay)
        if self._sparse_exc:
            raise self._sparse_exc
        return list(self._sparse)


class _FakeEncoder:
    def encode(self, query):
        return {"indices": [0, 1], "values": [1.0, 1.0]}


def _dense_result(chunk_id, scope_id):
    return {
        "id": chunk_id,
        "score": 0.9,
        "payload": {
            "chunk_id": str(chunk_id),
            "knowledge_scope_id": str(scope_id),
            "position_path": "com.example.Service#method",
        },
    }


def _make_service(store, settings=None):
    svc = RetrievalService(None, store, None, reranker=None)  # type: ignore[arg-type]
    if settings is not None:
        svc._settings = settings
    # Bypass DB-backed helpers (unit scope): lexical versions present + a
    # trivial sparse encoder.
    async def _has_lexical(scope_ids):
        return True
    async def _build_encoder(scope_ids):
        return _FakeEncoder()
    svc._has_lexical_ready_versions = _has_lexical  # type: ignore[method-assign]
    svc._build_sparse_encoder = _build_encoder  # type: ignore[method-assign]
    return svc


SCOPE = 1001
CHUNK = 2001


class TestGuardrailConfigWiring:
    async def _run(self, svc):
        return await svc._try_hybrid_recall(
            query="validateToken", query_vector=[0.1] * 16,
            scope_ids=[SCOPE], limit=5,
        )

    @pytest.mark.asyncio
    async def test_rrf_k_from_config_used(self, monkeypatch):
        settings = get_settings()
        settings = dataclasses.replace(
            settings,
            hybrid_retrieval=dataclasses.replace(settings.hybrid_retrieval, rrf_k=123),
        )
        calls = {}

        def fake_fuse(dense, sparse, k=60, graph_results=None):
            calls["k"] = k
            return []

        monkeypatch.setattr(retrieval_service, "rrf_fuse", fake_fuse)
        store = _FakeStore([_dense_result(CHUNK, SCOPE)])
        svc = _make_service(store, settings)
        await self._run(svc)
        assert calls["k"] == 123

    @pytest.mark.asyncio
    async def test_fusion_algorithm_unsupported_falls_back_to_rrf(self, monkeypatch):
        settings = get_settings()
        settings = dataclasses.replace(
            settings,
            hybrid_retrieval=dataclasses.replace(
                settings.hybrid_retrieval, fusion_algorithm="dbsf",
            ),
        )
        called = {"n": 0}

        def fake_fuse(dense, sparse, k=60, graph_results=None):
            called["n"] += 1
            return []

        monkeypatch.setattr(retrieval_service, "rrf_fuse", fake_fuse)
        store = _FakeStore([_dense_result(CHUNK, SCOPE)])
        svc = _make_service(store, settings)
        result = await self._run(svc)
        assert result is not None
        assert called["n"] == 1, "unsupported fusion_algorithm must fall back to RRF"


class TestSparseDegradation:
    @pytest.mark.asyncio
    async def test_sparse_failure_degrades_to_dense_partial(self):
        store = _FakeStore(
            [_dense_result(CHUNK, SCOPE)],
            sparse_exc=RuntimeError("sparse index broken"),
        )
        svc = _make_service(store)
        raw_results, _timings, failed_paths, _g, _t, _r = await svc._try_hybrid_recall(
            query="validateToken", query_vector=[0.1] * 16, scope_ids=[SCOPE], limit=5,
        )
        assert "sparse_failed" in failed_paths
        assert len(raw_results) == 1, "dense evidence must be retained"

    @pytest.mark.asyncio
    async def test_sparse_timeout_degrades_to_dense_partial(self, monkeypatch):
        settings = get_settings()
        settings = dataclasses.replace(
            settings,
            hybrid_retrieval=dataclasses.replace(
                settings.hybrid_retrieval, sparse_query_timeout_ms=50,
            ),
        )
        store = _FakeStore([_dense_result(CHUNK, SCOPE)], sparse_delay=0.5)
        svc = _make_service(store, settings)
        raw_results, _timings, failed_paths, _g, _t, _r = await svc._try_hybrid_recall(
            query="validateToken", query_vector=[0.1] * 16, scope_ids=[SCOPE], limit=5,
        )
        assert "sparse_timeout" in failed_paths
        assert len(raw_results) == 1, "dense evidence must be retained on sparse timeout"

class TestTotalTimeoutGuardrail:
    @pytest.mark.asyncio
    async def test_search_times_out_with_failed_status(self):
        settings = get_settings()
        settings = dataclasses.replace(
            settings,
            retrieval=dataclasses.replace(settings.retrieval, total_timeout_ms=100),
        )
        store = _FakeStore([])
        svc = _make_service(store, settings)

        async def hang(*args, **kwargs):
            await asyncio.sleep(5)
            return [], None

        svc.resolve_project_refs = hang  # type: ignore[method-assign]
        result = await svc.search(query="x", project_scopes=["s"])
        assert result["completion_status"] == "failed"
        assert result["error"]["code"] == "SEARCH_TIMEOUT"
