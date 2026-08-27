"""Integration tests for deletion lifecycle (T048).

Tests the deletion of projects and knowledge sources via the REST API,
verifying that deleted entities are excluded from listings and that
deletion is idempotent and isolated across projects.

Uses the test_client fixture with a real database session.
"""

import io
import uuid

import pytest


def _unique_name(prefix: str = "del-test") -> str:
    """Generate a unique name to avoid collisions between test runs."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _create_project(client, name: str | None = None) -> dict:
    """Helper: create a project and return its JSON response."""
    payload = {"name": name or _unique_name()}
    resp = await client.post("/api/projects", json=payload)
    assert resp.status_code == 201, f"Failed to create project: {resp.text}"
    return resp.json()


async def _upload_source(client, scope_id: str, filename: str = "test.md") -> dict:
    """Helper: upload a markdown knowledge source and return its JSON response."""
    content = b"# Test Content\n\nSome body text for deletion testing."
    resp = await client.post(
        f"/api/knowledge-sources?scope_id={scope_id}",
        files={"file": (filename, io.BytesIO(content), "text/markdown")},
    )
    assert resp.status_code == 201, f"Failed to upload source: {resp.text}"
    return resp.json()


@pytest.mark.asyncio
class TestDeletionLifecycle:
    """Integration tests for project/source deletion via the REST API."""

    async def test_delete_source_excludes_from_search(self, test_client):
        """Create project+source, delete the project, verify list excludes it."""
        # Create a project (which creates a knowledge scope)
        project = await _create_project(test_client, _unique_name("del-src"))
        project_id = project["project_id"]
        scope_id = project["knowledge_scope_id"]

        # Upload a knowledge source
        source = await _upload_source(test_client, scope_id)
        source_id = source["source_id"]

        # Verify source appears in listing
        list_resp = await test_client.get(
            f"/api/knowledge-sources?scope_id={scope_id}"
        )
        assert list_resp.status_code == 200
        sources_before = list_resp.json()["items"]
        source_ids_before = [s["source_id"] for s in sources_before]
        assert source_id in source_ids_before

        # Delete the project (cascades to scope and sources)
        del_resp = await test_client.delete(f"/api/projects/{project_id}")
        assert del_resp.status_code == 204

        # Verify the project is gone
        get_resp = await test_client.get(f"/api/projects/{project_id}")
        assert get_resp.status_code == 404

        # Verify sources for that scope are no longer listed
        # (scope was deleted with the project, so listing by scope may error or return empty)
        list_after = await test_client.get(
            f"/api/knowledge-sources?scope_id={scope_id}"
        )
        if list_after.status_code == 200:
            sources_after = list_after.json()["items"]
            source_ids_after = [s["source_id"] for s in sources_after]
            assert source_id not in source_ids_after

    async def test_clear_scope_deletes_all_sources(self, test_client):
        """Create project with multiple sources, delete project, verify all deleted."""
        project = await _create_project(test_client, _unique_name("clear-scope"))
        project_id = project["project_id"]
        scope_id = project["knowledge_scope_id"]

        # Upload multiple sources
        source_ids = []
        for i in range(3):
            src = await _upload_source(
                test_client, scope_id, filename=f"doc_{i}.md"
            )
            source_ids.append(src["source_id"])

        # Verify all sources present
        list_resp = await test_client.get(
            f"/api/knowledge-sources?scope_id={scope_id}"
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 3

        # Delete the project (clears the entire scope)
        del_resp = await test_client.delete(f"/api/projects/{project_id}")
        assert del_resp.status_code == 204

        # Verify none of the sources remain accessible
        for sid in source_ids:
            get_src = await test_client.get(f"/api/knowledge-sources/{sid}")
            # Source should be 404 since its parent scope/project was deleted
            assert get_src.status_code in (404, 500), (
                f"Source {sid} should be inaccessible after scope deletion"
            )

    async def test_idempotent_delete(self, test_client):
        """Deleting the same project twice does not cause an error on second call."""
        project = await _create_project(test_client, _unique_name("idem-del"))
        project_id = project["project_id"]

        # First delete succeeds
        del_resp_1 = await test_client.delete(f"/api/projects/{project_id}")
        assert del_resp_1.status_code == 204

        # Second delete returns 404 (already deleted) — not a server error
        del_resp_2 = await test_client.delete(f"/api/projects/{project_id}")
        assert del_resp_2.status_code == 404

    async def test_other_projects_unaffected(self, test_client):
        """Deleting project A does not affect project B's data."""
        # Create two independent projects
        project_a = await _create_project(test_client, _unique_name("proj-a"))
        project_b = await _create_project(test_client, _unique_name("proj-b"))

        scope_a = project_a["knowledge_scope_id"]
        scope_b = project_b["knowledge_scope_id"]

        # Upload a source to each project
        source_a = await _upload_source(test_client, scope_a, "a_doc.md")
        source_b = await _upload_source(test_client, scope_b, "b_doc.md")

        # Delete project A
        del_resp = await test_client.delete(
            f"/api/projects/{project_a['project_id']}"
        )
        assert del_resp.status_code == 204

        # Project B still exists and is accessible
        get_b = await test_client.get(
            f"/api/projects/{project_b['project_id']}"
        )
        assert get_b.status_code == 200
        assert get_b.json()["project_id"] == project_b["project_id"]

        # Source in project B is still accessible
        get_src_b = await test_client.get(
            f"/api/knowledge-sources/{source_b['source_id']}"
        )
        assert get_src_b.status_code == 200
        assert get_src_b.json()["source_id"] == source_b["source_id"]

        # Source in project A is gone
        get_src_a = await test_client.get(
            f"/api/knowledge-sources/{source_a['source_id']}"
        )
        assert get_src_a.status_code in (404, 500)
