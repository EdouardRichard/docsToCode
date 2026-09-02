"""Agentic run-config schema for Agent orchestration (005, T002).

Defines the guardrail defaults, model routing, agent toggle, and 004 graph
guardrail reuse for the three-Agent retrieval orchestration path.

Guardrails (research sec 2/10, spec FR-006, FR-033):
  - max_rounds: default 2, limit 3
  - node_timeout_ms: default 5_000, limit 10_000
  - top_k_max: 20 (binning cap, reused from 001)
  - max_evidence_per_source: default 3, limit 5
  - total_timeout_ms: 30_000 (whole-call guardrail, blueprint sec 19)
  - graph guardrails reused from 004: hop 2/3, budget 10/20, sub_timeout 3s

Agent toggle (FR-024, Constitution X):
  - enabled=False by default; Agent orchestration is a configurable switch.
    The deterministic 001/002/004 default path stays untouched until the
    comparison evaluation proves benefit (three-gate pass) and an operator
    enables AGENTIC_RETRIEVAL_ENABLED=true.

Model routing (FR-002, blueprint sec 18.4):
  - query_planner: low-latency model (simple, high-frequency)
  - evidence_analyst: stronger model (complex evidence judgment)
  - context_orchestrator: middle model (configurable)
  - No vendor lock-in; specific model names come from run-config/env.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgenticGuardrails:
    """Bounded guardrails for the Agent orchestration state machine.

    Values are config-overridable but MUST NOT exceed their limits
    (enforced by the state machine at runtime, FR-006).
    """

    # Retrieval round guardrails
    max_rounds_default: int = 2
    max_rounds_limit: int = 3

    # Per-node timeout guardrails (blueprint sec 19)
    node_timeout_ms_default: int = 5_000
    node_timeout_ms_limit: int = 10_000

    # Final context binning cap (reused from 001 top_k_max)
    top_k_max: int = 20

    # Per-source evidence cap (005 new, FR-006)
    max_evidence_per_source_default: int = 3
    max_evidence_per_source_limit: int = 5

    # Whole-call timeout (blueprint sec 19, 001-004 30s guardrail)
    total_timeout_ms: int = 30_000

    # 004 graph guardrails reused (FR-033, research sec 2)
    graph_hop_default: int = 2
    graph_hop_max: int = 3
    graph_candidate_budget_default: int = 10
    graph_candidate_budget_max: int = 20
    graph_sub_timeout_ms: int = 3_000

    # Confidence threshold for evidence analyst judgment (research sec 10)
    confidence_threshold_default: float = 0.6

    # Tracing body toggle (FR-011/FR-012, blueprint sec 20)
    trace_body_enabled_default: bool = True


@dataclass(frozen=True)
class ModelRouting:
    """Model routing for the three Agent roles (FR-002, blueprint sec 18.4).

    query_planner -> low-latency model (simple, high-frequency)
    evidence_analyst -> stronger model (complex evidence judgment)
    context_orchestrator -> middle model (configurable)

    Specific model names are NOT hardcoded to a vendor; they come from the
    run-config / environment so the capability router can select the best
    available provider (Constitution architecture constraint, sec 18).
    """

    query_planner_model: str = ""
    evidence_analyst_model: str = ""
    context_orchestrator_model: str = ""

    # Fallback model used when a role-specific model is not configured
    default_model: str = ""

    # Provider base URL and API key (from env, not hardcoded)
    llm_base_url: str = ""
    llm_api_key: str = ""


@dataclass(frozen=True)
class AgenticConfig:
    """Full agentic run-config assembled from environment-overridable fields.

    This is the frozen snapshot consumed by the state machine, capability
    router, and trace recorder.  Toggle enabled=False -> deterministic
    fallback (the 001/002/004 default path runs untouched, FR-024).
    """

    enabled: bool
    guardrails: AgenticGuardrails
    model_routing: ModelRouting

    # Effective values (already clamped to limits)
    max_rounds: int
    node_timeout_ms: int
    max_evidence_per_source: int
    trace_body_enabled: bool

    @property
    def is_deterministic_fallback(self) -> bool:
        """True when Agent orchestration is off -> deterministic 001/002 path."""
        return not self.enabled
