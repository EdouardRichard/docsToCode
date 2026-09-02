"""Unit test for state envelope / run record (T016 Red).

Tests the StateEnvelope that creates and validates the agentic_retrieval_run
record, ensuring fields conform to agentic-retrieval-run.schema.json
(FR-010/FR-031).

This test MUST FAIL before state_envelope.py is implemented (TDD Red).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


CONTRACTS_DIR = (
    Path(__file__).parent.parent.parent.parent.parent
    / "specs"
    / "005-agentic-retrieval-orchestration"
    / "contracts"
)


def _load_schema(filename: str) -> dict:
    path = CONTRACTS_DIR / filename
    with path.open(encoding="utf-8") as f:
        return json.load(f)


COMMON_SCHEMA = _load_schema("common.schema.json")
RUN_SCHEMA = _load_schema("agentic-retrieval-run.schema.json")


def _merged_with_common(schema: dict) -> dict:
    """Inline common.schema.json definitions for self-contained validation."""
    merged = copy.deepcopy(schema)
    merged.setdefault("$defs", {})
    merged["$defs"].update(copy.deepcopy(COMMON_SCHEMA["definitions"]))
    prefix = COMMON_SCHEMA["$id"] + "#/definitions/"

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


MERGED_RUN_SCHEMA = _merged_with_common(RUN_SCHEMA)


class TestStateEnvelopeImport:
    def test_import_state_envelope(self):
        """StateEnvelope must be importable."""
        from rag_mcp.orchestration.state_envelope import StateEnvelope
        assert StateEnvelope is not None


class TestRunRecordCreation:
    """FR-010: agentic_retrieval_run record with required fields."""

    def _make_envelope(self):
        from rag_mcp.orchestration.state_envelope import StateEnvelope
        return StateEnvelope(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )

    def test_run_record_has_required_fields(self):
        """Run record must have all fields required by the schema."""
        env = self._make_envelope()
        record = env.to_dict()
        required_fields = [
            "run_id", "request_id", "project_scope", "knowledge_scope_ids",
            "run_config", "completion_status", "max_rounds", "rounds_completed",
            "guardrail_state", "sub_path_timings", "agent_outputs_ref",
            "ledger_ref", "schema_valid_all",
        ]
        for field in required_fields:
            assert field in record, f"Missing required field: {field}"

    def test_run_record_has_project_scope(self):
        """project_scope must be set (Constitution I, explicit scope)."""
        env = self._make_envelope()
        record = env.to_dict()
        assert record["project_scope"] == ["proj-a"]

    def test_run_record_has_completion_status(self):
        """completion_status must be one of the four states (blueprint sec 14)."""
        env = self._make_envelope()
        record = env.to_dict()
        assert record["completion_status"] in ("complete", "partial", "no_evidence", "failed")

    def test_run_record_has_guardrail_state(self):
        """guardrail_state must be a dict (FR-006)."""
        env = self._make_envelope()
        record = env.to_dict()
        assert isinstance(record["guardrail_state"], dict)

    def test_run_record_has_agent_outputs_ref(self):
        """agent_outputs_ref must have all three roles (blueprint sec 11)."""
        env = self._make_envelope()
        record = env.to_dict()
        ref = record["agent_outputs_ref"]
        assert "query_planner" in ref
        assert "evidence_analyst" in ref
        assert "context_orchestrator" in ref

    def test_run_record_has_ledger_ref(self):
        """ledger_ref must have ledger_entry_ids and rounds (blueprint sec 13)."""
        env = self._make_envelope()
        record = env.to_dict()
        ref = record["ledger_ref"]
        assert "ledger_entry_ids" in ref
        assert "rounds" in ref

    def test_run_record_has_schema_valid_all(self):
        """schema_valid_all must be a boolean (SC-011)."""
        env = self._make_envelope()
        record = env.to_dict()
        assert isinstance(record["schema_valid_all"], bool)


class TestSchemaConformance:
    """FR-031: run record fields conform to agentic-retrieval-run.schema.json."""

    def _make_valid_record(self):
        from rag_mcp.orchestration.state_envelope import StateEnvelope
        env = StateEnvelope(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )
        env.set_completion_status("complete")
        env.set_max_rounds(2)
        env.set_rounds_completed(1)
        env.set_agent_output("query_planner", {"sub_problems": [], "schema_valid": True})
        env.set_agent_output("evidence_analyst", {"judgment_ids": [], "schema_valid_all": True})
        env.set_agent_output("context_orchestrator", {
            "context_result_id": "cr-1",
            "selection_list": [],
            "schema_valid": True,
        })
        env.set_ledger_ref({"ledger_entry_ids": [], "rounds": []})
        env.set_schema_valid_all(True)
        env.set_run_config({"enabled": True, "max_rounds": 2})
        return env.to_dict()

    def test_valid_record_passes_schema(self):
        """A properly constructed run record must pass the schema validation."""
        record = self._make_valid_record()
        # Should not raise
        Draft202012Validator(MERGED_RUN_SCHEMA).validate(record)

    def test_invalid_completion_status_rejected(self):
        """Invalid completion_status must be rejected by the schema."""
        record = self._make_valid_record()
        record["completion_status"] = "bogus"
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_RUN_SCHEMA).validate(record)

    def test_missing_project_scope_rejected(self):
        """Missing project_scope must be rejected (Constitution I)."""
        record = self._make_valid_record()
        del record["project_scope"]
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_RUN_SCHEMA).validate(record)

    def test_max_rounds_over_limit_rejected(self):
        """max_rounds > 3 must be rejected (FR-006 guardrail)."""
        record = self._make_valid_record()
        record["max_rounds"] = 5
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_RUN_SCHEMA).validate(record)

    def test_additional_properties_rejected(self):
        """Additional properties must be rejected (additionalProperties=false)."""
        record = self._make_valid_record()
        record["extra_field"] = "should not be here"
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_RUN_SCHEMA).validate(record)


class TestStateEnvelopeMutators:
    """StateEnvelope should allow incremental updates during the run."""

    def _make_envelope(self):
        from rag_mcp.orchestration.state_envelope import StateEnvelope
        return StateEnvelope(
            run_id="999",
            request_id="req-1",
            project_scope=["proj-a"],
            knowledge_scope_ids=["100"],
        )

    def test_set_completion_status(self):
        env = self._make_envelope()
        env.set_completion_status("partial")
        assert env.to_dict()["completion_status"] == "partial"

    def test_set_rounds_completed(self):
        env = self._make_envelope()
        env.set_rounds_completed(2)
        assert env.to_dict()["rounds_completed"] == 2

    def test_set_guardrail_state(self):
        env = self._make_envelope()
        env.set_guardrail_state({"max_rounds": 2, "node_timeout_ms": 5000})
        assert env.to_dict()["guardrail_state"]["max_rounds"] == 2

    def test_set_sub_path_timings(self):
        env = self._make_envelope()
        env.set_sub_path_timings({"dense": 10.0, "sparse": 20.0})
        assert env.to_dict()["sub_path_timings"]["dense"] == 10.0

    def test_set_total_cost(self):
        env = self._make_envelope()
        env.set_total_cost(0.0025)
        assert env.to_dict()["total_cost"] == 0.0025

    def test_default_completion_status(self):
        """Default completion_status should be a valid four-state value."""
        env = self._make_envelope()
        assert env.to_dict()["completion_status"] in ("complete", "partial", "no_evidence", "failed")
