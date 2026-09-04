"""001 T066: the two MCP tools must advertise readOnlyHint=true (FR-022).

Both search_knowledge and get_evidence are read-only retrieval tools; they
must not be presented to a client as mutating tools.
"""
from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP


async def _register_all():
    from rag_mcp.mcp.get_evidence import register_get_evidence_tool
    from rag_mcp.mcp.search_knowledge import register_search_knowledge_tool

    server = FastMCP("test")
    register_search_knowledge_tool(server, None, None, None, None)  # type: ignore[arg-type]
    register_get_evidence_tool(server, None)  # type: ignore[arg-type]
    return await server.list_tools()


@pytest.mark.asyncio
async def test_both_tools_annotated_read_only():
    tools = await _register_all()
    by_name = {t.name: t for t in tools}
    for name in ("search_knowledge", "get_evidence"):
        assert name in by_name, f"{name} not registered"
        ann = by_name[name].annotations
        assert ann is not None, f"{name} missing annotations"
        assert ann.readOnlyHint is True, f"{name} must be read-only"

@pytest.mark.asyncio
async def test_search_knowledge_mirrors_text_content(monkeypatch):
    """T067: FastMCP SDK auto-marshals a dict return into BOTH a mirrored
    TextContent (JSON string) and structuredContent (dict)."""
    import rag_mcp.mcp.search_knowledge as sk

    async def fake_core(**kwargs):
        return {
            "completion_status": "complete",
            "evidence": [{"evidence_id": "1"}],
            "request_id": "r1",
        }

    monkeypatch.setattr(sk, "search_knowledge_core", fake_core)
    server = FastMCP("test")
    sk.register_search_knowledge_tool(server, None, None, None, None)  # type: ignore[arg-type]

    result = await server.call_tool(
        "search_knowledge", {"query": "q", "project_scope": ["s"]},
    )
    # convert_result returns (unstructured_content, structured_content)
    assert isinstance(result, tuple)
    content_blocks, structured = result
    assert isinstance(content_blocks, list) and len(content_blocks) == 1
    assert content_blocks[0].type == "text"
    assert structured is not None
    assert structured["completion_status"] == "complete"
