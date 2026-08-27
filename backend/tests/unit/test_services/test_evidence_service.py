"""Unit tests for EvidenceService with mocked dependencies (T045).

Tests the real ``EvidenceService.get_evidence`` logic against a mocked
``AsyncSession``, covering the four terminal states and parent-context loading:
- invalid evidence_id → ``unavailable`` / INVALID_EVIDENCE_ID
- evidence not found → ``unavailable`` / EVIDENCE_NOT_FOUND
- scope mismatch → ``scope_mismatch`` / SCOPE_MISMATCH
- valid → ``available`` with full_content, source_version, scope_type
- parent context included when ``parent_chunk_id`` is set
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag_mcp.services.evidence_service import EvidenceService


def _result_mock(scalar_one=None, scalars_all=None):
    """Build a mock result object exposing scalar_one_or_none / scalars().all()."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    scalars = MagicMock()
    scalars.all.return_value = scalars_all or []
    result.scalars.return_value = scalars
    return result


def _make_chunk(**overrides):
    """Build a minimal Chunk-like object for EvidenceService."""
    chunk = MagicMock()
    chunk.chunk_id = 123
    chunk.knowledge_scope_id = 999
    chunk.version_id = 777
    chunk.parent_chunk_id = None
    chunk.content_text = "full chunk content"
    chunk.position_path = "## 安装 > ### 配置"
    for key, value in overrides.items():
        setattr(chunk, key, value)
    return chunk


class TestEvidenceService:
    @pytest.mark.asyncio
    async def test_invalid_evidence_id_returns_unavailable(self):
        """A non-numeric evidence_id short-circuits to unavailable without a DB query."""
        session = AsyncMock()
        svc = EvidenceService(session)

        resp = await svc.get_evidence("not-a-number", ["proj-a"])

        assert resp["status"] == "unavailable"
        assert resp["error"]["code"] == "INVALID_EVIDENCE_ID"
        session.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_found_returns_unavailable(self):
        """A valid numeric id with no matching Chunk returns unavailable."""
        session = AsyncMock()
        session.execute.side_effect = [
            _result_mock(scalars_all=[]),   # _resolve_scope_ids → no projects
            _result_mock(scalar_one=None),  # chunk query → not found
        ]
        svc = EvidenceService(session)

        resp = await svc.get_evidence("123", ["proj-a"])

        assert resp["status"] == "unavailable"
        assert resp["error"]["code"] == "EVIDENCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_scope_mismatch_returns_scope_mismatch(self):
        """A chunk outside the requested scopes returns scope_mismatch, not content."""
        session = AsyncMock()
        chunk = _make_chunk(knowledge_scope_id=999)
        session.execute.side_effect = [
            _result_mock(scalars_all=[111]),  # resolve → scope 111 (≠ 999)
            _result_mock(scalar_one=chunk),
        ]
        svc = EvidenceService(session)

        resp = await svc.get_evidence("123", ["proj-a"])

        assert resp["status"] == "scope_mismatch"
        assert resp["error"]["code"] == "SCOPE_MISMATCH"
        assert "full_content" not in resp

    @pytest.mark.asyncio
    async def test_available_with_full_content(self):
        """A matching chunk returns available with full_content and metadata."""
        session = AsyncMock()
        chunk = _make_chunk(knowledge_scope_id=999, parent_chunk_id=None)
        session.execute.side_effect = [
            _result_mock(scalars_all=[999]),      # resolve → scope 999
            _result_mock(scalar_one=chunk),       # chunk query
            _result_mock(scalar_one=3),           # version_number
            _result_mock(scalar_one="project"),   # scope_type
        ]
        svc = EvidenceService(session)

        resp = await svc.get_evidence("123", ["proj-a"])

        assert resp["status"] == "available"
        assert resp["full_content"] == "full chunk content"
        assert resp["source_version"] == 3
        assert resp["knowledge_scope_type"] == "project"
        assert resp["source_position"] == "## 安装 > ### 配置"
        assert "parent_context" not in resp

    @pytest.mark.asyncio
    async def test_parent_context_included_when_present(self):
        """A chunk with parent_chunk_id includes parent_context in the response."""
        session = AsyncMock()
        chunk = _make_chunk(knowledge_scope_id=999, parent_chunk_id=500)
        session.execute.side_effect = [
            _result_mock(scalars_all=[999]),          # resolve → scope 999
            _result_mock(scalar_one=chunk),           # chunk query
            _result_mock(scalar_one="# 父章节"),       # parent content query
            _result_mock(scalar_one=2),               # version_number
            _result_mock(scalar_one="project"),       # scope_type
        ]
        svc = EvidenceService(session)

        resp = await svc.get_evidence("123", ["proj-a"])

        assert resp["status"] == "available"
        assert resp["parent_context"] == "# 父章节"

    @pytest.mark.asyncio
    async def test_multi_scope_resolution_accepts_matching_scope(self):
        """A chunk matching any one of multiple resolved scopes is available.

        A single project reference can resolve to multiple knowledge_scope_ids
        (e.g. an alias matching several projects); the chunk is accepted if its
        scope is among them.
        """
        session = AsyncMock()
        chunk = _make_chunk(knowledge_scope_id=999)
        session.execute.side_effect = [
            _result_mock(scalars_all=[111, 999]),  # resolve → two scope ids
            _result_mock(scalar_one=chunk),
            _result_mock(scalar_one=1),
            _result_mock(scalar_one="project"),
        ]
        svc = EvidenceService(session)

        resp = await svc.get_evidence("123", ["proj-a"])

        assert resp["status"] == "available"
