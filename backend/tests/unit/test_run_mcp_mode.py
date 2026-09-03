"""Unit tests for _run_mcp.py mode handling (T025, RED first).

FR-001/FR-004: the MCP entry supports --mode writer|reader (INSTANCE_MODE
equivalent). Both forms register search_knowledge + get_evidence; the
reader MCP never starts ingestion / TTL cleanup / migration background
tasks (maintenance belongs to the writer management process).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _import_run_mcp():
    import importlib

    if "_run_mcp" in sys.modules:
        return sys.modules["_run_mcp"]
    spec = importlib.util.spec_from_file_location("_run_mcp", BACKEND_ROOT / "_run_mcp.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_run_mcp"] = module
    spec.loader.exec_module(module)
    return module


class StubEmbedding:
    """Minimal EmbeddingProvider stand-in (no model load)."""

    async def embed_texts(self, texts):
        return [[0.0, 0.1] for _ in texts]

    def get_dimension(self) -> int:
        return 2

    async def embed_query(self, text):
        return [0.0, 0.1]


# ------------------------------------------------------------------- mode args


def test_parse_mode_writer() -> None:
    module = _import_run_mcp()
    args = module.parse_args(["--mode", "writer"])
    assert args.mode == "writer"


def test_parse_mode_reader() -> None:
    module = _import_run_mcp()
    args = module.parse_args(["--mode", "reader"])
    assert args.mode == "reader"


def test_parse_mode_default_from_instance_mode_env(monkeypatch) -> None:
    """INSTANCE_MODE is the equivalent env form (research §1.6)."""
    monkeypatch.setenv("INSTANCE_MODE", "reader")
    module = _import_run_mcp()
    args = module.parse_args([])
    assert args.mode == "reader"


def test_parse_mode_invalid_rejected() -> None:
    module = _import_run_mcp()
    with pytest.raises(SystemExit):
        module.parse_args(["--mode", "admin"])


def test_resolve_mode_cli_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("INSTANCE_MODE", "reader")
    module = _import_run_mcp()
    assert module.resolve_mode(module.parse_args(["--mode", "writer"])) == "writer"
    monkeypatch.setattr("os.environ", {**__import__("os").environ, "INSTANCE_MODE": "writer"})
    assert module.resolve_mode(module.parse_args([])) == "writer"


def test_instance_mode_invalid_env_rejected(monkeypatch) -> None:
    monkeypatch.setenv("INSTANCE_MODE", "admin")
    module = _import_run_mcp()
    with pytest.raises(ValueError):
        module.resolve_mode(module.parse_args([]))


# -------------------------------------------------------------- background work


def test_reader_mode_starts_no_maintenance_tasks() -> None:
    """FR-004: reader MCP runs no ingestion/TTL cleanup/migration tasks."""
    module = _import_run_mcp()
    tasks = module.background_tasks_for_mode("reader")
    assert "ttl_cleanup" not in tasks
    assert "ingestion" not in tasks
    assert "migration" not in tasks


def test_writer_mcp_also_has_no_maintenance_tasks() -> None:
    """Maintenance belongs to the writer MANAGEMENT process, not writer MCP."""
    module = _import_run_mcp()
    tasks = module.background_tasks_for_mode("writer")
    assert "ttl_cleanup" not in tasks
    assert "ingestion" not in tasks
    assert "migration" not in tasks


def test_both_modes_run_instance_heartbeat() -> None:
    """Instance rows stay alive via heartbeat (data-model §2.4)."""
    module = _import_run_mcp()
    assert "instance_heartbeat" in module.background_tasks_for_mode("reader")
    assert "instance_heartbeat" in module.background_tasks_for_mode("writer")


# --------------------------------------------------------- tool registration


@pytest.mark.asyncio
async def test_build_registers_both_tools_reader(monkeypatch) -> None:
    module = _import_run_mcp()
    server = module.build_mcp_server(
        mode="reader",
        embedding_provider=StubEmbedding(),
    )
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert "search_knowledge" in names
    assert "get_evidence" in names


@pytest.mark.asyncio
async def test_build_registers_both_tools_writer(monkeypatch) -> None:
    module = _import_run_mcp()
    server = module.build_mcp_server(
        mode="writer",
        embedding_provider=StubEmbedding(),
    )
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert "search_knowledge" in names
    assert "get_evidence" in names


# ------------------------------------------------------------- startup checks


@pytest.mark.asyncio
async def test_startup_sequence_registers_instance_with_mode(monkeypatch) -> None:
    """T026: both MCP forms register an instance row (process_role=mcp)."""
    module = _import_run_mcp()
    registered = {}

    class FakeRegistry:
        async def register(self, instance_id, instance_mode, process_role, worker_id=None, **kw):
            registered["instance_mode"] = instance_mode
            registered["process_role"] = process_role
            registered["worker_id"] = worker_id

            class R:
                registered = True

            R.worker_id = 7 if worker_id is None else worker_id
            return R()

    monkeypatch.setattr(
        "rag_mcp.runtime.instance_registry.InstanceRegistryService",
        lambda session_factory: FakeRegistry(),
    )

    async def fake_verify(session, expected_head=None):
        return "0061"

    monkeypatch.setattr(
        "rag_mcp.runtime.schema_compat.verify_schema_compat", fake_verify
    )

    identity = await module.startup_sequence("reader")
    assert registered["instance_mode"] == "reader"
    assert registered["process_role"] == "mcp"
    assert identity.worker_id >= 0


@pytest.mark.asyncio
async def test_startup_sequence_schema_mismatch_fails(monkeypatch) -> None:
    from rag_mcp.runtime.schema_compat import SchemaMismatchError

    module = _import_run_mcp()

    async def fake_verify(session, expected_head=None):
        raise SchemaMismatchError("db at 0050, code head 0061")

    monkeypatch.setattr(
        "rag_mcp.runtime.schema_compat.verify_schema_compat", fake_verify
    )
    with pytest.raises(SchemaMismatchError):
        await module.startup_sequence("reader")


def test_timeout_validation_enforced_at_startup(monkeypatch) -> None:
    """T022: both MCP forms validate timeout profiles before serving."""
    module = _import_run_mcp()
    monkeypatch.setenv("HOST_TIMEOUT_MS_CHATGPT_APP", "10000")
    with pytest.raises(ValueError):
        module.validate_timeout_profiles_at_startup()
