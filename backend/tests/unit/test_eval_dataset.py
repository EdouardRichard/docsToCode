"""Test for eval dataset expansion (T022).

Tests: original 11 queries preserved, new lexical/Chinese queries added,
JSON format valid.

These tests MUST FAIL before T023 expands the dataset (TDD).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATASET_PATH = _REPO_ROOT / "eval" / "eval_dataset.json"


@pytest.fixture
def dataset() -> list[dict]:
    with open(_DATASET_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class TestOriginalQueriesPreserved:
    """FR-019: original 11 queries must be preserved for per-query comparison."""

    def test_at_least_11_queries(self, dataset):
        assert len(dataset) >= 11, f"Expected at least 11 queries, got {len(dataset)}"

    def test_original_validateToken_query_present(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("validateToken" in q for q in queries), "validateToken query must be preserved"

    def test_original_user_service_query_present(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("UserService" in q for q in queries), "UserService query must be preserved"


class TestNewQueriesAdded:
    """FR-019/FR-025: new lexical-precision and Chinese queries added."""

    def test_more_than_11_queries(self, dataset):
        assert len(dataset) > 11, f"Expected more than 11, got {len(dataset)}"

    def test_contains_chinese_query(self, dataset):
        queries = [e["query"] for e in dataset]
        for q in queries:
            for ch in q:
                cp = ord(ch)
                if 0x4e00 <= cp <= 0x9fff:
                    return
        pytest.fail("Dataset must contain at least one Chinese query (FR-025)")

    def test_contains_exact_symbol_query(self, dataset):
        queries = [e["query"] for e in dataset]
        assert any("#" in q and "." in q for q in queries), "Must contain exact symbol queries"


class TestJSONFormat:
    def test_is_list(self, dataset):
        assert isinstance(dataset, list)

    def test_each_entry_has_required_fields(self, dataset):
        for i, entry in enumerate(dataset):
            assert "query" in entry, f"Entry {i} missing query"
            assert "project_scope" in entry, f"Entry {i} missing project_scope"
            assert "expected_evidence_ids" in entry, f"Entry {i} missing expected_evidence_ids"

    def test_project_scope_nonempty(self, dataset):
        for i, entry in enumerate(dataset):
            assert len(entry["project_scope"]) > 0, f"Entry {i} has empty project_scope"
