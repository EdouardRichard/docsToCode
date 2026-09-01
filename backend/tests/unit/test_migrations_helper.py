"""Shared helpers for migration tests (T004-T007).

Provides async functions to inspect the live PostgreSQL schema so migration
tests can assert on tables, columns, indexes, and constraints without coupling
to ORM model definitions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def table_exists(session: AsyncSession, table_name: str) -> bool:
    result = await session.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=:t)"
        ),
        {"t": table_name},
    )
    return bool(result.scalar())


async def get_columns(session: AsyncSession, table_name: str) -> dict[str, dict[str, Any]]:
    """Return {column_name: {data_type, is_nullable, column_default, ...}}."""
    result = await session.execute(
        text(
            "SELECT column_name, data_type, is_nullable, column_default, "
            "character_maximum_length, numeric_precision, numeric_scale "
            "FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=:t "
            "ORDER BY ordinal_position"
        ),
        {"t": table_name},
    )
    cols: dict[str, dict[str, Any]] = {}
    for row in result:
        cols[row[0]] = {
            "data_type": row[1],
            "is_nullable": row[2],
            "column_default": row[3],
            "char_max_length": row[4],
            "numeric_precision": row[5],
            "numeric_scale": row[6],
        }
    return cols


async def get_indexes(session: AsyncSession, table_name: str) -> list[str]:
    result = await session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='public' AND tablename=:t"
        ),
        {"t": table_name},
    )
    return [row[0] for row in result]


async def get_check_constraints(session: AsyncSession, table_name: str) -> list[str]:
    """Return list of CHECK constraint expression texts."""
    # Resolve the table OID first to avoid ::regclass bind-parameter conflict.
    oid_result = await session.execute(
        text(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relname = :t AND c.relkind='r' AND n.nspname='public'"
        ),
        {"t": table_name},
    )
    table_oid = oid_result.scalar()
    if not table_oid:
        return []
    result = await session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype='c' AND conrelid = :oid"
        ),
        {"oid": table_oid},
    )
    return [row[0] for row in result]


async def has_column(session: AsyncSession, table_name: str, column_name: str) -> bool:
    cols = await get_columns(session, table_name)
    return column_name in cols
