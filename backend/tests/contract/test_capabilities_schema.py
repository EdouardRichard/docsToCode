"""Contract test for knowledge-capabilities schema (T013).

Validates the knowledge-capabilities.schema.json contract:
- dense_ready + lexical_ready valid/invalid combinations
- lexical_ready implies dense_ready (gating rule)
- Schema compliance for all combinations

Schema: specs/002-hybrid-retrieval-precision/contracts/knowledge-capabilities.schema.json
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = (
    _REPO_ROOT
    / "specs"
    / "002-hybrid-retrieval-precision"
    / "contracts"
    / "knowledge-capabilities.schema.json"
)


@pytest.fixture
def schema() -> dict:
    """Load the knowledge-capabilities schema."""
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Valid capability combinations
# ---------------------------------------------------------------------------

class TestValidCombinations:
    """Valid capability declarations that must pass schema validation."""

    @pytest.mark.parametrize("capabilities", [
        {"dense_ready": True},
        {"dense_ready": True, "lexical_ready": False},
        {"dense_ready": True, "lexical_ready": True},
    ])
    def test_valid_combinations(self, schema, capabilities):
        """Valid capability combos must pass schema validation."""
        jsonschema.validate(capabilities, schema)


# ---------------------------------------------------------------------------
# Invalid capability combinations
# ---------------------------------------------------------------------------

class TestInvalidCombinations:
    """Invalid capability declarations that must fail schema validation."""

    def test_dense_ready_false_rejected(self, schema):
        """dense_ready=false must be rejected (Dense is the base capability)."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": False}, schema)

    def test_dense_ready_false_lexical_true_rejected(self, schema):
        """dense_ready=false + lexical_ready=true must be rejected."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": False, "lexical_ready": True}, schema)

    def test_missing_dense_ready_rejected(self, schema):
        """Missing dense_ready must be rejected (required field)."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"lexical_ready": True}, schema)

    def test_extra_fields_rejected(self, schema):
        """Additional properties must be rejected (additionalProperties: false)."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": True, "graph_ready": True}, schema)

    def test_non_boolean_rejected(self, schema):
        """Non-boolean values must be rejected."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": "yes"}, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": True, "lexical_ready": 1}, schema)


# ---------------------------------------------------------------------------
# Gating rule: lexical_ready implies dense_ready
# ---------------------------------------------------------------------------

class TestGatingRule:
    """lexical_ready=true must imply dense_ready=true (FR-011/FR-013)."""

    def test_lexical_ready_implies_dense_ready(self, schema):
        """lexical_ready=true with dense_ready=true is valid."""
        jsonschema.validate({"dense_ready": True, "lexical_ready": True}, schema)

    def test_lexical_ready_without_dense_rejected(self, schema):
        """lexical_ready=true with dense_ready=false is rejected by schema."""
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": False, "lexical_ready": True}, schema)

    def test_dense_only_without_lexical_valid(self, schema):
        """dense_ready=true without lexical_ready is valid (Dense-only version)."""
        jsonschema.validate({"dense_ready": True}, schema)


# ---------------------------------------------------------------------------
# Schema structure validation
# ---------------------------------------------------------------------------

class TestSchemaStructure:
    """Verify the schema itself has the expected structure."""

    def test_schema_has_dense_ready(self, schema):
        assert "dense_ready" in schema["properties"]

    def test_schema_has_lexical_ready(self, schema):
        assert "lexical_ready" in schema["properties"]

    def test_dense_ready_required(self, schema):
        assert "dense_ready" in schema["required"]

    def test_additional_properties_false(self, schema):
        assert schema.get("additionalProperties") is False

    def test_all_of_constraints_present(self, schema):
        """Schema must have allOf constraints for gating rules."""
        assert "allOf" in schema
        assert len(schema["allOf"]) >= 2
