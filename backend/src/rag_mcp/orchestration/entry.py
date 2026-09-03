"""Agent orchestration entry for the MCP bridge (T057).

Runs the full agentic nine-step flow for one search_knowledge request:
resolve project refs (reused 001 component) -> AgenticStateMachine with the
real retrieval pipeline (T058) and the three wired Agents -> deterministic
serialization into the UNCHANGED mcp-search-output shape (FR-024).

Constitution X / FR-024: the external response schema is untouched
(additionalProperties:false stays valid); partial terminal states carry
uncovered/conflict/failed-path information through the existing gaps field
(FR-016); request_id bridges the internal ledger (FR-024/SC-004/SC-012).

Any internal failure raises AgenticPathUnavailable so the MCP tool can
degrade to the deterministic path instead of failing the request (SC-011).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Callable

from rag_mcp.config import get_settings
from rag_mcp.indexing.qdrant_client import QdrantStore
from rag_mcp.providers.base import EmbeddingProvider, RerankerProvider
from rag_mcp.services.retrieval_service import RetrievalService
from rag_mcp.utils.snowflake import generate_id

logger = logging.getLogger(__name__)

# T071 / SC-007: reserve headroom inside the 30s total-timeout guardrail so
# a degraded request (orchestration timeout + deterministic fallback) still
# finishes under the guardrail. The orchestration budget is
# total_timeout - headroom, floored at a sane minimum.
_DEFAULT_HEADROOM_MS = 2_000
_MIN_ORCHESTRATION_BUDGET_MS = 5_000


def degradation_headroom_ms() -> int:
    """Headroom reserved for the deterministic fallback (T071, SC-007)."""
    raw = os.getenv("AGENTIC_DEGRADATION_HEADROOM_MS", str(_DEFAULT_HEADROOM_MS))
    try:
        headroom = int(raw or 0)
    except ValueError:
        headroom = _DEFAULT_HEADROOM_MS
    return max(0, min(headroom, 60_000))


def orchestration_budget_ms(total_timeout_ms: int) -> int:
    """Orchestration budget = total timeout - degradation headroom (T071)."""
    budget = int(total_timeout_ms) - degradation_headroom_ms()
    return max(_MIN_ORCHESTRATION_BUDGET_MS, budget)


class AgenticPathUnavailable(Exception):
    """The agentic path cannot produce a response; degrade deterministically."""


async def run_agentic_search(
    *,
    query: str,
    project_scopes: list[str],
    top_k: int,
    task_context: dict | None,
    session_factory: Callable[[], Any],
    qdrant_store: QdrantStore,
    embedding_provider: EmbeddingProvider,
    reranker: RerankerProvider | None = None,
    agent_builder: Callable[[], Any] | None = None,
    return_record: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], dict[str, Any]]:
    """Execute one agentic search and serialize it to the MCP output shape.

    Args:
        agent_builder: Optional () -> (planner, analyst, orchestrator)
            override (tests/deterministic degradation); defaults to the real
            LLM-wired agents from run-config.
        return_record: When True also return the agentic_retrieval_run
            record (evaluation / bridge-key assertions).

    Raises:
        AgenticPathUnavailable: internal orchestration failure or timeout;
            the caller degrades to the deterministic path.
    """
    settings = get_settings()
    agentic_cfg = settings.agentic
    request_id = str(uuid.uuid4())

    # Step 1/2 reuse the deterministic 001 resolver (no behaviour change)
    async with session_factory() as session:
        service = RetrievalService(
            session=session,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
        scope_ids, error_info = await service.resolve_project_refs(project_scopes)

    if error_info is not None or not scope_ids:
        response = {
            "completion_status": "failed",
            "evidence": [],
            "error": error_info or {
                "code": "MISSING_PROJECT_SCOPE",
                "message": "No valid project scopes could be resolved.",
            },
            "request_id": request_id,
        }
        return (response, _empty_record(request_id)) if return_record else response

    from rag_mcp.orchestration.retrieval_pipeline import AgenticRetrievalPipeline
    from rag_mcp.orchestration.state_machine import AgenticStateMachine

    run_id = str(generate_id())
    machine = AgenticStateMachine(
        run_id=run_id,
        request_id=request_id,
        project_scope=list(project_scopes),
        knowledge_scope_ids=[str(s) for s in scope_ids],
        task_context=task_context,
    )

    if agent_builder is not None:
        planner, analyst, orchestrator = agent_builder()
        machine.set_query_planner(planner)
        machine.set_evidence_analyst(analyst)
        machine.set_context_orchestrator(orchestrator)
    else:
        machine.wire_default_agents(settings)

    machine.set_retrieval_pipeline(
        AgenticRetrievalPipeline(
            session_factory=session_factory,
            qdrant_store=qdrant_store,
            embedding_provider=embedding_provider,
            reranker=reranker,
        )
    )

    context = {
        "query": query,
        "task_context": task_context,
        "scope_ids": scope_ids,
        "top_k": top_k,
    }

    # Run-scoped persistence session (T059): ledger/judgment/selection rows
    # land during the run; the run record is appended at the end and the
    # whole unit commits together. Failures degrade to the deterministic
    # path so requests never return a ledger-less response (SC-006).
    from rag_mcp.orchestration.persistence import AgenticPersistence

    total_timeout_ms = agentic_cfg.guardrails.total_timeout_ms
    budget_ms = orchestration_budget_ms(total_timeout_ms)

    async with session_factory() as run_session:
        persistence = AgenticPersistence(run_session)
        machine.set_persistence(persistence)
        try:
            record = await asyncio.wait_for(
                machine.run_async(context),
                timeout=budget_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Agentic run exceeded %dms orchestration budget (of %dms total); degrading",
                budget_ms, total_timeout_ms,
            )
            await persistence.rollback()
            raise AgenticPathUnavailable(
                f"agentic orchestration budget {budget_ms}ms exceeded "
                f"(total guardrail {total_timeout_ms}ms)"
            )
        except AgenticPathUnavailable:
            await persistence.rollback()
            raise
        except Exception as exc:  # noqa: BLE001 - degrade, never fail the request
            logger.error("Agentic orchestration failed: %s", exc, exc_info=True)
            await persistence.rollback()
            raise AgenticPathUnavailable(str(exc)) from exc

        try:
            await persistence.persist_run_record(record)
            await persistence.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Agentic persistence failed: %s", exc, exc_info=True)
            await persistence.rollback()
            raise AgenticPathUnavailable(f"persistence failed: {exc}") from exc

    response = _serialize_mcp_response(
        record=record,
        machine=machine,
        request_id=request_id,
        top_k=top_k,
    )
    return (response, record) if return_record else response


def _empty_record(request_id: str) -> dict[str, Any]:
    """Minimal run record for pre-flight failures (scope resolution)."""
    return {
        "request_id": request_id,
        "ledger_ref": {"ledger_entry_ids": [], "rounds": []},
    }


def _serialize_mcp_response(
    *,
    record: dict[str, Any],
    machine: Any,
    request_id: str,
    top_k: int,
) -> dict[str, Any]:
    """Serialize the run into the UNCHANGED mcp-search-output shape (FR-024).

    No additional properties are introduced; partial states carry gap /
    conflict / failed-path details through the existing gaps field (FR-016).
    """
    from rag_mcp.orchestration.retrieval_pipeline import apply_per_source_guard, clamp01

    settings = get_settings()
    status = record.get("completion_status", "failed")
    candidates = machine.get_candidates()

    # Orchestrator selection decides which evidence enters the final context
    selection_list = (
        record.get("agent_outputs_ref", {})
        .get("context_orchestrator", {})
        .get("selection_list", [])
    )
    selected_ids = [
        s.get("evidence_id")
        for s in selection_list
        if s.get("decision") == "selected" and s.get("evidence_id")
    ]
    by_id = {c.get("evidence_id"): c for c in candidates}
    if selected_ids:
        chosen = [by_id[eid] for eid in selected_ids if eid in by_id]
    else:
        chosen = list(candidates)

    # Binning + per-source cap on the final evidence list (FR-006/FR-018)
    chosen = apply_per_source_guard(
        chosen, settings.agentic.max_evidence_per_source,
    )[: max(1, top_k)]

    evidence_items = []
    for c in chosen:
        try:
            source_version = max(1, int(c.get("source_version") or 1))
        except (TypeError, ValueError):
            source_version = 1
        evidence_items.append({
            "evidence_id": str(c.get("evidence_id", "")),
            "content_excerpt": (c.get("content_excerpt") or "")[:500],
            "source_version": source_version,
            "source_position": c.get("source_position") or "",
            "knowledge_scope_id": str(c.get("knowledge_scope_id", "")),
            "knowledge_scope_type": c.get("knowledge_scope_type") or "project",
            "relevance_score": clamp01(c.get("score", 0.0)),
        })

    if status == "failed":
        return {
            "completion_status": "failed",
            "evidence": [],
            "error": {
                "code": "SYSTEM_ERROR",
                "message": "Agent orchestration could not form a valid response.",
            },
            "request_id": request_id,
        }

    if status == "no_evidence":
        return {
            "completion_status": "no_evidence",
            "evidence": [],
            "request_id": request_id,
        }

    # complete / partial share the evidence shape; partial adds gaps (FR-016)
    response: dict[str, Any] = {
        "completion_status": status if evidence_items else "no_evidence",
        "evidence": evidence_items if evidence_items else [],
        "request_id": request_id,
    }
    if status == "partial" or (not evidence_items and status == "complete"):
        if not evidence_items:
            response["completion_status"] = "no_evidence"
        else:
            response["gaps"] = _build_gaps(machine)
    return response


def _build_gaps(machine: Any) -> list[dict[str, str]]:
    """Express uncovered/conflict/failed paths via the gaps field (FR-016)."""
    gaps: list[dict[str, str]] = []
    judgment = machine.get_latest_judgment() or {}

    for g in judgment.get("gap_descriptions", []) or []:
        entry = {"description": str(g.get("description", ""))}
        if g.get("suggested_action"):
            entry["suggested_action"] = str(g["suggested_action"])
        if entry["description"]:
            gaps.append(entry)

    uncovered = judgment.get("uncovered_sub_problem_ids") or []
    if uncovered:
        gaps.append({
            "description": f"Uncovered sub-problems after bounded retrieval: {uncovered}",
            "suggested_action": "Broaden the query or add knowledge sources covering these sub-problems.",
        })

    conflict = judgment.get("conflict_type", "none")
    if conflict and conflict != "none":
        gaps.append({
            "description": f"Evidence conflict surfaced by analysis: {conflict}",
            "suggested_action": "Review conflicting sources/versions before relying on this evidence.",
        })

    for fp in machine.get_failed_paths():
        gaps.append({
            "description": f"Retrieval sub-path failed: {fp}",
            "suggested_action": "Check service health and retry.",
        })

    if not gaps:
        gaps.append({
            "description": "Partial coverage: some sub-problems lack verified evidence.",
            "suggested_action": "Refine the query or extend the knowledge scope.",
        })
    return gaps
