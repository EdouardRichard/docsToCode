"""Tests for the LLM token estimate (T072, SC-007/FR-026).

The comparison report records model cost; to make that cost interpretable
the run record also carries an estimated token count (chars/3, mixed
zh/en) via an OPTIONAL, backward-compatible contract field
total_llm_tokens (Constitution VII — optional field, like graph_hop).

This test MUST FAIL before the field exists (TDD Red).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from rag_mcp.agents.base import AgentResult
from rag_mcp.orchestration.state_envelope import StateEnvelope
from rag_mcp.orchestration.state_machine import AgenticStateMachine

_CONTRACTS = (
    Path(__file__).parent.parent.parent.parent.parent
    / "specs" / "005-agentic-retrieval-orchestration" / "contracts"
)


def _load_schema(name: str) -> dict:
    return json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))


def _merged_run_schema() -> dict:
    run = _load_schema("agentic-retrieval-run.schema.json")
    common = _load_schema("common.schema.json")
    merged = copy.deepcopy(run)
    merged.setdefault("$defs", {})
    merged["$defs"].update(copy.deepcopy(common["definitions"]))
    prefix = common["$id"] + "#/definitions/"

    def _rewrite(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k == "$ref" and isinstance(v, str) and v.startswith(prefix):
                    obj[k] = "#/$defs/" + v[len(prefix):]
                else:
                    _rewrite(v)
        elif isinstance(obj, list):
            for item in obj:
                _rewrite(item)

    _rewrite(merged)
    return merged


def _minimal_record() -> dict:
    return {
        "run_id": "999",
        "request_id": "req-1",
        "project_scope": ["p"],
        "knowledge_scope_ids": ["100"],
        "run_config": {},
        "completion_status": "complete",
        "max_rounds": 2,
        "rounds_completed": 1,
        "guardrail_state": {},
        "sub_path_timings": {},
        "agent_outputs_ref": {
            "query_planner": {"sub_problems": [], "schema_valid": True},
            "evidence_analyst": {"judgment_ids": [], "schema_valid_all": True},
            "context_orchestrator": {
                "context_result_id": "cr-1", "selection_list": [], "schema_valid": True,
            },
        },
        "ledger_ref": {"ledger_entry_ids": [], "rounds": []},
        "schema_valid_all": True,
    }


class _StubAgent:
    def __init__(self, prompt_chars=0, completion_chars=0):
        class _Client:
            pass
        self._llm_client = _Client()
        self._llm_client.prompt_chars = prompt_chars
        self._llm_client.completion_chars = completion_chars

    def run(self, context):
        return AgentResult(output={}, schema_valid=True)


class TestTokenEstimate:
    def test_envelope_records_tokens(self):
        env = StateEnvelope(run_id="999", request_id="r", project_scope=["p"],
                            knowledge_scope_ids=["100"])
        env.set_total_llm_tokens(123.0)
        assert env.to_dict()["total_llm_tokens"] == 123.0

    def test_envelope_omits_tokens_when_unset(self):
        env = StateEnvelope(run_id="999", request_id="r", project_scope=["p"],
                            knowledge_scope_ids=["100"])
        assert "total_llm_tokens" not in env.to_dict()

    def test_schema_accepts_optional_total_llm_tokens(self):
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(_merged_run_schema())
        rec = _minimal_record()
        rec["total_llm_tokens"] = 200.0
        validator.validate(rec)  # must not raise

    def test_schema_backward_compatible_without_field(self):
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(_merged_run_schema())
        validator.validate(_minimal_record())  # must not raise

    def test_machine_records_tokens_from_usage(self):
        machine = AgenticStateMachine(
            run_id="999", request_id="req-1",
            project_scope=["p"], knowledge_scope_ids=["100"],
        )
        machine.set_query_planner(_StubAgent(prompt_chars=300, completion_chars=300))
        machine.set_evidence_analyst(_StubAgent(prompt_chars=300, completion_chars=0))
        machine.set_context_orchestrator(_StubAgent(prompt_chars=0, completion_chars=0))
        machine.run(context={"query": "q"})

        record = machine.get_state_envelope().to_dict()
        assert "total_llm_tokens" in record
        # 300+300 + 300 = 900 chars -> 300 tokens (chars/3)
        assert record["total_llm_tokens"] == 300.0
