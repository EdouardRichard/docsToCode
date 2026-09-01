"""GraphStore abstract interface (T009).

Defines the protocol for graph-neighbor retrieval with mandatory isolation
scope (blueprint §8.3, FR-006/FR-007). Concrete implementations (e.g.
PostgresGraphStore with recursive CTE) MUST honour the isolation contract:
graph expansion never crosses scope boundaries (Constitution I/FR-010).

The interface also preserves migration capability (blueprint §8.3): a
future Neo4j store can implement the same protocol without changing callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphScope:
    """Isolation triple for graph operations (Constitution I, FR-010).

    Graph edges and expansion candidates MUST be filtered to this scope;
    cross-scope traversal is forbidden (leakage=0).
    """
    knowledge_scope_id: int
    project_id: int
    index_version: int


@dataclass
class GraphCandidate:
    """A single graph-expanded candidate with its hop path and weight.

    Conforms to graph-expansion-trace.graph_candidates items (FR-008/FR-023).
    """
    chunk_id: int
    knowledge_scope_id: int
    start_chunk_id: int
    edge_path: list[dict[str, Any]] = field(default_factory=list)
    hop_count: int = 1
    structure_weight: float = 1.0
    graph_rank: int = 0
    relation_is_hard: bool = True
    evidence_id: int | None = None  # back-filled when candidate survives as evidence (DM-1)


class GraphStore(ABC):
    """Abstract graph store: neighbor lookup + multi-hop expansion.

    All methods receive a GraphScope to enforce isolation. Implementations
    MUST NOT return edges or candidates outside the requested scope.
    """

    @abstractmethod
    async def get_neighbors(
        self,
        chunk_id: int,
        relation_types: list[str] | None,
        direction: str,
        hop: int,
        budget: int,
        scope: GraphScope,
    ) -> list[GraphCandidate]:
        """Retrieve neighbors of chunk_id within scope, up to hop jumps.

        Args:
            chunk_id: The starting node (chunk) identity.
            relation_types: Filter to these relation types; None = all.
            direction: 'out', 'in', or 'bidirectional'.
            hop: Max hops (1-3, guardrail FR-017).
            budget: Total candidate budget (global, not per-hop, FR-017).
            scope: Isolation triple (knowledge_scope_id, project_id, index_version).

        Returns:
            List of GraphCandidate sorted by structure_weight descending,
            truncated to budget. Only edges within scope are traversed.
        """
        ...

    @abstractmethod
    async def expand(
        self,
        start_chunk_ids: list[int],
        scope: GraphScope,
        hop: int = 2,
        budget: int = 10,
        direction: str = "bidirectional",
        relation_types: list[str] | None = None,
    ) -> list[GraphCandidate]:
        """Expand from multiple start chunks, returning globally-ranked candidates.

        The total candidate count MUST NOT exceed budget (global truncation
        by structure_weight, blueprint §12). Each candidate carries its
        edge_path (FR-008). Cross-scope edges are never returned (FR-010).

        Args:
            start_chunk_ids: Seeds for expansion.
            scope: Isolation triple.
            hop: Max hops (1-3).
            budget: Total candidate budget (global).
            direction: 'bidirectional' (default), 'out', or 'in'.
            relation_types: Filter; None = all hard+active-soft.

        Returns:
            Ranked GraphCandidate list, truncated to budget.
        """
        ...

    @abstractmethod
    async def write_edges(
        self,
        edges: list[dict[str, Any]],
        scope: GraphScope,
    ) -> int:
        """Persist hard-relation edges within the given scope.

        Used by extractors at ingestion time. Returns the number of edges
        written (duplicates within scope are ignored via unique constraint).
        """
        ...
