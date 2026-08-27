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
    print(f"MCP server running on 127.0.0.1:8080 (Streamable HTTP)")
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
