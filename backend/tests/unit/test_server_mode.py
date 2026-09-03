"""Unit tests for server.py mode enforcement (T023, RED first).

FR-001/FR-004: the management process (server.py) is writer-only —
`--mode reader` must fail loudly (readers run only the read-only MCP); a
writer that cannot acquire the lease must NOT enter write mode; the TTL
cleanup loop belongs to the writer management process only.
"""

from __future__ import annotations

import pytest


def test_import_server_mode_helpers() -> None:
    from rag_mcp.server import validate_management_mode  # noqa: F401


def test_management_mode_reader_rejected() -> None:
    """FR-001: `--mode reader` on the management process is an error."""
    from rag_mcp.server import validate_management_mode

    with pytest.raises(ValueError) as excinfo:
        validate_management_mode("reader")
    message = str(excinfo.value)
    # The error tells the operator what to run instead
    assert "reader" in message
    assert "_run_mcp" in message or "MCP" in message


def test_management_mode_writer_accepted() -> None:
    from rag_mcp.server import validate_management_mode

    validate_management_mode("writer")


@pytest.mark.asyncio
async def test_lifespan_fails_when_lease_acquisition_fails(monkeypatch) -> None:
    """Writer lease acquisition failure refuses write mode (FR-002)."""
    from rag_mcp import server

    async def boom(settings):
        raise RuntimeError(
            "writer lease already held by instance 00000000-0000-0000-0000-000000000001"
        )

    monkeypatch.setattr(server, "_acquire_writer_lease", boom)
    with pytest.raises(RuntimeError):
        async with server.lifespan(server.app):
            pass


@pytest.mark.asyncio
async def test_lifespan_runs_ttl_loop_after_lease(monkeypatch) -> None:
    """TTL cleanup only starts after the lease is held (T024 order)."""
    from rag_mcp import server

    events: list[str] = []

    class FakeLease:
        lease_id = 1
        acquired = True

    async def fake_acquire(settings):
        events.append("lease")
        return FakeLease()

    async def fake_renewal_loop(lease_id, interval_s, expiry_window_s=90):
        events.append("renewal_loop")
        await asyncio_never()

    async def fake_ttl_loop(interval_s):
        events.append("ttl_loop")
        await asyncio_never()

    async def asyncio_never():
        import asyncio

        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_acquire_writer_lease", fake_acquire)
    monkeypatch.setattr(server, "_lease_renewal_loop", fake_renewal_loop)
    monkeypatch.setattr(server, "_ttl_cleanup_loop", fake_ttl_loop)
    monkeypatch.setattr(server, "_validate_timeout_profiles_or_fail", lambda s: None)

    import asyncio

    try:
        async with server.lifespan(server.app):
            # Let the created tasks start their first coroutine step.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert events[0] == "lease"
            assert "ttl_loop" in events
    finally:
        pass


def test_metrics_route_placeholder_registered() -> None:
    """T024/T062: metrics route is mounted on the writer management plane."""
    from rag_mcp.server import app

    # FastAPI includes routers as _IncludedRouter markers; resolve the real
    # path set through the OpenAPI schema.
    paths = set(app.openapi()["paths"].keys())
    assert "/runtime/metrics" in paths


@pytest.mark.asyncio
async def test_lifespan_enforces_timeout_profile_validation(monkeypatch) -> None:
    """T022: startup calls the timeout profile validation (reversed config dies)."""
    from rag_mcp import server

    called = {"validate": 0}

    def fake_validate(settings):
        called["validate"] += 1
        raise ValueError("server total timeout (30000ms) must be strictly smaller")

    monkeypatch.setattr(server, "_validate_timeout_profiles_or_fail", fake_validate)
    with pytest.raises(ValueError):
        async with server.lifespan(server.app):
            pass
    assert called["validate"] == 1
