"""Agent abstract base class with Schema validation + degradation fallback (T007).

Provides the foundational AgentBase that all three Agent roles inherit:
  - Structured output Schema validation (FR-003)
  - Degradation fallback to deterministic equivalent on schema failure (SC-011)
  - Never blocks the state machine: always returns a valid AgentResult

Design (blueprint sec 11, Constitution VI):
  - Agents produce structured outputs validated against node schemas.
  - When validation fails, the agent falls back to a deterministic equivalent
    and still returns a valid four-state-capable result.
  - The deterministic controller (state machine) decides jumps, not the Agent.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result of an Agent run, carrying output + validation status + metadata.

    Attributes:
        output: The agent output dict (from execute if valid, else fallback).
        schema_valid: True if the execute() output passed schema validation.
        model_and_version: Model name and version used for traceability (FR-002).
        degraded: True if the fallback path was taken (SC-011).
        error: Optional error message if validation failed.
    """

    output: dict[str, Any]
    schema_valid: bool
    model_and_version: str = ""
    degraded: bool = False
    error: str | None = None


class AgentBase(ABC):
    """Abstract base for all Agent roles (blueprint sec 11, FR-003).

    Subclasses MUST define:
        ROLE: str           -- agent role identifier (query_planner / evidence_analyst / context_orchestrator)
        NODE_SCHEMA: dict   -- JSON Schema (draft 2020-12) for the agent output
        execute(context)    -- produce structured output from context
        fallback(context)   -- deterministic equivalent when schema validation fails

    Optional:
        MODEL_AND_VERSION: str -- model name + version for traceability (default "")
    """

    ROLE: str = ""
    NODE_SCHEMA: dict[str, Any] = {}
    MODEL_AND_VERSION: str = ""

    def __init__(self, model_and_version: str | None = None) -> None:
        self._validator: Draft202012Validator | None = None
        if self.NODE_SCHEMA:
            self._validator = Draft202012Validator(self.NODE_SCHEMA)
        # Allow per-instance override of model_and_version
        if model_and_version is not None:
            self._model_and_version = model_and_version
        else:
            self._model_and_version = self.MODEL_AND_VERSION

    @property
    def role(self) -> str:
        """Return the agent role identifier."""
        return self.ROLE

    @property
    def model_and_version(self) -> str:
        """Return the model name and version for traceability (FR-002)."""
        return self._model_and_version

    @abstractmethod
    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """Produce structured output from the given context.

        The output MUST conform to NODE_SCHEMA. If it does not, validate_output
        will flag schema_valid=False and run() will invoke fallback().
        """
        ...

    @abstractmethod
    def fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Deterministic equivalent output when schema validation fails (SC-011).

        This MUST return a valid output that allows the state machine to continue.
        It must NOT raise and must NOT depend on the LLM.
        """
        ...

    def validate_output(self, output: dict[str, Any]) -> AgentResult:
        """Validate the agent output against NODE_SCHEMA (FR-003).

        Returns an AgentResult with schema_valid=True if valid, False otherwise.
        The output field is set to the given output (caller decides fallback).
        """
        if self._validator is None:
            # No schema defined: treat as valid
            return AgentResult(
                output=output,
                schema_valid=True,
                model_and_version=self._model_and_version,
            )

        errors = list(self._validator.iter_errors(output))
        if not errors:
            return AgentResult(
                output=output,
                schema_valid=True,
                model_and_version=self._model_and_version,
            )
        else:
            error_msg = "; ".join(str(e.message) for e in errors)
            logger.warning(
                "Agent %s output failed schema validation: %s",
                self.ROLE, error_msg,
            )
            return AgentResult(
                output=output,  # raw output; caller may replace with fallback
                schema_valid=False,
                model_and_version=self._model_and_version,
                error=error_msg,
            )

    def run(self, context: dict[str, Any]) -> AgentResult:
        """Execute the agent, validate output, fall back on failure (SC-011).

        This is the main entry point called by the state machine. It:
          1. Calls execute() to produce structured output.
          2. Validates the output against NODE_SCHEMA.
          3. If valid: returns the output with schema_valid=True.
          4. If invalid: calls fallback() for a deterministic equivalent,
             returns it with schema_valid=False and degraded=True.
             The state machine is NOT blocked (FR-003/SC-011).
        """
        try:
            raw_output = self.execute(context)
        except Exception as exc:
            # Agent execution itself failed: fall back immediately
            logger.warning("Agent %s execute() raised: %s", self.ROLE, exc)
            fallback_output = self._safe_fallback(context)
            return AgentResult(
                output=fallback_output,
                schema_valid=False,
                model_and_version=self._model_and_version,
                degraded=True,
                error=f"execute() raised: {exc}",
            )

        result = self.validate_output(raw_output)
        if result.schema_valid:
            return result

        # Schema validation failed: fall back to deterministic equivalent
        fallback_output = self._safe_fallback(context)
        return AgentResult(
            output=fallback_output,
            schema_valid=False,
            model_and_version=self._model_and_version,
            degraded=True,
            error=result.error,
        )

    def _safe_fallback(self, context: dict[str, Any]) -> dict[str, Any]:
        """Call fallback() safely, returning {} if it also fails (SC-011)."""
        try:
            return self.fallback(context)
        except Exception as exc:
            logger.error("Agent %s fallback() also raised: %s", self.ROLE, exc)
            return {}
