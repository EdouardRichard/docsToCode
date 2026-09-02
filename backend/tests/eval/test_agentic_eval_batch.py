"""Test for agentic eval batch composition (T040 Red, US4).

Tests the eval batch dataset:
  - >=6 queries (FR-027)
  - Multi-hop/gap/conflict categories, each >=2
  - >=1 Chinese query
  - JSON format matching 001 eval format

This test MUST FAIL before the dataset is created (TDD Red).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATASET_PATH = _REPO_ROOT / "eval" / "agentic_eval_dataset.json"


class TestDatasetExists:
    def test_dataset_file_exists(self):
        assert _DATASET_PATH.exists(), "eval/agentic_eval_dataset.json must exist"


class TestDatasetComposition:
    """FR-027: eval batch composition requirements."""

    @pytest.fixture
    def dataset(self):
        with open(_DATASET_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_at_least_six_queries(self, dataset):
        assert len(dataset) >= 6, f"Need >=6 queries, got {len(dataset)}"

    def test_has_multi_hop_queries(self, dataset):
        multi_hop = [q for q in dataset if q.get("category") == "multi_hop"]
        assert len(multi_hop) >= 2, f"Need >=2 multi_hop, got {len(multi_hop)}"

    def test_has_gap_queries(self, dataset):
        gap = [q for q in dataset if q.get("category") == "gap"]
        assert len(gap) >= 2, f"Need >=2 gap, got {len(gap)}"

    def test_has_conflict_queries(self, dataset):
        conflict = [q for q in dataset if q.get("category") == "conflict"]
        assert len(conflict) >= 2, f"Need >=2 conflict, got {len(conflict)}"

    def test_has_chinese_query(self, dataset):
        zh = [q for q in dataset if q.get("language") == "zh" or any(ord(c) > 127 for c in q.get("query", ""))]
        assert len(zh) >= 1, f"Need >=1 Chinese query, got {len(zh)}"

    def test_json_format_matches_001(self, dataset):
        """Each query must have query, project_scope, expected_evidence_ids (001 format)."""
        for q in dataset:
            assert "query" in q
            assert "project_scope" in q
            assert isinstance(q["project_scope"], list)
            assert "expected_evidence_ids" in q
            assert isinstance(q["expected_evidence_ids"], list)

    def test_each_query_has_category(self, dataset):
        """Each query should have a category field."""
        for q in dataset:
            assert "category" in q, f"Missing category: {q.get('query', '?')}"
