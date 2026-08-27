"""Contract validation test for all MCP schemas (T054).

Validates that example responses for search_knowledge and get_evidence
conform to their respective JSON Schema definitions. Ensures 100% schema
validity per SC-004.
"""

import json
from pathlib import Path

import pytest
from jsonschema import validate, ValidationError

CONTRACTS_DIR = Path(__file__).parent.parent.parent.parent / "specs" / "001-minimum-rag-mcp-loop" / "contracts"


def _load_schema(filename: str) -> dict:
    """Load a JSON schema from the contracts directory."""
    with open(CONTRACTS_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


SEARCH_OUTPUT_SCHEMA = _load_schema("mcp-search-output.schema.json")
GET_EVIDENCE_SCHEMA = _load_schema("mcp-get-evidence.schema.json")


class TestSearchKnowledgeSchemaValidity:
    """Validate search_knowledge response examples against schema."""

    def test_complete_response_valid(self):
        response = {
            "completion_status": "complete",
            "evidence": [
                {
                    "evidence_id": "123456789",
                    "content_excerpt": "Evidence text excerpt",
                    "source_version": 1,
                    "source_position": "## Installation > ### Requirements",
                    "knowledge_scope_id": "987654321",
                    "knowledge_scope_type": "project",
                    "relevance_score": 0.92,
                }
            ],
            "request_id": "req-001",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)

    def test_partial_response_with_gaps_valid(self):
        response = {
            "completion_status": "partial",
            "evidence": [
                {
                    "evidence_id": "111",
                    "content_excerpt": "Partial evidence",
                    "source_version": 1,
                    "source_position": "## Config",
                    "knowledge_scope_id": "222",
                    "knowledge_scope_type": "project",
                    "relevance_score": 0.7,
                }
            ],
            "gaps": [{"description": "Missing API endpoint details", "suggested_action": "Upload API docs"}],
            "request_id": "req-002",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)

    def test_no_evidence_response_valid(self):
        response = {
            "completion_status": "no_evidence",
            "evidence": [],
            "request_id": "req-003",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)

    def test_failed_response_valid(self):
        response = {
            "completion_status": "failed",
            "evidence": [],
            "error": {"code": "SYSTEM_ERROR", "message": "Qdrant unavailable"},
            "request_id": "req-004",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)

    def test_ambiguous_project_ref_valid(self):
        response = {
            "completion_status": "failed",
            "evidence": [],
            "error": {
                "code": "AMBIGUOUS_PROJECT_REF",
                "message": "Multiple projects match 'my-proj'",
                "candidates": [
                    {"project_id": "111", "name": "my-project-a"},
                    {"project_id": "222", "name": "my-project-b", "alias": "my-proj"},
                ],
            },
            "request_id": "req-005",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)

    def test_public_evidence_type_valid(self):
        response = {
            "completion_status": "complete",
            "evidence": [
                {
                    "evidence_id": "333",
                    "content_excerpt": "Public API docs",
                    "source_version": 2,
                    "source_position": "## Authentication",
                    "knowledge_scope_id": "444",
                    "knowledge_scope_type": "public",
                    "relevance_score": 0.85,
                }
            ],
            "request_id": "req-006",
        }
        validate(instance=response, schema=SEARCH_OUTPUT_SCHEMA)


class TestGetEvidenceSchemaValidity:
    """Validate get_evidence response examples against schema."""

    @property
    def output_schema(self):
        return GET_EVIDENCE_SCHEMA.get("properties", {}).get("output", GET_EVIDENCE_SCHEMA)

    def test_available_response_valid(self):
        response = {
            "evidence_id": "123456789",
            "full_content": "Full chunk content here...",
            "parent_context": "Parent section content...",
            "source_version": 1,
            "source_position": "## Section > ### Subsection",
            "knowledge_scope_id": "987654321",
            "knowledge_scope_type": "project",
            "status": "available",
        }
        # Validate against the output sub-schema or full schema
        if "properties" in GET_EVIDENCE_SCHEMA and "output" in GET_EVIDENCE_SCHEMA["properties"]:
            output_schema = GET_EVIDENCE_SCHEMA["properties"]["output"]
            validate(instance=response, schema=output_schema)
        else:
            validate(instance=response, schema=GET_EVIDENCE_SCHEMA)

    def test_scope_mismatch_response_valid(self):
        response = {
            "evidence_id": "123456789",
            "status": "scope_mismatch",
            "error": {
                "code": "SCOPE_MISMATCH",
                "message": "Evidence belongs to scope X, not requested scope Y",
            },
        }
        if "properties" in GET_EVIDENCE_SCHEMA and "output" in GET_EVIDENCE_SCHEMA["properties"]:
            output_schema = GET_EVIDENCE_SCHEMA["properties"]["output"]
            validate(instance=response, schema=output_schema)

    def test_unavailable_response_valid(self):
        response = {
            "evidence_id": "123456789",
            "status": "unavailable",
            "error": {
                "code": "VERSION_REMOVED",
                "message": "Evidence version has been deleted or superseded",
            },
        }
        if "properties" in GET_EVIDENCE_SCHEMA and "output" in GET_EVIDENCE_SCHEMA["properties"]:
            output_schema = GET_EVIDENCE_SCHEMA["properties"]["output"]
            validate(instance=response, schema=output_schema)
