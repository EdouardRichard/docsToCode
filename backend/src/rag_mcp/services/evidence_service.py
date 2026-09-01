"""EvidenceService: retrieves and validates individual evidence chunks.

Provides the get_evidence operation that expands a single evidence_id
(from search_knowledge results) into its full content, parent context,
and metadata. Enforces scope isolation to prevent cross-project data leakage.

Conforms to mcp-get-evidence.schema.json output structure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.models.chunk import Chunk
from rag_mcp.models.knowledge_scope import KnowledgeScope
from rag_mcp.models.knowledge_version import KnowledgeVersion
from rag_mcp.models.project import Project

if TYPE_CHECKING:
    # Graph models are only needed for type hints; importing them lazily here
    # avoids pulling the graph ORM into the evidence retrieval path at runtime
    # and sidesteps the models <-> graph.models import cycle.
    from rag_mcp.graph.models import GraphEdge, SoftRelation

logger = logging.getLogger(__name__)


class EvidenceService:
    """Service for retrieving and validating individual evidence chunks.

    Responsibilities:
    - Parse evidence_id as chunk_id
    - Query Chunk table with full metadata
    - Validate scope membership against requested project_scopes
    - Load parent chunk context when available
    - Return structured response matching mcp-get-evidence.schema.json
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_evidence(
        self,
        evidence_id: str,
        project_scopes: list[str],
    ) -> dict[str, Any]:
        """Retrieve full evidence content by evidence_id with scope validation.

        Args:
            evidence_id: The chunk_id string from search_knowledge results.
            project_scopes: List of project references (ID, alias, or repo_path)
                that must include the evidence's owning scope.

        Returns:
            Dict conforming to mcp-get-evidence.schema.json output structure.
        """
        # 1. Parse evidence_id as chunk_id
        try:
            chunk_id = int(evidence_id)
        except (ValueError, TypeError):
            return {
                "evidence_id": evidence_id,
                "status": "unavailable",
                "error": {
                    "code": "INVALID_EVIDENCE_ID",
                    "message": f"Evidence ID '{evidence_id}' is not a valid identifier.",
                },
            }

        # 2. Resolve project_scopes to knowledge_scope_ids
        resolved_scope_ids = await self._resolve_scope_ids(project_scopes)

        # 3. Query Chunk by chunk_id
        result = await self._session.execute(
            select(Chunk).where(Chunk.chunk_id == chunk_id)
        )
        chunk = result.scalar_one_or_none()

        if chunk is None:
            logger.info("Evidence %s not found in database", evidence_id)
            return {
                "evidence_id": evidence_id,
                "status": "unavailable",
                "error": {
                    "code": "EVIDENCE_NOT_FOUND",
                    "message": f"No evidence found with ID '{evidence_id}'. It may have been deleted or superseded.",
                },
            }

        # 4. Validate chunk's knowledge_scope_id is in requested scopes.
        #    FR-019 / SC-002: reject when scopes are missing, unresolvable,
        #    or do not contain the chunk's owning scope. Never fall through
        #    to 'available' when scope validation fails.
        if not resolved_scope_ids or chunk.knowledge_scope_id not in resolved_scope_ids:
            logger.warning(
                "Scope mismatch for evidence %s: chunk scope=%d, requested scopes=%s (resolved=%s)",
                evidence_id, chunk.knowledge_scope_id, project_scopes, resolved_scope_ids,
            )
            return {
                "evidence_id": evidence_id,
                "status": "scope_mismatch",
                "error": {
                    "code": "SCOPE_MISMATCH",
                    "message": (
                        f"Evidence '{evidence_id}' does not belong to any of the "
                        f"requested project scopes. Ensure the correct project is specified."
                    ),
                },
            }

        # 5. Load parent chunk context if parent_chunk_id exists
        parent_context: str | None = None
        if chunk.parent_chunk_id is not None:
            parent_result = await self._session.execute(
                select(Chunk.content_text).where(
                    Chunk.chunk_id == chunk.parent_chunk_id,
                )
            )
            parent_content = parent_result.scalar_one_or_none()
            if parent_content is not None:
                parent_context = parent_content

        # 6. Fetch version number
        version_result = await self._session.execute(
            select(KnowledgeVersion.version_number).where(
                KnowledgeVersion.version_id == chunk.version_id,
            )
        )
        source_version = version_result.scalar_one_or_none() or 0

        # 7. Determine scope type
        scope_type = await self._get_scope_type(chunk.knowledge_scope_id)

        # 8. Build successful response
        response: dict[str, Any] = {
            "evidence_id": evidence_id,
            "full_content": chunk.content_text,
            "source_version": source_version,
            "source_position": chunk.position_path,
            "knowledge_scope_id": str(chunk.knowledge_scope_id),
            "knowledge_scope_type": scope_type,
            "status": "available",
        }

        if parent_context is not None:
            response["parent_context"] = parent_context

        logger.info(
            "Evidence retrieved: id=%s, scope=%d, version=%d",
            evidence_id, chunk.knowledge_scope_id, source_version,
        )
        return response

    def annotate_evidence(
        self,
        evidence_dict: dict[str, Any],
        relation_edge: GraphEdge | None = None,
        soft_relation: SoftRelation | None = None,
    ) -> dict[str, Any]:
        """Annotate an evidence dict with hard/soft relation metadata.

        The annotation is strictly ADDITIVE: a ``relation`` field is attached
        describing how the evidence was reached, but no existing MCP contract
        field (evidence_id, full_content, source_version, source_position,
        knowledge_scope_id, knowledge_scope_type, status, parent_context) is
        ever changed or removed (FR-011, Constitution VII).

        - A GraphEdge (hard relation) is annotated as *verifiable* evidence
          (``type=hard``, ``is_hard=true``) carrying its deterministic
          ``parse_evidence`` (AST/DDL provenance) so the consumer can audit
          how the edge was extracted.
        - A SoftRelation (LLM-inferred) is annotated as *inferred* evidence
          (``type=soft``, ``is_hard=false``) carrying ``confidence``,
          ``model_and_version`` and ``lifecycle_state`` so the consumer can
          treat it as low-weight / disposable.
        - Hard and soft are distinguishable via ``relation.type`` and
          ``relation.is_hard`` (FR-004, SC-009).
        - When both are supplied the hard relation wins: a soft relation never
          masquerades as or overrides a hard fact (Constitution III).
        - With neither supplied the dict is returned unchanged.

        Args:
            evidence_dict: A get_evidence() result dict (mutable copy taken;
                the caller's dict is never modified in place).
            relation_edge: A hard GraphEdge that produced/owns this evidence.
            soft_relation: A soft SoftRelation that produced/owns this evidence.

        Returns:
            A new dict equal to ``evidence_dict`` plus a ``relation`` field,
            or the original dict unchanged when no relation is supplied.
        """
        if relation_edge is None and soft_relation is None:
            return evidence_dict

        if relation_edge is not None:
            relation = self._hard_relation_annotation(relation_edge)
        else:
            relation = self._soft_relation_annotation(soft_relation)

        # Copy so the caller's dict is never mutated in place; only append the
        # new additive 'relation' key — existing keys are left untouched.
        annotated = dict(evidence_dict)
        annotated["relation"] = relation
        return annotated

    @staticmethod
    def _hard_relation_annotation(edge: GraphEdge) -> dict[str, Any]:
        """Verifiable (hard) relation annotation from a deterministic GraphEdge."""
        return {
            "type": "hard",
            "relation_type": edge.relation_type,
            "edge_id": str(edge.edge_id),
            "is_hard": True,
            "parse_evidence": edge.parse_evidence,
        }

    @staticmethod
    def _soft_relation_annotation(relation: SoftRelation) -> dict[str, Any]:
        """Inferred (soft) relation annotation from an LLM SoftRelation."""
        confidence = relation.confidence
        if confidence is not None:
            # Numeric columns may hold Decimal; normalize to float for JSON.
            confidence = float(confidence)
        return {
            "type": "soft",
            "relation_type": "inferred",
            "edge_id": str(relation.edge_id),
            "is_hard": False,
            "confidence": confidence,
            "model_and_version": relation.model_and_version,
            "lifecycle_state": relation.lifecycle_state,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_scope_ids(self, project_refs: list[str]) -> list[int]:
        """Resolve project references to knowledge_scope_ids.

        Simplified resolution for evidence scope validation. Supports:
        - Numeric project_id → lookup Project → knowledge_scope_id
        - Alias string → lookup Project → knowledge_scope_id
        - Repo path → lookup Project → knowledge_scope_id

        Returns empty list if no refs resolve (treated as no restriction).
        """
        from sqlalchemy import or_

        scope_ids: list[int] = []

        for ref in project_refs:
            ref_stripped = ref.strip()
            if not ref_stripped:
                continue

            conditions = []

            # Try numeric ID
            try:
                numeric_id = int(ref_stripped)
                conditions.append(Project.project_id == numeric_id)
            except ValueError:
                pass

            # Alias match
            conditions.append(Project.alias == ref_stripped)

            # Repo path match
            conditions.append(Project.repo_path == ref_stripped)

            result = await self._session.execute(
                select(Project.knowledge_scope_id).where(or_(*conditions))
            )
            for scope_id in result.scalars().all():
                if scope_id not in scope_ids:
                    scope_ids.append(scope_id)

        return scope_ids

    async def _get_scope_type(self, scope_id: int) -> str:
        """Look up the scope_type for a knowledge_scope_id."""
        result = await self._session.execute(
            select(KnowledgeScope.scope_type).where(
                KnowledgeScope.scope_id == scope_id,
            )
        )
        scope_type = result.scalar_one_or_none()
        return scope_type or "project"
