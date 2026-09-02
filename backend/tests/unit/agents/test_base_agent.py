"""Unit test for AgentBase node-Schema validation + degradation fallback (T006 Red).

Tests the abstract Agent base class that provides:
  - Structured output Schema validation (FR-003)
  - Degradation fallback to deterministic equivalent on schema failure (SC-011)
  - Does not block the state machine (always returns a valid result)

This test MUST FAIL before base.py is implemented (TDD Red).
"""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator


class TestAgentBaseImport:
    def test_import_agent_base(self):
        """AgentBase must be importable from rag_mcp.agents.base."""
        from rag_mcp.agents.base import AgentBase
        assert AgentBase is not None

    def test_agent_base_is_abstract(self):
        """AgentBase should be an abstract base class."""
        from rag_mcp.agents.base import AgentBase
        # Cannot instantiate directly (abstract)
        with pytest.raises(TypeError):
            AgentBase()  # type: ignore[abstract]


class TestAgentBaseSchemaValidation:
    """FR-003: Agent structured output must pass node-Schema validation."""

    SAMPLE_SCHEMA = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "coverage_state": {"type": "string", "enum": ["covered", "partial", "uncovered"]},
            "needs_supplementary": {"type": "boolean"},
        },
        "required": ["coverage_state", "needs_supplementary"],
        "additionalProperties": False,
    }

    def _make_agent(self):
        """Create a concrete agent subclass for testing."""
        from rag_mcp.agents.base import AgentBase

        class TestAgent(AgentBase):
            ROLE = "evidence_analyst"
            NODE_SCHEMA = self.SAMPLE_SCHEMA

            def execute(self, context: dict) -> dict:
                return {
                    "coverage_state": "covered",
                    "needs_supplementary": False,
                }

            def fallback(self, context: dict) -> dict:
                return {
                    "coverage_state": "partial",
                    "needs_supplementary": False,
                }

        return TestAgent()

    def test_valid_output_passes_validation(self):
        """Valid structured output should have schema_valid=True."""
        agent = self._make_agent()
        result = agent.validate_output({"coverage_state": "covered", "needs_supplementary": False})
        assert result.schema_valid is True

    def test_invalid_output_fails_validation(self):
        """Invalid structured output should have schema_valid=False (FR-003)."""
        agent = self._make_agent()
        # Missing required field
        result = agent.validate_output({"coverage_state": "covered"})
        assert result.schema_valid is False

    def test_invalid_enum_fails_validation(self):
        """Invalid enum value should fail validation."""
        agent = self._make_agent()
        result = agent.validate_output({"coverage_state": "bogus", "needs_supplementary": False})
        assert result.schema_valid is False

    def test_additional_properties_rejected(self):
        """additionalProperties=False should reject extra fields."""
        agent = self._make_agent()
        result = agent.validate_output({
            "coverage_state": "covered",
            "needs_supplementary": False,
            "extra_field": "should not be here",
        })
        assert result.schema_valid is False


class TestAgentBaseDegradationFallback:
    """SC-011: schema_valid=false triggers deterministic fallback; returns valid four-state."""

    SAMPLE_SCHEMA = {
        "type": "object",
        "properties": {
            "coverage_state": {"type": "string", "enum": ["covered", "partial", "uncovered"]},
            "needs_supplementary": {"type": "boolean"},
        },
        "required": ["coverage_state", "needs_supplementary"],
        "additionalProperties": False,
    }

    def _make_agent_with_bad_output(self):
        """Create an agent whose execute() returns invalid output."""
        from rag_mcp.agents.base import AgentBase

        class BadAgent(AgentBase):
            ROLE = "evidence_analyst"
            NODE_SCHEMA = self.SAMPLE_SCHEMA

            def execute(self, context: dict) -> dict:
                # Returns invalid output (missing required field)
                return {"coverage_state": "covered"}  # missing needs_supplementary

            def fallback(self, context: dict) -> dict:
                return {
                    "coverage_state": "partial",
                    "needs_supplementary": False,
                }

        return BadAgent()

    def test_degradation_on_schema_failure(self):
        """When schema validation fails, agent should fall back to deterministic equivalent."""
        agent = self._make_agent_with_bad_output()
        result = agent.run({})
        # schema_valid should be False (the raw output was invalid)
        assert result.schema_valid is False
        # But the output should be the fallback (valid)
        assert result.output["coverage_state"] in ("covered", "partial", "uncovered")
        assert "needs_supplementary" in result.output

    def test_fallback_returns_valid_four_state(self):
        """Fallback must return a result that allows the state machine to continue (SC-011)."""
        agent = self._make_agent_with_bad_output()
        result = agent.run({})
        # The result must be usable by the state machine (not raise, not block)
        assert result is not None
        assert hasattr(result, "schema_valid")
        assert hasattr(result, "output")
        # Output must be a dict (usable)
        assert isinstance(result.output, dict)

    def test_does_not_block_state_machine(self):
        """Agent failure must not raise or block the state machine (FR-003/SC-011)."""
        agent = self._make_agent_with_bad_output()
        # run() should NOT raise even though execute() returns invalid output
        result = agent.run({})
        assert result is not None  # did not raise

    def test_valid_output_does_not_trigger_fallback(self):
        """When schema validation passes, fallback should NOT be used."""
        from rag_mcp.agents.base import AgentBase

        class GoodAgent(AgentBase):
            ROLE = "evidence_analyst"
            NODE_SCHEMA = self.SAMPLE_SCHEMA

            def execute(self, context: dict) -> dict:
                return {"coverage_state": "covered", "needs_supplementary": False}

            def fallback(self, context: dict) -> dict:
                return {"coverage_state": "uncovered", "needs_supplementary": True}

        agent = GoodAgent()
        result = agent.run({})
        assert result.schema_valid is True
        # Output should be the execute() output, not the fallback
        assert result.output["coverage_state"] == "covered"
        assert result.output["needs_supplementary"] is False


class TestAgentBaseMetadata:
    """Agent must record model_and_version for traceability (FR-002)."""

    def test_agent_role_property(self):
        """Agent must expose its role identifier."""
        from rag_mcp.agents.base import AgentBase

        class MyAgent(AgentBase):
            ROLE = "query_planner"
            NODE_SCHEMA = {"type": "object"}

            def execute(self, context: dict) -> dict:
                return {}

            def fallback(self, context: dict) -> dict:
                return {}

        agent = MyAgent()
        assert agent.role == "query_planner"

    def test_model_and_version_recorded(self):
        """Agent must record model_and_version in its result."""
        from rag_mcp.agents.base import AgentBase

        class MyAgent(AgentBase):
            ROLE = "query_planner"
            NODE_SCHEMA = {"type": "object", "required": ["result"], "additionalProperties": False}
            MODEL_AND_VERSION = "test-model-v1"

            def execute(self, context: dict) -> dict:
                return {"result": "ok"}

            def fallback(self, context: dict) -> dict:
                return {"result": "fallback"}

        agent = MyAgent()
        result = agent.run({})
        assert hasattr(result, "model_and_version")
        assert result.model_and_version == "test-model-v1"
