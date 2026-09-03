"""Unit tests for the 006 runtime Settings extension (T001, RED first).

Covers the Phase 1 shared-infrastructure task: the `Settings` object must
carry every 006 runtime configuration field (data-model §2/§3/§4, research
§1.4/§1.7/§1.9):

- instance_mode / worker_id
- writer lease renewal (30s) / expiry window (90s)
- retrieval TTL days (7)
- unified trace-body switch (TRACE_BODY_ENABLED, default true)
- three Provider capability configs with independent concurrency limits
  (LLM 4 / limit 8, Embedding 8 / limit 16, Reranker 2 / limit 4)
- per-Host timeout profiles (60000/60000/120000ms, server total 30000ms)

This test MUST FAIL before T002 (modules/provider_config.py and
config/timeout_profiles.py do not exist yet -> ImportError, missing
Settings fields -> AttributeError).
"""

from __future__ import annotations

import pytest

# All 006 runtime env vars exercised by this module (cleared per-test so the
# frozen defaults from the dataclass definitions apply).
_006_ENV_VARS = [
    "INSTANCE_MODE",
    "WORKER_ID",
    "LEASE_RENEW_INTERVAL_S",
    "LEASE_EXPIRY_WINDOW_S",
    "RETRIEVAL_TTL_DAYS",
    "TRACE_BODY_ENABLED",
    "AGENTIC_TRACE_BODY_ENABLED",
    "EMBEDDING_PROVIDER_TYPE",
    "EMBEDDING_MODEL",
    "EMBEDDING_ENDPOINT",
    "EMBEDDING_API_KEY_ENV",
    "EMBEDDING_CONCURRENCY_LIMIT",
    "EMBEDDING_CONCURRENCY_LIMIT_MAX",
    "RERANKER_PROVIDER_TYPE",
    "RERANKER_MODEL",
    "RERANKER_ENDPOINT",
    "RERANKER_API_KEY_ENV",
    "RERANKER_CONCURRENCY_LIMIT",
    "RERANKER_CONCURRENCY_LIMIT_MAX",
    "LLM_PROVIDER_TYPE",
    "LLM_MODEL",
    "LLM_ENDPOINT",
    "LLM_API_KEY_ENV",
    "LLM_CONCURRENCY_LIMIT",
    "LLM_CONCURRENCY_LIMIT_MAX",
    "HOST_TIMEOUT_MS_DEEPSEEK_HARNESS",
    "HOST_TIMEOUT_MS_CLAUDE_CODE",
    "HOST_TIMEOUT_MS_CHATGPT_APP",
    "RETRIEVAL_TOTAL_TIMEOUT_MS",
]


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove all 006 env overrides so dataclass defaults apply."""
    for var in _006_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# ---------------------------------------------------------------- imports (RED)


def test_import_provider_config() -> None:
    from rag_mcp.config.provider_config import ProviderConfig  # noqa: F401


def test_import_timeout_profiles() -> None:
    from rag_mcp.config.timeout_profiles import TimeoutProfiles  # noqa: F401


def test_settings_module_exposes_runtime_fields(clean_env) -> None:
    from rag_mcp.config import Settings

    s = Settings()
    assert hasattr(s, "instance_mode")
    assert hasattr(s, "worker_id")
    assert hasattr(s, "lease_renew_interval_s")
    assert hasattr(s, "lease_expiry_window_s")
    assert hasattr(s, "retrieval_ttl_days")
    assert hasattr(s, "trace_body_enabled")
    assert hasattr(s, "providers")
    assert hasattr(s, "timeout_profiles")


# ------------------------------------------------------------------- defaults


def test_instance_mode_defaults(clean_env) -> None:
    from rag_mcp.config import Settings

    s = Settings()
    assert s.instance_mode == "writer"
    # worker_id: None => auto-assign lowest free at registration (FR-030)
    assert s.worker_id is None


def test_instance_mode_env_override(clean_env) -> None:
    clean_env.setenv("INSTANCE_MODE", "reader")
    from rag_mcp.config import Settings

    assert Settings().instance_mode == "reader"


def test_instance_mode_invalid_rejected(clean_env) -> None:
    clean_env.setenv("INSTANCE_MODE", "admin")
    from rag_mcp.config import Settings

    with pytest.raises(ValueError, match="INSTANCE_MODE"):
        Settings()


def test_worker_id_env_override(clean_env) -> None:
    clean_env.setenv("WORKER_ID", "7")
    from rag_mcp.config import Settings

    assert Settings().worker_id == 7


def test_worker_id_out_of_range_rejected(clean_env) -> None:
    clean_env.setenv("WORKER_ID", "1024")
    from rag_mcp.config import Settings

    with pytest.raises(ValueError):
        Settings()


def test_lease_defaults(clean_env) -> None:
    from rag_mcp.config import Settings

    s = Settings()
    # data-model §3.3 / clarification Q2: renewal 30s, expiry 90s
    assert s.lease_renew_interval_s == 30
    assert s.lease_expiry_window_s == 90


def test_lease_env_override(clean_env) -> None:
    clean_env.setenv("LEASE_RENEW_INTERVAL_S", "5")
    clean_env.setenv("LEASE_EXPIRY_WINDOW_S", "15")
    from rag_mcp.config import Settings

    s = Settings()
    assert s.lease_renew_interval_s == 5
    assert s.lease_expiry_window_s == 15


def test_retrieval_ttl_days_default(clean_env) -> None:
    from rag_mcp.config import Settings

    assert Settings().retrieval_ttl_days == 7


def test_trace_body_enabled_default_true(clean_env) -> None:
    from rag_mcp.config import Settings

    assert Settings().trace_body_enabled is True


def test_trace_body_enabled_env_override(clean_env) -> None:
    clean_env.setenv("TRACE_BODY_ENABLED", "false")
    from rag_mcp.config import Settings

    assert Settings().trace_body_enabled is False


def test_trace_body_agentic_alias_compatibility(clean_env) -> None:
    """AGENTIC_TRACE_BODY_ENABLED stays a compatible alias (research §1.7)."""
    clean_env.setenv("AGENTIC_TRACE_BODY_ENABLED", "false")
    from rag_mcp.config import Settings

    assert Settings().trace_body_enabled is False


def test_trace_body_unified_switch_takes_precedence(clean_env) -> None:
    clean_env.setenv("AGENTIC_TRACE_BODY_ENABLED", "false")
    clean_env.setenv("TRACE_BODY_ENABLED", "true")
    from rag_mcp.config import Settings

    assert Settings().trace_body_enabled is True


# ------------------------------------------------------------ provider config


def test_provider_config_defaults(clean_env) -> None:
    from rag_mcp.config import Settings

    s = Settings()
    providers = s.providers
    # Default combo (research §1.4): local CPU embedding + reranker, remote LLM
    assert providers.embedding.provider_type == "local_cpu"
    assert providers.embedding.model == "BAAI/bge-m3"
    assert providers.embedding.endpoint is None
    assert providers.embedding.api_key_env is None
    assert providers.embedding.concurrency_limit == 8

    assert providers.reranker.provider_type == "local_cpu"
    assert providers.reranker.model == "BAAI/bge-reranker-v2-m3"
    assert providers.reranker.concurrency_limit == 2

    assert providers.llm.provider_type == "remote_api"
    assert providers.llm.concurrency_limit == 4


def test_provider_config_concurrency_defaults_and_limits(clean_env) -> None:
    from rag_mcp.config import Settings

    s = Settings()
    providers = s.providers
    # Concurrency default / hard ceiling per capability (clarification Q2)
    assert providers.llm.concurrency_limit == 4
    assert providers.llm.concurrency_limit_max == 8
    assert providers.embedding.concurrency_limit == 8
    assert providers.embedding.concurrency_limit_max == 16
    assert providers.reranker.concurrency_limit == 2
    assert providers.reranker.concurrency_limit_max == 4


def test_provider_config_env_override(clean_env) -> None:
    clean_env.setenv("RERANKER_PROVIDER_TYPE", "remote_api")
    clean_env.setenv("RERANKER_MODEL", "jina-reranker-v3")
    clean_env.setenv("RERANKER_ENDPOINT", "https://api.example.com/v1")
    clean_env.setenv("RERANKER_API_KEY_ENV", "RERANKER_API_KEY")
    from rag_mcp.config import Settings

    reranker = Settings().providers.reranker
    assert reranker.provider_type == "remote_api"
    assert reranker.model == "jina-reranker-v3"
    assert reranker.endpoint == "https://api.example.com/v1"
    assert reranker.api_key_env == "RERANKER_API_KEY"


def test_provider_config_unknown_type_rejected(clean_env) -> None:
    clean_env.setenv("EMBEDDING_PROVIDER_TYPE", "quantum")
    from rag_mcp.config import Settings

    with pytest.raises(ValueError, match="provider_type"):
        Settings()


def test_provider_concurrency_clamped_to_ceiling(clean_env) -> None:
    """Over-limit concurrency values are clamped to the hard ceiling (FR-009)."""
    clean_env.setenv("LLM_CONCURRENCY_LIMIT", "64")
    clean_env.setenv("EMBEDDING_CONCURRENCY_LIMIT", "99")
    clean_env.setenv("RERANKER_CONCURRENCY_LIMIT", "50")
    from rag_mcp.config import Settings

    providers = Settings().providers
    assert providers.llm.concurrency_limit == 8
    assert providers.embedding.concurrency_limit == 16
    assert providers.reranker.concurrency_limit == 4


# ------------------------------------------------------------ timeout profiles


def test_timeout_profiles_defaults(clean_env) -> None:
    from rag_mcp.config import Settings

    profiles = Settings().timeout_profiles
    # research §1.9: Host 60000/60000/120000ms, server total 30000ms
    assert profiles.deepseek_harness_ms == 60_000
    assert profiles.claude_code_ms == 60_000
    assert profiles.chatgpt_app_ms == 120_000
    assert profiles.server_total_ms == 30_000


def test_timeout_profiles_env_override(clean_env) -> None:
    clean_env.setenv("HOST_TIMEOUT_MS_CHATGPT_APP", "180000")
    clean_env.setenv("RETRIEVAL_TOTAL_TIMEOUT_MS", "20000")
    from rag_mcp.config import Settings

    profiles = Settings().timeout_profiles
    assert profiles.chatgpt_app_ms == 180_000
    assert profiles.server_total_ms == 20_000


def test_get_settings_returns_extended_settings(clean_env) -> None:
    from rag_mcp.config import get_settings

    s = get_settings()
    assert s.instance_mode == "writer"
    assert s.lease_renew_interval_s == 30
    assert s.lease_expiry_window_s == 90
    assert s.retrieval_ttl_days == 7
    assert s.trace_body_enabled is True
    assert s.timeout_profiles.server_total_ms == 30_000
