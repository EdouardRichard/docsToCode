"""Red test for query planner real LLM invocation (T019 regression).

TDD Red: when an LLM client is wired in, _llm_decompose must ACTUALLY
invoke it (making an HTTP call), not return None silently.
Currently the stub returns None and every query falls back to the
deterministic single-sub-problem path — the LLM is never called.

This test MUST FAIL before real LLM integration (TDD Red).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def _start_fake_llm_server():
    """Start a local OpenAI-compatible fake LLM server."""
    requests_seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            requests_seen.append({"path": self.path, "body": body})
            resp = {
                "choices": [{"message": {"role": "assistant",
                    "content": json.dumps({"sub_problems": [
                        {"query": "who calls validateToken", "signals": ["dense", "graph"]},
                        {"query": "what does validateToken depend on", "signals": ["sparse"]},
                    ]})}}],
            }
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", requests_seen


class TestQueryPlannerRealLLMCall:
    """T019 regression: planner must actually invoke the LLM when configured."""

    def test_planner_calls_llm_when_configured(self):
        """With an LLM wired in, run() must make a real LLM call (not return None)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.query_planner import QueryPlannerAgent

        server, url, requests_seen = _start_fake_llm_server()
        try:
            router = CapabilityRouter(
                query_planner_model="deepseek-v4-flash",
                default_model="deepseek-v4-flash",
                llm_base_url=url,
                llm_api_key="sk-test",
            )
            planner = QueryPlannerAgent(
                model_and_version="deepseek-v4-flash",
                llm_client=router.create_client("query_planner"),
            )
            result = planner.run({"query": "which services call UserService#validateToken and what does it depend on"})
            # The LLM must have been called
            assert len(requests_seen) >= 1, (
                "QueryPlannerAgent made no LLM call — _llm_decompose is still a stub returning None (T019 regression)"
            )
            # And the LLM response should be used (2 sub-problems from fake server)
            assert len(result.output["sub_problems"]) == 2
            assert result.output["sub_problems"][0]["query"] == "who calls validateToken"
            assert result.output["schema_valid"] is True
        finally:
            server.shutdown()

    def test_planner_still_falls_back_when_llm_errors(self):
        """When the LLM endpoint errors, degradation fallback still works (SC-011)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.query_planner import QueryPlannerAgent

        # Point at a dead port to force connection error
        router = CapabilityRouter(
            query_planner_model="test",
            default_model="test",
            llm_base_url="http://127.0.0.1:1",  # nothing listening
            llm_api_key="sk-test",
        )
        planner = QueryPlannerAgent(
            model_and_version="test",
            llm_client=router.create_client("query_planner"),
        )
        # Must NOT raise; must fall back deterministically
        result = planner.run({"query": "test query"})
        assert result.output["schema_valid"] is True
        assert len(result.output["sub_problems"]) >= 1
        # Fallback path: single sub-problem with original query
        assert result.output["sub_problems"][0]["query"] == "test query"
