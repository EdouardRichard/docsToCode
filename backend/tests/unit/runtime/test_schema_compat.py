"""Unit tests for reader schema compatibility checks (T019, RED first).

FR-007: a reader instance must verify at startup that the shared database's
`alembic_version` matches the code's migration head, and fail loudly with
version information on mismatch.
"""

from __future__ import annotations

import pytest


def test_import_schema_compat() -> None:
    from rag_mcp.runtime.schema_compat import (  # noqa: F401
        SchemaMismatchError,
        verify_schema_compat,
    )


@pytest.mark.asyncio
async def test_head_consistent_passes(db_session):
    from rag_mcp.runtime.schema_compat import verify_schema_compat

    # The shared dev DB is at the code head (migrations applied)
    head = await verify_schema_compat(db_session)
    assert head  # returns the matched head revision


@pytest.mark.asyncio
async def test_head_mismatch_fails_with_versions(db_session):
    from rag_mcp.runtime.schema_compat import SchemaMismatchError, verify_schema_compat

    with pytest.raises(SchemaMismatchError) as excinfo:
        await verify_schema_compat(db_session, expected_head="9999_fake_rev")
    message = str(excinfo.value)
    # The error names both the database version and the code head
    assert "9999_fake_rev" in message


@pytest.mark.asyncio
async def test_mismatch_message_carries_db_version(db_session, monkeypatch):
    from rag_mcp.runtime import schema_compat

    async def fake_db_version(session):
        return "0045"

    monkeypatch.setattr(schema_compat, "_get_db_version", fake_db_version)
    from rag_mcp.runtime.schema_compat import SchemaMismatchError

    with pytest.raises(SchemaMismatchError) as excinfo:
        await schema_compat.verify_schema_compat(db_session)
    assert "0045" in str(excinfo.value)


@pytest.mark.asyncio
async def test_code_head_matches_alembic_script_directory():
    """The computed code head equals the alembic ScriptDirectory head."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    from rag_mcp.runtime.schema_compat import get_code_alembic_head

    alembic_ini = Path(__file__).resolve().parents[3] / "alembic.ini"
    config = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(config)
    expected = script.get_current_head()

    assert await get_code_alembic_head() == expected
