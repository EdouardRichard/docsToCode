"""006 runtime hardening package.

Runtime-period components (blueprint §21.2/§20):

- WriteCoordinator: single-writer lease arbitration (FR-002/FR-003)
- SourceObjectStore: source object storage evolution interface (FR-006)
- InstanceRegistry: instance registration + worker_id allocation (FR-030)
- schema_compat: reader alembic head verification (FR-007)
- metrics: query-time runtime metrics aggregation (FR-016/FR-017, T058)

These components never touch the retrieval path or the MCP contracts.
"""

from rag_mcp.runtime.write_coordinator import (
    LeaseAcquisition,
    LeaseInfo,
    PostgresLeaseWriteCoordinator,
    WriteCoordinator,
)
from rag_mcp.runtime.instance_registry import (
    InstanceRegistryService,
    RegistrationResult,
)
from rag_mcp.runtime.source_object_store import (
    LocalFilesystemSourceObjectStore,
    SourceObjectStore,
)

__all__ = [
    "WriteCoordinator",
    "PostgresLeaseWriteCoordinator",
    "LeaseAcquisition",
    "LeaseInfo",
    "InstanceRegistryService",
    "RegistrationResult",
    "SourceObjectStore",
    "LocalFilesystemSourceObjectStore",
]
