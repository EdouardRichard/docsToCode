"""Unit test for append-only ledger store (T010 Red).

Tests the EvidenceLedgerStore that provides:
  - Append-only INSERT (no UPDATE/DELETE path, FR-008)
  - Snowflake ID for ledger_entry_id (^[0-9]+$ string form, FR-032)
  - Monotonic round_index / sub_problem_id (FR-009)
  - (request_id, evidence_id) bridge key resolution (FR-024)
  - Cross-scope write rejection (FR-022, Constitution hard constraint)

This test MUST FAIL before ledger.py is implemented (TDD Red).
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest


SNOWFLAKE_PATTERN = re.compile(r"^[0-9]+$")


class TestLedgerStoreImport:
    def test_import_ledger_store(self):
        """EvidenceLedgerStore must be importable."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        assert EvidenceLedgerStore is not None


class TestAppendOnlyInvariant:
    """FR-008: the ledger is append-only — no UPDATE/DELETE path (SC-006)."""

    def test_no_update_method(self):
        """EvidenceLedgerStore must NOT have update_entry or update methods."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        assert not hasattr(store, "update_entry"), "update_entry must not exist (append-only, FR-008)"
        assert not hasattr(store, "update"), "update must not exist (append-only, FR-008)"

    def test_no_delete_method(self):
        """EvidenceLedgerStore must NOT have delete_entry or delete methods."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        assert not hasattr(store, "delete_entry"), "delete_entry must not exist (append-only, FR-008)"
        assert not hasattr(store, "delete"), "delete must not exist (append-only, FR-008)"

    def test_has_insert_method(self):
        """EvidenceLedgerStore MUST have an insert_entry method."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        assert hasattr(store, "insert_entry"), "insert_entry must exist (append-only INSERT)"
        assert callable(store.insert_entry), "insert_entry must be callable"


class TestSnowflakeIdGeneration:
    """FR-032: ledger_entry_id must be a Snowflake ID (^[0-9]+$ string form)."""

    def test_generate_ledger_entry_id_is_numeric_string(self):
        """ledger_entry_id must match ^[0-9]+$ pattern (snowflake)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        entry_id = store.generate_ledger_entry_id()
        # Should be a string of digits (snowflake ID in string form)
        assert isinstance(entry_id, str), f"Expected str, got {type(entry_id)}"
        assert SNOWFLAKE_PATTERN.match(entry_id), f"ID {entry_id} does not match ^[0-9]+$"

    def test_generate_ledger_entry_id_is_unique(self):
        """Two calls must produce different IDs."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        id1 = store.generate_ledger_entry_id()
        id2 = store.generate_ledger_entry_id()
        assert id1 != id2, "Snowflake IDs must be unique"

    def test_generate_run_id_is_numeric_string(self):
        """run_id must also be a snowflake ID string."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        run_id = store.generate_run_id()
        assert isinstance(run_id, str)
        assert SNOWFLAKE_PATTERN.match(run_id), f"run_id {run_id} does not match ^[0-9]+$"


class TestMonotonicIndices:
    """FR-009: round_index and sub_problem_id must be monotonic within a run."""

    def test_round_index_starts_at_zero(self):
        """First round should be round_index=0 (first retrieval round)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        # round_index counter should start at 0
        assert store.next_round_index() == 0

    def test_round_index_monotonic(self):
        """round_index should be monotonic increasing."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        r0 = store.next_round_index()
        r1 = store.next_round_index()
        r2 = store.next_round_index()
        assert r0 < r1 < r2, f"round_index not monotonic: {r0}, {r1}, {r2}"

    def test_sub_problem_id_starts_at_one(self):
        """sub_problem_id should start at 1 (data-model sec 2.1)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        assert store.next_sub_problem_id() == 1

    def test_sub_problem_id_monotonic(self):
        """sub_problem_id should be monotonic increasing."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        s1 = store.next_sub_problem_id()
        s2 = store.next_sub_problem_id()
        s3 = store.next_sub_problem_id()
        assert s1 < s2 < s3, f"sub_problem_id not monotonic: {s1}, {s2}, {s3}"


class TestCrossScopeRejection:
    """FR-022: cross-scope writes must be rejected (Constitution hard constraint)."""

    def test_validate_scope_match(self):
        """Entry with matching isolation triple should be accepted."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        entry_data = {
            "knowledge_scope_id": 100,
            "project_id": 200,
            "index_version": 1,
        }
        project_scope = [{"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}]
        assert store.validate_scope(entry_data, project_scope) is True

    def test_validate_scope_mismatch_rejects(self):
        """Entry with mismatched isolation triple must be rejected (FR-022)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        # Entry claims scope A
        entry_data = {
            "knowledge_scope_id": 100,
            "project_id": 200,
            "index_version": 1,
        }
        # But the request scope is B (different project)
        project_scope = [{"knowledge_scope_id": 999, "project_id": 888, "index_version": 1}]
        assert store.validate_scope(entry_data, project_scope) is False

    def test_validate_scope_empty_scope_rejects(self):
        """Empty project_scope must be rejected (no implicit scope, FR-021)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        entry_data = {"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}
        assert store.validate_scope(entry_data, []) is False

    def test_validate_scope_none_scope_rejects(self):
        """None project_scope must be rejected (FR-021)."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        entry_data = {"knowledge_scope_id": 100, "project_id": 200, "index_version": 1}
        assert store.validate_scope(entry_data, None) is False


class TestBridgeKeyResolution:
    """FR-024: (request_id, evidence_id) bridge key resolves ledger entries."""

    @pytest.mark.asyncio
    async def test_get_by_request_evidence_exists(self):
        """EvidenceLedgerStore must have get_by_request_evidence method."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        store = EvidenceLedgerStore(MagicMock())
        assert hasattr(store, "get_by_request_evidence")
        assert callable(store.get_by_request_evidence)

    @pytest.mark.asyncio
    async def test_get_by_request_evidence_returns_entries(self):
        """Bridge key (request_id, evidence_id) should resolve to ledger entries."""
        from rag_mcp.orchestration.ledger import EvidenceLedgerStore
        mock_session = MagicMock()
        # Mock the query result
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        store = EvidenceLedgerStore(mock_session)
        entries = await store.get_by_request_evidence("req-1", "ev-1")
        assert isinstance(entries, list)
