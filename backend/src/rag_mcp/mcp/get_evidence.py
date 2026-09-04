"""MCP tool definition for get_evidence.

Registers the evidence expansion tool with the MCP server. Accepts an
evidence_id (from search_knowledge results) and project scopes, delegates
to EvidenceService, and returns full chunk content with parent context.

Conforms to mcp-get-evidence.schema.json input/output structure.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from rag_mcp.services.evidence_service import EvidenceService

logger = logging.getLogger(__name__)


def register_get_evidence_tool(
    mcp_server: FastMCP,
    session_factory: Any,
) -> None:
    """Register the get_evidence tool on the MCP server.

    Args:
        mcp_server: The FastMCP server instance.
        session_factory: Callable that returns an AsyncSession.
    """

    @mcp_server.tool(
        name="get_evidence",
        description=(
            "Retrieve the full content of a specific evidence item by its ID. "
            "Use this after search_knowledge to expand an evidence excerpt into "
            "its complete text, including parent context when available. "
            "Requires explicit project scope(s) for access control."
        ),
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    async def get_evidence(
        evidence_id: str,
        project_scope: list[str],
    ) -> dict[str, Any]:
        """Retrieve full evidence content by evidence_id.

        Args:
            evidence_id: The evidence ID string from search_knowledge results.
            project_scope: List of project references (stable ID, alias, or repo path).
                Must include the project that owns this evidence.

        Returns:
            Structured response with full_content, parent_context (if available),
            source metadata, and status indicating availability.
        """
        # Validate inputs
        if not evidence_id or not evidence_id.strip():
            return {
                "evidence_id": evidence_id or "",
                "status": "unavailable",
                "error": {
                    "code": "INVALID_EVIDENCE_ID",
                    "message": "Evidence ID must not be empty.",
                },
            }

        if not project_scope or len(project_scope) == 0:
            return {
                "evidence_id": evidence_id,
                "status": "unavailable",
                "error": {
                    "code": "MISSING_PROJECT_SCOPE",
                    "message": "At least one project_scope entry is required.",
                },
            }

        try:
            async with session_factory() as session:
                service = EvidenceService(session=session)
                result = await service.get_evidence(
                    evidence_id=evidence_id.strip(),
                    project_scopes=project_scope,
                )
                await session.commit()
                return result

        except Exception as exc:
            logger.error("get_evidence tool failed: %s", exc, exc_info=True)
            return {
                "evidence_id": evidence_id,
                "status": "unavailable",
                "error": {
                    "code": "SYSTEM_ERROR",
                    "message": f"Internal error: {type(exc).__name__}",
                },
            }
