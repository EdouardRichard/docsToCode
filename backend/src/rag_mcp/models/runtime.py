"""006 runtime period ORM models (T008).

Three operational tables (data-model §2/§3/§5):

- InstanceRegistry: per-process registration, worker_id allocation with the
  active partial unique index as the misconfiguration detection point
  (FR-030, clarification Q6).
- WriterLease: PostgreSQL single-writer lease. The partial unique index
  idx_lease_single_active gives the DB-level "at most one active writer"
  guarantee (FR-002); renewal 30s / expiry window 90s (clarification Q2).
- RuntimeMaintenanceLog: append-only TTL purge audit (FR-016).

These tables never enter the vector store or the knowledge base (blueprint
§20); they are runtime/audit state only.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rag_mcp.models import Base


class InstanceRegistry(Base):
    """One row per instance process lifetime (register -> heartbeat -> release)."""

    __tablename__ = "instance_registry"
    __table_args__ = (
        CheckConstraint("instance_mode IN ('writer', 'reader')", name="chk_registry_instance_mode"),
        CheckConstraint("process_role IN ('management', 'mcp')", name="chk_registry_process_role"),
        CheckConstraint("state IN ('active', 'released', 'expired')", name="chk_registry_state"),
        CheckConstraint("worker_id BETWEEN 0 AND 1023", name="chk_registry_worker_id"),
        # Misconfiguration detection point: concurrent active instances must
        # hold distinct worker_ids (data-model §2.2, FR-030).
        Index(
            "idx_registry_worker_active",
            "worker_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("idx_registry_expires", "expires_at", postgresql_where=text("state = 'active'")),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, comment="UUID v4 per process lifetime"
    )
    worker_id: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="Snowflake worker_id (0-1023, distinct among active)"
    )
    instance_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="'writer' or 'reader'"
    )
    process_role: Mapped[str] = mapped_column(
        String(12), nullable=False, comment="'management' or 'mcp'"
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
        comment="'active', 'released' or 'expired'",
    )
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )
    last_heartbeat_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, comment="Updated every heartbeat cycle"
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, comment="last_heartbeat_at + expiry window"
    )
    released_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, comment="Set on graceful deregistration"
    )

    def __repr__(self) -> str:
        return (
            f"<InstanceRegistry(instance_id={self.instance_id}, "
            f"worker_id={self.worker_id}, mode={self.instance_mode!r}, "
            f"role={self.process_role!r}, state={self.state!r})>"
        )


class WriterLease(Base):
    """Single-writer lease row (acquire -> renew -> release/expire)."""

    __tablename__ = "writer_lease"
    __table_args__ = (
        CheckConstraint("state IN ('active', 'released', 'expired')", name="chk_lease_state"),
        # DB-level single-writer guarantee: at most one active lease at any
        # instant (data-model §3.2, FR-002 double-write = 0).
        Index(
            "idx_lease_single_active",
            "state",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("idx_lease_expires", "expires_at", postgresql_where=text("state = 'active'")),
    )

    lease_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    holder_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instance_registry.instance_id"),
        nullable=False,
        comment="Holder (writer management process)",
    )
    state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
        comment="'active', 'released' or 'expired'",
    )
    acquired_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    renewed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, comment="Last renewal time"
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, comment="renewed_at + expiry window (90s)"
    )
    released_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, comment="Set on graceful release"
    )

    holder: Mapped[InstanceRegistry] = relationship(
        "InstanceRegistry", lazy="selectin", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<WriterLease(lease_id={self.lease_id}, "
            f"holder={self.holder_instance_id}, state={self.state!r})>"
        )


class RuntimeMaintenanceLog(Base):
    """Append-only TTL purge audit (only INSERT; self-purged by the same TTL)."""

    __tablename__ = "runtime_maintenance_log"
    __table_args__ = (
        CheckConstraint("event_type IN ('ttl_purge')", name="chk_maintenance_event_type"),
    )

    log_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False, comment="Snowflake ID"
    )
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Maintenance event type ('ttl_purge')"
    )
    purged_retrieval_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    purged_agentic_runs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    purged_maintenance_logs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return (
            f"<RuntimeMaintenanceLog(log_id={self.log_id}, "
            f"event_type={self.event_type!r}, purged={self.purged_retrieval_runs})>"
        )
