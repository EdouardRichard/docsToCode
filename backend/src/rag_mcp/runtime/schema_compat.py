"""Reader schema compatibility check (006, T020).

FR-007: migrations run only on the writer management process. A reader (or
any instance) verifies at startup that the shared database's
`alembic_version` equals the code's migration head; a mismatch fails
loudly with both versions so the operator can run the writer first.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SchemaMismatchError(RuntimeError):
    """Shared DB schema version does not match the code migration head."""


def _alembic_ini_path() -> Path:
    # backend/src/rag_mcp/runtime/schema_compat.py -> backend/alembic.ini
    return Path(__file__).resolve().parents[3] / "alembic.ini"


async def get_code_alembic_head() -> str:
    """Compute the migration head from the code's alembic script directory."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = _alembic_ini_path()
    if not ini.exists():
        raise SchemaMismatchError(f"alembic.ini not found at {ini}")
    config = Config(str(ini))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    if head is None:
        raise SchemaMismatchError("alembic script directory has no head revision")
    return head


async def _get_db_version(session: AsyncSession) -> str:
    result = await session.execute(text("SELECT version_num FROM alembic_version"))
    row = result.scalar()
    if row is None:
        raise SchemaMismatchError(
            "alembic_version table is empty; run the writer management process "
            "to apply migrations (FR-007)"
        )
    return str(row)


async def verify_schema_compat(
    session: AsyncSession, expected_head: str | None = None
) -> str:
    """Verify DB alembic_version == code head; raise SchemaMismatchError else.

    Returns the matched head revision. `expected_head` overrides the code
    head (tests / forced verification).
    """
    code_head = expected_head if expected_head is not None else await get_code_alembic_head()
    db_version = await _get_db_version(session)
    if db_version != code_head:
        raise SchemaMismatchError(
            f"shared database schema version {db_version!r} does not match the "
            f"code migration head {code_head!r}; start the writer management "
            f"process first so migrations run on it (FR-007)"
        )
    logger.info("schema compat verified: database at head %s", code_head)
    return code_head
