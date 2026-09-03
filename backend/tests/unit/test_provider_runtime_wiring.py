"""006 convergence T076: provider config wiring at runtime entry (RED first).

FR-010/FR-011/SC-004: the MCP runtime must assemble embedding/reranker from
the unified Provider run config (not hardcoded local CPU), and both entry
points must reject an invalid Provider configuration at startup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _import_run_mcp():
    import importlib

    if "_run_mcp" in sys.modules:
        return sys.modules["_run_mcp"]
    spec = importlib.util.spec_from_file_location("_run_mcp", BACKEND_ROOT / "_run_mcp.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_run_mcp"] = module
    spec.loader.exec_module(module)
    return module


def test_assemble_runtime_providers_uses_configured_provider(monkeypatch):
    from rag_mcp.config import get_settings

    module = _import_run_mcp()

    monkeypatch.setenv("EMBEDDING_PROVIDER_TYPE", "remote_api")
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "https://embed.example.com/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")

    embedding, _reranker = module.assemble_runtime_providers(get_settings())

    from rag_mcp.providers.remote_api_embedding import RemoteAPIEmbeddingProvider

    assert isinstance(embedding, RemoteAPIEmbeddingProvider)


def test_assemble_runtime_providers_rejects_invalid_config(monkeypatch):
    from rag_mcp.config import get_settings

    module = _import_run_mcp()

    monkeypatch.setenv("EMBEDDING_PROVIDER_TYPE", "remote_api")
    monkeypatch.delenv("EMBEDDING_ENDPOINT", raising=False)

    with pytest.raises(ValueError):
        module.assemble_runtime_providers(get_settings())


def test_server_validates_provider_config_at_startup(monkeypatch):
    from rag_mcp.config import get_settings
    from rag_mcp.server import _validate_provider_config_or_fail

    monkeypatch.setenv("RERANKER_PROVIDER_TYPE", "remote_api")
    monkeypatch.delenv("RERANKER_ENDPOINT", raising=False)

    with pytest.raises(ValueError):
        _validate_provider_config_or_fail(get_settings())
