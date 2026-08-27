"""Concurrency isolation test (T055).

Verifies that 5 concurrent requests don't leak state between each other.
Tests FR-023 and SC-008.
"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_five_concurrent_requests_isolated(test_client):
    """5 concurrent requests maintain isolation (SC-008).

    Sends 2 project list + 2 knowledge source list + 1 health check
    concurrently and verifies all return correct, non-corrupted responses.
    """
    # First create two distinct projects
    resp1 = await test_client.post("/api/projects", json={"name": "Concurrency Project A"})
    resp2 = await test_client.post("/api/projects", json={"name": "Concurrency Project B"})
    assert resp1.status_code == 201
    assert resp2.status_code == 201

    async def list_projects():
        r = await test_client.get("/api/projects")
        return r.status_code, r.json()

    async def list_sources():
        r = await test_client.get("/api/knowledge-sources")
        return r.status_code, r.json()

    async def health_check():
        r = await test_client.get("/health")
        return r.status_code, r.json()

    # Launch 5 concurrent requests
    results = await asyncio.gather(
        list_projects(),
        list_projects(),
        list_sources(),
        list_sources(),
        health_check(),
    )

    # All should succeed
    for status, data in results:
        assert status == 200, f"Request failed with status {status}"

    # Health checks should be consistent
    health_results = [r for r in results if "version" in r[1]]
    for _, data in health_results:
        assert data["status"] == "ok"

    # Project lists should be consistent (same total count)
    project_results = [r for r in results if "items" in r[1] and "total" in r[1]]
    if len(project_results) >= 2:
        totals = [r[1]["total"] for r in project_results]
        assert totals[0] == totals[1], f"Inconsistent project counts: {totals}"


@pytest.mark.asyncio
async def test_request_id_uniqueness(test_client):
    """Each request gets a unique X-Request-ID header."""
    responses = await asyncio.gather(
        test_client.get("/health"),
        test_client.get("/health"),
        test_client.get("/health"),
    )

    request_ids = [r.headers.get("x-request-id") for r in responses]
    # All should have request IDs
    assert all(rid is not None for rid in request_ids), "Missing X-Request-ID headers"
    # All should be unique
    assert len(set(request_ids)) == len(request_ids), f"Duplicate request IDs: {request_ids}"
