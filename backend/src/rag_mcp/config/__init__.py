"""Runtime configuration for RAG MCP Server.

Loads from environment variables, with an optional ``.env`` file for local
secrets (the file is gitignored). Falls back to local docker-compose defaults
so ``docker compose up`` works out of the box. No remote host, credential or
public IP is hardcoded here — set ``DATABASE_URL`` / ``QDRANT_URL`` explicitly
for remote deployments (see ``.env.example``).

Blueprint §16.3: services bind to 127.0.0.1 by default and are never exposed
to the network without explicit configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # pragma: no cover - optional convenience, not a hard dependency
    from dotenv import load_dotenv

    _ENV_CANDIDATES = (
        Path(__file__).resolve().parents[4] / ".env",  # repository root
        Path.cwd() / ".env",
    )
    for _candidate in _ENV_CANDIDATES:
        if _candidate.exists():
            # Existing process env vars take precedence over .env (override=False)
            load_dotenv(_candidate, override=False)
            break
except Exception:  # pragma: no cover - dotenv missing is fine
    pass


@dataclass(frozen=True)
class RetrievalConfig:
    """Retrieval pipeline guardrails (blueprint §12)."""

    total_timeout_ms: int = 30_000
    qdrant_query_timeout_ms: int = 10_000
    top_k_default: int = 5
    top_k_max: int = 20
    max_evidence_per_source: int = 5
    max_parent_context_tokens: int = 2_000


@dataclass(frozen=True)
class HybridRetrievalConfig:
    """Hybrid retrieval (002) parameters: sparse recall, RRF fusion, rerank.

    Defaults follow research.md §1.2/§1.3:
    - rrf_k=60 (classic RRF constant)
    - rerank_budget=20 (blueprint §18.5 candidate cap)
    - sparse_query_timeout_ms=5_000 (sparse sub-path guard)
    - fusion_algorithm='rrf' (DBSF reserved as configurable alternative)
    - reranker_model='BAAI/bge-reranker-v2-m3' (blueprint §18.2 default)
    """

    rrf_k: int = 60
    rerank_budget: int = 20
    sparse_query_timeout_ms: int = 5_000
    fusion_algorithm: str = "rrf"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"


@dataclass(frozen=True)
class GraphConfig:
    """Graph-enhanced retrieval guardrails (004, research §2/§3, FR-017).

    - hop_default=2 / hop_max=3 (spec clarification Q1)
    - candidate_budget=10 (single total budget, not per-hop)
    - candidate_budget_max=20 (upper bound)
    - graph_sub_timeout_ms=3_000 (graph expansion sub-path guard)
    - total_timeout_ms=30_000 (whole-call guardrail, blueprint §19)
    - direction_default='bidirectional' (calls+called_by, fk_references+fk_referenced_by)
    - structure_weight_hard=1.0, soft=0.3, hop_decay=0.5 (research §2)
    - soft_confidence_threshold=0.6 (research §4, active gating)
    - enabled=False: graph-enhanced retrieval is a CONFIGURABLE SWITCH. The
      deterministic 001/002 default path stays untouched until the comparison
      evaluation proves benefit (FR-024 three-gate pass) and an operator
      enables GRAPH_ENHANCED_RETRIEVAL_ENABLED=true.
    """

    enabled: bool = False
    hop_default: int = 2
    hop_max: int = 3
    candidate_budget: int = 10
    candidate_budget_max: int = 20
    graph_sub_timeout_ms: int = 3_000
    total_timeout_ms: int = 30_000
    direction_default: str = "bidirectional"
    structure_weight_hard: float = 1.0
    structure_weight_soft: float = 0.3
    structure_weight_hop_decay: float = 0.5
    soft_confidence_threshold: float = 0.6


@dataclass(frozen=True)
class IngestionConfig:
    """Ingestion pipeline parameters."""

    batch_size: int = 32
    chunk_target_tokens: int = 768
    chunk_min_tokens: int = 64
    chunk_max_tokens: int = 1_024


from rag_mcp.config.agentic import AgenticConfig, AgenticGuardrails, ModelRouting
from rag_mcp.config.provider_config import (
    ProviderConfig,
    ProviderSettings,
    load_provider_settings,
)
from rag_mcp.config.timeout_profiles import TimeoutProfiles, validate_timeout_profiles

# 006 instance modes (blueprint §21.2)
_INSTANCE_MODES = ("writer", "reader")
_TRUTHY_ENV = ("1", "true", "yes", "on")


def _env_instance_mode() -> str:
    raw = os.getenv("INSTANCE_MODE")
    if raw is None or raw == "":
        return "writer"
    value = raw.strip().lower()
    if value not in _INSTANCE_MODES:
        raise ValueError(
            f"INSTANCE_MODE must be one of {_INSTANCE_MODES}, got {raw!r}"
        )
    return value


def _env_worker_id() -> int | None:
    raw = os.getenv("WORKER_ID")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"WORKER_ID must be an integer, got {raw!r}") from exc
    if not 0 <= value <= 1023:
        raise ValueError(f"WORKER_ID must be 0-1023, got {value}")
    return value


def _env_trace_body() -> bool:
    """Unified 006 trace-body switch (research §1.7).

    TRACE_BODY_ENABLED is the single switch covering all retrieval modes;
    AGENTIC_TRACE_BODY_ENABLED (005) stays as a compatible alias used only
    when the unified switch is not set.
    """
    unified = os.getenv("TRACE_BODY_ENABLED")
    if unified is not None:
        return unified.strip().lower() in _TRUTHY_ENV
    alias = os.getenv("AGENTIC_TRACE_BODY_ENABLED")
    if alias is not None:
        return alias.strip().lower() in _TRUTHY_ENV
    return True


@dataclass(frozen=True)
class Settings:
    """Application settings assembled from environment variables."""

    # Database (async engine used by the running services)
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/rag_mcp",
        )
    )
    # Sync URL for Alembic migrations
    database_url_sync: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL_SYNC",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/rag_mcp",
        )
    )

    # Qdrant
    qdrant_url: str = field(
        default_factory=lambda: os.getenv("QDRANT_URL", "http://localhost:6333")
    )

    # Server ports (management API and MCP server run on separate ports)
    management_port: int = field(
        default_factory=lambda: int(os.getenv("MANAGEMENT_PORT", "8000"))
    )
    mcp_port: int = field(
        default_factory=lambda: int(os.getenv("MCP_PORT", "8080"))
    )

    # Data storage
    data_root: str = field(
        default_factory=lambda: os.getenv("DATA_ROOT", "./data/uploads")
    )

    # Embedding model
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    )

    # Ingestion scheduling
    ingestion_background: bool = field(
        default_factory=lambda: os.getenv("INGESTION_BACKGROUND", "true").lower()
        in ("1", "true", "yes", "on")
    )

    # Maintenance
    retrieval_ttl_cleanup_interval_s: int = field(
        default_factory=lambda: int(os.getenv("RETRIEVAL_TTL_CLEANUP_INTERVAL_S", "3600"))
    )

    # Sub-configs
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    hybrid_retrieval: HybridRetrievalConfig = field(default_factory=HybridRetrievalConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)

    # 004: Graph-enhanced retrieval (environment-overridable)
    # Configurable switch (FR-024): default OFF keeps the deterministic
    # 001/002 path; enable only after the comparison evaluation passes.
    graph_enhanced_retrieval_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "GRAPH_ENHANCED_RETRIEVAL_ENABLED", "false"
        ).lower() in ("1", "true", "yes", "on")
    )
    graph_hop_default: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_HOP_DEFAULT", "2"))
    )
    graph_hop_max: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_HOP_MAX", "3"))
    )
    graph_candidate_budget: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_CANDIDATE_BUDGET", "10"))
    )
    graph_candidate_budget_max: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_CANDIDATE_BUDGET_MAX", "20"))
    )
    graph_sub_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_SUB_TIMEOUT_MS", "3000"))
    )
    graph_total_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("GRAPH_TOTAL_TIMEOUT_MS", "30000"))
    )
    graph_direction_default: str = field(
        default_factory=lambda: os.getenv("GRAPH_DIRECTION_DEFAULT", "bidirectional")
    )
    graph_structure_weight_hard: float = field(
        default_factory=lambda: float(os.getenv("GRAPH_STRUCTURE_WEIGHT_HARD", "1.0"))
    )
    graph_structure_weight_soft: float = field(
        default_factory=lambda: float(os.getenv("GRAPH_STRUCTURE_WEIGHT_SOFT", "0.3"))
    )
    graph_structure_weight_hop_decay: float = field(
        default_factory=lambda: float(os.getenv("GRAPH_STRUCTURE_WEIGHT_HOP_DECAY", "0.5"))
    )
    graph_soft_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("GRAPH_SOFT_CONFIDENCE_THRESHOLD", "0.6"))
    )
    # 005: Agent orchestration (environment-overridable)
    # Configurable switch (FR-024, Constitution X): default OFF keeps the
    # deterministic 001/002/004 path; enable only after comparison eval passes.
    agentic_retrieval_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "AGENTIC_RETRIEVAL_ENABLED", "false"
        ).lower() in ("1", "true", "yes", "on")
    )
    agentic_max_rounds: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_ROUNDS", "2"))
    )
    agentic_max_rounds_limit: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_ROUNDS_LIMIT", "3"))
    )
    agentic_node_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_NODE_TIMEOUT_MS", "5000"))
    )
    agentic_node_timeout_ms_limit: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_NODE_TIMEOUT_MS_LIMIT", "10000"))
    )
    agentic_top_k_max: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_TOP_K_MAX", "20"))
    )
    agentic_max_evidence_per_source: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_EVIDENCE_PER_SOURCE", "3"))
    )
    agentic_max_evidence_per_source_limit: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_MAX_EVIDENCE_PER_SOURCE_LIMIT", "5"))
    )
    agentic_total_timeout_ms: int = field(
        default_factory=lambda: int(os.getenv("AGENTIC_TOTAL_TIMEOUT_MS", "30000"))
    )
    agentic_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("AGENTIC_CONFIDENCE_THRESHOLD", "0.6"))
    )
    agentic_trace_body_enabled: bool = field(
        default_factory=lambda: os.getenv("AGENTIC_TRACE_BODY_ENABLED", "true").lower()
        in ("1", "true", "yes", "on")
    )
    # LLM model routing (FR-002, blueprint sec 18.4)
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL", "")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("api_key", os.getenv("LLM_API_KEY", ""))
    )
    llm_model: str = field(
        default_factory=lambda: os.getenv("model", os.getenv("LLM_MODEL", "deepseek-v4-flash"))
    )
    agentic_model_query_planner: str = field(
        default_factory=lambda: os.getenv("AGENTIC_MODEL_QUERY_PLANNER", "")
    )
    agentic_model_evidence_analyst: str = field(
        default_factory=lambda: os.getenv("AGENTIC_MODEL_EVIDENCE_ANALYST", "")
    )
    agentic_model_context_orchestrator: str = field(
        default_factory=lambda: os.getenv("AGENTIC_MODEL_CONTEXT_ORCHESTRATOR", "")
    )

    # 006 Runtime hardening (data-model §2/§3/§4, research §1.4/§1.7/§1.9)
    # Deployment form: writer = management process + writer MCP; reader = read-only MCP
    instance_mode: str = field(default_factory=_env_instance_mode)
    # None -> auto-assign lowest free worker_id at registration (FR-030)
    worker_id: int | None = field(default_factory=_env_worker_id)
    # Writer lease: renewal 30s / expiry window 90s (clarification Q2)
    lease_renew_interval_s: int = field(
        default_factory=lambda: int(os.getenv("LEASE_RENEW_INTERVAL_S", "30"))
    )
    lease_expiry_window_s: int = field(
        default_factory=lambda: int(os.getenv("LEASE_EXPIRY_WINDOW_S", "90"))
    )
    # Retrieval run TTL (default 7 days, FR-019; drives expires_at + cleanup)
    retrieval_ttl_days: int = field(
        default_factory=lambda: int(os.getenv("RETRIEVAL_TTL_DAYS", "7"))
    )
    # Unified trace-body switch for all retrieval modes (FR-018)
    trace_body_enabled: bool = field(default_factory=_env_trace_body)
    # Provider runtime configuration (embedding/reranker/llm, FR-008~FR-015)
    providers: ProviderSettings = field(default_factory=load_provider_settings)
    # Per-Host timeout profiles (FR-021/FR-022)
    timeout_profiles: TimeoutProfiles = field(default_factory=TimeoutProfiles.from_env)

    @property
    def graph(self) -> "GraphConfig":
        """Assemble a frozen GraphConfig from environment-overridable fields."""
        return GraphConfig(
            enabled=self.graph_enhanced_retrieval_enabled,
            hop_default=self.graph_hop_default,
            hop_max=self.graph_hop_max,
            candidate_budget=self.graph_candidate_budget,
            candidate_budget_max=self.graph_candidate_budget_max,
            graph_sub_timeout_ms=self.graph_sub_timeout_ms,
            total_timeout_ms=self.graph_total_timeout_ms,
            direction_default=self.graph_direction_default,
            structure_weight_hard=self.graph_structure_weight_hard,
            structure_weight_soft=self.graph_structure_weight_soft,
            structure_weight_hop_decay=self.graph_structure_weight_hop_decay,
            soft_confidence_threshold=self.graph_soft_confidence_threshold,
        )
    @property
    def agentic(self) -> "AgenticConfig":
        """Assemble a frozen AgenticConfig from environment-overridable fields.

        Toggle enabled=False -> deterministic fallback (FR-024, Constitution X).
        """
        # Clamp effective values to limits
        max_rounds = min(self.agentic_max_rounds, self.agentic_max_rounds_limit)
        node_timeout = min(self.agentic_node_timeout_ms, self.agentic_node_timeout_ms_limit)
        max_ev = min(
            self.agentic_max_evidence_per_source,
            self.agentic_max_evidence_per_source_limit,
        )
        guardrails = AgenticGuardrails(
            max_rounds_default=max_rounds,
            max_rounds_limit=self.agentic_max_rounds_limit,
            node_timeout_ms_default=node_timeout,
            node_timeout_ms_limit=self.agentic_node_timeout_ms_limit,
            top_k_max=self.agentic_top_k_max,
            max_evidence_per_source_default=max_ev,
            max_evidence_per_source_limit=self.agentic_max_evidence_per_source_limit,
            total_timeout_ms=self.agentic_total_timeout_ms,
            graph_hop_default=self.graph_hop_default,
            graph_hop_max=self.graph_hop_max,
            graph_candidate_budget_default=self.graph_candidate_budget,
            graph_candidate_budget_max=self.graph_candidate_budget_max,
            graph_sub_timeout_ms=self.graph_sub_timeout_ms,
            confidence_threshold_default=self.agentic_confidence_threshold,
            trace_body_enabled_default=self.trace_body_enabled,
        )
        model_routing = ModelRouting(
            query_planner_model=self.agentic_model_query_planner or self.llm_model,
            evidence_analyst_model=self.agentic_model_evidence_analyst or self.llm_model,
            context_orchestrator_model=self.agentic_model_context_orchestrator or self.llm_model,
            default_model=self.llm_model,
            llm_base_url=self.llm_base_url,
            llm_api_key=self.llm_api_key,
        )
        return AgenticConfig(
            enabled=self.agentic_retrieval_enabled,
            guardrails=guardrails,
            model_routing=model_routing,
            max_rounds=max_rounds,
            node_timeout_ms=node_timeout,
            max_evidence_per_source=max_ev,
            trace_body_enabled=self.trace_body_enabled,
        )



def get_settings() -> Settings:
    """Return application settings singleton."""
    return Settings()
