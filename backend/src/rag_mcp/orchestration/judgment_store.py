"""Agent judgment store for evidence analyst persistence (T027, US2).

Persists evidence analyst judgments to the agent_judgment table.
Append-only: only INSERT (no UPDATE/DELETE, FR-008).
Records round_index (monotonic), model_and_version (FR-002).
Output conforms to agent-judgment.schema.json.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from rag_mcp.orchestration.models import AgentJudgment
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)


class JudgmentStore:
    """Append-only store for agent judgments (FR-013/FR-008)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._round_counter: int = 0

    def next_round_index(self) -> int:
        """Return the next round_index (starts at 0, monotonic, FR-009)."""
        idx = self._round_counter
        self._round_counter += 1
        return idx

    def to_judgment_record(self, data: dict[str, Any]) -> dict[str, Any]:
        """Convert judgment data to a schema-conforming record (agent-judgment.schema.json)."""
        judgment_id = data.get("judgment_id") or str(generate_id())
        if isinstance(judgment_id, int):
            judgment_id = str(judgment_id)
        run_id = data.get("run_id", "0")
        if isinstance(run_id, int):
            run_id = str(run_id)
        return {
            "judgment_id": judgment_id,
            "run_id": run_id,
            "round_index": data.get("round_index", 0),
            "coverage_state": data.get("coverage_state", "partial"),
            "conflict_type": data.get("conflict_type", "none"),
            "uncovered_sub_problem_ids": data.get("uncovered_sub_problem_ids", []),
            "needs_supplementary": data.get("needs_supplementary", False),
            "gap_descriptions": data.get("gap_descriptions", []),
            "model_and_version": data.get("model_and_version", ""),
            "schema_valid": data.get("schema_valid", True),
        }

    async def insert_judgment(self, data: dict[str, Any]) -> AgentJudgment:
        """Insert a judgment (append-only, FR-008)."""
        record = self.to_judgment_record(data)
        judgment = AgentJudgment(
            judgment_id=int(record["judgment_id"]),
            run_id=record["run_id"],
            round_index=record["round_index"],
            coverage_state=record["coverage_state"],
            conflict_type=record["conflict_type"],
            uncovered_sub_problem_ids=record["uncovered_sub_problem_ids"],
            needs_supplementary=record["needs_supplementary"],
            gap_descriptions=record["gap_descriptions"],
            model_and_version=record["model_and_version"],
            schema_valid=record["schema_valid"],
        )
        self._session.add(judgment)
        await self._session.flush()
        return judgment
