"""Contract tests for Knowledge Source upload API (T022).

Validates that the knowledge source API endpoints handle file uploads correctly,
reject unsupported formats, and return proper KnowledgeSourceResponse structure.
"""

import io

import pytest


# Uses test_client fixture from conftest.py (with DB session override)


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_format(test_client):
    """POST /api/knowledge-sources rejects files with unsupported extensions."""
    content = b"some content here"
    response = await test_client.post(
        "/api/knowledge-sources?scope_id=123",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
    )
    assert response.status_code == 400
    data = response.json()
    assert "Unsupported" in data.get("detail", "") or "unsupported" in str(data).lower()


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(test_client):
    """POST /api/knowledge-sources rejects empty files."""
    response = await test_client.post(
        "/api/knowledge-sources?scope_id=123",
        files={"file": ("empty.md", io.BytesIO(b""), "text/markdown")},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_upload_accepts_markdown_file(test_client):
    """POST /api/knowledge-sources accepts .md files and returns valid structure."""
    # First create a project to get a valid scope_id
    proj_resp = await test_client.post("/api/projects", json={"name": "Upload Test MD"})
    assert proj_resp.status_code == 201
    scope_id = proj_resp.json()["knowledge_scope_id"]

    content = b"# Test\n\nSome markdown content here."
    response = await test_client.post(
        f"/api/knowledge-sources?scope_id={scope_id}",
        files={"file": ("test.md", io.BytesIO(content), "text/markdown")},
    )
    assert response.status_code == 201
    data = response.json()
    assert "source_id" in data
    assert "filename" in data
    assert data["filename"] == "test.md"
    assert data["format"] == "markdown"
    assert data["status"] == "uploaded"
    assert "content_hash" in data
    assert "size_bytes" in data
    assert data["size_bytes"] == len(content)


@pytest.mark.asyncio
async def test_upload_accepts_java_file(test_client):
    """POST /api/knowledge-sources accepts .java files."""
    # First create a project to get a valid scope_id
    proj_resp = await test_client.post("/api/projects", json={"name": "Upload Test Java"})
    assert proj_resp.status_code == 201
    scope_id = proj_resp.json()["knowledge_scope_id"]

    content = b"public class Test { }"
    response = await test_client.post(
        f"/api/knowledge-sources?scope_id={scope_id}",
        files={"file": ("Test.java", io.BytesIO(content), "text/x-java")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["format"] == "java"


@pytest.mark.asyncio
async def test_list_knowledge_sources_structure(test_client):
    """GET /api/knowledge-sources returns KnowledgeSourceListResponse structure."""
    response = await test_client.get("/api/knowledge-sources")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_get_nonexistent_source_returns_404(test_client):
    """GET /api/knowledge-sources/{id} with invalid ID returns 404."""
    response = await test_client.get("/api/knowledge-sources/999999999999")
    assert response.status_code == 404
