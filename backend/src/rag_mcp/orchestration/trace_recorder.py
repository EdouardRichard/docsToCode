"""Trace recorder for Agent orchestration sub-path timings and refs (T015).

Records:
  - sub_path_timings: per-subpath millisecond timings (FR-031)
  - agent_outputs_ref: references to Agent outputs (blueprint sec 20)
  - ledger_ref: references to ledger entries (blueprint sec 13)
  - TTL: sets ttl_expires_at on run records (blueprint sec 20)
  - Redaction: when trace_body_enabled=False, only retains ID/status/
    timing/error, stripping query/evidence body content (FR-011/FR-012)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class TraceRecorder:
    """Records sub-path timings, agent output refs, and ledger refs.

    When trace_body_enabled=False (FR-012), only IDs, statuses, timings, and
    errors are retained; query/evidence body content is stripped.
    """

    DEFAULT_TTL_SECONDS = 7 * 24 * 3600

    def __init__(self, trace_body_enabled: bool = True) -> None:
        self._trace_body_enabled = trace_body_enabled
        self._timings: dict[str, float] = {}
        self._agent_outputs: dict[str, dict[str, Any]] = {}
        self._ledger_entry_ids: list[str] = []
        self._rounds: list[dict[str, Any]] = []
        self._ttl_expires_at: datetime | None = None

    def record_timing(self, subpath: str, ms: float) -> None:
        """Record a timing for a subpath (FR-031). Timings always retained."""
        self._timings[subpath] = ms

    def get_timings(self) -> dict[str, float]:
        """Return all recorded sub-path timings."""
        return dict(self._timings)

    def record_agent_output(self, role: str, output: dict[str, Any]) -> None:
        """Record an Agent output reference (blueprint sec 20)."""
        if self._trace_body_enabled:
            self._agent_outputs[role] = output
        else:
            # Redact: only keep IDs and validation flags (FR-012)
            redacted: dict[str, Any] = {}
            for key in ("judgment_ids", "context_result_id", "schema_valid",
                         "schema_valid_all", "sub_problems"):
                if key in output:
                    val = output[key]
                    if key == "sub_problems":
                        # Keep only sub_problem_ids, not full body
                        redacted[key] = [
                            {"sub_problem_id": sp.get("sub_problem_id")} if isinstance(sp, dict)
                            else sp
                            for sp in val
                        ]
                    else:
                        redacted[key] = val
            self._agent_outputs[role] = redacted

    def get_agent_outputs_ref(self) -> dict[str, dict[str, Any]]:
        """Return the agent output references (all three roles)."""
        return dict(self._agent_outputs)

    def record_ledger_entry(self, ledger_entry_id: str, retrieval_query: str = "") -> None:
        """Record a ledger entry ID (blueprint sec 13, FR-012)."""
        self._ledger_entry_ids.append(ledger_entry_id)

    def record_round(self, round_index: int, sub_problem_ids: list[int] | None = None, judgment_id: str | None = None) -> None:
        """Record a round with its sub_problem_ids and judgment_id (SC-009)."""
        self._rounds.append({
            "round_index": round_index,
            "sub_problem_ids": sub_problem_ids or [],
            "judgment_id": judgment_id,
        })

    def get_ledger_ref(self) -> dict[str, Any]:
        """Return the ledger reference (ledger_entry_ids + rounds)."""
        return {
            "ledger_entry_ids": list(self._ledger_entry_ids),
            "rounds": list(self._rounds),
        }

    def set_ttl(self, seconds: int | None = None) -> None:
        """Set the TTL expiry time (blueprint sec 20, default 7 days)."""
        ttl_seconds = seconds if seconds is not None else self.DEFAULT_TTL_SECONDS
        self._ttl_expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    def get_ttl_expires_at(self) -> datetime | None:
        """Return the TTL expiry datetime, or None if not set."""
        return self._ttl_expires_at

    @property
    def trace_body_enabled(self) -> bool:
        """Whether full body content (queries/evidence) is recorded."""
        return self._trace_body_enabled