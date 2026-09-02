"""Unit test for agent_judgment store (T026 Red, US2).

Tests the JudgmentStore that persists evidence analyst judgments:
  - Persists judgments to agent_judgment table (FR-013)
  - round_index is monotonic within a run (FR-009)
  - model_and_version is recorded (FR-002)
  - Output conforms to agent-judgment.schema.json

This test MUST FAIL before judgment_store.py is implemented (TDD Red).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import Draft202012Validator


CONTRACTS_DIR = (
    Path(__file__).parent.parent.parent.parent.parent
    / "specs" / "005-agentic-retrieval-orchestration" / "contracts"
)


def _load_schema(filename: str) -> dict:
    path = CONTRACTS_DIR / filename
    with path.open(encoding="utf-8") as f:
        return json.load(f)


COMMON_SCHEMA = _load_schema("common.schema.json")
JUDGMENT_SCHEMA = _load_schema("agent-judgment.schema.json")


def _merged_with_common(schema: dict) -> dict:
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


MERGED_JUDGMENT_SCHEMA = _merged_with_common(JUDGMENT_SCHEMA)


class TestJudgmentStoreImport:
    def test_import_judgment_store(self):
        """JudgmentStore must be importable."""
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        assert JudgmentStore is not None


class TestAppendOnly:
    """FR-008: judgment store is append-only (no update/delete)."""

    def test_no_update_method(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        assert not hasattr(store, "update_judgment")
        assert not hasattr(store, "update")

    def test_no_delete_method(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        assert not hasattr(store, "delete_judgment")
        assert not hasattr(store, "delete")

    def test_has_insert_method(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        assert hasattr(store, "insert_judgment")
        assert callable(store.insert_judgment)


class TestRoundIndexMonotonic:
    """FR-009: round_index is monotonic within a run."""

    def test_round_index_starts_at_zero(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        assert store.next_round_index() == 0

    def test_round_index_monotonic(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        r0 = store.next_round_index()
        r1 = store.next_round_index()
        assert r0 < r1


class TestModelAndVersion:
    """FR-002: model_and_version must be recorded."""

    def test_model_and_version_in_output(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        record = store.to_judgment_record({
            "run_id": "999",
            "round_index": 0,
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": "deepseek-v4-flash",
            "schema_valid": True,
        })
        assert "model_and_version" in record
        assert record["model_and_version"] == "deepseek-v4-flash"


class TestSchemaConformance:
    """Output conforms to agent-judgment.schema.json."""

    def test_valid_judgment_passes_schema(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        record = store.to_judgment_record({
            "run_id": "999",
            "round_index": 0,
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": "test-v1",
            "schema_valid": True,
        })
        # Should not raise
        Draft202012Validator(MERGED_JUDGMENT_SCHEMA).validate(record)

    def test_invalid_coverage_state_rejected(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        record = store.to_judgment_record({
            "run_id": "999",
            "round_index": 0,
            "coverage_state": "bogus",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": "test-v1",
            "schema_valid": True,
        })
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_JUDGMENT_SCHEMA).validate(record)

    def test_invalid_conflict_type_rejected(self):
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        store = JudgmentStore(MagicMock())
        record = store.to_judgment_record({
            "run_id": "999",
            "round_index": 0,
            "coverage_state": "covered",
            "conflict_type": "bogus",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": "test-v1",
            "schema_valid": True,
        })
        with pytest.raises(Exception):
            Draft202012Validator(MERGED_JUDGMENT_SCHEMA).validate(record)

    @pytest.mark.asyncio
    async def test_insert_judgment_persists(self):
        """insert_judgment should persist to the database."""
        from rag_mcp.orchestration.judgment_store import JudgmentStore
        mock_session = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        store = JudgmentStore(mock_session)
        judgment = await store.insert_judgment({
            "judgment_id": "1234567890",
            "run_id": "999",
            "round_index": 0,
            "coverage_state": "covered",
            "conflict_type": "none",
            "uncovered_sub_problem_ids": [],
            "needs_supplementary": False,
            "gap_descriptions": [],
            "model_and_version": "test-v1",
            "schema_valid": True,
        })
        assert judgment is not None
        mock_session.add.assert_called_once()
