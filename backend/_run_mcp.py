"""Standalone MCP server entry point for Streamable HTTP transport."""
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from rag_mcp.mcp import create_mcp_server
from rag_mcp.providers.local_cpu import LocalCPUEmbeddingProvider
from rag_mcp.indexing.qdrant_client import QdrantStore


def main():
    provider = LocalCPUEmbeddingProvider()
    qdrant = QdrantStore()
    server = create_mcp_server(
        embedding_provider=provider,
        qdrant_store=qdrant,
    )

    # Warm the embedding model before serving so the first search_knowledge
    # call does not exceed the client's request timeout (bge-m3 lazy load).
    print("Warming up embedding model (bge-m3) — may take ~30-60s ...")
    provider.warmup()

    print(f"MCP server running on 127.0.0.1:8080 (Streamable HTTP)")
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
