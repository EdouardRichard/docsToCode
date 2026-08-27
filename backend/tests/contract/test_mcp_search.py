"""Contract tests for search_knowledge MCP Tool output schema (T038).

Validates that RetrievalService output structures conform to
mcp-search-output.schema.json. These are pure structure validation tests -
no DB, Qdrant, or embedding dependencies required.

The schema uses $ref to common.schema.json#/definitions/EvidenceItem, so we
resolve references manually before validation with jsonschema.
"""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

# ---------------------------------------------------------------------------
# Schema loading helpers
# ---------------------------------------------------------------------------

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "specs" / "001-minimum-rag-mcp-loop" / "contracts"


def _load_schema(filename: str) -> dict:
    """Load a JSON schema file from the contracts directory."""
    return json.loads((_CONTRACTS_DIR / filename).read_text(encoding="utf-8"))


def _build_resolved_search_output_schema() -> dict:
    """Load the search-output schema (already fully inlined, no $ref)."""
    search_schema = _load_schema("mcp-search-output.schema.json")
    # Remove top-level $id to avoid resolver issues
    search_schema.pop("$id", None)
    search_schema.pop("$schema", None)
    return search_schema


SEARCH_OUTPUT_SCHEMA = _build_resolved_search_output_schema()


def _validate(instance: dict) -> None:
    """Validate an instance against the search output schema; raise on failure."""
    validator = Draft202012Validator(SEARCH_OUTPUT_SCHEMA)
    errors = list(validator.iter_errors(instance))
    if errors:
        messages = [f"  - {e.json_path}: {e.message}" for e in errors]
        raise AssertionError(
            f"Schema validation failed with {len(errors)} error(s):\n" + "\n".join(messages)
        )


def _assert_invalid(instance: dict) -> list[ValidationError]:
    """Assert that an instance does NOT pass schema validation. Returns errors."""
    validator = Draft202012Validator(SEARCH_OUTPUT_SCHEMA)
    errors = list(validator.iter_errors(instance))
    assert errors, "Expected schema validation to fail, but it passed."
    return errors


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_evidence_item(**overrides) -> dict:
    """Create a minimal valid EvidenceItem dict."""
    item = {
        "evidence_id": "123456789012345678",
        "content_excerpt": "Some evidence text excerpt.",
        "source_version": 1,
        "source_position": "## Section > ### Subsection",
        "knowledge_scope_id": "987654321098765432",
        "knowledge_scope_type": "project",
        "relevance_score": 0.95,
    }
    item.update(overrides)
    return item


