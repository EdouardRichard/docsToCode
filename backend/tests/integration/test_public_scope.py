"""Public knowledge domain minimal management (001 Phase 9, FR-002/FR-016).

Blueprint §23.4.1 / §25 first-phase success condition: public and project
knowledge distinguishable by source. Constitution I: public knowledge uses a
distinct public scope, never masquerading as a project; retrieval without an
explicit scope is still rejected.
"""

from __future__ import annotations

import pytest

from rag_mcp.utils.snowflake import generate_id


async def _make_public_scope(db_session, name: str) -> int:
    """Create a public scope + source + published version, return scope_id."""
    from sqlalchemy import text

    scope_id = generate_id()
    source_id = generate_id()
    version_id = generate_id()
    await db_session.execute(text(
        "INSERT INTO knowledge_scopes (scope_id, scope_type, name, status) "
        "VALUES (:sid, 'public', :name, 'active')"
    ), {"sid": scope_id, "name": name})
    await db_session.execute(text(
        "INSERT INTO knowledge_sources (source_id, knowledge_scope_id, filename, "
        "content_hash, format, size_bytes, status) "
        "VALUES (:src, :sid, 'pub.md', :ch, 'markdown', 100, 'published')"
    ), {"src": source_id, "sid": scope_id, "ch": f"pubhash-{scope_id}"})
    await db_session.execute(text(
        "INSERT INTO knowledge_versions (version_id, knowledge_scope_id, "
        "version_number, status) VALUES (:vid, :sid, 1, 'published')"
    ), {"vid": version_id, "sid": scope_id})
    await db_session.flush()
    return scope_id


class TestPublicScopeManagementApi:
    """POST/GET /api/projects/public-scopes (FR-002 minimal public-domain mgmt)."""

    @pytest.mark.asyncio
    async def test_create_and_list_public_scope(self, test_client, db_session):
        import uuid

        name = f"pub-knowledge-{uuid.uuid4().hex[:8]}"
        resp = await test_client.post("/api/projects/public-scopes", json={"name": name})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["scope_type"] == "public"
        assert body["status"] == "active"
        assert body["name"] == name
        # scope_id is a numeric snowflake string
        assert body["scope_id"].isdigit()

        lst = await test_client.get("/api/projects/public-scopes")
        assert lst.status_code == 200
        items = [i for i in lst.json()["items"] if i["name"] == name]
        assert len(items) == 1
        assert items[0]["scope_type"] == "public"

    @pytest.mark.asyncio
    async def test_duplicate_active_name_rejected(self, test_client, db_session):
        import uuid

        name = f"pub-dup-{uuid.uuid4().hex[:8]}"
        r1 = await test_client.post("/api/projects/public-scopes", json={"name": name})
        assert r1.status_code == 201
        r2 = await test_client.post("/api/projects/public-scopes", json={"name": name})
        assert r2.status_code == 409

    @pytest.mark.asyncio
    async def test_create_requires_name(self, test_client):
        resp = await test_client.post("/api/projects/public-scopes", json={})
        assert resp.status_code == 422


class TestPublicScopeRetrievalIdentity:
    """Public scope resolution + evidence domain identity (FR-016)."""

    @pytest.mark.asyncio
    async def test_public_scope_id_resolves_for_search(
        self, db_session, monkeypatch,
    ):
        """A numeric public scope ref resolves in resolve_project_refs."""
        from rag_mcp.services.retrieval_service import RetrievalService

        scope_id = await _make_public_scope(db_session, f"pub-{generate_id()}")
        await db_session.commit()

        service = RetrievalService.__new__(RetrievalService)
        service._session = db_session
        resolved, error = await service.resolve_project_refs([str(scope_id)])
        assert error is None
        assert resolved == [scope_id]

    @pytest.mark.asyncio
    async def test_unknown_scope_still_rejected(self, db_session):
        """Unresolvable refs still yield MISSING_PROJECT_SCOPE (Constitution I)."""
        from rag_mcp.services.retrieval_service import RetrievalService

        service = RetrievalService.__new__(RetrievalService)
        service._session = db_session
        resolved, error = await service.resolve_project_refs(["999999999999999"])
        assert resolved == []
        assert error is not None
        assert error["code"] == "MISSING_PROJECT_SCOPE"

    @pytest.mark.asyncio
    async def test_evidence_carries_public_domain_identity(self, db_session):
        """Evidence from a public scope carries knowledge_scope_type='public'."""
        from rag_mcp.services.retrieval_service import RetrievalService

        scope_id = await _make_public_scope(db_session, f"pub-{generate_id()}")
        chunk_id = generate_id()
        source_id = None
        from sqlalchemy import text
        row = (await db_session.execute(text(
            "SELECT source_id FROM knowledge_sources WHERE knowledge_scope_id = :sid"
        ), {"sid": scope_id})).scalar_one()
        source_id = row
        version_id = (await db_session.execute(text(
            "SELECT version_id FROM knowledge_versions WHERE knowledge_scope_id = :sid"
        ), {"sid": scope_id})).scalar_one()
        await db_session.execute(text(
            "INSERT INTO chunks (chunk_id, source_id, version_id, knowledge_scope_id, "
            "content_text, position_path, chunk_type, start_line, end_line, "
            "token_count, embedding_model, index_version) "
            "VALUES (:cid, :sid, :vid, :ksid, 'public capability doc', "
            "'pub.md §1', 'section', 1, 5, 10, 'test', 1)"
        ), {"cid": chunk_id, "sid": source_id, "vid": version_id, "ksid": scope_id})
        await db_session.flush()

        service = RetrievalService.__new__(RetrievalService)
        service._session = db_session
        results = [{
            "score": 0.9,
            "payload": {
                "chunk_id": str(chunk_id),
                "version_id": str(version_id),
                "knowledge_scope_id": str(scope_id),
                "position_path": "pub.md §1",
            },
        }]
        items = await service._build_evidence_items(results)
        assert len(items) == 1
        assert items[0]["knowledge_scope_type"] == "public"
        assert items[0]["knowledge_scope_id"] == str(scope_id)

    @pytest.mark.asyncio
    async def test_project_evidence_keeps_project_identity(self, db_session):
        """Project-scope evidence still carries knowledge_scope_type='project'."""
        from sqlalchemy import text

        from rag_mcp.services.retrieval_service import RetrievalService
        from tests.unit.test_postgres_graph_store import _setup_scope

        scope_id = generate_id()
        project_id = generate_id()
        version_id = generate_id()
        source_id = await _setup_scope(db_session, scope_id, project_id, version_id)
        chunk_id = generate_id()
        await db_session.execute(text(
            "INSERT INTO chunks (chunk_id, source_id, version_id, knowledge_scope_id, "
            "content_text, position_path, chunk_type, start_line, end_line, "
            "token_count, embedding_model, index_version) "
            "VALUES (:cid, :sid, :vid, :ksid, 'project doc', 'doc.md §1', "
            "'section', 1, 5, 10, 'test', 1)"
        ), {"cid": chunk_id, "sid": source_id, "vid": version_id, "ksid": scope_id})
        await db_session.flush()

        service = RetrievalService.__new__(RetrievalService)
        service._session = db_session
        results = [{
            "score": 0.9,
            "payload": {
                "chunk_id": str(chunk_id),
                "version_id": str(version_id),
                "knowledge_scope_id": str(scope_id),
                "position_path": "doc.md §1",
            },
        }]
        items = await service._build_evidence_items(results)
        assert len(items) == 1
        assert items[0]["knowledge_scope_type"] == "project"
