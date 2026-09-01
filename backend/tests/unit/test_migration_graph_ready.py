"""Unit test for graph_ready capability migration (T007).

Validates that the knowledge_versions table (or knowledge_capabilities) has
a graph_ready boolean column with default false (data-model §5).

This test MUST FAIL before the migration is written/applied (TDD).
"""

from __future__ import annotations

import pytest

from tests.unit.test_migrations_helper import get_columns, has_column, table_exists


@pytest.mark.asyncio
async def test_graph_ready_column_on_knowledge_versions(db_session):
    """knowledge_versions must have a graph_ready boolean column (default false)."""
    cols = await get_columns(db_session, "knowledge_versions")
    assert "graph_ready" in cols, "knowledge_versions must have graph_ready column"
    assert cols["graph_ready"]["data_type"] == "boolean"
    assert cols["graph_ready"]["is_nullable"] == "NO"
    # default should be false
    default = str(cols["graph_ready"]["column_default"]).lower()
    assert "false" in default, f"graph_ready default must be false, got: {default}"
