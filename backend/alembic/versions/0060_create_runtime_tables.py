"""create_runtime_tables

Revision ID: 0060
Revises: 0050
Create Date: 2026-09-10 02:00:00.000000

006 Runtime Hardening: creates the three runtime period tables
(data-model.md §2/§3/§5):

  - instance_registry      : per-process registration + worker_id allocation
                             (FR-030, partial unique active worker_id)
  - writer_lease           : single-writer lease (FR-002/FR-003, partial
                             unique active state = DB-level single writer)
  - runtime_maintenance_log: append-only TTL purge audit (FR-016)

These tables never enter the vector store or knowledge base (blueprint §20).
Migration runs on the writer management process only (readers verify the
alembic head at startup, FR-007).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0060'
down_revision: Union[str, Sequence[str], None] = '0050'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. instance_registry (data-model §2)
    op.create_table(
        'instance_registry',
        sa.Column('instance_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='UUID v4 per process lifetime'),
        sa.Column('worker_id', sa.SmallInteger(), nullable=False,
                  comment='Snowflake worker_id (0-1023, distinct among active)'),
        sa.Column('instance_mode', sa.String(length=8), nullable=False,
                  comment="'writer' or 'reader'"),
        sa.Column('process_role', sa.String(length=12), nullable=False,
                  comment="'management' or 'mcp'"),
        sa.Column('state', sa.String(length=16), nullable=False,
                  server_default=sa.text("'active'"),
                  comment="'active', 'released' or 'expired'"),
        sa.Column('started_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('last_heartbeat_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  comment='Updated every heartbeat cycle'),
        sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  comment='last_heartbeat_at + expiry window'),
        sa.Column('released_at', postgresql.TIMESTAMP(timezone=True), nullable=True,
                  comment='Set on graceful deregistration'),
        sa.PrimaryKeyConstraint('instance_id', name='pk_instance_registry'),
        sa.CheckConstraint("instance_mode IN ('writer', 'reader')",
                           name='chk_registry_instance_mode'),
        sa.CheckConstraint("process_role IN ('management', 'mcp')",
                           name='chk_registry_process_role'),
        sa.CheckConstraint("state IN ('active', 'released', 'expired')",
                           name='chk_registry_state'),
        sa.CheckConstraint('worker_id BETWEEN 0 AND 1023',
                           name='chk_registry_worker_id'),
    )
    # Partial unique index: concurrent active instances hold distinct
    # worker_ids — the same-WORKER_ID misconfiguration detection point.
    op.create_index(
        'idx_registry_worker_active', 'instance_registry', ['worker_id'],
        unique=True, postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        'idx_registry_expires', 'instance_registry', ['expires_at'],
        postgresql_where=sa.text("state = 'active'"),
    )

    # 2. writer_lease (data-model §3)
    op.create_table(
        'writer_lease',
        sa.Column('lease_id', sa.BigInteger(), nullable=False,
                  comment='Snowflake ID'),
        sa.Column('holder_instance_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Holder (writer management process)'),
        sa.Column('state', sa.String(length=16), nullable=False,
                  server_default=sa.text("'active'"),
                  comment="'active', 'released' or 'expired'"),
        sa.Column('acquired_at', postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('renewed_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  comment='Last renewal time'),
        sa.Column('expires_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  comment='renewed_at + expiry window (90s default)'),
        sa.Column('released_at', postgresql.TIMESTAMP(timezone=True), nullable=True,
                  comment='Set on graceful release'),
        sa.ForeignKeyConstraint(['holder_instance_id'], ['instance_registry.instance_id'],
                                name='fk_lease_holder_instance'),
        sa.PrimaryKeyConstraint('lease_id', name='pk_writer_lease'),
        sa.CheckConstraint("state IN ('active', 'released', 'expired')",
                           name='chk_lease_state'),
    )
    # Partial unique index: at most one active lease at any instant — the
    # DB-level guarantee that double-write events = 0 (FR-002).
    op.create_index(
        'idx_lease_single_active', 'writer_lease', ['state'],
        unique=True, postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        'idx_lease_expires', 'writer_lease', ['expires_at'],
        postgresql_where=sa.text("state = 'active'"),
    )

    # 3. runtime_maintenance_log (data-model §5, append-only)
    op.create_table(
        'runtime_maintenance_log',
        sa.Column('log_id', sa.BigInteger(), nullable=False,
                  comment='Snowflake ID'),
        sa.Column('event_type', sa.String(length=32), nullable=False,
                  comment="Maintenance event type ('ttl_purge')"),
        sa.Column('purged_retrieval_runs', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('purged_agentic_runs', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('purged_maintenance_logs', sa.Integer(), nullable=False,
                  server_default=sa.text('0')),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('log_id', name='pk_runtime_maintenance_log'),
        sa.CheckConstraint("event_type IN ('ttl_purge')",
                           name='chk_maintenance_event_type'),
    )


def downgrade() -> None:
    op.drop_table('runtime_maintenance_log')
    op.drop_index('idx_lease_expires', table_name='writer_lease')
    op.drop_index('idx_lease_single_active', table_name='writer_lease')
    op.drop_table('writer_lease')
    op.drop_index('idx_registry_expires', table_name='instance_registry')
    op.drop_index('idx_registry_worker_active', table_name='instance_registry')
    op.drop_table('instance_registry')
