"""Contract test for knowledge-capabilities.graph-extension.schema.json (T017).

Validates graph_ready implies dense+lexical; illegal combos rejected
(data-model §5, FR-013/FR-014/FR-015).

Schema: specs/003-structured-asset-expansion/contracts/knowledge-capabilities.graph-extension.schema.json
"""

from __future__ import annotations

import jsonschema
import pytest

from tests.contract._graph_schema_helper import capabilities_ext_schema


@pytest.fixture
def schema() -> dict:
    return capabilities_ext_schema()


class TestValidCombinations:
    def test_graph_ready_true(self, schema):
        jsonschema.validate(
            {"dense_ready": True, "lexical_ready": True, "graph_ready": True}, schema)

    def test_graph_ready_false(self, schema):
        jsonschema.validate(
            {"dense_ready": True, "lexical_ready": True, "graph_ready": False}, schema)

    def test_dense_only_graph_false(self, schema):
        jsonschema.validate(
            {"dense_ready": True, "lexical_ready": False, "graph_ready": False}, schema)


class TestInvalidCombinations:
    def test_graph_ready_without_dense_rejected(self, schema):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {"dense_ready": False, "lexical_ready": True, "graph_ready": True}, schema)

    def test_graph_ready_without_lexical_rejected(self, schema):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(
                {"dense_ready": True, "lexical_ready": False, "graph_ready": True}, schema)

    def test_dense_false_rejected(self, schema):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"dense_ready": False}, schema)

    def test_missing_dense_ready_rejected(self, schema):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"graph_ready": True}, schema)
