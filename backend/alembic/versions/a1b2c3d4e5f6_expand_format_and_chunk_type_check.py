"""expand_format_and_chunk_type_check

Revision ID: a1b2c3d4e5f6
Revises: e8a2c1b7d4f3
Create Date: 2026-08-28 10:00:00.000000

003 Structured Asset Expansion: expands DB CHECK constraints for
knowledge_sources.format (2 -> 8 values) and chunks.chunk_type
(2 -> 18 values).  Ensures new formats and chunk types can be persisted
without violating constraints (data-model.md §6.2/§6.3).

Backward compatible: 001/002 existing 'markdown'/'java' sources and
'section'/'symbol' chunks remain valid under the new CHECK.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'e8a2c1b7d4f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Expand format and chunk_type CHECK constraints (data-model §6.2/§6.3)."""
    # knowledge_sources.format: 2 -> 8 values
    op.execute(
        "ALTER TABLE knowledge_sources "
        "DROP CONSTRAINT IF EXISTS knowledge_sources_format_check"
    )
    op.execute(
        "ALTER TABLE knowledge_sources "
        "ADD CONSTRAINT knowledge_sources_format_check "
        "CHECK (format IN ('markdown','java','openapi','ddl','go','python','word','pdf'))"
    )

    # chunks.chunk_type: 2 -> 18 values
    op.execute(
        "ALTER TABLE chunks "
        "DROP CONSTRAINT IF EXISTS chunks_chunk_type_check"
    )
    op.execute(
        "ALTER TABLE chunks "
        "ADD CONSTRAINT chunks_chunk_type_check "
        "CHECK (chunk_type IN ("
        "  'section','symbol',"
        "  'endpoint','schema',"
        "  'table','column','constraint','index','view','procedure',"
        "  'function','method','type','interface','class',"
        "  'heading','paragraph','list'"
        "))"
    )


def downgrade() -> None:
    """Revert to original 2-value CHECK constraints."""
    op.execute(
        "ALTER TABLE knowledge_sources "
        "DROP CONSTRAINT IF EXISTS knowledge_sources_format_check"
    )
    op.execute(
        "ALTER TABLE knowledge_sources "
        "ADD CONSTRAINT knowledge_sources_format_check "
        "CHECK (format IN ('markdown','java'))"
    )
    op.execute(
        "ALTER TABLE chunks "
        "DROP CONSTRAINT IF EXISTS chunks_chunk_type_check"
    )
    op.execute(
        "ALTER TABLE chunks "
        "ADD CONSTRAINT chunks_chunk_type_check "
        "CHECK (chunk_type IN ('section','symbol'))"
    )
