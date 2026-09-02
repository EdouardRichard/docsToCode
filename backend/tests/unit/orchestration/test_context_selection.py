"""Unit test for context_selection_list store (T034 Red, US3).

Tests the ContextSelectionStore:
  - Only INSERT (append-only, FR-008)
  - context_result_id + decision enum (FR-032)
  - Original ledger entries not overwritten (FR-017)
  - Conforms to schema

This test MUST FAIL before context_selection.py is implemented (TDD Red).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestContextSelectionStoreImport:
    def test_import_store(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        assert ContextSelectionStore is not None


class TestAppendOnly:
    """FR-008: context selection is append-only."""

    def test_no_update_method(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        store = ContextSelectionStore(MagicMock())
        assert not hasattr(store, "update_selection")
        assert not hasattr(store, "update")

    def test_no_delete_method(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        store = ContextSelectionStore(MagicMock())
        assert not hasattr(store, "delete_selection")
        assert not hasattr(store, "delete")

    def test_has_insert_method(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        store = ContextSelectionStore(MagicMock())
        assert hasattr(store, "insert_selection")
        assert callable(store.insert_selection)


class TestSchemaConformance:
    """Output conforms to schema (FR-017/FR-032)."""

    def test_to_record_has_required_fields(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        store = ContextSelectionStore(MagicMock())
        record = store.to_selection_record({
            "context_result_id": "cr-1",
            "run_id": "999",
            "ledger_entry_id": "123",
            "decision": "selected",
        })
        assert record["context_result_id"] == "cr-1"
        assert record["decision"] == "selected"
        assert record["ledger_entry_id"] == "123"

    def test_invalid_decision_rejected(self):
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        store = ContextSelectionStore(MagicMock())
        # The store should raise on invalid decision
        with pytest.raises(ValueError):
            store.to_selection_record({
                "context_result_id": "cr-1",
                "run_id": "999",
                "ledger_entry_id": "123",
                "decision": "bogus",
            })

    @pytest.mark.asyncio
    async def test_insert_persists(self):
        """insert_selection should persist to DB."""
        from rag_mcp.orchestration.context_selection import ContextSelectionStore
        mock_session = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        store = ContextSelectionStore(mock_session)
        entry = await store.insert_selection({
            "context_result_id": "cr-1",
            "run_id": "999",
            "ledger_entry_id": "123",
            "decision": "selected",
        })
        assert entry is not None
        mock_session.add.assert_called_once()
