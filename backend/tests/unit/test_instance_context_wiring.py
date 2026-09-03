"""006 convergence T077: instance attribution wiring (RED first).

FR-016/FR-020/data-model §4.1: the MCP process must install its instance
identity into the per-process context so retrieval run records carry
instance_id / instance_mode (metrics group by instance form).
"""

from __future__ import annotations

import sys
import uuid
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


def test_build_mcp_server_sets_instance_context():
    from rag_mcp.runtime.instance_context import get_instance
    from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider
    from rag_mcp.providers.local_cpu_reranker import LocalCPUReranker
    from rag_mcp.indexing.qdrant_client import QdrantStore

    module = _import_run_mcp()

    iid = uuid.uuid4()
    identity = module.InstanceIdentity(instance_id=iid, worker_id=7, mode="reader")

    module.build_mcp_server(
        "reader",
        embedding_provider=LocalCPUEmbeddingProvider(),
        reranker=LocalCPUReranker(),
        qdrant_store=QdrantStore(),
        identity=identity,
    )

    got = get_instance()
    assert got[0] == iid
    assert got[1] == "reader"
    assert got[2] == 7
