"""Runtime metrics aggregation (006, T058).

FR-016/FR-017: query-time aggregation over the shared run-state tables
(retrieval_runs + runtime_maintenance_log + instance_registry) — no metric
storage table. Every field is an aggregated number or identifier; query and
evidence bodies NEVER appear. The window is bounded by the run TTL
(RETRIEVAL_TTL_DAYS). The response conforms to runtime-metrics.schema.json.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(ts: datetime) -> str:
    return ts.isoformat()


async def aggregate_request_totals(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT instance_mode, tool, COUNT(*) FROM retrieval_runs "
            "WHERE created_at >= :f AND created_at < :t "
            "GROUP BY instance_mode, tool ORDER BY instance_mode, tool"
        ),
        {"f": from_ts, "t": to_ts},
    )
    return [
        {"instance_mode": r[0], "tool": r[1], "requests": int(r[2])} for r in rows
    ]


async def aggregate_status_distribution(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT instance_mode, completion_status, COUNT(*) FROM retrieval_runs "
            "WHERE created_at >= :f AND created_at < :t "
            "GROUP BY instance_mode, completion_status "
            "ORDER BY instance_mode, completion_status"
        ),
        {"f": from_ts, "t": to_ts},
    )
    return [
        {"instance_mode": r[0], "status": r[1], "count": int(r[2])} for r in rows
    ]


async def aggregate_latency(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT tool, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms), "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) "
            "FROM retrieval_runs "
            "WHERE created_at >= :f AND created_at < :t "
            "GROUP BY tool ORDER BY tool"
        ),
        {"f": from_ts, "t": to_ts},
    )
    return [
        {"tool": r[0], "p50_ms": int(round(float(r[1] or 0))), "p95_ms": int(round(float(r[2] or 0)))}
        for r in rows
    ]


# Map the internal subpath_timings keys (dense_recall_ms, total_ms, ...)
# onto the runtime-metrics.schema.json enum [dense, sparse, fusion, rerank,
# graph, total]. Unknown keys are dropped (they are not part of the contract).
_SUBPATH_ALIASES = {
    "total": "total", "total_ms": "total",
    "dense": "dense", "dense_ms": "dense", "dense_recall_ms": "dense",
    "sparse": "sparse", "sparse_ms": "sparse", "sparse_recall_ms": "sparse",
    "fusion": "fusion", "fusion_ms": "fusion",
    "rerank": "rerank", "rerank_ms": "rerank",
    "graph": "graph", "graph_ms": "graph", "graph_expansion_ms": "graph",
}


async def aggregate_subpath_timings(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> list[dict[str, Any]]:
    """P50 of each subpath-timings JSONB key, grouped by tool (FR-016)."""
    rows = await session.execute(
        text(
            "SELECT tool, subpath_timings FROM retrieval_runs "
            "WHERE created_at >= :f AND created_at < :t "
            "AND subpath_timings IS NOT NULL"
        ),
        {"f": from_ts, "t": to_ts},
    )
    buckets: dict[tuple[str, str], list[float]] = {}
    for tool, timings in rows:
        if not isinstance(timings, dict):
            continue
        for key, value in timings.items():
            canonical = _SUBPATH_ALIASES.get(key)
            if canonical is None:
                continue
            if isinstance(value, (int, float)):
                buckets.setdefault((tool, canonical), []).append(float(value))
    out = []
    for (tool, subpath), values in sorted(buckets.items()):
        values.sort()
        mid = values[len(values) // 2]
        out.append({"tool": tool, "subpath": subpath, "p50_ms": int(round(mid))})
    return out


async def aggregate_provider_usage(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                "SELECT "
                "COALESCE(SUM((provider_usage->>'embedding_calls')::int), 0), "
                "COALESCE(SUM((provider_usage->>'rerank_calls')::int), 0), "
                "COALESCE(SUM((provider_usage->>'llm_calls')::int), 0), "
                "COALESCE(SUM((provider_usage->>'llm_prompt_chars')::int), 0), "
                "COALESCE(SUM((provider_usage->>'llm_completion_chars')::int), 0) "
                "FROM retrieval_runs "
                "WHERE created_at >= :f AND created_at < :t AND provider_usage IS NOT NULL"
            ),
            {"f": from_ts, "t": to_ts},
        )
    ).first()
    keys = (
        "embedding_calls", "rerank_calls", "llm_calls",
        "llm_prompt_chars", "llm_completion_chars",
    )
    return {key: int(row[i] or 0) for i, key in enumerate(keys)}


async def aggregate_ttl_purge(
    session: AsyncSession, from_ts: datetime, to_ts: datetime
) -> dict[str, int]:
    row = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(purged_retrieval_runs), 0), "
                "COALESCE(SUM(purged_agentic_runs), 0), "
                "COALESCE(SUM(purged_maintenance_logs), 0), COUNT(*) "
                "FROM runtime_maintenance_log "
                "WHERE created_at >= :f AND created_at < :t"
            ),
            {"f": from_ts, "t": to_ts},
        )
    ).first()
    return {
        "purged_retrieval_runs": int(row[0] or 0),
        "purged_agentic_runs": int(row[1] or 0),
        "purged_maintenance_logs": int(row[2] or 0),
        "events": int(row[3] or 0),
    }


async def get_active_instances(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT ir.instance_id, ir.instance_mode, ir.process_role, ir.worker_id, "
            "EXISTS (SELECT 1 FROM writer_lease wl WHERE wl.state='active' "
            "  AND wl.holder_instance_id = ir.instance_id) AS lease_holder "
            "FROM instance_registry ir WHERE ir.state='active' ORDER BY ir.started_at"
        )
    )
    return [
        {
            "instance_id": str(r[0]),
            "instance_mode": r[1],
            "process_role": r[2],
            "worker_id": int(r[3]),
            "writer_lease_holder": bool(r[4]),
        }
        for r in rows
    ]


async def build_runtime_metrics(
    session: AsyncSession, settings, now: datetime | None = None
) -> dict[str, Any]:
    """Assemble the full runtime-metrics.schema.json readout."""
    to_ts = now or datetime.now(timezone.utc)
    ttl_days = int(settings.retrieval_ttl_days)
    from_ts = to_ts - timedelta(days=ttl_days)

    return {
        "generated_at": _iso(to_ts),
        "window": {
            "from": _iso(from_ts),
            "to": _iso(to_ts),
            "ttl_days": ttl_days,
        },
        "request_totals": await aggregate_request_totals(session, from_ts, to_ts),
        "completion_status_distribution": await aggregate_status_distribution(session, from_ts, to_ts),
        "latency": await aggregate_latency(session, from_ts, to_ts),
        "subpath_timings_ms": await aggregate_subpath_timings(session, from_ts, to_ts),
        "provider_usage": await aggregate_provider_usage(session, from_ts, to_ts),
        "ttl_purge": await aggregate_ttl_purge(session, from_ts, to_ts),
        "active_instances": await get_active_instances(session),
    }
