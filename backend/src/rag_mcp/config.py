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


def get_settings() -> Settings:
    """Return application settings singleton."""
    return Settings()
