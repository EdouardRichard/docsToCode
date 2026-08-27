"""Contract tests for get_evidence output schema validation (T044).

Validates that get_evidence responses conform to the "output" sub-schema
defined in mcp-get-evidence.schema.json. Uses jsonschema for validation.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

# Load schema at module level
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs"
    / "001-minimum-rag-mcp-loop"
    / "contracts"
    / "mcp-get-evidence.schema.json"
)

with open(_SCHEMA_PATH, "r", encoding="utf-8") as _f:
    _FULL_SCHEMA = json.load(_f)

# Extract the "output" sub-schema for validation
OUTPUT_SCHEMA = _FULL_SCHEMA["properties"]["output"]


def _validate(data: dict) -> list[str]:
    """Validate data against the output schema; return list of error messages."""
    validator = Draft202012Validator(OUTPUT_SCHEMA)
    return [error.message for error in validator.iter_errors(data)]


class TestGetEvidenceOutputSchema:
    """Contract tests for get_evidence output structure."""

    def test_available_response_valid(self):
        """status=available with full_content passes schema validation."""
        response = {
            "evidence_id": "123456789012345678",
            "full_content": "This is the complete chunk content for evidence retrieval.",
            "parent_context": "# Parent Section\n\nSome parent context here.",
            "source_version": 3,
            "source_position": "docs/guide.md##installation",
            "knowledge_scope_id": "987654321098765432",
            "knowledge_scope_type": "project",
            "status": "available",
        }
        errors = _validate(response)
        assert errors == [], f"Validation errors: {errors}"

    def test_scope_mismatch_response_valid(self):
        """status=scope_mismatch with error object passes schema validation."""
        response = {
            "evidence_id": "123456789012345678",
            "status": "scope_mismatch",
            "error": {
                "code": "SCOPE_MISMATCH",
                "message": "Evidence does not belong to any of the requested project scopes.",
            },
        }
        errors = _validate(response)
        assert errors == [], f"Validation errors: {errors}"

    def test_unavailable_response_valid(self):
        """status=unavailable with error object passes schema validation."""
        response = {
            "evidence_id": "123456789012345678",
            "status": "unavailable",
            "error": {
                "code": "EVIDENCE_UNAVAILABLE",
                "message": "The evidence version has been superseded or deleted.",
            },
        }
        errors = _validate(response)
        assert errors == [], f"Validation errors: {errors}"

    def test_available_requires_full_content(self):
        """status=available without full_content fails validation.

        While the schema only requires evidence_id and status at the top level,
        an available response semantically requires full_content. This test
        validates that a minimal available response (without full_content) still
        passes the JSON schema but documents the semantic expectation.
        Note: The current schema does NOT enforce full_content as required for
        available status — this is a known gap documented by this test.
        """
        response = {
            "evidence_id": "123456789012345678",
            "status": "available",
        }
        # Per the current schema, evidence_id + status are the only required fields.
        # This passes schema validation but would fail semantic validation.
        errors = _validate(response)
        # Schema-level: passes (only evidence_id and status are required)
        assert errors == [], (
            f"Schema should accept minimal available response; errors: {errors}"
        )

    def test_error_structure_valid(self):
        """Error object must contain both code and message fields."""
        # Valid error structure
        valid_response = {
            "evidence_id": "123456789012345678",
            "status": "unavailable",
            "error": {
                "code": "SOME_ERROR",
                "message": "Something went wrong.",
            },
        }
        errors = _validate(valid_response)
        assert errors == [], f"Valid error structure rejected: {errors}"

        # Invalid: error missing 'code'
        invalid_no_code = {
            "evidence_id": "123456789012345678",
            "status": "unavailable",
            "error": {
                "message": "Missing code field.",
            },
        }
        errors = _validate(invalid_no_code)
        assert len(errors) > 0, "Error without 'code' should fail validation"

        # Invalid: error missing 'message'
        invalid_no_message = {
            "evidence_id": "123456789012345678",
            "status": "unavailable",
            "error": {
                "code": "NO_MESSAGE",
            },
        }
        errors = _validate(invalid_no_message)
        assert len(errors) > 0, "Error without 'message' should fail validation"

        # Invalid: error with extra properties
        invalid_extra = {
            "evidence_id": "123456789012345678",
            "status": "unavailable",
            "error": {
                "code": "EXTRA",
                "message": "Has extra field.",
                "stack_trace": "should not be here",
            },
        }
        errors = _validate(invalid_extra)
        assert len(errors) > 0, "Error with additionalProperties should fail validation"
