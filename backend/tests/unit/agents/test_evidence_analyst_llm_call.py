"""Red test for evidence analyst real LLM invocation (T025 regression).

TDD Red: when an LLM client is wired in, _llm_judge must ACTUALLY invoke
it, not return None silently.

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
                    "content": json.dumps({
                        "coverage_state": "partial",
                        "conflict_type": "none",
                        "uncovered_sub_problem_ids": [2],
                        "needs_supplementary": True,
                        "gap_descriptions": [{"description": "sub-problem 2 lacks evidence"}],
                    })}}],
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


class TestEvidenceAnalystRealLLMCall:
    """T025 regression: analyst must actually invoke the LLM when configured."""

    def test_analyst_calls_llm_when_configured(self):
        """With an LLM wired in, run() must make a real LLM call."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent

        server, url, requests_seen = _start_fake_llm_server()
        try:
            router = CapabilityRouter(
                evidence_analyst_model="deepseek-v4-flash",
                default_model="deepseek-v4-flash",
                llm_base_url=url,
                llm_api_key="sk-test",
            )
            analyst = EvidenceAnalystAgent(
                model_and_version="deepseek-v4-flash",
                llm_client=router.create_client("evidence_analyst"),
            )
            result = analyst.run({
                "run_id": "999",
                "round_index": 0,
                "query": "test",
                "sub_problems": [{"sub_problem_id": 1}, {"sub_problem_id": 2}],
                "evidence": [{"evidence_id": "ev-1", "content": "some evidence"}],
            })
            # The LLM must have been called
            assert len(requests_seen) >= 1, (
                "EvidenceAnalystAgent made no LLM call — _llm_judge is still a stub returning None (T025 regression)"
            )
            # LLM judgment used
            assert result.output["coverage_state"] == "partial"
            assert result.output["needs_supplementary"] is True
            assert result.output["uncovered_sub_problem_ids"] == [2]
            assert result.output["schema_valid"] is True
        finally:
            server.shutdown()

    def test_analyst_still_falls_back_when_llm_errors(self):
        """When the LLM endpoint errors, degradation fallback still works (SC-011)."""
        from rag_mcp.agents.capability_router import CapabilityRouter
        from rag_mcp.agents.evidence_analyst import EvidenceAnalystAgent

        router = CapabilityRouter(
            evidence_analyst_model="test",
            default_model="test",
            llm_base_url="http://127.0.0.1:1",
            llm_api_key="sk-test",
        )
        analyst = EvidenceAnalystAgent(
            model_and_version="test",
            llm_client=router.create_client("evidence_analyst"),
        )
        result = analyst.run({
            "run_id": "999",
            "round_index": 0,
            "query": "test",
            "sub_problems": [{"sub_problem_id": 1}],
            "evidence": [],
        })
        assert result.output["schema_valid"] is True
        assert result.output["coverage_state"] in ("covered", "partial", "uncovered")
