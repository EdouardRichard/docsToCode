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
        Path(__file__).resolve().parents[3] / ".env",  # repository root
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


def get_settings() -> Settings:
    """Return application settings singleton."""
    return Settings()
