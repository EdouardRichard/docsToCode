"""PostgreSQL recursive-CTE graph store (T010).

Implements GraphStore using WITH RECURSIVE for 1-3 hop expansion with
guardrail truncation (total budget, not per-hop, FR-017), scope isolation
(leakage=0, FR-010), and structure_weight global sorting (blueprint section 12).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.config import get_settings
from rag_mcp.graph.store.base import GraphCandidate, GraphScope, GraphStore
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)


class PostgresGraphStore(GraphStore):
    """PostgreSQL graph store with recursive CTE 1-3 hop + guardrails."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings = get_settings()
        self._graph_cfg = self._settings.graph

    async def expand(
        self,
        start_chunk_ids: list[int],
        scope: GraphScope,
        hop: int = 2,
        budget: int = 10,
        direction: str = "bidirectional",
        relation_types: list[str] | None = None,
    ) -> list[GraphCandidate]:
        """Expand from start chunks using recursive CTE within scope."""
        if not start_chunk_ids:
            return []

        max_hop = min(hop, self._graph_cfg.hop_max)
        budget = min(budget, self._graph_cfg.candidate_budget_max)
        cfg = self._graph_cfg

        # Build relation-type filter (safe: enum values, not user input)
        if relation_types:
            rt_list = ", ".join("'" + rt + "'" for rt in relation_types)
            rt_filter = " AND relation_type IN (" + rt_list + ")"
        else:
            rt_filter = ""

        params: dict[str, Any] = {
            "ksid": scope.knowledge_scope_id,
            "pid": scope.project_id,
            "iv": scope.index_version,
            "start_chunks": start_chunk_ids,
            "max_hop": max_hop,
            "budget": budget,
            "hard_w": cfg.structure_weight_hard,
            "soft_w": cfg.structure_weight_soft,
            "decay": cfg.structure_weight_hop_decay,
        }

        fwd = self._forward_edges_cte(rt_filter)
        rev = self._reverse_edges_cte(rt_filter)
        if direction == "bidirectional":
            directed_union = fwd + " UNION ALL " + rev
        elif direction == "out":
            directed_union = fwd
        else:
            directed_union = rev

        sql = self._build_expansion_sql(directed_union)
        result = await self._session.execute(text(sql), params)
        rows = result.fetchall()

        candidates: list[GraphCandidate] = []
        for i, row in enumerate(rows):
            candidates.append(GraphCandidate(
                chunk_id=row[0],
                knowledge_scope_id=scope.knowledge_scope_id,
                start_chunk_id=row[1],
                edge_path=row[2] or [],
                hop_count=row[3],
                structure_weight=float(row[4]),
                graph_rank=i + 1,
                relation_is_hard=row[5],
                evidence_id=None,
            ))

        logger.info(
            "Graph expand: scope=%d/%d/%d, candidates=%d, budget=%d",
            scope.knowledge_scope_id, scope.project_id,
            scope.index_version, len(candidates), budget,
        )
        return candidates

    async def get_neighbors(
        self,
        chunk_id: int,
        relation_types: list[str] | None,
        direction: str,
        hop: int,
        budget: int,
        scope: GraphScope,
    ) -> list[GraphCandidate]:
        """Get neighbors of a single chunk (delegates to expand)."""
        return await self.expand(
            [chunk_id], scope, hop, budget, direction, relation_types)

    async def write_edges(
        self,
        edges: list[dict[str, Any]],
        scope: GraphScope,
    ) -> int:
        """Persist hard-relation edges within the given scope."""
        if not edges:
            return 0
        count = 0
        for edge_data in edges:
            edge_id = edge_data.get("edge_id") or generate_id()
            try:
                pe = edge_data.get("parse_evidence", {})
                if isinstance(pe, dict):
                    pe = json.dumps(pe)
                await self._session.execute(text(
                    "INSERT INTO graph_edge (edge_id, knowledge_scope_id, "
                    "project_id, index_version, source_chunk_id, "
                    "target_chunk_id, relation_type, direction, is_hard, "
                    "version, parse_evidence) "
                    "VALUES (:eid, :ksid, :pid, :iv, :src, :tgt, :rt, "
                    ":dir, true, :v, CAST(:pe AS jsonb)) ON CONFLICT DO NOTHING"
                ), {
                    "eid": edge_id,
                    "ksid": scope.knowledge_scope_id,
                    "pid": scope.project_id,
                    "iv": scope.index_version,
                    "src": edge_data["source_chunk_id"],
                    "tgt": edge_data["target_chunk_id"],
                    "rt": edge_data["relation_type"],
                    "dir": edge_data.get("direction", "out"),
                    "v": edge_data.get("version", 1),
                    "pe": pe,
                })
                count += 1
            except Exception as exc:
                logger.warning("Failed to write edge %s: %s", edge_id, exc)
        return count

    async def mark_graph_unretrievable(self, scope: GraphScope) -> None:
        """Mark the scope's versions as graph_ready=false (non-retrievable).

        Blueprint sec 5: cleanup first marks non-retrievable, then async deletes.
        """
        await self._session.execute(text(
            "UPDATE knowledge_versions SET graph_ready = false "
            "WHERE knowledge_scope_id = :ksid"
        ), {"ksid": scope.knowledge_scope_id})
        logger.info("Marked graph relations non-retrievable for scope %d",
                     scope.knowledge_scope_id)

    async def delete_graph_relations(self, scope: GraphScope) -> int:
        """Delete graph_edge and soft_relation records for the scope.

        Returns total deleted count. Only affects the given scope (FR-010).
        """
        result = await self._session.execute(text(
            "DELETE FROM graph_edge WHERE knowledge_scope_id = :ksid "
            "AND project_id = :pid AND index_version = :iv"
        ), {"ksid": scope.knowledge_scope_id, "pid": scope.project_id,
            "iv": scope.index_version})
        hard_deleted = result.rowcount

        try:
            result2 = await self._session.execute(text(
                "DELETE FROM soft_relation WHERE knowledge_scope_id = :ksid "
                "AND project_id = :pid AND index_version = :iv"
            ), {"ksid": scope.knowledge_scope_id, "pid": scope.project_id,
                "iv": scope.index_version})
            soft_deleted = result2.rowcount
        except Exception:
            soft_deleted = 0

        total = hard_deleted + soft_deleted
        logger.info("Deleted %d graph relations (hard=%d, soft=%d) for scope %d",
                     total, hard_deleted, soft_deleted, scope.knowledge_scope_id)
        return total

    async def cleanup_scope(self, scope: GraphScope) -> None:
        """Orchestrate cleanup: mark non-retrievable, then delete (blueprint sec 5)."""
        await self.mark_graph_unretrievable(scope)
        await self.delete_graph_relations(scope)

    def _forward_edges_cte(self, rt_filter: str) -> str:
        return (
            "SELECT source_chunk_id AS from_chunk, target_chunk_id AS to_chunk, "
            "edge_id, relation_type, direction, is_hard FROM graph_edge "
            "WHERE knowledge_scope_id = :ksid AND project_id = :pid "
            "AND index_version = :iv" + rt_filter
        )

    def _reverse_edges_cte(self, rt_filter: str) -> str:
        return (
            "SELECT target_chunk_id AS from_chunk, source_chunk_id AS to_chunk, "
            "edge_id, "
            "CASE relation_type "
            "WHEN 'calls' THEN 'called_by' "
            "WHEN 'called_by' THEN 'calls' "
            "WHEN 'fk_references' THEN 'fk_referenced_by' "
            "WHEN 'fk_referenced_by' THEN 'fk_references' "
            "ELSE relation_type END AS relation_type, "
            "CASE direction WHEN 'out' THEN 'in' ELSE 'out' END AS direction, "
            "is_hard FROM graph_edge "
            "WHERE knowledge_scope_id = :ksid AND project_id = :pid "
            "AND index_version = :iv" + rt_filter
        )

    def _build_expansion_sql(self, directed_union: str) -> str:
        return (
            "WITH RECURSIVE directed_edges AS ( " + directed_union + " ), "
            "expansion AS ( "
            "SELECT de.to_chunk AS chunk_id, de.from_chunk AS start_chunk_id, "
            "jsonb_build_array(jsonb_build_object('hop', 1, 'edge_id', de.edge_id, "
            "'relation_type', de.relation_type, 'direction', de.direction, "
            "'is_hard', de.is_hard)) AS edge_path, "
            "1 AS hop, "
            "CASE WHEN de.is_hard THEN CAST(:hard_w AS float8) ELSE CAST(:soft_w AS float8) END AS structure_weight, "
            "de.is_hard, ARRAY[de.from_chunk] AS visited "
            "FROM directed_edges de WHERE de.from_chunk = ANY(:start_chunks) "
            "UNION ALL "
            "SELECT de.to_chunk, exp.start_chunk_id, "
            "exp.edge_path || jsonb_build_array(jsonb_build_object('hop', exp.hop + 1, "
            "'edge_id', de.edge_id, 'relation_type', de.relation_type, "
            "'direction', de.direction, 'is_hard', de.is_hard)), "
            "exp.hop + 1, "
            "CASE WHEN de.is_hard THEN CAST(:hard_w AS float8) * power(CAST(:decay AS float8), exp.hop) "
            "ELSE CAST(:soft_w AS float8) * power(CAST(:decay AS float8), exp.hop) END, "
            "de.is_hard, exp.visited || de.from_chunk "
            "FROM expansion exp JOIN directed_edges de ON de.from_chunk = exp.chunk_id "
            "WHERE exp.hop < :max_hop AND NOT (de.to_chunk = ANY(exp.visited)) "
            "), "
            "best AS ( "
            "SELECT DISTINCT ON (chunk_id) chunk_id, start_chunk_id, edge_path, "
            "hop, structure_weight, is_hard FROM expansion "
            "ORDER BY chunk_id, structure_weight DESC, hop ASC "
            ") "
            "SELECT chunk_id, start_chunk_id, edge_path, hop AS hop_count, "
            "structure_weight, is_hard AS relation_is_hard "
            "FROM best ORDER BY structure_weight DESC, hop_count ASC, chunk_id ASC "
            "LIMIT :budget"
        )
