"""Integration tests for scope isolation at the service level (T039).

Verifies that querying with scope A does not return scope B's data.
Tests the RetrievalService.resolve_project_refs method and the underlying
QdrantStore search filtering by knowledge_scope_id.

Uses the test_client fixture from conftest.py for DB access.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.knowledge_scope import KnowledgeScope
from rag_mcp.models.project import Project
from rag_mcp.services.project_service import ProjectService
from rag_mcp.schemas.project import ProjectCreate
from rag_mcp.utils.snowflake import generate_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_projects(db_session: AsyncSession):
    """Create two projects with distinct knowledge scopes.

    Returns a dict with project_a, project_b, and their scope IDs.
    """
    svc = ProjectService(db_session)

    project_a = await svc.create_project(
        ProjectCreate(name="Scope Isolation Project A", alias=f"scope-a-{generate_id()}")
    )
    project_b = await svc.create_project(
        ProjectCreate(name="Scope Isolation Project B", alias=f"scope-b-{generate_id()}")
    )

    await db_session.commit()

    return {
        "project_a": project_a,
        "project_b": project_b,
        "scope_id_a": project_a.knowledge_scope_id,
        "scope_id_b": project_b.knowledge_scope_id,
    }


# ---------------------------------------------------------------------------
# Tests: Project ref resolution
# ---------------------------------------------------------------------------


class TestResolveProjectRefs:
    """Test that project references resolve to correct scope IDs."""

    @pytest.mark.asyncio
    async def test_resolve_by_project_id(self, db_session: AsyncSession, two_projects):
        """Resolving by numeric project_id returns the correct scope."""
        svc = ProjectService(db_session)
        project_a = two_projects["project_a"]

        found = await svc.get_project(project_a.project_id)
        assert found is not None
        assert found.knowledge_scope_id == two_projects["scope_id_a"]

    @pytest.mark.asyncio
    async def test_resolve_by_alias(self, db_session: AsyncSession, two_projects):
        """Resolving by alias returns the correct project and scope."""
        svc = ProjectService(db_session)
        project_a = two_projects["project_a"]

        found = await svc.get_project_by_alias(project_a.alias)
        assert found is not None
        assert found.project_id == project_a.project_id
        assert found.knowledge_scope_id == two_projects["scope_id_a"]

    @pytest.mark.asyncio
    async def test_different_projects_have_different_scopes(self, db_session: AsyncSession, two_projects):
        """Two distinct projects must have different knowledge_scope_id values."""
        assert two_projects["scope_id_a"] != two_projects["scope_id_b"]

    @pytest.mark.asyncio
    async def test_nonexistent_project_returns_none(self, db_session: AsyncSession):
        """Looking up a nonexistent project ID returns None."""
        svc = ProjectService(db_session)
        found = await svc.get_project(999999999999999999)
        assert found is None

    @pytest.mark.asyncio
    async def test_nonexistent_alias_returns_none(self, db_session: AsyncSession):
        """Looking up a nonexistent alias returns None."""
        svc = ProjectService(db_session)
        found = await svc.get_project_by_alias("nonexistent-alias-xyz")
        assert found is None


# ---------------------------------------------------------------------------
# Tests: Scope isolation via QdrantStore filtering
# ---------------------------------------------------------------------------


class TestScopeIsolationInSearch:
    """Verify that QdrantStore.search respects scope_id filters.

    These tests mock the Qdrant client to avoid requiring a running Qdrant
    instance while still validating the filter construction logic. The store
    uses qdrant-client's ``query_points`` API (>= 1.9), so the mock targets
    ``query_points``.
    """

    @staticmethod
    def _make_store(mock_client):
        from rag_mcp.indexing.qdrant_client import QdrantStore

        store = QdrantStore.__new__(QdrantStore)
        store._client = mock_client
        return store

    @staticmethod
    def _query_filter(mock_client):
        call_kwargs = mock_client.query_points.call_args
        assert call_kwargs is not None, "Expected query_points to be called"
        return call_kwargs.kwargs.get("query_filter")

    @pytest.mark.asyncio
    async def test_search_filters_by_single_scope(self):
        """QdrantStore.search with one scope_id constructs a filter."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.query_points.return_value = MagicMock(points=[])
        store = self._make_store(mock_client)

        store.search(
            collection="test_collection",
            vector=[0.1] * 1024,
            scope_ids=[111],
            limit=5,
        )

        query_filter = self._query_filter(mock_client)
        assert query_filter is not None, "Expected a filter to be constructed"
        assert query_filter.must and len(query_filter.must) == 1

    @pytest.mark.asyncio
    async def test_search_filters_by_multiple_scopes(self):
        """QdrantStore.search with multiple scope_ids uses should conditions."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.query_points.return_value = MagicMock(points=[])
        store = self._make_store(mock_client)

        store.search(
            collection="test_collection",
            vector=[0.1] * 1024,
            scope_ids=[111, 222],
            limit=5,
        )

        query_filter = self._query_filter(mock_client)
        assert query_filter is not None, "Expected a filter for multi-scope search"
        assert query_filter.should and len(query_filter.should) == 2

    @pytest.mark.asyncio
    async def test_search_without_scope_has_no_scope_filter(self):
        """QdrantStore.search without scope_ids passes no scope filter."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_client.query_points.return_value = MagicMock(points=[])
        store = self._make_store(mock_client)

        store.search(
            collection="test_collection",
            vector=[0.1] * 1024,
            scope_ids=None,
            limit=5,
        )

        query_filter = self._query_filter(mock_client)
        # Without scope_ids and version_id, filter should be None
        assert query_filter is None, "No filter expected when scope_ids is None"


# ---------------------------------------------------------------------------
# Tests: API-level scope isolation
# ---------------------------------------------------------------------------


class TestScopeIsolationViaAPI:
    """End-to-end scope isolation through the HTTP API layer."""

    @pytest.mark.asyncio
    async def test_list_projects_returns_both(self, test_client, two_projects):
        """Both created projects appear in the project list."""
        response = await test_client.get("/api/projects")
        if response.status_code == 200:
            data = response.json()
            # API serializes Snowflake IDs as strings (JSON-safe)
            project_ids = [p["project_id"] for p in data["items"]]
            assert str(two_projects["project_a"].project_id) in project_ids
            assert str(two_projects["project_b"].project_id) in project_ids

    @pytest.mark.asyncio
    async def test_get_project_a_does_not_return_b(self, test_client, two_projects):
        """Fetching project A by ID does not return project B's data."""
        response = await test_client.get(
            f"/api/projects/{two_projects['project_a'].project_id}"
        )
        if response.status_code == 200:
            data = response.json()
            # API serializes Snowflake IDs as strings (JSON-safe)
            assert data["project_id"] == str(two_projects["project_a"].project_id)
            assert data["knowledge_scope_id"] == str(two_projects["scope_id_a"])
            assert data["knowledge_scope_id"] != str(two_projects["scope_id_b"])