def _make_response(**overrides) -> dict:
    """Create a minimal valid complete response."""
    resp = {
        "completion_status": "complete",
        "evidence": [_make_evidence_item()],
        "request_id": "req-abc-123",
    }
    resp.update(overrides)
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSearchKnowledgeOutputSchema:
    """Validate search_knowledge output conforms to mcp-search-output.schema.json."""

    def test_complete_response_valid(self):
        """A complete response with evidence passes schema validation."""
        response = _make_response()
        _validate(response)

    def test_complete_response_multiple_evidence(self):
        """A complete response with multiple evidence items is valid."""
        response = _make_response(
            evidence=[
                _make_evidence_item(evidence_id="111", relevance_score=0.95),
                _make_evidence_item(evidence_id="222", relevance_score=0.85),
                _make_evidence_item(evidence_id="333", relevance_score=0.75),
            ]
        )
        _validate(response)

    def test_partial_response_requires_gaps(self):
        """Partial response must include gaps array."""
        # Partial without gaps should fail
        response = _make_response(completion_status="partial")
        # Remove gaps key entirely
        if "gaps" in response:
            del response["gaps"]
        _assert_invalid(response)

    def test_partial_response_with_gaps_valid(self):
        """Partial response with gaps array passes validation."""
        response = _make_response(
            completion_status="partial",
            gaps=[
                {"description": "Missing API authentication details"},
                {
                    "description": "No deployment configuration found",
                    "suggested_action": "Check docs/deployment.md",
                },
            ],
        )
        _validate(response)

    def test_partial_response_empty_gaps_array_valid(self):
        """Partial response with empty gaps array is structurally valid.

        The schema requires 'gaps' key presence for partial status but
        does not enforce minItems on the gaps array itself.
        """
        response = _make_response(completion_status="partial", gaps=[])
        _validate(response)

    def test_no_evidence_response_empty_array(self):
        """no_evidence response has empty evidence array."""
        response = _make_response(completion_status="no_evidence", evidence=[])
        _validate(response)

    def test_no_evidence_response_nonempty_evidence_fails(self):
        """no_evidence response with non-empty evidence array fails validation."""
        response = _make_response(
            completion_status="no_evidence",
            evidence=[_make_evidence_item()],
        )
        _assert_invalid(response)

    def test_failed_response_requires_error(self):
        """Failed response must include error object."""
        response = _make_response(completion_status="failed")
        # No error key -> should fail
        if "error" in response:
            del response["error"]
        _assert_invalid(response)

    def test_failed_response_with_error_valid(self):
        """Failed response with error object passes validation."""
        response = _make_response(
            completion_status="failed",
            evidence=[],
            error={
                "code": "SYSTEM_ERROR",
                "message": "Qdrant connection timeout",
            },
        )
        _validate(response)

    def test_failed_response_all_error_codes(self):
        """All defined error codes are accepted in failed responses."""
        valid_codes = [
            "SYSTEM_ERROR",
            "MISSING_PROJECT_SCOPE",
            "AMBIGUOUS_PROJECT_REF",
            "INVALID_PROJECT_REF",
            "INDEX_UNAVAILABLE",
        ]
        for code in valid_codes:
            response = _make_response(
                completion_status="failed",
                evidence=[],
                error={"code": code, "message": f"Error: {code}"},
            )
            _validate(response)

    def test_ambiguous_project_ref_error_with_candidates(self):
        """AMBIGUOUS_PROJECT_REF error includes candidates array."""
        response = _make_response(
            completion_status="failed",
            evidence=[],
            error={
                "code": "AMBIGUOUS_PROJECT_REF",
                "message": "Multiple projects match 'my-project'",
                "candidates": [
                    {
                        "project_id": "111111111111111111",
                        "name": "My Project Alpha",
                        "alias": "my-project-alpha",
                    },
                    {
                        "project_id": "222222222222222222",
                        "name": "My Project Beta",
                        "repo_path": "/repos/my-project-beta",
                    },
                ],
            },
        )
        _validate(response)

    def test_ambiguous_project_ref_candidate_minimal_fields(self):
        """Candidate objects require only project_id and name."""
        response = _make_response(
            completion_status="failed",
            evidence=[],
            error={
                "code": "AMBIGUOUS_PROJECT_REF",
                "message": "Ambiguous ref",
                "candidates": [
                    {"project_id": "111111111111111111", "name": "Project A"}
                ],
            },
        )
        _validate(response)

    def test_all_four_completion_statuses_valid(self):
        """All four statuses (complete, partial, no_evidence, failed) are valid."""
        cases = [
            _make_response(completion_status="complete"),
            _make_response(
                completion_status="partial",
                gaps=[{"description": "gap"}],
            ),
            _make_response(completion_status="no_evidence", evidence=[]),
            _make_response(
                completion_status="failed",
                evidence=[],
                error={"code": "SYSTEM_ERROR", "message": "fail"},
            ),
        ]
        for case in cases:
            _validate(case)

    def test_missing_required_field_completion_status(self):
        """Response without completion_status fails validation."""
        response = {"evidence": [], "request_id": "req-1"}
        _assert_invalid(response)

    def test_missing_required_field_evidence(self):
        """Response without evidence field fails validation."""
        response = {"completion_status": "complete", "request_id": "req-1"}
        _assert_invalid(response)

    def test_missing_required_field_request_id(self):
        """Response without request_id fails validation."""
        response = {"completion_status": "complete", "evidence": []}
        _assert_invalid(response)

    def test_additional_properties_rejected(self):
        """Extra fields at the top level are rejected (additionalProperties: false)."""
        response = _make_response(extra_field="not allowed")
        _assert_invalid(response)

    def test_evidence_item_content_excerpt_max_length(self):
        """Evidence content_excerpt exceeding 500 chars fails validation."""
        response = _make_response(
            evidence=[
                _make_evidence_item(content_excerpt="x" * 501)
            ]
        )
        _assert_invalid(response)

    def test_evidence_item_content_excerpt_at_boundary(self):
        """Evidence content_excerpt at exactly 500 chars passes validation."""
        response = _make_response(
            evidence=[
                _make_evidence_item(content_excerpt="x" * 500)
            ]
        )
        _validate(response)

    def test_evidence_item_relevance_score_boundaries(self):
        """Relevance score must be between 0 and 1 inclusive."""
        # Valid boundaries
        _validate(_make_response(evidence=[_make_evidence_item(relevance_score=0.0)]))
        _validate(_make_response(evidence=[_make_evidence_item(relevance_score=1.0)]))

        # Invalid: above 1
        _assert_invalid(_make_response(evidence=[_make_evidence_item(relevance_score=1.01)]))

        # Invalid: below 0
        _assert_invalid(_make_response(evidence=[_make_evidence_item(relevance_score=-0.01)]))

    def test_evidence_item_source_version_minimum(self):
        """Source version must be >= 1."""
        _validate(_make_response(evidence=[_make_evidence_item(source_version=1)]))
        _assert_invalid(_make_response(evidence=[_make_evidence_item(source_version=0)]))

    def test_evidence_item_knowledge_scope_type_enum(self):
        """knowledge_scope_type must be 'project' or 'public'."""
        _validate(_make_response(evidence=[_make_evidence_item(knowledge_scope_type="project")]))
        _validate(_make_response(evidence=[_make_evidence_item(knowledge_scope_type="public")]))
        _assert_invalid(_make_response(evidence=[_make_evidence_item(knowledge_scope_type="invalid")]))

    def test_evidence_item_additional_properties_rejected(self):
        """Extra fields in EvidenceItem are rejected."""
        response = _make_response(
            evidence=[_make_evidence_item(bogus_field="nope")]
        )
        _assert_invalid(response)

    def test_error_object_additional_properties_rejected(self):
        """Extra fields in error object are rejected (additionalProperties: false)."""
        response = _make_response(
            completion_status="failed",
            evidence=[],
            error={
                "code": "SYSTEM_ERROR",
                "message": "fail",
                "extra": "not allowed",
            },
        )
        _assert_invalid(response)

    def test_gap_object_requires_description(self):
        """Gap objects must have a description field."""
        response = _make_response(
            completion_status="partial",
            gaps=[{"suggested_action": "do something"}],  # missing description
        )
        _assert_invalid(response)

    def test_gap_object_additional_properties_rejected(self):
        """Extra fields in gap objects are rejected."""
        response = _make_response(
            completion_status="partial",
            gaps=[{"description": "gap", "extra": "nope"}],
        )
        _assert_invalid(response)

    def test_invalid_completion_status_rejected(self):
        """An unknown completion_status value fails validation."""
        response = _make_response(completion_status="unknown_status")
        _assert_invalid(response)

    def test_evidence_item_knowledge_scope_id_pattern(self):
        """knowledge_scope_id must match ^[0-9]+$ pattern."""
        # Valid numeric string
        _validate(_make_response(evidence=[_make_evidence_item(knowledge_scope_id="12345")]))
        # Invalid: contains letters
        _assert_invalid(_make_response(evidence=[_make_evidence_item(knowledge_scope_id="abc123")]))
        # Invalid: empty string
        _assert_invalid(_make_response(evidence=[_make_evidence_item(knowledge_scope_id="")]))
