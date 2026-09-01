import pytest
from unittest.mock import AsyncMock, MagicMock
from rag_mcp.services.retrieval_service import RetrievalService

def _make_service(session=None, qdrant=None, embedding=None):
    from rag_mcp.config import get_settings
    service = RetrievalService.__new__(RetrievalService)
    service._session = session if session is not None else AsyncMock()
    service._qdrant_store = qdrant if qdrant is not None else MagicMock()
    service._embedding_provider = embedding if embedding is not None else AsyncMock()
    service._reranker = None
    service._settings = get_settings()
    return service

class TestNoScopeRejection:
    @pytest.mark.asyncio
    async def test_resolve_empty_scopes_rejected(self):
        service = _make_service()
        resolved_ids, error_info = await service.resolve_project_refs([])
        assert resolved_ids == []
        assert error_info is not None
        assert error_info['code'] == 'MISSING_PROJECT_SCOPE'

    @pytest.mark.asyncio
    async def test_search_empty_scopes_returns_failed(self):
        service = _make_service()
        result = await service.search(query='test query', project_scopes=[])
        assert result['completion_status'] == 'failed'
        assert result['error']['code'] == 'MISSING_PROJECT_SCOPE'
        assert result['evidence'] == []

    @pytest.mark.asyncio
    async def test_search_unresolvable_scope_returns_failed(self):
        session = AsyncMock()
        # Mock execute to return empty project list (no match)
        result_mock = MagicMock()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        result_mock.scalars.return_value = scalars_mock
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock
        service = _make_service(session=session)
        result = await service.search(query='test', project_scopes=['nonexistent-alias-xyz'])
        assert result['completion_status'] == 'failed'
        assert result['error']['code'] == 'MISSING_PROJECT_SCOPE'