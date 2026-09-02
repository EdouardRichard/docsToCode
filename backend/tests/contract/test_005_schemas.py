"""Contract validation test for 005 Agentic Retrieval Orchestration schemas (T004/T005).

Validates the four internal contract schemas (common, evidence-ledger-entry,
agent-judgment, agentic-retrieval-run) are valid JSON Schema (draft 2020-12),
that the固化 identity/enum definitions are present, and that dependent schemas
$ref the shared common.schema.json definitions. These are internal traceability
contracts; the external MCP search_knowledge/get_evidence schemas remain unchanged
(FR-024, Constitution VII).
"""
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

CONTRACTS_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "specs"
    / "005-agentic-retrieval-orchestration"
    / "contracts"
)


def _load_schema(filename: str) -> dict:
    """Load a JSON schema from the 005 contracts directory."""
    path = CONTRACTS_DIR / filename
    assert path.exists(), f"contract schema not found: {path}"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


COMMON_SCHEMA = _load_schema("common.schema.json")
LEDGER_SCHEMA = _load_schema("evidence-ledger-entry.schema.json")
JUDGMENT_SCHEMA = _load_schema("agent-judgment.schema.json")
RUN_SCHEMA = _load_schema("agentic-retrieval-run.schema.json")


# The 005 schemas reuse common.schema.json's shared definitions via absolute $ref
# to common's $id. For self-contained instance validation in the test harness we
# inline common's definitions into the schema under test as $defs and rewrite the
# $ref to #/$defs/X (no cross-file resolution magic). Cross-file $ref structure
# is asserted separately in TestCrossReferencesToCommon.
def _merged_with_common(schema: dict) -> dict:
    """Return a copy of schema with common.schema.json definitions inlined under $defs."""
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


def _enum(def_name: str) -> list:
    return COMMON_SCHEMA["definitions"][def_name]["enum"]


class TestSchemasAreValidDraft202012:
    @pytest.mark.parametrize("name,schema", [
        ("common.schema.json", COMMON_SCHEMA),
        ("evidence-ledger-entry.schema.json", LEDGER_SCHEMA),
        ("agent-judgment.schema.json", JUDGMENT_SCHEMA),
        ("agentic-retrieval-run.schema.json", RUN_SCHEMA),
    ])
    def test_schema_is_valid(self, name, schema):
        Draft202012Validator.check_schema(schema)


class Test固化EnumsPresent:
    def test_agent_role_enum(self):
        assert _enum("AgentRole") == ["query_planner", "evidence_analyst", "context_orchestrator"]

    def test_coverage_state_enum(self):
        assert _enum("CoverageState") == ["covered", "partial", "uncovered"]

    def test_conflict_type_enum(self):
        assert _enum("ConflictType") == ["none", "version_conflict", "source_conflict", "domain_conflict"]

    def test_selection_decision_enum(self):
        assert _enum("SelectionDecision") == ["selected", "truncated", "deduped"]

    def test_retriever_type_enum(self):
        assert _enum("RetrieverType") == ["dense", "sparse", "graph", "fusion", "rerank"]


class Test固化IdDefinitionsPresent:
    @pytest.mark.parametrize("def_name", [
        "RunId", "LedgerEntryId", "SubProblemId", "RoundIndex",
        "ContextResultId", "RequestId", "EvidenceId", "CompletionStatus",
        "KnowledgeScopeId", "SourceVersion", "RelevanceScore",
    ])
    def test_id_definition_exists(self, def_name):
        assert def_name in COMMON_SCHEMA["definitions"], f"common.schema.json missing definition: {def_name}"

    @pytest.mark.parametrize("def_name", ["RunId", "LedgerEntryId", "KnowledgeScopeId"])
    def test_snowflake_id_pattern(self, def_name):
        assert COMMON_SCHEMA["definitions"][def_name].get("pattern") == "^[0-9]+$"


class TestCrossReferencesToCommon:
    @pytest.mark.parametrize("schema,filename", [
        (LEDGER_SCHEMA, "evidence-ledger-entry.schema.json"),
        (JUDGMENT_SCHEMA, "agent-judgment.schema.json"),
        (RUN_SCHEMA, "agentic-retrieval-run.schema.json"),
    ])
    def test_refs_common(self, schema, filename):
        text = json.dumps(schema)
        assert "common.schema.json#/definitions/" in text, f"{filename} must $ref common.schema.json shared definitions"


class TestEvidenceLedgerEntryContract:
    def test_required_fields(self):
        required = set(LEDGER_SCHEMA["required"])
        for field in [
            "ledger_entry_id", "request_id", "run_id", "round_index", "sub_problem_id",
            "evidence_id", "retrieval_query", "retriever", "score", "source_version",
            "source_position", "knowledge_scope_id", "knowledge_scope_type", "referenced_by_agent",
        ]:
            assert field in required, f"evidence-ledger-entry missing required field: {field}"

    def test_additional_properties_false(self):
        assert LEDGER_SCHEMA.get("additionalProperties") is False

    def test_valid_ledger_entry_instance(self):
        example = {
            "ledger_entry_id": "1234567890",
            "request_id": "req-1",
            "run_id": "999",
            "round_index": 0,
            "sub_problem_id": 1,
            "evidence_id": "ev-1",
            "retrieval_query": "who calls validateToken",
            "retriever": "graph",
            "score": 0.42,
            "source_version": 1,
            "source_position": "com.example.Service#validateToken",
            "knowledge_scope_id": "100",
            "knowledge_scope_type": "project",
            "referenced_by_agent": "query_planner",
        }
        Draft202012Validator(_merged_with_common(LEDGER_SCHEMA)).validate(example)

    def test_invalid_retriever_rejected(self):
        example = {
            "ledger_entry_id": "1", "request_id": "r", "run_id": "9", "round_index": 0,
            "sub_problem_id": 1, "evidence_id": "e", "retrieval_query": "q", "retriever": "bogus",
            "score": 0.1, "source_version": 1, "source_position": "p",
            "knowledge_scope_id": "100", "knowledge_scope_type": "project",
            "referenced_by_agent": "query_planner",
        }
        with pytest.raises(Exception):
            Draft202012Validator(_merged_with_common(LEDGER_SCHEMA)).validate(example)
