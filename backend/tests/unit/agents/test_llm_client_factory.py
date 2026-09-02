"""Red test for capability router LLM client factory (T009 regression).

TDD Red: The capability router must not merely return model names —
it must be able to produce an actually callable LLM client
(Model Gateway, blueprint sec 18), and this client must be able to make
HTTP calls to an OpenAI-compatible endpoint.

This test MUST FAIL before the llm_client integration (TDD Red).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class TestCapabilityRouterClientFactory:
    """T009 regression: router must produce an invocable LLM client."""

    def test_router_has_create_client(self):
        """CapabilityRouter must expose create_client(role) (T009)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="test-model",
            default_model="test-model",
            llm_base_url="http://127.0.0.1:9",
            llm_api_key="sk-test",
        )
        assert hasattr(router, "create_client"), (
            "CapabilityRouter must have create_client() — currently only routes names, never calls LLM (FR-002)"
        )
        assert callable(router.create_client)

    def test_create_client_returns_callable(self):
        """create_client(role) must return an object with a callable chat/messages API."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            query_planner_model="test-model",
            default_model="test-model",
            llm_base_url="http://127.0.0.1:9",
            llm_api_key="sk-test",
        )
        client = router.create_client("query_planner")
        assert client is not None
        # Client must expose a chat completions entry point (OpenAI-compatible)
        assert hasattr(client, "chat_json"), (
            "LLM client must expose chat_json() for structured output calls"
        )
        assert callable(client.chat_json)


class TestLLMClientRealHttpCall:
    """The LLM client must make a real HTTP POST to the chat completions endpoint.

    Uses a local HTTP server (no token cost) to prove the call path exists:
    currently _llm_decompose/_llm_judge return None without any HTTP call.
    """

    @pytest.fixture
    def fake_openai_server(self):
        """Local OpenAI-compatible fake server recording requests."""
        requests_seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                requests_seen.append({"path": self.path, "body": body})
                resp = {
                    "choices": [{"message": {"role": "assistant",
                        "content": json.dumps({"sub_problems": [
                            {"query": "sub-q", "signals": ["dense"]}
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
        yield {"url": f"http://127.0.0.1:{server.server_address[1]}", "requests": requests_seen}
        server.shutdown()

    def test_client_makes_real_http_call(self, fake_openai_server):
        """chat_json must actually POST to {base_url}/chat/completions."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        router = CapabilityRouter(
            default_model="test-model",
            llm_base_url=fake_openai_server["url"],
            llm_api_key="sk-test",
        )
        client = router.create_client("query_planner")
        result = client.chat_json("decompose this query", {"query": "test"})
        # Must have made at least one HTTP call
        assert len(fake_openai_server["requests"]) >= 1, (
            "LLM client made no HTTP call — LLM integration is a stub"
        )
        # Must hit the OpenAI-compatible chat completions endpoint
        req = fake_openai_server["requests"][0]
        assert "chat/completions" in req["path"]
        # Must carry messages
        assert "messages" in req["body"]
